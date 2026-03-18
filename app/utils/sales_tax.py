from __future__ import annotations

import re
from datetime import datetime, timezone

from bson import ObjectId


US_ZIP_REGEX = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


def extract_us_zip(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    match = US_ZIP_REGEX.search(text)
    if not match:
        return ""
    return match.group(1)


def get_shop_zip_code(shop_doc: dict | None) -> str:
    if not isinstance(shop_doc, dict):
        return ""

    zip_field = extract_us_zip(shop_doc.get("zip"))
    if zip_field:
        return zip_field

    # Legacy-compatible fallback: parse ZIP from free-form address.
    address_value = (
        shop_doc.get("address")
        or shop_doc.get("address_line")
        or ""
    )
    return extract_us_zip(address_value)


def get_zip_sales_tax_rate(master_db, zip_code: str) -> dict | None:
    normalized_zip = extract_us_zip(zip_code)
    if not normalized_zip:
        return None

    return master_db.zip_sales_tax_rates.find_one(
        {
            "zip_code": normalized_zip,
            "is_active": {"$ne": False},
        }
    )


def resolve_active_shop_sales_tax_rate(master_db, shop_id: str | ObjectId | None) -> dict | None:
    if not shop_id:
        return None

    shop_oid = None
    if isinstance(shop_id, ObjectId):
        shop_oid = shop_id
    else:
        try:
            shop_oid = ObjectId(str(shop_id))
        except Exception:
            return None

    shop = master_db.shops.find_one({"_id": shop_oid})
    zip_code = get_shop_zip_code(shop)
    if not zip_code:
        return None

    return get_zip_sales_tax_rate(master_db, zip_code)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
