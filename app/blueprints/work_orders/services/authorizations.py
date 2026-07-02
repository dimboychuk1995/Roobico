"""
Публичная авторизация работ клиентом: токены (master.work_order_authorizations),
резолв токена в (auth_doc, shop_db, shop, wo), контекст страницы подтверждения.
"""
from __future__ import annotations

from app.extensions import get_master_db, get_mongo_client
from app.blueprints.work_orders.services.common import format_dt_label, oid, round2
from app.blueprints.work_orders.services.pdf_contexts import _build_wo_pdf_context


# Approximate cap for total attachment payload before base64 encoding.
# Resend allows ~40MB per email; base64 inflates ~33%, so cap raw bytes at 20MB.
_AUTH_EMAIL_ATTACHMENT_BYTES_CAP = 20 * 1024 * 1024


def _authorizations_collection():
    """Authorizations live in master DB so anonymous public routes can resolve
    a token without knowing the shop."""
    return get_master_db().work_order_authorizations


def _load_authorization_attachments(shop_db, wo, scope: str, labor_index):
    """Return a list of attachment dicts to embed in the authorization email.

    Includes WO-level attachments and all labor-block attachments for the WO.
    Honours an overall size cap to keep the email deliverable.
    """
    col = shop_db.attachments
    wo_id = wo.get("_id")
    if not wo_id:
        return []

    cursor = col.find(
        {
            "entity_type": {"$in": ["work_order", "work_order_labor"]},
            "entity_id": wo_id,
        },
    ).sort("uploaded_at", 1)

    files = []
    total_bytes = 0
    for doc in cursor:
        data = doc.get("data")
        if not data:
            continue
        try:
            raw = bytes(data)
        except Exception:
            continue
        size = len(raw)
        if total_bytes + size > _AUTH_EMAIL_ATTACHMENT_BYTES_CAP:
            continue
        total_bytes += size
        files.append({
            "filename": doc.get("filename") or "attachment",
            "data": raw,
            "content_type": doc.get("content_type") or "application/octet-stream",
            "size": size,
        })
    return files


def _build_authorization_context(shop_db, shop, wo, scope: str, labor_index):
    """Return dict for both email + public page templates.

    scope: "work_order" or "labor".
    labor_index: int when scope == "labor", else None.
    """
    ctx = _build_wo_pdf_context(shop_db, shop, wo)
    ctx["scope"] = scope
    ctx["labor_index"] = labor_index
    if scope == "labor" and isinstance(labor_index, int):
        labors = ctx.get("labors") or []
        if 0 <= labor_index < len(labors):
            single = labors[labor_index]
            ctx["single_labor"] = single
            ctx["single_labor_number"] = labor_index + 1
        else:
            ctx["single_labor"] = None
            ctx["single_labor_number"] = labor_index + 1 if isinstance(labor_index, int) else None
    ctx["attachment_files"] = _load_authorization_attachments(shop_db, wo, scope, labor_index)
    return ctx


def _resolve_token(token):
    """Return (auth_doc, shop_db, shop, wo, error_message)."""
    if not token or not isinstance(token, str) or len(token) < 16:
        return None, None, None, None, "Invalid authorization link"

    auth_col = _authorizations_collection()
    auth_doc = auth_col.find_one({"token": token})
    if not auth_doc:
        return None, None, None, None, "This authorization link is invalid or has expired."

    master = get_master_db()
    shop = master.shops.find_one({"_id": auth_doc.get("shop_id")})
    if not shop:
        return auth_doc, None, None, None, "Shop is no longer available."

    db_name = auth_doc.get("db_name") or shop.get("db_name")
    if not db_name:
        return auth_doc, None, shop, None, "Shop database is not configured."

    client = get_mongo_client()
    shop_db = client[str(db_name)]
    wo = shop_db.work_orders.find_one({"_id": auth_doc.get("work_order_id"),
                                       "is_active": True})
    if not wo:
        return auth_doc, shop_db, shop, None, "Work order not found."

    return auth_doc, shop_db, shop, wo, None
