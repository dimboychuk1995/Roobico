from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bson import ObjectId
from flask import request, redirect, url_for, flash, session, jsonify

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


def _parse_goal_count(args) -> int:
    # Deprecated: kept for backward compatibility with older URLs that may include ?goal=.
    goal_raw = str(args.get("goal") or "").strip()
    try:
        goal_count = int(goal_raw) if goal_raw else 120
    except Exception:
        goal_count = 120
    if goal_count < 1:
        goal_count = 1
    return goal_count


DEFAULT_DASHBOARD_GOALS = {
    "labor": 0.0,
    "parts_sales": 0.0,
    "total": 0.0,
}

# Average days per month, used to prorate monthly goals to arbitrary periods.
_AVG_DAYS_PER_MONTH = 30.4375


def _get_dashboard_goals(shop) -> dict:
    raw = (shop or {}).get("dashboard_goals") if isinstance(shop, dict) else None
    if not isinstance(raw, dict):
        raw = {}
    return {
        "labor": max(0.0, _to_float(raw.get("labor"))),
        "parts_sales": max(0.0, _to_float(raw.get("parts_sales"))),
        "total": max(0.0, _to_float(raw.get("total"))),
    }


def _save_dashboard_goals(shop, payload) -> dict:
    if not isinstance(shop, dict) or not shop.get("_id"):
        return DEFAULT_DASHBOARD_GOALS.copy()
    cleaned = {
        "labor": max(0.0, _to_float((payload or {}).get("labor"))),
        "parts_sales": max(0.0, _to_float((payload or {}).get("parts_sales"))),
        "total": max(0.0, _to_float((payload or {}).get("total"))),
    }
    master = get_master_db()
    master.shops.update_one(
        {"_id": shop["_id"]},
        {"$set": {"dashboard_goals": cleaned, "dashboard_goals_updated_at": datetime.now(timezone.utc)}},
    )
    return cleaned


def _period_days(created_from, created_to_exclusive) -> float:
    if not created_from or not created_to_exclusive:
        return 0.0
    delta = created_to_exclusive - created_from
    return max(0.0, delta.total_seconds() / 86400.0)


