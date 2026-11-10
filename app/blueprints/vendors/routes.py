from __future__ import annotations

from datetime import datetime, timezone

from flask import request, redirect, url_for, flash, session, jsonify

from app.utils import audit_log
from app.blueprints.vendors import vendors_bp
from app.blueprints.main.routes import _render_app_page
from app.extensions import get_master_db
from app.utils.auth import (
    login_required,
    SESSION_TENANT_ID,
    SESSION_USER_ID,
)
from app.utils.pagination import get_pagination_params, get_sort_params, paginate_find
from app.utils.permissions import has_permission, permission_required
from app.utils.mongo_search import build_regex_search_filter
from app.utils.rate_limit import hit_rate_limit
from app.blueprints.vendors import services as vendor_services
from app.utils.duplicates import duplicate_message, find_duplicate_vendor
from app.utils.display_datetime import format_date_mmddyyyy
from app.utils.date_filters import build_date_range_filters
from app.utils.contacts import (
    build_contacts_from_form,
    build_contacts_from_payload,
    build_vendor_legacy_contact_fields,
    contact_full_name,
    get_contacts,
    get_main_contact,
    get_main_contact_email,
    get_main_contact_phone,
)


def utcnow():
    return datetime.now(timezone.utc)


def _fmt_dt_label(dt):
    return format_date_mmddyyyy(dt)


from app.utils.tenant import (
    oid as _oid,
    get_shop_db as _get_shop_db,
)


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _vendors_collection():
    master = get_master_db()
    db, shop = _get_shop_db(master)
    if db is None:
        return None, None, None
    return db.vendors, shop, master


def _can_view_credentials() -> bool:
    return has_permission(vendor_services.CREDENTIALS_PERMISSION)


def _json_error(code: str, message: str, status: int = 400):
    """JSON-ошибка: error — код (или исторически текст), message — для UI."""
    return jsonify({"ok": False, "error": code, "message": message}), status


def _decorate_vendor(vendor: dict, *, can_view_credentials: bool = False) -> dict:
    contacts = get_contacts(vendor, entity_type="vendor")
    main_contact = get_main_contact(vendor, entity_type="vendor") or {}
    vendor["contacts"] = contacts
    vendor["main_contact_name"] = contact_full_name(main_contact) or ""
    vendor["main_contact_phone"] = get_main_contact_phone(vendor, entity_type="vendor")
    vendor["main_contact_email"] = get_main_contact_email(vendor, entity_type="vendor")
    vendor.update(vendor_services.website_fields(vendor.get("website")))
    # Токен пароля не уходит дальше роута никогда; логин — только с правом.
    has_password = vendor_services.has_portal_password(vendor)
    for key in vendor_services.SECRET_FIELDS:
        vendor.pop(key, None)
    if can_view_credentials:
        vendor["portal_login"] = str(vendor.get("portal_login") or "")
        vendor["has_portal_password"] = has_password
    else:
        vendor.pop("portal_login", None)
    return vendor


def _resolve_view(raw, can_view_credentials: bool) -> str:
    """vendors (полный список) | logins (Portal logins). Без права — только vendors.
    Кто видит учётки, но не создаёт и не правит вендоров, по умолчанию
    попадает сразу в Portal logins."""
    if not can_view_credentials:
        return "vendors"
    view = str(raw or "").strip().lower()
    if view in ("vendors", "logins"):
        return view
    if not has_permission("vendors.create") and not has_permission("vendors.edit"):
        return "logins"
    return "vendors"


