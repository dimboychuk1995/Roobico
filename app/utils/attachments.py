"""
Attachments utility — upload / list / download / delete files stored in MongoDB.

Each file is stored as a document in the shop-level `attachments` collection
with the binary payload in a `data` field (BSON Binary).

Supported MIME types: images (jpeg, png, gif, webp, bmp, tiff, svg)
                     and PDF (application/pdf).

Max single file size: 16 MB (GridFS not needed for typical photos/invoices).
"""
from __future__ import annotations

import mimetypes
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId, Binary
from pymongo.collection import Collection


MAX_FILE_SIZE = 16 * 1024 * 1024  # 16 MB

ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "image/svg+xml",
    "application/pdf",
}

ENTITY_TYPES = {
    "part",
    "parts_order",
    "parts_order_payment",
    "core",
    "core_return",
    "vendor",
    "customer",
    "unit",
    "customer_payment",
    "work_order",
    "work_order_labor",
    "work_order_payment",
}


def _oid(value) -> Optional[ObjectId]:
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _utcnow():
    return datetime.now(timezone.utc)


def _guess_content_type(filename: str) -> str:
    ct, _ = mimetypes.guess_type(filename or "")
    return ct or "application/octet-stream"


def validate_upload(file_storage) -> Optional[str]:
    """Return an error string or None if the file is acceptable."""
    if not file_storage or not file_storage.filename:
        return "No file selected."

    ct = file_storage.content_type or _guess_content_type(file_storage.filename)
    if ct not in ALLOWED_CONTENT_TYPES:
        return f"File type '{ct}' is not allowed. Only images and PDFs are accepted."

    # Read to check size, then seek back
    file_storage.seek(0, 2)
    size = file_storage.tell()
    file_storage.seek(0)

    if size > MAX_FILE_SIZE:
        mb = MAX_FILE_SIZE // (1024 * 1024)
        return f"File is too large ({size:,} bytes). Maximum allowed: {mb} MB."

    if size == 0:
        return "File is empty."

    return None


def save_attachment(
    col: Collection,
    *,
    entity_type: str,
    entity_id,
    file_storage,
    uploaded_by,
    parent_id=None,
    shop_id=None,
) -> dict:
    """
    Persist one uploaded file to the attachments collection.
    Returns the inserted document (without binary data — for JSON response).
    """
    entity_oid = _oid(entity_id)
    if not entity_oid:
        raise ValueError("Invalid entity_id")
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"Unknown entity_type: {entity_type}")

    filename = file_storage.filename
    ct = file_storage.content_type or _guess_content_type(filename)

    file_storage.seek(0, 2)
    size = file_storage.tell()
    file_storage.seek(0)
    raw = file_storage.read()

    doc = {
        "entity_type": entity_type,
        "entity_id": entity_oid,
        "filename": filename,
        "content_type": ct,
        "size": size,
        "data": Binary(raw),
        "uploaded_by": _oid(uploaded_by),
        "uploaded_at": _utcnow(),
    }
    if parent_id:
        doc["parent_id"] = _oid(parent_id)
    if shop_id:
        doc["shop_id"] = _oid(shop_id)

    result = col.insert_one(doc)
    doc["_id"] = result.inserted_id

    # Return a lightweight version (no binary)
    return _attachment_to_dict(doc)


def list_attachments(col: Collection, entity_type: str, entity_id, parent_id=None) -> list[dict]:
    """Return all attachments for an entity, sorted newest-first. No binary data."""
    entity_oid = _oid(entity_id)
    if not entity_oid:
        return []

    query = {"entity_type": entity_type, "entity_id": entity_oid}
    if parent_id:
        query["parent_id"] = _oid(parent_id)

    cursor = col.find(query, {"data": 0}).sort("uploaded_at", -1)
    return [_attachment_to_dict(doc) for doc in cursor]


def get_attachment(col: Collection, attachment_id) -> Optional[dict]:
    """Return full attachment document (WITH binary data) or None."""
    oid = _oid(attachment_id)
    if not oid:
        return None
    return col.find_one({"_id": oid})


def delete_attachment(col: Collection, attachment_id) -> bool:
    """Delete one attachment by _id. Returns True if deleted."""
    oid = _oid(attachment_id)
    if not oid:
        return False
    result = col.delete_one({"_id": oid})
    return result.deleted_count > 0


def _attachment_to_dict(doc: dict) -> dict:
    """Convert a Mongo attachment doc to a JSON-safe dict (no binary)."""
    return {
        "id": str(doc["_id"]),
        "entity_type": doc.get("entity_type", ""),
        "entity_id": str(doc.get("entity_id", "")),
        "parent_id": str(doc["parent_id"]) if doc.get("parent_id") else None,
        "filename": doc.get("filename", ""),
        "content_type": doc.get("content_type", ""),
        "size": doc.get("size", 0),
        "uploaded_at": doc["uploaded_at"].isoformat() if doc.get("uploaded_at") else None,
        "is_image": (doc.get("content_type") or "").startswith("image/"),
    }
