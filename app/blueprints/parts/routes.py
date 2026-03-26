from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from flask import request, redirect, url_for, flash, session, jsonify

from app.blueprints.main.routes import _render_app_page
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import login_required, SESSION_TENANT_ID, SESSION_USER_ID
from app.utils.pagination import get_pagination_params, paginate_find
from app.utils.mongo_search import build_regex_search_filter
from app.utils.parts_search import build_parts_search_terms, build_query_tokens, part_matches_query
from app.utils.permissions import permission_required
from app.utils.display_datetime import (
    format_date_mmddyyyy,
    format_preferred_shop_date,
    get_active_shop_today_iso,
    shop_date_input_value,
    shop_local_date_to_utc,
)
from app.utils.date_filters import build_date_range_filters

from . import parts_bp


NON_INVENTORY_AMOUNT_TYPES = {
    "shop_supply",
    "tools",
    "utilities",
    "payment_to_another_service",
}


def utcnow():
    return datetime.now(timezone.utc)


def _oid(value) -> Optional[ObjectId]:
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _tenant_id_variants():
    """
    Нужна из-за того, что tenant_id исторически мог быть то строкой, то ObjectId.
    """
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


def _parts_collections():
    """
    Все коллекции живут в SHOP DB:
      - parts
      - vendors
      - parts_categories
      - parts_locations
      - parts_orders
    """
    master = get_master_db()
    db, shop = _get_shop_db(master)
    if db is None:
        return None, None, None, None, None, shop, master

    return (
        db.parts,
        db.vendors,
        db.parts_categories,
        db.parts_locations,
        db.parts_orders,
        shop,
        master,
    )