def _vendors_logins_page(coll, shop, q: str):
    """Упрощённый вид «Portal logins»: активные вендоры — сайт, логин, пароль
    (пароль только по кнопке Show/Copy через /api/<id>/credentials)."""
    page, per_page = get_pagination_params(request.args, default_per_page=50, max_per_page=200)
    shop_id = shop["_id"]
    query = {"shop_id": {"$in": [shop_id, str(shop_id), None]}, "is_active": {"$ne": False}}
    search_filter = build_regex_search_filter(q, text_fields=["name", "website", "portal_login"])
    if search_filter:
        query = {"$and": [query, search_filter]}
    vendors, pagination = paginate_find(
        coll,
        query,
        [("name", 1), ("_id", 1)],
        page,
        per_page,
        projection={"name": 1, "website": 1, "portal_login": 1, "portal_password_enc": 1},
    )
    rows = []
    for vendor in vendors:
        rows.append({
            "id": str(vendor["_id"]),
            "name": vendor.get("name") or "—",
            **vendor_services.website_fields(vendor.get("website")),
            **vendor_services.credential_fields(vendor),
        })
    return _render_app_page(
        "public/vendors.html",
        active_page="vendors",
        view="logins",
        login_rows=rows,
        vendors=[],
        pagination=pagination,
        q=q,
        status="active",
        sort_by="",
        sort_dir="",
    )


@vendors_bp.get("/")
@login_required
@permission_required("vendors.view")
def vendors_page():
    coll, shop, master = _vendors_collection()
    if coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("dashboard.dashboard"))

    q = (request.args.get("q") or "").strip()
    can_view_credentials = _can_view_credentials()
    if _resolve_view(request.args.get("view"), can_view_credentials) == "logins":
        return _vendors_logins_page(coll, shop, q)

    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)

    # Общий фильтр статуса: active (по умолчанию) / inactive / all.
    status = (request.args.get("status") or "active").strip().lower()
    if status not in ("active", "inactive", "all"):
        status = "active"
    if status == "active":
        query = {"is_active": {"$ne": False}}
    elif status == "inactive":
        query = {"is_active": False}
    else:
        query = {}
    search_filter = build_regex_search_filter(
        q,
        text_fields=[
            "name",
            "phone",
            "email",
            "contacts.first_name",
            "contacts.last_name",
            "contacts.phone",
            "contacts.email",
            "website",
            "address",
            "primary_contact_first_name",
            "primary_contact_last_name",
            "notes",
        ],
        object_id_fields=["_id", "shop_id", "tenant_id", "created_by", "updated_by"],
    )
    if search_filter:
        query = {"$and": [query, search_filter]} if query else search_filter

    vendors, pagination = paginate_find(
        coll,
        query,
        get_sort_params(request.args, [("is_active", -1), ("name", 1), ("created_at", -1)], ["name", "is_active", "created_at"]),
        page,
        per_page,
        # Без права токен даже не читаем; с правом он нужен только для флага
        # has_portal_password и вырезается в _decorate_vendor.
        projection=None if can_view_credentials else {key: 0 for key in vendor_services.SECRET_FIELDS},
    )

    for vendor in vendors:
        _decorate_vendor(vendor, can_view_credentials=can_view_credentials)

    return _render_app_page(
        "public/vendors.html",
        active_page="vendors",
        view="vendors",
        login_rows=[],
        vendors=vendors,
        pagination=pagination,
        q=q,
        status=status,
        sort_by=(request.args.get("sort_by") or "").strip(),
        sort_dir=(request.args.get("sort_dir") or "").strip(),
    )


@vendors_bp.get("/api/balances")
@login_required
@permission_required("parts_orders.view")
def vendors_api_balances():
    """Баланс вендора = неоплаченные парт-ордера, поэтому право то же, что
    на просмотр заказов (vendors.view без parts_orders.view денег не даёт)."""
    coll, shop, _master = _vendors_collection()
    if coll is None or shop is None:
        return jsonify(ok=False, error="Shop not configured"), 400

    raw_ids = request.args.getlist("ids")
    oids = [o for o in (_oid(r) for r in raw_ids) if o]
    if not oids:
        return jsonify(ok=True, balances={})

    orders_coll = coll.database.parts_orders
    pipeline = [
        {
            "$match": {
                "shop_id": shop["_id"],
                "vendor_id": {"$in": oids},
                "is_active": {"$ne": False},
                # Заказы из почты до подтверждения человеком — ещё не долг.
                "needs_confirmation": {"$ne": True},
            }
        },
        {
            "$group": {
                "_id": "$vendor_id",
                "balance_total": {"$sum": {"$ifNull": ["$remaining_balance", 0]}},
            }
        },
    ]
    balance_map = {}
    for row in orders_coll.aggregate(pipeline):
        if not isinstance(row, dict):
            continue
        vid = row.get("_id")
        if not vid:
            continue
        balance_map[str(vid)] = round(_to_float(row.get("balance_total"), 0.0), 2)

    return jsonify(ok=True, balances=balance_map)


