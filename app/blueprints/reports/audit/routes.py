from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bson import ObjectId
from flask import current_app, has_request_context, jsonify, make_response, render_template, request, session

from app.blueprints.main.routes import NAV_ITEMS
from app.blueprints.reports import reports_bp
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import SESSION_TENANT_ID, login_required
from app.utils.date_filters import build_date_range_filters
from app.utils.layout import render_internal_page
from app.utils.pagination import get_sort_params
from app.utils.pdf_utils import render_chart_to_base64, render_html_to_pdf
from app.utils.permissions import filter_nav_items, has_permission, permission_required
from app.utils.user_names import display_name_for, load_user_names, tenant_values_for_shop


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

# Отчёты без денег — доступны по reports.view; всё остальное (выручка,
# платежи, балансы, закупки) — reports.view_financial.
NON_FINANCIAL_REPORT_TABS = {
    "mechanic_hours",
}


def _report_permission_for(report_key: str) -> str:
    key = str(report_key or "").strip().lower()
    return "reports.view" if key in NON_FINANCIAL_REPORT_TABS else "reports.view_financial"


def _report_forbidden(report_key: str):
    required = _report_permission_for(report_key)
    if has_permission(required):
        return None
    return required

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
        # Заказы из почты до подтверждения человеком — ещё не долг вендору.
        "needs_confirmation": {"$ne": True},
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


