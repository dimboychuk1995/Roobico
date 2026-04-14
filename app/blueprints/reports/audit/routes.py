from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bson import ObjectId
from flask import jsonify, make_response, render_template, request, session

from app.blueprints.main.routes import NAV_ITEMS
from app.blueprints.reports import reports_bp
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import SESSION_TENANT_ID, login_required
from app.utils.date_filters import build_date_range_filters
from app.utils.layout import render_internal_page
from app.utils.pagination import get_sort_params
from app.utils.pdf_utils import render_chart_to_base64, render_html_to_pdf
from app.utils.permissions import filter_nav_items


STANDARD_REPORT_TABS = {
    "sales_summary",
    "payments_summary",
    "customer_balances",
    "vendor_balances",
    "parts_orders_summary",
    "general_revenue",
    "mechanic_hours",
}

CUSTOMER_FILTER_TABS = {
    "sales_summary",
    "payments_summary",
    "customer_balances",
}

VENDOR_FILTER_TABS = {
    "parts_orders_summary",
}

NON_INVENTORY_AMOUNT_TYPES = {
    "shop_supply",
    "tools",
    "utilities",
    "payment_to_another_service",
}


def _now_utc():
    return datetime.now(timezone.utc)


def _time_bucket_key(dt, bucket_type: str = "month") -> str:
    """Return a sortable string key for grouping by week or month."""
    if not isinstance(dt, datetime):
        return ""
    if bucket_type == "week":
        # ISO week: Monday-based, label = start of week
        monday = dt - timedelta(days=dt.weekday())
        return monday.strftime("%Y-%m-%d")
    # month
    return dt.strftime("%Y-%m")


def _fill_bucket_gaps(buckets: dict, bucket_type: str) -> list[str]:
    """Return sorted labels with missing gaps filled in."""
    if not buckets:
        return []
    keys = sorted(buckets.keys())
    if len(keys) <= 1:
        return keys
    filled: list[str] = []
    if bucket_type == "week":
        cur = datetime.strptime(keys[0], "%Y-%m-%d")
        end = datetime.strptime(keys[-1], "%Y-%m-%d")
        while cur <= end:
            filled.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=7)
    else:
        cur = datetime.strptime(keys[0] + "-01", "%Y-%m-%d")
        end = datetime.strptime(keys[-1] + "-01", "%Y-%m-%d")
        while cur <= end:
            filled.append(cur.strftime("%Y-%m"))
            if cur.month == 12:
                cur = cur.replace(year=cur.year + 1, month=1)
            else:
                cur = cur.replace(month=cur.month + 1)
    return filled


def _round2(value) -> float:
    try:
        return round(float(value or 0) + 1e-12, 2)
    except Exception:
        return 0.0


def _maybe_oid(raw):
    if not raw:
        return None
    try:
        return ObjectId(str(raw))
    except Exception:
        return None


def _to_oid_list(values: list[str]) -> list[ObjectId]:
    out: list[ObjectId] = []
    seen = set()
    for raw in values:
        oid = _maybe_oid(raw)
        if oid is None:
            continue
        key = str(oid)
        if key in seen:
            continue
        seen.add(key)
        out.append(oid)
    return out


def _tenant_variants() -> list:
    tenant_raw = session.get(SESSION_TENANT_ID)
    if tenant_raw is None:
        return []

    out = {tenant_raw, str(tenant_raw)}
    tenant_oid = _maybe_oid(tenant_raw)
    if tenant_oid is not None:
        out.add(tenant_oid)
    return list(out)


def _get_active_shop(master):
    tenant_ids = _tenant_variants()
    shop_oid = _maybe_oid(session.get("shop_id"))
    if not tenant_ids or shop_oid is None:
        return None

    return master.shops.find_one({"_id": shop_oid, "tenant_id": {"$in": tenant_ids}})


def _get_shop_db(shop_doc):
    if not shop_doc:
        return None
    db_name = (
        shop_doc.get("db_name")
        or shop_doc.get("database")
        or shop_doc.get("db")
        or shop_doc.get("mongo_db")
        or shop_doc.get("shop_db")
    )
    if not db_name:
        return None
    return get_mongo_client()[str(db_name)]


def _append_and(query: dict, extra: dict | None):
    if not extra:
        return query
    if not query:
        return extra
    return {"$and": [query, extra]}


def _build_date_filter(date_ctx: dict, field: str = "created_at", fallback_field: str | None = None):
    created_from = date_ctx.get("created_from")
    created_to_exclusive = date_ctx.get("created_to_exclusive")

    range_filter = {}
    if created_from is not None:
        range_filter["$gte"] = created_from
    if created_to_exclusive is not None:
        range_filter["$lt"] = created_to_exclusive
    if not range_filter:
        return None

    if fallback_field:
        return {
            "$or": [
                {field: range_filter},
                {field: {"$exists": False}, fallback_field: range_filter},
                {field: None, fallback_field: range_filter},
            ]
        }

    return {field: range_filter}


def _customer_label(doc: dict) -> str:
    company = str(doc.get("company_name") or "").strip()
    if company:
        return company
    first_name = str(doc.get("first_name") or "").strip()
    last_name = str(doc.get("last_name") or "").strip()
    full_name = f"{first_name} {last_name}".strip()
    return full_name or "-"


def _get_vendor_options(shop_db, shop_id: ObjectId):
    options = []
    vendor_map = {}
    cursor = shop_db.vendors.find(
        {"shop_id": shop_id},
        {"name": 1},
    ).sort([("name", 1)])

    for doc in cursor:
        vid = doc.get("_id")
        if not vid:
            continue
        label = str(doc.get("name") or "-").strip() or "-"
        sid = str(vid)
        vendor_map[sid] = label
        options.append({"id": sid, "label": label})

    return options, vendor_map


def _get_customer_options(shop_db, shop_id: ObjectId):
    options = []
    customer_map = {}
    cursor = shop_db.customers.find(
        {
            "shop_id": shop_id,
            "is_active": {"$ne": False},
        },
        {
            "company_name": 1,
            "first_name": 1,
            "last_name": 1,
        },
    ).sort([("company_name", 1), ("last_name", 1), ("first_name", 1)])

    for doc in cursor:
        cid = doc.get("_id")
        if not cid:
            continue
        label = _customer_label(doc)
        sid = str(cid)
        customer_map[sid] = label
        options.append({"id": sid, "label": label})

    return options, customer_map


