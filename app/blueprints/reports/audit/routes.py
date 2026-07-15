from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bson import ObjectId
from flask import current_app, jsonify, make_response, render_template, request, session

from app.blueprints.main.routes import NAV_ITEMS
from app.blueprints.reports import reports_bp
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import SESSION_TENANT_ID, SESSION_SHOP_ID, login_required
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
    "general_revenue",
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


def _preferred_dt(doc: dict, primary: str = "work_order_date", fallback: str = "created_at"):
    """Return preferred date matching how tables display dates."""
    val = doc.get(primary) if isinstance(doc, dict) else None
    if isinstance(val, datetime):
        return val
    return doc.get(fallback) if isinstance(doc, dict) else None


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


def _build_labor_rates_map(shop_db, shop_id) -> dict:
    """Return {rate_code -> hourly_rate} for a shop."""
    rates: dict[str, float] = {}
    try:
        cursor = shop_db.labor_rates.find({"shop_id": shop_id}, {"code": 1, "hourly_rate": 1})
    except Exception:
        return rates
    for r in cursor:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        try:
            rate = float(r.get("hourly_rate") or 0)
        except (ValueError, TypeError):
            rate = 0.0
        rates[code] = rate
    return rates


def _wo_invoiced_hours(wo: dict, rates_map: dict | None = None) -> float:
    """Sum of labor hours per WO.

    Tolerates two block shapes in the wild:
      - canonical nested: {"labor": {"hours", "rate_code", "hourly_rate"}, ...}
      - legacy flat:      {"labor_hours", "labor_rate_code", ...}

    Uses stored hours when present. When hours are missing but a rate is known
    (per-block snapshot, or current value by rate_code), falls back to
    `totals.labors[i].labor / rate` so flat-rate entries still contribute.
    """
    rates_map = rates_map or {}
    totals = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    totals_blocks = totals.get("labors") if isinstance(totals.get("labors"), list) else []
    hours_total = 0.0
    for i, block in enumerate(wo.get("labors") or []):
        if not isinstance(block, dict):
            continue
        nested = block.get("labor") if isinstance(block.get("labor"), dict) else {}
        # Field lookup: prefer nested, fall back to flat fields on the block.
        raw_hours = nested.get("hours") if "hours" in nested else block.get("labor_hours")
        raw_rate_code = nested.get("rate_code") if "rate_code" in nested else block.get("labor_rate_code")
        raw_hourly_rate = nested.get("hourly_rate") if "hourly_rate" in nested else block.get("labor_hourly_rate")

        try:
            h = float(raw_hours or 0)
        except (ValueError, TypeError):
            h = 0.0
        if h > 0:
            hours_total += h
            continue
        # Fallback via labor amount / rate. Prefer the per-block snapshot so
        # changes/removal of labor rates later don't affect historical data.
        try:
            snap_rate = float(raw_hourly_rate or 0)
        except (ValueError, TypeError):
            snap_rate = 0.0
        rate = snap_rate
        if rate <= 0:
            rate_code = str(raw_rate_code or "").strip()
            if rate_code:
                rate = float(rates_map.get(rate_code) or 0.0)
        if rate <= 0:
            continue
        amt = 0.0
        if i < len(totals_blocks) and isinstance(totals_blocks[i], dict):
            try:
                amt = float(totals_blocks[i].get("labor") or 0)
            except (ValueError, TypeError):
                amt = 0.0
        if amt > 0:
            hours_total += amt / rate
    return _round2(hours_total)


def _wo_totals(wo: dict):
    """Match Work Orders table column semantics:
      - labor_total = labor + shop_supply
      - parts_total = parts + cores + misc (full stored parts_total)
      - sales_tax_total
      - grand_total = labor_total + parts_total + sales_tax_total
    """
    totals = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    labor = totals.get("labor_total") if totals.get("labor_total") is not None else wo.get("labor_total")
    parts = totals.get("parts_total") if totals.get("parts_total") is not None else wo.get("parts_total")
    misc = totals.get("misc_total") or 0
    core = totals.get("core_total") or 0
    parts_pure = totals.get("parts")
    tax = totals.get("sales_tax_total") if totals.get("sales_tax_total") is not None else wo.get("sales_tax_total")
    grand = totals.get("grand_total") if totals.get("grand_total") is not None else wo.get("grand_total")
    return {
        "labor_total": _round2(labor),
        # Match WO table "Parts" column exactly (includes cores + misc)
        "parts_total": _round2(parts),
        "parts_pure": _round2(parts_pure if parts_pure is not None else (float(parts or 0) - float(core or 0) - float(misc or 0))),
        "core_total": _round2(core),
        "misc_charges_total": _round2(misc),
        "sales_tax_total": _round2(tax),
        "grand_total": _round2(grand),
    }


def _num_expr(expr):
    """Терпимое приведение к double: null/мусор → 0 (как float(x or 0) с try/except)."""
    return {"$convert": {"input": expr, "to": "double", "onError": 0.0, "onNull": 0.0}}


def _round2_expr(expr):
    """Зеркало _round2(): +1e-12 перед округлением, иначе банковское округление
    Mongo расходится с Python на граничных .xx5 и суммы уезжают на центы."""
    return {"$round": [{"$add": [expr, 1e-12]}, 2]}


def _totals_fallback_expr(field: str):
    """totals.<field>, при отсутствии — топ-левел <field>: семантика _wo_totals()."""
    return _round2_expr(_num_expr({"$ifNull": [f"$totals.{field}", {"$ifNull": [f"${field}", 0]}]}))


def _parts_cost_expr():
    """Сумма qty*cost по labors[].parts[] — зеркало _wo_parts_cost()."""
    return _round2_expr({"$reduce": {
        "input": {"$cond": [{"$isArray": "$labors"}, "$labors", []]},
        "initialValue": 0.0,
        "in": {"$add": ["$$value", {"$reduce": {
            "input": {"$cond": [{"$isArray": "$$this.parts"}, "$$this.parts", []]},
            "initialValue": 0.0,
            "in": {"$add": ["$$value", {"$multiply": [
                {"$max": [0, {"$convert": {"input": "$$this.qty", "to": "int", "onError": 0, "onNull": 0}}]},
                {"$max": [0.0, _num_expr("$$this.cost")]},
            ]}]},
        }}]},
    }})


