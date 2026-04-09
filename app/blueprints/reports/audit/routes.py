from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from flask import make_response, render_template, request, session

from app.blueprints.main.routes import NAV_ITEMS
from app.blueprints.reports import reports_bp
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import SESSION_TENANT_ID, login_required
from app.utils.date_filters import build_date_range_filters
from app.utils.layout import render_internal_page
from app.utils.pagination import get_sort_params
from app.utils.pdf_utils import render_html_to_pdf
from app.utils.permissions import filter_nav_items


STANDARD_REPORT_TABS = {
    "sales_summary",
    "payments_summary",
    "customer_balances",
    "vendor_balances",
}

CUSTOMER_FILTER_TABS = {
    "sales_summary",
    "payments_summary",
    "customer_balances",
}


def _now_utc():
    return datetime.now(timezone.utc)


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
    tax = totals.get("sales_tax_total") if totals.get("sales_tax_total") is not None else wo.get("sales_tax_total")
    grand = totals.get("grand_total") if totals.get("grand_total") is not None else wo.get("grand_total")
    return {
        "labor_total": _round2(labor),
        "parts_total": _round2(parts),
        "sales_tax_total": _round2(tax),
        "grand_total": _round2(grand),
    }


def _report_sales_summary(shop_db, shop_id, date_ctx, include_customer_ids, exclude_customer_ids, customer_map):
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

    for wo in shop_db.work_orders.find(query, {"customer_id": 1, "totals": 1, "labor_total": 1, "parts_total": 1, "sales_tax_total": 1, "grand_total": 1}):
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
                "sales_tax_total": 0.0,
                "grand_total": 0.0,
            },
        )
        totals = _wo_totals(wo)
        bucket["orders_count"] += 1
        bucket["labor_total"] = _round2(bucket["labor_total"] + totals["labor_total"])
        bucket["parts_total"] = _round2(bucket["parts_total"] + totals["parts_total"])
        bucket["sales_tax_total"] = _round2(bucket["sales_tax_total"] + totals["sales_tax_total"])
        bucket["grand_total"] = _round2(bucket["grand_total"] + totals["grand_total"])

    rows = sorted(rows_by_customer.values(), key=lambda x: x.get("grand_total", 0), reverse=True)

    total_orders = sum(int(r.get("orders_count") or 0) for r in rows)
    total_revenue = _round2(sum(float(r.get("grand_total") or 0) for r in rows))
    total_labor = _round2(sum(float(r.get("labor_total") or 0) for r in rows))
    total_parts = _round2(sum(float(r.get("parts_total") or 0) for r in rows))
    total_tax = _round2(sum(float(r.get("sales_tax_total") or 0) for r in rows))

    return {
        "title": "Sales Summary",
        "summary": {
            "orders_count": total_orders,
            "revenue_total": total_revenue,
            "avg_ticket": _round2(total_revenue / total_orders) if total_orders else 0.0,
            "labor_total": total_labor,
            "parts_total": total_parts,
            "sales_tax_total": total_tax,
        },
        "rows": rows,
    }


def _report_payments_summary(shop_db, shop_id, date_ctx, include_customer_ids, exclude_customer_ids, customer_map):
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

    for payment in shop_db.work_order_payments.find(payments_query, {"work_order_id": 1, "amount": 1}):
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

    rows = sorted(rows_by_customer.values(), key=lambda x: x.get("amount_total", 0), reverse=True)

    return {
        "title": "Payments Summary",
        "summary": {
            "payments_count": payments_count,
            "payments_total": payments_total,
            "avg_payment": _round2(payments_total / payments_count) if payments_count else 0.0,
        },
        "rows": rows,
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


def _build_standard_reports_context(selected_tab: str, args):
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

    include_customer_ids_raw = args.getlist("include_customer_ids")
    exclude_customer_ids_raw = args.getlist("exclude_customer_ids")

    include_customer_ids = _to_oid_list(include_customer_ids_raw)
    exclude_customer_ids = _to_oid_list(exclude_customer_ids_raw)

    exclude_set = {str(x) for x in exclude_customer_ids}
    include_customer_ids = [x for x in include_customer_ids if str(x) not in exclude_set]

    customer_options, customer_map = _get_customer_options(shop_db, shop["_id"])

    if selected_tab == "sales_summary":
        report_data = _report_sales_summary(
            shop_db,
            shop["_id"],
            date_ctx,
            include_customer_ids,
            exclude_customer_ids,
            customer_map,
        )
    elif selected_tab == "payments_summary":
        report_data = _report_payments_summary(
            shop_db,
            shop["_id"],
            date_ctx,
            include_customer_ids,
            exclude_customer_ids,
            customer_map,
        )
    elif selected_tab == "customer_balances":
        report_data = _report_customer_balances(
            shop_db,
            shop["_id"],
            date_ctx,
            include_customer_ids,
            exclude_customer_ids,
            customer_map,
        )
    else:
        report_data = _report_vendor_balances(
            shop_db,
            shop["_id"],
            date_ctx,
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
    ctx = _build_standard_reports_context(report_key, request.args)

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
        report_data=ctx["report_data"],
        generated_at=ctx["generated_at"],
        shop_name=ctx["shop_name"],
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
