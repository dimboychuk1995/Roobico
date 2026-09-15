"""Parts orders from the location's AI-monitored email inbox.

Pipeline (see app/help/parts_orders.md → "Orders from email"):

    webhook (raw MIME) ──▶ store_inbound_email()  ──▶ inbound_emails{status=received}
                                                        │
    cron / inline thread ─▶ process_inbound_email()  ◀──┘
        1. PDFs first (AI)    → every PDF/image is read by the vision invoice parser; an order
                                document with lines decides "it IS an order", a quote/statement
                                decides "it is NOT" — the body never overrides a PDF
        2. body (AI)          → only without a decisive attachment: classify the text, then read
                                the lines from it; not an order → status=ignored (kept in the list)
        3. vendor            → match by sender / name, else create the vendor automatically
        4. parts              → match by part number; the rest goes to `unmatched_items`
        5. dedup              → same vendor + same vendor order number → link to that order
        6. mode=auto          → parts order with needs_confirmation=True (+ push to the office)
           mode=suggest       → status=suggested, a human clicks "Create order"

Nothing here touches money flows: an unconfirmed order cannot be received,
paid or returned until someone confirms it (`confirm_email_order`).
"""
from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from bson.binary import Binary
from werkzeug.datastructures import FileStorage

from app.utils.attachments import save_attachment
from app.utils.contacts import build_contacts_from_payload, build_vendor_legacy_contact_fields
from app.utils.integrations.email_inbox import (
    MODE_SUGGEST,
    bump_email_orders_stats,
    get_email_orders_settings,
)
from app.utils.push_notifications import office_user_ids_for_shop, send_push_to_users
from app.utils.tenant import shop_db_name

from . import email_orders_ai as ai

logger = logging.getLogger(__name__)

# inbound_emails.status
STATUS_RECEIVED = "received"        # stored, waiting for processing
STATUS_PROCESSING = "processing"    # locked by a worker
STATUS_IGNORED = "ignored"          # AI: not a parts order (kept in the list)
STATUS_SUGGESTED = "suggested"      # extracted, waiting for a human (mode=suggest)
STATUS_ORDER_CREATED = "order_created"
STATUS_LINKED = "linked"            # attached to an already existing order
STATUS_REJECTED = "rejected"        # the created order was rejected by a human
STATUS_ERROR = "error"

PENDING_STATUSES = (STATUS_RECEIVED,)
RETRYABLE_STATUSES = (STATUS_RECEIVED, STATUS_ERROR)
FORCEABLE_STATUSES = (STATUS_RECEIVED, STATUS_IGNORED, STATUS_SUGGESTED, STATUS_ERROR, STATUS_REJECTED)

PROCESSING_STALE_AFTER = timedelta(minutes=15)
MAX_AUTO_ATTEMPTS = 3
ATTACHMENT_DATA_RETENTION = timedelta(days=30)
# Every PDF/image is read: the email body often says nothing ("see attached")
# while the PDF is the actual order. A sane cap against zip-bomb style mails.
MAX_ATTACHMENTS_TO_EXTRACT = 10
LINK_LOOKBACK = timedelta(days=21)

GENERIC_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com",
    "msn.com", "icloud.com", "me.com", "aol.com", "comcast.net", "att.net", "protonmail.com",
    "proton.me", "sbcglobal.net", "verizon.net", "mail.com", "ymail.com",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ci_exact(value: str) -> dict | None:
    value = str(value or "").strip()
    if not value:
        return None
    return {"$regex": f"^{re.escape(value)}$", "$options": "i"}


def _sender_domain(email_addr: str) -> str:
    addr = str(email_addr or "").strip().lower()
    return addr.rsplit("@", 1)[1] if "@" in addr else ""


