from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bson import ObjectId
from flask import request, redirect, url_for, flash, session

from app.blueprints.dashboard import dashboard_bp
from app.blueprints.main.routes import _render_app_page
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import login_required, SESSION_TENANT_ID
from app.utils.date_filters import build_date_range_filters
from app.utils.permissions import permission_required


def _maybe_object_id(value):
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return str(value)


def _tenant_id_variants():
    raw = session.get(SESSION_TENANT_ID)
    out = set()
    if raw is None:
        return []
    out.add(raw)
    out.add(str(raw))
    try:
        out.add(ObjectId(str(raw)))
    except Exception:
        pass
    return list(out)


def _get_active_shop_db():
    master = get_master_db()
    shop_oid = _maybe_object_id(session.get("shop_id"))
    tenant_variants = _tenant_id_variants()
    if not shop_oid or not tenant_variants:
        return None, None

    shop = master.shops.find_one({"_id": shop_oid, "tenant_id": {"$in": tenant_variants}})
    if not shop:
        return None, None

    db_name = shop.get("db_name")
    if not db_name:
        return None, shop

    client = get_mongo_client()
    return client[str(db_name)], shop


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


def _round2(value):
    try:
        return round(float(value or 0) + 1e-12, 2)
    except Exception:
        return 0.0


def _to_float(value):
    try:
        return float(str(value).strip())
    except Exception:
        return 0.0