@vendors_bp.post("/create")
@login_required
@permission_required("vendors.create")
def vendors_create():
    coll, shop, master = _vendors_collection()
    if coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("vendors.vendors_page"))

    tenant_oid = _oid(session.get(SESSION_TENANT_ID))
    if not tenant_oid:
        flash("Tenant session missing. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    name = (request.form.get("name") or "").strip()
    address = (request.form.get("address") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    contacts = build_contacts_from_form(request.form)
    # Форма открыта из «Portal logins» — туда же и возвращаемся.
    back_url = url_for("vendors.vendors_page", view="logins") if request.form.get("return_view") == "logins" \
        else url_for("vendors.vendors_page")

    if not name:
        flash("Vendor name is required.", "error")
        return redirect(back_url)

    existing = find_duplicate_vendor(coll.database, shop["_id"], name)
    if existing:
        flash(duplicate_message("Vendor", existing.get("name"), existing), "error")
        return redirect(back_url)

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    try:
        website = vendor_services.clean_website(request.form.get("website"))
        cred_fields, cred_changes = (
            vendor_services.build_credential_update(request.form, None, user_id=user_oid, now=now)
            if _can_view_credentials() else ({}, [])
        )
    except vendor_services.VendorInputError as exc:
        flash(exc.message, "error")
        return redirect(back_url)

    doc = {
        "name": name,
        "website": website,
        "address": address or None,
        "contacts": contacts,
        "notes": notes or None,

        "is_active": True,

        "created_at": now,
        "updated_at": now,
        "created_by": user_oid,
        "updated_by": user_oid,
        "deactivated_at": None,
        "deactivated_by": None,

        "shop_id": shop["_id"],
        "tenant_id": tenant_oid,
    }
    doc.update(build_vendor_legacy_contact_fields(contacts))
    doc.update(cred_fields)

    coll.insert_one(doc)

    audit_log.log_event("create", "vendor", doc.get("_id"), label=audit_log.vendor_label(doc),
                        changes=cred_changes)
    flash("Vendor created successfully.", "success")
    return redirect(back_url)


@vendors_bp.post("/api/create")
@login_required
@permission_required("vendors.create")
def vendors_api_create():
    """
    AJAX create vendor with full fields.
    Accepts JSON: { name, website, address, notes, contacts: [{first_name, last_name, phone, email, is_main}],
                    portal_login, portal_password }  (учётка — только с vendors.view_credentials)
    Returns: { ok: true, vendor_id: "<oid>", vendor_name: "..." }
    """
    coll, shop, master = _vendors_collection()
    if coll is None or shop is None:
        return _json_error("Shop database not configured.", "Shop database not configured.")

    tenant_oid = _oid(session.get(SESSION_TENANT_ID))
    if not tenant_oid:
        return _json_error("Tenant session missing.", "Tenant session missing. Please log in again.", 401)

    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    name = str(data.get("name") or "").strip()
    if not name:
        return _json_error("Vendor name is required.", "Vendor name is required.")

    existing = find_duplicate_vendor(coll.database, shop["_id"], name)
    if existing:
        msg = duplicate_message("Vendor", existing.get("name"), existing)
        return _json_error(msg, msg, 409)

    address = str(data.get("address") or "").strip()
    notes = str(data.get("notes") or "").strip()
    contacts = build_contacts_from_payload({"contacts": data.get("contacts") or []})

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    try:
        website = vendor_services.clean_website(data.get("website"))
        cred_fields, cred_changes = (
            vendor_services.build_credential_update(data, None, user_id=user_oid, now=now)
            if _can_view_credentials() else ({}, [])
        )
    except vendor_services.VendorInputError as exc:
        return jsonify(exc.payload()), exc.status

    doc = {
        "name": name,
        "website": website,
        "address": address or None,
        "contacts": contacts,
        "notes": notes or None,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "created_by": user_oid,
        "updated_by": user_oid,
        "deactivated_at": None,
        "deactivated_by": None,
        "shop_id": shop["_id"],
        "tenant_id": tenant_oid,
    }
    doc.update(build_vendor_legacy_contact_fields(contacts))
    doc.update(cred_fields)

    res = coll.insert_one(doc)
    audit_log.log_event("create", "vendor", res.inserted_id, label=name, changes=cred_changes)
    return jsonify(ok=True, vendor_id=str(res.inserted_id), vendor_name=name)


@vendors_bp.post("/<vendor_id>/deactivate")
@login_required
@permission_required("vendors.deactivate")
def vendors_deactivate(vendor_id):
    coll, shop, master = _vendors_collection()
    if coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("vendors.vendors_page"))

    vid = _oid(vendor_id)
    if not vid:
        flash("Invalid vendor id.", "error")
        return redirect(url_for("vendors.vendors_page"))

    existing = coll.find_one({"_id": vid})
    if not existing:
        flash("Vendor not found.", "error")
        return redirect(url_for("vendors.vendors_page"))

    if existing.get("is_active") is False:
        flash("Vendor is already deactivated.", "info")
        return redirect(url_for("vendors.vendors_page"))

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    coll.update_one(
        {"_id": vid},
        {"$set": {
            "is_active": False,
            "updated_at": now,
            "updated_by": user_oid,
            "deactivated_at": now,
            "deactivated_by": user_oid,
        }},
    )

    audit_log.log_event("deactivate", "vendor", vid, label=audit_log.vendor_label(existing))
    flash("Vendor deactivated.", "success")
    return redirect(url_for("vendors.vendors_page"))


@vendors_bp.post("/<vendor_id>/restore")
@login_required
@permission_required("vendors.deactivate")
def vendors_restore(vendor_id):
    """
    Restore (reactivate) vendor в SHOP DB.
    Используем то же право vendors.deactivate (можем переименовать позже).
    """
    coll, shop, master = _vendors_collection()
    if coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("vendors.vendors_page"))

    vid = _oid(vendor_id)
    if not vid:
        flash("Invalid vendor id.", "error")
        return redirect(url_for("vendors.vendors_page"))

    existing = coll.find_one({"_id": vid})
    if not existing:
        flash("Vendor not found.", "error")
        return redirect(url_for("vendors.vendors_page"))

    if existing.get("is_active") is True:
        flash("Vendor is already active.", "info")
        return redirect(url_for("vendors.vendors_page"))

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    coll.update_one(
        {"_id": vid},
        {"$set": {
            "is_active": True,
            "updated_at": now,
            "updated_by": user_oid,
            "deactivated_at": None,
            "deactivated_by": None,
        }},
    )

    audit_log.log_event("activate", "vendor", vid, label=audit_log.vendor_label(existing))
    flash("Vendor restored.", "success")
    return redirect(url_for("vendors.vendors_page"))