def _wo_totals(wo: dict):
    totals = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    labor = totals.get("labor_total") if totals.get("labor_total") is not None else wo.get("labor_total")
    parts = totals.get("parts_total") if totals.get("parts_total") is not None else wo.get("parts_total")
    misc = totals.get("misc_total") or 0
    tax = totals.get("sales_tax_total") if totals.get("sales_tax_total") is not None else wo.get("sales_tax_total")
    grand = totals.get("grand_total") if totals.get("grand_total") is not None else wo.get("grand_total")
    misc_total = _round2(misc)
    parts_total = _round2(parts)
    # parts_total in DB already includes misc_total, so subtract to get pure parts
    parts_only = _round2(parts_total - misc_total)
    return {
        "labor_total": _round2(labor),
        "parts_total": parts_only,
        "misc_charges_total": misc_total,
        "sales_tax_total": _round2(tax),
        "grand_total": _round2(grand),
    }


def _report_sales_summary(shop_db, shop_id, date_ctx, include_customer_ids, exclude_customer_ids, customer_map, chart_bucket="month"):
    query = {
        "shop_id": shop_id,
        "is_active": True,
    }
    query = _append_and(query, _build_date_filter(date_ctx, field="created_at"))

    if include_customer_ids:
        query = _append_and(query, {"customer_id": {"$in": include_customer_ids}})
    if exclude_customer_ids:
        query = _append_and(query, {"customer_id": {"$nin": exclude_customer_ids}})

    rows_by_customer = {}
    time_buckets: dict[str, dict] = {}

    for wo in shop_db.work_orders.find(query, {"customer_id": 1, "created_at": 1, "totals": 1, "labor_total": 1, "parts_total": 1, "sales_tax_total": 1, "grand_total": 1}):
        customer_id = wo.get("customer_id")
        if not customer_id:
            continue
        sid = str(customer_id)
        bucket = rows_by_customer.setdefault(
            sid,
            {
                "customer_id": sid,
                "customer_label": customer_map.get(sid) or "-",
                "orders_count": 0,
                "labor_total": 0.0,
                "parts_total": 0.0,
                "misc_charges_total": 0.0,
                "sales_tax_total": 0.0,
                "grand_total": 0.0,
            },
        )
        totals = _wo_totals(wo)
        bucket["orders_count"] += 1
        bucket["labor_total"] = _round2(bucket["labor_total"] + totals["labor_total"])
        bucket["parts_total"] = _round2(bucket["parts_total"] + totals["parts_total"])
        bucket["misc_charges_total"] = _round2(bucket["misc_charges_total"] + totals["misc_charges_total"])
        bucket["sales_tax_total"] = _round2(bucket["sales_tax_total"] + totals["sales_tax_total"])
        bucket["grand_total"] = _round2(bucket["grand_total"] + totals["grand_total"])

        # Chart time bucket
        tk = _time_bucket_key(wo.get("created_at"), chart_bucket)
        if tk:
            tb = time_buckets.setdefault(tk, {"revenue": 0.0, "labor": 0.0, "parts": 0.0})
            tb["revenue"] = _round2(tb["revenue"] + totals["grand_total"])
            tb["labor"] = _round2(tb["labor"] + totals["labor_total"])
            tb["parts"] = _round2(tb["parts"] + totals["parts_total"])

    rows = sorted(rows_by_customer.values(), key=lambda x: x.get("grand_total", 0), reverse=True)

    total_orders = sum(int(r.get("orders_count") or 0) for r in rows)
    total_revenue = _round2(sum(float(r.get("grand_total") or 0) for r in rows))
    total_labor = _round2(sum(float(r.get("labor_total") or 0) for r in rows))
    total_parts = _round2(sum(float(r.get("parts_total") or 0) for r in rows))
    total_misc = _round2(sum(float(r.get("misc_charges_total") or 0) for r in rows))
    total_tax = _round2(sum(float(r.get("sales_tax_total") or 0) for r in rows))

    labels = _fill_bucket_gaps(time_buckets, chart_bucket)
    chart_data = {
        "labels": labels,
        "datasets": [
            {"label": "Revenue", "data": [_round2(time_buckets.get(l, {}).get("revenue", 0)) for l in labels]},
            {"label": "Labor", "data": [_round2(time_buckets.get(l, {}).get("labor", 0)) for l in labels]},
            {"label": "Parts", "data": [_round2(time_buckets.get(l, {}).get("parts", 0)) for l in labels]},
        ],
    }

    return {
        "title": "Sales Summary",
        "summary": {
            "orders_count": total_orders,
            "revenue_total": total_revenue,
            "avg_ticket": _round2(total_revenue / total_orders) if total_orders else 0.0,
            "labor_total": total_labor,
            "parts_total": total_parts,
            "misc_charges_total": total_misc,
            "sales_tax_total": total_tax,
        },
        "rows": rows,
        "chart_data": chart_data,
    }


