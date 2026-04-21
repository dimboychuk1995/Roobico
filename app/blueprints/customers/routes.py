from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bson import ObjectId
from flask import request, redirect, url_for, flash, session, jsonify

from app.blueprints.customers import customers_bp
from app.blueprints.main.routes import _render_app_page
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import (
    login_required,
    SESSION_TENANT_ID,
    SESSION_USER_ID,
)
from app.utils.pagination import get_pagination_params, get_sort_params, paginate_find
from app.utils.mongo_search import build_regex_search_filter
from app.utils.permissions import permission_required
from app.utils.display_datetime import format_date_mmddyyyy, format_preferred_shop_date
from app.utils.date_filters import build_date_range_filters
from app.utils.contacts import (
    build_contacts_from_form,
    build_contacts_from_payload,
    build_customer_legacy_contact_fields,
    contact_full_name,
    get_contacts,
    get_main_contact,
    get_main_contact_email,
    get_main_contact_phone,
    has_contact_name,
)


def utcnow():
    return datetime.now(timezone.utc)


def _round2(value):
    try:
        return round(float(value or 0) + 1e-12, 2)
    except Exception:
        return 0.0


def _fmt_dt_label(dt):
    return format_date_mmddyyyy(dt)


def _parse_iso_date_utc(value: str):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
        return parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _to_iso_date(value):
    if not value:
        return ""
    return value.strftime("%Y-%m-%d")


def _start_of_week_monday(value):
    return value - timedelta(days=value.weekday())


def _start_of_month(value):
    return value.replace(day=1)