@vendors_bp.get("/<vendor_id>")
@login_required
@permission_required("vendors.view")
def vendor_open(vendor_id):
    """
    Ссылка на вендора из глобального поиска (/vendors/<id>; мобилка разбирает
    этот же URL сама). Отдельной страницы вендора на вебе нет — ведём на
    список, отфильтрованный по его имени (раньше здесь был 404).
    """
    coll, shop, master = _vendors_collection()
    if coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("dashboard.dashboard"))

    vid = _oid(vendor_id)
    vendor = coll.find_one(vendor_services.shop_vendor_filter(vid, shop), {"name": 1, "is_active": 1}) if vid else None
    if not vendor:
        flash("Vendor not found.", "error")
        return redirect(url_for("vendors.vendors_page"))

    params = {"q": vendor.get("name") or ""}
    if vendor.get("is_active") is False:
        params["status"] = "all"
    if _can_view_credentials():
        params["view"] = "vendors"
    return redirect(url_for("vendors.vendors_page", **params))


@vendors_bp.get("/api/<vendor_id>")
@login_required
@permission_required("vendors.view")
def vendors_api_get(vendor_id):
    """
    AJAX get vendor data for edit modal (веб) и карточки вендора (мобилка).
    website_href/website_label — рабочая ссылка и подпись; portal_login и
    has_portal_password — только с vendors.view_credentials. Пароля нет никогда.
    """
    coll, shop, master = _vendors_collection()
    if coll is None or shop is None:
        return _json_error("Shop not configured", "Shop database not configured.")

    vid = _oid(vendor_id)
    if not vid:
        return _json_error("Invalid vendor id", "Invalid vendor id.")

    vendor = coll.find_one(vendor_services.shop_vendor_filter(vid, shop))
    if not vendor:
        return _json_error("Vendor not found", "Vendor not found.", 404)

    can_view_credentials = _can_view_credentials()
    vendor = _decorate_vendor(vendor, can_view_credentials=can_view_credentials)

    item = {
        "_id": str(vendor["_id"]),
        "name": vendor.get("name") or "",
        "phone": vendor.get("main_contact_phone") or "",
        "email": vendor.get("main_contact_email") or "",
        "website": vendor.get("website") or "",
        "website_href": vendor.get("website_href") or "",
        "website_label": vendor.get("website_label") or "",
        "primary_contact_first_name": (main_contact := (get_main_contact(vendor, entity_type="vendor") or {})).get("first_name") or "",
        "primary_contact_last_name": main_contact.get("last_name") or "",
        "contacts": vendor.get("contacts") or [],
        "address": vendor.get("address") or "",
        "notes": vendor.get("notes") or "",
        "is_active": vendor.get("is_active", True),
    }
    if can_view_credentials:
        item["portal_login"] = vendor.get("portal_login") or ""
        item["has_portal_password"] = bool(vendor.get("has_portal_password"))
    return jsonify({"ok": True, "item": item})


