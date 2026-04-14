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
    {"key": "calendar", "label": "Calendar", "endpoint": "calendar.calendar_page"},

    {"key": "parts", "label": "Parts", "endpoint": "parts.parts_page"},
    {"key": "vendors", "label": "Vendors", "endpoint": "vendors.vendors_page"},
    {"key": "customers", "label": "Customers", "endpoint": "customers.customers_page"},
    {"key": "work_orders", "label": "Work Orders", "endpoint": "work_orders.work_orders_page"},
    {"key": "settings", "label": "Settings", "endpoint": "main.settings"},
    {"key": "reports", "label": "Reports", "endpoint": "reports.reports_index"},
    {"key": "import_export", "label": "Import / Export", "endpoint": "import_export.import_export_index"},
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
    user, tenant = _load_user_and_tenant_from_session()
    if not user or not tenant:
        flash("Session data mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))
    return _render_app_page(
        "public/settings/organization.html",
        active_page="settings",
        org=tenant,
    )


@main_bp.post("/settings/organization")
@login_required
@permission_required("settings.manage_org")
def settings_organization_save():
    user, tenant = _load_user_and_tenant_from_session()
    if not user or not tenant:
        return jsonify({"ok": False, "error": "Session expired."}), 401

    master = get_master_db()
    data = request.get_json(silent=True) or {}

    update_fields = {}
    allowed = [
        "name", "legal_name", "dot_mc", "tax_id", "website", "notes",
        "phone", "email",
        "address", "city", "state", "zip", "country",
        "contact_name", "contact_email", "contact_phone",
        "billing_email", "billing_phone", "billing_address",
        "timezone", "currency", "date_format", "units",
    ]
    for key in allowed:
        if key in data:
            update_fields[key] = (str(data[key]).strip() if data[key] is not None else "")

    if not update_fields:
        return jsonify({"ok": False, "error": "No fields to update."}), 400

    from datetime import datetime, timezone as tz
    update_fields["updated_at"] = datetime.now(tz.utc)

    master.tenants.update_one(
        {"_id": tenant["_id"]},
        {"$set": update_fields},
    )

    # Update session tenant name if changed
    if "name" in update_fields and update_fields["name"]:
        session["tenant_name"] = update_fields["name"]
        session.modified = True

    return jsonify({"ok": True})


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
    regex = re.escape(q)
    groups = []

    # helper: numeric-field regex match via $expr/$toString
    def _num_regex(field):
        return {
            "$expr": {
                "$regexMatch": {
                    "input": {"$toString": {"$ifNull": [f"${field}", ""]}},
                    "regex": regex,
                    "options": "i",
                }
            }
        }

    def _primary_contact(doc):
        """Return (name, phone, email) from the first contact."""
        contacts = doc.get("contacts") or []
        if not contacts:
            return "", "", ""
        cn = contacts[0]
        name = " ".join(filter(None, [cn.get("first_name"), cn.get("last_name")]))
        phone = cn.get("phone") or ""
        email = cn.get("email") or ""
        return name, phone, email

    # 1. Units  (unit_number, vin)
    units = list(
        db.units.find(
            {"$or": [
                {"unit_number": pattern},
                {"vin": pattern},
            ]},
            {"unit_number": 1, "year": 1, "make": 1, "model": 1, "vin": 1, "customer_id": 1},
        ).limit(_GLOBAL_SEARCH_LIMIT)
    )
    if units:
        # resolve customer names
        cust_ids = list({u["customer_id"] for u in units if u.get("customer_id")})
        cust_map = {}
        if cust_ids:
            for c in db.customers.find({"_id": {"$in": cust_ids}}, {"company_name": 1, "contacts": 1}):
                company = (c.get("company_name") or "").strip()
                if not company:
                    company, _, _ = _primary_contact(c)
                cust_map[c["_id"]] = company or ""

        items = []
        for u in units:
            parts_l = []
            if u.get("unit_number"):
                parts_l.append(str(u["unit_number"]))
            desc = " ".join(filter(None, [str(u.get("year") or ""), u.get("make") or "", u.get("model") or ""]))
            if desc:
                parts_l.append(desc)
            vin = u.get("vin") or ""
            if vin:
                parts_l.append(f"VIN {vin}")
            company = cust_map.get(u.get("customer_id")) or ""
            if company:
                parts_l.append(company)
            label = " · ".join(parts_l) or "—"
            cid = u.get("customer_id") or ""
            items.append({"label": label, "url": f"/customers/{cid}/units/{u['_id']}"})
        groups.append({"category": "Units", "items": items})

    # 2. Customers  (company, name)
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
            company = (c.get("company_name") or "").strip()
            name, phone, email = _primary_contact(c)
            parts_l = []
            if company:
                parts_l.append(company)
            if name and name != company:
                parts_l.append(name)
            if phone:
                parts_l.append(phone)
            if email:
                parts_l.append(email)
            label = " · ".join(parts_l) or "—"
            items.append({"label": label, "url": f"/customers/{c['_id']}"})
        groups.append({"category": "Customers", "items": items})

    # 3. Vendors  (name, primary contact)
    vendors = list(
        db.vendors.find(
            {"$or": [
                {"name": pattern},
                {"contacts.first_name": pattern},
                {"contacts.last_name": pattern},
            ]},
            {"name": 1, "contacts": 1},
        ).limit(_GLOBAL_SEARCH_LIMIT)
    )
    if vendors:
        items = []
        for v in vendors:
            company = (v.get("name") or "").strip()
            name, phone, email = _primary_contact(v)
            parts_l = []
            if company:
                parts_l.append(company)
            if name and name != company:
                parts_l.append(name)
            if phone:
                parts_l.append(phone)
            if email:
                parts_l.append(email)
            label = " · ".join(parts_l) or "—"
            items.append({"label": label, "url": f"/vendors/{v['_id']}"})
        groups.append({"category": "Vendors", "items": items})

    # 4. Work Orders  (wo_number — numeric field)
    work_orders = list(
        db.work_orders.find(
            _num_regex("wo_number"),
            {"wo_number": 1, "customer_id": 1, "totals": 1, "grand_total": 1, "status": 1},
        ).limit(_GLOBAL_SEARCH_LIMIT)
    )
    if work_orders:
        # resolve customer names
        wo_cust_ids = list({wo["customer_id"] for wo in work_orders if wo.get("customer_id")})
        wo_cust_map = {}
        if wo_cust_ids:
            for c in db.customers.find({"_id": {"$in": wo_cust_ids}}, {"company_name": 1, "contacts": 1}):
                company = (c.get("company_name") or "").strip()
                if not company:
                    company, _, _ = _primary_contact(c)
                wo_cust_map[c["_id"]] = company or ""

        items = []
        for wo in work_orders:
            totals_doc = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
            gt = totals_doc.get("grand_total") if totals_doc.get("grand_total") is not None else (wo.get("grand_total") or 0)
            gt = round(float(gt or 0), 2)
            status = (wo.get("status") or "open").strip().lower()
            paid_label = "Paid" if status == "paid" else "Unpaid"
            customer_name = wo_cust_map.get(wo.get("customer_id")) or ""

            parts_l = [f"WO #{wo.get('wo_number', '')}"]
            if customer_name:
                parts_l.append(customer_name)
            parts_l.append(f"${gt:,.2f}")
            parts_l.append(paid_label)
            label = " · ".join(parts_l)
            items.append({"label": label, "url": f"/work_orders/details?work_order_id={wo['_id']}"})
        groups.append({"category": "Work Orders", "items": items})

    # 5. Parts
    parts = list(
        db.parts.find(
            {"$or": [
                {"part_number": pattern},
                {"description": pattern},
            ]},
            {"part_number": 1, "description": 1, "in_stock": 1, "do_not_track_inventory": 1},
        ).limit(_GLOBAL_SEARCH_LIMIT)
    )
    if parts:
        items = []
        for p in parts:
            pn = p.get("part_number") or ""
            desc = p.get("description") or ""
            parts_l = []
            if pn and desc:
                parts_l.append(f"{pn} — {desc}")
            else:
                parts_l.append(pn or desc or "—")
            if bool(p.get("do_not_track_inventory")):
                parts_l.append("Not tracked")
            else:
                stock = int(p.get("in_stock") or 0)
                parts_l.append(f"In stock: {stock}")
            label = " · ".join(parts_l)
            items.append({"label": label, "url": f"/parts/?tab=parts&q={q}"})
        groups.append({"category": "Parts", "items": items})

    # 6. Part Orders  (order_number — numeric, vendor_bill — text)
    orders = list(
        db.parts_orders.find(
            {"$or": [
                _num_regex("order_number"),
                {"vendor_bill": pattern},
            ]},
            {"order_number": 1, "vendor_bill": 1, "vendor_id": 1, "status": 1,
             "items": 1, "non_inventory_amounts": 1},
        ).limit(_GLOBAL_SEARCH_LIMIT)
    )
    if orders:
        # resolve vendor names
        ord_vendor_ids = list({o["vendor_id"] for o in orders if o.get("vendor_id")})
        ord_vendor_map = {}
        if ord_vendor_ids:
            for v in db.vendors.find({"_id": {"$in": ord_vendor_ids}}, {"name": 1}):
                ord_vendor_map[v["_id"]] = (v.get("name") or "").strip()

        # resolve payment status
        order_ids = [o["_id"] for o in orders]
        paid_totals = {}
        for pay in db.parts_order_payments.find({"order_id": {"$in": order_ids}, "is_active": True}, {"order_id": 1, "amount": 1}):
            paid_totals[pay["order_id"]] = paid_totals.get(pay["order_id"], 0.0) + float(pay.get("amount") or 0)

        items = []
        for o in orders:
            vendor_name = ord_vendor_map.get(o.get("vendor_id")) or ""
            num = o.get("order_number", "")
            vb = o.get("vendor_bill") or ""
            status = (o.get("status") or "ordered").strip().lower()
            received_label = "Received" if status == "received" else "Not received"

            # compute total amount from items + non_inventory
            total_amt = 0.0
            for it in (o.get("items") or []):
                if isinstance(it, dict):
                    total_amt += float(it.get("quantity") or 0) * float(it.get("price") or 0)
            for ni in (o.get("non_inventory_amounts") or []):
                if isinstance(ni, dict):
                    total_amt += float(ni.get("amount") or 0)
            total_amt = round(total_amt, 2)

            paid_amt = round(paid_totals.get(o["_id"], 0.0), 2)
            pay_label = "Paid" if total_amt > 0 and paid_amt >= total_amt - 0.01 else "Unpaid"

            parts_l = []
            if vendor_name:
                parts_l.append(vendor_name)
            parts_l.append(f"Order #{num}")
            if vb:
                parts_l.append(f"Bill: {vb}")
            parts_l.append(pay_label)
            parts_l.append(received_label)
            label = " · ".join(parts_l)
            items.append({"label": label, "url": f"/parts/?tab=orders&open_order={o['_id']}"})
        groups.append({"category": "Part Orders", "items": items})

    return jsonify({"results": groups})


