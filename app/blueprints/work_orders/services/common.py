"""
Общие скалярные и датовые хелперы work orders.

Часть распила routes.py на сервисный слой: здесь функции без Flask-контекста
(преобразования типов, округление, диапазоны дат для фильтров).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bson import ObjectId

from app.utils.date_filters import build_date_range_filters
from app.utils.display_datetime import format_date_mmddyyyy, format_preferred_shop_date


def utcnow():
    return datetime.now(timezone.utc)


def oid(v):
    if not v:
        return None
    try:
        return ObjectId(str(v))
    except Exception:
        return None


def i32(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return int(s)
    except Exception:
        return None


def f64(v):
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def round2(v):
    n = f64(v)
    if n is None:
        return 0.0
    return round(n + 1e-12, 2)


def as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if v is None:
        return False
    raw = str(v).strip().lower()
    return raw in ("1", "true", "yes", "on")


def format_dt_label(dt):
    return format_date_mmddyyyy(dt)


def _fmt_dt_iso(dt):
    if isinstance(dt, datetime):
        try:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            return dt.isoformat()
    return ""


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


def get_date_range_filters(args, from_key: str = "date_from", to_key: str = "date_to", preset_key: str = "date_preset"):
    return build_date_range_filters(args, from_key=from_key, to_key=to_key, preset_key=preset_key)


def append_and_filter(query: dict, extra_filter: dict):
    if not extra_filter:
        return query
    return {"$and": [query, extra_filter]}


def build_created_at_range_filter(created_from=None, created_to_exclusive=None):
    created_filter = {}
    if created_from:
        created_filter["$gte"] = created_from
    if created_to_exclusive:
        created_filter["$lt"] = created_to_exclusive
    if not created_filter:
        return None
    return {"created_at": created_filter}


def build_preferred_date_range_filter(date_field: str, created_from=None, created_to_exclusive=None):
    created_filter = build_created_at_range_filter(created_from, created_to_exclusive)
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


def format_preferred_date_label(primary_dt, fallback_dt):
    return format_preferred_shop_date(primary_dt, fallback=fallback_dt)
