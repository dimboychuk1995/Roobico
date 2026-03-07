from __future__ import annotations

from datetime import datetime, timezone

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
from app.utils.pagination import get_pagination_params, paginate_find
from app.utils.mongo_search import build_regex_search_filter
from app.utils.permissions import permission_required
from app.utils.display_datetime import format_date_mmddyyyy


def utcnow():
    return datetime.now(timezone.utc)


def _round2(value):
    try:
        return round(float(value or 0) + 1e-12, 2)
    except Exception:
        return 0.0


def _fmt_dt_label(dt):
    return format_date_mmddyyyy(dt)


def _customer_label(customer: dict) -> str:
    company = (customer.get("company_name") or "").strip()
    if company:
        return company
    first_name = (customer.get("first_name") or "").strip()
    last_name = (customer.get("last_name") or "").strip()
    full_name = f"{first_name} {last_name}".strip()
    return full_name or "-"


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
        return redirect(url_for("main.dashboard"))

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
        [("is_active", -1), ("company_name", 1), ("last_name", 1), ("first_name", 1), ("created_at", -1)],
        page,
        per_page,
    )

    _attach_customers_current_balances(customers, coll.database, shop["_id"])

    return _render_app_page(
        "public/customers.html",
        active_page="customers",
        customers=customers,
        pagination=pagination,
        q=q,
    )


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
        "first_name": customer.get("first_name") or "",
        "last_name": customer.get("last_name") or "",
        "phone": customer.get("phone") or "",
        "email": customer.get("email") or "",
        "address": customer.get("address") or "",
        "default_labor_rate": str(customer.get("default_labor_rate")) if isinstance(customer.get("default_labor_rate"), ObjectId) else "",
        "default_labor_rate_id": selected_rate_id,
        "is_active": customer.get("is_active", True),
        "created_at": _fmt_dt_label(customer.get("created_at")),
        "updated_at": _fmt_dt_label(customer.get("updated_at")),
        "current_balance": _customer_current_balance(shop_db, shop["_id"], cid),
        "default_labor_rate_name": selected_rate_name,
    }

    tab_items = []
    tab_pagination = None

    if tab == "work_orders":
        wo_query = {
            "shop_id": shop["_id"],
            "customer_id": cid,
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
        work_orders, tab_pagination = paginate_find(
            shop_db.work_orders,
            wo_query,
            [("created_at", -1)],
            page,
            per_page,
            projection={
                "wo_number": 1,
                "status": 1,
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
                    "created_at": _fmt_dt_label(wo.get("created_at")),
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
            [("created_at", -1)],
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

            payments, tab_pagination = paginate_find(
                shop_db.work_order_payments,
                payments_query,
                [("created_at", -1)],
                page,
                per_page,
                projection={
                    "work_order_id": 1,
                    "amount": 1,
                    "payment_method": 1,
                    "notes": 1,
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
                        "created_at": _fmt_dt_label(p.get("created_at")),
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

        estimates, tab_pagination = paginate_find(
            shop_db.work_orders,
            estimate_query,
            [("created_at", -1)],
            page,
            per_page,
            projection={
                "wo_number": 1,
                "status": 1,
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
                    "created_at": _fmt_dt_label(wo.get("created_at")),
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
        labor_rates=labor_rates,
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
    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)

    tab_items = []
    pagination = None

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
        rows, pagination = paginate_find(
            shop_db.work_orders,
            wo_query,
            [("created_at", -1)],
            page,
            per_page,
            projection={
                "wo_number": 1,
                "status": 1,
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
                    "created_at": _fmt_dt_label(row.get("created_at")),
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
    }

    return _render_app_page(
        "public/customers/unit_details.html",
        active_page="customers",
        customer_id=str(cid),
        customer_label=_customer_label(customer),
        unit=unit_view,
        active_tab=tab,
        q=q,
        tab_items=tab_items,
        pagination=pagination,
    )


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
    first_name = (request.form.get("first_name") or "").strip()
    last_name = (request.form.get("last_name") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    address = (request.form.get("address") or "").strip()

    # Минимальная валидация: пусть будет обязательна компания ИЛИ имя+фамилия
    if not company_name and not (first_name and last_name):
        flash("Company name or First+Last name is required.", "error")
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
        "first_name": first_name or None,
        "last_name": last_name or None,
        "phone": phone or None,
        "email": email or None,
        "address": address or None,
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
    first_name = (request.form.get("first_name") or "").strip()
    last_name = (request.form.get("last_name") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    address = (request.form.get("address") or "").strip()
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

    if not company_name and not (first_name and last_name):
        flash("Company name or First+Last name is required.", "error")
        return redirect(url_for("customers.customer_details_page", customer_id=str(cid), tab="details"))

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    coll.update_one(
        {"_id": cid},
        {
            "$set": {
                "company_name": company_name or None,
                "first_name": first_name or None,
                "last_name": last_name or None,
                "phone": phone or None,
                "email": email or None,
                "address": address or None,
                "default_labor_rate": default_labor_rate_id,
                "updated_at": now,
                "updated_by": user_oid,
            }
        },
    )

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
                "first_name": customer.get("first_name") or "",
                "last_name": customer.get("last_name") or "",
                "phone": customer.get("phone") or "",
                "email": customer.get("email") or "",
                "address": customer.get("address") or "",
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
    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    phone = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip().lower()
    address = (data.get("address") or "").strip()

    if not company_name and not (first_name and last_name):
        return jsonify({"ok": False, "error": "Company name or First+Last name is required."}), 400

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    coll.update_one(
        {"_id": cid},
        {
            "$set": {
                "company_name": company_name or None,
                "first_name": first_name or None,
                "last_name": last_name or None,
                "phone": phone or None,
                "email": email or None,
                "address": address or None,
                "updated_at": now,
                "updated_by": user_oid,
            }
        },
    )

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