def _parse_int(value: str, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _parse_float(value: str, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


def _parse_non_inventory_amounts(raw_lines):
    if not isinstance(raw_lines, list):
        return [], None

    out = []
    for line in raw_lines:
        if not isinstance(line, dict):
            continue

        amount_type = str(line.get("type") or "").strip().lower()
        description = str(line.get("description") or "").strip()
        amount = _parse_float(line.get("amount"), default=0.0)

        if not amount_type and not description and amount <= 0:
            continue

        if amount_type not in NON_INVENTORY_AMOUNT_TYPES:
            return [], "Select non inventory type."

        if amount < 0:
            return [], "Non inventory amount cannot be negative."

        if amount <= 0:
            return [], "Non inventory amount must be greater than 0."

        if not description:
            return [], "Non inventory amount description is required."

        out.append({
            "type": amount_type,
            "description": description,
            "amount": float(amount),
        })

    return out, None


def _parts_order_amounts(order_doc: dict):
    items_amount = 0.0
    for item in (order_doc.get("items") or []):
        if not isinstance(item, dict):
            continue
        qty = max(0, _parse_int(item.get("quantity"), default=0))
        price = max(0.0, _parse_float(item.get("price"), default=0.0))
        items_amount += qty * price

    non_inventory_amount = 0.0
    for line in (order_doc.get("non_inventory_amounts") or []):
        if not isinstance(line, dict):
            continue
        non_inventory_amount += max(0.0, _parse_float(line.get("amount"), default=0.0))

    total_amount = items_amount + non_inventory_amount
    return {
        "items_amount": float(items_amount),
        "non_inventory_amount": float(non_inventory_amount),
        "total_amount": float(total_amount),
    }


def _sum_active_order_payments(payments_coll, order_id: ObjectId) -> float:
    if payments_coll is None or not order_id:
        return 0.0

    pipeline = [
        {
            "$match": {
                "parts_order_id": order_id,
                "is_active": True,
            }
        },
        {
            "$group": {
                "_id": None,
                "amount_total": {"$sum": {"$ifNull": ["$amount", 0]}},
            }
        },
    ]
    rows = list(payments_coll.aggregate(pipeline))
    if not rows:
        return 0.0
    row = rows[0] if isinstance(rows[0], dict) else {}
    return float(_parse_float(row.get("amount_total"), default=0.0))


def _build_parts_order_paid_map(payments_coll, order_ids: list[ObjectId]) -> dict[ObjectId, float]:
    if payments_coll is None or not order_ids:
        return {}

    pipeline = [
        {
            "$match": {
                "parts_order_id": {"$in": order_ids},
                "is_active": True,
            }
        },
        {
            "$group": {
                "_id": "$parts_order_id",
                "amount_total": {"$sum": {"$ifNull": ["$amount", 0]}},
            }
        },
    ]

    out: dict[ObjectId, float] = {}
    for row in payments_coll.aggregate(pipeline):
        if not isinstance(row, dict):
            continue
        oid = row.get("_id")
        if oid:
            out[oid] = float(_parse_float(row.get("amount_total"), default=0.0))
    return out


def _payment_status_from_amounts(total_amount: float, paid_amount: float) -> str:
    total = float(_parse_float(total_amount, default=0.0))
    paid = float(_parse_float(paid_amount, default=0.0))
    if total <= 0:
        return "paid"
    if paid <= 0:
        return "unpaid"
    if paid + 0.01 >= total:
        return "paid"
    return "partially_paid"


def _build_parts_order_payment_summary(order_doc: dict, paid_amount: float):
    amounts = _parts_order_amounts(order_doc or {})
    total_amount = float(_parse_float(amounts.get("total_amount"), default=0.0))
    paid = max(0.0, float(_parse_float(paid_amount, default=0.0)))
    remaining = max(0.0, float(total_amount - paid))
    status = _payment_status_from_amounts(total_amount, paid)
    return {
        "total_amount": float(total_amount),
        "paid_amount": float(paid),
        "remaining_balance": float(remaining),
        "payment_status": status,
    }


def _sync_parts_order_payment_state(orders_coll, payments_coll, order_doc: dict, user_oid, now):
    if orders_coll is None or payments_coll is None or not isinstance(order_doc, dict):
        return

    order_id = order_doc.get("_id")
    if not order_id:
        return

    paid_amount = _sum_active_order_payments(payments_coll, order_id)
    summary = _build_parts_order_payment_summary(order_doc, paid_amount)

    orders_coll.update_one(
        {"_id": order_id},
        {
            "$set": {
                "payment_status": summary["payment_status"],
                "paid_amount": float(summary["paid_amount"]),
                "remaining_balance": float(summary["remaining_balance"]),
                "updated_at": now,
                "updated_by": user_oid,
            }
        },
    )


def _get_parts_orders_totals(orders_coll, query: dict):
    totals = {
        "total": 0.0,
        "shop_supply": 0.0,
        "tools": 0.0,
        "utilities": 0.0,
        "another_service": 0.0,
    }

    cursor = orders_coll.find(query, {"items": 1, "non_inventory_amounts": 1})
    for order in cursor:
        amounts = _parts_order_amounts(order)
        totals["total"] += amounts.get("total_amount") or 0.0

        for line in (order.get("non_inventory_amounts") or []):
            if not isinstance(line, dict):
                continue
            amount = max(0.0, _parse_float(line.get("amount"), default=0.0))
            line_type = str(line.get("type") or "").strip().lower()
            if line_type == "shop_supply":
                totals["shop_supply"] += amount
            elif line_type == "tools":
                totals["tools"] += amount
            elif line_type == "utilities":
                totals["utilities"] += amount
            elif line_type == "payment_to_another_service":
                totals["another_service"] += amount

    return {
        "total": float(totals["total"]),
        "shop_supply": float(totals["shop_supply"]),
        "tools": float(totals["tools"]),
        "utilities": float(totals["utilities"]),
        "another_service": float(totals["another_service"]),
    }


def _get_parts_orders_payment_totals(payments_coll, query: dict):
    totals = {
        "payments_total": 0.0,
        "payments_count": 0,
    }
    if payments_coll is None:
        return totals

    pipeline = [
        {"$match": query},
        {
            "$group": {
                "_id": None,
                "payments_total": {"$sum": {"$ifNull": ["$amount", 0]}},
                "payments_count": {"$sum": 1},
            }
        },
    ]
    rows = list(payments_coll.aggregate(pipeline))
    if not rows:
        return totals

    row = rows[0] if isinstance(rows[0], dict) else {}
    return {
        "payments_total": float(_parse_float(row.get("payments_total"), default=0.0)),
        "payments_count": int(_parse_int(row.get("payments_count"), default=0)),
    }


def _get_parts_inventory_totals(parts_coll, query: dict):
    totals = {
        "inventory_cost": 0.0,
        "core_cost": 0.0,
    }

    cursor = parts_coll.find(
        query,
        {
            "in_stock": 1,
            "average_cost": 1,
            "core_has_charge": 1,
            "core_cost": 1,
            "do_not_track_inventory": 1,
        },
    )

    for part in cursor:
        if bool(part.get("do_not_track_inventory")):
            continue

        qty = max(0, _parse_int(part.get("in_stock"), default=0))
        avg_cost = max(0.0, _parse_float(part.get("average_cost"), default=0.0))
        totals["inventory_cost"] += qty * avg_cost

        if bool(part.get("core_has_charge")):
            core_cost = max(0.0, _parse_float(part.get("core_cost"), default=0.0))
            totals["core_cost"] += qty * core_cost

    return {
        "inventory_cost": float(totals["inventory_cost"]),
        "core_cost": float(totals["core_cost"]),
    }


def _validate_ref(coll, ref_id_raw: str, label: str):
    """
    Универсальная валидация ссылок на документы (vendor/category/location) в SHOP DB.
    Если ref_id_raw пустой — вернём (None, None) без ошибки.
    """
    ref_id_raw = (ref_id_raw or "").strip()
    if not ref_id_raw:
        return None, None

    ref_oid = _oid(ref_id_raw)
    if not ref_oid:
        return None, f"Invalid {label} id."

    doc = coll.find_one({"_id": ref_oid})
    if not doc:
        return None, f"{label} not found."

    if doc.get("is_active") is False:
        return None, f"{label} is inactive."

    return ref_oid, None


def _name_from_doc(doc: dict) -> str:
    """
    Универсально вытаскиваем отображаемое имя.
    Подходит и для vendors, и для категорий/локаций.
    """
    return (
        doc.get("name")
        or doc.get("company_name")
        or doc.get("title")
        or doc.get("label")
        or ""
    ).strip()


def _fmt_dt_iso(dt) -> str:
    if isinstance(dt, datetime):
        try:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            return dt.isoformat()
    return ""


def _fmt_dt_label(dt) -> str:
    return format_date_mmddyyyy(dt)


def _fmt_preferred_dt_label(primary_dt, fallback_dt) -> str:
    return format_preferred_shop_date(primary_dt, fallback=fallback_dt)


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


def _build_preferred_date_filter(date_field: str, created_from=None, created_to_exclusive=None):
    created_filter = {}
    if created_from:
        created_filter["$gte"] = created_from
    if created_to_exclusive:
        created_filter["$lt"] = created_to_exclusive
    if not created_filter:
        return None

    return {
        "$or": [
            {date_field: created_filter},
            {date_field: {"$exists": False}, "created_at": created_filter},
            {date_field: None, "created_at": created_filter},
        ]
    }


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


def _get_next_order_number(shop_db, shop_id):
    """
    Get next parts order number using atomic counter.
    Returns integer starting from 1000.
    """
    from pymongo import ReturnDocument
    
    result = shop_db.counters.find_one_and_update(
        {"_id": f"order_number_{shop_id}"},
        {
            "$inc": {"seq": 1},
            "$setOnInsert": {"initial_value": 1000}
        },
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    
    seq = result.get("seq", 1)
    initial = result.get("initial_value", 1000)
    
    return initial + seq - 1


@parts_bp.get("/")
@login_required
@permission_required("parts.view")
def parts_page():
    """
    Показать те запчасти что у нас есть:
      - is_active=True
      - in_stock > 0

    Также сразу тянем справочники для селектов в модалке:
      vendors / categories / locations (active only)

    + last_order_id из session (чтобы показать кнопку Received)
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("dashboard.dashboard"))

    active_tab = (request.args.get("tab") or "parts").strip().lower()
    if active_tab not in {"parts", "orders", "payments", "cores", "cores_returns"}:
        active_tab = "parts"

    q = (request.args.get("q") or "").strip()

    date_filters = _get_date_range_filters(request.args)
    date_preset = date_filters["date_preset"]
    date_from = date_filters["date_from"]
    date_to = date_filters["date_to"]

    parts_page_num, parts_per_page = get_pagination_params(
        request.args,
        default_per_page=20,
        max_per_page=100,
        page_key="parts_page",
        per_page_key="parts_per_page",
    )
    orders_page_num, orders_per_page = get_pagination_params(
        request.args,
        default_per_page=20,
        max_per_page=100,
        page_key="orders_page",
        per_page_key="orders_per_page",
    )
    cores_page_num, cores_per_page = get_pagination_params(
        request.args,
        default_per_page=20,
        max_per_page=100,
        page_key="cores_page",
        per_page_key="cores_per_page",
    )
    payments_page_num, payments_per_page = get_pagination_params(
        request.args,
        default_per_page=20,
        max_per_page=100,
        page_key="payments_page",
        per_page_key="payments_per_page",
    )

    payments_coll = orders_coll.database.parts_order_payments if orders_coll is not None else None

    vendor_ids_by_name = []
    if q and vendors_coll is not None:
        name_query = {
            "$or": [
                {"name": {"$regex": q, "$options": "i"}},
                {"company_name": {"$regex": q, "$options": "i"}},
            ]
        }
        vendor_ids_by_name = [x.get("_id") for x in vendors_coll.find(name_query, {"_id": 1}) if x.get("_id")]

    category_ids_by_name = []
    if q and cats_coll is not None:
        category_ids_by_name = [
            x.get("_id")
            for x in cats_coll.find({"name": {"$regex": q, "$options": "i"}}, {"_id": 1})
            if x.get("_id")
        ]

    location_ids_by_name = []
    if q and locs_coll is not None:
        location_ids_by_name = [
            x.get("_id")
            for x in locs_coll.find({"name": {"$regex": q, "$options": "i"}}, {"_id": 1})
            if x.get("_id")
        ]

    parts_in_stock = []
    pagination = None
    parts_totals = {
        "inventory_cost": 0.0,
        "core_cost": 0.0,
    }

    if active_tab == "parts":
        parts_query = {
            "is_active": True,
            "$or": [
                {"in_stock": {"$gt": 0}},
                {"do_not_track_inventory": True},
            ],
        }
        parts_search_filter = build_regex_search_filter(
            q,
            text_fields=["part_number", "description", "reference", "search_terms"],
            numeric_fields=["in_stock", "average_cost"],
            object_id_fields=["_id", "vendor_id", "category_id", "location_id", "shop_id", "tenant_id"],
        )
        if q:
            extra = []
            if vendor_ids_by_name:
                extra.append({"vendor_id": {"$in": vendor_ids_by_name}})
            if category_ids_by_name:
                extra.append({"category_id": {"$in": category_ids_by_name}})
            if location_ids_by_name:
                extra.append({"location_id": {"$in": location_ids_by_name}})

            if parts_search_filter and extra:
                parts_query = {"$and": [parts_query, {"$or": [parts_search_filter, *extra]}]}
            elif parts_search_filter:
                parts_query = {"$and": [parts_query, parts_search_filter]}
            elif extra:
                parts_query = {"$and": [parts_query, {"$or": extra}]}

        parts_totals = _get_parts_inventory_totals(parts_coll, parts_query)

        parts_in_stock, pagination = paginate_find(
            parts_coll,
            parts_query,
            [("part_number", 1), ("description", 1), ("created_at", -1)],
            parts_page_num,
            parts_per_page,
        )

    # 2) Reference lists for modal selects
    vendors = []
    categories = []
    locations = []

    if vendors_coll is not None:
        vendors = list(
            vendors_coll.find({"is_active": {"$ne": False}})
            .sort([("name", 1), ("company_name", 1), ("created_at", -1)])
        )

    if cats_coll is not None:
        categories = list(
            cats_coll.find({"is_active": {"$ne": False}})
            .sort([("name", 1), ("created_at", -1)])
        )

    if locs_coll is not None:
        locations = list(
            locs_coll.find({"is_active": {"$ne": False}})
            .sort([("name", 1), ("created_at", -1)])
        )

    # 3) Make lookup maps for showing names in the table
    vendor_map = {v["_id"]: _name_from_doc(v) for v in vendors if v.get("_id")}
    category_map = {c["_id"]: _name_from_doc(c) for c in categories if c.get("_id")}
    location_map = {l["_id"]: _name_from_doc(l) for l in locations if l.get("_id")}

    if active_tab == "parts":
        for p in parts_in_stock:
            vid = p.get("vendor_id")
            cid = p.get("category_id")
            lid = p.get("location_id")

            if vid:
                p["vendor_name"] = vendor_map.get(vid) or ""
            if cid:
                p["category_name"] = category_map.get(cid) or ""
            if lid:
                p["location_name"] = location_map.get(lid) or ""

            misc_charges_safe = []
            for charge in (p.get("misc_charges") or []):
                if not isinstance(charge, dict):
                    continue
                misc_charges_safe.append(
                    {
                        "description": str(charge.get("description") or ""),
                        "price": float(_parse_float(charge.get("price"), default=0.0)),
                    }
                )

            p["edit_payload"] = {
                "part_number": str(p.get("part_number") or ""),
                "description": str(p.get("description") or ""),
                "reference": str(p.get("reference") or ""),
                "vendor_id": str(p.get("vendor_id")) if p.get("vendor_id") else "",
                "category_id": str(p.get("category_id")) if p.get("category_id") else "",
                "location_id": str(p.get("location_id")) if p.get("location_id") else "",
                "in_stock": int(_parse_int(p.get("in_stock"), default=0)),
                "average_cost": float(_parse_float(p.get("average_cost"), default=0.0)),
                "do_not_track_inventory": bool(p.get("do_not_track_inventory")),
                "core_has_charge": bool(p.get("core_has_charge")),
                "core_cost": float(_parse_float(p.get("core_cost"), default=0.0)),
                "misc_has_charge": bool(p.get("misc_has_charge")),
                "misc_charges": misc_charges_safe,
            }

    last_order_id = session.get("last_parts_order_id")

    # Get orders list for Orders tab
    orders_list = []
    orders_pagination = None
    orders_totals = {
        "total": 0.0,
        "shop_supply": 0.0,
        "tools": 0.0,
        "utilities": 0.0,
        "another_service": 0.0,
    }
    if orders_coll is not None and active_tab == "orders":
        orders_query = {"shop_id": shop["_id"], "is_active": {"$ne": False}}

        created_filter = _build_preferred_date_filter(
            "order_date",
            date_filters["created_from"],
            date_filters["created_to_exclusive"],
        )
        if created_filter:
            orders_query = {"$and": [orders_query, created_filter]}

        orders_search_filter = build_regex_search_filter(
            q,
            text_fields=["status", "vendor_bill"],
            numeric_fields=["order_number"],
            object_id_fields=["_id", "vendor_id", "shop_id", "tenant_id", "created_by", "updated_by"],
        )
        if q and vendor_ids_by_name:
            if orders_search_filter:
                orders_query = {
                    "$and": [
                        orders_query,
                        {"$or": [orders_search_filter, {"vendor_id": {"$in": vendor_ids_by_name}}]},
                    ]
                }
            else:
                orders_query = {"$and": [orders_query, {"vendor_id": {"$in": vendor_ids_by_name}}]}
        elif orders_search_filter:
            orders_query = {"$and": [orders_query, orders_search_filter]}

        orders_totals = _get_parts_orders_totals(orders_coll, orders_query)

        orders_rows, orders_pagination = paginate_find(
            orders_coll,
            orders_query,
            [("order_date", -1), ("created_at", -1)],
            orders_page_num,
            orders_per_page,
            projection={
                "_id": 1,
                "vendor_id": 1,
                "order_number": 1,
                "status": 1,
                "vendor_bill": 1,
                "order_date": 1,
                "created_at": 1,
                "items": 1,
                "non_inventory_amounts": 1,
            },
        )
        
        # Get vendor names for orders
        vendor_ids = [o.get("vendor_id") for o in orders_rows if o.get("vendor_id")]
        vendors_map = {}
        if vendor_ids and vendors_coll is not None:
            for v in vendors_coll.find({"_id": {"$in": vendor_ids}}):
                vendors_map[v.get("_id")] = _name_from_doc(v)
        
        order_ids = [o.get("_id") for o in orders_rows if o.get("_id")]
        paid_map = _build_parts_order_paid_map(payments_coll, order_ids) if payments_coll is not None else {}

        for order in orders_rows:
            paid_amount = paid_map.get(order.get("_id"), 0.0)
            payment_summary = _build_parts_order_payment_summary(order, paid_amount)
            amounts = _parts_order_amounts(order)

            order_items_inline = []
            for item in (order.get("items") or []):
                if not isinstance(item, dict):
                    continue
                part_id = item.get("part_id")
                order_items_inline.append(
                    {
                        "part_id": str(part_id) if part_id else "",
                        "part_number": str(item.get("part_number") or ""),
                        "description": str(item.get("description") or ""),
                        "quantity": int(_parse_int(item.get("quantity"), default=0)),
                        "price": float(_parse_float(item.get("price"), default=0.0)),
                    }
                )

            non_inventory_inline = []
            for line in (order.get("non_inventory_amounts") or []):
                if not isinstance(line, dict):
                    continue
                non_inventory_inline.append(
                    {
                        "type": str(line.get("type") or "shop_supply").strip().lower(),
                        "description": str(line.get("description") or "").strip(),
                        "amount": float(_parse_float(line.get("amount"), default=0.0)),
                    }
                )

            orders_list.append({
                "id": str(order.get("_id")),
                "order_number": order.get("order_number"),
                "vendor_id": str(order.get("vendor_id")) if order.get("vendor_id") else "",
                "vendor": vendors_map.get(order.get("vendor_id")) or "-",
                "status": order.get("status") or "ordered",
                "vendor_bill": str(order.get("vendor_bill") or "").strip(),
                "items": order_items_inline,
                "non_inventory_amounts": non_inventory_inline,
                "payment_status": payment_summary.get("payment_status") or "unpaid",
                "paid_amount": float(payment_summary.get("paid_amount") or 0.0),
                "remaining_balance": float(payment_summary.get("remaining_balance") or 0.0),
                "items_count": len(order.get("items") or []),
                "total_amount": amounts.get("total_amount") or 0.0,
                "created_at": _fmt_preferred_dt_label(order.get("order_date"), order.get("created_at")),
                "order_date": shop_date_input_value(order.get("order_date") or order.get("created_at"), default_today=True),
            })

    # Payments tab list
    payments_list = []
    payments_pagination = None
    payments_totals = {"payments_total": 0.0, "payments_count": 0}
    if payments_coll is not None and orders_coll is not None and active_tab == "payments":
        payments_query = {"shop_id": shop["_id"], "is_active": True}

        created_filter = _build_preferred_date_filter(
            "payment_date",
            date_filters["created_from"],
            date_filters["created_to_exclusive"],
        )
        if created_filter:
            payments_query = {"$and": [payments_query, created_filter]}

        payments_search_filter = build_regex_search_filter(
            q,
            text_fields=["payment_method", "notes"],
            numeric_fields=["amount"],
            object_id_fields=["_id", "parts_order_id", "shop_id", "created_by"],
        )

        if q:
            order_base_query = {"shop_id": shop["_id"], "is_active": {"$ne": False}}
            order_search_filter = build_regex_search_filter(
                q,
                text_fields=["status", "payment_status", "vendor_bill"],
                numeric_fields=["order_number", "paid_amount", "remaining_balance"],
                object_id_fields=["_id", "vendor_id", "shop_id", "tenant_id"],
            )

            if vendor_ids_by_name and order_search_filter:
                order_query = {
                    "$and": [
                        order_base_query,
                        {"$or": [order_search_filter, {"vendor_id": {"$in": vendor_ids_by_name}}]},
                    ]
                }
            elif vendor_ids_by_name:
                order_query = {"$and": [order_base_query, {"vendor_id": {"$in": vendor_ids_by_name}}]}
            elif order_search_filter:
                order_query = {"$and": [order_base_query, order_search_filter]}
            else:
                order_query = order_base_query

            matched_order_ids = [
                row.get("_id") for row in orders_coll.find(order_query, {"_id": 1}) if row.get("_id")
            ]

            extra = []
            if matched_order_ids:
                extra.append({"parts_order_id": {"$in": matched_order_ids}})

            if payments_search_filter and extra:
                payments_query = {"$and": [payments_query, {"$or": [payments_search_filter, *extra]}]}
            elif payments_search_filter:
                payments_query = {"$and": [payments_query, payments_search_filter]}
            elif extra:
                payments_query = {"$and": [payments_query, {"$or": extra}]}
        elif payments_search_filter:
            payments_query = {"$and": [payments_query, payments_search_filter]}

        payments_totals = _get_parts_orders_payment_totals(payments_coll, payments_query)
        payment_rows, payments_pagination = paginate_find(
            payments_coll,
            payments_query,
            [("payment_date", -1), ("created_at", -1)],
            payments_page_num,
            payments_per_page,
        )

        order_ids = [x.get("parts_order_id") for x in payment_rows if x.get("parts_order_id")]
        orders_map = {}
        vendor_ids = []
        if order_ids:
            for row in orders_coll.find(
                {"_id": {"$in": order_ids}, "shop_id": shop["_id"]},
                {"order_number": 1, "vendor_id": 1, "status": 1},
            ):
                oid = row.get("_id")
                if not oid:
                    continue
                orders_map[oid] = row
                if row.get("vendor_id"):
                    vendor_ids.append(row.get("vendor_id"))

        vendors_map_for_payments = {}
        if vendor_ids and vendors_coll is not None:
            for row in vendors_coll.find({"_id": {"$in": vendor_ids}}):
                vendors_map_for_payments[row.get("_id")] = _name_from_doc(row)

        for pay in payment_rows:
            order_row = orders_map.get(pay.get("parts_order_id")) or {}
            vendor_name = vendors_map_for_payments.get(order_row.get("vendor_id")) or "-"
            payments_list.append(
                {
                    "id": str(pay.get("_id")),
                    "parts_order_id": str(pay.get("parts_order_id")) if pay.get("parts_order_id") else "",
                    "order_number": order_row.get("order_number") or "-",
                    "order_status": str(order_row.get("status") or "ordered"),
                    "vendor": vendor_name,
                    "amount": float(_parse_float(pay.get("amount"), default=0.0)),
                    "payment_method": str(pay.get("payment_method") or "cash"),
                    "notes": str(pay.get("notes") or ""),
                    "created_at": _fmt_preferred_dt_label(pay.get("payment_date"), pay.get("created_at")),
                }
            )

    # Get cores list for Cores tab
    cores_list = []
    cores_pagination = None
    cores_coll = parts_coll.database.cores if parts_coll is not None else None
    if cores_coll is not None and active_tab == "cores":
        cores_query = {
            "shop_id": shop["_id"],
            "is_active": {"$ne": False},
            "quantity": {"$gt": 0},
        }
        cores_search_filter = build_regex_search_filter(
            q,
            text_fields=["part_number", "description"],
            numeric_fields=["quantity", "core_cost"],
            object_id_fields=["_id", "part_id", "shop_id", "tenant_id", "created_by", "updated_by"],
        )
        if cores_search_filter:
            cores_query = {"$and": [cores_query, cores_search_filter]}

        cores_rows, cores_pagination = paginate_find(
            cores_coll,
            cores_query,
            [("part_number", 1), ("updated_at", -1)],
            cores_page_num,
            cores_per_page,
        )

        for core in cores_rows:
            cores_list.append(
                {
                    "id": str(core.get("_id")),
                    "part_id": str(core.get("part_id")) if core.get("part_id") else "",
                    "part_number": core.get("part_number") or "-",
                    "description": core.get("description") or "",
                    "core_cost": float(core.get("core_cost") or 0),
                    "quantity": int(core.get("quantity") or 0),
                    "updated_at": _fmt_dt_label(core.get("updated_at")),
                }
            )

    return _render_app_page(
        "public/parts.html",
        active_page="parts",
        active_tab=active_tab,
        q=q,
        parts=parts_in_stock,
        pagination=pagination,
        orders_pagination=orders_pagination,
        vendors=vendors,
        categories=categories,
        locations=locations,
        last_order_id=last_order_id,
        orders=orders_list,
        cores=cores_list,
        cores_pagination=cores_pagination,
        parts_totals=parts_totals,
        orders_totals=orders_totals,
        payments=payments_list,
        payments_pagination=payments_pagination,
        payments_totals=payments_totals,
        date_preset=date_preset,
        date_from=date_from,
        date_to=date_to,
        today_date_input_value=get_active_shop_today_iso(),
    )



@parts_bp.post("/create")
@login_required
@permission_required("parts.edit")
def parts_create():
    """
    Добавление запчасти:
      part_number (required)
      description
      reference
      vendor_id (из vendors в этом shop DB)
      category_id (из parts_categories в этом shop DB)
      location_id (из parts_locations в этом shop DB)
      in_stock (int)
      average_cost (float)
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("parts.parts_page"))

    tenant_oid = _oid(session.get(SESSION_TENANT_ID))
    if not tenant_oid:
        flash("Tenant session missing. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    part_number = (request.form.get("part_number") or "").strip()
    description = (request.form.get("description") or "").strip()
    reference = (request.form.get("reference") or "").strip()

    vendor_id_raw = (request.form.get("vendor_id") or "").strip()
    category_id_raw = (request.form.get("category_id") or "").strip()
    location_id_raw = (request.form.get("location_id") or "").strip()

    in_stock_raw = (request.form.get("in_stock") or "").strip()
    avg_cost_raw = (request.form.get("average_cost") or "").strip()
    core_has_charge_raw = (request.form.get("core_has_charge") or "").strip()
    core_cost_raw = (request.form.get("core_cost") or "").strip()
    misc_has_charge_raw = (request.form.get("misc_has_charge") or "").strip()
    do_not_track_inventory_raw = (request.form.get("do_not_track_inventory") or "").strip()

    if not part_number:
        flash("Part number is required.", "error")
        return redirect(url_for("parts.parts_page"))

    in_stock = _parse_int(in_stock_raw, default=0)

    average_cost = _parse_float(avg_cost_raw, default=0.0)
    if average_cost < 0:
        flash("Average cost cannot be negative.", "error")
        return redirect(url_for("parts.parts_page"))

    core_has_charge = core_has_charge_raw == "1"
    core_cost = _parse_float(core_cost_raw, default=0.0)
    if core_has_charge and core_cost < 0:
        flash("Core cost cannot be negative.", "error")
        return redirect(url_for("parts.parts_page"))

    misc_has_charge = misc_has_charge_raw == "1"
    do_not_track_inventory = do_not_track_inventory_raw == "1"

    if not do_not_track_inventory and in_stock < 0:
        flash("In stock cannot be negative.", "error")
        return redirect(url_for("parts.parts_page"))

    # Do-not-track parts cannot have core charge.
    if do_not_track_inventory:
        core_has_charge = False
        core_cost = 0.0
    misc_charges = []
    if misc_has_charge:
        import re

        misc_re = re.compile(r"^misc_charges\[(\d+)\]\[(description|price|taxable)\]$")
        misc_map: dict[int, dict] = {}

        for key, val in request.form.items():
            m = misc_re.match(key)
            if not m:
                continue

            idx = int(m.group(1))
            field = m.group(2)
            item = misc_map.setdefault(idx, {})

            if field == "description":
                item["description"] = (val or "").strip()
            elif field == "price":
                item["price"] = (val or "").strip()
            elif field == "taxable":
                item["taxable"] = (val or "").strip()

        for idx in sorted(misc_map.keys()):
            item = misc_map[idx]
            desc = (item.get("description") or "").strip()
            price_raw = (item.get("price") or "").strip()
            if not (desc or price_raw):
                continue

            price = _parse_float(price_raw, default=0.0)
            if price < 0:
                flash("Misc charge price cannot be negative.", "error")
                return redirect(url_for("parts.parts_page"))

            misc_charges.append({
                "description": desc,
                "price": float(price),
                "taxable": item.get("taxable", "") in ("1", "true", "on"),
            })

    # ✅ PyMongo Collection нельзя проверять через bool()
    vendor_oid, err = (
        _validate_ref(vendors_coll, vendor_id_raw, "Vendor")
        if vendors_coll is not None
        else (None, None)
    )
    if err:
        flash(err, "error")
        return redirect(url_for("parts.parts_page"))

    category_oid, err = (
        _validate_ref(cats_coll, category_id_raw, "Category")
        if cats_coll is not None
        else (None, None)
    )
    if err:
        flash(err, "error")
        return redirect(url_for("parts.parts_page"))

    location_oid, err = (
        _validate_ref(locs_coll, location_id_raw, "Location")
        if locs_coll is not None
        else (None, None)
    )
    if err:
        flash(err, "error")
        return redirect(url_for("parts.parts_page"))

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))
    order_date = shop_local_date_to_utc(request.form.get("order_date"), default_today=True)

    doc = {
        "part_number": part_number,
        "description": description or None,
        "reference": reference or None,
        "search_terms": build_parts_search_terms(part_number, description, reference),

        "vendor_id": vendor_oid,
        "category_id": category_oid,
        "location_id": location_oid,

        "do_not_track_inventory": bool(do_not_track_inventory),
        "average_cost": float(average_cost),
        "core_has_charge": bool(core_has_charge),
        "core_cost": float(core_cost) if core_has_charge else None,
        "misc_has_charge": bool(misc_has_charge),
        "misc_charges": misc_charges if misc_has_charge else [],

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

    if not do_not_track_inventory:
        doc["in_stock"] = in_stock

    parts_coll.insert_one(doc)

    flash("Part created successfully.", "success")
    return redirect(url_for("parts.parts_page"))


@parts_bp.get("/api/search")
@login_required
def parts_api_search():
    """
    Search parts for dropdown/autocomplete.
    Query params:
      q: search string
      limit: optional (default 20, max 50)

    Returns: { ok: true, items: [{id, part_number, description, average_cost, vendor_id}] }
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        return {"ok": False, "error": "Shop database not configured."}, 400

    q = (request.args.get("q") or "").strip()
    limit = _parse_int(request.args.get("limit") or "20", default=20)
    if limit <= 0:
        limit = 20
    if limit > 50:
        limit = 50

    # Если пустой запрос — не грузим базу
    if not q:
        return {"ok": True, "items": []}

    import re

    normalized_query, query_tokens = build_query_tokens(q)
    if not normalized_query:
        return {"ok": True, "items": []}

    query_filter = {
        "shop_id": shop["_id"],
        "is_active": True,
    }
    if len(query_tokens) <= 1:
        query_filter["search_terms"] = normalized_query
    else:
        query_filter["search_terms"] = {"$all": query_tokens}

    projection = {
        "part_number": 1,
        "description": 1,
        "reference": 1,
        "average_cost": 1,
        "vendor_id": 1,
        "core_has_charge": 1,
        "core_cost": 1,
    }

    fetch_limit = min(300, max(50, limit * 6))
    cursor = (
        parts_coll.find(query_filter, projection)
        .sort([("part_number", 1)])
        .limit(fetch_limit)
    )

    items = []
    seen_ids = set()
    for p in cursor:
        if not part_matches_query(
            normalized_query,
            p.get("part_number"),
            p.get("description"),
            p.get("reference"),
        ):
            continue

        part_id = p.get("_id")
        if part_id in seen_ids:
            continue
        seen_ids.add(part_id)

        items.append({
            "id": str(p["_id"]),
            "part_number": p.get("part_number") or "",
            "description": p.get("description") or "",
            "average_cost": float(p.get("average_cost") or 0.0),
            "vendor_id": str(p["vendor_id"]) if p.get("vendor_id") else "",
            "core_has_charge": bool(p.get("core_has_charge", False)),
            "core_cost": float(p.get("core_cost") or 0.0),
        })

        if len(items) >= limit:
            break

    if len(items) < limit:
        contains = re.escape(q)
        fallback_filter = {
            "shop_id": shop["_id"],
            "is_active": True,
            "search_terms": {"$exists": False},
            "$or": [
                {"part_number": {"$regex": contains, "$options": "i"}},
                {"description": {"$regex": contains, "$options": "i"}},
                {"reference": {"$regex": contains, "$options": "i"}},
            ],
        }

        fallback_cursor = (
            parts_coll.find(fallback_filter, projection)
            .sort([("part_number", 1)])
            .limit(fetch_limit)
        )

        for p in fallback_cursor:
            if not part_matches_query(
                normalized_query,
                p.get("part_number"),
                p.get("description"),
                p.get("reference"),
            ):
                continue

            part_id = p.get("_id")
            if part_id in seen_ids:
                continue
            seen_ids.add(part_id)

            items.append({
                "id": str(p["_id"]),
                "part_number": p.get("part_number") or "",
                "description": p.get("description") or "",
                "average_cost": float(p.get("average_cost") or 0.0),
                "vendor_id": str(p["vendor_id"]) if p.get("vendor_id") else "",
                "core_has_charge": bool(p.get("core_has_charge", False)),
                "core_cost": float(p.get("core_cost") or 0.0),
            })

            if len(items) >= limit:
                break

    return {"ok": True, "items": items}


@parts_bp.post("/api/orders/create")
@login_required
@permission_required("parts.edit")
def parts_api_orders_create():
    """
    AJAX create order.
    Accepts JSON:
      {
        "vendor_id": "<oid str>",
        "items": [
          {"part_id":"<oid str>", "quantity": 2, "price": 12.34},
          ...
        ]
      }

    Returns JSON:
      { "ok": true, "order_id": "<oid str>", "items_count": N }
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or vendors_coll is None or orders_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop database not configured for this shop."}), 400

    tenant_oid = _oid(session.get(SESSION_TENANT_ID))
    if not tenant_oid:
        session.clear()
        return jsonify({"ok": False, "error": "Tenant session missing. Please login again."}), 401

    data = request.get_json(silent=True) or {}

    vendor_id_raw = (data.get("vendor_id") or "").strip()
    if not vendor_id_raw:
        return jsonify({"ok": False, "error": "Vendor is required."}), 400

    vendor_oid, err = _validate_ref(vendors_coll, vendor_id_raw, "Vendor")
    if err:
        return jsonify({"ok": False, "error": err}), 400

    items_in = data.get("items") or []
    if not isinstance(items_in, list):
        items_in = []

    non_inventory_amounts, non_inventory_err = _parse_non_inventory_amounts(data.get("non_inventory_amounts") or [])
    if non_inventory_err:
        return jsonify({"ok": False, "error": non_inventory_err}), 400

    items = []
    for it in items_in:
        pid = _oid((it.get("part_id") or "").strip())
        if not pid:
            continue

        qty = _parse_int(it.get("quantity"), default=0)
        price = _parse_float(it.get("price"), default=-1.0)

        if qty <= 0:
            continue
        if price < 0:
            return jsonify({"ok": False, "error": "Price cannot be negative."}), 400

        part = parts_coll.find_one({"_id": pid, "is_active": True})
        if not part:
            continue

        items.append({
            "part_id": pid,
            "part_number": part.get("part_number"),
            "description": part.get("description"),
            "price": float(price),
            "quantity": int(qty),
        })

    if not items and not non_inventory_amounts:
        return jsonify({"ok": False, "error": "No valid items in order."}), 400

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))
    order_date = shop_local_date_to_utc(data.get("order_date"), default_today=True)

    # Get next order number
    shop_db = orders_coll.database
    order_number = _get_next_order_number(shop_db, shop["_id"])

    order_doc = {
        "vendor_id": vendor_oid,
        "order_number": order_number,
        "vendor_bill": "",
        "items": items,
        "non_inventory_amounts": non_inventory_amounts,
        "status": "ordered",
        "order_date": order_date,
        "payment_status": "unpaid",
        "paid_amount": 0.0,
        "remaining_balance": float(_parts_order_amounts({"items": items, "non_inventory_amounts": non_inventory_amounts}).get("total_amount") or 0.0),
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "created_by": user_oid,
        "updated_by": user_oid,
        "shop_id": shop["_id"],
        "tenant_id": tenant_oid,
    }

    res = orders_coll.insert_one(order_doc)

    return jsonify(
        {
            "ok": True,
            "order_id": str(res.inserted_id),
            "items_count": len(items),
            "non_inventory_count": len(non_inventory_amounts),
        }
    )