def _norm_pn(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


# ─────────────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────────────

def store_inbound_email(
    shop_db,
    shop: dict,
    parsed: dict,
    *,
    envelope_from: str = "",
    envelope_to: str = "",
    provider: str = "cloudflare",
) -> tuple[Optional[dict], bool]:
    """Insert the parsed message. Returns (doc, created). A message id seen
    before for this shop is not inserted again (webhook retries)."""
    now = _utcnow()
    message_id = str(parsed.get("message_id") or "").strip()
    if not message_id:
        # No Message-ID header (rare, broken senders): synthesize a stable one.
        message_id = f"synthetic:{ObjectId()}"

    existing = shop_db.inbound_emails.find_one(
        {"shop_id": shop["_id"], "message_id": message_id}, {"_id": 1, "status": 1}
    )
    if existing:
        return existing, False

    attachments = []
    for att in parsed.get("attachments") or []:
        attachments.append({
            "filename": str(att.get("filename") or "attachment")[:200],
            "content_type": str(att.get("content_type") or "application/octet-stream"),
            "size": int(att.get("size") or 0),
            "data": Binary(att.get("data") or b""),
        })

    doc = {
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "provider": provider,
        "message_id": message_id,
        "envelope_from": str(envelope_from or "").strip().lower()[:320],
        "envelope_to": str(envelope_to or "").strip().lower()[:320],
        "from_email": str(parsed.get("from_email") or "")[:320],
        "from_name": str(parsed.get("from_name") or "")[:200],
        # Manual "Fwd:" by the shop: the vendor is in the forwarded block, not in From.
        "forwarded_from_email": str(parsed.get("forwarded_from_email") or "")[:320],
        "forwarded_from_name": str(parsed.get("forwarded_from_name") or "")[:200],
        "to": list(parsed.get("to") or [])[:20],
        "cc": list(parsed.get("cc") or [])[:20],
        "subject": str(parsed.get("subject") or "")[:500],
        "email_date": parsed.get("date"),
        "text": str(parsed.get("text") or ""),
        "attachments": attachments,
        "skipped_attachments": list(parsed.get("skipped_attachments") or [])[:20],
        "status": STATUS_RECEIVED,
        "attempts": 0,
        "classification": None,
        "extracted": None,
        "parts_order_id": None,
        "error": None,
        "received_at": now,
        "created_at": now,
        "updated_at": now,
    }
    res = shop_db.inbound_emails.insert_one(doc)
    doc["_id"] = res.inserted_id
    bump_email_orders_stats(shop_db, shop["_id"], emails=1)
    return doc, True


def serialize_inbound_email(doc: dict, *, orders_map: dict | None = None) -> dict:
    """UI-safe projection (no attachment bytes)."""
    order = (orders_map or {}).get(doc.get("parts_order_id")) if doc.get("parts_order_id") else None
    classification = doc.get("classification") or {}
    extracted = doc.get("extracted") or {}
    return {
        "id": str(doc.get("_id")),
        "received_at": doc.get("received_at").isoformat() if isinstance(doc.get("received_at"), datetime) else None,
        "from_email": doc.get("from_email") or "",
        "from_name": doc.get("from_name") or "",
        "subject": doc.get("subject") or "",
        "status": doc.get("status") or "",
        "kind": classification.get("kind") or "",
        "confidence": classification.get("confidence"),
        "reason": classification.get("reason") or "",
        "vendor_name": (doc.get("vendor") or {}).get("name") or extracted.get("vendor_name") or classification.get("vendor_name") or "",
        "vendor_ref": extracted.get("invoice_number") or classification.get("order_reference") or "",
        "items_count": len(extracted.get("items") or []),
        "attachments": [
            {"filename": a.get("filename"), "content_type": a.get("content_type"), "size": a.get("size")}
            for a in (doc.get("attachments") or [])
        ],
        "parts_order_id": str(doc.get("parts_order_id")) if doc.get("parts_order_id") else "",
        "order_number": (order or {}).get("order_number") if order else None,
        "order_active": bool((order or {}).get("is_active", True)) if order else None,
        "error": doc.get("error") or "",
        "processed_at": doc.get("processed_at").isoformat() if isinstance(doc.get("processed_at"), datetime) else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Matching helpers
# ─────────────────────────────────────────────────────────────────────────────

def own_addresses(master, shop: dict) -> tuple[set[str], set[str]]:
    """Emails and (non-generic) domains that belong to the shop itself:
    tenant users, the shop and tenant contact emails. Mail coming from these
    is the shop forwarding something — never a vendor."""
    from app.blueprints.work_orders.services.lookups import _tenant_variants_from_shop

    emails: set[str] = set()
    variants = _tenant_variants_from_shop(shop)
    if variants:
        for u in master.users.find({"tenant_id": {"$in": variants}}, {"email": 1}):
            if u.get("email"):
                emails.add(str(u["email"]).strip().lower())
        tenant = master.tenants.find_one({"_id": {"$in": variants}}, {"email": 1, "contact_email": 1})
        for key in ("email", "contact_email"):
            if tenant and tenant.get(key):
                emails.add(str(tenant[key]).strip().lower())
    for key in ("email", "contact_email"):
        if shop.get(key):
            emails.add(str(shop[key]).strip().lower())
    domains = {_sender_domain(e) for e in emails}
    domains = {d for d in domains if d and d not in GENERIC_EMAIL_DOMAINS}
    return emails, domains


def vendor_sender_for(email_doc: dict, own_emails: set[str], own_domains: set[str]) -> tuple[str, str]:
    """(email, name) of the party that actually wrote the message — the
    original sender of a manual forward, or From — unless it is the shop
    itself, in which case ('', '') so the sender is not used for matching
    or learning."""
    candidates = [
        (str(email_doc.get("forwarded_from_email") or "").strip().lower(), str(email_doc.get("forwarded_from_name") or "")),
        (str(email_doc.get("from_email") or "").strip().lower(), str(email_doc.get("from_name") or "")),
    ]
    for addr, name in candidates:
        if not addr:
            continue
        if addr in own_emails or _sender_domain(addr) in own_domains:
            continue
        return addr, name
    return "", ""


_NAME_NOISE = {"inc", "llc", "ltd", "co", "corp", "company", "the", "and", "of"}
MAX_AI_VENDOR_CANDIDATES = 300


def _norm_name(value: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", str(value or "").lower())
    return " ".join(w for w in s.split() if w not in _NAME_NOISE)


def _unique(vendors_coll, query: dict) -> Optional[dict]:
    """The match only when exactly one vendor satisfies it — a portal address
    (repairlink@oeconnection.com) shared by several dealers is ambiguous."""
    docs = list(vendors_coll.find(query).limit(2))
    return docs[0] if len(docs) == 1 else None


def match_vendor(vendors_coll, shop_id: ObjectId, *, sender_email: str, vendor_name: str) -> Optional[dict]:
    """Vendor for an email: exact name → one name contained in the other
    ("Hawk Ford" ⊂ "Hawk Ford of St. Charles Pro Elite…") → unique learned
    sender / domain → AI over the shop's vendor list. Name beats sender
    because dealer portals send for many vendors from one address."""
    base = {"shop_id": shop_id, "is_active": {"$ne": False}}
    name = str(vendor_name or "").strip()
    sender = str(sender_email or "").strip().lower()

    vendors = list(vendors_coll.find(base, {"name": 1}).limit(5000))
    if name:
        doc = vendors_coll.find_one({**base, "name": _ci_exact(name)})
        if doc:
            return doc
        wanted = _norm_name(name)
        if len(wanted) >= 4:
            contained: list[tuple[int, ObjectId]] = []
            for v in vendors:
                have = _norm_name(v.get("name"))
                if len(have) < 4:
                    continue
                if have == wanted:
                    return vendors_coll.find_one({"_id": v["_id"]})
                if f" {have} " in f" {wanted} " or f" {wanted} " in f" {have} ":
                    contained.append((abs(len(have) - len(wanted)), v["_id"]))
            if contained:
                contained.sort(key=lambda t: t[0])
                return vendors_coll.find_one({"_id": contained[0][1]})

    if sender:
        doc = _unique(vendors_coll, {**base, "email_senders": sender})
        if doc:
            return doc
        doc = _unique(vendors_coll, {**base, "$or": [{"email": sender}, {"contacts.email": sender}]})
        if doc:
            return doc
        domain = _sender_domain(sender)
        if domain and domain not in GENERIC_EMAIL_DOMAINS:
            doc = _unique(vendors_coll, {**base, "email_domains": domain})
            if doc:
                return doc
            domain_re = {"$regex": "@" + re.escape(domain) + "$", "$options": "i"}
            doc = _unique(vendors_coll, {**base, "$or": [{"email": domain_re}, {"contacts.email": domain_re}]})
            if doc:
                return doc

    if name and vendors:
        candidates = vendors[:MAX_AI_VENDOR_CANDIDATES]
        try:
            index = ai.match_vendor_ai(
                vendor_name=name, sender_email=sender,
                candidates=[str(v.get("name") or "") for v in candidates],
            )
        except Exception:  # noqa: BLE001 — AI matching is best effort; fall through to "create"
            logger.exception("email_orders: AI vendor matching failed for %r", name)
            index = None
        if index is not None:
            return vendors_coll.find_one({"_id": candidates[index]["_id"]})
    return None


def remove_auto_vendor_if_unused(shop_db, vendor_id, *, exclude_order_id=None) -> bool:
    """Hard-delete a vendor the inbox created automatically, once nothing
    references it (the user picked another vendor, or rejected the order).
    Only vendors flagged created_from_email are ever removed."""
    if not vendor_id:
        return False
    vendor = shop_db.vendors.find_one({"_id": vendor_id, "created_from_email": True}, {"_id": 1})
    if not vendor:
        return False
    orders_q: dict = {"vendor_id": vendor_id, "is_active": {"$ne": False}}
    if exclude_order_id is not None:
        orders_q["_id"] = {"$ne": exclude_order_id}
    if shop_db.parts_orders.find_one(orders_q, {"_id": 1}):
        return False
    if shop_db.parts.find_one({"vendor_id": vendor_id, "is_active": True}, {"_id": 1}):
        return False
    shop_db.vendors.delete_one({"_id": vendor_id, "created_from_email": True})
    return True


def vendor_changed_on_email_order(shop_db, order: dict, new_vendor_id, *, actor_user_id=None) -> None:
    """Called after a user saved an email-created order with another vendor:
    the auto-created one is removed and the sender is learned for the vendor
    the user chose (so the next email from it matches straight away)."""
    source = order.get("source") or {}
    old_vendor_id = order.get("vendor_id")
    if source.get("kind") != "email" or not new_vendor_id or new_vendor_id == old_vendor_id:
        return
    if source.get("vendor_created"):
        remove_auto_vendor_if_unused(shop_db, old_vendor_id, exclude_order_id=order["_id"])
        shop_db.parts_orders.update_one({"_id": order["_id"]}, {"$set": {"source.vendor_created": False}})
    if source.get("vendor_sender"):
        learn_vendor_sender(shop_db.vendors, new_vendor_id, source.get("vendor_sender"))


def learn_vendor_sender(vendors_coll, vendor_id: ObjectId, sender_email: str) -> None:
    sender = str(sender_email or "").strip().lower()
    if not vendor_id or not sender:
        return
    update: dict = {"$addToSet": {"email_senders": sender}}
    domain = _sender_domain(sender)
    if domain and domain not in GENERIC_EMAIL_DOMAINS:
        update["$addToSet"]["email_domains"] = domain
    vendors_coll.update_one({"_id": vendor_id}, update)


def create_vendor_from_email(vendors_coll, shop: dict, extracted: dict, *, sender_email: str, sender_name: str,
                             vendor_name: str) -> dict:
    now = _utcnow()
    name = (vendor_name or extracted.get("vendor_name") or sender_name or _sender_domain(sender_email) or "Unknown vendor").strip()[:200]
    contact_email = (extracted.get("vendor_email") or sender_email or "").strip().lower()
    contacts = build_contacts_from_payload({"contacts": [{
        "first_name": extracted.get("vendor_contact_first_name") or "",
        "last_name": extracted.get("vendor_contact_last_name") or "",
        "phone": extracted.get("vendor_phone") or "",
        "email": contact_email,
        "is_main": True,
    }]})
    doc = {
        "name": name,
        "website": (extracted.get("vendor_website") or "").strip() or None,
        "address": (extracted.get("vendor_address") or "").strip() or None,
        "contacts": contacts,
        "notes": "Created automatically from an email in the parts orders inbox.",
        "is_active": True,
        "created_from_email": True,
        "created_at": now,
        "updated_at": now,
        "created_by": None,
        "updated_by": None,
        "deactivated_at": None,
        "deactivated_by": None,
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
    }
    doc.update(build_vendor_legacy_contact_fields(contacts))
    res = vendors_coll.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


def match_part(parts_coll, shop_id: ObjectId, part_number: str) -> Optional[dict]:
    pn = str(part_number or "").strip()
    if not pn:
        return None
    base = {"shop_id": shop_id, "is_active": True}
    doc = parts_coll.find_one({**base, "part_number": _ci_exact(pn)})
    if doc:
        return doc
    norm = _norm_pn(pn)
    if len(norm) < 4 or len(norm) > 40:
        return None
    # Same letters/digits, different separators ("acme 100" vs "ACME-100",
    # "DR 8600310" vs "DR-8600310"): allow any non-alphanumerics between chars.
    loose = "^" + r"[^a-z0-9]*".join(re.escape(ch) for ch in norm) + "$"
    for cand in parts_coll.find({**base, "part_number": {"$regex": loose, "$options": "i"}}, {"part_number": 1}).limit(20):
        if _norm_pn(cand.get("part_number")) == norm:
            return parts_coll.find_one({"_id": cand["_id"]})
    return None


def build_order_items(parts_coll, shop_id: ObjectId, extracted_items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split extracted lines into order items (matched to catalog parts) and
    unmatched lines that a human resolves at confirmation time."""
    items: list[dict] = []
    unmatched: list[dict] = []
    by_part: dict[ObjectId, dict] = {}
    for line in extracted_items or []:
        pn = str(line.get("part_number") or "").strip()
        desc = str(line.get("description") or "").strip()
        qty = max(1, int(line.get("quantity") or 1))
        price = max(0.0, round(float(line.get("price") or 0.0), 2))
        part = match_part(parts_coll, shop_id, pn)
        if not part:
            unmatched.append({"part_number": pn, "description": desc, "quantity": qty, "price": price})
            continue
        pid = part["_id"]
        if pid in by_part:
            by_part[pid]["quantity"] += qty
            continue
        item = {
            "part_id": pid,
            "part_number": part.get("part_number"),
            "description": part.get("description") or desc,
            "price": float(price),
            "quantity": int(qty),
            "core_charge": 0.0,
        }
        by_part[pid] = item
        items.append(item)
    return items, unmatched


def find_linkable_order(orders_coll, shop_id: ObjectId, vendor_id: ObjectId, *, vendor_ref: str,
                        items: list[dict]) -> Optional[dict]:
    """An order this email most likely belongs to (confirmation of an order
    the shop already typed in, or an invoice following a confirmation)."""
    base = {"shop_id": shop_id, "vendor_id": vendor_id, "is_active": {"$ne": False}, "is_return": {"$ne": True}}
    ref = str(vendor_ref or "").strip()
    if ref:
        pat = _ci_exact(ref)
        doc = orders_coll.find_one({**base, "$or": [{"vendor_bill": pat}, {"source.vendor_ref": pat}]})
        if doc:
            return doc
    if not items:
        return None
    wanted = {(str(i.get("part_id")), int(i.get("quantity") or 0)) for i in items}
    since = _utcnow() - LINK_LOOKBACK
    for cand in orders_coll.find({**base, "created_at": {"$gte": since}}, {"items": 1}).sort("created_at", -1).limit(50):
        have = {(str(i.get("part_id")), int(i.get("quantity") or 0)) for i in (cand.get("items") or []) if isinstance(i, dict)}
        if have and have == wanted:
            return orders_coll.find_one({"_id": cand["_id"]})
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Attachments → order
# ─────────────────────────────────────────────────────────────────────────────

def _attach_email_files(shop_db, shop_id: ObjectId, order_id: ObjectId, email_doc: dict) -> int:
    saved = 0
    for att in email_doc.get("attachments") or []:
        data = att.get("data")
        if not data:
            continue
        raw = bytes(data)
        fs = FileStorage(
            stream=io.BytesIO(raw),
            filename=str(att.get("filename") or "attachment"),
            content_type=str(att.get("content_type") or "application/octet-stream"),
        )
        try:
            save_attachment(
                shop_db.attachments,
                entity_type="parts_order",
                entity_id=order_id,
                file_storage=fs,
                uploaded_by=None,
                shop_id=shop_id,
            )
            saved += 1
        except Exception:  # noqa: BLE001 — a bad attachment must not lose the order
            logger.exception("email_orders: failed to save attachment %s for order %s", att.get("filename"), order_id)
    return saved


def _drop_attachment_bytes(shop_db, email_id: ObjectId) -> None:
    shop_db.inbound_emails.update_one({"_id": email_id}, {"$unset": {"attachments.$[].data": ""}})


def purge_stale_attachment_data(shop_db, shop_id: ObjectId, *, older_than: timedelta = ATTACHMENT_DATA_RETENTION) -> int:
    cutoff = _utcnow() - older_than
    res = shop_db.inbound_emails.update_many(
        {"shop_id": shop_id, "received_at": {"$lt": cutoff}, "attachments.data": {"$exists": True}},
        {"$unset": {"attachments.$[].data": ""}},
    )
    return int(res.modified_count or 0)


# ─────────────────────────────────────────────────────────────────────────────
# Processing
# ─────────────────────────────────────────────────────────────────────────────

def _lock_email(shop_db, email_id: ObjectId, allowed_statuses: tuple[str, ...]) -> Optional[dict]:
    now = _utcnow()
    return shop_db.inbound_emails.find_one_and_update(
        {"_id": email_id, "status": {"$in": list(allowed_statuses)}},
        {"$set": {"status": STATUS_PROCESSING, "processing_started_at": now, "updated_at": now},
         "$inc": {"attempts": 1}},
        return_document=True,
    )


def _finish(shop_db, email_id: ObjectId, status: str, **fields) -> None:
    now = _utcnow()
    update = {"status": status, "processed_at": now, "updated_at": now, "error": None}
    update.update(fields)
    shop_db.inbound_emails.update_one({"_id": email_id}, {"$set": update, "$unset": {"processing_started_at": ""}})


def _extract_from_attachments(email_doc: dict) -> tuple[dict | None, str, list[dict]]:
    """Read EVERY PDF/image attachment (vision). Returns (best, source, all_results)
    where best is the order document with the most line items, source is
    'attachment:<name>' and all_results keeps (filename, kind, items count)
    for the audit trail."""
    best: dict | None = None
    best_src = ""
    summary: list[dict] = []
    tried = 0
    for att in email_doc.get("attachments") or []:
        if tried >= MAX_ATTACHMENTS_TO_EXTRACT:
            break
        data = att.get("data")
        if not data:
            continue
        tried += 1
        name = att.get("filename")
        try:
            result = ai.extract_order_from_attachment(bytes(data), str(att.get("content_type") or ""))
        except Exception as exc:  # noqa: BLE001 — one unreadable PDF must not kill the whole email
            logger.exception("email_orders: attachment extraction failed (%s)", name)
            summary.append({"filename": name, "error": str(exc)[:200]})
            continue
        kind = result.get("document_kind") or "other"
        n_items = len(result.get("items") or [])
        summary.append({"filename": name, "document_kind": kind, "items": n_items,
                        "invoice_number": result.get("invoice_number") or ""})
        is_order_doc = kind in ai.ORDER_KINDS or (kind == "other" and n_items > 0)
        # Prefer order documents; among them the one with most lines.
        if best is None:
            better = True
        else:
            best_is_order = (best.get("document_kind") in ai.ORDER_KINDS) or (best.get("document_kind") == "other" and best.get("items"))
            if is_order_doc and not best_is_order:
                better = True
            elif is_order_doc == bool(best_is_order):
                better = n_items > len(best.get("items") or [])
            else:
                better = False
        if better:
            best = result
            best_src = f"attachment:{name}"
    return best, best_src, summary


def _decide_and_extract(email_doc: dict, *, force: bool) -> tuple[dict, dict, str, list[dict]]:
    """Step 1+2 of the pipeline. Returns (classification, extraction, source, attachment_summary).

    PDFs rule: when the email carries PDF/image attachments they are read
    first and decide the outcome — a body saying just "see attached" is the
    norm. The body is classified/extracted only when no attachment turned
    out to be an order document (or there are none)."""
    classification: dict = {}
    best, src, summary = _extract_from_attachments(email_doc)

    if best is not None:
        kind = best.get("document_kind") or "other"
        n_items = len(best.get("items") or [])
        if kind in ai.ORDER_KINDS and n_items > 0:
            classification = {
                "kind": kind, "is_parts_order": True, "confidence": 0.9,
                "vendor_name": best.get("vendor_name") or "",
                "order_reference": best.get("invoice_number") or "",
                "reason": f"{n_items} line item(s) read from {src.split(':', 1)[-1]}",
                "decided_by": "attachment",
            }
            return classification, best, src, summary
        if kind in ("quote", "statement", "receipt") and not force:
            # The PDF says clearly what it is: not an order. Do not let the
            # body ("your order", "thanks for your business") override it.
            classification = {
                "kind": kind, "is_parts_order": False, "confidence": 0.85,
                "vendor_name": best.get("vendor_name") or "",
                "order_reference": best.get("invoice_number") or "",
                "reason": f"Attachment {src.split(':', 1)[-1]} is a {kind}, not an order document",
                "decided_by": "attachment",
            }
            return classification, best, src, summary
        if force and n_items > 0:
            return {"decided_by": "forced"}, best, src, summary

    # No decisive attachment: the email body itself.
    if force:
        classification = {"decided_by": "forced"}
    else:
        classification = ai.classify_email(
            subject=email_doc.get("subject") or "",
            from_email=email_doc.get("from_email") or "",
            from_name=email_doc.get("from_name") or "",
            text=email_doc.get("text") or "",
            attachment_names=[a.get("filename") for a in (email_doc.get("attachments") or [])],
        )
        classification["decided_by"] = "body"
        if not classification.get("is_parts_order"):
            return classification, best or {"items": []}, src, summary

    text = str(email_doc.get("text") or "").strip()
    if text:
        result = ai.extract_order_from_text(
            subject=email_doc.get("subject") or "",
            from_email=email_doc.get("from_email") or "",
            from_name=email_doc.get("from_name") or "",
            text=text,
        )
        if result.get("items") or best is None:
            return classification, result, "body", summary
    return classification, best or {"items": []}, src, summary


def process_inbound_email(
    master,
    shop: dict,
    shop_db,
    email_id: ObjectId,
    *,
    force: bool = False,
    actor_user_id: ObjectId | None = None,
) -> dict:
    """Process one inbound email end to end.

    ``force=True`` (a human pressed "Create order" on an ignored / suggested /
    failed email): classification is skipped, the message is treated as an
    order document and an order is created even in suggest mode.
    Returns {"ok", "status", "order_id", "message"}.
    """
    allowed = FORCEABLE_STATUSES if force else RETRYABLE_STATUSES
    email_doc = _lock_email(shop_db, email_id, allowed)
    if not email_doc:
        current = shop_db.inbound_emails.find_one({"_id": email_id}, {"status": 1})
        status = (current or {}).get("status") or "missing"
        return {"ok": False, "status": status, "order_id": None,
                "message": f"Email is not processable in status '{status}'."}

    settings = get_email_orders_settings(shop_db, shop["_id"])
    orders_coll = shop_db.parts_orders
    vendors_coll = shop_db.vendors
    parts_coll = shop_db.parts

    try:
        # 1+2. decide (PDFs first, then the body) and extract ------------------
        extracted = email_doc.get("extracted") if force else None
        extraction_source = (email_doc.get("extraction_source") or "") if force else ""
        attachment_summary = email_doc.get("attachment_summary") or []
        if force and extracted and (extracted.get("items") or []):
            # A human insists on an email the AI already read: reuse the lines.
            classification = dict(email_doc.get("classification") or {})
            classification["decided_by"] = "forced"
        else:
            classification, extracted, extraction_source, attachment_summary = _decide_and_extract(
                email_doc, force=force,
            )
            if not force and not classification.get("is_parts_order"):
                _finish(shop_db, email_id, STATUS_IGNORED, classification=classification,
                        extracted=extracted if extracted.get("items") else None,
                        extraction_source=extraction_source, attachment_summary=attachment_summary)
                return {"ok": True, "status": STATUS_IGNORED, "order_id": None,
                        "message": classification.get("reason") or "Not a parts order."}

        # 3. vendor ------------------------------------------------------------
        vendor_name = extracted.get("vendor_name") or classification.get("vendor_name") or ""
        own_emails, own_domains = own_addresses(master, shop)
        vendor_sender, vendor_sender_name = vendor_sender_for(email_doc, own_emails, own_domains)
        vendor = match_vendor(vendors_coll, shop["_id"], sender_email=vendor_sender, vendor_name=vendor_name)
        vendor_created = False
        if not vendor:
            vendor = create_vendor_from_email(
                vendors_coll, shop, extracted,
                sender_email=vendor_sender,
                sender_name=vendor_sender_name,
                vendor_name=vendor_name,
            )
            vendor_created = True

        # 4. parts -------------------------------------------------------------
        items, unmatched = build_order_items(parts_coll, shop["_id"], extracted.get("items") or [])
        vendor_ref = (extracted.get("invoice_number") or classification.get("order_reference") or "").strip()[:120]

        vendor_info = {"id": vendor["_id"], "name": vendor.get("name"), "created": vendor_created}

        # 5. dedup / link ------------------------------------------------------
        existing = find_linkable_order(orders_coll, shop["_id"], vendor["_id"], vendor_ref=vendor_ref, items=items)
        if existing:
            now = _utcnow()
            update: dict = {
                "$push": {"source_emails": {
                    "inbound_email_id": email_id,
                    "from_email": email_doc.get("from_email"),
                    "subject": email_doc.get("subject"),
                    "received_at": email_doc.get("received_at"),
                    "kind": classification.get("kind") or "",
                }},
                "$set": {"updated_at": now},
            }
            if vendor_ref and not str(existing.get("vendor_bill") or "").strip():
                update["$set"]["vendor_bill"] = vendor_ref
            orders_coll.update_one({"_id": existing["_id"]}, update)
            _attach_email_files(shop_db, shop["_id"], existing["_id"], email_doc)
            _drop_attachment_bytes(shop_db, email_id)
            learn_vendor_sender(vendors_coll, vendor["_id"], vendor_sender)
            _finish(shop_db, email_id, STATUS_LINKED, classification=classification, extracted=extracted,
                    extraction_source=extraction_source, attachment_summary=attachment_summary,
                    vendor=vendor_info, parts_order_id=existing["_id"])
            return {"ok": True, "status": STATUS_LINKED, "order_id": str(existing["_id"]),
                    "message": f"Linked to existing order #{existing.get('order_number')}."}

        # 6. suggest mode: stop here, a human creates the order ---------------
        if settings["mode"] == MODE_SUGGEST and not force:
            _finish(shop_db, email_id, STATUS_SUGGESTED, classification=classification, extracted=extracted,
                    extraction_source=extraction_source, attachment_summary=attachment_summary, vendor=vendor_info)
            return {"ok": True, "status": STATUS_SUGGESTED, "order_id": None,
                    "message": "Order extracted — waiting for a human to create it."}

        if not items and not unmatched:
            _finish(shop_db, email_id, STATUS_ERROR, classification=classification, extracted=extracted,
                    extraction_source=extraction_source, attachment_summary=attachment_summary,
                    vendor=vendor_info, error="No line items could be read from this email.")
            return {"ok": False, "status": STATUS_ERROR, "order_id": None,
                    "message": "No line items could be read from this email."}

        # 7. create the order --------------------------------------------------
        from app.blueprints.parts import routes as parts_routes  # lazy: routes imports services

        now = _utcnow()
        order_number = parts_routes._get_next_order_number(shop_db, shop["_id"])
        amounts = parts_routes._parts_order_amounts({"items": items, "non_inventory_amounts": []})
        order_date = email_doc.get("email_date") if isinstance(email_doc.get("email_date"), datetime) else email_doc.get("received_at") or now
        order_doc = {
            "work_order_id": None,
            "work_order_number": None,
            "vendor_id": vendor["_id"],
            "order_number": order_number,
            "vendor_bill": vendor_ref,
            "items": items,
            "unmatched_items": unmatched,
            "non_inventory_amounts": [],
            "status": "ordered",
            "order_date": order_date,
            "payment_status": "unpaid",
            "paid_amount": 0.0,
            "remaining_balance": float(amounts.get("total_amount") or 0.0),
            "needs_confirmation": True,
            "source": {
                "kind": "email",
                "inbound_email_id": email_id,
                "from_email": email_doc.get("from_email"),
                "from_name": email_doc.get("from_name"),
                "vendor_sender": vendor_sender,
                "vendor_sender_name": vendor_sender_name,
                "subject": email_doc.get("subject"),
                "received_at": email_doc.get("received_at"),
                "vendor_ref": vendor_ref,
                "document_kind": classification.get("kind") or "",
                "confidence": classification.get("confidence"),
                "extraction_source": extraction_source,
                "vendor_created": vendor_created,
                "forced_by": actor_user_id,
            },
            "notes": "",
            "is_active": True,
            "created_at": now,
            "updated_at": now,
            "created_by": actor_user_id,
            "updated_by": actor_user_id,
            "shop_id": shop["_id"],
            "tenant_id": shop.get("tenant_id"),
        }
        res = orders_coll.insert_one(order_doc)
        order_id = res.inserted_id

        _attach_email_files(shop_db, shop["_id"], order_id, email_doc)
        _drop_attachment_bytes(shop_db, email_id)
        _finish(shop_db, email_id, STATUS_ORDER_CREATED, classification=classification, extracted=extracted,
                extraction_source=extraction_source, attachment_summary=attachment_summary,
                vendor=vendor_info, parts_order_id=order_id)
        bump_email_orders_stats(shop_db, shop["_id"], orders=1)

        _notify_office(master, shop, order_number, vendor.get("name") or "", len(items), len(unmatched), order_id)
        return {"ok": True, "status": STATUS_ORDER_CREATED, "order_id": str(order_id),
                "message": f"Order #{order_number} created (not confirmed)."}

    except Exception as exc:  # noqa: BLE001 — recorded on the email, retried by cron
        logger.exception("email_orders: processing failed for inbound email %s", email_id)
        now = _utcnow()
        shop_db.inbound_emails.update_one(
            {"_id": email_id},
            {"$set": {"status": STATUS_ERROR, "error": str(exc)[:500], "updated_at": now, "processed_at": now},
             "$unset": {"processing_started_at": ""}},
        )
        return {"ok": False, "status": STATUS_ERROR, "order_id": None, "message": str(exc)[:300]}


def _notify_office(master, shop: dict, order_number, vendor_name: str, matched: int, unmatched: int, order_id) -> None:
    try:
        user_ids = office_user_ids_for_shop(master, shop)
        lines = f"{matched} item(s)" + (f", {unmatched} to review" if unmatched else "")
        send_push_to_users(
            master, user_ids,
            f"Parts order #{order_number} from email",
            f"{vendor_name or 'Vendor'}: {lines}. Please confirm.",
            {"type": "parts_order_email", "order_id": str(order_id), "shop_id": str(shop.get('_id'))},
        )
    except Exception:  # noqa: BLE001 — a push failure must not fail the order
        logger.exception("email_orders: push notification failed for order %s", order_id)


def run_inbound_processing(master, *, shop_id: ObjectId | None = None, limit: int = 50) -> dict:
    """Cron entry point: process pending emails of every location with an
    inbox (or one shop). Also unlocks stale 'processing' locks and purges
    attachment bytes older than the retention window."""
    from app.extensions import get_mongo_client

    client = get_mongo_client()
    query: dict = {"inbound_email_token": {"$exists": True}}
    if shop_id is not None:
        query["_id"] = shop_id

    summary = {"shops": 0, "processed": 0, "orders": 0, "linked": 0, "ignored": 0,
               "suggested": 0, "errors": 0, "skipped": 0, "purged": 0}
    now = _utcnow()
    for shop in master.shops.find(query):
        db_name = shop_db_name(shop)
        if not db_name:
            continue
        shop_db = client[db_name]
        settings = get_email_orders_settings(shop_db, shop["_id"])
        if not settings["enabled"]:
            continue
        summary["shops"] += 1

        # Stale locks (worker died mid-flight) go back to the queue.
        shop_db.inbound_emails.update_many(
            {"shop_id": shop["_id"], "status": STATUS_PROCESSING,
             "processing_started_at": {"$lt": now - PROCESSING_STALE_AFTER}},
            {"$set": {"status": STATUS_RECEIVED, "updated_at": now}, "$unset": {"processing_started_at": ""}},
        )

        pending = shop_db.inbound_emails.find(
            {"shop_id": shop["_id"], "$or": [
                {"status": STATUS_RECEIVED},
                {"status": STATUS_ERROR, "attempts": {"$lt": MAX_AUTO_ATTEMPTS}},
            ]},
            {"_id": 1},
        ).sort("received_at", 1).limit(limit)

        for row in pending:
            result = process_inbound_email(master, shop, shop_db, row["_id"])
            summary["processed"] += 1
            st = result.get("status")
            if st == STATUS_ORDER_CREATED:
                summary["orders"] += 1
            elif st == STATUS_LINKED:
                summary["linked"] += 1
            elif st == STATUS_IGNORED:
                summary["ignored"] += 1
            elif st == STATUS_SUGGESTED:
                summary["suggested"] += 1
            elif st == STATUS_ERROR:
                summary["errors"] += 1
            else:
                summary["skipped"] += 1

        summary["purged"] += purge_stale_attachment_data(shop_db, shop["_id"])
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Human decisions on created orders
# ─────────────────────────────────────────────────────────────────────────────

def confirm_email_order(shop_db, shop: dict, order: dict, *, actor_user_id: ObjectId | None,
                        drop_unmatched: bool = False) -> tuple[bool, str]:
    """Clear needs_confirmation. Unmatched lines must be resolved (added as
    parts / dropped) first unless ``drop_unmatched`` is set."""
    if not order.get("needs_confirmation"):
        return True, "Order is already confirmed."
    unmatched = [u for u in (order.get("unmatched_items") or []) if isinstance(u, dict)]
    if unmatched and not drop_unmatched:
        return False, f"{len(unmatched)} line(s) from the email are not matched to parts yet. Add them or drop them first."
    if not (order.get("items") or []) and not (order.get("non_inventory_amounts") or []):
        return False, "The order has no items. Add at least one part before confirming."

    now = _utcnow()
    update: dict = {
        "$set": {
            "needs_confirmation": False,
            "confirmed_at": now,
            "confirmed_by": actor_user_id,
            "updated_at": now,
            "updated_by": actor_user_id,
        },
        "$unset": {"unmatched_items": ""},
    }
    if unmatched:
        update["$set"]["dropped_unmatched_items"] = unmatched
    shop_db.parts_orders.update_one({"_id": order["_id"]}, update)

    source = order.get("source") or {}
    # Learn only the real vendor sender (never the shop's own forwarding address).
    if order.get("vendor_id") and source.get("vendor_sender"):
        learn_vendor_sender(shop_db.vendors, order["vendor_id"], source.get("vendor_sender"))
    return True, "Order confirmed."


def reject_email_order(shop_db, shop: dict, order: dict, *, actor_user_id: ObjectId | None) -> tuple[bool, str]:
    """Soft-delete an unconfirmed email order; the inbox entry is marked
    rejected; a vendor that was auto-created for this email only is
    deactivated again."""
    if not order.get("needs_confirmation"):
        return False, "Only unconfirmed orders can be rejected. Delete the order instead."
    now = _utcnow()
    shop_db.parts_orders.update_one(
        {"_id": order["_id"]},
        {"$set": {"is_active": False, "rejected_at": now, "rejected_by": actor_user_id,
                  "updated_at": now, "updated_by": actor_user_id}},
    )
    source = order.get("source") or {}
    inbound_id = source.get("inbound_email_id")
    if inbound_id:
        shop_db.inbound_emails.update_one(
            {"_id": inbound_id},
            {"$set": {"status": STATUS_REJECTED, "updated_at": now, "rejected_by": actor_user_id}},
        )
    if source.get("vendor_created"):
        remove_auto_vendor_if_unused(shop_db, order.get("vendor_id"), exclude_order_id=order["_id"])
    return True, "Order rejected."


def count_unconfirmed(orders_coll, shop_id: ObjectId) -> int:
    return orders_coll.count_documents({"shop_id": shop_id, "is_active": {"$ne": False}, "needs_confirmation": True})


def serialize_unmatched(order: dict) -> list[dict]:
    out = []
    for idx, u in enumerate(order.get("unmatched_items") or []):
        if not isinstance(u, dict):
            continue
        out.append({
            "index": idx,
            "part_number": str(u.get("part_number") or ""),
            "description": str(u.get("description") or ""),
            "quantity": int(u.get("quantity") or 1),
            "price": float(u.get("price") or 0.0),
        })
    return out


def serialize_source(order: dict) -> dict | None:
    src = order.get("source")
    if not isinstance(src, dict) or src.get("kind") != "email":
        return None
    received = src.get("received_at")
    return {
        "kind": "email",
        "from_email": src.get("from_email") or "",
        "from_name": src.get("from_name") or "",
        "vendor_sender": src.get("vendor_sender") or "",
        "vendor_sender_name": src.get("vendor_sender_name") or "",
        "subject": src.get("subject") or "",
        "received_at": received.isoformat() if isinstance(received, datetime) else None,
        "vendor_ref": src.get("vendor_ref") or "",
        "document_kind": src.get("document_kind") or "",
        "confidence": src.get("confidence"),
        "vendor_created": bool(src.get("vendor_created")),
        "inbound_email_id": str(src.get("inbound_email_id")) if src.get("inbound_email_id") else "",
    }