def _ascii_text(value, limit: int = 200) -> str:
    """Чужой текст (ошибка uAttend) для payroll_warning: только ASCII — его
    печатает PDF-движок (cp1252) — и не длиннее limit (сетевые ошибки
    тащат за собой repr объектов)."""
    text = str(value or "").replace("—", "-").replace("–", "-")
    text = " ".join(text.encode("ascii", "replace").decode("ascii").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _payroll_tenant_values(shop: dict | None = None) -> list:
    """Тенант для payroll: из документа магазина (он уже проверен на
    принадлежность тенанту сессии и доступен вне запроса), иначе — сессии."""
    values = tenant_values_for_shop(shop)
    if values:
        return values
    return _tenant_variants() if has_request_context() else []


def _compute_payroll_totals(shop_db, shop_id, date_from: str, date_to: str, tenant_values=None) -> dict:
    """Payroll периода для General Revenue — те же цифры, что в Timecard / Salary.

    Тонкая обёртка над ``reports.services.payroll.compute_payroll``: расчёт
    один на оба отчёта, поэтому суммы не расходятся. Здесь только
    раскладка под строки General Revenue:
      • salary — недельная ставка × недель в периоде (строки с $0 не нужны);
      • hourly — часы uAttend × ставка; сотрудник с часами, но без ставки,
        попадает строкой $0 с пояснением, чтобы пропажа денег была видна;
      • без корректного периода (All Time, полуоткрытый custom) payroll не
        считается — недельную ставку не на что умножать.

    Returns dict {
        "weeks_in_period": float,
        "salary_total": float,
        "hourly_total": float,
        "labor_total": float,
        "total_hours": float,
        "employees": [{name, pay_type, rate_or_salary, hours, total, note}],
        "integration_status": "none|disabled|configured",
        "uattend_error": str | None,  # часы uAttend не получены
        "has_period": bool,
        "available": bool,  # False if nothing usable was found
    }
    """
    from app.blueprints.reports.services.payroll import compute_payroll, weeks_in_period

    out = {
        "weeks_in_period": 0.0,
        "salary_total": 0.0,
        "hourly_total": 0.0,
        "labor_total": 0.0,
        "total_hours": 0.0,
        "employees": [],
        "integration_status": "none",
        "uattend_error": None,
        "has_period": False,
        "available": False,
    }

    if weeks_in_period(date_from, date_to) <= 0:
        return out

    payroll = compute_payroll(
        get_master_db(),
        shop_db,
        shop_id,
        tenant_values if tenant_values is not None else _payroll_tenant_values(),
        date_from,
        date_to,
    )

    out["has_period"] = True
    out["weeks_in_period"] = payroll["weeks_in_period"]
    out["integration_status"] = payroll["integration_status"]
    out["uattend_error"] = payroll["integration_error"]

    # NOTE: ASCII-only notes — они печатаются в PDF (xhtml2pdf/cp1252).
    employees: list[dict] = []
    hourly_hours = 0.0
    for row in payroll["rows"]:
        if row["pay_type"] == "salary":
            if not row["total"] or row["total"] <= 0:
                continue
            note = ""
            if row.get("ignored_hourly_rate"):
                note = (
                    f"Also has a uAttend rate of ${row['ignored_hourly_rate']:,.2f}/hr, but Pay type "
                    "is Salary - paid the weekly salary, not by the hour. To pay by punches, "
                    "set Pay type to Hourly in Settings - Users."
                )
            employees.append({
                "name": row["name"],
                "pay_type": "salary",
                "rate_or_salary": row["salary_amount"],
                "hours": None,
                "total": row["total"],
                "note": note,
            })
            continue

        hours = row.get("hours")
        if row.get("total") is not None and row["total"] > 0:
            hourly_hours += hours or 0.0
            employees.append({
                "name": row["name"],
                "pay_type": "hourly",
                "rate_or_salary": row["hourly_rate"],
                "hours": _round2(hours),
                "total": row["total"],
                "note": "",
            })
        elif hours and row.get("hourly_rate") is None:
            employees.append({
                "name": row["name"],
                "pay_type": "hourly",
                "rate_or_salary": None,
                "hours": _round2(hours),
                "total": 0.0,
                "note": (
                    "Clocked hours in uAttend, but no hourly rate is set in "
                    "Settings - Integrations, so this pay is counted as $0."
                ),
            })

    out["salary_total"] = _round2(payroll["totals"]["salary"])
    out["hourly_total"] = _round2(payroll["totals"]["hourly"])
    out["labor_total"] = _round2(payroll["totals"]["grand"])
    out["total_hours"] = _round2(hourly_hours)
    out["employees"] = employees
    out["available"] = bool(employees)
    return out


def _apply_live_mechanic_names(buckets: dict, name_key: str, shop: dict | None = None) -> None:
    """
    Подписи строк «по механикам» (ключ buckets — user_id): живое имя из
    master.users одним запросом, включая неактивных (« (inactive)»), — имя в
    назначениях WO / тайм-логах / кэше uAttend лишь снимок на момент записи.
    Снимок — запас для удалённого пользователя, иначе «Former user». Ключи
    uAttend-сотрудников без аккаунта («uattend:<uid>») — их имя из uAttend
    (пусто — «-»). Какие часы считаются, не меняется.
    """
    people = load_user_names(list(buckets), tenant_values=tenant_values_for_shop(shop))
    for key, bucket in buckets.items():
        if str(key).startswith("uattend:"):
            bucket[name_key] = str(bucket.get(name_key) or "").strip() or "-"
            continue
        bucket[name_key] = display_name_for(people, key, bucket.get(name_key), mark_inactive=True)


def _report_general_revenue(shop_db, shop_id, date_ctx, include_customer_ids=None, exclude_customer_ids=None, chart_bucket="month", shop=None, show_employee_pay=True):
    # ``shop`` — документ активного магазина: по нему определяется тенант
    # для payroll (без него — тенант сессии).
    # ``show_employee_pay`` — печатать ли строки по сотрудникам (имя, ставка,
    # часы). Это данные Timecard / Salary: роут передаёт сюда право
    # reports.view_payroll; итоги payroll видны всем с reports.view_financial.
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
                    "name": str(mech.get("name") or "").strip(),
                    "hours": 0.0,
                })
                if not bucket["name"]:
                    bucket["name"] = str(mech.get("name") or "").strip()
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

    # Имена — живые по user_id (снимок из WO устаревает после переименования).
    _apply_live_mechanic_names(mechanics, "name")

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
    # Те же цифры, что в Timecard / Salary (общий сервис payroll).
    # payroll_warning — почему payroll неполный или не посчитан: показывается
    # баннером (веб), строкой (PDF), карточкой (мобильное приложение) и в
    # пояснениях секций. Только ASCII — текст попадает в PDF.
    payroll = None
    payroll_warning = ""
    if not customer_filter_active:
        try:
            payroll = _compute_payroll_totals(
                shop_db, shop_id,
                date_ctx.get("date_from") or "",
                date_ctx.get("date_to") or "",
                tenant_values=_payroll_tenant_values(shop),
            )
        except Exception:  # noqa: BLE001 — отчёт о выручке важнее payroll
            current_app.logger.exception("Payroll totals failed for General Revenue")
            payroll = None
            payroll_warning = (
                "Payroll could not be calculated because of an internal error, "
                "so it is counted as $0 here. Try again later."
            )
        if payroll is not None:
            if not payroll.get("has_period"):
                payroll_warning = (
                    "Payroll is counted per period (weekly salaries x weeks + uAttend hours) "
                    "and needs both a start and an end date. All Time or an open-ended "
                    "Custom range has none, so payroll is counted as $0 - pick a preset "
                    "or fill in both dates to include it."
                )
            elif payroll.get("uattend_error"):
                payroll_warning = (
                    f"uAttend hours could not be loaded ({_ascii_text(payroll['uattend_error'])}). "
                    "Hourly pay is NOT included in payroll here - "
                    + ("only salaries are counted. " if payroll.get("available") else "payroll is counted as $0. ")
                    + "Open Reports - Timecard / Salary to check the uAttend connection."
                )
    labor_cost = _round2((payroll or {}).get("labor_total") or 0)
    net_after_labor = _round2(net_revenue - labor_cost) if payroll and payroll.get("available") else net_revenue

    # --- Two headline Net Revenue figures ──────────────────────────────
    # 1) Parts (Cost) basis: All Revenue − Parts (Cost in WO) − All Salaries
    # 2) Parts Orders basis: All Revenue − Parts Orders (spent at vendors) − All Salaries
    # When no payroll is available (filter active / not configured), labor is treated as 0
    # so the two figures still make sense and never become misleading.
    net_revenue_parts_cost = _round2(sales_revenue - sales_parts_cost - labor_cost)
    net_revenue_parts_orders = _round2(sales_revenue - po_total - labor_cost)
    # Гросс-маржа по заказам: выручка минус себестоимость запчастей, БЕЗ зарплат
    # (левый хиро-блок в summary).
    revenue_minus_parts_cost = _round2(sales_revenue - sales_parts_cost)

    # --- Rows: plain-language sections so a non-accountant can follow ---
    # Each row carries `desc` (what the number means), `kind` (in/out/info/
    # result — drives +/- signs and colors in web/PDF) and `indent` for
    # reference sub-lines. `row_type: section` rows are group headers.
    # NOTE: ASCII "-" in labels/descs — the PDF engine (xhtml2pdf/cp1252)
    # cannot render U+2212.
    def _section(title, desc):
        return {"row_type": "section", "category": title, "amount": None, "desc": desc}

    def _row(category, amount, kind, desc, *, indent=False, emphasis=False, is_hours=False):
        row = {"category": category, "amount": amount, "kind": kind, "desc": desc}
        if indent:
            row["indent"] = True
        if emphasis:
            row["emphasis"] = True
        if is_hours:
            row["is_hours"] = True
        return row

    payroll_ok = bool(payroll and payroll.get("available"))

    rows = [
        _section(
            "Money In — Sales (Work Orders)",
            "Everything billed to customers on Work Orders in this period.",
        ),
        _row("Labor billed", sales_labor, "in",
             "Mechanics' work charged to customers."),
        _row("Parts billed (sale price)", sales_parts_sale, "in",
             "Parts charged to customers, at the selling price."),
        _row("Parts cost (reference)", sales_parts_cost, "info",
             "What those same parts cost the shop. For reference only - not added to revenue.",
             indent=True),
        _row("Parts profit (sale - cost)", parts_profit, "info",
             "Markup earned on parts: sale price minus shop cost.",
             indent=True),
        _row("Core charges billed", sales_core_charges, "in",
             "Refundable deposits charged for old replaced parts (cores)."),
        _row("Misc charges billed", sales_misc, "in",
             "Other Work Order charges: shop supplies, fees, etc."),
        _row("Sales tax collected", sales_tax, "in",
             "Tax collected from customers (passed on to the state)."),
        _row("Total Revenue", sales_revenue, "result",
             "Labor + parts + cores + misc + tax = everything billed in this period.",
             emphasis=True),
        _section(
            "Money Out — Parts Orders (vendor purchases)",
            "Parts and supplies the shop bought from vendors in this period - real money spent.",
        ),
        _row("Parts bought", po_parts_only, "out",
             "Parts purchased from vendors (quantity x price)."),
        _row("Cores charged by vendors", po_cores_total, "out",
             "Core deposits vendors charged the shop."),
        _row("Non-inventory purchases", po_non_inventory, "out",
             "Shop supplies, tools, utilities and other non-part spending on parts orders."),
        _row("Total spent at vendors", po_total, "result",
             "Parts + cores + non-inventory = the whole vendor bill for the period.",
             emphasis=True),
        _row("...of it already paid", po_paid, "info",
             "Part of the vendor bill that has been paid.", indent=True),
        _row("...still owed to vendors", po_balance, "info",
             "Part of the vendor bill not paid yet.", indent=True),
    ]

    # --- Labor Payroll section (only when no customer filter is applied) -
    if payroll_ok:
        rows.append(_section(
            "Money Out — Payroll",
            (f"What employees were paid for this period: weekly salaries x "
             f"{payroll['weeks_in_period']:g} weeks + hourly pay from the uAttend time clock. "
             "Same numbers as Reports - Timecard / Salary."
             + ("" if show_employee_pay else
                " The per-employee breakdown is shown to users with the "
                "'View Timecard / Salary report' permission.")
             + (f" WARNING: {payroll_warning}" if payroll_warning else "")),
        ))
        rows.append(_row(
            f"Salaries (x {payroll['weeks_in_period']:g} weeks)",
            _round2(payroll["salary_total"]), "out",
            "Employees on a fixed weekly salary.",
        ))
        rows.append(_row(
            "Hourly (uAttend)", _round2(payroll["hourly_total"]), "out",
            "Hourly employees: clocked hours x their rate.",
        ))
        rows.append(_row(
            "Total payroll", _round2(payroll["labor_total"]), "result",
            "Salaries + hourly pay for the period.", emphasis=True,
        ))
        for emp in (payroll["employees"] if show_employee_pay else []):
            if emp["pay_type"] == "salary":
                label = (
                    f"{emp['name']} — salary "
                    f"${emp['rate_or_salary']:,.2f}/wk"
                )
            elif emp["rate_or_salary"] is None:
                label = f"{emp['name']} — {emp['hours']:.2f}h, hourly rate not set"
            else:
                label = (
                    f"{emp['name']} — "
                    f"{emp['hours']:.2f}h x ${emp['rate_or_salary']:,.2f}/hr"
                )
            rows.append(_row(label, emp["total"], "info", emp.get("note") or "", indent=True))

    # --- Bottom line: the same numbers as above, laid out as arithmetic --
    # Причина — конкретная (payroll_warning), если известна; при посчитанном
    # payroll предупреждение уже стоит в описании секции Payroll выше.
    payroll_note = (
        "" if payroll_ok
        else " " + (payroll_warning or "Payroll is unavailable here, so it is counted as $0.")
    )
    rows.append(_section(
        "Bottom Line — Cash view (Parts Orders basis)",
        "What's left if parts are counted as real money spent at vendors this period."
        + payroll_note,
    ))
    rows.append(_row("Total Revenue", sales_revenue, "in",
                     "Everything billed (from the Money In section above)."))
    rows.append(_row("- Parts Orders (total spent)", po_total, "out",
                     "Everything spent at vendors (from the Parts Orders section above)."))
    rows.append(_row("= Left after vendor purchases", net_revenue, "result",
                     "Revenue minus vendor spending, before payroll."))
    if payroll_ok:
        rows.append(_row("- Payroll (total)", labor_cost, "out",
                         "Total payroll from the Payroll section above."))
    rows.append(_row(
        "= NET REVENUE — Cash view", net_revenue_parts_orders, "result",
        "What the shop actually kept: Revenue - Parts Orders - Payroll.",
        emphasis=True,
    ))

    rows.append(_section(
        "Bottom Line — Job view (Parts Cost basis)",
        "What's left if parts are counted at the shop's cost of only those parts "
        "actually installed on Work Orders." + payroll_note,
    ))
    rows.append(_row("Total Revenue", sales_revenue, "in",
                     "Everything billed (same number as above)."))
    rows.append(_row("- Parts cost on Work Orders", sales_parts_cost, "out",
                     "Shop's cost of the parts that went onto Work Orders."))
    rows.append(_row("= Left after parts cost", revenue_minus_parts_cost, "result",
                     "Revenue minus parts cost, before payroll."))
    if payroll_ok:
        rows.append(_row("- Payroll (total)", labor_cost, "out",
                         "Total payroll from the Payroll section above."))
    rows.append(_row(
        "= NET REVENUE — Job view", net_revenue_parts_cost, "result",
        "Profit on the work performed: Revenue - Parts Cost - Payroll.",
        emphasis=True,
    ))

    # --- Mechanic Hours section ---
    rows.append(_section(
        "Mechanic Hours",
        "Billed labor hours from Work Orders, split by mechanic. Hours, not dollars.",
    ))
    rows.append(_row("All mechanics — total", total_mech_hours, "result",
                     "Sum of billed hours across all mechanics for the period.",
                     emphasis=True, is_hours=True))
    for m in mech_sorted:
        rows.append(_row(m["name"], m["hours"], "info", "", indent=True, is_hours=True))

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

    summary = {}
    if payroll_warning:
        # Ключ есть только при проблеме: мобильное приложение рисует карточку
        # на каждый скалярный ключ summary.
        summary["payroll_warning"] = payroll_warning

    return {
        "title": "General Revenue Report",
        "summary": {
            **summary,
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
            "revenue_minus_parts_cost": revenue_minus_parts_cost,
            "payroll_weeks": (payroll or {}).get("weeks_in_period") if payroll else None,
            "customer_filter_active": customer_filter_active,
        },
        "rows": rows,
        "chart_data": chart_data,
    }


