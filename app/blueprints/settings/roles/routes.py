from __future__ import annotations

import re
from datetime import datetime, timezone

from bson import ObjectId
from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify,
)

from app.blueprints.settings import settings_bp
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import (
    login_required,
    SESSION_USER_ID,
    SESSION_TENANT_ID,
    SESSION_TENANT_DB,
)
from app.utils.permissions import (
    permission_required,
    filter_nav_items,
    refresh_session_permissions,
)
from app.blueprints.main.routes import NAV_ITEMS
from app.utils.layout import build_app_layout_context
from app.constants.permissions import (
    PERMISSIONS,
    PERMISSION_GROUPS,
    ALL_PERMISSIONS,
    SYSTEM_ROLE_KEYS,
    PROTECTED_ROLE_KEYS,
)


# ── helpers ────────────────────────────────────────────────────────────

def _utcnow():
    return datetime.now(timezone.utc)


def _maybe_oid(v):
    if not v:
        return None
    try:
        return ObjectId(str(v))
    except Exception:
        return None


def _get_tenant_db():
    db_name = session.get(SESSION_TENANT_DB)
    if not db_name:
        return None
    return get_mongo_client()[db_name]


def _slugify_role_key(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "role"


def _ensure_unique_role_key(tdb, base: str) -> str:
    candidate = base
    idx = 2
    while tdb.roles.find_one({"key": candidate}, {"_id": 1}):
        candidate = f"{base}_{idx}"
        idx += 1
    return candidate


def _serialize_role(role: dict, users_by_role: dict | None = None) -> dict:
    users_by_role = users_by_role or {}
    key = role.get("key") or ""
    return {
        "id": str(role.get("_id")),
        "key": key,
        "name": role.get("name") or key,
        "permissions": sorted(set(role.get("permissions") or [])),
        "is_system": bool(role.get("is_system")),
        "is_protected": bool(role.get("is_protected") or key in PROTECTED_ROLE_KEYS),
        "user_count": int(users_by_role.get(key, 0)),
    }


def _normalize_perm_list(raw, *, allow_unknown: bool = False) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = set()
    for p in raw:
        if not isinstance(p, str):
            continue
        p = p.strip()
        if not p:
            continue
        if not allow_unknown and p not in PERMISSIONS:
            continue
        out.add(p)
    return sorted(out)


def _render_settings_page(template_name: str, **ctx):
    layout = build_app_layout_context(filter_nav_items(NAV_ITEMS), "settings")
    if not layout.get("_current_user") or not layout.get("_current_tenant"):
        flash("Session expired. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))
    layout.update(ctx)
    return render_template(template_name, **layout)


def _users_count_by_role(master, tenant_id) -> dict[str, int]:
    out: dict[str, int] = {}
    if not tenant_id:
        return out
    pipeline = [
        {"$match": {"tenant_id": tenant_id, "is_active": True}},
        {"$group": {"_id": "$role", "n": {"$sum": 1}}},
    ]
    for row in master.users.aggregate(pipeline):
        rk = (row.get("_id") or "").strip().lower() if isinstance(row.get("_id"), str) else ""
        if rk:
            out[rk] = int(row.get("n") or 0)
    return out


# ── PAGE ────────────────────────────────────────────────────────────────

@settings_bp.get("/roles")
@login_required
@permission_required("settings.manage_roles")
def roles_index():
    """Render the roles & permissions admin page (tree UI)."""
    return _render_settings_page(
        "public/settings/roles.html",
        permission_groups=PERMISSION_GROUPS,
        permission_labels=PERMISSIONS,
        protected_role_keys=sorted(PROTECTED_ROLE_KEYS),
        system_role_keys=sorted(SYSTEM_ROLE_KEYS),
    )


# ── API: catalog ────────────────────────────────────────────────────────

@settings_bp.get("/api/permissions/catalog")
@login_required
@permission_required("settings.manage_roles")
def api_permissions_catalog():
    return jsonify({
        "ok": True,
        "groups": PERMISSION_GROUPS,
        "labels": PERMISSIONS,
        "all": ALL_PERMISSIONS,
    })


# ── API: roles list ─────────────────────────────────────────────────────

@settings_bp.get("/api/roles")
@login_required
@permission_required("settings.manage_roles")
def api_roles_list():
    tdb = _get_tenant_db()
    if tdb is None:
        return jsonify({"ok": False, "error": "Tenant DB missing."}), 400

    master = get_master_db()
    tenant_id = _maybe_oid(session.get(SESSION_TENANT_ID))
    users_by_role = _users_count_by_role(master, tenant_id)

    roles = list(tdb.roles.find({}).sort([("is_system", -1), ("name", 1)]))
    return jsonify({
        "ok": True,
        "roles": [_serialize_role(r, users_by_role) for r in roles],
    })


# ── API: create role ────────────────────────────────────────────────────

@settings_bp.post("/api/roles")
@login_required
@permission_required("settings.manage_roles")
def api_roles_create():
    tdb = _get_tenant_db()
    if tdb is None:
        return jsonify({"ok": False, "error": "Tenant DB missing."}), 400

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Role name is required."}), 400

    perms = _normalize_perm_list(data.get("permissions"))

    base_key = _slugify_role_key(name)
    key = _ensure_unique_role_key(tdb, base_key)

    now = _utcnow()
    user_oid = _maybe_oid(session.get(SESSION_USER_ID))

    doc = {
        "key": key,
        "name": name,
        "permissions": perms,
        "is_system": False,
        "is_protected": False,
        "created_at": now,
        "updated_at": now,
        "created_by": user_oid,
        "updated_by": user_oid,
    }
    res = tdb.roles.insert_one(doc)
    doc["_id"] = res.inserted_id
    return jsonify({"ok": True, "role": _serialize_role(doc)})


# ── API: update role ────────────────────────────────────────────────────

@settings_bp.put("/api/roles/<role_id>")
@login_required
@permission_required("settings.manage_roles")
def api_roles_update(role_id):
    tdb = _get_tenant_db()
    if tdb is None:
        return jsonify({"ok": False, "error": "Tenant DB missing."}), 400

    rid = _maybe_oid(role_id)
    if not rid:
        return jsonify({"ok": False, "error": "Invalid role id."}), 400

    role = tdb.roles.find_one({"_id": rid})
    if not role:
        return jsonify({"ok": False, "error": "Role not found."}), 404

    if (role.get("key") or "") in PROTECTED_ROLE_KEYS:
        return jsonify({"ok": False, "error": "This role is protected and cannot be modified."}), 403

    data = request.get_json(silent=True) or {}
    update = {"updated_at": _utcnow(), "updated_by": _maybe_oid(session.get(SESSION_USER_ID))}

    if "name" in data:
        new_name = (data.get("name") or "").strip()
        if not new_name:
            return jsonify({"ok": False, "error": "Role name cannot be empty."}), 400
        update["name"] = new_name

    if "permissions" in data:
        update["permissions"] = _normalize_perm_list(data.get("permissions"))

    tdb.roles.update_one({"_id": rid}, {"$set": update})

    fresh = tdb.roles.find_one({"_id": rid})

    # пересчитать сессию ТЕКУЩЕГО юзера (если эта роль его)
    refresh_session_permissions()

    return jsonify({"ok": True, "role": _serialize_role(fresh)})


# ── API: delete role ────────────────────────────────────────────────────

@settings_bp.delete("/api/roles/<role_id>")
@login_required
@permission_required("settings.manage_roles")
def api_roles_delete(role_id):
    tdb = _get_tenant_db()
    if tdb is None:
        return jsonify({"ok": False, "error": "Tenant DB missing."}), 400

    rid = _maybe_oid(role_id)
    if not rid:
        return jsonify({"ok": False, "error": "Invalid role id."}), 400

    role = tdb.roles.find_one({"_id": rid})
    if not role:
        return jsonify({"ok": False, "error": "Role not found."}), 404

    if (role.get("key") or "") in SYSTEM_ROLE_KEYS:
        return jsonify({"ok": False, "error": "System roles cannot be deleted."}), 403

    master = get_master_db()
    tenant_id = _maybe_oid(session.get(SESSION_TENANT_ID))
    used = master.users.count_documents({
        "tenant_id": tenant_id,
        "role": role.get("key"),
        "is_active": True,
    })
    if used:
        return jsonify({
            "ok": False,
            "error": f"Cannot delete: role is assigned to {used} active user(s).",
        }), 400

    tdb.roles.delete_one({"_id": rid})
    return jsonify({"ok": True})


# ── API: clone role ─────────────────────────────────────────────────────

@settings_bp.post("/api/roles/<role_id>/clone")
@login_required
@permission_required("settings.manage_roles")
def api_roles_clone(role_id):
    tdb = _get_tenant_db()
    if tdb is None:
        return jsonify({"ok": False, "error": "Tenant DB missing."}), 400

    rid = _maybe_oid(role_id)
    if not rid:
        return jsonify({"ok": False, "error": "Invalid role id."}), 400

    src = tdb.roles.find_one({"_id": rid})
    if not src:
        return jsonify({"ok": False, "error": "Role not found."}), 404

    new_name = f"{src.get('name') or src.get('key')} (copy)"
    base_key = _ensure_unique_role_key(tdb, _slugify_role_key(new_name))

    now = _utcnow()
    user_oid = _maybe_oid(session.get(SESSION_USER_ID))

    doc = {
        "key": base_key,
        "name": new_name,
        "permissions": _normalize_perm_list(src.get("permissions") or []),
        "is_system": False,
        "is_protected": False,
        "created_at": now,
        "updated_at": now,
        "created_by": user_oid,
        "updated_by": user_oid,
    }
    res = tdb.roles.insert_one(doc)
    doc["_id"] = res.inserted_id
    return jsonify({"ok": True, "role": _serialize_role(doc)})


# ──────────────────────────────────────────────────────────────────────
# Per-user permission overrides
# ──────────────────────────────────────────────────────────────────────

def _user_in_tenant(master, tenant_id, user_oid):
    return master.users.find_one({"_id": user_oid, "tenant_id": tenant_id})


@settings_bp.get("/api/users/<user_id>/permissions")
@login_required
@permission_required("settings.manage_users")
def api_user_permissions_get(user_id):
    master = get_master_db()
    tdb = _get_tenant_db()
    if tdb is None:
        return jsonify({"ok": False, "error": "Tenant DB missing."}), 400

    tenant_id = _maybe_oid(session.get(SESSION_TENANT_ID))
    uid = _maybe_oid(user_id)
    if not uid:
        return jsonify({"ok": False, "error": "Invalid user id."}), 400

    user = _user_in_tenant(master, tenant_id, uid)
    if not user:
        return jsonify({"ok": False, "error": "User not found."}), 404

    role_key = (user.get("role") or "viewer").strip().lower()
    role_doc = tdb.roles.find_one({"key": role_key}) or {}
    role_perms = sorted(set(role_doc.get("permissions") or []))

    allow = sorted({str(p) for p in (user.get("allow_permissions") or []) if isinstance(p, str)})
    deny = sorted({str(p) for p in (user.get("deny_permissions") or []) if isinstance(p, str)})

    # effective preview
    effective = (set(role_perms) | set(allow)) - set(deny)
    if role_key in PROTECTED_ROLE_KEYS:
        effective = set(ALL_PERMISSIONS)

    return jsonify({
        "ok": True,
        "user": {
            "id": str(user.get("_id")),
            "name": user.get("name") or f"{user.get('first_name','')} {user.get('last_name','')}".strip(),
            "email": user.get("email") or "",
            "role": role_key,
            "role_name": role_doc.get("name") or role_key,
            "is_protected_role": role_key in PROTECTED_ROLE_KEYS,
        },
        "role_permissions": role_perms,
        "allow_permissions": allow,
        "deny_permissions": deny,
        "effective_permissions": sorted(effective),
        "groups": PERMISSION_GROUPS,
        "labels": PERMISSIONS,
    })


@settings_bp.put("/api/users/<user_id>/permissions")
@login_required
@permission_required("settings.manage_users")
def api_user_permissions_update(user_id):
    master = get_master_db()
    tdb = _get_tenant_db()
    if tdb is None:
        return jsonify({"ok": False, "error": "Tenant DB missing."}), 400

    tenant_id = _maybe_oid(session.get(SESSION_TENANT_ID))
    uid = _maybe_oid(user_id)
    if not uid:
        return jsonify({"ok": False, "error": "Invalid user id."}), 400

    user = _user_in_tenant(master, tenant_id, uid)
    if not user:
        return jsonify({"ok": False, "error": "User not found."}), 404

    data = request.get_json(silent=True) or {}
    allow = _normalize_perm_list(data.get("allow_permissions"))
    deny = _normalize_perm_list(data.get("deny_permissions"))

    # deny выигрывает: уберём из allow всё, что в deny
    deny_set = set(deny)
    allow = [p for p in allow if p not in deny_set]

    master.users.update_one(
        {"_id": uid},
        {"$set": {
            "allow_permissions": allow,
            "deny_permissions": deny,
            "updated_at": _utcnow(),
            "updated_by": _maybe_oid(session.get(SESSION_USER_ID)),
        }},
    )

    # пересчитать сессию текущего пользователя если это он сам
    refresh_session_permissions(user_id=uid)

    return jsonify({"ok": True, "allow_permissions": allow, "deny_permissions": deny})