def _report_payments_summary(shop_db, shop_id, date_ctx, include_customer_ids, exclude_customer_ids, customer_map, chart_bucket="month"):
    work_orders_cursor = shop_db.work_orders.find(
        {
            "shop_id": shop_id,
            "is_active": True,
        },
        {
            "_id": 1,
            "customer_id": 1,
        },
    )

    work_order_to_customer = {}
    for wo in work_orders_cursor:
        wo_id = wo.get("_id")
        customer_id = wo.get("customer_id")
        if not wo_id or not customer_id:
            continue

        if include_customer_ids and customer_id not in include_customer_ids:
            continue
        if exclude_customer_ids and customer_id in exclude_customer_ids:
            continue

        work_order_to_customer[wo_id] = customer_id

    if not work_order_to_customer:
        return {
            "title": "Payments Summary",
            "summary": {
                "payments_count": 0,
                "payments_total": 0.0,
                "avg_payment": 0.0,
            },
            "rows": [],
            "chart_data": {"labels": [], "datasets": []},
        }

    payments_query = {
        "shop_id": shop_id,
        "is_active": True,
        "work_order_id": {"$in": list(work_order_to_customer.keys())},
    }
    payments_query = _append_and(
        payments_query,
        _build_date_filter(date_ctx, field="paid_at", fallback_field="created_at"),
    )

    rows_by_customer = {}
    payments_count = 0
    payments_total = 0.0
    time_buckets: dict[str, dict] = {}

    for payment in shop_db.work_order_payments.find(payments_query, {"work_order_id": 1, "amount": 1, "paid_at": 1, "created_at": 1}):
        wo_id = payment.get("work_order_id")
        customer_id = work_order_to_customer.get(wo_id)
        if customer_id is None:
            continue

        amount = _round2(payment.get("amount") or 0)
        sid = str(customer_id)

        bucket = rows_by_customer.setdefault(
            sid,
            {
                "customer_id": sid,
                "customer_label": customer_map.get(sid) or "-",
                "payments_count": 0,
                "amount_total": 0.0,
            },
        )

        bucket["payments_count"] += 1
        bucket["amount_total"] = _round2(bucket["amount_total"] + amount)
        payments_count += 1
        payments_total = _round2(payments_total + amount)

        # Chart time bucket
        pay_dt = payment.get("paid_at") or payment.get("created_at")
        tk = _time_bucket_key(pay_dt, chart_bucket)
        if tk:
            tb = time_buckets.setdefault(tk, {"amount": 0.0})
            tb["amount"] = _round2(tb["amount"] + amount)

    rows = sorted(rows_by_customer.values(), key=lambda x: x.get("amount_total", 0), reverse=True)

    labels = _fill_bucket_gaps(time_buckets, chart_bucket)
    chart_data = {
        "labels": labels,
        "datasets": [
            {"label": "Payments", "data": [_round2(time_buckets.get(l, {}).get("amount", 0)) for l in labels]},
        ],
    }

    return {
        "title": "Payments Summary",
        "summary": {
            "payments_count": payments_count,
            "payments_total": payments_total,
            "avg_payment": _round2(payments_total / payments_count) if payments_count else 0.0,
        },
        "rows": rows,
        "chart_data": chart_data,
    }


def _report_customer_balances(shop_db, shop_id, date_ctx, include_customer_ids, exclude_customer_ids, customer_map):
    query = {
        "shop_id": shop_id,
        "is_active": True,
    }
    query = _append_and(query, _build_date_filter(date_ctx, field="created_at"))
    if include_customer_ids:
        query = _append_and(query, {"customer_id": {"$in": include_customer_ids}})
    if exclude_customer_ids:
        query = _append_and(query, {"customer_id": {"$nin": exclude_customer_ids}})

    work_orders = list(
        shop_db.work_orders.find(
            query,
            {
                "_id": 1,
                "customer_id": 1,
                "totals": 1,
                "grand_total": 1,
            },
        )
    )

    if not work_orders:
        return {
            "title": "Customer Balances",
            "summary": {
                "customers_count": 0,
                "outstanding_total": 0.0,
                "billed_total": 0.0,
                "paid_total": 0.0,
            },
            "rows": [],
        }

    wo_ids = [wo.get("_id") for wo in work_orders if wo.get("_id")]
    paid_map = {}
    if wo_ids:
        pipeline = [
            {
                "$match": {
                    "work_order_id": {"$in": wo_ids},
                    "is_active": True,
                }
            },
            {
                "$group": {
                    "_id": "$work_order_id",
                    "paid_total": {"$sum": {"$ifNull": ["$amount", 0]}},
                }
            },
        ]
        for row in shop_db.work_order_payments.aggregate(pipeline):
            oid = row.get("_id")
            if oid is not None:
                paid_map[oid] = _round2(row.get("paid_total") or 0)

    rows_by_customer = {}
    for wo in work_orders:
        customer_id = wo.get("customer_id")
        wo_id = wo.get("_id")
        if not customer_id or not wo_id:
            continue

        totals = _wo_totals(wo)
        billed = totals["grand_total"]
        paid = _round2(paid_map.get(wo_id, 0.0))
        remaining = _round2(max(0.0, billed - paid))

        sid = str(customer_id)
        bucket = rows_by_customer.setdefault(
            sid,
            {
                "customer_id": sid,
                "customer_label": customer_map.get(sid) or "-",
                "orders_count": 0,
                "billed_total": 0.0,
                "paid_total": 0.0,
                "outstanding_total": 0.0,
            },
        )
        bucket["orders_count"] += 1
        bucket["billed_total"] = _round2(bucket["billed_total"] + billed)
        bucket["paid_total"] = _round2(bucket["paid_total"] + paid)
        bucket["outstanding_total"] = _round2(bucket["outstanding_total"] + remaining)

    rows = sorted(rows_by_customer.values(), key=lambda x: x.get("outstanding_total", 0), reverse=True)

    billed_total = _round2(sum(float(r.get("billed_total") or 0) for r in rows))
    paid_total = _round2(sum(float(r.get("paid_total") or 0) for r in rows))
    outstanding_total = _round2(sum(float(r.get("outstanding_total") or 0) for r in rows))

    return {
        "title": "Customer Balances",
        "summary": {
            "customers_count": len(rows),
            "outstanding_total": outstanding_total,
            "billed_total": billed_total,
            "paid_total": paid_total,
        },
        "rows": rows,
    }