def _invoiced_hours_expr(rates_map: dict):
    """Зеркало _wo_invoiced_hours(): stored hours, иначе totals.labors[i].labor / rate
    (ставка — снапшот блока, иначе текущая по rate_code)."""
    rates_list = {"$literal": [{"k": k, "v": v} for k, v in rates_map.items()]}
    return _round2_expr({"$let": {
        "vars": {
            "blocks": {"$cond": [{"$isArray": "$labors"}, "$labors", []]},
            "tblocks": {"$cond": [{"$isArray": "$totals.labors"}, "$totals.labors", []]},
        },
        "in": {"$reduce": {
            "input": {"$range": [0, {"$size": "$$blocks"}]},
            "initialValue": 0.0,
            "in": {"$add": ["$$value", {"$let": {
                "vars": {
                    "b": {"$arrayElemAt": ["$$blocks", "$$this"]},
                    "tb": {"$arrayElemAt": ["$$tblocks", "$$this"]},
                },
                "in": {"$let": {
                    "vars": {
                        "h": _num_expr({"$ifNull": ["$$b.labor.hours", "$$b.labor_hours"]}),
                        "snap": _num_expr({"$ifNull": ["$$b.labor.hourly_rate", "$$b.labor_hourly_rate"]}),
                        "code": {"$trim": {"input": {"$convert": {
                            "input": {"$ifNull": ["$$b.labor.rate_code", "$$b.labor_rate_code"]},
                            "to": "string", "onError": "", "onNull": "",
                        }}}},
                        "amt": _num_expr("$$tb.labor"),
                    },
                    "in": {"$cond": [
                        {"$gt": ["$$h", 0]},
                        "$$h",
                        {"$let": {
                            "vars": {"rate": {"$cond": [
                                {"$gt": ["$$snap", 0]},
                                "$$snap",
                                _num_expr({"$arrayElemAt": [{"$map": {
                                    "input": {"$filter": {"input": rates_list, "as": "r", "cond": {"$eq": ["$$r.k", "$$code"]}}},
                                    "as": "r", "in": "$$r.v",
                                }}, 0]}),
                            ]}},
                            "in": {"$cond": [
                                {"$and": [{"$gt": ["$$rate", 0]}, {"$gt": ["$$amt", 0]}]},
                                {"$divide": ["$$amt", "$$rate"]},
                                0.0,
                            ]},
                        }},
                    ]},
                }},
            }}]},
        }},
    }})


def _time_bucket_key_expr(chart_bucket: str):
    """Зеркало _time_bucket_key(_preferred_dt(...)): month → YYYY-MM,
    week → дата понедельника YYYY-MM-DD. Не-даты → null (отбрасываются)."""
    preferred = {"$cond": [
        {"$eq": [{"$type": "$work_order_date"}, "date"]},
        "$work_order_date",
        {"$cond": [{"$eq": [{"$type": "$created_at"}, "date"]}, "$created_at", None]},
    ]}
    if chart_bucket == "week":
        monday = {"$subtract": [
            "$$dt",
            {"$multiply": [86400000, {"$subtract": [{"$isoDayOfWeek": "$$dt"}, 1]}]},
        ]}
        fmt = {"$dateToString": {"format": "%Y-%m-%d", "date": monday}}
    else:
        fmt = {"$dateToString": {"format": "%Y-%m", "date": "$$dt"}}
    return {"$let": {"vars": {"dt": preferred}, "in": {"$cond": [{"$eq": ["$$dt", None]}, None, fmt]}}}


def _report_sales_summary(shop_db, shop_id, date_ctx, include_customer_ids, exclude_customer_ids, customer_map, chart_bucket="month"):
    query = {
        "shop_id": shop_id,
        "is_active": True,
        # Старый цикл пропускал WO без customer_id (включая график) — сохраняем.
        "customer_id": {"$ne": None},
    }
    query = _append_and(query, _build_date_filter(date_ctx, field="work_order_date", fallback_field="created_at"))

    if include_customer_ids:
        query = _append_and(query, {"customer_id": {"$in": include_customer_ids}})
    if exclude_customer_ids:
        query = _append_and(query, {"customer_id": {"$nin": exclude_customer_ids}})

    rates_map = _build_labor_rates_map(shop_db, shop_id)

    # Вся арифметика в Mongo: раньше сюда выгружались все WO периода вместе с
    # тяжёлыми labors[] и суммировались в Python — на больших периодах это
    # держало воркер и память. Pipeline считает то же самое на сервере БД.
    pipeline = [
        {"$match": query},
        {"$project": {
            "customer_id": 1,
            "labor_total": _totals_fallback_expr("labor_total"),
            "parts_total": _totals_fallback_expr("parts_total"),
            "sales_tax_total": _totals_fallback_expr("sales_tax_total"),
            "grand_total": _totals_fallback_expr("grand_total"),
            "parts_cost": _parts_cost_expr(),
            "invoiced_hours": _invoiced_hours_expr(rates_map),
            "bucket_key": _time_bucket_key_expr(chart_bucket),
        }},
        {"$facet": {
            "by_customer": [
                {"$group": {
                    "_id": "$customer_id",
                    "orders_count": {"$sum": 1},
                    "labor_total": {"$sum": "$labor_total"},
                    "parts_total": {"$sum": "$parts_total"},
                    "parts_cost_total": {"$sum": "$parts_cost"},
                    "sales_tax_total": {"$sum": "$sales_tax_total"},
                    "grand_total": {"$sum": "$grand_total"},
                    "invoiced_hours": {"$sum": "$invoiced_hours"},
                }},
            ],
            "by_time": [
                {"$match": {"bucket_key": {"$ne": None}}},
                {"$group": {
                    "_id": "$bucket_key",
                    "revenue": {"$sum": "$grand_total"},
                    "labor": {"$sum": "$labor_total"},
                    "parts": {"$sum": "$parts_total"},
                }},
            ],
        }},
    ]

    facets = next(iter(shop_db.work_orders.aggregate(pipeline, allowDiskUse=True)), {})

    rows = []
    for g in facets.get("by_customer") or []:
        sid = str(g.get("_id"))
        rows.append({
            "customer_id": sid,
            "customer_label": customer_map.get(sid) or "-",
            "orders_count": int(g.get("orders_count") or 0),
            "labor_total": _round2(g.get("labor_total")),
            "parts_total": _round2(g.get("parts_total")),
            "parts_cost_total": _round2(g.get("parts_cost_total")),
            "sales_tax_total": _round2(g.get("sales_tax_total")),
            "grand_total": _round2(g.get("grand_total")),
            "invoiced_hours": _round2(g.get("invoiced_hours")),
        })

    time_buckets: dict[str, dict] = {
        str(g["_id"]): {
            "revenue": _round2(g.get("revenue")),
            "labor": _round2(g.get("labor")),
            "parts": _round2(g.get("parts")),
        }
        for g in (facets.get("by_time") or [])
        if g.get("_id")
    }

    rows = sorted(rows, key=lambda x: x.get("grand_total", 0), reverse=True)

    total_orders = sum(int(r.get("orders_count") or 0) for r in rows)
    total_revenue = _round2(sum(float(r.get("grand_total") or 0) for r in rows))
    total_labor = _round2(sum(float(r.get("labor_total") or 0) for r in rows))
    total_parts = _round2(sum(float(r.get("parts_total") or 0) for r in rows))
    total_parts_cost = _round2(sum(float(r.get("parts_cost_total") or 0) for r in rows))
    total_tax = _round2(sum(float(r.get("sales_tax_total") or 0) for r in rows))
    total_invoiced_hours = _round2(sum(float(r.get("invoiced_hours") or 0) for r in rows))

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
            "parts_cost_total": total_parts_cost,
            "sales_tax_total": total_tax,
            "invoiced_hours": total_invoiced_hours,
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
    query = _append_and(query, _build_date_filter(date_ctx, field="work_order_date", fallback_field="created_at"))
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