def _start_of_quarter(value):
    quarter_start_month = ((value.month - 1) // 3) * 3 + 1
    return value.replace(month=quarter_start_month, day=1)


def _start_of_year(value):
    return value.replace(month=1, day=1)


def _date_range_for_preset(preset: str, today):
    if preset == "today":
        return today, today
    if preset == "yesterday":
        y = today - timedelta(days=1)
        return y, y
    if preset == "this_week":
        return _start_of_week_monday(today), today
    if preset == "last_week":
        this_week_start = _start_of_week_monday(today)
        last_week_start = this_week_start - timedelta(days=7)
        return last_week_start, this_week_start - timedelta(days=1)
    if preset == "this_month":
        return _start_of_month(today), today
    if preset == "last_month":
        this_month_start = _start_of_month(today)
        last_month_end = this_month_start - timedelta(days=1)
        return _start_of_month(last_month_end), last_month_end
    if preset == "this_quarter":
        return _start_of_quarter(today), today
    if preset == "last_quarter":
        this_quarter_start = _start_of_quarter(today)
        last_quarter_end = this_quarter_start - timedelta(days=1)
        return _start_of_quarter(last_quarter_end), last_quarter_end
    if preset == "this_year":
        return _start_of_year(today), today
    if preset == "last_year":
        this_year_start = _start_of_year(today)
        last_year_end = this_year_start - timedelta(days=1)
        return _start_of_year(last_year_end), last_year_end
    return None, None


def _get_date_range_filters(args, from_key: str = "date_from", to_key: str = "date_to", preset_key: str = "date_preset"):
    return build_date_range_filters(args, from_key=from_key, to_key=to_key, preset_key=preset_key)


def _append_and_filter(query: dict, extra_filter: dict):
    if not extra_filter:
        return query
    return {"$and": [query, extra_filter]}


def _build_created_at_range_filter(created_from=None, created_to_exclusive=None):
    created_filter = {}
    if created_from:
        created_filter["$gte"] = created_from
    if created_to_exclusive:
        created_filter["$lt"] = created_to_exclusive
    if not created_filter:
        return None
    return {"created_at": created_filter}


def _build_preferred_date_range_filter(date_field: str, created_from=None, created_to_exclusive=None):
    created_filter = _build_created_at_range_filter(created_from, created_to_exclusive)
    if not created_filter:
        return None

    range_filter = created_filter["created_at"]
    return {
        "$or": [
            {date_field: range_filter},
            {date_field: {"$exists": False}, "created_at": range_filter},
            {date_field: None, "created_at": range_filter},
        ]
    }


def _fmt_preferred_dt_label(primary_dt, fallback_dt):
    return format_preferred_shop_date(primary_dt, fallback=fallback_dt)


def _get_customer_work_orders_totals(shop_db, query: dict):
    pipeline = [
        {"$match": query},
        {
            "$group": {
                "_id": None,
                "labor_total": {"$sum": {"$ifNull": ["$totals.labor_total", {"$ifNull": ["$labor_total", 0]}]}},
                "parts_total": {"$sum": {"$ifNull": ["$totals.parts_total", {"$ifNull": ["$parts_total", 0]}]}},
                "grand_total": {"$sum": {"$ifNull": ["$totals.grand_total", {"$ifNull": ["$grand_total", 0]}]}},
            }
        },
    ]

    rows = list(shop_db.work_orders.aggregate(pipeline))
    if not rows:
        return {"labor_total": 0.0, "parts_total": 0.0, "grand_total": 0.0}

    row = rows[0] if isinstance(rows[0], dict) else {}
    return {
        "labor_total": _round2(row.get("labor_total") or 0),
        "parts_total": _round2(row.get("parts_total") or 0),
        "grand_total": _round2(row.get("grand_total") or 0),
    }


def _get_customer_payments_totals(shop_db, query: dict):
    pipeline = [
        {"$match": query},
        {
            "$group": {
                "_id": None,
                "amount_total": {"$sum": {"$ifNull": ["$amount", 0]}},
            }
        },
    ]

    rows = list(shop_db.work_order_payments.aggregate(pipeline))
    if not rows:
        return {"amount_total": 0.0}

    row = rows[0] if isinstance(rows[0], dict) else {}
    return {"amount_total": _round2(row.get("amount_total") or 0)}


def _customer_label(customer: dict) -> str:
    company = (customer.get("company_name") or "").strip()
    if company:
        return company
    full_name = contact_full_name(get_main_contact(customer, entity_type="customer"))
    return full_name or "-"


def _decorate_customer(customer: dict) -> dict:
    contacts = get_contacts(customer, entity_type="customer")
    main_contact = get_main_contact(customer, entity_type="customer") or {}
    customer["contacts"] = contacts
    customer["main_contact_name"] = contact_full_name(main_contact) or ""
    customer["main_contact_phone"] = get_main_contact_phone(customer, entity_type="customer")
    customer["main_contact_email"] = get_main_contact_email(customer, entity_type="customer")
    return customer


def _unit_label(unit: dict) -> str:
    bits = []
    if unit.get("unit_number"):
        bits.append(str(unit.get("unit_number")))
    if unit.get("year"):
        bits.append(str(unit.get("year")))
    if unit.get("make"):
        bits.append(str(unit.get("make")))
    if unit.get("model"):
        bits.append(str(unit.get("model")))
    if unit.get("vin"):
        bits.append(f"VIN {unit.get('vin')}")
    return " • ".join([x for x in bits if x]) or "-"


def _get_labor_rates(shop_db, shop_id: ObjectId) -> list[dict]:
    rows = list(
        shop_db.labor_rates.find({"shop_id": shop_id, "is_active": True}).sort([("name", 1)])
    )
    out = []
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        out.append(
            {
                "id": str(r.get("_id")),
                "code": code,
                "name": str(r.get("name") or code).strip(),
                "hourly_rate": _round2(r.get("hourly_rate")),
            }
        )
    return out


def _order_grand_total(order: dict) -> float:
    totals = order.get("totals") if isinstance(order.get("totals"), dict) else {}
    if totals.get("grand_total") is not None:
        return _round2(totals.get("grand_total"))
    return _round2(order.get("grand_total"))


def _build_paid_map(payments_coll, work_order_ids: list[ObjectId]) -> dict[ObjectId, float]:
    if not work_order_ids:
        return {}

    pipeline = [
        {
            "$match": {
                "work_order_id": {"$in": work_order_ids},
                "is_active": True,
            }
        },
        {
            "$group": {
                "_id": "$work_order_id",
                "paid_total": {"$sum": "$amount"},
            }
        },
    ]

    out = {}
    for row in payments_coll.aggregate(pipeline):
        out[row.get("_id")] = _round2(row.get("paid_total"))
    return out


def _attach_customers_current_balances(customers: list[dict], shop_db, shop_id: ObjectId):
    if not customers:
        return

    customer_ids = [c.get("_id") for c in customers if c.get("_id")]
    if not customer_ids:
        return

    work_orders = list(
        shop_db.work_orders.find(
            {
                "shop_id": shop_id,
                "customer_id": {"$in": customer_ids},
                "is_active": True,
            },
            {
                "_id": 1,
                "customer_id": 1,
                "totals": 1,
                "grand_total": 1,
            },
        )
    )

    work_order_ids = [wo.get("_id") for wo in work_orders if wo.get("_id")]
    paid_map = _build_paid_map(shop_db.work_order_payments, work_order_ids)

    balances_by_customer = {cid: 0.0 for cid in customer_ids}
    for wo in work_orders:
        cid = wo.get("customer_id")
        wo_id = wo.get("_id")
        if not cid or not wo_id:
            continue

        grand_total = _order_grand_total(wo)
        paid_amount = _round2(paid_map.get(wo_id, 0.0))
        remaining = _round2(grand_total - paid_amount)
        if remaining > 0:
            balances_by_customer[cid] = _round2(balances_by_customer.get(cid, 0.0) + remaining)

    for customer in customers:
        customer["current_balance"] = _round2(balances_by_customer.get(customer.get("_id"), 0.0))


def _customer_current_balance(shop_db, shop_id: ObjectId, customer_id: ObjectId) -> float:
    query = {
        "shop_id": shop_id,
        "customer_id": customer_id,
        "is_active": True,
    }
    all_orders = list(
        shop_db.work_orders.find(
            query,
            {"_id": 1, "totals": 1, "grand_total": 1},
        )
    )
    all_order_ids = [wo.get("_id") for wo in all_orders if wo.get("_id")]
    all_paid_map = _build_paid_map(shop_db.work_order_payments, all_order_ids)

    current_balance = 0.0
    for wo in all_orders:
        wo_id = wo.get("_id")
        grand_total = _order_grand_total(wo)
        paid_amount = _round2(all_paid_map.get(wo_id, 0.0))
        remaining = _round2(grand_total - paid_amount)
        if remaining > 0:
            current_balance = _round2(current_balance + remaining)

    return current_balance


def _empty_pagination(page: int, per_page: int):
    return {
        "page": max(1, int(page or 1)),
        "per_page": max(1, int(per_page or 20)),
        "total": 0,
        "pages": 1,
        "has_prev": False,
        "has_next": False,
        "prev_page": 1,
        "next_page": 1,
    }


def _oid(value):
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _tenant_id_variants():
    raw = session.get(SESSION_TENANT_ID)
    out = set()
    if raw is None:
        return []
    out.add(raw)
    out.add(str(raw))
    oid = _oid(raw)
    if oid:
        out.add(oid)
    return list(out)


def _get_active_shop(master):
    shop_id_raw = session.get("shop_id")
    shop_oid = _oid(shop_id_raw)
    if not shop_oid:
        return None

    tenant_variants = _tenant_id_variants()
    if not tenant_variants:
        return None

    return master.shops.find_one({"_id": shop_oid, "tenant_id": {"$in": tenant_variants}})


def _get_shop_db(master):
    shop = _get_active_shop(master)
    if not shop:
        return None, None

    db_name = (
        shop.get("db_name")
        or shop.get("database")
        or shop.get("db")
        or shop.get("mongo_db")
        or shop.get("shop_db")
    )
    if not db_name:
        return None, shop

    client = get_mongo_client()
    return client[str(db_name)], shop


def _customers_collection():
    master = get_master_db()
    db, shop = _get_shop_db(master)
    if db is None:
        return None, None, None
    return db.customers, shop, master


@customers_bp.get("/customers")
@login_required
@permission_required("customers.view")
def customers_page():
    coll, shop, master = _customers_collection()
    if coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("dashboard.dashboard"))

    q = (request.args.get("q") or "").strip()
    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)

    query = {}
    search_filter = build_regex_search_filter(
        q,
        text_fields=[
            "company_name",
            "first_name",
            "last_name",
            "phone",
            "email",
            "contacts.first_name",
            "contacts.last_name",
            "contacts.phone",
            "contacts.email",
            "address",
            "default_labor_rate",
        ],
        object_id_fields=["_id", "shop_id", "tenant_id", "created_by", "updated_by"],
    )
    if search_filter:
        query = {"$and": [query, search_filter]} if query else search_filter

    customers, pagination = paginate_find(
        coll,
        query,
        get_sort_params(request.args, [("is_active", -1), ("company_name", 1), ("last_name", 1), ("first_name", 1), ("created_at", -1)], ["company_name", "last_name", "first_name", "created_at", "is_active"]),
        page,
        per_page,
    )

    for customer in customers:
        _decorate_customer(customer)

    sort_by = (request.args.get("sort_by") or "").strip()
    sort_dir = (request.args.get("sort_dir") or "").strip()

    return _render_app_page(
        "public/customers.html",
        active_page="customers",
        customers=customers,
        pagination=pagination,
        q=q,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


@customers_bp.get("/customers/api/balances")
@login_required
@permission_required("customers.view")
def customers_balances_api():
    coll, shop, master = _customers_collection()
    if coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    raw_ids = request.args.getlist("ids")
    if not raw_ids:
        raw_ids = request.args.get("ids", "").split(",")

    customer_ids = []
    for raw in raw_ids:
        cid = _oid(raw.strip())
        if cid:
            customer_ids.append(cid)

    if not customer_ids:
        return jsonify({"ok": True, "balances": {}})

    shop_db = coll.database
    work_orders = list(
        shop_db.work_orders.find(
            {
                "shop_id": shop["_id"],
                "customer_id": {"$in": customer_ids},
                "is_active": True,
            },
            {"_id": 1, "customer_id": 1, "totals": 1, "grand_total": 1},
        )
    )

    work_order_ids = [wo.get("_id") for wo in work_orders if wo.get("_id")]
    paid_map = _build_paid_map(shop_db.work_order_payments, work_order_ids)

    balances = {str(cid): 0.0 for cid in customer_ids}
    for wo in work_orders:
        cid = wo.get("customer_id")
        wo_id = wo.get("_id")
        if not cid or not wo_id:
            continue
        grand_total = _order_grand_total(wo)
        paid_amount = _round2(paid_map.get(wo_id, 0.0))
        remaining = _round2(grand_total - paid_amount)
        if remaining > 0:
            balances[str(cid)] = _round2(balances.get(str(cid), 0.0) + remaining)

    return jsonify({"ok": True, "balances": balances})


@customers_bp.get("/customers/<customer_id>")
@login_required
@permission_required("customers.view")
def customer_details_page(customer_id):
    coll, shop, master = _customers_collection()
    if coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("customers.customers_page"))

    cid = _oid(customer_id)
    if not cid:
        flash("Invalid customer id.", "error")
        return redirect(url_for("customers.customers_page"))

    customer = coll.find_one({"_id": cid, "shop_id": shop["_id"]})
    if not customer:
        flash("Customer not found.", "error")
        return redirect(url_for("customers.customers_page"))

    tab = (request.args.get("tab") or "work_orders").strip().lower()
    allowed_tabs = {"work_orders", "units", "payments", "estimates", "details"}
    if tab not in allowed_tabs:
        tab = "work_orders"

    q = (request.args.get("q") or "").strip()
    paid_status = (request.args.get("paid_status") or "all").strip().lower()
    if paid_status not in ("all", "paid", "unpaid"):
        paid_status = "all"

    date_filters = _get_date_range_filters(request.args)
    date_from = date_filters["date_from"]
    date_to = date_filters["date_to"]
    date_preset = date_filters["date_preset"]
    created_from = date_filters["created_from"]
    created_to_exclusive = date_filters["created_to_exclusive"]

    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)
    shop_db = coll.database
    labor_rates = _get_labor_rates(shop_db, shop["_id"])
    labor_rates_by_id = {x.get("id"): x for x in labor_rates if x.get("id")}
    labor_rates_by_code = {x.get("code"): x for x in labor_rates if x.get("code")}

    selected_rate = None
    raw_default_rate = customer.get("default_labor_rate")
    if isinstance(raw_default_rate, ObjectId):
        selected_rate = labor_rates_by_id.get(str(raw_default_rate))
    else:
        # Backward compatibility for legacy string values stored before migration.
        legacy_code = str(raw_default_rate or "").strip().lower()
        if legacy_code == "standart":
            legacy_code = "standard"
        selected_rate = labor_rates_by_code.get(legacy_code)

    selected_rate_name = (selected_rate or {}).get("name") or "-"
    selected_rate_id = (selected_rate or {}).get("id") or ""

    customer_view = {
        "id": str(customer.get("_id")),
        "label": _customer_label(customer),
        "company_name": customer.get("company_name") or "",
        "first_name": (main_contact := (get_main_contact(customer, entity_type="customer") or {})).get("first_name") or "",
        "last_name": main_contact.get("last_name") or "",
        "phone": get_main_contact_phone(customer, entity_type="customer") or "",
        "email": get_main_contact_email(customer, entity_type="customer") or "",
        "contacts": get_contacts(customer, entity_type="customer"),
        "address": customer.get("address") or "",
        "taxable": bool(customer.get("taxable", False)),
        "default_labor_rate": str(customer.get("default_labor_rate")) if isinstance(customer.get("default_labor_rate"), ObjectId) else "",
        "default_labor_rate_id": selected_rate_id,
        "is_active": customer.get("is_active", True),
        "created_at": _fmt_dt_label(customer.get("created_at")),
        "updated_at": _fmt_dt_label(customer.get("updated_at")),
        "current_balance": None,
        "default_labor_rate_name": selected_rate_name,
    }

    tab_items = []
    tab_pagination = None
    work_orders_totals = {"labor_total": 0.0, "parts_total": 0.0, "grand_total": 0.0}
    payments_totals = {"amount_total": 0.0}
    estimates_totals = {"labor_total": 0.0, "parts_total": 0.0, "grand_total": 0.0}

    if tab == "work_orders":
        wo_query = {
            "shop_id": shop["_id"],
            "customer_id": cid,
            "is_active": True,
        }
        if paid_status == "paid":
            wo_query["status"] = "paid"
        elif paid_status == "unpaid":
            wo_query["status"] = {"$ne": "paid"}

        wo_search = build_regex_search_filter(
            q,
            text_fields=["status"],
            numeric_fields=["wo_number", "grand_total", "totals.grand_total", "totals.parts_total", "totals.labor_total"],
            object_id_fields=["_id", "unit_id", "customer_id", "shop_id", "tenant_id"],
        )
        if wo_search:
            wo_query = {"$and": [wo_query, wo_search]}

        created_at_filter = _build_preferred_date_range_filter("work_order_date", created_from, created_to_exclusive)
        if created_at_filter:
            wo_query = _append_and_filter(wo_query, created_at_filter)

        work_orders_totals = _get_customer_work_orders_totals(shop_db, wo_query)

        work_orders, tab_pagination = paginate_find(
            shop_db.work_orders,
            wo_query,
            get_sort_params(request.args, [("work_order_date", -1), ("created_at", -1)], ["wo_number", "status", "work_order_date", "grand_total", "created_at"]),
            page,
            per_page,
            projection={
                "wo_number": 1,
                "status": 1,
                "work_order_date": 1,
                "created_at": 1,
                "totals": 1,
                "grand_total": 1,
                "unit_id": 1,
            },
        )
        page_wo_ids = [wo.get("_id") for wo in work_orders if wo.get("_id")]
        paid_map = _build_paid_map(shop_db.work_order_payments, page_wo_ids)

        unit_ids = [wo.get("unit_id") for wo in work_orders if wo.get("unit_id")]
        units_map = {}
        if unit_ids:
            for unit in shop_db.units.find({"_id": {"$in": unit_ids}}):
                units_map[unit.get("_id")] = _unit_label(unit)

        for wo in work_orders:
            wo_id = wo.get("_id")
            grand_total = _order_grand_total(wo)
            paid_amount = _round2(paid_map.get(wo_id, 0.0))
            remaining = _round2(grand_total - paid_amount)
            if remaining < 0:
                remaining = 0.0
            tab_items.append(
                {
                    "id": str(wo_id),
                    "wo_number": wo.get("wo_number") or "-",
                    "status": (wo.get("status") or "open").strip().lower(),
                    "created_at": _fmt_preferred_dt_label(wo.get("work_order_date"), wo.get("created_at")),
                    "unit": units_map.get(wo.get("unit_id")) or "-",
                    "grand_total": grand_total,
                    "paid_amount": paid_amount,
                    "remaining_balance": remaining,
                }
            )

    elif tab == "units":
        units_query = {
            "shop_id": shop["_id"],
            "customer_id": cid,
            "is_active": True,
        }
        units_search = build_regex_search_filter(
            q,
            text_fields=["unit_number", "make", "model", "vin", "type"],
            numeric_fields=["year", "mileage"],
            object_id_fields=["_id", "customer_id", "shop_id", "tenant_id"],
        )
        if units_search:
            units_query = {"$and": [units_query, units_search]}

        units, tab_pagination = paginate_find(
            shop_db.units,
            units_query,
            get_sort_params(request.args, [("created_at", -1)], ["unit_number", "vin", "mileage", "created_at"]),
            page,
            per_page,
            projection={
                "unit_number": 1,
                "year": 1,
                "make": 1,
                "model": 1,
                "vin": 1,
                "mileage": 1,
                "created_at": 1,
            },
        )

        for u in units:
            tab_items.append(
                {
                    "id": str(u.get("_id")),
                    "unit_number": u.get("unit_number") or "-",
                    "label": _unit_label(u),
                    "vin": u.get("vin") or "-",
                    "mileage": u.get("mileage") if u.get("mileage") is not None else "-",
                    "created_at": _fmt_dt_label(u.get("created_at")),
                }
            )

    elif tab == "payments":
        wo_rows = list(
            shop_db.work_orders.find(
                {
                    "shop_id": shop["_id"],
                    "customer_id": cid,
                    "is_active": True,
                },
                {"_id": 1, "wo_number": 1},
            )
        )

        wo_ids = [wo.get("_id") for wo in wo_rows if wo.get("_id")]
        wo_map = {wo.get("_id"): wo.get("wo_number") for wo in wo_rows if wo.get("_id")}

        if wo_ids:
            payments_query = {
                "shop_id": shop["_id"],
                "work_order_id": {"$in": wo_ids},
                "is_active": True,
            }
            payments_search = build_regex_search_filter(
                q,
                text_fields=["payment_method", "notes"],
                numeric_fields=["amount"],
                object_id_fields=["_id", "work_order_id", "shop_id", "created_by"],
            )
            if q:
                wo_id_matches = [wo_id for wo_id, wo_num in wo_map.items() if q.lower() in str(wo_num or "").lower()]
                extra = []
                if wo_id_matches:
                    extra.append({"work_order_id": {"$in": wo_id_matches}})
                if payments_search and extra:
                    payments_query = {"$and": [payments_query, {"$or": [payments_search, *extra]}]}
                elif payments_search:
                    payments_query = {"$and": [payments_query, payments_search]}
                elif extra:
                    payments_query = {"$and": [payments_query, {"$or": extra}]}
            elif payments_search:
                payments_query = {"$and": [payments_query, payments_search]}

            created_at_filter = _build_preferred_date_range_filter("payment_date", created_from, created_to_exclusive)
            if created_at_filter:
                payments_query = _append_and_filter(payments_query, created_at_filter)

            payments_totals = _get_customer_payments_totals(shop_db, payments_query)

            payments, tab_pagination = paginate_find(
                shop_db.work_order_payments,
                payments_query,
                get_sort_params(request.args, [("payment_date", -1), ("created_at", -1)], ["amount", "payment_method", "payment_date", "created_at"]),
                page,
                per_page,
                projection={
                    "work_order_id": 1,
                    "amount": 1,
                    "payment_method": 1,
                    "notes": 1,
                    "payment_date": 1,
                    "created_at": 1,
                },
            )

            for p in payments:
                tab_items.append(
                    {
                        "id": str(p.get("_id")),
                        "work_order_id": str(p.get("work_order_id")) if p.get("work_order_id") else "",
                        "wo_number": wo_map.get(p.get("work_order_id")) or "-",
                        "amount": _round2(p.get("amount")),
                        "payment_method": (p.get("payment_method") or "cash").replace("_", " "),
                        "notes": p.get("notes") or "",
                        "created_at": _fmt_preferred_dt_label(p.get("payment_date"), p.get("created_at")),
                    }
                )
        else:
            tab_pagination = _empty_pagination(page, per_page)

    elif tab == "estimates":
        estimate_statuses = ["estimate", "estimated", "quote", "quoted"]
        estimate_query = {
            "shop_id": shop["_id"],
            "customer_id": cid,
            "is_active": True,
            "status": {"$in": estimate_statuses},
        }
        estimate_search = build_regex_search_filter(
            q,
            text_fields=["status"],
            numeric_fields=["wo_number", "grand_total", "totals.grand_total", "totals.parts_total", "totals.labor_total"],
            object_id_fields=["_id", "unit_id", "customer_id", "shop_id", "tenant_id"],
        )
        if estimate_search:
            estimate_query = {"$and": [estimate_query, estimate_search]}

        created_at_filter = _build_preferred_date_range_filter("work_order_date", created_from, created_to_exclusive)
        if created_at_filter:
            estimate_query = _append_and_filter(estimate_query, created_at_filter)

        estimates_totals = _get_customer_work_orders_totals(shop_db, estimate_query)

        estimates, tab_pagination = paginate_find(
            shop_db.work_orders,
            estimate_query,
            get_sort_params(request.args, [("work_order_date", -1), ("created_at", -1)], ["wo_number", "status", "work_order_date", "grand_total", "created_at"]),
            page,
            per_page,
            projection={
                "wo_number": 1,
                "status": 1,
                "work_order_date": 1,
                "created_at": 1,
                "totals": 1,
                "grand_total": 1,
                "unit_id": 1,
            },
        )

        unit_ids = [wo.get("unit_id") for wo in estimates if wo.get("unit_id")]
        units_map = {}
        if unit_ids:
            for unit in shop_db.units.find({"_id": {"$in": unit_ids}}):
                units_map[unit.get("_id")] = _unit_label(unit)

        for wo in estimates:
            tab_items.append(
                {
                    "id": str(wo.get("_id")),
                    "wo_number": wo.get("wo_number") or "-",
                    "status": (wo.get("status") or "estimate").strip().lower(),
                    "created_at": _fmt_preferred_dt_label(wo.get("work_order_date"), wo.get("created_at")),
                    "unit": units_map.get(wo.get("unit_id")) or "-",
                    "grand_total": _order_grand_total(wo),
                }
            )

    else:
        tab_pagination = _empty_pagination(page, per_page)

    return _render_app_page(
        "public/customers/customer_details.html",
        active_page="customers",
        customer=customer_view,
        customer_id=str(cid),
        active_tab=tab,
        q=q,
        tab_items=tab_items,
        pagination=tab_pagination,
        work_orders_totals=work_orders_totals,
        payments_totals=payments_totals,
        estimates_totals=estimates_totals,
        paid_status=paid_status,
        date_from=date_from,
        date_to=date_to,
        date_preset=date_preset,
        labor_rates=labor_rates,
        sort_by=(request.args.get("sort_by") or "").strip(),
        sort_dir=(request.args.get("sort_dir") or "").strip(),
    )