def _report_mechanic_hours(shop_db, shop, date_ctx, chart_bucket="month"):
    shop_id = shop["_id"]
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

                # Имя — снимок из WO; живое подставляется в конце по user_id.
                bucket = mechanics.setdefault(uid, {
                    "mechanic_id": uid,
                    "mechanic_name": str(mech.get("name") or "").strip(),
                    "total_hours": 0.0,
                    "wo_count": 0,
                    "labor_entries": 0,
                })
                if not bucket["mechanic_name"]:
                    bucket["mechanic_name"] = str(mech.get("name") or "").strip()

                bucket["total_hours"] = _round2(bucket["total_hours"] + allocated)
                bucket["labor_entries"] += 1

                wo_set = wo_ids_seen.setdefault(uid, set())
                if wo_id and wo_id not in wo_set:
                    wo_set.add(wo_id)
                    bucket["wo_count"] += 1

                # Chart time bucket per mechanic — по id, не по имени: тёзки не
                # сливаются в одну линию, переименование не рвёт линию надвое.
                tk = _time_bucket_key(wo_dt, chart_bucket)
                if tk:
                    tb = time_buckets.setdefault(tk, {})
                    tb[uid] = _round2(tb.get(uid, 0) + allocated)

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
            "mechanic_name": str(info.get("user_name") or "").strip(),
            "total_hours": 0.0,
            "wo_count": 0,
            "labor_entries": 0,
        })
        if not bucket["mechanic_name"]:
            bucket["mechanic_name"] = str(info.get("user_name") or "").strip()
        bucket["tracked_hours"] = _round2((info.get("seconds") or 0) / 3600.0)
    for bucket in mechanics.values():
        bucket.setdefault("tracked_hours", 0.0)

    # uAttend-часы за тот же период — как на дашборде: сотрудники uAttend,
    # заматченные на не-механиков (менеджеры, владелец), исключаются;
    # несматченные остаются отдельными строками — это механики без аккаунта.
    uattend_connected = False
    total_uattend_hours = 0.0
    date_from_iso = str(date_ctx.get("date_from") or "")
    date_to_iso = str(date_ctx.get("date_to") or "")
    if date_from_iso and date_to_iso:
        from app.blueprints.work_orders.services.lookups import get_assignable_mechanics
        from app.utils.integrations.uattend_hours import load_uattend_period_hours
        from app.utils.integrations.uattend_match import current_match_map
        from app.utils.tenant import tenant_id_variants

        mechanic_ids = {m["id"] for m in get_assignable_mechanics(shop)}
        match_map = current_match_map(shop_db, shop_id, get_master_db(), tenant_id_variants())
        non_mechanic_uids = {
            uatt_uid
            for uatt_uid, match in match_map.items()
            if str(match.get("internal_id") or "")
            and str(match["internal_id"]) not in mechanic_ids
        }
        uattend = load_uattend_period_hours(
            shop_db, shop_id, date_from_iso, date_to_iso, exclude_uids=non_mechanic_uids,
        )
        uattend_connected = bool(uattend.get("connected"))
        if uattend_connected and uattend.get("by_uid"):
            emp_names = {}
            for emp in shop_db.uattend_employees.find(
                {"shop_id": shop_id, "is_active": True},
                {"uattend_user_id": 1, "first_name": 1, "last_name": 1, "email": 1},
            ):
                try:
                    emp_uid = int(emp.get("uattend_user_id"))
                except (TypeError, ValueError):
                    continue
                emp_name = f"{emp.get('first_name') or ''} {emp.get('last_name') or ''}".strip()
                emp_names[emp_uid] = emp_name or (emp.get("email") or "")

            for uatt_uid, uatt_hours in uattend["by_uid"].items():
                try:
                    uatt_hours = float(uatt_hours or 0)
                except (TypeError, ValueError):
                    continue
                if uatt_hours <= 0 or uatt_uid in non_mechanic_uids:
                    continue
                match = match_map.get(uatt_uid)
                if match and match.get("internal_id"):
                    # Строка внутреннего пользователя: имя — по internal_id в
                    # конце, закэшированное internal_name — лишь запас.
                    key = str(match["internal_id"])
                    name = str(match.get("internal_name") or "").strip()
                else:
                    key = f"uattend:{uatt_uid}"
                    name = emp_names.get(uatt_uid) or f"uAttend #{uatt_uid}"
                bucket = mechanics.setdefault(key, {
                    "mechanic_id": key,
                    "mechanic_name": name,
                    "total_hours": 0.0,
                    "wo_count": 0,
                    "labor_entries": 0,
                    "tracked_hours": 0.0,
                })
                if name and not bucket.get("mechanic_name"):
                    bucket["mechanic_name"] = name
                bucket["uattend_hours"] = _round2(float(bucket.get("uattend_hours") or 0.0) + uatt_hours)
                total_uattend_hours += uatt_hours
        if uattend_connected:
            for bucket in mechanics.values():
                bucket.setdefault("uattend_hours", 0.0)

    # Подписи — живые имена по id (одним запросом, включая неактивных);
    # строки uAttend без внутреннего аккаунта — имя из uAttend.
    _apply_live_mechanic_names(mechanics, "mechanic_name", shop)

    rows = sorted(
        mechanics.values(),
        key=lambda x: (
            _round2(x.get("total_hours")),
            _round2(x.get("tracked_hours")),
            _round2(x.get("uattend_hours")),
        ),
        reverse=True,
    )

    total_hours = _round2(sum(float(r.get("total_hours") or 0) for r in rows))
    total_tracked_hours = _round2(sum(float(r.get("tracked_hours") or 0) for r in rows))
    total_wo = sum(int(r.get("wo_count") or 0) for r in rows)
    total_entries = sum(int(r.get("labor_entries") or 0) for r in rows)

    # Build chart data: one dataset per mechanic (данные — по id строки)
    labels = _fill_bucket_gaps(time_buckets, chart_bucket)
    chart_data = {
        "labels": labels,
        "datasets": [
            {
                "label": r["mechanic_name"],
                "data": [_round2(time_buckets.get(l, {}).get(r["mechanic_id"], 0)) for l in labels],
            }
            for r in rows
        ],
        "is_hours": True,
    }

    summary = {
        "mechanics_count": len(rows),
        "total_hours": total_hours,
        "total_tracked_hours": total_tracked_hours,
        "total_wo": total_wo,
        "total_entries": total_entries,
    }
    # Ключ присутствует только при подключённой интеграции — фронт по нему
    # решает, показывать ли колонку uAttend.
    if uattend_connected:
        summary["total_uattend_hours"] = _round2(total_uattend_hours)

    return {
        "title": "Mechanic Hours",
        "summary": summary,
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
            shop,
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
            shop=shop,
            # Кто сколько получает — только с правом на Timecard / Salary.
            show_employee_pay=has_permission("reports.view_payroll"),
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
@permission_required("reports.view")
def reports_index():
    layout_nav = filter_nav_items(NAV_ITEMS)
    return render_internal_page(
        "public/reports.html",
        layout_nav,
        "reports",
    )


@reports_bp.get("/standard/<report_key>")
@login_required
@permission_required("reports.view")
def standard_report_page(report_key: str):
    required = _report_forbidden(report_key)
    if required:
        from flask import flash, redirect, url_for

        flash("Access denied.", "error")
        return redirect(url_for("reports.reports_index"))

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
@permission_required("reports.view")
def standard_report_api(report_key: str):
    required = _report_forbidden(report_key)
    if required:
        return jsonify(ok=False, error="forbidden", required=required), 403

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
@permission_required("reports.export")
def standard_report_pdf(report_key: str):
    required = _report_forbidden(report_key)
    if required:
        return jsonify(ok=False, error="forbidden", required=required), 403

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
@permission_required("reports.view_payroll")
def timecard_salary_page():
    """Timecard / Salary: зарплата по сотрудникам за период.

    Расчёт — ``reports.services.payroll.compute_payroll``, общий с payroll в
    General Revenue. Страница всегда ходит в uAttend за свежими punches
    (``refresh_hours``) и этим же обновляет кэш часов для General Revenue.
    """
    from app.blueprints.reports.services.payroll import compute_payroll

    layout_nav = filter_nav_items(NAV_ITEMS)

    # ── Date range (default: this month) ─────────────────────────────────
    df = build_date_range_filters(request.args, default_preset="this_month")
    date_from = df["date_from"]  # YYYY-MM-DD or ""
    date_to = df["date_to"]
    debug_mode = request.args.get("debug") in ("1", "true", "yes")

    master = get_master_db()
    shop = _get_active_shop(master)
    shop_db = _get_shop_db(shop)

    payroll = compute_payroll(
        master,
        shop_db,
        shop["_id"] if shop else None,
        _tenant_variants(),
        date_from,
        date_to,
        refresh_hours=True,
        debug=debug_mode,
    )

    return render_internal_page(
        "public/reports/timecard_salary.html",
        layout_nav,
        "reports",
        rows=payroll["rows"],
        totals=payroll["totals"],
        date_from=date_from,
        date_to=date_to,
        date_preset=df["date_preset"],
        integration_status=payroll["integration_status"],
        integration_error=payroll["integration_error"],
        debug_mode=debug_mode,
        debug_payload=payroll["debug_payload"],
    )


@reports_bp.get("/audit")
@login_required
@permission_required("reports.view_audit")
def activity_journal_page():
    """
    Журнал событий: кто, что и когда сделал (бизнес-события из
    app.utils.audit_log) плюс технические строки запросов, для которых
    события не описаны. Фильтры: период, пользователь, тип объекта,
    действие, источник, конкретный объект (entity_type + entity_id —
    ссылки «History» со страниц объектов), текстовый поиск.
    """
    import re as _re

    from app.utils.audit_endpoint_labels import describe_endpoint
    from app.utils.audit_log import ACTIONS, ENTITY_TYPES, SOURCES
    from app.utils.display_datetime import to_active_shop_datetime
    from app.utils.pagination import get_pagination_params, paginate_find
    from app.utils.tenant import tenant_id_variants

    layout_nav = filter_nav_items(NAV_ITEMS)
    master = get_master_db()
    tenant_id = str(session.get(SESSION_TENANT_ID) or "")
    args = request.args

    q = (args.get("q") or "").strip()
    user_filter = (args.get("user_id") or "").strip()
    entity_type = (args.get("entity_type") or "").strip().lower()
    entity_id = (args.get("entity_id") or "").strip()
    action = (args.get("action") or "").strip().lower()
    source = (args.get("source") or "").strip().lower()
    shop_filter = (args.get("shop_id") or "").strip()
    kind = (args.get("kind") or "all").strip().lower()
    if kind not in ("all", "events", "requests"):
        kind = "all"
    df = build_date_range_filters(args, default_preset="all_time")

    query: dict = {"tenant_id": tenant_id} if tenant_id else {"tenant_id": "__none__"}
    if df.get("created_from") or df.get("created_to_exclusive"):
        rng = {}
        if df.get("created_from"):
            rng["$gte"] = df["created_from"]
        if df.get("created_to_exclusive"):
            rng["$lt"] = df["created_to_exclusive"]
        query["created_at"] = rng
    if user_filter:
        query["user_id"] = user_filter
    if entity_type:
        query["entity_type"] = entity_type
    if entity_id:
        query["entity_id"] = entity_id
    if action:
        query["action"] = action
    if source:
        query["source"] = source
    if shop_filter:
        query["shop_id"] = shop_filter
    if kind == "events":
        query["kind"] = "event"
    elif kind == "requests":
        query["kind"] = {"$ne": "event"}
    if q:
        pattern = {"$regex": _re.escape(q), "$options": "i"}
        query = {"$and": [query, {"$or": [
            {"summary": pattern}, {"entity_label": pattern}, {"user_name": pattern},
            {"path": pattern}, {"changes.new": pattern}, {"changes.old": pattern},
        ]}]}

    page, per_page = get_pagination_params(args, default_per_page=50, max_per_page=200)
    rows, pagination = paginate_find(master.audit_journal, query, [("created_at", -1)], page, per_page)

    entries = []
    for row in rows:
        is_event = row.get("kind") == "event"
        row_action = str(row.get("action") or "")
        row_entity_type = str(row.get("entity_type") or "")
        summary = str(row.get("summary") or "")
        if not is_event and not summary:
            # Старые записи (до бизнес-журнала): только метод/путь.
            row_action, row_entity_type, summary = describe_endpoint(
                str(row.get("endpoint") or ""), str(row.get("method") or ""), str(row.get("path") or "")
            )
            if int(row.get("status_code") or 0) >= 400:
                summary = f"Rejected ({row.get('status_code')}): {summary}"
        local_dt = to_active_shop_datetime(row.get("created_at"))
        entries.append({
            "id": str(row.get("_id")),
            "is_event": is_event,
            "when": local_dt.strftime("%m/%d/%Y %H:%M:%S") if local_dt else "-",
            "when_day": local_dt.strftime("%m/%d/%Y") if local_dt else "-",
            "when_time": local_dt.strftime("%H:%M:%S") if local_dt else "",
            "user_id": str(row.get("user_id") or ""),
            "user_name": str(row.get("user_name") or ""),
            "user_role": str(row.get("user_role") or ""),
            "action": row_action,
            "action_label": ACTIONS.get(row_action, row_action.replace("_", " ").capitalize() or "-"),
            "entity_type": row_entity_type,
            "entity_type_label": ENTITY_TYPES.get(row_entity_type, row_entity_type.replace("_", " ").capitalize() or "-"),
            "entity_id": str(row.get("entity_id") or ""),
            "entity_label": str(row.get("entity_label") or ""),
            "entity_url": str(row.get("entity_url") or ""),
            "summary": summary,
            "changes": row.get("changes") if isinstance(row.get("changes"), list) else [],
            "details": row.get("details") if isinstance(row.get("details"), dict) else {},
            "source": str(row.get("source") or "web"),
            "source_label": SOURCES.get(str(row.get("source") or "web"), str(row.get("source") or "web").capitalize()),
            "ok": bool(row.get("ok", True)),
            "status_code": row.get("status_code"),
            "method": str(row.get("method") or ""),
            "path": str(row.get("path") or ""),
            "ip": str(row.get("ip") or ""),
            "error": str(row.get("error") or ""),
            "shop_id": str(row.get("shop_id") or ""),
            "shop_name": str(row.get("shop_name") or ""),
            "payload": row.get("payload") if isinstance(row.get("payload"), dict) else {},
        })

    tv = tenant_id_variants()
    users = []
    if tv:
        cursor = master.users.find(
            {"tenant_id": {"$in": tv}},
            {"name": 1, "first_name": 1, "last_name": 1, "email": 1, "role": 1, "is_active": 1},
        ).sort([("name", 1), ("email", 1)])
        for u in cursor:
            name = str(u.get("name") or "").strip()
            if not name:
                name = (str(u.get("first_name") or "") + " " + str(u.get("last_name") or "")).strip()
            users.append({"id": str(u["_id"]), "name": name or str(u.get("email") or ""), "active": u.get("is_active", True)})
    shops = []
    if tv:
        for sh in master.shops.find({"tenant_id": {"$in": tv}}, {"name": 1}).sort("name", 1):
            shops.append({"id": str(sh["_id"]), "name": str(sh.get("name") or "")})
    shop_names = {sh["id"]: sh["name"] for sh in shops}
    for e in entries:
        if not e["shop_name"]:
            e["shop_name"] = shop_names.get(e["shop_id"], "")

    return render_internal_page(
        "public/reports/audit.html",
        layout_nav,
        "reports",
        activity_entries=entries,
        pagination=pagination,
        activity_total=pagination.get("total", 0),
        filters={
            "q": q, "user_id": user_filter, "entity_type": entity_type, "entity_id": entity_id,
            "action": action, "source": source, "shop_id": shop_filter, "kind": kind,
            "date_preset": df.get("date_preset") or "all_time", "date_from": df.get("date_from") or "",
            "date_to": df.get("date_to") or "",
        },
        users=users,
        shops=shops,
        entity_types=ENTITY_TYPES,
        actions=ACTIONS,
        sources=SOURCES,
    )