def _build_parts_order_paid_map(shop_db, order_ids: list):
    """Sum active payments from parts_order_payments per order. Mirrors
    `_build_parts_order_paid_map` in parts/routes.py used by the Parts Orders table."""
    out: dict = {}
    if not order_ids:
        return out
    pipeline = [
        {"$match": {"parts_order_id": {"$in": order_ids}, "is_active": True}},
        {"$group": {"_id": "$parts_order_id", "paid_total": {"$sum": {"$ifNull": ["$amount", 0]}}}},
    ]
    for row in shop_db.parts_order_payments.aggregate(pipeline):
        oid = row.get("_id")
        if oid is not None:
            out[oid] = float(row.get("paid_total") or 0)
    return out


def _report_vendor_balances(shop_db, shop_id, date_ctx):
    query = {
        "shop_id": shop_id,
        "is_active": {"$ne": False},
    }
    query = _append_and(query, _build_date_filter(date_ctx, field="order_date", fallback_field="created_at"))

    orders = list(shop_db.parts_orders.find(query, {
        "_id": 1,
        "vendor_id": 1,
        "items": 1,
        "non_inventory_amounts": 1,
    }))

    order_ids = [o.get("_id") for o in orders if o.get("_id")]
    paid_map = _build_parts_order_paid_map(shop_db, order_ids)

    vendor_map = {}
    for v in shop_db.vendors.find(
        {"shop_id": shop_id},
        {"name": 1},
    ):
        vid = v.get("_id")
        if vid:
            vendor_map[str(vid)] = str(v.get("name") or "-").strip() or "-"

    rows_by_vendor: dict[str, dict] = {}
    for order in orders:
        vendor_id = order.get("vendor_id")
        sid = str(vendor_id) if vendor_id else ""
        bucket = rows_by_vendor.setdefault(
            sid,
            {
                "vendor_id": sid,
                "vendor_label": vendor_map.get(sid) or "-",
                "orders_count": 0,
                "total_amount": 0.0,
                "paid_amount": 0.0,
                "remaining_balance": 0.0,
            },
        )

        # Live total = qty*(price+core_charge) + sum(non_inventory_amounts)
        order_total = 0.0
        for item in (order.get("items") or []):
            if not isinstance(item, dict):
                continue
            qty = max(0, int(item.get("quantity") or 0))
            price = max(0.0, float(item.get("price") or 0))
            core_charge = max(0.0, float(item.get("core_charge") or 0))
            order_total += qty * (price + core_charge)
        for line in (order.get("non_inventory_amounts") or []):
            if not isinstance(line, dict):
                continue
            order_total += max(0.0, float(line.get("amount") or 0))
        order_total = _round2(order_total)
        paid = _round2(max(0.0, paid_map.get(order.get("_id"), 0.0)))
        balance = _round2(max(0.0, order_total - paid))

        bucket["orders_count"] += 1
        bucket["total_amount"] = _round2(bucket["total_amount"] + order_total)
        bucket["paid_amount"] = _round2(bucket["paid_amount"] + paid)
        bucket["remaining_balance"] = _round2(bucket["remaining_balance"] + balance)

    rows = sorted(rows_by_vendor.values(), key=lambda x: x.get("remaining_balance", 0), reverse=True)

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
    query = _append_and(query, _build_date_filter(date_ctx, field="order_date", fallback_field="created_at"))
    if include_vendor_ids:
        query = _append_and(query, {"vendor_id": {"$in": include_vendor_ids}})

    orders = list(shop_db.parts_orders.find(query, {
        "_id": 1,
        "vendor_id": 1,
        "created_at": 1,
        "order_date": 1,
        "items": 1,
        "non_inventory_amounts": 1,
    }))

    order_ids = [o.get("_id") for o in orders if o.get("_id")]
    paid_map = _build_parts_order_paid_map(shop_db, order_ids)

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

        # Parts items total — match Parts Orders table convention:
        #   items_amount = qty * (price + core_charge)
        # Break out parts (qty*price) and cores (qty*core_charge) separately.
        items_total = 0.0
        cores_total = 0.0
        for item in (order.get("items") or []):
            if not isinstance(item, dict):
                continue
            qty = max(0, int(item.get("quantity") or 0))
            price = max(0.0, float(item.get("price") or 0))
            core_charge = max(0.0, float(item.get("core_charge") or 0))
            items_total += qty * price
            cores_total += qty * core_charge

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

        # Live total/paid/balance — same as Parts Orders table
        order_total = _round2(items_total + cores_total + ni_total)
        paid = _round2(max(0.0, paid_map.get(order.get("_id"), 0.0)))
        balance = _round2(max(0.0, order_total - paid))
        bucket["total_amount"] = _round2(bucket["total_amount"] + order_total)
        bucket["paid_amount"] = _round2(bucket["paid_amount"] + paid)
        bucket["remaining_balance"] = _round2(bucket["remaining_balance"] + balance)

        # Chart time bucket — same date semantics as Parts Orders table
        tk = _time_bucket_key(_preferred_dt(order, "order_date", "created_at"), chart_bucket)
        if tk:
            tb = time_buckets.setdefault(tk, {"total": 0.0, "parts": 0.0, "non_inv": 0.0})
            tb["total"] = _round2(tb["total"] + order_total)
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


