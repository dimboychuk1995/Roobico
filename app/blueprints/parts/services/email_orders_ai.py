"""AI steps for the email inbox → parts orders pipeline.

Two calls per interesting message:

1. ``classify_email`` — cheap model, decides whether the message is a parts
   order document (order confirmation / invoice / packing slip) or junk
   (promo, shipping tracking, quotes, statements, ...).
2. Extraction — attachments go through the existing vision invoice parser
   (``app.utils.invoice_parser.parse_invoice``); a message with the order in
   its body goes through ``extract_order_from_text`` which returns the very
   same JSON shape.

Both functions are pure (no DB) so tests can monkeypatch them.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.utils.invoice_parser import SYSTEM_PROMPT as INVOICE_SYSTEM_PROMPT
from app.utils.invoice_parser import _get_openai_client, parse_invoice

logger = logging.getLogger(__name__)

CLASSIFY_MODEL = "gpt-4o-mini"
EXTRACT_MODEL = "gpt-4o"

ORDER_KINDS = {"order_confirmation", "invoice", "packing_slip"}
ALL_KINDS = ORDER_KINDS | {"quote", "shipping_notice", "statement", "promo", "other"}

MAX_CLASSIFY_CHARS = 6_000
MAX_EXTRACT_CHARS = 30_000

CLASSIFY_PROMPT = """You triage the email inbox of a truck / auto repair shop's parts department.
Decide whether an email is a PARTS ORDER DOCUMENT from a vendor/supplier, i.e. it lists parts
the shop bought or is buying. Return ONLY JSON:
{
  "kind": "order_confirmation" | "invoice" | "packing_slip" | "quote" | "shipping_notice" | "statement" | "promo" | "other",
  "is_parts_order": true | false,
  "confidence": 0.0-1.0,
  "vendor_name": "string — supplier company name if identifiable, else empty",
  "order_reference": "string — vendor order/invoice/PO number if visible, else empty",
  "reason": "one short sentence"
}
Rules:
- is_parts_order is true ONLY for order_confirmation, invoice and packing_slip that contain
  (or clearly attach) itemized parts lines: part numbers / descriptions / quantities / prices.
- A quote/estimate is NOT an order. A shipping/tracking notification without line items is NOT an
  order. Account statements, marketing, newsletters, password resets, receipts for fuel, tolls,
  software, utilities, meals, payroll etc. are NOT parts orders.
- Attachments named like invoice/order/packing-slip PDFs strongly suggest a parts order even if the
  body is short ("please see attached invoice").
- When unsure, prefer is_parts_order=false with low confidence.
"""

TEXT_EXTRACT_SUFFIX = """

You are given the TEXT of a vendor email (order confirmation / invoice / packing slip), not an image.
Still fill document_kind for the email itself.
Read the line items from the text tables. Column layouts vary (tabs, pipes, multiple spaces).
Use the same rules as above. Treat the email sender company as the vendor when the body does
not spell out a vendor name. invoice_number is the vendor's order / invoice / confirmation number."""