@vendors_bp.get("/api/<vendor_id>/credentials")
@login_required
@permission_required("vendors.view_credentials")
def vendors_api_credentials(vendor_id):
    """
    Показ учётки портала вендора: сайт, логин и РАСШИФРОВАННЫЙ пароль.
    Только по праву vendors.view_credentials, с rate limit на пользователя;
    каждый показ сохранённого пароля пишется в Activity Journal (без значения).
    Ответ не кэшируется (Cache-Control: no-store).
    """
    def _no_store(response, status=200):
        response.status_code = status
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response

    coll, shop, master = _vendors_collection()
    if coll is None or shop is None:
        return _no_store(jsonify({"ok": False, "error": "shop_not_configured",
                                  "message": "Shop database not configured."}), 400)

    user_key = str(session.get(SESSION_USER_ID) or "") or request.remote_addr
    if hit_rate_limit("vendor_credentials", user_key,
                      max_attempts=vendor_services.REVEAL_RATE_LIMIT,
                      window_seconds=vendor_services.REVEAL_RATE_WINDOW_SECONDS):
        return _no_store(jsonify({"ok": False, "error": "rate_limited",
                                  "message": vendor_services.REVEAL_RATE_LIMIT_MESSAGE}), 429)

    vid = _oid(vendor_id)
    vendor = coll.find_one(vendor_services.shop_vendor_filter(vid, shop)) if vid else None
    if not vendor:
        return _no_store(jsonify({"ok": False, "error": "not_found", "message": "Vendor not found."}), 404)

    try:
        creds = vendor_services.reveal_credentials(vendor)
    except vendor_services.VendorInputError as exc:
        return _no_store(jsonify(exc.payload()), exc.status)

    if creds["password"]:
        label = audit_log.vendor_label(vendor)
        audit_log.log_event("view", "vendor", vid, label=label,
                            summary=f"Viewed portal password of {label}")
    return _no_store(jsonify({"ok": True, **creds}))