def _compute_payroll_totals(shop_db, shop_id, date_from: str, date_to: str) -> dict:
    """Compute labor payroll cost for the period (salary + uAttend hourly).

    Mirrors the logic of the standalone Timecard / Salary Report:
      • Internal users with pay_type=salary: weekly_amount × weeks_in_period.
      • uAttend employees marked `selected=True` with hourly_rate set:
            rate × hours_from_/reports/punch.
      • Matched pairs (AI-detected) are NOT double-counted: if a uAttend
        employee maps to an internal user, the uAttend hourly cost is
        skipped (salary takes precedence).

    Returns dict {
        "weeks_in_period": float,
        "salary_total": float,
        "hourly_total": float,
        "labor_total": float,
        "total_hours": float,
        "employees": [{name, pay_type, rate_or_salary, hours, total}],
        "integration_status": "none|disabled|configured",
        "available": bool,  # False if nothing usable was found
    }
    """
    out = {
        "weeks_in_period": 0.0,
        "salary_total": 0.0,
        "hourly_total": 0.0,
        "labor_total": 0.0,
        "total_hours": 0.0,
        "employees": [],
        "integration_status": "none",
        "available": False,
    }

    if not date_from or not date_to:
        return out

    try:
        from datetime import date as _date
        _df = _date.fromisoformat(date_from)
        _dt = _date.fromisoformat(date_to)
        if _dt < _df:
            return out
        days = (_dt - _df).days + 1
        weeks = round(days / 7.0, 4)
    except (TypeError, ValueError):
        return out

    out["weeks_in_period"] = weeks

    # ── Internal users (excluding owner) ───────────────────────────────
    master = get_master_db()
    tenant_id_raw = session.get(SESSION_TENANT_ID)
    tenant_values = []
    if tenant_id_raw:
        tenant_values.append(tenant_id_raw)
        try:
            tenant_values.append(ObjectId(str(tenant_id_raw)))
        except Exception:
            pass
        tenant_values.append(str(tenant_id_raw))

    internal_users = list(master.users.find(
        {
            "tenant_id": {"$in": tenant_values},
            "role": {"$ne": "owner"},
            "is_active": True,
        },
        {
            "first_name": 1, "last_name": 1, "name": 1, "email": 1,
            "role": 1, "pay_type": 1, "salary_amount": 1,
        },
    ))

    # ── uAttend integration ────────────────────────────────────────────
    from app.utils.integrations.storage import (
        get_integration, get_decrypted_api_key,
    )
    from app.utils.integrations.uattend_client import (
        UAttendClient, UAttendError,
    )

    emp_rows: list[dict] = []
    hours_by_uid: dict[int, float] = {}
    ai_match_map: dict[int, dict] = {}

    doc = get_integration(shop_db, shop_id, "uattend")
    if doc:
        if not doc.get("enabled"):
            out["integration_status"] = "disabled"
        else:
            out["integration_status"] = "configured"
            emp_rows = list(shop_db.uattend_employees.find(
                {
                    "shop_id": shop_id,
                    "is_active": True,
                    "selected": True,
                },
                {
                    "uattend_user_id": 1, "email": 1,
                    "first_name": 1, "last_name": 1,
                    "hourly_rate": 1,
                },
            ))
            try:
                api_key = get_decrypted_api_key(shop_db, shop_id, "uattend")
            except Exception:  # noqa: BLE001
                api_key = ""
            if api_key and emp_rows:
                try:
                    uids = [int(r.get("uattend_user_id")) for r in emp_rows]
                    report = UAttendClient(api_key).get_punches(
                        date_from, date_to, user_ids=uids,
                    )
                    hours_by_uid = _aggregate_uattend_hours(report)
                except (UAttendError, Exception):  # noqa: BLE001
                    hours_by_uid = {}

            # AI match to avoid double counting.
            try:
                from app.utils.employee_matcher import (
                    match_employees, cache_key,
                )
                internal_for_ai = [
                    {
                        "internal_id": str(u.get("_id")),
                        "name": (u.get("name") or "").strip() or (
                            f"{u.get('first_name', '') or ''} "
                            f"{u.get('last_name', '') or ''}".strip()
                        ),
                        "email": u.get("email") or "",
                    }
                    for u in internal_users
                ]
                uattend_for_ai = [
                    {
                        "uattend_user_id": r.get("uattend_user_id"),
                        "name": (
                            f"{r.get('first_name', '') or ''} "
                            f"{r.get('last_name', '') or ''}".strip()
                            or (r.get("email") or "")
                        ),
                        "email": r.get("email") or "",
                    }
                    for r in emp_rows
                ]
                ck = cache_key(internal_for_ai, uattend_for_ai)
                cached = shop_db.uattend_match_cache.find_one(
                    {"shop_id": shop_id, "key": ck}
                )
                if cached and isinstance(cached.get("matches"), dict):
                    ai_match_map = {
                        int(k): v for k, v in cached["matches"].items()
                        if isinstance(v, dict)
                    }
                else:
                    ai_match_map = match_employees(internal_for_ai, uattend_for_ai)
                    try:
                        shop_db.uattend_match_cache.update_one(
                            {"shop_id": shop_id, "key": ck},
                            {"$set": {
                                "shop_id": shop_id,
                                "key": ck,
                                "matches": {
                                    str(k): v for k, v in ai_match_map.items()
                                },
                            }},
                            upsert=True,
                        )
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                ai_match_map = {}

    matched_uattend_uids = {int(k) for k in ai_match_map.keys()}

    # ── Build per-employee totals ──────────────────────────────────────
    employees: list[dict] = []

    for u in internal_users:
        pay_type = (u.get("pay_type") or "salary").lower()
        if pay_type != "salary":
            continue
        try:
            weekly = float(u.get("salary_amount") or 0)
        except (TypeError, ValueError):
            weekly = 0.0
        if weekly <= 0:
            continue
        name = (u.get("name") or "").strip() or (
            f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()
        ) or "—"
        period_total = _round2(weekly * weeks)
        out["salary_total"] = _round2(out["salary_total"] + period_total)
        employees.append({
            "name": name,
            "pay_type": "salary",
            "rate_or_salary": weekly,
            "hours": None,
            "total": period_total,
        })

    for r in emp_rows:
        try:
            uid = int(r.get("uattend_user_id"))
        except (TypeError, ValueError):
            continue
        if uid in matched_uattend_uids:
            continue  # Already counted via the matched internal salary user.
        try:
            rate = float(r.get("hourly_rate")) if r.get("hourly_rate") is not None else None
        except (TypeError, ValueError):
            rate = None
        hours = hours_by_uid.get(uid)
        if not rate or not hours:
            continue
        name = (
            f"{r.get('first_name', '') or ''} {r.get('last_name', '') or ''}".strip()
            or (r.get("email") or "—")
        )
        line_total = _round2(rate * hours)
        out["hourly_total"] = _round2(out["hourly_total"] + line_total)
        out["total_hours"] = _round2(out["total_hours"] + hours)
        employees.append({
            "name": name,
            "pay_type": "hourly",
            "rate_or_salary": rate,
            "hours": _round2(hours),
            "total": line_total,
        })

    out["labor_total"] = _round2(out["salary_total"] + out["hourly_total"])
    out["employees"] = employees
    out["available"] = bool(employees)
    return out