def _report_vendor_balances(shop_db, shop_id, date_ctx):
    query = {
        "shop_id": shop_id,
        "is_active": {"$ne": False},
    }
    query = _append_and(query, _build_date_filter(date_ctx, field="created_at"))

    pipeline = [
        {"$match": query},
        {
            "$group": {
                "_id": "$vendor_id",
                "orders_count": {"$sum": 1},
                "total_amount": {"$sum": {"$ifNull": ["$total_amount", 0]}},
                "paid_amount": {"$sum": {"$ifNull": ["$paid_amount", 0]}},
                "remaining_balance": {"$sum": {"$ifNull": ["$remaining_balance", 0]}},
            }
        },
    ]

    vendor_map = {}
    for v in shop_db.vendors.find(
        {
            "shop_id": shop_id,
        },
        {"name": 1},
    ):
        vid = v.get("_id")
        if vid:
            vendor_map[str(vid)] = str(v.get("name") or "-").strip() or "-"

    rows = []
    for row in shop_db.parts_orders.aggregate(pipeline):
        vendor_id = row.get("_id")
        sid = str(vendor_id) if vendor_id else ""
        rows.append(
            {
                "vendor_id": sid,
                "vendor_label": vendor_map.get(sid) or "-",
                "orders_count": int(row.get("orders_count") or 0),
                "total_amount": _round2(row.get("total_amount") or 0),
                "paid_amount": _round2(row.get("paid_amount") or 0),
                "remaining_balance": _round2(row.get("remaining_balance") or 0),
            }
        )

    rows.sort(key=lambda x: x.get("remaining_balance", 0), reverse=True)

    return {
        "title": "Vendor Balances",
        "summary": {
            "vendors_count": len(rows),
            "orders_count": sum(int(r.get("orders_count") or 0) for r in rows),
            "total_amount": _round2(sum(float(r.get("total_amount") or 0) for r in rows)),
            "paid_amount": _round2(sum(float(r.get("paid_amount") or 0) for r in rows)),
            "remaining_balance": _round2(sum(float(r.get("remaining_balance") or 0) for r in rows)),
        },
        "rows": rows,
    }


def _report_parts_orders_summary(shop_db, shop_id, date_ctx, include_vendor_ids, vendor_map, chart_bucket="month"):
    query = {
        "shop_id": shop_id,
        "is_active": {"$ne": False},
    }
    query = _append_and(query, _build_date_filter(date_ctx, field="created_at"))
    if include_vendor_ids:
        query = _append_and(query, {"vendor_id": {"$in": include_vendor_ids}})

    orders = list(shop_db.parts_orders.find(query, {
        "vendor_id": 1,
        "created_at": 1,
        "items": 1,
        "non_inventory_amounts": 1,
        "total_amount": 1,
        "paid_amount": 1,
        "remaining_balance": 1,
    }))

    # Collect all part_ids to look up core charges
    all_part_ids = set()
    for order in orders:
        for item in (order.get("items") or []):
            pid = item.get("part_id")
            if pid:
                all_part_ids.add(pid)

    core_map: dict = {}
    if all_part_ids:
        for part in shop_db.parts.find(
            {"_id": {"$in": list(all_part_ids)}},
            {"core_has_charge": 1, "core_cost": 1},
        ):
            if part.get("core_has_charge"):
                core_map[part["_id"]] = max(0.0, float(part.get("core_cost") or 0))

    rows_by_vendor: dict[str, dict] = {}
    time_buckets: dict[str, dict] = {}

    for order in orders:
        vendor_id = order.get("vendor_id")
        sid = str(vendor_id) if vendor_id else ""

        bucket = rows_by_vendor.setdefault(
            sid,
            {
                "vendor_id": sid,
                "vendor_label": vendor_map.get(sid) or "-",
                "orders_count": 0,
                "parts_total": 0.0,
                "cores_total": 0.0,
                "shop_supply_total": 0.0,
                "tools_total": 0.0,
                "utilities_total": 0.0,
                "payment_to_another_service_total": 0.0,
                "non_inventory_total": 0.0,
                "total_amount": 0.0,
                "paid_amount": 0.0,
                "remaining_balance": 0.0,
            },
        )
        bucket["orders_count"] += 1

        # Parts items total
        items_total = 0.0
        cores_total = 0.0
        for item in (order.get("items") or []):
            if not isinstance(item, dict):
                continue
            qty = max(0, int(item.get("quantity") or 0))
            price = max(0.0, float(item.get("price") or 0))
            items_total += qty * price
            # Core charges from parts collection
            pid = item.get("part_id")
            if pid and pid in core_map:
                cores_total += qty * core_map[pid]

        bucket["parts_total"] = _round2(bucket["parts_total"] + items_total)
        bucket["cores_total"] = _round2(bucket["cores_total"] + cores_total)

        # Non-inventory by type
        ni_total = 0.0
        for line in (order.get("non_inventory_amounts") or []):
            if not isinstance(line, dict):
                continue
            amount = max(0.0, float(line.get("amount") or 0))
            ni_total += amount
            amount_type = str(line.get("type") or "").strip().lower()
            type_key = amount_type + "_total" if amount_type in NON_INVENTORY_AMOUNT_TYPES else ""
            if type_key and type_key in bucket:
                bucket[type_key] = _round2(bucket[type_key] + amount)

        bucket["non_inventory_total"] = _round2(bucket["non_inventory_total"] + ni_total)
        bucket["total_amount"] = _round2(bucket["total_amount"] + float(order.get("total_amount") or 0))
        bucket["paid_amount"] = _round2(bucket["paid_amount"] + float(order.get("paid_amount") or 0))
        bucket["remaining_balance"] = _round2(bucket["remaining_balance"] + float(order.get("remaining_balance") or 0))

        # Chart time bucket
        tk = _time_bucket_key(order.get("created_at"), chart_bucket)
        if tk:
            tb = time_buckets.setdefault(tk, {"total": 0.0, "parts": 0.0, "non_inv": 0.0})
            tb["total"] = _round2(tb["total"] + float(order.get("total_amount") or 0))
            tb["parts"] = _round2(tb["parts"] + items_total)
            tb["non_inv"] = _round2(tb["non_inv"] + ni_total)

    rows = sorted(rows_by_vendor.values(), key=lambda x: x.get("total_amount", 0), reverse=True)

    def _sum_field(field):
        return _round2(sum(float(r.get(field) or 0) for r in rows))

    labels = _fill_bucket_gaps(time_buckets, chart_bucket)
    chart_data = {
        "labels": labels,
        "datasets": [
            {"label": "Total", "data": [_round2(time_buckets.get(l, {}).get("total", 0)) for l in labels]},
            {"label": "Parts", "data": [_round2(time_buckets.get(l, {}).get("parts", 0)) for l in labels]},
            {"label": "Non-Inventory", "data": [_round2(time_buckets.get(l, {}).get("non_inv", 0)) for l in labels]},
        ],
    }

    return {
        "title": "Parts Orders Summary",
        "summary": {
            "vendors_count": len(rows),
            "orders_count": sum(int(r.get("orders_count") or 0) for r in rows),
            "parts_total": _sum_field("parts_total"),
            "cores_total": _sum_field("cores_total"),
            "non_inventory_total": _sum_field("non_inventory_total"),
            "total_amount": _sum_field("total_amount"),
            "paid_amount": _sum_field("paid_amount"),
            "remaining_balance": _sum_field("remaining_balance"),
        },
        "rows": rows,
        "chart_data": chart_data,
    }