def _recalc_weighted_avg(old_qty: int, old_avg: float, add_qty: int, add_price: float) -> float:
    """
    Weighted average:
      (old_avg * old_qty + add_price * add_qty) / (old_qty + add_qty)

    Safeguards for 0 qty.
    """
    old_qty = int(old_qty or 0)
    add_qty = int(add_qty or 0)
    old_avg = float(old_avg or 0.0)
    add_price = float(add_price or 0.0)

    denom = old_qty + add_qty
    if denom <= 0:
        return 0.0

    return (old_avg * old_qty + add_price * add_qty) / denom


@parts_bp.get("/api/orders/<order_id>")
@login_required
@permission_required("parts.view")
def parts_api_orders_get(order_id: str):
    """
    Get order details for editing.
    Returns JSON with order data.
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if orders_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop database not configured for this shop."}), 400

    oid = _oid(order_id)
    if not oid:
        return jsonify({"ok": False, "error": "Invalid order id"}), 400

    order = orders_coll.find_one({"_id": oid, "shop_id": shop["_id"], "is_active": {"$ne": False}})
    if not order:
        return jsonify({"ok": False, "error": "Order not found"}), 404

    # Convert items for JSON serialization. Keep backward compatibility with
    # older docs where item keys can differ.
    items = []
    raw_items = order.get("items")
    if not isinstance(raw_items, list):
        raw_items = order.get("parts") if isinstance(order.get("parts"), list) else []

    part_ids = [item.get("part_id") for item in raw_items if isinstance(item, dict) and item.get("part_id")]
    
    # Fetch parts data to get core charge info
    parts_map = {}
    if part_ids and parts_coll is not None:
        parts_cursor = parts_coll.find(
            {"_id": {"$in": part_ids}},
            {"core_has_charge": 1, "core_cost": 1}
        )
        parts_map = {p["_id"]: p for p in parts_cursor}
    
    for item in raw_items:
        if isinstance(item, dict):
            part_id = item.get("part_id")
            part_data = parts_map.get(part_id, {}) if part_id else {}

            quantity = item.get("quantity") if item.get("quantity") is not None else item.get("qty")
            price = item.get("price") if item.get("price") is not None else item.get("cost")
            
            items.append({
                "part_id": str(part_id) if part_id else "",
                "part_number": item.get("part_number") or "",
                "description": item.get("description") or "",
                "quantity": quantity or 0,
                "price": float(price or 0),
                "core_has_charge": bool(part_data.get("core_has_charge", False)),
                "core_cost": float(part_data.get("core_cost") or 0.0),
            })

    payments_coll = orders_coll.database.parts_order_payments
    paid_amount = _sum_active_order_payments(payments_coll, oid)
    payment_summary = _build_parts_order_payment_summary(order, paid_amount)

    return jsonify({
        "ok": True,
        "order": {
            "id": str(order.get("_id")),
            "vendor_id": str(order.get("vendor_id")) if order.get("vendor_id") else "",
            "status": order.get("status") or "ordered",
            "vendor_bill": str(order.get("vendor_bill") or "").strip(),
            "items": items,
            "non_inventory_amounts": [
                {
                    "type": str(x.get("type") or "shop_supply").strip().lower(),
                    "description": str(x.get("description") or "").strip(),
                    "amount": float(_parse_float(x.get("amount"), default=0.0)),
                }
                for x in (order.get("non_inventory_amounts") or [])
                if isinstance(x, dict)
            ],
            "payment_summary": payment_summary,
            "order_date": shop_date_input_value(order.get("order_date") or order.get("created_at"), default_today=True),
            "created_at": _fmt_dt_iso(order.get("created_at")),
            "received_at": _fmt_dt_iso(order.get("received_at")),
        }
    })


@parts_bp.route("/api/orders/<order_id>/update", methods=["POST", "PUT"])
@login_required
@permission_required("parts.edit")
def parts_api_orders_update(order_id: str):
    """
    AJAX update order.
    Accepts JSON: { "vendor_id": "<oid str>", "items": [...] }
    Returns JSON: { "ok": true }
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or vendors_coll is None or orders_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop database not configured for this shop."}), 400

    oid = _oid(order_id)
    if not oid:
        return jsonify({"ok": False, "error": "Invalid order id"}), 400

    # Get existing order
    order = orders_coll.find_one({"_id": oid, "shop_id": shop["_id"], "is_active": {"$ne": False}})
    if not order:
        return jsonify({"ok": False, "error": "Order not found"}), 404

    # Don't allow updating received orders
    if str(order.get("status") or "").strip().lower() == "received":
        return jsonify({"ok": False, "error": "Cannot update received orders"}), 400

    data = request.get_json(silent=True) or {}

    vendor_id_raw = (data.get("vendor_id") or "").strip()
    if not vendor_id_raw:
        return jsonify({"ok": False, "error": "Vendor is required."}), 400

    vendor_oid, err = _validate_ref(vendors_coll, vendor_id_raw, "Vendor")
    if err:
        return jsonify({"ok": False, "error": err}), 400

    items_in = data.get("items") or []
    if not isinstance(items_in, list):
        items_in = []

    non_inventory_amounts, non_inventory_err = _parse_non_inventory_amounts(data.get("non_inventory_amounts") or [])
    if non_inventory_err:
        return jsonify({"ok": False, "error": non_inventory_err}), 400

    items = []
    for it in items_in:
        pid = _oid((it.get("part_id") or "").strip())
        if not pid:
            continue

        qty = _parse_int(it.get("quantity"), default=0)
        price = _parse_float(it.get("price"), default=-1.0)

        if qty <= 0:
            continue
        if price < 0:
            return jsonify({"ok": False, "error": "Price cannot be negative."}), 400

        part = parts_coll.find_one({"_id": pid, "is_active": True})
        if not part:
            continue

        items.append({
            "part_id": pid,
            "part_number": part.get("part_number"),
            "description": part.get("description"),
            "price": float(price),
            "quantity": int(qty),
        })

    if not items and not non_inventory_amounts:
        return jsonify({"ok": False, "error": "No valid items in order."}), 400

    payments_coll = orders_coll.database.parts_order_payments
    already_paid_amount = _sum_active_order_payments(payments_coll, oid)
    next_total_amount = _parts_order_amounts({"items": items, "non_inventory_amounts": non_inventory_amounts}).get("total_amount") or 0.0
    if float(already_paid_amount) - float(next_total_amount) > 0.01:
        return jsonify({
            "ok": False,
            "error": "Paid amount is greater than updated order total. Increase order total or remove payment first.",
        }), 400

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))
    order_date = shop_local_date_to_utc(data.get("order_date"), default_today=True)

    # Update order
    orders_coll.update_one(
        {"_id": oid},
        {
            "$set": {
                "vendor_id": vendor_oid,
                "items": items,
                "non_inventory_amounts": non_inventory_amounts,
                "order_date": order_date,
                "updated_at": now,
                "updated_by": user_oid,
            }
        }
    )

    updated_order = orders_coll.find_one({"_id": oid})
    _sync_parts_order_payment_state(orders_coll, payments_coll, updated_order or {}, user_oid, now)

    return jsonify({"ok": True})


