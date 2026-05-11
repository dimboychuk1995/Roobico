# app/utils/permissions.py

from __future__ import annotations

from functools import wraps
from bson import ObjectId
from flask import session, request, redirect, url_for, flash, jsonify, g

from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import SESSION_USER_ID, SESSION_TENANT_DB
from app.constants.permissions import ALL_PERMISSIONS, PROTECTED_ROLE_KEYS


def _maybe_object_id(value):
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return str(value)


def _is_api_request() -> bool:
    path = (request.path or "").lower()
    if path.startswith("/api/") or "/api/" in path:
        return True
    if request.is_json:
        return True
    accept = (request.headers.get("Accept") or "").lower()
    return "application/json" in accept


def get_tenant_db():
    db_name = session.get(SESSION_TENANT_DB)
    if not db_name:
        return None
    client = get_mongo_client()
    return client[db_name]


def _load_master_user(user_id=None):
    master = get_master_db()
    user_oid = _maybe_object_id(user_id or session.get(SESSION_USER_ID))
    if not user_oid:
        return None
    return master.users.find_one({"_id": user_oid, "is_active": True})


def _sync_protected_role_permissions(tdb, role_doc) -> None:
    """
    Owner и любые PROTECTED_ROLE_KEYS всегда получают ВСЕ permissions
    автоматически (даже если в каталог добавили новые ключи).
    """
    if not role_doc:
        return
    if role_doc.get("key") not in PROTECTED_ROLE_KEYS:
        return

    existing = set(role_doc.get("permissions") or [])
    desired = set(ALL_PERMISSIONS)
    if existing == desired:
        return

    tdb.roles.update_one(
        {"_id": role_doc["_id"]},
        {"$set": {"permissions": sorted(desired), "is_protected": True, "is_system": True}},
    )


def _user_allow_set(user: dict) -> set[str]:
    """Read user's allow overrides (supports both legacy and new field name)."""
    out: set[str] = set()
    for key in ("allow_permissions", "permissions_allow"):
        v = user.get(key)
        if isinstance(v, list):
            out |= {str(x).strip() for x in v if str(x).strip()}
    return out


def _user_deny_set(user: dict) -> set[str]:
    out: set[str] = set()
    for key in ("deny_permissions", "permissions_deny"):
        v = user.get(key)
        if isinstance(v, list):
            out |= {str(x).strip() for x in v if str(x).strip()}
    return out


def compute_user_permissions(user: dict, tdb=None) -> set[str]:
    """
    Чистый компьютер: вернёт set прав для данного user_doc.
    Используется в auth (login) и в декораторе.

    Приоритет (от низкого к высокому):
      1) role.permissions (из tenant_db.roles, ключ = user.role)
      2) user.allow_permissions  -> добавляются
      3) user.deny_permissions   -> вычитаются (deny выше, чем allow)
    """
    if user is None:
        return set()
    if tdb is None:
        tdb = get_tenant_db()
    if tdb is None:
        return set()

    role_key = (user.get("role") or "viewer").strip().lower()
    role_doc = tdb.roles.find_one({"key": role_key})
    _sync_protected_role_permissions(tdb, role_doc)
    if role_doc and role_doc.get("key") in PROTECTED_ROLE_KEYS:
        # owner & co. — всегда полный список
        role_perms = set(ALL_PERMISSIONS)
    else:
        role_perms = set(role_doc.get("permissions") or []) if role_doc else set()

    allow = _user_allow_set(user)
    deny = _user_deny_set(user)

    return (role_perms | allow) - deny


def get_effective_permissions() -> set[str]:
    """
    Per-request cache of current user's effective permissions.
    """
    if hasattr(g, "effective_permissions"):
        return g.effective_permissions

    user = _load_master_user()
    if user is None:
        g.effective_permissions = set()
        return g.effective_permissions

    perms = compute_user_permissions(user, get_tenant_db())
    g.effective_permissions = perms
    # Также обновим кэш в session, чтобы шаблоны могли использовать
    # session["user_permissions"] без отдельного запроса.
    session["user_permissions"] = sorted(perms)
    session.modified = True
    return perms


def refresh_session_permissions(user_id=None) -> set[str]:
    """
    Пересобрать и записать в сессию права пользователя.
    Вызывать после изменения роли/allow/deny/правки роли в админке.
    """
    user = _load_master_user(user_id=user_id)
    if user is None:
        return set()
    perms = compute_user_permissions(user, get_tenant_db())
    if user_id is None or str(user.get("_id")) == str(session.get(SESSION_USER_ID)):
        session["user_permissions"] = sorted(perms)
        session.modified = True
        if hasattr(g, "effective_permissions"):
            g.effective_permissions = perms
    return perms


def has_permission(permission_key: str) -> bool:
    return permission_key in get_effective_permissions()


def permission_required(permission_key: str):
    """
    Декоратор:
      - HTML: flash + redirect на dashboard
      - API:  403 JSON
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            if has_permission(permission_key):
                return view_func(*args, **kwargs)

            if _is_api_request():
                return jsonify({"ok": False, "error": "forbidden", "required": permission_key}), 403

            flash("Access denied.", "error")
            return redirect(url_for("dashboard.dashboard"))
        return wrapper
    return decorator


# ──────────────────────────────────────────────────────────────
# Меню
# ──────────────────────────────────────────────────────────────

# Маппинг nav-key → required permission. Для пунктов без явного perm
# используется fallback по ключу.
_NAV_PERM_DEFAULTS = {
    "dashboard": "dashboard.view",
    "calendar": "calendar.view",
    "parts": "parts.view",
    "vendors": "vendors.view",
    "customers": "customers.view",
    "work_orders": "work_orders.view",
    "settings": "settings.view",
    "reports": "reports.view",
    "import_export": "import_export.view",
}


def filter_nav_items(nav_items: list[dict]) -> list[dict]:
    """
    Убираем пункты меню, к которым нет доступа.
    Берём perm из item['perm'] либо из дефолтного маппинга по item['key'].
    """
    perms = get_effective_permissions()
    out = []
    for item in nav_items:
        perm = item.get("perm") or _NAV_PERM_DEFAULTS.get(item.get("key"))
        if not perm or perm in perms:
            out.append(item)
    return out