def _calc_order_total(order_doc: dict) -> float:
    total = 0.0
    for item in (order_doc.get("items") or []):
        if not isinstance(item, dict):
            continue
        qty = max(0, int(_to_float(item.get("quantity"), 0)))
        price = max(0.0, _to_float(item.get("price"), 0.0))
        total += qty * price
        # Add core charge per unit if applicable
        if item.get("core_has_charge") and _to_float(item.get("core_cost"), 0.0) > 0:
            total += qty * _to_float(item.get("core_cost"), 0.0)
    for line in (order_doc.get("non_inventory_amounts") or []):
        if not isinstance(line, dict):
            continue
        total += max(0.0, _to_float(line.get("amount"), 0.0))
    return round(total, 2)


def _build_vendor_orders_summary(orders_coll, payments_coll, query: dict) -> dict:
    all_orders = list(orders_coll.find(query, {
        "_id": 1, "status": 1, "items": 1, "non_inventory_amounts": 1,
    }))

    total_orders = len(all_orders)
    total_amount = 0.0
    received_count = 0
    not_received_count = 0
    order_ids = []

    for order in all_orders:
        total_amount += _calc_order_total(order)
        status = (order.get("status") or "ordered").strip().lower()
        if status == "received":
            received_count += 1
        else:
            not_received_count += 1
        order_ids.append(order["_id"])

    # Calculate paid amounts
    total_paid = 0.0
    if order_ids and payments_coll is not None:
        pipeline = [
            {"$match": {"parts_order_id": {"$in": order_ids}, "is_active": True}},
            {"$group": {"_id": None, "total": {"$sum": {"$ifNull": ["$amount", 0]}}}},
        ]
        rows = list(payments_coll.aggregate(pipeline))
        if rows:
            total_paid = float(rows[0].get("total", 0))

    unpaid = max(0.0, total_amount - total_paid)

    return {
        "total_orders": total_orders,
        "total_amount": round(total_amount, 2),
        "total_paid": round(total_paid, 2),
        "unpaid": round(unpaid, 2),
        "received": received_count,
        "not_received": not_received_count,
    }


@vendors_bp.get("/api/<vendor_id>/part-orders")
@login_required
@permission_required("parts_orders.view")
def vendors_api_part_orders(vendor_id):
    coll, shop, master = _vendors_collection()
    if coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    vid = _oid(vendor_id)
    if not vid:
        return jsonify({"ok": False, "error": "Invalid vendor id"}), 400

    vendor = coll.find_one({"_id": vid, "shop_id": shop["_id"]})
    if not vendor:
        return jsonify({"ok": False, "error": "Vendor not found"}), 404

    date_filters = build_date_range_filters(request.args, default_preset="this_month")

    page, per_page = get_pagination_params(request.args, default_per_page=10, max_per_page=100)
    orders_coll = coll.database.parts_orders
    payments_coll = coll.database.parts_order_payments

    query = {
        "shop_id": shop["_id"],
        "vendor_id": vid,
        "is_active": {"$ne": False},
    }

    if date_filters["created_from"] or date_filters["created_to_exclusive"]:
        date_cond = {}
        if date_filters["created_from"]:
            date_cond["$gte"] = date_filters["created_from"]
        if date_filters["created_to_exclusive"]:
            date_cond["$lt"] = date_filters["created_to_exclusive"]
        query["created_at"] = date_cond

    orders, pagination = paginate_find(
        orders_coll,
        query,
        [("created_at", -1)],
        page,
        per_page,
        projection={
            "order_number": 1,
            "status": 1,
            "items": 1,
            "non_inventory_amounts": 1,
            "created_at": 1,
        },
    )

    items = []
    for order in orders:
        raw_items = order.get("items") if isinstance(order.get("items"), list) else []
        order_total = _calc_order_total(order)
        items.append(
            {
                "id": str(order.get("_id")),
                "order_number": order.get("order_number") or "-",
                "status": (order.get("status") or "ordered").strip().lower(),
                "items_count": len(raw_items),
                "total_amount": order_total,
                "created_at": _fmt_dt_label(order.get("created_at")),
            }
        )

    # Build summary across ALL matching orders (not just current page)
    summary = _build_vendor_orders_summary(orders_coll, payments_coll, query)

    return jsonify(
        {
            "ok": True,
            "vendor": {
                "id": str(vendor.get("_id")),
                "name": vendor.get("name") or "-",
            },
            "items": items,
            "pagination": pagination,
            "summary": summary,
            "date_preset": date_filters["date_preset"],
            "date_from": date_filters["date_from"],
            "date_to": date_filters["date_to"],
        }
    )