def _wo_parts_cost(wo: dict) -> float:
    """Calculate actual parts cost from work order labors (qty * cost)."""
    cost_total = 0.0
    for block in (wo.get("labors") or []):
        if not isinstance(block, dict):
            continue
        for part in (block.get("parts") or []):
            if not isinstance(part, dict):
                continue
            qty = max(0, int(part.get("qty") or 0))
            cost = max(0.0, float(part.get("cost") or 0))
            cost_total += qty * cost
    return _round2(cost_total)


def _report_general_revenue(shop_db, shop_id, date_ctx, chart_bucket="month"):
    # --- Sales (Work Orders) ---
    wo_query = {
        "shop_id": shop_id,
        "is_active": True,
    }
    wo_query = _append_and(wo_query, _build_date_filter(date_ctx, field="created_at"))

    wo_count = 0
    sales_labor = 0.0
    sales_parts_sale = 0.0
    sales_parts_cost = 0.0
    sales_core_charges = 0.0
    sales_misc = 0.0
    sales_tax = 0.0
    sales_revenue = 0.0
    mechanics: dict[str, dict] = {}
    time_buckets: dict[str, dict[str, float]] = {}

    for wo in shop_db.work_orders.find(wo_query, {
        "totals": 1, "labor_total": 1, "parts_total": 1,
        "sales_tax_total": 1, "grand_total": 1, "labors": 1,
        "created_at": 1,
    }):
        t = _wo_totals(wo)
        bk = _time_bucket_key(wo.get("created_at"), chart_bucket)
        totals = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
        # Use totals.parts (pure base: price*qty) when available;
        # fall back to parts_total minus core and misc for legacy WOs.
        parts_base = totals.get("parts")
        core_total = _round2(totals.get("core_total") or 0)
        if parts_base is not None:
            parts_base = _round2(parts_base)
        else:
            parts_base = _round2(t["parts_total"] - core_total)

        wo_count += 1
        sales_labor += t["labor_total"]
        sales_parts_sale += parts_base
        wo_parts_cost = _wo_parts_cost(wo)
        sales_parts_cost += wo_parts_cost
        sales_core_charges += core_total
        sales_misc += t["misc_charges_total"]
        sales_tax += t["sales_tax_total"]
        sales_revenue += t["grand_total"]

        if bk:
            tb = time_buckets.setdefault(bk, {"revenue": 0.0, "labor": 0.0, "parts_sale": 0.0, "parts_cost": 0.0, "mech_hours": 0.0})
            tb["revenue"] += t["grand_total"]
            tb["labor"] += t["labor_total"]
            tb["parts_sale"] += parts_base
            tb["parts_cost"] += wo_parts_cost

        # --- Mechanic hours from this WO ---
        wo_mech_hours = 0.0
        for block in (wo.get("labors") or []):
            if not isinstance(block, dict):
                continue
            labor = block.get("labor")
            if not isinstance(labor, dict):
                continue
            try:
                hours = float(labor.get("hours") or 0)
            except (ValueError, TypeError):
                hours = 0.0
            if hours <= 0:
                continue
            assigned = labor.get("assigned_mechanics")
            if not isinstance(assigned, list) or not assigned:
                continue
            for mech in assigned:
                if not isinstance(mech, dict):
                    continue
                uid = str(mech.get("user_id") or "").strip()
                if not uid:
                    continue
                try:
                    pct = float(mech.get("percent") or 0)
                except (ValueError, TypeError):
                    pct = 0.0
                if pct <= 0:
                    continue
                allocated = _round2(hours * pct / 100.0)
                if allocated <= 0:
                    continue
                bucket = mechanics.setdefault(uid, {
                    "name": str(mech.get("name") or "").strip() or uid,
                    "hours": 0.0,
                })
                bucket["hours"] = _round2(bucket["hours"] + allocated)
                wo_mech_hours += allocated

        if bk and wo_mech_hours > 0:
            time_buckets.setdefault(bk, {"revenue": 0.0, "labor": 0.0, "parts_sale": 0.0, "parts_cost": 0.0, "mech_hours": 0.0})
            time_buckets[bk]["mech_hours"] += wo_mech_hours

    sales_labor = _round2(sales_labor)
    sales_parts_sale = _round2(sales_parts_sale)
    sales_parts_cost = _round2(sales_parts_cost)
    sales_core_charges = _round2(sales_core_charges)
    sales_misc = _round2(sales_misc)
    sales_tax = _round2(sales_tax)
    sales_revenue = _round2(sales_revenue)

    # Sort mechanics by hours desc
    mech_sorted = sorted(mechanics.values(), key=lambda m: m["hours"], reverse=True)
    total_mech_hours = _round2(sum(m["hours"] for m in mech_sorted))

    # --- Parts Orders ---
    po_query = {
        "shop_id": shop_id,
        "is_active": {"$ne": False},
    }
    po_query = _append_and(po_query, _build_date_filter(date_ctx, field="created_at"))

    po_count = 0
    po_total = 0.0
    po_non_inventory = 0.0
    po_paid = 0.0
    po_balance = 0.0

    for order in shop_db.parts_orders.find(po_query, {
        "total_amount": 1, "paid_amount": 1, "remaining_balance": 1,
        "non_inventory_amounts": 1,
    }):
        po_count += 1
        po_total += float(order.get("total_amount") or 0)
        po_paid += float(order.get("paid_amount") or 0)
        po_balance += float(order.get("remaining_balance") or 0)
        for line in (order.get("non_inventory_amounts") or []):
            if isinstance(line, dict):
                po_non_inventory += max(0.0, float(line.get("amount") or 0))

    po_total = _round2(po_total)
    po_non_inventory = _round2(po_non_inventory)
    po_paid = _round2(po_paid)
    po_balance = _round2(po_balance)
    po_parts_only = _round2(po_total - po_non_inventory)

    # --- Combined ---
    net_revenue = _round2(sales_revenue - po_total)
    parts_profit = _round2(sales_parts_sale - sales_parts_cost)

    rows = [
        {
            "category": "Sales — Labor",
            "amount": sales_labor,
        },
        {
            "category": "Sales — Parts (Sale Price)",
            "amount": sales_parts_sale,
        },
        {
            "category": "Sales — Parts (Cost)",
            "amount": sales_parts_cost,
        },
        {
            "category": "Sales — Parts Profit",
            "amount": parts_profit,
        },
        {
            "category": "Sales — Core Charges",
            "amount": sales_core_charges,
        },
        {
            "category": "Sales — Misc Charges",
            "amount": sales_misc,
        },
        {
            "category": "Sales — Tax",
            "amount": sales_tax,
        },
        {
            "category": "Sales — Total Revenue",
            "amount": sales_revenue,
        },
        {
            "category": "Parts Orders — Parts",
            "amount": po_parts_only,
        },
        {
            "category": "Parts Orders — Non-Inventory",
            "amount": po_non_inventory,
        },
        {
            "category": "Parts Orders — Total Spent",
            "amount": po_total,
        },
        {
            "category": "Parts Orders — Paid",
            "amount": po_paid,
        },
        {
            "category": "Parts Orders — Balance",
            "amount": po_balance,
        },
        {
            "category": "Net Revenue (Sales − Parts Orders)",
            "amount": net_revenue,
        },
    ]

    # --- Mechanic Hours section ---
    rows.append({"category": "", "amount": None})
    rows.append({"category": "Mechanic Hours — Total", "amount": total_mech_hours, "is_hours": True})
    for m in mech_sorted:
        rows.append({"category": f"  {m['name']}", "amount": m["hours"], "is_hours": True})

    labels = _fill_bucket_gaps(time_buckets, chart_bucket)
    chart_data = {
        "labels": labels,
        "datasets": [
            {"label": "Revenue", "data": [_round2(time_buckets.get(l, {}).get("revenue", 0)) for l in labels]},
            {"label": "Labor", "data": [_round2(time_buckets.get(l, {}).get("labor", 0)) for l in labels]},
            {"label": "Parts (Sale)", "data": [_round2(time_buckets.get(l, {}).get("parts_sale", 0)) for l in labels]},
            {"label": "Parts (Cost)", "data": [_round2(time_buckets.get(l, {}).get("parts_cost", 0)) for l in labels]},
            {"label": "Mechanic Hours", "data": [_round2(time_buckets.get(l, {}).get("mech_hours", 0)) for l in labels], "yAxisID": "y1"},
        ],
    }

    return {
        "title": "General Revenue Report",
        "summary": {
            "sales_revenue": sales_revenue,
            "parts_sale": sales_parts_sale,
            "parts_cost": sales_parts_cost,
            "parts_profit": parts_profit,
            "core_charges": sales_core_charges,
            "po_total_spent": po_total,
            "net_revenue": net_revenue,
            "wo_count": wo_count,
            "po_count": po_count,
            "total_mech_hours": total_mech_hours,
        },
        "rows": rows,
        "chart_data": chart_data,
    }