@customers_bp.get("/customers/<customer_id>/units/<unit_id>")
@login_required
@permission_required("customers.view")
def customer_unit_details_page(customer_id, unit_id):
    coll, shop, master = _customers_collection()
    if coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("customers.customers_page"))

    cid = _oid(customer_id)
    uid = _oid(unit_id)
    if not cid or not uid:
        flash("Invalid customer or unit id.", "error")
        return redirect(url_for("customers.customers_page"))

    customer = coll.find_one({"_id": cid, "shop_id": shop["_id"]})
    if not customer:
        flash("Customer not found.", "error")
        return redirect(url_for("customers.customers_page"))

    shop_db = coll.database
    unit = shop_db.units.find_one(
        {"_id": uid, "customer_id": cid, "shop_id": shop["_id"], "is_active": True}
    )
    if not unit:
        flash("Unit not found for this customer.", "error")
        return redirect(url_for("customers.customer_details_page", customer_id=str(cid), tab="units"))

    tab = (request.args.get("tab") or "work_orders").strip().lower()
    if tab not in {"work_orders", "details"}:
        tab = "work_orders"

    q = (request.args.get("q") or "").strip()
    date_filters = build_date_range_filters(request.args, default_preset="all_time")
    date_from = date_filters["date_from"]
    date_to = date_filters["date_to"]
    date_preset = date_filters["date_preset"]
    created_from = date_filters["created_from"]
    created_to_exclusive = date_filters["created_to_exclusive"]
    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)

    tab_items = []
    pagination = None
    work_orders_totals = {"labor_total": 0.0, "parts_total": 0.0, "grand_total": 0.0}

    if tab == "work_orders":
        wo_query = {
            "shop_id": shop["_id"],
            "customer_id": cid,
            "unit_id": uid,
            "is_active": True,
        }
        wo_search = build_regex_search_filter(
            q,
            text_fields=["status"],
            numeric_fields=["wo_number", "grand_total", "totals.grand_total", "totals.parts_total", "totals.labor_total"],
            object_id_fields=["_id", "unit_id", "customer_id", "shop_id", "tenant_id"],
        )
        if wo_search:
            wo_query = {"$and": [wo_query, wo_search]}

        created_at_filter = _build_preferred_date_range_filter("work_order_date", created_from, created_to_exclusive)
        if created_at_filter:
            wo_query = _append_and_filter(wo_query, created_at_filter)

        work_orders_totals = _get_customer_work_orders_totals(shop_db, wo_query)

        # Compute total paid across all matching WOs for remaining calculation
        all_wo_ids = [
            doc["_id"]
            for doc in shop_db.work_orders.find(wo_query, {"_id": 1})
        ]
        all_paid_map = _build_paid_map(shop_db.work_order_payments, all_wo_ids)
        total_paid = _round2(sum(all_paid_map.values()))
        total_remaining = _round2(work_orders_totals["grand_total"] - total_paid)
        if total_remaining < 0:
            total_remaining = 0.0
        work_orders_totals["paid_total"] = total_paid
        work_orders_totals["remaining_total"] = total_remaining

        rows, pagination = paginate_find(
            shop_db.work_orders,
            wo_query,
            get_sort_params(request.args, [("work_order_date", -1), ("created_at", -1)], ["wo_number", "status", "work_order_date", "grand_total", "created_at"]),
            page,
            per_page,
            projection={
                "wo_number": 1,
                "status": 1,
                "work_order_date": 1,
                "created_at": 1,
                "totals": 1,
                "grand_total": 1,
            },
        )

        row_ids = [x.get("_id") for x in rows if x.get("_id")]
        paid_map = _build_paid_map(shop_db.work_order_payments, row_ids)

        for row in rows:
            row_id = row.get("_id")
            grand_total = _order_grand_total(row)
            paid_amount = _round2(paid_map.get(row_id, 0.0))
            remaining = _round2(grand_total - paid_amount)
            if remaining < 0:
                remaining = 0.0

            tab_items.append(
                {
                    "id": str(row_id),
                    "wo_number": row.get("wo_number") or "-",
                    "status": (row.get("status") or "open").strip().lower(),
                    "created_at": _fmt_preferred_dt_label(row.get("work_order_date"), row.get("created_at")),
                    "grand_total": grand_total,
                    "paid_amount": paid_amount,
                    "remaining_balance": remaining,
                }
            )

    else:
        pagination = _empty_pagination(page, per_page)

    unit_view = {
        "id": str(unit.get("_id")),
        "unit_number": unit.get("unit_number") or "-",
        "label": _unit_label(unit),
        "vin": unit.get("vin") or "-",
        "year": unit.get("year") if unit.get("year") is not None else "-",
        "make": unit.get("make") or "-",
        "model": unit.get("model") or "-",
        "type": unit.get("type") or "-",
        "mileage": unit.get("mileage") if unit.get("mileage") is not None else "-",
        "created_at": _fmt_dt_label(unit.get("created_at")),
        "updated_at": _fmt_dt_label(unit.get("updated_at")),
        "raw_unit_number": unit.get("unit_number") or "",
        "raw_vin": unit.get("vin") or "",
        "raw_year": unit.get("year") if unit.get("year") is not None else "",
        "raw_make": unit.get("make") or "",
        "raw_model": unit.get("model") or "",
        "raw_type": unit.get("type") or "",
        "raw_mileage": unit.get("mileage") if unit.get("mileage") is not None else "",
    }

    return _render_app_page(
        "public/customers/unit_details.html",
        active_page="customers",
        customer_id=str(cid),
        customer_label=_customer_label(customer),
        unit=unit_view,
        active_tab=tab,
        q=q,
        date_preset=date_preset,
        date_from=date_from,
        date_to=date_to,
        tab_items=tab_items,
        work_orders_totals=work_orders_totals if tab == "work_orders" else None,
        pagination=pagination,
        sort_by=(request.args.get("sort_by") or "").strip(),
        sort_dir=(request.args.get("sort_dir") or "").strip(),
    )


