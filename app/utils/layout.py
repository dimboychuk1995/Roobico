from __future__ import annotations

from bson import ObjectId
from flask import render_template, session, flash, redirect, url_for

from app.extensions import get_master_db
from app.utils.display_datetime import get_active_shop_timezone_name


def _oid(value):
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _unique_str_list(values):
    out = []
    seen = set()
    for v in values or []:
        s = str(v)
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _resolve_user_tenant(master):
    user_id = _oid(session.get("user_id"))
    tenant_id = _oid(session.get("tenant_id"))

    user = master.users.find_one({"_id": user_id, "is_active": True}) if user_id else None
    tenant = master.tenants.find_one({"_id": tenant_id, "status": "active"}) if tenant_id else None
    return user, tenant


def _resolve_shop_context(master, tenant, user):
    """
    Source of truth:
      - session["shop_ids"] (fallback -> user["shop_ids"])
    Ensures:
      - session["shop_id"] is always set to a valid allowed shop, otherwise first shop
    Returns:
      - shop_options: list[{id, name}]
      - active_shop_id: str|None
      - app_shop_display: str
    """
    # 1) allowed shops list
    session_shop_ids = session.get("shop_ids")
    if isinstance(session_shop_ids, list) and session_shop_ids:
        allowed_ids = _unique_str_list(session_shop_ids)
    else:
        allowed_ids = _unique_str_list(user.get("shop_ids") if isinstance(user.get("shop_ids"), list) else [])
        session["shop_ids"] = allowed_ids
        session.modified = True

    # 2) load shop docs (only allowed + tenant)
    shop_options = []
    allowed_oids = []
    for sid in allowed_ids:
        oid = _oid(sid)
        if oid:
            allowed_oids.append(oid)

    if tenant and allowed_oids:
        for s in master.shops.find(
            {"tenant_id": tenant["_id"], "_id": {"$in": allowed_oids}}
        ).sort("created_at", 1):
            shop_options.append({"id": str(s["_id"]), "name": s.get("name") or "—"})

    valid_ids = [x["id"] for x in shop_options]

    # 3) ensure active shop in session
    active_shop_id = session.get("shop_id")
    if not active_shop_id or str(active_shop_id) not in valid_ids:
        active_shop_id = valid_ids[0] if valid_ids else None
        session["shop_id"] = active_shop_id
        session.modified = True

    # 4) display name
    app_shop_display = "—"
    if active_shop_id:
        for opt in shop_options:
            if opt["id"] == str(active_shop_id):
                app_shop_display = opt["name"]
                break

    return shop_options, (str(active_shop_id) if active_shop_id else None), app_shop_display


def build_app_layout_context(nav_items, active_page: str):
    """
    Единая точка: собираем все переменные для layouts/app_base.html:
      - app_user_display
      - app_tenant_display
      - app_shop_display
      - shop_options + active_shop_id (для переключателя)
      - nav_items / active_page
      - также отдаём user/tenant (иногда удобно)
    """
    master = get_master_db()
    user, tenant = _resolve_user_tenant(master)

    if not user or not tenant:
        return {
            "ok": False,
            "error_response": (
                flash("Session data mismatch. Please login again.", "error"),
                session.clear(),
                redirect(url_for("main.index")),
            )[-1],
        }

    user_name = user.get("name") or user.get("username") or ""
    user_email = user.get("email") or ""
    tenant_name = tenant.get("name") or tenant.get("title") or tenant.get("company_name") or ""

    app_user_display = user_name or user_email or "—"
    app_tenant_display = tenant_name or "—"

    shop_options, active_shop_id, app_shop_display = _resolve_shop_context(master, tenant, user)

    return {
        "ok": True,
        "app_user_display": app_user_display,
        "app_tenant_display": app_tenant_display,
        "app_shop_display": app_shop_display,
        "shop_options": shop_options,
        "active_shop_id": active_shop_id,
        "nav_items": nav_items,
        "active_page": active_page,
        "app_timezone": get_active_shop_timezone_name(),
        "_current_user": user,
        "_current_tenant": tenant,
    }


def render_internal_page(template_name: str, nav_items, active_page: str, **ctx):
    """
    Рендер внутренних страниц через единый layout context.
    """
    layout = build_app_layout_context(nav_items, active_page)
    if not layout.get("ok"):
        return layout["error_response"]

    layout.pop("ok", None)
    layout.pop("error_response", None)

    layout.update(ctx)
    return render_template(template_name, **layout)