def _report_mechanic_hours(shop_db, shop_id, date_ctx, chart_bucket="month"):
    wo_query = {
        "shop_id": shop_id,
        "is_active": True,
    }
    wo_query = _append_and(wo_query, _build_date_filter(date_ctx, field="created_at"))

    mechanics: dict[str, dict] = {}
    time_buckets: dict[str, dict[str, float]] = {}

    for wo in shop_db.work_orders.find(wo_query, {"labors": 1, "created_at": 1}):
        wo_id = wo.get("_id")
        wo_ids_seen: dict[str, set] = {}
        wo_dt = wo.get("created_at")

        for block in (wo.get("labors") or []):
            if not isinstance(block, dict):
                continue
            labor = block.get("labor")
            if not isinstance(labor, dict):
                continue

            try:
                hours = float(labor.get("hours") or 0)
            except (ValueError, TypeError):
                hours = 0.0
            if hours <= 0:
                continue

            assigned = labor.get("assigned_mechanics")
            if not isinstance(assigned, list) or not assigned:
                continue

            for mech in assigned:
                if not isinstance(mech, dict):
                    continue
                uid = str(mech.get("user_id") or "").strip()
                if not uid:
                    continue

                try:
                    pct = float(mech.get("percent") or 0)
                except (ValueError, TypeError):
                    pct = 0.0
                if pct <= 0:
                    continue

                allocated = _round2(hours * pct / 100.0)
                if allocated <= 0:
                    continue

                bucket = mechanics.setdefault(uid, {
                    "mechanic_id": uid,
                    "mechanic_name": str(mech.get("name") or "").strip() or "-",
                    "total_hours": 0.0,
                    "wo_count": 0,
                    "labor_entries": 0,
                })

                bucket["total_hours"] = _round2(bucket["total_hours"] + allocated)
                bucket["labor_entries"] += 1

                wo_set = wo_ids_seen.setdefault(uid, set())
                if wo_id and wo_id not in wo_set:
                    wo_set.add(wo_id)
                    bucket["wo_count"] += 1

                # Chart time bucket per mechanic
                tk = _time_bucket_key(wo_dt, chart_bucket)
                if tk:
                    tb = time_buckets.setdefault(tk, {})
                    mech_name = bucket["mechanic_name"]
                    tb[mech_name] = _round2(tb.get(mech_name, 0) + allocated)

    rows = sorted(mechanics.values(), key=lambda x: x.get("total_hours", 0), reverse=True)

    total_hours = _round2(sum(float(r.get("total_hours") or 0) for r in rows))
    total_wo = sum(int(r.get("wo_count") or 0) for r in rows)
    total_entries = sum(int(r.get("labor_entries") or 0) for r in rows)

    # Build chart data: one dataset per mechanic
    labels = _fill_bucket_gaps(time_buckets, chart_bucket)
    mech_names = [r["mechanic_name"] for r in rows]
    chart_data = {
        "labels": labels,
        "datasets": [
            {"label": name, "data": [_round2(time_buckets.get(l, {}).get(name, 0)) for l in labels]}
            for name in mech_names
        ],
        "is_hours": True,
    }

    return {
        "title": "Mechanic Hours",
        "summary": {
            "mechanics_count": len(rows),
            "total_hours": total_hours,
            "total_wo": total_wo,
            "total_entries": total_entries,
        },
        "rows": rows,
        "chart_data": chart_data,
    }