@customers_bp.post("/customers/<customer_id>/units/<unit_id>/update")
@login_required
@permission_required("customers.edit")
def customer_unit_update(customer_id, unit_id):
    coll, shop, master = _customers_collection()
    if coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("customers.customers_page"))

    cid = _oid(customer_id)
    uid = _oid(unit_id)
    if not cid or not uid:
        flash("Invalid customer or unit id.", "error")
        return redirect(url_for("customers.customers_page"))

    customer = coll.find_one({"_id": cid, "shop_id": shop["_id"]})
    if not customer:
        flash("Customer not found.", "error")
        return redirect(url_for("customers.customers_page"))

    shop_db = coll.database
    unit = shop_db.units.find_one(
        {"_id": uid, "customer_id": cid, "shop_id": shop["_id"], "is_active": True}
    )
    if not unit:
        flash("Unit not found for this customer.", "error")
        return redirect(url_for("customers.customer_details_page", customer_id=str(cid), tab="units"))

    def _strip(key):
        return (request.form.get(key) or "").strip() or None

    def _int(key):
        raw = (request.form.get(key) or "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None

    update_fields = {
        "unit_number": _strip("unit_number"),
        "vin": _strip("vin"),
        "make": _strip("make"),
        "model": _strip("model"),
        "year": _int("year"),
        "type": _strip("type"),
        "mileage": _int("mileage"),
        "updated_at": utcnow(),
        "updated_by": _oid(session.get(SESSION_USER_ID)),
    }

    shop_db.units.update_one({"_id": uid}, {"$set": update_fields})
    flash("Unit updated.", "success")
    return redirect(url_for("customers.customer_unit_details_page", customer_id=str(cid), unit_id=str(uid), tab="details"))


@customers_bp.post("/customers/<customer_id>/units/<unit_id>/deactivate")
@login_required
@permission_required("customers.edit")
def customer_unit_deactivate(customer_id, unit_id):
    coll, shop, master = _customers_collection()
    if coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("customers.customers_page"))

    cid = _oid(customer_id)
    uid = _oid(unit_id)
    if not cid or not uid:
        flash("Invalid customer or unit id.", "error")
        return redirect(url_for("customers.customers_page"))

    shop_db = coll.database
    unit = shop_db.units.find_one(
        {"_id": uid, "customer_id": cid, "shop_id": shop["_id"], "is_active": True}
    )
    if not unit:
        flash("Unit not found or already deactivated.", "error")
        return redirect(url_for("customers.customer_details_page", customer_id=str(cid), tab="units"))

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    shop_db.units.update_one(
        {"_id": uid},
        {"$set": {
            "is_active": False,
            "updated_at": now,
            "updated_by": user_oid,
            "deactivated_at": now,
            "deactivated_by": user_oid,
        }},
    )

    flash("Unit deactivated.", "success")
    return redirect(url_for("customers.customer_details_page", customer_id=str(cid), tab="units"))