@parts_bp.post("/api/orders/<order_id>/payment")
@login_required
@permission_required("parts.edit")
def parts_api_orders_payment(order_id: str):
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if orders_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop database not configured for this shop."}), 400

    oid = _oid(order_id)
    if not oid:
        return jsonify({"ok": False, "error": "Invalid order id."}), 400

    order = orders_coll.find_one({"_id": oid, "shop_id": shop["_id"], "is_active": {"$ne": False}})
    if not order:
        return jsonify({"ok": False, "error": "Order not found."}), 404

    data = request.get_json(silent=True) or {}
    amount = _parse_float(data.get("amount"), default=-1.0)
    payment_method = str(data.get("payment_method") or "").strip() or "cash"
    notes = str(data.get("notes") or "").strip()
    # For received orders payment date is locked to active shop "today".
    if str(order.get("status") or "").strip().lower() == "received":
        payment_date = shop_local_date_to_utc(None, default_today=True)
    else:
        payment_date = shop_local_date_to_utc(data.get("payment_date"), default_today=True)

    if amount <= 0:
        return jsonify({"ok": False, "error": "invalid_amount"}), 400

    payments_coll = orders_coll.database.parts_order_payments
    paid_amount = _sum_active_order_payments(payments_coll, oid)
    summary = _build_parts_order_payment_summary(order, paid_amount)
    total_amount = float(summary.get("total_amount") or 0.0)

    next_paid_amount = float(paid_amount + amount)
    if next_paid_amount - total_amount > 0.01:
        return jsonify({
            "ok": False,
            "error": "overpayment",
            "message": f"Payment would exceed invoice total. Current balance: ${max(0.0, total_amount - paid_amount):.2f}",
        }), 400

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    doc = {
        "parts_order_id": oid,
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "amount": float(round(amount + 1e-12, 2)),
        "payment_method": payment_method,
        "notes": notes,
        "payment_date": payment_date,
        "is_active": True,
        "created_at": now,
        "created_by": user_oid,
    }
    result = payments_coll.insert_one(doc)

    refreshed_order = orders_coll.find_one({"_id": oid})
    _sync_parts_order_payment_state(orders_coll, payments_coll, refreshed_order or {}, user_oid, now)

    refreshed_paid_amount = _sum_active_order_payments(payments_coll, oid)
    refreshed_summary = _build_parts_order_payment_summary(refreshed_order or order, refreshed_paid_amount)

    return jsonify(
        {
            "ok": True,
            "payment_id": str(result.inserted_id),
            "amount_paid": float(refreshed_summary.get("paid_amount") or 0.0),
            "remaining_balance": float(refreshed_summary.get("remaining_balance") or 0.0),
            "payment_status": refreshed_summary.get("payment_status") or "unpaid",
            "is_fully_paid": (refreshed_summary.get("payment_status") == "paid"),
        }
    )


