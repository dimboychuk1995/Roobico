from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from bson import ObjectId
from flask import g, has_request_context, session

from app.extensions import get_master_db, get_mongo_client

DEFAULT_TIMEZONE = "America/Chicago"


def _oid(value):
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _tenant_id_variants():
    raw = session.get("tenant_id")
    out = set()
    if raw is None:
        return []
    out.add(raw)
    out.add(str(raw))
    oid = _oid(raw)
    if oid:
        out.add(oid)
    return list(out)


def _extract_tz(doc):
    if not isinstance(doc, dict):
        return ""
    value = str(doc.get("timezone") or "").strip()
    return value


def _safe_tzinfo(tz_name: str):
    """
    Return a tzinfo object without throwing ZoneInfoNotFoundError.
    Prefers IANA zone, falls back to fixed offsets for critical defaults.
    """
    name = str(tz_name or "").strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except Exception:
        if name in ("America/Chicago", "US/Central"):
            # Fallback when tzdata is unavailable.
            return timezone(timedelta(hours=-6))
        if name in ("UTC", "Etc/UTC"):
            return timezone.utc
        # Unknown zone without tzdata: use Chicago default fallback.
        return timezone(timedelta(hours=-6))


def _get_active_shop(master):
    shop_id_raw = session.get("shop_id")
    shop_oid = _oid(shop_id_raw)
    if not shop_oid:
        return None

    tenant_variants = _tenant_id_variants()
    if not tenant_variants:
        return None

    return master.shops.find_one({"_id": shop_oid, "tenant_id": {"$in": tenant_variants}})


def get_active_shop_timezone_name(default: str = DEFAULT_TIMEZONE) -> str:
    if not has_request_context():
        return default

    cached = getattr(g, "_active_shop_timezone", None)
    if cached:
        return cached

    tz_name = default

    try:
        master = get_master_db()
        shop = _get_active_shop(master)

        if shop:
            shop_oid = shop.get("_id")
            shop_id_str = str(shop_oid)
            tenant_id = shop.get("tenant_id")

            # 1) Active shop DB timezone_location
            db_name = (
                shop.get("db_name")
                or shop.get("database")
                or shop.get("db")
                or shop.get("mongo_db")
                or shop.get("shop_db")
            )
            if db_name:
                shop_db = get_mongo_client()[str(db_name)]
                tz_doc = shop_db.timezone_location.find_one(
                    {
                        "is_active": {"$ne": False},
                        "$or": [
                            {"shop_id": shop_oid},
                            {"shop_id": shop_id_str},
                            {"location_id": shop_oid},
                            {"location_id": shop_id_str},
                        ],
                    },
                    {"timezone": 1, "updated_at": 1, "created_at": 1},
                    sort=[("updated_at", -1), ("created_at", -1)],
                )
                tz_name = _extract_tz(tz_doc) or tz_name

            # 2) master DB timezone_location (shop_id must match active shop id)
            if tz_name == default:
                tz_doc = master.timezone_location.find_one(
                    {
                        "is_active": {"$ne": False},
                        "$or": [
                            {"shop_id": shop_oid},
                            {"shop_id": shop_id_str},
                        ],
                    },
                    {"timezone": 1, "updated_at": 1, "created_at": 1},
                    sort=[("updated_at", -1), ("created_at", -1)],
                )
                tz_name = _extract_tz(tz_doc) or tz_name
    except Exception:
        tz_name = default

    g._active_shop_timezone = tz_name
    return tz_name


def to_active_shop_datetime(value):
    if not isinstance(value, datetime):
        return None

    tz_name = get_active_shop_timezone_name()
    tz = _safe_tzinfo(tz_name)

    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    try:
        return dt.astimezone(tz)
    except Exception:
        return dt


def format_date_mmddyyyy(value, default: str = "-") -> str:
    dt = to_active_shop_datetime(value)
    if not dt:
        return default
    return dt.strftime("%m/%d/%Y")