@customers_bp.post("/customers/create")
@login_required
@permission_required("customers.edit")
def customers_create():
    coll, shop, master = _customers_collection()
    if coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("customers.customers_page"))

    tenant_oid = _oid(session.get(SESSION_TENANT_ID))
    if not tenant_oid:
        flash("Tenant session missing. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    company_name = (request.form.get("company_name") or "").strip()
    contacts = build_contacts_from_form(request.form)
    address = (request.form.get("address") or "").strip()
    taxable = (request.form.get("taxable") or "").strip().lower() in {"1", "true", "on", "yes"}

    if not company_name and not has_contact_name(contacts):
        flash("Company name or at least one contact name is required.", "error")
        return redirect(url_for("customers.customers_page"))

    if len(address) < 5:
        flash("Customer address is required.", "error")
        return redirect(url_for("customers.customers_page"))

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    default_rate_doc = coll.database.labor_rates.find_one(
        {"shop_id": shop["_id"], "is_active": True, "code": "standard"},
        {"_id": 1},
    )
    if not default_rate_doc:
        default_rate_doc = coll.database.labor_rates.find_one(
            {"shop_id": shop["_id"], "is_active": True},
            {"_id": 1},
            sort=[("name", 1)],
        )

    doc = {
        "company_name": company_name or None,
        "contacts": contacts,
        "address": address or None,
        "taxable": taxable,
        "default_labor_rate": default_rate_doc.get("_id") if default_rate_doc else None,

        "is_active": True,

        "created_at": now,
        "updated_at": now,
        "created_by": user_oid,
        "updated_by": user_oid,
        "deactivated_at": None,
        "deactivated_by": None,

        # как и vendors — в SHOP DB, но храним ссылки
        "shop_id": shop["_id"],
        "tenant_id": tenant_oid,
    }
    doc.update(build_customer_legacy_contact_fields(contacts))

    coll.insert_one(doc)

    flash("Customer created successfully.", "success")
    return redirect(url_for("customers.customers_page"))


@customers_bp.post("/customers/<customer_id>/deactivate")
@login_required
@permission_required("customers.deactivate")
def customers_deactivate(customer_id):
    coll, shop, master = _customers_collection()
    if coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("customers.customers_page"))

    cid = _oid(customer_id)
    if not cid:
        flash("Invalid customer id.", "error")
        return redirect(url_for("customers.customers_page"))

    existing = coll.find_one({"_id": cid})
    if not existing:
        flash("Customer not found.", "error")
        return redirect(url_for("customers.customers_page"))

    if existing.get("is_active") is False:
        flash("Customer is already deactivated.", "info")
        return redirect(url_for("customers.customers_page"))

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    coll.update_one(
        {"_id": cid},
        {"$set": {
            "is_active": False,
            "updated_at": now,
            "updated_by": user_oid,
            "deactivated_at": now,
            "deactivated_by": user_oid,
        }},
    )

    flash("Customer deactivated.", "success")
    return redirect(url_for("customers.customers_page"))