def _report_general_revenue(shop_db, shop_id, date_ctx, include_customer_ids=None, exclude_customer_ids=None, chart_bucket="month"):
    # --- Sales (Work Orders) ---
    wo_query = {
        "shop_id": shop_id,
        "is_active": True,
    }
    wo_query = _append_and(wo_query, _build_date_filter(date_ctx, field="work_order_date", fallback_field="created_at"))

    if include_customer_ids:
        wo_query = _append_and(wo_query, {"customer_id": {"$in": include_customer_ids}})
    if exclude_customer_ids:
        wo_query = _append_and(wo_query, {"customer_id": {"$nin": exclude_customer_ids}})

    customer_filter_active = bool(include_customer_ids or exclude_customer_ids)

    wo_count = 0
    sales_labor = 0.0
    sales_parts_sale = 0.0
    sales_parts_cost = 0.0
    sales_core_charges = 0.0
    sales_misc = 0.0
    sales_tax = 0.0
    sales_revenue = 0.0
    invoiced_hours = 0.0
    mechanics: dict[str, dict] = {}
    time_buckets: dict[str, dict[str, float]] = {}
    rates_map = _build_labor_rates_map(shop_db, shop_id)

    for wo in shop_db.work_orders.find(wo_query, {
        "totals": 1, "labor_total": 1, "parts_total": 1,
        "sales_tax_total": 1, "grand_total": 1, "labors": 1,
        "created_at": 1, "work_order_date": 1,
    }):
        t = _wo_totals(wo)
        bk = _time_bucket_key(_preferred_dt(wo, "work_order_date", "created_at"), chart_bucket)
        # Pure base parts (qty*price) — fall back via stored parts_total minus cores+misc.
        parts_base = _round2(t["parts_pure"])
        core_total = _round2(t["core_total"])

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

        invoiced_hours += _wo_invoiced_hours(wo, rates_map)

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
    invoiced_hours = _round2(invoiced_hours)

    # Sort mechanics by hours desc
    mech_sorted = sorted(mechanics.values(), key=lambda m: m["hours"], reverse=True)
    total_mech_hours = _round2(sum(m["hours"] for m in mech_sorted))

    # --- Parts Orders ---
    po_query = {
        "shop_id": shop_id,
        "is_active": {"$ne": False},
    }
    po_query = _append_and(po_query, _build_date_filter(date_ctx, field="order_date", fallback_field="created_at"))

    # When a customer filter is active, Parts Orders cannot be attributed to
    # specific customers, so we skip them to keep the report consistent.
    po_orders = [] if customer_filter_active else list(shop_db.parts_orders.find(po_query, {
        "_id": 1,
        "items": 1,
        "non_inventory_amounts": 1,
    }))
    po_order_ids = [o.get("_id") for o in po_orders if o.get("_id")]
    po_paid_map = _build_parts_order_paid_map(shop_db, po_order_ids)

    po_count = 0
    po_items_total = 0.0  # qty*price (without cores)
    po_cores_total = 0.0  # qty*core_charge
    po_non_inventory = 0.0
    po_total = 0.0
    po_paid = 0.0
    po_balance = 0.0

    for order in po_orders:
        po_count += 1
        items_amount = 0.0
        cores_amount = 0.0
        for item in (order.get("items") or []):
            if not isinstance(item, dict):
                continue
            qty = max(0, int(item.get("quantity") or 0))
            price = max(0.0, float(item.get("price") or 0))
            core_charge = max(0.0, float(item.get("core_charge") or 0))
            items_amount += qty * price
            cores_amount += qty * core_charge

        ni_amount = 0.0
        for line in (order.get("non_inventory_amounts") or []):
            if isinstance(line, dict):
                ni_amount += max(0.0, float(line.get("amount") or 0))

        order_total = items_amount + cores_amount + ni_amount
        paid = max(0.0, po_paid_map.get(order.get("_id"), 0.0))
        balance = max(0.0, order_total - paid)

        po_items_total += items_amount
        po_cores_total += cores_amount
        po_non_inventory += ni_amount
        po_total += order_total
        po_paid += paid
        po_balance += balance

    po_items_total = _round2(po_items_total)
    po_cores_total = _round2(po_cores_total)
    po_non_inventory = _round2(po_non_inventory)
    po_total = _round2(po_total)
    po_paid = _round2(po_paid)
    po_balance = _round2(po_balance)
    po_parts_only = po_items_total

    # --- Combined ---
    net_revenue = _round2(sales_revenue - po_total)
    parts_profit = _round2(sales_parts_sale - sales_parts_cost)

    # --- Labor Payroll (only when no customer filter is applied) ─────────
    payroll = None
    if not customer_filter_active:
        try:
            payroll = _compute_payroll_totals(
                shop_db, shop_id,
                date_ctx.get("date_from") or "",
                date_ctx.get("date_to") or "",
            )
        except Exception as exc:  # noqa: BLE001
            current_app.logger.warning(
                "Payroll totals failed for General Revenue: %s", exc
            )
            payroll = None
    labor_cost = _round2((payroll or {}).get("labor_total") or 0)
    net_after_labor = _round2(net_revenue - labor_cost) if payroll and payroll.get("available") else net_revenue

    # --- Two headline Net Revenue figures ──────────────────────────────
    # 1) Parts (Cost) basis: All Revenue − Parts (Cost in WO) − All Salaries
    # 2) Parts Orders basis: All Revenue − Parts Orders (spent at vendors) − All Salaries
    # When no payroll is available (filter active / not configured), labor is treated as 0
    # so the two figures still make sense and never become misleading.
    net_revenue_parts_cost = _round2(sales_revenue - sales_parts_cost - labor_cost)
    net_revenue_parts_orders = _round2(sales_revenue - po_total - labor_cost)

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
            "category": "Parts Orders — Cores",
            "amount": po_cores_total,
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

    # --- Labor Payroll section (only when no customer filter is applied) -
    if payroll and payroll.get("available"):
        rows.append({"category": "", "amount": None})
        rows.append({
            "category": (
                f"Labor Payroll — Salary "
                f"(× {payroll['weeks_in_period']:g} weeks)"
            ),
            "amount": _round2(payroll["salary_total"]),
        })
        rows.append({
            "category": "Labor Payroll — Hourly (uAttend)",
            "amount": _round2(payroll["hourly_total"]),
        })
        rows.append({
            "category": "Labor Payroll — Total",
            "amount": _round2(payroll["labor_total"]),
        })
        for emp in payroll["employees"]:
            if emp["pay_type"] == "salary":
                label = (
                    f"  {emp['name']} — salary "
                    f"${emp['rate_or_salary']:,.2f}/wk"
                )
            else:
                label = (
                    f"  {emp['name']} — "
                    f"{emp['hours']:.2f}h × ${emp['rate_or_salary']:,.2f}/hr"
                )
            rows.append({"category": label, "amount": emp["total"]})
        rows.append({
            "category": "Net Revenue (after Labor)",
            "amount": net_after_labor,
        })

    # --- Headline Net Revenue figures (always shown) ---------------------
    rows.append({"category": "", "amount": None})
    rows.append({
        "category": "Net Revenue — Parts (Cost) basis  [Revenue − Parts Cost − Salaries]",
        "amount": net_revenue_parts_cost,
    })
    rows.append({
        "category": "Net Revenue — Parts Orders basis  [Revenue − Parts Orders − Salaries]",
        "amount": net_revenue_parts_orders,
    })

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
            "sales_labor": sales_labor,
            "parts_sale": sales_parts_sale,
            "parts_cost": sales_parts_cost,
            "parts_profit": parts_profit,
            "core_charges": sales_core_charges,
            "po_total_spent": po_total,
            "net_revenue": net_revenue,
            "wo_count": wo_count,
            "po_count": po_count,
            "invoiced_hours": invoiced_hours,
            "total_mech_hours": total_mech_hours,
            "labor_cost": labor_cost,
            "net_after_labor": net_after_labor,
            "net_revenue_parts_cost": net_revenue_parts_cost,
            "net_revenue_parts_orders": net_revenue_parts_orders,
            "payroll_weeks": (payroll or {}).get("weeks_in_period") if payroll else None,
            "customer_filter_active": customer_filter_active,
        },
        "rows": rows,
        "chart_data": chart_data,
    }


