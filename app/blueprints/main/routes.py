import re

from flask import render_template, request, redirect, url_for, session, flash, g, jsonify
from bson import ObjectId

from app.utils.auth import login_required, SESSION_USER_ID, SESSION_TENANT_ID
from app.utils.permissions import permission_required
from app.extensions import get_master_db, get_mongo_client
from app.utils.display_datetime import get_active_shop_timezone_name
from . import main_bp


@main_bp.get("/")
def index():
    return render_template("public/auth.html")


# Единый список меню (добавлять новые страницы — 1 строка тут)
NAV_ITEMS = [
    {"key": "dashboard", "label": "Dashboard", "endpoint": "dashboard.dashboard"},

    {"key": "parts", "label": "Parts", "endpoint": "parts.parts_page"},
    {"key": "vendors", "label": "Vendors", "endpoint": "vendors.vendors_page"},
    {"key": "customers", "label": "Customers", "endpoint": "customers.customers_page"},
    {"key": "work_orders", "label": "Work Orders", "endpoint": "work_orders.work_orders_page"},
    {"key": "settings", "label": "Settings", "endpoint": "main.settings"},
    {"key": "reports", "label": "Reports", "endpoint": "reports.reports_index"},
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
        return redirect(request.referrer or url_for("dashboard.dashboard"))

    allowed = session.get("shop_ids") or []
    allowed = [str(x) for x in allowed]

    if shop_id_raw not in allowed:
        flash("You don't have access to this shop.", "error")
        return redirect(request.referrer or url_for("dashboard.dashboard"))

    try:
        shop_oid = ObjectId(shop_id_raw)
    except Exception:
        flash("Invalid shop id.", "error")
        return redirect(request.referrer or url_for("dashboard.dashboard"))

    shop = master.shops.find_one({"_id": shop_oid, "tenant_id": tenant["_id"]})
    if not shop:
        flash("Shop not found.", "error")
        return redirect(request.referrer or url_for("dashboard.dashboard"))

    session["shop_id"] = shop_id_raw
    session.modified = True

    return redirect(request.referrer or url_for("dashboard.dashboard"))


# ===== Pages =====

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


# ── helpers for global search ──────────────────────────────────

def _get_shop_db_for_search():
    master = get_master_db()
    shop_id_raw = session.get("shop_id")
    if not shop_id_raw:
        return None
    try:
        shop_oid = ObjectId(str(shop_id_raw))
    except Exception:
        return None

    tenant_id = _maybe_object_id(session.get(SESSION_TENANT_ID))
    shop = master.shops.find_one({"_id": shop_oid, "tenant_id": tenant_id})
    if not shop:
        return None

    db_name = (
        shop.get("db_name")
        or shop.get("database")
        or shop.get("db")
        or shop.get("mongo_db")
        or shop.get("shop_db")
    )
    if not db_name:
        return None

    client = get_mongo_client()
    return client[str(db_name)]


_GLOBAL_SEARCH_LIMIT = 5


@main_bp.get("/api/global-search")
@login_required
def global_search_api():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"results": []})

    db = _get_shop_db_for_search()
    if db is None:
        return jsonify({"results": []})

    pattern = re.compile(re.escape(q), re.IGNORECASE)
    groups = []

    # 1. Customers
    customers = list(
        db.customers.find(
            {"$or": [
                {"company_name": pattern},
                {"contacts.first_name": pattern},
                {"contacts.last_name": pattern},
            ]},
            {"company_name": 1, "contacts": 1},
        ).limit(_GLOBAL_SEARCH_LIMIT)
    )
    if customers:
        items = []
        for c in customers:
            label = c.get("company_name") or ""
            contacts = c.get("contacts") or []
            if contacts:
                cn = contacts[0]
                name = " ".join(filter(None, [cn.get("first_name"), cn.get("last_name")]))
                if name and name != label:
                    label = f"{label} — {name}" if label else name
            items.append({"label": label or "—", "url": f"/customers/{c['_id']}"})
        groups.append({"category": "Customers", "items": items})

    # 2. Units
    units = list(
        db.units.find(
            {"$or": [
                {"unit_number": pattern},
                {"make": pattern},
                {"model": pattern},
                {"vin": pattern},
            ]},
            {"unit_number": 1, "year": 1, "make": 1, "model": 1, "vin": 1, "customer_id": 1},
        ).limit(_GLOBAL_SEARCH_LIMIT)
    )
    if units:
        items = []
        for u in units:
            bits = []
            if u.get("unit_number"):
                bits.append(str(u["unit_number"]))
            if u.get("year"):
                bits.append(str(u["year"]))
            if u.get("make"):
                bits.append(str(u["make"]))
            if u.get("model"):
                bits.append(str(u["model"]))
            label = " ".join(bits) or "—"
            cid = u.get("customer_id") or ""
            items.append({"label": label, "url": f"/customers/{cid}/units/{u['_id']}"})
        groups.append({"category": "Units", "items": items})

    # 3. Work Orders (by wo_number)
    wo_filter = {"wo_number": pattern}
    work_orders = list(
        db.work_orders.find(wo_filter, {"wo_number": 1}).limit(_GLOBAL_SEARCH_LIMIT)
    )
    if work_orders:
        items = []
        for wo in work_orders:
            label = f"WO #{wo.get('wo_number', '')}"
            items.append({"label": label, "url": f"/work_orders/details?work_order_id={wo['_id']}"})
        groups.append({"category": "Work Orders", "items": items})

    # 4. Parts
    parts = list(
        db.parts.find(
            {"$or": [
                {"part_number": pattern},
                {"description": pattern},
            ]},
            {"part_number": 1, "description": 1},
        ).limit(_GLOBAL_SEARCH_LIMIT)
    )
    if parts:
        items = []
        for p in parts:
            pn = p.get("part_number") or ""
            desc = p.get("description") or ""
            label = f"{pn} — {desc}" if pn and desc else (pn or desc or "—")
            items.append({"label": label, "url": f"/parts/?tab=parts&q={q}"})
        groups.append({"category": "Parts", "items": items})

    # 5. Part Orders (by order_number)
    orders = list(
        db.parts_orders.find(
            {"order_number": pattern},
            {"order_number": 1},
        ).limit(_GLOBAL_SEARCH_LIMIT)
    )
    if orders:
        items = []
        for o in orders:
            label = f"Order #{o.get('order_number', '')}"
            items.append({"label": label, "url": f"/parts/?tab=orders&open_order={o['_id']}"})
        groups.append({"category": "Part Orders", "items": items})

    return jsonify({"results": groups})


