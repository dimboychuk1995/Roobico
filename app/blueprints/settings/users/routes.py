from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from flask import render_template, request, redirect, url_for, flash, session

from app.blueprints.settings import settings_bp
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import (
    login_required,
    SESSION_USER_ID,
    SESSION_TENANT_ID,
    SESSION_TENANT_DB,
    SESSION_SHOP_ID,
)
from app.utils.permissions import permission_required, filter_nav_items
from app.blueprints.main.routes import NAV_ITEMS
from app.utils.layout import build_app_layout_context
from app.utils.pagination import get_pagination_params, get_sort_params, paginate_find
from app.utils.mongo_search import build_regex_search_filter


# -----------------------------
# Helpers
# -----------------------------

def utcnow():
    return datetime.now(timezone.utc)


def _maybe_object_id(value):
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _id_variants(value):
    """
    Варианты для надёжного поиска tenant_id:
    - как есть
    - ObjectId(.) если возможно
    - str(.)
    """
    if value is None:
        return []

    variants = [value]

    oid = _maybe_object_id(value)
    if oid is not None:
        variants.append(oid)

    variants.append(str(value))

    out = []
    seen = set()
    for v in variants:
        key = str(v)
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def _get_tenant_db():
    db_name = session.get(SESSION_TENANT_DB)
    if not db_name:
        return None
    client = get_mongo_client()
    return client[db_name]


def _load_current_user(master):
    user_id = _maybe_object_id(session.get(SESSION_USER_ID))
    if not user_id:
        return None
    return master.users.find_one({"_id": user_id, "is_active": True})