def _report_mechanic_hours(shop_db, shop_id, date_ctx, chart_bucket="month"):
    wo_query = {
        "shop_id": shop_id,
        "is_active": True,
    }
    wo_query = _append_and(wo_query, _build_date_filter(date_ctx, field="work_order_date", fallback_field="created_at"))

    mechanics: dict[str, dict] = {}
    time_buckets: dict[str, dict[str, float]] = {}

    for wo in shop_db.work_orders.find(wo_query, {"labors": 1, "created_at": 1, "work_order_date": 1}):
        wo_id = wo.get("_id")
        wo_ids_seen: dict[str, set] = {}
        wo_dt = _preferred_dt(wo, "work_order_date", "created_at")

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

    # Фактические часы из таймеров механиков (wo_time_logs) — рядом с billed.
    from app.blueprints.work_orders.services.time_tracking import summarize_mechanic_hours

    tracked = summarize_mechanic_hours(
        shop_db,
        shop_id,
        date_from=date_ctx.get("created_from"),
        date_to_exclusive=date_ctx.get("created_to_exclusive"),
    )
    for uid, info in tracked.items():
        bucket = mechanics.setdefault(uid, {
            "mechanic_id": uid,
            "mechanic_name": str(info.get("user_name") or "").strip() or "-",
            "total_hours": 0.0,
            "wo_count": 0,
            "labor_entries": 0,
        })
        bucket["tracked_hours"] = _round2((info.get("seconds") or 0) / 3600.0)
    for bucket in mechanics.values():
        bucket.setdefault("tracked_hours", 0.0)

    rows = sorted(mechanics.values(), key=lambda x: x.get("total_hours", 0), reverse=True)

    total_hours = _round2(sum(float(r.get("total_hours") or 0) for r in rows))
    total_tracked_hours = _round2(sum(float(r.get("tracked_hours") or 0) for r in rows))
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
            "total_tracked_hours": total_tracked_hours,
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
            include_customer_ids,
            exclude_customer_ids,
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
    response.headers.set("Content-Disposition", "attachment", filename=filename)
    response.headers["Content-Length"] = str(len(pdf_bytes))
    return response