def _build_standard_reports_context(selected_tab: str, args, *, skip_report_data: bool = False):
    master = get_master_db()
    shop = _get_active_shop(master)

    selected_tab = str(selected_tab or "sales_summary").strip().lower()
    if selected_tab not in STANDARD_REPORT_TABS:
        selected_tab = "sales_summary"

    layout_nav = filter_nav_items(NAV_ITEMS)

    if not shop:
        return {
            "ok": False,
            "error": "Active shop is not selected.",
            "layout_nav": layout_nav,
            "selected_tab": selected_tab,
        }

    shop_db = _get_shop_db(shop)
    if shop_db is None:
        return {
            "ok": False,
            "error": "Shop database not configured.",
            "layout_nav": layout_nav,
            "selected_tab": selected_tab,
        }

    date_ctx = build_date_range_filters(args, default_preset="this_month")

    customer_ids_raw = (args.get("customer_ids") or "").strip()
    include_customer_ids_raw = [v.strip() for v in customer_ids_raw.split(",") if v.strip()] if customer_ids_raw else args.getlist("include_customer_ids")
    exclude_customer_ids_raw = args.getlist("exclude_customer_ids")

    include_customer_ids = _to_oid_list(include_customer_ids_raw)
    exclude_customer_ids = _to_oid_list(exclude_customer_ids_raw)

    exclude_set = {str(x) for x in exclude_customer_ids}
    include_customer_ids = [x for x in include_customer_ids if str(x) not in exclude_set]

    customer_options, customer_map = _get_customer_options(shop_db, shop["_id"])

    vendor_ids_raw = (args.get("vendor_ids") or "").strip()
    include_vendor_ids_raw = [v.strip() for v in vendor_ids_raw.split(",") if v.strip()] if vendor_ids_raw else []
    include_vendor_ids = _to_oid_list(include_vendor_ids_raw)

    vendor_options, vendor_map = _get_vendor_options(shop_db, shop["_id"])

    chart_bucket_raw = str(args.get("chart_bucket") or "month").strip().lower()
    chart_bucket = chart_bucket_raw if chart_bucket_raw in ("week", "month") else "month"

    if skip_report_data:
        return {
            "ok": True,
            "layout_nav": layout_nav,
            "selected_tab": selected_tab,
            "date_preset": date_ctx.get("date_preset") or "this_month",
            "date_from": date_ctx.get("date_from") or "",
            "date_to": date_ctx.get("date_to") or "",
            "customer_options": customer_options,
            "include_customer_ids": [str(x) for x in include_customer_ids],
            "exclude_customer_ids": [str(x) for x in exclude_customer_ids],
            "show_customer_filters": selected_tab in CUSTOMER_FILTER_TABS,
            "show_vendor_filters": selected_tab in VENDOR_FILTER_TABS,
            "vendor_options": vendor_options,
            "include_vendor_ids": [str(x) for x in include_vendor_ids],
            "report_data": {"title": "", "summary": {}, "rows": []},
            "shop_name": str(shop.get("name") or "-"),
            "generated_at": _now_utc(),
        }

    if selected_tab == "sales_summary":
        report_data = _report_sales_summary(
            shop_db,
            shop["_id"],
            date_ctx,
            include_customer_ids,
            exclude_customer_ids,
            customer_map,
            chart_bucket=chart_bucket,
        )
    elif selected_tab == "payments_summary":
        report_data = _report_payments_summary(
            shop_db,
            shop["_id"],
            date_ctx,
            include_customer_ids,
            exclude_customer_ids,
            customer_map,
            chart_bucket=chart_bucket,
        )
    elif selected_tab == "customer_balances":
        report_data = _report_customer_balances(
            shop_db,
            shop["_id"],
            {},
            include_customer_ids,
            exclude_customer_ids,
            customer_map,
        )
    elif selected_tab == "vendor_balances":
        report_data = _report_vendor_balances(
            shop_db,
            shop["_id"],
            {},
        )
    elif selected_tab == "parts_orders_summary":
        report_data = _report_parts_orders_summary(
            shop_db,
            shop["_id"],
            date_ctx,
            include_vendor_ids,
            vendor_map,
            chart_bucket=chart_bucket,
        )
    elif selected_tab == "mechanic_hours":
        report_data = _report_mechanic_hours(
            shop_db,
            shop["_id"],
            date_ctx,
            chart_bucket=chart_bucket,
        )
    else:
        report_data = _report_general_revenue(
            shop_db,
            shop["_id"],
            date_ctx,
            chart_bucket=chart_bucket,
        )

    return {
        "ok": True,
        "layout_nav": layout_nav,
        "selected_tab": selected_tab,
        "date_preset": date_ctx.get("date_preset") or "this_month",
        "date_from": date_ctx.get("date_from") or "",
        "date_to": date_ctx.get("date_to") or "",
        "customer_options": customer_options,
        "include_customer_ids": [str(x) for x in include_customer_ids],
        "exclude_customer_ids": [str(x) for x in exclude_customer_ids],
        "show_customer_filters": selected_tab in CUSTOMER_FILTER_TABS,
        "show_vendor_filters": selected_tab in VENDOR_FILTER_TABS,
        "vendor_options": vendor_options,
        "include_vendor_ids": [str(x) for x in include_vendor_ids],
        "report_data": report_data,
        "shop_name": str(shop.get("name") or "-"),
        "generated_at": _now_utc(),
    }


