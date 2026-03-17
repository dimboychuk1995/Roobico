from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.utils.display_datetime import get_active_shop_timezone_name


_ALLOWED_PRESETS = {
    "all_time",
    "custom",
    "today",
    "yesterday",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
    "this_quarter",
    "last_quarter",
    "this_year",
    "last_year",
}


def _safe_tzinfo(tz_name: str):
    # Keep this helper local so date filters stay robust even if tzdata is unavailable.
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz_name)
    except Exception:
        if tz_name in ("America/Chicago", "US/Central"):
            return timezone(timedelta(hours=-6))
        if tz_name in ("UTC", "Etc/UTC"):
            return timezone.utc
        return timezone(timedelta(hours=-6))


def _to_iso_date(value):
    if not value:
        return ""
    return value.strftime("%Y-%m-%d")


def _parse_iso_local_date(value: str):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except Exception:
        return None


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


def _local_day_start_to_utc(day_value, tzinfo):
    if day_value is None:
        return None
    return datetime.combine(day_value, datetime.min.time(), tzinfo=tzinfo).astimezone(timezone.utc)


def build_date_range_filters(args, from_key: str = "date_from", to_key: str = "date_to", preset_key: str = "date_preset", default_preset: str = "this_month"):
    date_from_raw = (args.get(from_key) or "").strip()
    date_to_raw = (args.get(to_key) or "").strip()
    preset_raw = (args.get(preset_key) or "").strip().lower()
    search_raw = (args.get("q") or "").strip()

    # If user is searching and did not explicitly touch date controls,
    # default to all-time to avoid unintentionally narrowing search to this week.
    explicit_date_filters = bool((args.get(preset_key) or "").strip() or date_from_raw or date_to_raw)
    if search_raw and not explicit_date_filters:
        preset_raw = "all_time"

    if preset_raw not in _ALLOWED_PRESETS:
        preset_raw = default_preset

    tz_name = get_active_shop_timezone_name()
    tzinfo = _safe_tzinfo(tz_name)
    local_today = datetime.now(tzinfo).date()

    if preset_raw == "all_time":
        date_from = ""
        date_to = ""
    elif preset_raw == "custom":
        date_from = date_from_raw
        date_to = date_to_raw
        if not date_from and not date_to:
            preset_raw = default_preset
            start_date, end_date = _date_range_for_preset(preset_raw, local_today)
            date_from = _to_iso_date(start_date)
            date_to = _to_iso_date(end_date)
    else:
        start_date, end_date = _date_range_for_preset(preset_raw, local_today)
        date_from = _to_iso_date(start_date)
        date_to = _to_iso_date(end_date)

    from_date = _parse_iso_local_date(date_from)
    to_date = _parse_iso_local_date(date_to)

    if from_date and to_date and from_date > to_date:
        from_date, to_date = to_date, from_date
        date_from, date_to = date_to, date_from

    created_from = _local_day_start_to_utc(from_date, tzinfo)
    created_to_exclusive = None
    if to_date is not None:
        created_to_exclusive = _local_day_start_to_utc(to_date + timedelta(days=1), tzinfo)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "date_preset": preset_raw,
        "created_from": created_from,
        "created_to_exclusive": created_to_exclusive,
    }