@reports_bp.get("/timecard-salary")
@login_required
def timecard_salary_page():
    layout_nav = filter_nav_items(NAV_ITEMS)

    # ── Date range (default: this month) ─────────────────────────────────
    df = build_date_range_filters(request.args, default_preset="this_month")
    date_from = df["date_from"]  # YYYY-MM-DD or ""
    date_to = df["date_to"]
    debug_mode = request.args.get("debug") in ("1", "true", "yes")
    debug_payload = {"request": None, "response": None}

    # ── Load internal users (excluding owner role) ───────────────────────
    master = get_master_db()
    tenant_id_raw = session.get(SESSION_TENANT_ID)
    tenant_values = []
    if tenant_id_raw:
        tenant_values.append(tenant_id_raw)
        try:
            tenant_values.append(ObjectId(str(tenant_id_raw)))
        except Exception:
            pass
        tenant_values.append(str(tenant_id_raw))

    user_query = {
        "tenant_id": {"$in": tenant_values},
        "role": {"$ne": "owner"},
        "is_active": True,
    }
    users_cur = master.users.find(
        user_query,
        {
            "first_name": 1, "last_name": 1, "name": 1, "email": 1,
            "role": 1, "pay_type": 1, "salary_amount": 1,
        },
    ).sort([("last_name", 1), ("first_name", 1)])
    internal_users = list(users_cur)

    # ── Resolve current shop & uAttend integration ───────────────────────
    integration_status = "none"  # none | disabled | configured
    rates_by_email: dict[str, float] = {}
    hours_by_email: dict[str, float] = {}
    integration_error: str | None = None
    uattend_emp_rows: list[dict] = []
    hours_by_uid: dict[int, float] = {}
    # uattend_user_id -> {"internal_id", "internal_name", "internal_email", "confidence"}
    ai_match_map: dict[int, dict] = {}

    shop_oid = None
    try:
        shop_oid = ObjectId(str(session.get(SESSION_SHOP_ID)))
    except Exception:
        shop_oid = None

    if shop_oid is not None:
        shop = master.shops.find_one({"_id": shop_oid})
        db_name = (shop or {}).get("db_name") if shop else None
        if db_name:
            client = get_mongo_client()
            shop_db = client[str(db_name)]

            from app.utils.integrations.storage import (
                get_integration, get_decrypted_api_key,
            )
            from app.utils.integrations.uattend_client import (
                UAttendClient, UAttendError,
            )

            doc = get_integration(shop_db, shop_oid, "uattend")
            if doc:
                if not doc.get("enabled"):
                    integration_status = "disabled"
                else:
                    integration_status = "configured"
                    # Build rate / id maps from cached uattend_employees.
                    # Only employees that were explicitly selected (checkbox)
                    # in Settings → Integrations should appear in this report.
                    emp_rows = list(
                        shop_db.uattend_employees.find(
                            {
                                "shop_id": shop_oid,
                                "is_active": True,
                                "selected": True,
                            },
                            {
                                "uattend_user_id": 1, "email": 1,
                                "first_name": 1, "last_name": 1,
                                "hourly_rate": 1, "selected": 1,
                            },
                        )
                    )
                    uattend_emp_rows = emp_rows
                    uid_to_email: dict[int, str] = {}
                    for r in emp_rows:
                        email = (r.get("email") or "").strip().lower()
                        if not email:
                            continue
                        uid = r.get("uattend_user_id")
                        try:
                            uid_to_email[int(uid)] = email
                        except (TypeError, ValueError):
                            continue
                        rate = r.get("hourly_rate")
                        if rate is not None:
                            try:
                                rates_by_email[email] = float(rate)
                            except (TypeError, ValueError):
                                pass

                    # ── AI match: internal users ↔ uAttend employees ───
                    try:
                        from app.utils.employee_matcher import (
                            match_employees, cache_key,
                        )

                        internal_for_ai = []
                        for u in internal_users:
                            nm = (u.get("name") or "").strip() or (
                                f"{u.get('first_name', '') or ''} "
                                f"{u.get('last_name', '') or ''}".strip()
                            )
                            internal_for_ai.append({
                                "internal_id": str(u.get("_id")),
                                "name": nm,
                                "email": u.get("email") or "",
                            })
                        uattend_for_ai = []
                        for r in emp_rows:
                            nm = (
                                f"{r.get('first_name', '') or ''} "
                                f"{r.get('last_name', '') or ''}".strip()
                                or (r.get("email") or "")
                            )
                            uattend_for_ai.append({
                                "uattend_user_id": r.get("uattend_user_id"),
                                "name": nm,
                                "email": r.get("email") or "",
                            })

                        ck = cache_key(internal_for_ai, uattend_for_ai)
                        cached = shop_db.uattend_match_cache.find_one(
                            {"shop_id": shop_oid, "key": ck}
                        )
                        if cached and isinstance(cached.get("matches"), dict):
                            ai_match_map = {
                                int(k): v for k, v in cached["matches"].items()
                                if isinstance(v, dict)
                            }
                        else:
                            ai_match_map = match_employees(
                                internal_for_ai, uattend_for_ai
                            )
                            try:
                                shop_db.uattend_match_cache.update_one(
                                    {"shop_id": shop_oid, "key": ck},
                                    {"$set": {
                                        "shop_id": shop_oid,
                                        "key": ck,
                                        "matches": {
                                            str(k): v
                                            for k, v in ai_match_map.items()
                                        },
                                    }},
                                    upsert=True,
                                )
                            except Exception:  # noqa: BLE001
                                pass
                    except Exception as exc:  # noqa: BLE001
                        current_app.logger.warning(
                            "Employee AI match failed: %s", exc
                        )
                        ai_match_map = {}

                    # Pull punches for the date range (if dates are set).
                    if date_from and date_to:
                        try:
                            api_key = get_decrypted_api_key(shop_db, shop_oid, "uattend")
                        except Exception as exc:  # noqa: BLE001
                            api_key = ""
                            integration_error = f"Could not decrypt API key: {exc}"

                        if api_key:
                            try:
                                client_u = UAttendClient(api_key)
                                selected_uids = []
                                for r in emp_rows:
                                    try:
                                        selected_uids.append(int(r.get("uattend_user_id")))
                                    except (TypeError, ValueError):
                                        continue
                                report = client_u.get_punches(
                                    date_from, date_to,
                                    user_ids=selected_uids or None,
                                )
                                if debug_mode:
                                    debug_payload["request"] = {
                                        "StartDate": date_from,
                                        "EndDate": date_to,
                                        "UserIds": selected_uids,
                                    }
                                    debug_payload["response"] = report
                            except UAttendError as exc:
                                integration_error = str(exc)
                                report = None

                            if report is not None:
                                hours_by_uid = _aggregate_uattend_hours(report)
                                if not hours_by_uid:
                                    try:
                                        import json as _json
                                        current_app.logger.warning(
                                            "uAttend /reports/punch returned no hours. "
                                            "Raw shape sample: %s",
                                            _json.dumps(report)[:2000],
                                        )
                                    except Exception:
                                        pass
                                for uid, hrs in hours_by_uid.items():
                                    em = uid_to_email.get(uid)
                                    if em:
                                        hours_by_email[em] = hours_by_email.get(em, 0.0) + hrs

    # ── Build rows ───────────────────────────────────────────────────────
    rows = []
    totals = {"salary": 0.0, "hourly": 0.0, "hours": 0.0, "grand": 0.0}

    # Compute how many weeks the selected period covers — salaries are
    # stored as a WEEKLY amount and the report total scales with the period.
    weeks_in_period: float = 0.0
    if date_from and date_to:
        try:
            from datetime import date as _date
            _df = _date.fromisoformat(date_from)
            _dt = _date.fromisoformat(date_to)
            if _dt >= _df:
                days = (_dt - _df).days + 1  # inclusive
                weeks_in_period = round(days / 7.0, 4)
        except (TypeError, ValueError):
            weeks_in_period = 0.0
    if weeks_in_period <= 0:
        # Fallback so salary still shows something (one week).
        weeks_in_period = 1.0

    # Reverse map: internal_id -> uattend full name (for badges on salary rows)
    matched_by_internal_id: dict[str, str] = {}
    uid_to_uattend_name: dict[int, str] = {}
    for emp in uattend_emp_rows:
        try:
            _uid = int(emp.get("uattend_user_id"))
        except (TypeError, ValueError):
            continue
        uid_to_uattend_name[_uid] = (
            f"{emp.get('first_name', '') or ''} {emp.get('last_name', '') or ''}".strip()
            or (emp.get('email') or '')
        )
    # internal_id -> {uattend_user_id, name, hours, rate}
    matched_uattend_by_internal_id: dict[str, dict] = {}
    matched_uattend_uids: set[int] = set()
    for _uid, m in ai_match_map.items():
        iid = (m or {}).get("internal_id")
        if not iid:
            continue
        matched_uattend_uids.add(int(_uid))
        matched_by_internal_id[str(iid)] = uid_to_uattend_name.get(int(_uid), "")
        emp = next(
            (
                e for e in uattend_emp_rows
                if str(e.get("uattend_user_id")) == str(_uid)
            ),
            None,
        )
        if emp is not None:
            try:
                _rate = float(emp.get("hourly_rate")) if emp.get("hourly_rate") is not None else None
            except (TypeError, ValueError):
                _rate = None
            matched_uattend_by_internal_id[str(iid)] = {
                "uattend_user_id": int(_uid),
                "uattend_name": uid_to_uattend_name.get(int(_uid), ""),
                "hours": hours_by_uid.get(int(_uid)),
                "rate": _rate,
            }

    for u in internal_users:
        pay_type = (u.get("pay_type") or "salary").lower()
        # At this stage we do not try to match hourly internal users to
        # uAttend employees — uAttend employees are listed separately below.
        if pay_type != "salary":
            continue
        full_name = (u.get("name") or "").strip() or (
            f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()
        )
        try:
            weekly = float(u.get("salary_amount") or 0)
        except (TypeError, ValueError):
            weekly = 0.0
        period_total = round(weekly * weeks_in_period, 2)
        merged = matched_uattend_by_internal_id.get(str(u.get("_id")))
        row = {
            "name": full_name or "—",
            "email": u.get("email") or "",
            "role": u.get("role") or "",
            "pay_type": "salary",
            "salary_amount": weekly,
            "salary_weeks": weeks_in_period,
            "hourly_rate": (merged or {}).get("rate"),
            "hours": (merged or {}).get("hours"),
            "total": period_total,
            "note": (
                f"${weekly:,.2f}/wk × {weeks_in_period:g} weeks"
                if weekly else ""
            ),
            "matched_uattend_name": (merged or {}).get("uattend_name") or "",
        }
        totals["salary"] += period_total
        totals["grand"] += period_total
        if row["hours"]:
            totals["hours"] += row["hours"]
        rows.append(row)

    # ── Append rows for uAttend employees from the integration ──────────
    # Show every active uAttend employee as a separate hourly row, except
    # those already merged into an internal salary user above.
    for emp in uattend_emp_rows:
        uid_raw = emp.get("uattend_user_id")
        try:
            uid = int(uid_raw) if uid_raw is not None else None
        except (TypeError, ValueError):
            uid = None
        if uid is not None and uid in matched_uattend_uids:
            continue
        full_name = (
            f"{emp.get('first_name', '') or ''} {emp.get('last_name', '') or ''}".strip()
            or (emp.get('email') or '—')
        )
        rate = emp.get("hourly_rate")
        try:
            rate = float(rate) if rate is not None else None
        except (TypeError, ValueError):
            rate = None
        hours = hours_by_uid.get(uid) if uid is not None else None
        matched = ai_match_map.get(uid) if uid is not None else None
        row = {
            "name": full_name,
            "email": emp.get("email") or "",
            "role": "uAttend employee",
            "pay_type": "hourly",
            "salary_amount": None,
            "hourly_rate": rate,
            "hours": hours,
            "total": None,
            "note": "",
            "matched_internal_name": (matched or {}).get("internal_name") or "",
            "matched_internal_email": (matched or {}).get("internal_email") or "",
            "matched_confidence": (matched or {}).get("confidence") or 0,
        }
        if rate is None and hours is None:
            row["note"] = "Hourly rate not set · no punches in this period."
        elif rate is None:
            row["note"] = "Hourly rate not set in Integrations."
        elif hours is None:
            row["note"] = "No punches in this period."
        if rate is not None and hours is not None:
            row["total"] = round(rate * hours, 2)
            totals["hourly"] += row["total"]
            totals["hours"] += hours
            totals["grand"] += row["total"]
        rows.append(row)

    return render_internal_page(
        "public/reports/timecard_salary.html",
        layout_nav,
        "reports",
        rows=rows,
        totals=totals,
        date_from=date_from,
        date_to=date_to,
        date_preset=df["date_preset"],
        integration_status=integration_status,
        integration_error=integration_error,
        debug_mode=debug_mode,
        debug_payload=debug_payload,
    )