@parts_bp.get("/api/orders/<order_id>/payments")
@login_required
@permission_required("parts.view")
def parts_api_orders_payments(order_id: str):
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if orders_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop database not configured for this shop."}), 400

    oid = _oid(order_id)
    if not oid:
        return jsonify({"ok": False, "error": "Invalid order id."}), 400

    order = orders_coll.find_one({"_id": oid, "shop_id": shop["_id"], "is_active": {"$ne": False}})
    if not order:
        return jsonify({"ok": False, "error": "Order not found."}), 404

    payments_coll = orders_coll.database.parts_order_payments
    payments = list(
        payments_coll.find({"parts_order_id": oid, "is_active": True}).sort([("payment_date", -1), ("created_at", -1)])
    )

    paid_amount = _sum_active_order_payments(payments_coll, oid)
    summary = _build_parts_order_payment_summary(order, paid_amount)

    payment_list = [
        {
            "id": str(p.get("_id")),
            "amount": float(_parse_float(p.get("amount"), default=0.0)),
            "payment_method": str(p.get("payment_method") or "cash"),
            "notes": str(p.get("notes") or ""),
            "payment_date": _fmt_dt_iso(p.get("payment_date") or p.get("created_at")),
            "created_at": _fmt_dt_iso(p.get("created_at")),
        }
        for p in payments
    ]

    return jsonify(
        {
            "ok": True,
            "order_id": str(order.get("_id")),
            "order_number": order.get("order_number"),
            "order_status": str(order.get("status") or "ordered"),
            "order_date": _fmt_dt_iso(order.get("order_date") or order.get("created_at")),
            "created_at": _fmt_dt_iso(order.get("created_at")),
            "received_at": _fmt_dt_iso(order.get("received_at")),
            "grand_total": float(summary.get("total_amount") or 0.0),
            "paid_amount": float(summary.get("paid_amount") or 0.0),
            "remaining_balance": float(summary.get("remaining_balance") or 0.0),
            "payment_status": summary.get("payment_status") or "unpaid",
            "payments": payment_list,
        }
    )