def _json_from_model(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        lines = [l for l in raw.split("\n") if not l.strip().startswith("```")]
        raw = "\n".join(lines)
    return json.loads(raw)


def classify_email(
    *,
    subject: str,
    from_email: str,
    from_name: str,
    text: str,
    attachment_names: list[str] | None = None,
) -> dict[str, Any]:
    """Return {"kind", "is_parts_order", "confidence", "vendor_name",
    "order_reference", "reason"}. Raises ValueError when the model answer is
    unusable (caller marks the email as error and cron retries)."""
    client = _get_openai_client()
    names = ", ".join(attachment_names or []) or "(none)"
    body = (text or "")[:MAX_CLASSIFY_CHARS]
    user_msg = (
        f"From: {from_name} <{from_email}>\n"
        f"Subject: {subject}\n"
        f"Attachments: {names}\n\n"
        f"Body:\n{body}"
    )
    response = client.chat.completions.create(
        model=CLASSIFY_MODEL,
        messages=[
            {"role": "system", "content": CLASSIFY_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=300,
        temperature=0,
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content or ""
    try:
        data = _json_from_model(raw)
    except json.JSONDecodeError as exc:
        logger.error("classify_email: invalid JSON from model: %s", raw[:300])
        raise ValueError("AI classification returned invalid JSON") from exc

    kind = str(data.get("kind") or "other").strip().lower()
    if kind not in ALL_KINDS:
        kind = "other"
    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    is_order = bool(data.get("is_parts_order")) and kind in ORDER_KINDS
    return {
        "kind": kind,
        "is_parts_order": is_order,
        "confidence": max(0.0, min(1.0, confidence)),
        "vendor_name": str(data.get("vendor_name") or "").strip()[:200],
        "order_reference": str(data.get("order_reference") or "").strip()[:120],
        "reason": str(data.get("reason") or "").strip()[:300],
    }


def _normalize_extraction(result: dict) -> dict:
    items = []
    for item in result.get("items") or []:
        if not isinstance(item, dict):
            continue
        try:
            qty = max(int(float(item.get("quantity") or 1)), 1)
        except (TypeError, ValueError):
            qty = 1
        try:
            price = round(float(item.get("price") or 0), 2)
        except (TypeError, ValueError):
            price = 0.0
        pn = str(item.get("part_number") or "").strip()
        desc = str(item.get("description") or "").strip()
        if not pn and not desc:
            continue
        items.append({"part_number": pn, "description": desc, "quantity": qty, "price": price})
    try:
        total = round(float(result.get("total") or 0), 2)
    except (TypeError, ValueError):
        total = 0.0
    kind = str(result.get("document_kind") or "").strip().lower()
    return {
        "document_kind": kind if kind in ALL_KINDS or kind in ("receipt",) else "other",
        "vendor_name": str(result.get("vendor_name") or "").strip(),
        "vendor_address": str(result.get("vendor_address") or "").strip(),
        "vendor_phone": str(result.get("vendor_phone") or "").strip(),
        "vendor_email": str(result.get("vendor_email") or "").strip(),
        "vendor_website": str(result.get("vendor_website") or "").strip(),
        "vendor_contact_first_name": str(result.get("vendor_contact_first_name") or "").strip(),
        "vendor_contact_last_name": str(result.get("vendor_contact_last_name") or "").strip(),
        "invoice_number": str(result.get("invoice_number") or "").strip(),
        "invoice_date": str(result.get("invoice_date") or "").strip(),
        "items": items,
        "total": total,
    }


def extract_order_from_text(*, subject: str, from_email: str, from_name: str, text: str) -> dict[str, Any]:
    """Same JSON shape as ``parse_invoice`` but from the email body."""
    client = _get_openai_client()
    body = (text or "")[:MAX_EXTRACT_CHARS]
    user_msg = (
        f"From: {from_name} <{from_email}>\nSubject: {subject}\n\n"
        f"Email text:\n{body}"
    )
    response = client.chat.completions.create(
        model=EXTRACT_MODEL,
        messages=[
            {"role": "system", "content": INVOICE_SYSTEM_PROMPT + TEXT_EXTRACT_SUFFIX},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=2500,
        temperature=0,
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content or ""
    try:
        data = _json_from_model(raw)
    except json.JSONDecodeError as exc:
        logger.error("extract_order_from_text: invalid JSON from model: %s", raw[:300])
        raise ValueError("AI extraction returned invalid JSON") from exc
    return _normalize_extraction(data)


ATTACHMENT_MAX_PAGES = 6


def extract_order_from_attachment(data: bytes, content_type: str) -> dict[str, Any]:
    """Vision extraction of a PDF/image attachment (existing invoice parser).
    Also tells what the document IS (`document_kind`) so a quote or a
    statement PDF is not turned into an order. Only the first pages of a
    PDF are read — an order document fits; a 40-page statement does not
    deserve 40 vision calls."""
    return _normalize_extraction(parse_invoice(data, content_type, max_pages=ATTACHMENT_MAX_PAGES))
