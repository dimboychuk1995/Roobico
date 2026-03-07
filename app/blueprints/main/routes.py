from flask import render_template, request, redirect, url_for, session, flash, g
from bson import ObjectId

from app.utils.auth import login_required, SESSION_USER_ID, SESSION_TENANT_ID
from app.utils.permissions import permission_required
from app.extensions import get_master_db
from app.utils.display_datetime import get_active_shop_timezone_name
from . import main_bp


@main_bp.get("/")
def index():
    return render_template("public/auth.html")


# Единый список меню (добавлять новые страницы — 1 строка тут)
NAV_ITEMS = [
    {"key": "dashboard", "label": "Dashboard", "endpoint": "main.dashboard"},

    {"key": "parts", "label": "Parts", "endpoint": "parts.parts_page"},
    {"key": "vendors", "label": "Vendors", "endpoint": "vendors.vendors_page"},
    {"key": "customers", "label": "Customers", "endpoint": "customers.customers_page"},
    {"key": "work_orders", "label": "Work Orders", "endpoint": "work_orders.work_orders_page"},
    {"key": "settings", "label": "Settings", "endpoint": "main.settings"},
    {"key": "reports", "label": "Reports", "endpoint": "main.reports"},
]


def _maybe_object_id(value):
    """
    Если value похож на ObjectId (24 hex) — вернём ObjectId(value),
    иначе вернём как есть (строка/что угодно).
    """
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return str(value)


def _load_user_and_tenant_from_session():
    """
    Возвращает (user, tenant) или (None, None) если сессия битая/не совпадает.
    """
    master = get_master_db()

    user_id = _maybe_object_id(session.get(SESSION_USER_ID))
    tenant_id = _maybe_object_id(session.get(SESSION_TENANT_ID))

    user = master.users.find_one({"_id": user_id, "is_active": True})
    tenant = master.tenants.find_one({"_id": tenant_id, "status": "active"})

    return user, tenant