@parts_bp.post("/api/payments/<payment_id>/delete")
@login_required
@permission_required("parts.edit")
def parts_api_delete_payment(payment_id: str):
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if orders_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop database not configured for this shop."}), 400

    pay_oid = _oid(payment_id)
    if not pay_oid:
        return jsonify({"ok": False, "error": "Invalid payment id."}), 400

    payments_coll = orders_coll.database.parts_order_payments
    payment = payments_coll.find_one({"_id": pay_oid, "shop_id": shop["_id"], "is_active": True})
    if not payment:
        return jsonify({"ok": False, "error": "Payment not found."}), 404

    order_oid = payment.get("parts_order_id")
    order = orders_coll.find_one({"_id": order_oid, "shop_id": shop["_id"], "is_active": {"$ne": False}})
    if not order:
        return jsonify({"ok": False, "error": "Order not found."}), 404

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    payments_coll.update_one(
        {"_id": pay_oid},
        {
            "$set": {
                "is_active": False,
                "deleted_at": now,
                "deleted_by": user_oid,
                "updated_at": now,
                "updated_by": user_oid,
            }
        },
    )

    refreshed_order = orders_coll.find_one({"_id": order_oid, "shop_id": shop["_id"], "is_active": {"$ne": False}}) or order
    _sync_parts_order_payment_state(orders_coll, payments_coll, refreshed_order or {}, user_oid, now)

    refreshed_paid_amount = _sum_active_order_payments(payments_coll, order_oid)
    refreshed_summary = _build_parts_order_payment_summary(refreshed_order or order, refreshed_paid_amount)

    return jsonify(
        {
            "ok": True,
            "payment_id": str(pay_oid),
            "parts_order_id": str(order_oid) if order_oid else "",
            "payment_status": refreshed_summary.get("payment_status") or "unpaid",
            "amount_paid": float(refreshed_summary.get("paid_amount") or 0.0),
            "remaining_balance": float(refreshed_summary.get("remaining_balance") or 0.0),
            "is_fully_paid": (refreshed_summary.get("payment_status") == "paid"),
        }
    )