@dashboard_bp.get("/dashboard")
@login_required
@permission_required("dashboard.view")
def dashboard():
    shop_db, shop = _get_active_shop_db()
    if shop_db is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("main.settings"))

    date_filters = _get_date_range_filters(request.args)
    date_from = date_filters["date_from"]
    date_to = date_filters["date_to"]
    date_preset = date_filters["date_preset"]
    created_from = date_filters["created_from"]
    created_to_exclusive = date_filters["created_to_exclusive"]

    date_match = {"shop_id": shop["_id"], "is_active": True}
    created_filter = {}
    if created_from:
        created_filter["$gte"] = created_from
    if created_to_exclusive:
        created_filter["$lt"] = created_to_exclusive
    if created_filter:
        date_match["created_at"] = created_filter

    period_wo_rows = list(
        shop_db.work_orders.find(
            date_match,
            {
                "_id": 1,
                "totals": 1,
                "grand_total": 1,
                "labor_total": 1,
                "parts_total": 1,
                "labors": 1,
                "blocks": 1,
                "status": 1,
            },
        )
    )

    period_total = len(period_wo_rows)

    period_labor_total = 0.0
    period_parts_total = 0.0
    period_grand_total = 0.0
    mechanic_hours_map = {}
    for wo in period_wo_rows:
        totals = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
        labor_total = totals.get("labor_total") if totals.get("labor_total") is not None else wo.get("labor_total")
        parts_total = totals.get("parts_total") if totals.get("parts_total") is not None else wo.get("parts_total")
        grand_total = totals.get("grand_total") if totals.get("grand_total") is not None else wo.get("grand_total")
        period_labor_total = _round2(period_labor_total + _round2(labor_total))
        period_parts_total = _round2(period_parts_total + _round2(parts_total))
        period_grand_total = _round2(period_grand_total + _round2(grand_total))

        labor_blocks = wo.get("labors") if isinstance(wo.get("labors"), list) else []
        if not labor_blocks and isinstance(wo.get("blocks"), list):
            labor_blocks = wo.get("blocks")

        for block in labor_blocks:
            if not isinstance(block, dict):
                continue
            labor_doc = block.get("labor") if isinstance(block.get("labor"), dict) else {}
            hours_raw = labor_doc.get("hours") if labor_doc.get("hours") is not None else block.get("labor_hours")
            hours_value = max(0.0, _to_float(hours_raw))
            if hours_value <= 0:
                continue

            assigned = labor_doc.get("assigned_mechanics")
            if not isinstance(assigned, list):
                assigned = block.get("assigned_mechanics")
            if not isinstance(assigned, list) or not assigned:
                continue

            for item in assigned:
                if not isinstance(item, dict):
                    continue
                share = _to_float(item.get("percent"))
                if share <= 0:
                    continue
                share_hours = hours_value * (share / 100.0)

                mechanic_id = str(item.get("user_id") or "").strip()
                mechanic_name = str(item.get("name") or "").strip()
                if not mechanic_name:
                    mechanic_name = str(item.get("email") or "").strip()
                if not mechanic_name:
                    mechanic_name = "Unknown mechanic"

                mechanic_key = mechanic_id or mechanic_name.lower()
                row = mechanic_hours_map.get(mechanic_key)
                if row is None:
                    row = {
                        "user_id": mechanic_id,
                        "name": mechanic_name,
                        "hours": 0.0,
                    }
                    mechanic_hours_map[mechanic_key] = row

                row["hours"] = _round2(row["hours"] + share_hours)

    mechanic_hours_rows = sorted(
        mechanic_hours_map.values(),
        key=lambda x: _to_float(x.get("hours")),
        reverse=True,
    )

    period_wo_ids = [x.get("_id") for x in period_wo_rows if x.get("_id")]
    period_paid_map = {}
    if period_wo_ids:
        period_pipeline = [
            {"$match": {"work_order_id": {"$in": period_wo_ids}, "is_active": True}},
            {"$group": {"_id": "$work_order_id", "paid_total": {"$sum": "$amount"}}},
        ]
        for row in shop_db.work_order_payments.aggregate(period_pipeline):
            period_paid_map[row.get("_id")] = _round2(row.get("paid_total") or 0)

    period_paid_amount = 0.0
    period_unpaid_amount = 0.0
    for wo in period_wo_rows:
        totals = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
        status = str(wo.get("status") or "").strip().lower()
        grand_total = totals.get("grand_total") if totals.get("grand_total") is not None else wo.get("grand_total")
        grand_total = _round2(grand_total)
        paid_amount = _round2(period_paid_map.get(wo.get("_id"), 0))

        # Backward-compatible fallback: some datasets mark status as paid without
        # storing rows in work_order_payments.
        if status == "paid":
            paid_amount = _round2(max(paid_amount, grand_total))

        paid_capped = _round2(min(grand_total, paid_amount))
        unpaid_amount = _round2(max(0.0, grand_total - paid_capped))
        period_paid_amount = _round2(period_paid_amount + paid_capped)
        period_unpaid_amount = _round2(period_unpaid_amount + unpaid_amount)

    period_money_total = _round2(period_paid_amount + period_unpaid_amount)
    paid_percent = (period_paid_amount / period_money_total * 100.0) if period_money_total else 0.0

    parts_orders_query = {"shop_id": shop["_id"], "is_active": {"$ne": False}}
    if created_filter:
        parts_orders_query["created_at"] = created_filter

    period_parts_orders_rows = list(
        shop_db.parts_orders.find(
            parts_orders_query,
            {"_id": 1, "status": 1, "items": 1, "non_inventory_amounts": 1},
        )
    )

    period_parts_orders_total = len(period_parts_orders_rows)
    period_parts_orders_received = 0
    period_parts_orders_ordered = 0
    period_parts_orders_items_amount = 0.0
    period_parts_orders_non_inventory_amount = 0.0
    period_parts_orders_total_amount = 0.0
    for order in period_parts_orders_rows:
        status = str(order.get("status") or "").strip().lower()
        order_amount = 0.0
        for item in (order.get("items") or []):
            if not isinstance(item, dict):
                continue
            qty = max(0, int(_to_float(item.get("quantity"))))
            price = max(0.0, _to_float(item.get("price")))
            line_amount = _round2(qty * price)
            period_parts_orders_items_amount = _round2(period_parts_orders_items_amount + line_amount)
            order_amount = _round2(order_amount + line_amount)

        for line in (order.get("non_inventory_amounts") or []):
            if not isinstance(line, dict):
                continue
            amount = max(0.0, _to_float(line.get("amount")))
            period_parts_orders_non_inventory_amount = _round2(period_parts_orders_non_inventory_amount + amount)
            order_amount = _round2(order_amount + amount)

        period_parts_orders_total_amount = _round2(period_parts_orders_total_amount + order_amount)

        if status == "received":
            period_parts_orders_received += 1
        else:
            period_parts_orders_ordered += 1

    parts_orders_received_percent = (
        (period_parts_orders_received / period_parts_orders_total) * 100.0
        if period_parts_orders_total
        else 0.0
    )

    all_time_base = {"shop_id": shop["_id"], "is_active": True}
    all_time_wo_total = shop_db.work_orders.count_documents(all_time_base)

    # Temporary dynamic goal source via query arg; replace with DB/settings source later.
    goal_raw = str(request.args.get("goal") or "").strip()
    try:
        goal_count = int(goal_raw) if goal_raw else 120
    except Exception:
        goal_count = 120
    if goal_count < 1:
        goal_count = 1

    goal_percent = min(100.0, (period_total / goal_count) * 100.0)

    wo_rows = list(
        shop_db.work_orders.find(
            all_time_base,
            {"_id": 1, "totals": 1, "grand_total": 1, "status": 1},
        )
    )
    wo_ids = [x.get("_id") for x in wo_rows if x.get("_id")]

    paid_map = {}
    if wo_ids:
        pipeline = [
            {"$match": {"work_order_id": {"$in": wo_ids}, "is_active": True}},
            {"$group": {"_id": "$work_order_id", "paid_total": {"$sum": "$amount"}}},
        ]
        for row in shop_db.work_order_payments.aggregate(pipeline):
            paid_map[row.get("_id")] = _round2(row.get("paid_total") or 0)

    outstanding_balance = 0.0
    for wo in wo_rows:
        totals = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
        status = str(wo.get("status") or "").strip().lower()
        grand_total = totals.get("grand_total") if totals.get("grand_total") is not None else wo.get("grand_total")
        grand_total = _round2(grand_total)
        paid_amount = _round2(paid_map.get(wo.get("_id"), 0))

        if status == "paid":
            paid_amount = _round2(max(paid_amount, grand_total))

        remaining = _round2(grand_total - paid_amount)
        if remaining > 0:
            outstanding_balance = _round2(outstanding_balance + remaining)

    return _render_app_page(
        "public/dashboard.html",
        active_page="dashboard",
        date_from=date_from,
        date_to=date_to,
        date_preset=date_preset,
        period_paid_amount=period_paid_amount,
        period_unpaid_amount=period_unpaid_amount,
        period_labor_total=period_labor_total,
        period_parts_total=period_parts_total,
        period_grand_total=period_grand_total,
        mechanic_hours_rows=mechanic_hours_rows,
        period_money_total=period_money_total,
        period_total=period_total,
        paid_percent=paid_percent,
        period_parts_orders_total=period_parts_orders_total,
        period_parts_orders_received=period_parts_orders_received,
        period_parts_orders_ordered=period_parts_orders_ordered,
        parts_orders_received_percent=parts_orders_received_percent,
        period_parts_orders_items_amount=period_parts_orders_items_amount,
        period_parts_orders_non_inventory_amount=period_parts_orders_non_inventory_amount,
        period_parts_orders_total_amount=period_parts_orders_total_amount,
        goal_count=goal_count,
        period_wo_total=period_total,
        all_time_wo_total=all_time_wo_total,
        goal_percent=goal_percent,
        outstanding_balance=outstanding_balance,
    )