def _render_app_page(template_name: str, active_page: str, **ctx):
    """
    Общий рендер для всех внутренних страниц.
    + Прокидывает permissions пользователя (и в payload, и в g.user_permissions)
    """
    user, tenant = _load_user_and_tenant_from_session()

    if not user or not tenant:
        flash("Session data mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    user_name = user.get("name") or user.get("username") or ""
    user_email = user.get("email") or ""
    tenant_name = tenant.get("name") or tenant.get("title") or tenant.get("company_name") or ""

    app_user_display = user_name or user_email or "—"
    app_tenant_display = tenant_name or "—"

    master = get_master_db()

    # ✅ shops list for dropdown (only allowed shops)
    allowed_ids = session.get("shop_ids") or []
    allowed_ids = [str(x) for x in allowed_ids]

    allowed_oids = []
    for sid in allowed_ids:
        try:
            allowed_oids.append(ObjectId(sid))
        except Exception:
            pass

    shop_options = []
    if allowed_oids:
        for s in master.shops.find({"tenant_id": tenant["_id"], "_id": {"$in": allowed_oids}}).sort("created_at", 1):
            shop_options.append({"id": str(s["_id"]), "name": s.get("name") or "—"})

    # ✅ ensure active shop in session
    active_shop_id = session.get("shop_id")
    if not active_shop_id or active_shop_id not in [x["id"] for x in shop_options]:
        if shop_options:
            active_shop_id = shop_options[0]["id"]
            session["shop_id"] = active_shop_id
            session.modified = True
        else:
            active_shop_id = None

    # ✅ Active shop display
    app_shop_display = "—"
    if active_shop_id:
        for opt in shop_options:
            if opt["id"] == active_shop_id:
                app_shop_display = opt["name"]
                break

    # ✅ Permissions: пытаемся достать из session (как обычно делают при логине),
    # fallback — из user doc если вдруг там лежит.
    raw_perms = (
        session.get("user_permissions")
        or session.get("permissions")
        or session.get("perms")
        or user.get("permissions")
        or []
    )

    # Нормализация
    perms_set = set()

    if isinstance(raw_perms, str):
        parts = raw_perms.replace(",", " ").split()
        perms_set.update([p.strip() for p in parts if p.strip()])
    elif isinstance(raw_perms, (list, tuple, set)):
        perms_set.update([str(p).strip() for p in raw_perms if str(p).strip()])
    elif isinstance(raw_perms, dict):
        for k, v in raw_perms.items():
            if v:
                perms_set.add(str(k).strip())

    user_permissions = sorted(perms_set)

    # ✅ Прокидываем в g
    g.user_permissions = perms_set

    payload = dict(
        app_user_display=app_user_display,
        app_tenant_display=app_tenant_display,
        app_shop_display=app_shop_display,

        # для dropdown в хедере
        shop_options=shop_options,
        active_shop_id=active_shop_id,

        nav_items=NAV_ITEMS,
        active_page=active_page,

        # ✅ для шаблонов
        user_permissions=user_permissions,          # список
        user_permissions_list=user_permissions,     # алиас под твой DEBUG блок
        user_permissions_set=perms_set,             # удобно: {% if 'x' in user_permissions_set %}
        app_timezone=get_active_shop_timezone_name(),
    )

    payload.update(ctx)
    return render_template(template_name, **payload)


@main_bp.post("/session/active-shop")
@login_required
def set_active_shop():
    """
    Меняем активную шапу и сохраняем в session["shop_id"].
    Проверяем:
      - shop принадлежит текущему tenant
      - shop входит в shop_ids пользователя
    """
    master = get_master_db()
    user, tenant = _load_user_and_tenant_from_session()

    if not user or not tenant:
        flash("Session data mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    shop_id_raw = (request.form.get("shop_id") or "").strip()
    if not shop_id_raw:
        flash("Shop is required.", "error")
        return redirect(request.referrer or url_for("main.dashboard"))

    allowed = session.get("shop_ids") or []
    allowed = [str(x) for x in allowed]

    if shop_id_raw not in allowed:
        flash("You don't have access to this shop.", "error")
        return redirect(request.referrer or url_for("main.dashboard"))

    try:
        shop_oid = ObjectId(shop_id_raw)
    except Exception:
        flash("Invalid shop id.", "error")
        return redirect(request.referrer or url_for("main.dashboard"))

    shop = master.shops.find_one({"_id": shop_oid, "tenant_id": tenant["_id"]})
    if not shop:
        flash("Shop not found.", "error")
        return redirect(request.referrer or url_for("main.dashboard"))

    session["shop_id"] = shop_id_raw
    session.modified = True

    return redirect(request.referrer or url_for("main.dashboard"))


# ===== Pages =====

@main_bp.get("/dashboard")
@login_required
@permission_required("dashboard.view")
def dashboard():
    return _render_app_page("public/dashboard.html", active_page="dashboard")


@main_bp.get("/settings")
@login_required
@permission_required("settings.view")
def settings():
    return _render_app_page("public/settings.html", active_page="settings")


@main_bp.get("/settings/organization")
@login_required
@permission_required("settings.manage_org")
def settings_organization():
    return _render_app_page("public/settings/organization.html", active_page="settings")


@main_bp.get("/settings/roles")
@login_required
@permission_required("settings.manage_roles")
def settings_roles():
    return _render_app_page("public/settings/roles.html", active_page="settings")


@main_bp.get("/settings/workflows")
@login_required
@permission_required("settings.manage_org")
def settings_workflows():
    return _render_app_page("public/settings/workflows.html", active_page="settings")


@main_bp.get("/settings/notifications")
@login_required
@permission_required("settings.manage_org")
def settings_notifications():
    return _render_app_page("public/settings/notifications.html", active_page="settings")


@main_bp.get("/reports")
@login_required
def reports():
    return _render_app_page("public/reports.html", active_page="reports")