@parts_bp.post("/api/orders/<order_id>/receive")
@login_required
@permission_required("parts.edit")
def parts_api_orders_receive(order_id: str):
    """
    AJAX receive order (full receive).
    Returns JSON { ok: true, updated_parts: N }
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or orders_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop database not configured for this shop."}), 400

    oid = _oid(order_id)
    if not oid:
        return jsonify({"ok": False, "error": "Invalid order id."}), 400

    order = orders_coll.find_one({"_id": oid})
    if not order:
        return jsonify({"ok": False, "error": "Order not found."}), 404

    if order.get("is_active") is False:
        return jsonify({"ok": False, "error": "Order is inactive."}), 400

    if order.get("status") == "received":
        return jsonify({"ok": True, "updated_parts": 0, "message": "Order already received."})

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = {}
    vendor_bill = str(payload.get("vendor_bill") or request.form.get("vendor_bill") or "").strip()
    if len(vendor_bill) > 120:
        return jsonify({"ok": False, "error": "Vendor Bill is too long (max 120)."}), 400

    items = order.get("items") or []
    if not isinstance(items, list) or len(items) == 0:
        return jsonify({"ok": False, "error": "Order has no items."}), 400

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    updated = 0
    updated_not_tracked = 0
    for it in items:
        pid = it.get("part_id")
        if not pid:
            continue

        recv_qty = int(it.get("quantity") or 0)
        recv_price = float(it.get("price") or 0.0)

        if recv_qty <= 0:
            continue
        if recv_price < 0:
            return jsonify({"ok": False, "error": "Order contains negative price."}), 400

        part = parts_coll.find_one({"_id": pid})
        if not part or part.get("is_active") is False:
            continue

        if bool(part.get("do_not_track_inventory")):
            parts_coll.update_one(
                {"_id": pid},
                {"$set": {
                    "average_cost": float(recv_price),
                    "updated_at": now,
                    "updated_by": user_oid,
                }},
            )
            updated_not_tracked += 1
            updated += 1
            continue

        old_qty = int(part.get("in_stock") or 0)
        old_avg = float(part.get("average_cost") or 0.0)

        new_avg = _recalc_weighted_avg(old_qty, old_avg, recv_qty, recv_price)
        new_qty = old_qty + recv_qty

        parts_coll.update_one(
            {"_id": pid},
            {"$set": {
                "in_stock": int(new_qty),
                "average_cost": float(new_avg),
                "updated_at": now,
                "updated_by": user_oid,
            }},
        )

        updated += 1

    orders_coll.update_one(
        {"_id": oid},
        {"$set": {
            "status": "received",
            "vendor_bill": vendor_bill,
            "received_at": now,
            "received_by": user_oid,
            "updated_at": now,
            "updated_by": user_oid,
        }},
    )

    return jsonify({
        "ok": True,
        "updated_parts": updated,
        "updated_not_tracked": updated_not_tracked,
    })


def _rollback_received_order_inventory(parts_coll, items, user_oid, now):
    """Rollback stock quantities for received order items (without touching avg cost)."""
    if not isinstance(items, list):
        return 0, []

    errors = []
    updates = []

    for it in items:
        if not isinstance(it, dict):
            continue

        pid = it.get("part_id")
        qty = _parse_int(it.get("quantity"), default=0)
        part_number = str(it.get("part_number") or "").strip()

        if not pid or qty <= 0:
            continue

        part = parts_coll.find_one({"_id": pid, "is_active": True}, {"in_stock": 1, "do_not_track_inventory": 1})
        if not part:
            continue

        if bool(part.get("do_not_track_inventory")):
            continue

        current_qty = int(part.get("in_stock") or 0)
        if current_qty < qty:
            label = part_number or str(pid)
            errors.append(f"Cannot rollback '{label}': need {qty}, have {current_qty} in stock")
            continue

        updates.append({
            "part_id": pid,
            "new_qty": current_qty - qty,
        })

    if errors:
        return 0, errors

    updated = 0
    for upd in updates:
        parts_coll.update_one(
            {"_id": upd["part_id"]},
            {
                "$set": {
                    "in_stock": int(upd["new_qty"]),
                    "updated_at": now,
                    "updated_by": user_oid,
                }
            },
        )
        updated += 1

    return updated, []


@parts_bp.post("/api/orders/<order_id>/unreceive")
@login_required
@permission_required("parts.edit")
def parts_api_orders_unreceive(order_id: str):
    """AJAX unreceive order and rollback inventory quantities for tracked parts."""
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or orders_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop database not configured for this shop."}), 400

    oid = _oid(order_id)
    if not oid:
        return jsonify({"ok": False, "error": "Invalid order id."}), 400

    order = orders_coll.find_one({"_id": oid, "shop_id": shop["_id"], "is_active": {"$ne": False}})
    if not order:
        return jsonify({"ok": False, "error": "Order not found."}), 404

    if order.get("status") != "received":
        return jsonify({"ok": True, "updated_parts": 0, "message": "Order is not received."})

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    updated, rollback_errors = _rollback_received_order_inventory(
        parts_coll,
        order.get("items") or [],
        user_oid,
        now,
    )
    if rollback_errors:
        return jsonify({"ok": False, "error": rollback_errors[0], "details": rollback_errors}), 400

    orders_coll.update_one(
        {"_id": oid},
        {
            "$set": {
                "status": "ordered",
                "updated_at": now,
                "updated_by": user_oid,
            },
            "$unset": {
                "received_at": "",
                "received_by": "",
            },
        },
    )

    return jsonify({"ok": True, "updated_parts": updated})


@parts_bp.route("/api/orders/<order_id>", methods=["DELETE"])
@login_required
@permission_required("parts.edit")
def parts_api_orders_delete(order_id: str):
    """Delete (soft-delete) parts order. If received, rollback tracked inventory quantities."""
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or orders_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop database not configured for this shop."}), 400

    oid = _oid(order_id)
    if not oid:
        return jsonify({"ok": False, "error": "Invalid order id."}), 400

    order = orders_coll.find_one({"_id": oid, "shop_id": shop["_id"], "is_active": {"$ne": False}})
    if not order:
        return jsonify({"ok": False, "error": "Order not found."}), 404

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))
    updated = 0

    if order.get("status") == "received":
        updated, rollback_errors = _rollback_received_order_inventory(
            parts_coll,
            order.get("items") or [],
            user_oid,
            now,
        )
        if rollback_errors:
            return jsonify({"ok": False, "error": rollback_errors[0], "details": rollback_errors}), 400

    orders_coll.update_one(
        {"_id": oid},
        {
            "$set": {
                "is_active": False,
                "deleted_at": now,
                "deleted_by": user_oid,
                "updated_at": now,
                "updated_by": user_oid,
            }
        },
    )

    payments_coll = orders_coll.database.parts_order_payments
    payments_coll.update_many(
        {"parts_order_id": oid, "is_active": True},
        {
            "$set": {
                "is_active": False,
                "updated_at": now,
                "updated_by": user_oid,
            }
        },
    )

    return jsonify({"ok": True, "updated_parts": updated})


@parts_bp.get("/api/<part_id>")
@login_required
@permission_required("parts.view")
def parts_api_get(part_id: str):
    """
    AJAX get part data for edit modal.
    Returns JSON with full part info.
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    pid = _oid(part_id)
    if not pid:
        return jsonify({"ok": False, "error": "Invalid part id"}), 400

    part = parts_coll.find_one({"_id": pid})
    if not part:
        return jsonify({"ok": False, "error": "Part not found"}), 404

    # Convert ObjectIds to strings for JSON
    return jsonify({
        "ok": True,
        "item": {
            "_id": str(part["_id"]),
            "part_number": part.get("part_number") or "",
            "description": part.get("description") or "",
            "reference": part.get("reference") or "",
            "vendor_id": str(part["vendor_id"]) if part.get("vendor_id") else "",
            "category_id": str(part["category_id"]) if part.get("category_id") else "",
            "location_id": str(part["location_id"]) if part.get("location_id") else "",
            "in_stock": int(part.get("in_stock") or 0),
            "do_not_track_inventory": bool(part.get("do_not_track_inventory")),
            "average_cost": float(part.get("average_cost") or 0.0),
            "core_has_charge": bool(part.get("core_has_charge", False)),
            "core_cost": float(part.get("core_cost") or 0.0),
            "misc_has_charge": bool(part.get("misc_has_charge", False)),
            "misc_charges": part.get("misc_charges") or [],
        }
    })