def _render_settings_page(template_name: str, **ctx):
    """
    Один общий рендер для settings-страниц через единый layout builder.
    """
    layout = build_app_layout_context(filter_nav_items(NAV_ITEMS), "settings")

    if not layout.get("_current_user") or not layout.get("_current_tenant"):
        flash("Session expired. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    layout.update(ctx)
    return render_template(template_name, **layout)


def _safe_str_list(v):
    if not isinstance(v, list):
        return []
    return [str(x) for x in v if x is not None]


def _load_shops_for_tenant(master, tenant_id):
    """
    Отдаём список шап (для чекбоксов) в формате:
      [{id, name}, ...]
    """
    shops = []
    if not tenant_id:
        return shops

    for s in master.shops.find({"tenant_id": tenant_id}).sort("created_at", 1):
        shops.append({
            "id": str(s["_id"]),
            "name": s.get("name") or "—",
        })
    return shops


def _redirect_users_index():
    q = (request.values.get("q") or "").strip()
    page = (request.values.get("page") or "").strip()
    per_page = (request.values.get("per_page") or "").strip()

    params = {}
    if q:
        params["q"] = q
    if page:
        params["page"] = page
    if per_page:
        params["per_page"] = per_page
    return redirect(url_for("settings.users_index", **params))


# -----------------------------
# UI Routes
# -----------------------------

@settings_bp.route("/users", methods=["GET", "POST"])
@login_required
@permission_required("settings.manage_users")
def users_index():
    master = get_master_db()

    tenant_id_raw = session.get(SESSION_TENANT_ID)
    if not tenant_id_raw:
        flash("Tenant session missing. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    current_user = _load_current_user(master)
    if not current_user:
        flash("User session mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    # tenant id (нормализуем)
    tenant_from_user = current_user.get("tenant_id") or tenant_id_raw
    tenant_oid = _maybe_object_id(tenant_from_user) or _maybe_object_id(tenant_id_raw)

    if request.method == "POST":
        return _handle_create_user(master, current_user, tenant_oid, tenant_id_raw)

    tenant_values = _id_variants(tenant_from_user)

    q = (request.args.get("q") or "").strip()

    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)
    query_filter = {"tenant_id": {"$in": tenant_values}}
    search_filter = build_regex_search_filter(
        q,
        text_fields=["name", "first_name", "last_name", "email", "phone", "role"],
        object_id_fields=["_id"],
    )
    if search_filter:
        query_filter = {"$and": [query_filter, search_filter]}

    users, pagination = paginate_find(
        master.users,
        query_filter,
        get_sort_params(request.args, [("created_at", -1)], ["name", "email", "role", "is_active", "created_at"]),
        page,
        per_page,
    )

    for u in users:
        u["_id_str"] = str(u.get("_id") or "")
        raw_shop_ids = u.get("shop_ids") if isinstance(u.get("shop_ids"), list) else []
        u["shop_ids_str"] = [str(x) for x in raw_shop_ids if x is not None]

    # ✅ шапы для чекбоксов (только текущий tenant)
    shops_for_form = _load_shops_for_tenant(master, tenant_oid)

    return _render_settings_page(
        "public/settings/users.html",
        users=users,
        pagination=pagination,
        q=q,
        shops_for_form=shops_for_form,
        active_shop_id=str(session.get(SESSION_SHOP_ID) or ""),
        sort_by=(request.args.get("sort_by") or "").strip(),
        sort_dir=(request.args.get("sort_dir") or "").strip(),
    )


@settings_bp.route("/users/<user_id>/edit", methods=["POST"])
@login_required
@permission_required("settings.manage_users")
def users_edit(user_id):
    master = get_master_db()

    tenant_id_raw = session.get(SESSION_TENANT_ID)
    if not tenant_id_raw:
        flash("Tenant session missing. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    current_user = _load_current_user(master)
    if not current_user:
        flash("User session mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    target_id = _maybe_object_id(user_id)
    if not target_id:
        flash("Invalid user id.", "error")
        return _redirect_users_index()

    tenant_values = _id_variants(current_user.get("tenant_id") or tenant_id_raw)
    target = master.users.find_one({"_id": target_id, "tenant_id": {"$in": tenant_values}})
    if not target:
        flash("User not found.", "error")
        return _redirect_users_index()

    first_name = (request.form.get("first_name") or "").strip()
    last_name = (request.form.get("last_name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    phone = (request.form.get("phone") or "").strip()
    role = (request.form.get("role") or "viewer").strip().lower()
    is_active = bool(request.form.get("is_active"))

    if not first_name or not last_name or not email:
        flash("Please fill first name, last name and email.", "error")
        return _redirect_users_index()

    existing = master.users.find_one({"email": email})
    if existing and existing.get("_id") != target_id:
        flash("User with this email already exists.", "error")
        return _redirect_users_index()

    tdb = _get_tenant_db()
    if tdb is None:
        flash("Tenant DB not found in session. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    role_doc = tdb.roles.find_one({"key": role})
    if not role_doc:
        flash("Selected role does not exist in tenant roles.", "error")
        return _redirect_users_index()

    selected_shop_ids_raw = request.form.getlist("shop_ids")
    allowed_shop_ids = set(_safe_str_list(session.get("shop_ids")))

    selected_shop_oids = []
    for sid in selected_shop_ids_raw:
        sid_str = str(sid).strip()
        if not sid_str:
            continue
        if allowed_shop_ids and sid_str not in allowed_shop_ids:
            continue
        oid = _maybe_object_id(sid_str)
        if oid and oid not in selected_shop_oids:
            selected_shop_oids.append(oid)

    if not selected_shop_oids:
        selected_shop_oids = target.get("shop_ids") if isinstance(target.get("shop_ids"), list) else []

    update_doc = {
        "first_name": first_name,
        "last_name": last_name,
        "name": f"{first_name} {last_name}".strip(),
        "email": email,
        "phone": phone or None,
        "role": role,
        "shop_ids": selected_shop_oids,
        "is_active": is_active,
        "updated_at": utcnow(),
    }

    password = request.form.get("password") or ""
    if password:
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return _redirect_users_index()
        from werkzeug.security import generate_password_hash
        update_doc["password_hash"] = generate_password_hash(password)

    if str(target_id) == str(session.get(SESSION_USER_ID)) and not is_active:
        flash("You cannot deactivate your own account.", "error")
        return _redirect_users_index()

    master.users.update_one({"_id": target_id}, {"$set": update_doc})
    flash("User updated successfully.", "success")
    return _redirect_users_index()


@settings_bp.route("/users/<user_id>/deactivate", methods=["POST"])
@login_required
@permission_required("settings.manage_users")
def users_deactivate(user_id):
    master = get_master_db()

    tenant_id_raw = session.get(SESSION_TENANT_ID)
    if not tenant_id_raw:
        flash("Tenant session missing. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    target_id = _maybe_object_id(user_id)
    if not target_id:
        flash("Invalid user id.", "error")
        return _redirect_users_index()

    if str(target_id) == str(session.get(SESSION_USER_ID)):
        flash("You cannot deactivate your own account.", "error")
        return _redirect_users_index()

    tenant_values = _id_variants(tenant_id_raw)
    res = master.users.update_one(
        {"_id": target_id, "tenant_id": {"$in": tenant_values}},
        {"$set": {"is_active": False, "updated_at": utcnow()}},
    )

    if res.matched_count == 0:
        flash("User not found.", "error")
        return _redirect_users_index()

    flash("User deactivated.", "success")
    return _redirect_users_index()


def _handle_create_user(master, current_user, tenant_oid, tenant_id_raw):
    first_name = (request.form.get("first_name") or "").strip()
    last_name = (request.form.get("last_name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    phone = (request.form.get("phone") or "").strip()
    role = (request.form.get("role") or "viewer").strip().lower()
    is_active = bool(request.form.get("is_active"))

    password = request.form.get("password") or ""
    password_confirm = request.form.get("password_confirm") or ""

    if not first_name or not last_name or not email:
        flash("Please fill first name, last name and email.", "error")
        return redirect(url_for("settings.users_index"))

    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("settings.users_index"))

    if password != password_confirm:
        flash("Passwords do not match.", "error")
        return redirect(url_for("settings.users_index"))

    if master.users.find_one({"email": email}):
        flash("User with this email already exists.", "error")
        return redirect(url_for("settings.users_index"))

    # Validate role exists in tenant DB
    tdb = _get_tenant_db()
    if tdb is None:
        flash("Tenant DB not found in session. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    role_doc = tdb.roles.find_one({"key": role})
    if not role_doc:
        flash("Selected role does not exist in tenant roles.", "error")
        return redirect(url_for("settings.users_index"))

    from werkzeug.security import generate_password_hash

    tenant_id = tenant_oid or _maybe_object_id(current_user.get("tenant_id")) or _maybe_object_id(tenant_id_raw)
    creator_id = _maybe_object_id(session.get(SESSION_USER_ID))

    # ✅ Чекбоксы шап:
    # name="shop_ids" -> request.form.getlist("shop_ids")
    selected_shop_ids_raw = request.form.getlist("shop_ids")

    # Разрешаем выдавать доступ ТОЛЬКО к тем шапам, которые доступны создателю (из сессии)
    allowed_shop_ids = set(_safe_str_list(session.get("shop_ids")))

    selected_shop_oids = []
    for sid in selected_shop_ids_raw:
        sid_str = str(sid).strip()
        if not sid_str:
            continue
        if allowed_shop_ids and sid_str not in allowed_shop_ids:
            # попытка выдать доступ к “чужой” шапе — игнорим
            continue
        oid = _maybe_object_id(sid_str)
        if oid and oid not in selected_shop_oids:
            selected_shop_oids.append(oid)

    # Если ничего не выбрали — fallback на активную шапу
    if not selected_shop_oids:
        active_shop_oid = _maybe_object_id(session.get(SESSION_SHOP_ID))
        creator_shop_ids = current_user.get("shop_ids") if isinstance(current_user.get("shop_ids"), list) else []
        if active_shop_oid is None and creator_shop_ids:
            active_shop_oid = creator_shop_ids[0]
        selected_shop_oids = [active_shop_oid] if active_shop_oid is not None else []

    user_doc = {
        "tenant_id": tenant_id,
        "shop_ids": selected_shop_oids,  # ✅ только shop_ids
        "email": email,
        "password_hash": generate_password_hash(password),
        "first_name": first_name,
        "last_name": last_name,
        "name": f"{first_name} {last_name}".strip(),
        "phone": phone or None,
        "role": role,
        "is_active": is_active,
        "must_reset_password": False,
        "allow_permissions": [],
        "deny_permissions": [],
        "created_at": utcnow(),
        "updated_at": utcnow(),
        "created_by": creator_id,
    }

    master.users.insert_one(user_doc)

    flash("User created successfully.", "success")
    return redirect(url_for("settings.users_index"))