def _full_preset_days(preset: str, created_from, created_to_exclusive) -> float:
    """Return the natural full length (in days) of the selected preset.

    For in-progress presets (this_week / this_quarter / this_year) we extend the
    range to the end of the calendar period so the goal isn't under-counted just
    because the period hasn't ended yet.
    """
    preset = str(preset or "").strip().lower()
    if not created_from:
        return _period_days(created_from, created_to_exclusive)

    if preset == "this_week":
        # Mon..Sun = 7 days
        return 7.0
    if preset == "this_quarter":
        # Extend created_from to the start of the next quarter.
        start_month = ((created_from.month - 1) // 3) * 3 + 1
        next_q_year = created_from.year
        next_q_month = start_month + 3
        if next_q_month > 12:
            next_q_month -= 12
            next_q_year += 1
        next_q_start = created_from.replace(
            year=next_q_year, month=next_q_month, day=1, hour=0, minute=0, second=0, microsecond=0
        )
        delta = next_q_start - created_from.replace(month=start_month, day=1, hour=0, minute=0, second=0, microsecond=0)
        return max(0.0, delta.total_seconds() / 86400.0)
    if preset == "this_year":
        next_year_start = created_from.replace(
            year=created_from.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
        year_start = created_from.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        delta = next_year_start - year_start
        return max(0.0, delta.total_seconds() / 86400.0)

    # last_*, today, yesterday, custom, all_time -> actual selected range
    return _period_days(created_from, created_to_exclusive)


def _prorate_monthly_goal(monthly_goal: float, date_preset: str, created_from, created_to_exclusive) -> float:
    """Convert a monthly goal to the selected period.

    - this_month / last_month: use monthly goal as-is (no proration).
    - all_time / no period: fall back to the monthly goal.
    - this_week / this_quarter / this_year: use the FULL calendar period length
      (not truncated to today), so the target reflects the whole upcoming period.
    - last_*, custom, today, yesterday: scale by the actual days in range.
    """
    base = max(0.0, _to_float(monthly_goal))
    if base <= 0:
        return 0.0
    preset = str(date_preset or "").strip().lower()
    if preset in ("this_month", "last_month"):
        return base
    days = _full_preset_days(preset, created_from, created_to_exclusive)
    if days <= 0:
        return base
    return base * (days / _AVG_DAYS_PER_MONTH)


def _build_created_filter(created_from, created_to_exclusive):
    created_filter = {}
    if created_from:
        created_filter["$gte"] = created_from
    if created_to_exclusive:
        created_filter["$lt"] = created_to_exclusive
    return created_filter


def _build_preferred_date_filter(date_field: str, created_from, created_to_exclusive):
    """Match table behavior: filter on ``date_field`` (e.g. work_order_date / order_date),
    falling back to ``created_at`` when the preferred field is missing/None."""
    range_filter = _build_created_filter(created_from, created_to_exclusive)
    if not range_filter:
        return None
    return {
        "$or": [
            {date_field: range_filter},
            {date_field: {"$exists": False}, "created_at": range_filter},
            {date_field: None, "created_at": range_filter},
        ]
    }


def _build_period_work_orders_query(shop, created_from, created_to_exclusive):
    query = {"shop_id": shop["_id"], "is_active": True}
    preferred_filter = _build_preferred_date_filter("work_order_date", created_from, created_to_exclusive)
    if preferred_filter:
        query = {"$and": [query, preferred_filter]}
    return query


def _load_period_work_orders(shop_db, shop, created_from, created_to_exclusive, projection):
    query = _build_period_work_orders_query(shop, created_from, created_to_exclusive)
    return list(shop_db.work_orders.find(query, projection))


def _compute_wo_money_metrics(shop_db, shop, created_from, created_to_exclusive):
    period_wo_rows = _load_period_work_orders(
        shop_db,
        shop,
        created_from,
        created_to_exclusive,
        {
            "_id": 1,
            "totals": 1,
            "grand_total": 1,
            "labor_total": 1,
            "parts_total": 1,
            "status": 1,
        },
    )

    period_total = len(period_wo_rows)
    period_labor_total = 0.0
    period_parts_total = 0.0
    period_grand_total = 0.0
    for wo in period_wo_rows:
        totals = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
        labor_total = totals.get("labor_total") if totals.get("labor_total") is not None else wo.get("labor_total")
        parts_total = totals.get("parts_total") if totals.get("parts_total") is not None else wo.get("parts_total")
        grand_total = totals.get("grand_total") if totals.get("grand_total") is not None else wo.get("grand_total")
        period_labor_total = _round2(period_labor_total + _round2(labor_total))
        period_parts_total = _round2(period_parts_total + _round2(parts_total))
        period_grand_total = _round2(period_grand_total + _round2(grand_total))

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
        if status == "paid":
            paid_amount = _round2(max(paid_amount, grand_total))

        paid_capped = _round2(min(grand_total, paid_amount))
        unpaid_amount = _round2(max(0.0, grand_total - paid_capped))
        period_paid_amount = _round2(period_paid_amount + paid_capped)
        period_unpaid_amount = _round2(period_unpaid_amount + unpaid_amount)

    period_money_total = _round2(period_paid_amount + period_unpaid_amount)
    paid_percent = (period_paid_amount / period_money_total * 100.0) if period_money_total else 0.0

    return {
        "period_paid_amount": period_paid_amount,
        "period_unpaid_amount": period_unpaid_amount,
        "period_labor_total": period_labor_total,
        "period_parts_total": period_parts_total,
        "period_grand_total": period_grand_total,
        "period_money_total": period_money_total,
        "period_total": period_total,
        "paid_percent": paid_percent,
    }


def _compute_mechanic_hours_metrics(shop_db, shop, created_from, created_to_exclusive):
    period_wo_rows = _load_period_work_orders(
        shop_db,
        shop,
        created_from,
        created_to_exclusive,
        {
            "labors": 1,
            "blocks": 1,
        },
    )

    mechanic_hours_map = {}
    for wo in period_wo_rows:
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
                mechanic_name = str(item.get("name") or "").strip() or str(item.get("email") or "").strip()
                if not mechanic_name:
                    mechanic_name = "Unknown mechanic"

                mechanic_key = mechanic_id or mechanic_name.lower()
                row = mechanic_hours_map.get(mechanic_key)
                if row is None:
                    row = {"user_id": mechanic_id, "name": mechanic_name, "hours": 0.0}
                    mechanic_hours_map[mechanic_key] = row

                row["hours"] = _round2(row["hours"] + share_hours)

    mechanic_hours_rows = sorted(
        mechanic_hours_map.values(),
        key=lambda x: _to_float(x.get("hours")),
        reverse=True,
    )
    return {"mechanic_hours_rows": mechanic_hours_rows}


def _compute_parts_orders_metrics(shop_db, shop, created_from, created_to_exclusive):
    preferred_filter = _build_preferred_date_filter("order_date", created_from, created_to_exclusive)
    parts_orders_query = {"shop_id": shop["_id"], "is_active": {"$ne": False}}
    if preferred_filter:
        parts_orders_query = {"$and": [parts_orders_query, preferred_filter]}

    period_parts_orders_rows = list(
        shop_db.parts_orders.find(
            parts_orders_query,
            {"_id": 1, "status": 1, "items": 1, "non_inventory_amounts": 1, "payment_status": 1, "paid_amount": 1},
        )
    )

    period_parts_orders_total = len(period_parts_orders_rows)
    period_parts_orders_received = 0
    period_parts_orders_ordered = 0
    period_parts_orders_items_amount = 0.0
    period_parts_orders_non_inventory_amount = 0.0
    period_parts_orders_total_amount = 0.0
    period_parts_orders_paid_count = 0
    period_parts_orders_unpaid_count = 0
    period_parts_orders_paid_amount = 0.0
    period_parts_orders_unpaid_amount = 0.0

    period_parts_order_ids = [x.get("_id") for x in period_parts_orders_rows if x.get("_id")]
    parts_orders_paid_map = {}
    if period_parts_order_ids:
        pipeline = [
            {"$match": {"parts_order_id": {"$in": period_parts_order_ids}, "is_active": True}},
            {"$group": {"_id": "$parts_order_id", "paid_total": {"$sum": "$amount"}}},
        ]
        for row in shop_db.parts_order_payments.aggregate(pipeline):
            parts_orders_paid_map[row.get("_id")] = _round2(row.get("paid_total") or 0)

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

        paid_amount = _round2(parts_orders_paid_map.get(order.get("_id"), order.get("paid_amount") or 0))
        payment_status = str(order.get("payment_status") or "").strip().lower()
        is_paid = False
        if payment_status == "paid" or order_amount <= 0 or paid_amount + 0.01 >= order_amount:
            is_paid = True

        if is_paid:
            period_parts_orders_paid_count += 1
            period_parts_orders_paid_amount = _round2(period_parts_orders_paid_amount + order_amount)
        else:
            period_parts_orders_unpaid_count += 1
            period_parts_orders_unpaid_amount = _round2(period_parts_orders_unpaid_amount + order_amount)

        if status == "received":
            period_parts_orders_received += 1
        else:
            period_parts_orders_ordered += 1

    parts_orders_received_percent = (
        (period_parts_orders_received / period_parts_orders_total) * 100.0
        if period_parts_orders_total
        else 0.0
    )
    parts_orders_paid_amount_total = _round2(period_parts_orders_paid_amount + period_parts_orders_unpaid_amount)
    parts_orders_paid_percent_by_amount = (
        (period_parts_orders_paid_amount / parts_orders_paid_amount_total) * 100.0
        if parts_orders_paid_amount_total
        else 0.0
    )
    parts_orders_paid_percent = (
        (period_parts_orders_paid_count / period_parts_orders_total) * 100.0
        if period_parts_orders_total
        else 0.0
    )

    return {
        "period_parts_orders_total": period_parts_orders_total,
        "period_parts_orders_received": period_parts_orders_received,
        "period_parts_orders_ordered": period_parts_orders_ordered,
        "period_parts_orders_paid_count": period_parts_orders_paid_count,
        "period_parts_orders_unpaid_count": period_parts_orders_unpaid_count,
        "period_parts_orders_paid_amount": period_parts_orders_paid_amount,
        "period_parts_orders_unpaid_amount": period_parts_orders_unpaid_amount,
        "parts_orders_paid_percent_by_amount": parts_orders_paid_percent_by_amount,
        "parts_orders_received_percent": parts_orders_received_percent,
        "parts_orders_paid_percent": parts_orders_paid_percent,
        "period_parts_orders_items_amount": period_parts_orders_items_amount,
        "period_parts_orders_non_inventory_amount": period_parts_orders_non_inventory_amount,
        "period_parts_orders_total_amount": period_parts_orders_total_amount,
    }


def _compute_goal_progress_metrics(shop_db, shop, created_from, created_to_exclusive, date_preset: str):
    monthly = _get_dashboard_goals(shop)
    labor_goal = _round2(_prorate_monthly_goal(monthly["labor"], date_preset, created_from, created_to_exclusive))
    parts_goal = _round2(_prorate_monthly_goal(monthly["parts_sales"], date_preset, created_from, created_to_exclusive))
    total_goal = _round2(_prorate_monthly_goal(monthly["total"], date_preset, created_from, created_to_exclusive))

    wo_money = _compute_wo_money_metrics(shop_db, shop, created_from, created_to_exclusive)
    labor_actual = _round2(wo_money.get("period_labor_total") or 0)
    parts_actual = _round2(wo_money.get("period_parts_total") or 0)
    total_actual = _round2(wo_money.get("period_grand_total") or 0)

    def _pct(actual, goal):
        if goal <= 0:
            return 0.0
        return min(100.0, (actual / goal) * 100.0)

    return {
        "goals_monthly": monthly,
        "goals_period": {
            "labor": labor_goal,
            "parts_sales": parts_goal,
            "total": total_goal,
        },
        "goals_actual": {
            "labor": labor_actual,
            "parts_sales": parts_actual,
            "total": total_actual,
        },
        "goals_percent": {
            "labor": _pct(labor_actual, labor_goal),
            "parts_sales": _pct(parts_actual, parts_goal),
            "total": _pct(total_actual, total_goal),
        },
        "goals_period_label": _humanize_preset(date_preset),
    }


def _humanize_preset(preset: str) -> str:
    mapping = {
        "today": "Today",
        "yesterday": "Yesterday",
        "this_week": "This Week",
        "last_week": "Last Week",
        "this_month": "This Month",
        "last_month": "Last Month",
        "this_quarter": "This Quarter",
        "last_quarter": "Last Quarter",
        "this_year": "This Year",
        "last_year": "Last Year",
        "all_time": "All Time",
        "custom": "Custom Range",
    }
    return mapping.get(str(preset or "").strip().lower(), "Selected Period")


def _compute_outstanding_balance_metrics(shop_db, shop):
    all_time_base = {"shop_id": shop["_id"], "is_active": True}
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

    return {"outstanding_balance": outstanding_balance}


def _compute_dashboard_block_metrics(block_name, shop_db, shop, created_from, created_to_exclusive, date_preset: str):
    if block_name == "wo-money":
        return _compute_wo_money_metrics(shop_db, shop, created_from, created_to_exclusive)
    if block_name == "parts-orders":
        return _compute_parts_orders_metrics(shop_db, shop, created_from, created_to_exclusive)
    if block_name == "goal-progress":
        return _compute_goal_progress_metrics(shop_db, shop, created_from, created_to_exclusive, date_preset)
    if block_name == "outstanding-balance":
        return _compute_outstanding_balance_metrics(shop_db, shop)
    if block_name == "mechanic-hours":
        return _compute_mechanic_hours_metrics(shop_db, shop, created_from, created_to_exclusive)
    raise KeyError(block_name)


DASHBOARD_BLOCK_NAMES = (
    "wo-money",
    "parts-orders",
    "goal-progress",
    "outstanding-balance",
    "mechanic-hours",
)


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

    monthly_goals = _get_dashboard_goals(shop)

    return _render_app_page(
        "public/dashboard.html",
        active_page="dashboard",
        date_from=date_from,
        date_to=date_to,
        date_preset=date_preset,
        monthly_goals=monthly_goals,
        period_paid_amount=0.0,
        period_unpaid_amount=0.0,
        period_labor_total=0.0,
        period_parts_total=0.0,
        period_grand_total=0.0,
        mechanic_hours_rows=[],
        period_money_total=0.0,
        period_total=0,
        paid_percent=0.0,
        period_parts_orders_total=0,
        period_parts_orders_received=0,
        period_parts_orders_ordered=0,
        period_parts_orders_paid_count=0,
        period_parts_orders_unpaid_count=0,
        period_parts_orders_paid_amount=0.0,
        period_parts_orders_unpaid_amount=0.0,
        parts_orders_paid_percent_by_amount=0.0,
        parts_orders_received_percent=0.0,
        parts_orders_paid_percent=0.0,
        period_parts_orders_items_amount=0.0,
        period_parts_orders_non_inventory_amount=0.0,
        period_parts_orders_total_amount=0.0,
        outstanding_balance=0.0,
        dashboard_metrics_api_url=url_for("dashboard.dashboard_metrics_api"),
        dashboard_goals_save_url=url_for("dashboard.dashboard_goals_save_api"),
    )


def _compute_dashboard_metrics(shop_db, shop, created_from, created_to_exclusive, date_preset: str):
    metrics = {}
    for block_name in DASHBOARD_BLOCK_NAMES:
        metrics.update(
            _compute_dashboard_block_metrics(
                block_name,
                shop_db=shop_db,
                shop=shop,
                created_from=created_from,
                created_to_exclusive=created_to_exclusive,
                date_preset=date_preset,
            )
        )
    return metrics


@dashboard_bp.get("/dashboard/api/metrics")
@login_required
@permission_required("dashboard.view")
def dashboard_metrics_api():
    shop_db, shop = _get_active_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop database not configured for this shop."}), 400

    date_filters = _get_date_range_filters(request.args)
    created_from = date_filters["created_from"]
    created_to_exclusive = date_filters["created_to_exclusive"]
    date_preset = date_filters["date_preset"]

    metrics = _compute_dashboard_metrics(
        shop_db=shop_db,
        shop=shop,
        created_from=created_from,
        created_to_exclusive=created_to_exclusive,
        date_preset=date_preset,
    )

    return jsonify({"ok": True, **metrics})


@dashboard_bp.get("/dashboard/api/metrics/<block_name>")
@login_required
@permission_required("dashboard.view")
def dashboard_metrics_block_api(block_name: str):
    shop_db, shop = _get_active_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop database not configured for this shop."}), 400

    if block_name not in DASHBOARD_BLOCK_NAMES:
        return jsonify({"ok": False, "error": "Unknown dashboard block."}), 404

    date_filters = _get_date_range_filters(request.args)
    created_from = date_filters["created_from"]
    created_to_exclusive = date_filters["created_to_exclusive"]
    date_preset = date_filters["date_preset"]

    metrics = _compute_dashboard_block_metrics(
        block_name,
        shop_db=shop_db,
        shop=shop,
        created_from=created_from,
        created_to_exclusive=created_to_exclusive,
        date_preset=date_preset,
    )
    return jsonify({"ok": True, "block": block_name, "data": metrics})


@dashboard_bp.post("/dashboard/api/goals")
@login_required
@permission_required("dashboard.view")
def dashboard_goals_save_api():
    shop_db, shop = _get_active_shop_db()
    if shop is None:
        return jsonify({"ok": False, "error": "Shop database not configured for this shop."}), 400

    payload = request.get_json(silent=True) or {}
    cleaned = _save_dashboard_goals(shop, payload)
    return jsonify({"ok": True, "goals_monthly": cleaned})