@parts_bp.get("/api/<part_id>/history")
@login_required
@permission_required("parts.view")
def parts_api_history(part_id: str):
    """
    Return part usage history from:
      - parts_orders (ordered/received)
      - work_orders (used in jobs)
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or orders_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    pid = _oid(part_id)
    if not pid:
        return jsonify({"ok": False, "error": "Invalid part id"}), 400

    part = parts_coll.find_one({"_id": pid, "shop_id": shop["_id"]})
    if not part:
        return jsonify({"ok": False, "error": "Part not found"}), 404

    part_number = str(part.get("part_number") or "").strip()

    # Orders history
    orders_rows = list(
        orders_coll.find(
            {
                "shop_id": shop["_id"],
                "is_active": {"$ne": False},
                "items.part_id": pid,
            }
        ).sort([("created_at", -1)]).limit(200)
    )

    vendor_map = {}
    if vendors_coll is not None:
        vendor_ids = [r.get("vendor_id") for r in orders_rows if r.get("vendor_id")]
        if vendor_ids:
            for v in vendors_coll.find({"_id": {"$in": vendor_ids}}):
                vendor_map[v.get("_id")] = _name_from_doc(v)

    orders_out = []
    for row in orders_rows:
        qty = 0
        latest_price = None
        for it in (row.get("items") or []):
            if not isinstance(it, dict):
                continue
            if it.get("part_id") != pid:
                continue
            item_qty = _parse_int(it.get("quantity"), default=0)
            qty += max(0, item_qty)
            latest_price = _parse_float(it.get("price"), default=0.0)

        if qty <= 0:
            continue

        orders_out.append({
            "order_id": str(row.get("_id")),
            "order_number": row.get("order_number"),
            "status": str(row.get("status") or ""),
            "vendor": vendor_map.get(row.get("vendor_id")) or "-",
            "quantity": qty,
            "price": float(latest_price or 0.0),
            "created_at": _fmt_dt_iso(row.get("created_at")),
            "received_at": _fmt_dt_iso(row.get("received_at")),
        })

    # Work order history (prefer part_id match, fallback by part_number for legacy docs)
    db, _ = _get_shop_db(master)
    if db is None:
        return jsonify({"ok": True, "part": {"id": str(pid), "part_number": part_number}, "orders": orders_out, "work_orders": []})

    work_orders_coll = db.work_orders
    wo_filter = {
        "shop_id": shop["_id"],
        "is_active": {"$ne": False},
        "$or": [
            {"labors.parts.part_id": pid},
            {"labors.parts.part_number": part_number},
        ],
    }
    wo_rows = list(work_orders_coll.find(wo_filter).sort([("created_at", -1)]).limit(300))

    customer_ids = [w.get("customer_id") for w in wo_rows if w.get("customer_id")]
    unit_ids = [w.get("unit_id") for w in wo_rows if w.get("unit_id")]

    customers_map = {}
    if customer_ids:
        for c in db.customers.find({"_id": {"$in": customer_ids}}):
            company = str(c.get("company_name") or "").strip()
            full = (str(c.get("first_name") or "").strip() + " " + str(c.get("last_name") or "").strip()).strip()
            customers_map[c.get("_id")] = company or full or "-"

    units_map = {}
    if unit_ids:
        for u in db.units.find({"_id": {"$in": unit_ids}}):
            bits = []
            if u.get("unit_number"):
                bits.append(str(u.get("unit_number")))
            if u.get("year"):
                bits.append(str(u.get("year")))
            if u.get("make"):
                bits.append(str(u.get("make")))
            if u.get("model"):
                bits.append(str(u.get("model")))
            units_map[u.get("_id")] = " ".join([x for x in bits if x]).strip() or "-"

    work_orders_out = []
    for w in wo_rows:
        used_qty = 0
        for labor in (w.get("labors") or []):
            if not isinstance(labor, dict):
                continue
            for p in (labor.get("parts") or []):
                if not isinstance(p, dict):
                    continue
                p_pid = p.get("part_id")
                p_num = str(p.get("part_number") or "").strip()
                if p_pid == pid or (not p_pid and part_number and p_num == part_number):
                    used_qty += max(0, _parse_int(p.get("qty"), default=0))

        if used_qty <= 0:
            continue

        totals = w.get("totals") if isinstance(w.get("totals"), dict) else {}
        grand_total = totals.get("grand_total") if totals.get("grand_total") is not None else w.get("grand_total")

        work_orders_out.append({
            "work_order_id": str(w.get("_id")),
            "wo_number": w.get("wo_number"),
            "status": str(w.get("status") or "open"),
            "customer": customers_map.get(w.get("customer_id")) or "-",
            "unit": units_map.get(w.get("unit_id")) or "-",
            "used_qty": used_qty,
            "grand_total": float(_parse_float(grand_total, default=0.0)),
            "created_at": _fmt_dt_iso(w.get("created_at")),
        })

    return jsonify({
        "ok": True,
        "part": {
            "id": str(pid),
            "part_number": part_number,
            "description": str(part.get("description") or ""),
        },
        "orders": orders_out,
        "work_orders": work_orders_out,
    })


@parts_bp.post("/api/<part_id>/update")
@login_required
@permission_required("parts.edit")
def parts_api_update(part_id: str):
    """
    AJAX update part.
    Accepts JSON with part data.
    Returns JSON { ok: true/false, ... }
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    pid = _oid(part_id)
    if not pid:
        return jsonify({"ok": False, "error": "Invalid part id"}), 400

    part = parts_coll.find_one({"_id": pid})
    if not part:
        return jsonify({"ok": False, "error": "Part not found"}), 404

    data = request.get_json(silent=True) or {}

    # Validate and extract fields
    part_number = (data.get("part_number") or "").strip()
    if not part_number:
        return jsonify({"ok": False, "error": "Part number is required"}), 400

    description = (data.get("description") or "").strip()
    reference = (data.get("reference") or "").strip()

    average_cost = _parse_float(data.get("average_cost", 0.0), default=0.0)
    if average_cost < 0:
        return jsonify({"ok": False, "error": "Average cost cannot be negative"}), 400

    do_not_track_inventory = bool(data.get("do_not_track_inventory", False))
    in_stock = _parse_int(data.get("in_stock", 0), default=0)
    if not do_not_track_inventory and in_stock < 0:
        return jsonify({"ok": False, "error": "In stock cannot be negative"}), 400

    core_has_charge = data.get("core_has_charge", False)
    core_cost = _parse_float(data.get("core_cost", 0.0), default=0.0)
    if core_has_charge and core_cost < 0:
        return jsonify({"ok": False, "error": "Core cost cannot be negative"}), 400

    if do_not_track_inventory:
        core_has_charge = False
        core_cost = 0.0

    misc_has_charge = data.get("misc_has_charge", False)
    misc_charges_raw = data.get("misc_charges", []) or []
    if misc_has_charge and not isinstance(misc_charges_raw, list):
        return jsonify({"ok": False, "error": "Invalid misc charges"}), 400
    misc_charges = [
        {
            "description": str(ch.get("description") or "").strip(),
            "price": float(ch.get("price") or 0),
            "taxable": bool(ch.get("taxable", True)),
        }
        for ch in misc_charges_raw
        if isinstance(ch, dict) and str(ch.get("description") or "").strip()
    ]

    # Validate references
    vendor_oid, err = (
        _validate_ref(vendors_coll, data.get("vendor_id", ""), "Vendor")
        if vendors_coll is not None
        else (None, None)
    )
    if err:
        return jsonify({"ok": False, "error": err}), 400

    category_oid, err = (
        _validate_ref(cats_coll, data.get("category_id", ""), "Category")
        if cats_coll is not None
        else (None, None)
    )
    if err:
        return jsonify({"ok": False, "error": err}), 400

    location_oid, err = (
        _validate_ref(locs_coll, data.get("location_id", ""), "Location")
        if locs_coll is not None
        else (None, None)
    )
    if err:
        return jsonify({"ok": False, "error": err}), 400

    # Update document
    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    set_doc = {
            "part_number": part_number,
            "description": description or None,
            "reference": reference or None,
            "search_terms": build_parts_search_terms(part_number, description, reference),
            "vendor_id": vendor_oid,
            "category_id": category_oid,
            "location_id": location_oid,
            "do_not_track_inventory": bool(do_not_track_inventory),
            "average_cost": float(average_cost),
            "core_has_charge": bool(core_has_charge),
            "core_cost": float(core_cost) if core_has_charge else None,
            "misc_has_charge": bool(misc_has_charge),
            "misc_charges": misc_charges if misc_has_charge else [],
            "updated_at": now,
            "updated_by": user_oid,
    }
    unset_doc = {}

    if do_not_track_inventory:
        unset_doc["in_stock"] = ""
    else:
        set_doc["in_stock"] = in_stock

    update_doc = {"$set": set_doc}
    if unset_doc:
        update_doc["$unset"] = unset_doc

    parts_coll.update_one({"_id": pid}, update_doc)

    return jsonify({"ok": True, "message": "Part updated successfully"})


@parts_bp.post("/<part_id>/deactivate")
@login_required
@permission_required("parts.edit")
def parts_deactivate(part_id: str):
    """
    Удаление запчасти = деактивация (soft delete).
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("parts.parts_page"))

    pid = _oid(part_id)
    if not pid:
        flash("Invalid part id.", "error")
        return redirect(url_for("parts.parts_page"))

    existing = parts_coll.find_one({"_id": pid})
    if not existing:
        flash("Part not found.", "error")
        return redirect(url_for("parts.parts_page"))

    if existing.get("is_active") is False:
        flash("Part is already deactivated.", "info")
        return redirect(url_for("parts.parts_page"))

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    parts_coll.update_one(
        {"_id": pid},
        {"$set": {
            "is_active": False,
            "updated_at": now,
            "updated_by": user_oid,
            "deactivated_at": now,
            "deactivated_by": user_oid,
        }},
    )

    flash("Part deactivated.", "success")
    return redirect(url_for("parts.parts_page"))


@parts_bp.post("/<part_id>/restore")
@login_required
@permission_required("parts.deactivate")
def parts_restore(part_id: str):
    """
    Restore (reactivate) part.
    Используем то же право parts.deactivate.
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("parts.parts_page"))

    pid = _oid(part_id)
    if not pid:
        flash("Invalid part id.", "error")
        return redirect(url_for("parts.parts_page"))

    existing = parts_coll.find_one({"_id": pid})
    if not existing:
        flash("Part not found.", "error")
        return redirect(url_for("parts.parts_page"))

    if existing.get("is_active") is True:
        flash("Part is already active.", "info")
        return redirect(url_for("parts.parts_page"))

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    parts_coll.update_one(
        {"_id": pid},
        {"$set": {
            "is_active": True,
            "updated_at": now,
            "updated_by": user_oid,
            "deactivated_at": None,
            "deactivated_by": None,
        }},
    )

    flash("Part restored.", "success")
    return redirect(url_for("parts.parts_page"))