@customers_bp.post("/customers/<customer_id>/update")
@login_required
@permission_required("customers.edit")
def customer_details_update(customer_id):
    coll, shop, master = _customers_collection()
    if coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("customers.customers_page"))

    cid = _oid(customer_id)
    if not cid:
        flash("Invalid customer id.", "error")
        return redirect(url_for("customers.customers_page"))

    customer = coll.find_one({"_id": cid, "shop_id": shop["_id"]})
    if not customer:
        flash("Customer not found.", "error")
        return redirect(url_for("customers.customers_page"))

    company_name = (request.form.get("company_name") or "").strip()
    contacts = build_contacts_from_form(request.form)
    address = (request.form.get("address") or "").strip()
    taxable = (request.form.get("taxable") or "").strip().lower() in {"1", "true", "on", "yes"}
    default_labor_rate_id = _oid(request.form.get("default_labor_rate"))
    if request.form.get("default_labor_rate") and not default_labor_rate_id:
        flash("Selected default labor rate is invalid.", "error")
        return redirect(url_for("customers.customer_details_page", customer_id=str(cid), tab="details"))

    if default_labor_rate_id:
        rate_exists = coll.database.labor_rates.count_documents(
            {"_id": default_labor_rate_id, "shop_id": shop["_id"], "is_active": True}
        )
        if rate_exists == 0:
            flash("Selected default labor rate is invalid.", "error")
            return redirect(url_for("customers.customer_details_page", customer_id=str(cid), tab="details"))

    if not company_name and not has_contact_name(contacts):
        flash("Company name or at least one contact name is required.", "error")
        return redirect(url_for("customers.customer_details_page", customer_id=str(cid), tab="details"))

    if len(address) < 5:
        flash("Customer address is required.", "error")
        return redirect(url_for("customers.customer_details_page", customer_id=str(cid), tab="details"))

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    update_data = {
        "company_name": company_name or None,
        "contacts": contacts,
        "address": address or None,
        "taxable": taxable,
        "default_labor_rate": default_labor_rate_id,
        "updated_at": now,
        "updated_by": user_oid,
    }
    update_data.update(build_customer_legacy_contact_fields(contacts))

    coll.update_one({"_id": cid}, {"$set": update_data})

    flash("Customer updated successfully.", "success")
    return redirect(url_for("customers.customer_details_page", customer_id=str(cid), tab="details"))


