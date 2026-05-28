"""Per-shop storage for 3rd-party integration configurations.

Documents live in the shop database, in the ``integrations`` collection.
Schema::

    {
        "_id": ObjectId,
        "shop_id": ObjectId,
        "provider": "uattend",          # lowercase provider key
        "enabled": bool,
        "label": str,                   # optional human label
        "api_key_encrypted": str,       # Fernet token (never plaintext)
        "api_key_last4": str,           # for UI masking without decryption
        "last_sync_at": datetime | None,
        "last_sync_status": "ok" | "error" | None,
        "last_sync_error": str | None,
        "last_tested_at": datetime | None,
        "last_test_status": "ok" | "error" | None,
        "last_test_error": str | None,
        "created_at": datetime,
        "updated_at": datetime,
        "created_by": ObjectId | None,
        "updated_by": ObjectId | None,
    }

Unique index: (shop_id, provider).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from pymongo.errors import OperationFailure

from .crypto import encrypt_secret, decrypt_secret, mask_secret


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_indexes(shop_db) -> None:
    """Create the unique index on (shop_id, provider). Idempotent."""
    try:
        shop_db.integrations.create_index(
            [("shop_id", 1), ("provider", 1)],
            unique=True,
            name="uniq_integrations_shop_provider",
        )
    except OperationFailure:
        # Index may already exist with a different name — non-fatal.
        pass


def get_integration(shop_db, shop_id: ObjectId, provider: str) -> Optional[dict]:
    """Return the raw integration document, or None."""
    return shop_db.integrations.find_one(
        {"shop_id": shop_id, "provider": provider.lower()}
    )


def get_integration_public(shop_db, shop_id: ObjectId, provider: str) -> dict:
    """Return a UI-safe representation (no plaintext secret)."""
    doc = get_integration(shop_db, shop_id, provider) or {}
    if not doc:
        return {
            "provider": provider.lower(),
            "configured": False,
            "enabled": False,
            "label": "",
            "api_key_masked": "",
            "last_sync_at": None,
            "last_sync_status": None,
            "last_sync_error": None,
            "last_tested_at": None,
            "last_test_status": None,
            "last_test_error": None,
        }
    last4 = doc.get("api_key_last4") or ""
    return {
        "provider": doc.get("provider"),
        "configured": True,
        "enabled": bool(doc.get("enabled")),
        "label": doc.get("label") or "",
        "api_key_masked": ("•" * 8 + last4) if last4 else "•" * 8,
        "last_sync_at": doc.get("last_sync_at"),
        "last_sync_status": doc.get("last_sync_status"),
        "last_sync_error": doc.get("last_sync_error"),
        "last_tested_at": doc.get("last_tested_at"),
        "last_test_status": doc.get("last_test_status"),
        "last_test_error": doc.get("last_test_error"),
    }


def get_decrypted_api_key(shop_db, shop_id: ObjectId, provider: str) -> str:
    """Decrypt and return the stored api key. Returns '' if not configured."""
    doc = get_integration(shop_db, shop_id, provider)
    if not doc:
        return ""
    token = doc.get("api_key_encrypted") or ""
    if not token:
        return ""
    return decrypt_secret(token)


def save_integration(
    shop_db,
    shop_id: ObjectId,
    provider: str,
    *,
    api_key: Optional[str] = None,
    label: Optional[str] = None,
    enabled: Optional[bool] = None,
    actor_user_id: Optional[ObjectId] = None,
) -> dict:
    """Upsert an integration.

    If ``api_key`` is None / empty, the existing key is kept (allows editing
    label/enabled without re-typing the secret). If provided, it is encrypted
    and overwrites the old one.
    """
    provider = provider.lower().strip()
    existing = get_integration(shop_db, shop_id, provider)

    now = _utcnow()
    update: dict = {"updated_at": now}
    insert: dict = {
        "shop_id": shop_id,
        "provider": provider,
        "created_at": now,
        "created_by": actor_user_id,
    }

    if actor_user_id is not None:
        update["updated_by"] = actor_user_id

    if label is not None:
        update["label"] = str(label).strip()

    if enabled is not None:
        update["enabled"] = bool(enabled)
    elif not existing:
        # New record defaults to enabled=True if not specified.
        update["enabled"] = True

    if api_key:
        plaintext = str(api_key).strip()
        update["api_key_encrypted"] = encrypt_secret(plaintext)
        update["api_key_last4"] = plaintext[-4:] if len(plaintext) >= 4 else plaintext
        # New key invalidates previous test state.
        update["last_tested_at"] = None
        update["last_test_status"] = None
        update["last_test_error"] = None
    elif not existing:
        # Cannot create a brand-new integration without a key.
        raise ValueError("API key is required when creating an integration.")

    shop_db.integrations.update_one(
        {"shop_id": shop_id, "provider": provider},
        {"$set": update, "$setOnInsert": insert},
        upsert=True,
    )
    return get_integration(shop_db, shop_id, provider) or {}


def delete_integration(shop_db, shop_id: ObjectId, provider: str) -> bool:
    res = shop_db.integrations.delete_one(
        {"shop_id": shop_id, "provider": provider.lower()}
    )
    return res.deleted_count > 0


def update_test_result(
    shop_db,
    shop_id: ObjectId,
    provider: str,
    *,
    ok: bool,
    error: Optional[str] = None,
) -> None:
    shop_db.integrations.update_one(
        {"shop_id": shop_id, "provider": provider.lower()},
        {
            "$set": {
                "last_tested_at": _utcnow(),
                "last_test_status": "ok" if ok else "error",
                "last_test_error": (error or "")[:500] if not ok else None,
                "updated_at": _utcnow(),
            }
        },
    )


def update_sync_result(
    shop_db,
    shop_id: ObjectId,
    provider: str,
    *,
    ok: bool,
    error: Optional[str] = None,
) -> None:
    shop_db.integrations.update_one(
        {"shop_id": shop_id, "provider": provider.lower()},
        {
            "$set": {
                "last_sync_at": _utcnow(),
                "last_sync_status": "ok" if ok else "error",
                "last_sync_error": (error or "")[:500] if not ok else None,
                "updated_at": _utcnow(),
            }
        },
    )