def _aggregate_uattend_hours(report_response) -> dict[int, float]:
    """Walk a /reports/punch response and sum hours per UserId.

    Tolerates several shapes:
      • {"Punches": [{UserId, RegularHours/RegularTime, ...}]}
      • {"Users":   [{UserId, TotalRegularHours/RegularHours/RegularTime, ...}]}
      • Nested structures with PunchPairs containing RegularHours/RegularTime
      • Records with PunchIn/PunchOut ISO timestamps (delta computed)
    Falls back to a defensive recursive walk if the structure is unknown.
    """
    from datetime import datetime as _dt

    def hhmm(v):
        if v is None:
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip()
        if not s:
            return 0.0
        if ":" in s:
            try:
                parts = s.split(":")
                h = int(parts[0])
                m = int(parts[1]) if len(parts) > 1 else 0
                return h + (m / 60.0)
            except (TypeError, ValueError):
                return 0.0
        try:
            return float(s)
        except (TypeError, ValueError):
            return 0.0

    def parse_dt(v):
        if not v:
            return None
        try:
            s = str(v).replace("Z", "+00:00")
            return _dt.fromisoformat(s)
        except (TypeError, ValueError):
            return None

    def add(totals, uid_raw, hours):
        try:
            uid = int(uid_raw)
        except (TypeError, ValueError):
            return
        if hours and hours > 0:
            totals[uid] = totals.get(uid, 0.0) + hours

    def hours_from_node(node) -> float:
        # Prefer explicit aggregate fields, then time-range delta.
        for key in (
            "TotalRegularHours", "RegularHours", "RegularTime",
            "TotalHours", "Hours",
        ):
            if key in node and node.get(key) not in (None, ""):
                h = hhmm(node.get(key))
                if h > 0:
                    return h
        pi = parse_dt(node.get("PunchIn"))
        po = parse_dt(node.get("PunchOut"))
        if pi and po and po > pi:
            return (po - pi).total_seconds() / 3600.0
        return 0.0

    totals: dict[int, float] = {}

    # ── Shape 0: uAttend native — Body.PunchReportLineItems[] with Tot ──
    if isinstance(report_response, dict):
        body = report_response.get("Body")
        if isinstance(body, dict):
            items = body.get("PunchReportLineItems")
            if isinstance(items, list) and items:
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    h = hhmm(it.get("Tot"))
                    if h <= 0:
                        h = hours_from_node(it)
                    add(totals, it.get("UserId"), h)
                if totals:
                    return totals

    # ── Shape 1: top-level "Users" array with per-user totals ───────────
    if isinstance(report_response, dict):
        users = report_response.get("Users")
        if isinstance(users, list) and users and all(isinstance(u, dict) for u in users):
            captured = False
            for u in users:
                uid = u.get("UserId") or u.get("Id")
                if uid is None:
                    continue
                h = hours_from_node(u)
                if h <= 0:
                    # Sum nested punch pairs for this user.
                    for wd in u.get("Workdays") or []:
                        for pp in (wd.get("PunchPairs") if isinstance(wd, dict) else None) or []:
                            h += hours_from_node(pp) if isinstance(pp, dict) else 0.0
                if h > 0:
                    add(totals, uid, h)
                    captured = True
            if captured:
                return totals

        # ── Shape 2: flat "Punches" array ───────────────────────────────
        punches = report_response.get("Punches")
        if isinstance(punches, list) and punches:
            for p in punches:
                if isinstance(p, dict):
                    add(totals, p.get("UserId"), hours_from_node(p))
            if totals:
                return totals

    # ── Fallback: recursive walk ────────────────────────────────────────
    seen: set[int] = set()

    def walk(node, current_uid=None):
        if isinstance(node, dict):
            uid = node.get("UserId") or node.get("Id")
            if uid is not None:
                current_uid = uid
            h = hours_from_node(node)
            if h > 0 and id(node) not in seen:
                seen.add(id(node))
                add(totals, current_uid, h)
            for v in node.values():
                if isinstance(v, (list, dict)):
                    walk(v, current_uid)
        elif isinstance(node, list):
            for item in node:
                walk(item, current_uid)

    walk(report_response)
    return totals


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