@customers_bp.get("/customers/api/<customer_id>")
@login_required
@permission_required("customers.view")
def customers_api_get(customer_id):
    coll, shop, master = _customers_collection()
    if coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    cid = _oid(customer_id)
    if not cid:
        return jsonify({"ok": False, "error": "Invalid customer id"}), 400

    customer = coll.find_one({"_id": cid, "shop_id": shop["_id"]})
    if not customer:
        return jsonify({"ok": False, "error": "Customer not found"}), 404

    return jsonify(
        {
            "ok": True,
            "item": {
                "_id": str(customer.get("_id")),
                "company_name": customer.get("company_name") or "",
                "first_name": (main_contact := (get_main_contact(customer, entity_type="customer") or {})).get("first_name") or "",
                "last_name": main_contact.get("last_name") or "",
                "phone": get_main_contact_phone(customer, entity_type="customer") or "",
                "email": get_main_contact_email(customer, entity_type="customer") or "",
                "contacts": get_contacts(customer, entity_type="customer"),
                "address": customer.get("address") or "",
                "taxable": bool(customer.get("taxable", False)),
                "is_active": customer.get("is_active", True),
            },
        }
    )


@customers_bp.post("/customers/api/<customer_id>/update")
@login_required
@permission_required("customers.edit")
def customers_api_update(customer_id):
    coll, shop, master = _customers_collection()
    if coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    cid = _oid(customer_id)
    if not cid:
        return jsonify({"ok": False, "error": "Invalid customer id"}), 400

    customer = coll.find_one({"_id": cid, "shop_id": shop["_id"]})
    if not customer:
        return jsonify({"ok": False, "error": "Customer not found"}), 404

    data = request.get_json(silent=True) or {}
    company_name = (data.get("company_name") or "").strip()
    contacts = build_contacts_from_payload(data)
    address = (data.get("address") or "").strip()
    taxable_raw = data.get("taxable", False)
    taxable = bool(taxable_raw) if isinstance(taxable_raw, bool) else str(taxable_raw).strip().lower() in {"1", "true", "on", "yes"}

    if not company_name and not has_contact_name(contacts):
        return jsonify({"ok": False, "error": "Company name or at least one contact name is required."}), 400

    if len(address) < 5:
        return jsonify({"ok": False, "error": "Customer address is required."}), 400

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    update_data = {
        "company_name": company_name or None,
        "contacts": contacts,
        "address": address or None,
        "taxable": taxable,
        "updated_at": now,
        "updated_by": user_oid,
    }
    update_data.update(build_customer_legacy_contact_fields(contacts))

    coll.update_one({"_id": cid}, {"$set": update_data})

    return jsonify({"ok": True, "message": "Customer updated successfully"})