@vendors_bp.post("/api/<vendor_id>/update")
@login_required
@permission_required("vendors.edit")
def vendors_api_update(vendor_id):
    """
    AJAX update vendor (веб-модалка и мобилка).
    Accepts JSON with vendor data; с vendors.view_credentials ещё
    portal_login (нет ключа — логин не меняется), portal_password (пусто —
    сохранённый остаётся) и portal_password_clear: true (удалить пароль).
    Без права поля учётки молча игнорируются.
    Returns JSON { ok: true/false, ... }
    """
    coll, shop, master = _vendors_collection()
    if coll is None or shop is None:
        return _json_error("Shop not configured", "Shop database not configured.")

    vid = _oid(vendor_id)
    if not vid:
        return _json_error("Invalid vendor id", "Invalid vendor id.")

    vendor = coll.find_one(vendor_services.shop_vendor_filter(vid, shop))
    if not vendor:
        return _json_error("Vendor not found", "Vendor not found.", 404)

    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}

    name = str(data.get("name") or "").strip()
    if not name:
        return _json_error("Vendor name is required", "Vendor name is required.")

    existing = find_duplicate_vendor(coll.database, shop["_id"], name, exclude_id=vid)
    if existing:
        msg = duplicate_message("Vendor", existing.get("name"), existing)
        return _json_error(msg, msg, 409)

    address = str(data.get("address") or "").strip()
    notes = str(data.get("notes") or "").strip()
    contacts = build_contacts_from_payload(data)
    is_active = data.get("is_active", True)

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    try:
        website = vendor_services.clean_website(data.get("website"))
        cred_fields, cred_changes = (
            vendor_services.build_credential_update(data, vendor, user_id=user_oid, now=now)
            if _can_view_credentials() else ({}, [])
        )
    except vendor_services.VendorInputError as exc:
        return jsonify(exc.payload()), exc.status

    update_data = {
        "name": name,
        "website": website,
        "address": address or None,
        "notes": notes or None,
        "contacts": contacts,
        "is_active": bool(is_active),
        "updated_at": now,
        "updated_by": user_oid,
    }
    update_data.update(build_vendor_legacy_contact_fields(contacts))

    # If changing to inactive, set deactivated fields
    if not is_active and vendor.get("is_active", True):
        update_data["deactivated_at"] = now
        update_data["deactivated_by"] = user_oid
    # If reactivating, clear deactivated fields
    elif is_active and not vendor.get("is_active", True):
        update_data["deactivated_at"] = None
        update_data["deactivated_by"] = None

    changes = audit_log.diff_fields(vendor, update_data, audit_log.VENDOR_FIELDS) + cred_changes
    update_data.update(cred_fields)

    coll.update_one(
        {"_id": vid},
        {"$set": update_data}
    )

    audit_log.log_event("update", "vendor", vid, label=audit_log.vendor_label(vendor), changes=changes)
    return jsonify({"ok": True, "message": "Vendor updated successfully"})