@reports_bp.get("")
@login_required
def reports_index():
    layout_nav = filter_nav_items(NAV_ITEMS)
    return render_internal_page(
        "public/reports.html",
        layout_nav,
        "reports",
    )


@reports_bp.get("/standard/<report_key>")
@login_required
def standard_report_page(report_key: str):
    ctx = _build_standard_reports_context(report_key, request.args, skip_report_data=True)

    if not ctx.get("ok"):
        return render_internal_page(
            "public/reports/standard.html",
            ctx.get("layout_nav") or filter_nav_items(NAV_ITEMS),
            "reports",
            report_error=ctx.get("error") or "Unable to build report.",
            selected_tab=ctx.get("selected_tab") or "sales_summary",
            date_preset="this_month",
            date_from="",
            date_to="",
            customer_options=[],
            include_customer_ids=[],
            exclude_customer_ids=[],
            show_customer_filters=False,
            show_vendor_filters=False,
            vendor_options=[],
            include_vendor_ids=[],
            report_data={"title": "", "summary": {}, "rows": []},
        )

    return render_internal_page(
        "public/reports/standard.html",
        ctx["layout_nav"],
        "reports",
        selected_tab=ctx["selected_tab"],
        date_preset=ctx["date_preset"],
        date_from=ctx["date_from"],
        date_to=ctx["date_to"],
        customer_options=ctx["customer_options"],
        include_customer_ids=ctx["include_customer_ids"],
        exclude_customer_ids=ctx["exclude_customer_ids"],
        show_customer_filters=ctx["show_customer_filters"],
        show_vendor_filters=ctx["show_vendor_filters"],
        vendor_options=ctx["vendor_options"],
        include_vendor_ids=ctx["include_vendor_ids"],
        report_data=ctx["report_data"],
        generated_at=ctx["generated_at"],
        shop_name=ctx["shop_name"],
    )


@reports_bp.get("/api/standard/<report_key>")
@login_required
def standard_report_api(report_key: str):
    ctx = _build_standard_reports_context(report_key, request.args)
    if not ctx.get("ok"):
        return jsonify(ok=False, error=ctx.get("error") or "Unable to build report."), 400

    rd = ctx["report_data"]
    return jsonify(
        ok=True,
        selected_tab=ctx["selected_tab"],
        shop_name=ctx["shop_name"],
        report_data={
            "title": rd.get("title") or "",
            "summary": rd.get("summary") or {},
            "rows": rd.get("rows") or [],
            "chart_data": rd.get("chart_data") or None,
        },
    )


@reports_bp.get("/standard/<report_key>/pdf")
@login_required
def standard_report_pdf(report_key: str):
    ctx = _build_standard_reports_context(report_key, request.args)
    if not ctx.get("ok"):
        payload = (ctx.get("error") or "Unable to build report.").encode("utf-8")
        response = make_response(payload, 400)
        response.headers["Content-Type"] = "text/plain; charset=utf-8"
        return response

    html = render_template(
        "public/reports/standard_pdf.html",
        selected_tab=ctx["selected_tab"],
        date_preset=ctx["date_preset"],
        date_from=ctx["date_from"],
        date_to=ctx["date_to"],
        include_customer_ids=ctx["include_customer_ids"],
        exclude_customer_ids=ctx["exclude_customer_ids"],
        show_customer_filters=ctx["show_customer_filters"],
        report_data=ctx["report_data"],
        generated_at=ctx["generated_at"],
        shop_name=ctx["shop_name"],
        chart_image=render_chart_to_base64(ctx["report_data"].get("chart_data")),
    )

    pdf_bytes = render_html_to_pdf(html)
    filename = f"{ctx['selected_tab']}_{ctx['generated_at'].strftime('%Y%m%d_%H%M%S')}.pdf"

    response = make_response(pdf_bytes)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Content-Length"] = str(len(pdf_bytes))
    return response


@reports_bp.get("/audit")
@login_required
def activity_journal_page():
    layout_nav = filter_nav_items(NAV_ITEMS)
    master = get_master_db()

    tenant_id = str(session.get(SESSION_TENANT_ID) or "")

    page_raw = (request.args.get("page") or "1").strip()
    try:
        page = int(page_raw)
    except Exception:
        page = 1
    if page < 1:
        page = 1

    per_page = 25
    method_filter = (request.args.get("method") or "").strip().upper()
    endpoint_filter = (request.args.get("endpoint") or "").strip()

    query = {}
    if tenant_id:
        query["tenant_id"] = tenant_id
    if method_filter:
        query["method"] = method_filter
    if endpoint_filter:
        query["endpoint"] = endpoint_filter

    sort = get_sort_params(
        request.args,
        [("created_at", -1)],
        ["created_at", "method", "endpoint", "path", "status_code"],
    )
    sort_by = sort[0][0] if sort else "created_at"
    sort_dir = "asc" if sort and sort[0][1] == 1 else "desc"

    total = master.audit_journal.count_documents(query)
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    if page > total_pages:
        page = total_pages

    skip = (page - 1) * per_page
    cursor = master.audit_journal.find(
        query,
        {
            "created_at": 1,
            "method": 1,
            "path": 1,
            "endpoint": 1,
            "status_code": 1,
            "user_id": 1,
            "shop_id": 1,
            "payload": 1,
            "error": 1,
        },
    ).sort(sort).skip(skip).limit(per_page)

    entries = []
    for row in cursor:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        entries.append(
            {
                "created_at": row.get("created_at"),
                "method": str(row.get("method") or ""),
                "path": str(row.get("path") or ""),
                "endpoint": str(row.get("endpoint") or ""),
                "status_code": int(row.get("status_code") or 0),
                "user_id": str(row.get("user_id") or ""),
                "shop_id": str(row.get("shop_id") or ""),
                "error": str(row.get("error") or ""),
                "payload": payload,
            }
        )

    return render_internal_page(
        "public/reports/audit.html",
        layout_nav,
        "reports",
        activity_entries=entries,
        activity_total=total,
        activity_page=page,
        activity_per_page=per_page,
        activity_total_pages=total_pages,
        method_filter=method_filter,
        endpoint_filter=endpoint_filter,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