@customers_bp.post("/customers/api/<customer_id>/deactivate")
@login_required
@permission_required("customers.deactivate")
def customers_api_deactivate(customer_id):
    coll, shop, master = _customers_collection()
    if coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    cid = _oid(customer_id)
    if not cid:
        return jsonify({"ok": False, "error": "Invalid customer id"}), 400

    customer = coll.find_one({"_id": cid, "shop_id": shop["_id"]})
    if not customer:
        return jsonify({"ok": False, "error": "Customer not found"}), 404

    if customer.get("is_active") is False:
        return jsonify({"ok": False, "error": "Customer is already deactivated"}), 400

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    coll.update_one(
        {"_id": cid},
        {
            "$set": {
                "is_active": False,
                "updated_at": now,
                "updated_by": user_oid,
                "deactivated_at": now,
                "deactivated_by": user_oid,
            }
        },
    )

    return jsonify({"ok": True, "message": "Customer deactivated"})


@customers_bp.get("/customers/api/work_orders/<wo_id>/labors")
@login_required
@permission_required("customers.view")
def customers_api_wo_labors(wo_id):
    """Return labors list for a work order (lightweight, for expand-in-place)."""
    coll, shop, master = _customers_collection()
    if coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    woid = _oid(wo_id)
    if not woid:
        return jsonify({"ok": False, "error": "Invalid work order id"}), 400

    db, _ = _get_shop_db(master)
    if db is None:
        return jsonify({"ok": False, "error": "Database not configured"}), 400

    wo = db.work_orders.find_one(
        {"_id": woid, "shop_id": shop["_id"], "is_active": {"$ne": False}},
        {"labors": 1},
    )
    if not wo:
        return jsonify({"ok": False, "error": "Work order not found"}), 404

    labors_out = []
    for labor_block in wo.get("labors") or []:
        if not isinstance(labor_block, dict):
            continue
        labor_info = labor_block.get("labor") or {}
        parts_list = labor_block.get("parts") or []

        parts_out = []
        for p in parts_list:
            if not isinstance(p, dict):
                continue
            parts_out.append({
                "part_number": str(p.get("part_number") or ""),
                "description": str(p.get("description") or ""),
                "qty": p.get("qty") or 0,
                "price": float(p.get("price") or 0),
            })

        labors_out.append({
            "description": str(labor_info.get("description") or ""),
            "hours": str(labor_info.get("hours") or ""),
            "labor_total": float(labor_info.get("labor_full_total") or 0),
            "parts": parts_out,
        })

    return jsonify({"ok": True, "labors": labors_out})


