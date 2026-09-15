"""
AI-powered vendor invoice parser using OpenAI GPT-4o vision.

Accepts PDF or image files, extracts vendor info and line items.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert at reading parts documents (invoices, order confirmations, packing slips)
for a heavy-duty truck and fleet repair shop. The shop is the BUYER. Extract the data below as JSON.

Return ONLY valid JSON (no markdown, no explanation) with this exact structure:
{
  "vendor_name": "string — the SELLER's trade name as printed in the header/logo (e.g. 'Hawk Ford of St. Charles', 'FleetPride')",
  "vendor_address": "string — the seller's own street address (city, state, zip)",
  "vendor_phone": "string — the seller's phone",
  "vendor_email": "string — the seller's email",
  "vendor_website": "string — the seller's website if printed",
  "vendor_contact_first_name": "string — seller-side contact person first name (Salesperson / Invoiced By / Counterman / Rep)",
  "vendor_contact_last_name": "string — seller-side contact person last name",
  "invoice_number": "string — the document's own number: Invoice #, Order #, Confirmation #, Packing Slip #",
  "invoice_date": "string — the document date in MM/DD/YYYY",
  "document_kind": "invoice" | "order_confirmation" | "packing_slip" | "quote" | "statement" | "receipt" | "credit_memo" | "other",
  "items": [
    {
      "part_number": "string — the part number as invoiced (e.g. 'DR 8600310', 'F81Z-6B209-AB', 'PEXR955337')",
      "description": "string — part description",
      "quantity": 1,
      "price": 0.00
    }
  ],
  "total": 0.00
}

WHO IS WHO
- The seller/supplier is the vendor. The buyer (Bill To / Sold To / Ship To / Customer / Account) is the shop —
  never put the buyer's name, address, phone or email into vendor fields.
- vendor_name is the trade name in the header or logo, not a "Remit To" lockbox or bank entity and not a parent
  holding company. Keep the dealership name as printed ("Hawk Ford of St. Charles"), do not shorten it.
- vendor_address is the seller's own address, not Bill To, Ship To or Remit To (unless Remit To is the only
  seller address printed).

DOCUMENT NUMBER AND DATE
- invoice_number is the document's own number. NOT the buyer's PO #, account #, customer #, RO #, VIN or unit #.
  For an order confirmation use the vendor order / confirmation number; for a packing slip the packing slip #
  (or the order # if that is the only one).
- invoice_date is the document date (Invoice Date / Order Date), not Due Date, Ship Date or Printed Date.

LINE ITEMS — what to include
- One entry per parts line. Include only physical parts/supplies the shop is buying.
- EXCLUDE these lines entirely: core charges / core deposits / core credits (CORE, CORE CHG, CORE RETURN),
  sales tax, freight / shipping / delivery / fuel surcharge, environmental / hazmat / disposal fees, shop
  supplies, restocking fees, labor, deposits, discounts as separate lines, subtotal / total / balance lines,
  payment info, signature blocks, page headers and footers.
- Backordered or cancelled lines with 0 shipped (B/O, BACKORDER, CANCELLED) are excluded on invoices and
  packing slips; on order confirmations and quotes every ordered line counts.
- Multi-page documents: continue across pages, do not duplicate lines repeated in "continued" headers,
  and take totals once.

PART NUMBER
- The primary part number column as invoiced. Combine a manufacturer/line prefix with the number into ONE string
  when they are printed as one code ("DR 8600310", "MOT 12345", "FLT AF25550").
- NOT a part number: the line/sequence number (1, 2, 3…), UPC/barcode digits, bin/location, the buyer's PO #,
  and superseded / replaced / "was" / interchange numbers — when a line shows "12345 supersedes 67890" or
  "replaced by", use the number that was actually invoiced (usually the new one).
- Descriptions may wrap to a second line: merge it into the same item.

QUANTITY
- Every line has a quantity. Read the number from the Qty / Quantity / Ord / Ship / Shipped / QTY SHP / Units /
  Each column. It is NOT always 1 — never default to 1 when a number is printed.
- With separate Ordered and Shipped columns use Shipped (on invoices and packing slips); with Ordered and
  Backordered columns use Ordered − Backordered. On order confirmations use the Ordered quantity.
- Quantity is the count of sale units (EA, PC, SET, KIT, BOX, GAL, QT). "2 EA" = 2; a "BOX of 4" sold as
  1 box = 1. Use whole numbers.

PRICE
- price is the NET UNIT price the buyer pays for one unit, after discounts.
- Columns: prefer Net / Net Price / Net Each / Your Price / Your Cost / Cost / Sale / Disc Price / Dealer Price /
  Unit Price. NEVER use List, MSRP, Retail, Suggested, Jobber or Core columns.
- NEVER use the extended / amount / total column as the unit price. If only quantity and extended amount are
  printed, unit price = extended ÷ quantity, rounded to cents.
- If a Discount % column applies to List: price = List × (1 − discount). If a line shows a unit price and a
  separate core price, price is the unit price only (the core is tracked separately).
- Ignore a Supplier / Vendor / Source column inside the line — it is internal to the seller.

TOTAL
- total is the document's grand total as printed (including tax and freight), used only as a sanity check.

document_kind — what the document IS:
- "invoice": a bill for parts with line items. "order_confirmation": the vendor acknowledges an order
  (Order Confirmation, Order Acknowledgement, Sales Order, "Thank you for your order"). "packing_slip":
  shipment contents (Packing Slip / Pick Ticket / Delivery Note).
- "quote": Quote / Estimate / Proposal — an offer, NOT an order. "statement": an account summary listing several
  invoices and balances. "receipt": a payment receipt without part lines. "credit_memo": a credit / return
  document with negative amounts. Anything not about parts (fuel, meals, software, utilities, tolls,
  marketing) is "other".

- If you cannot determine a field, use an empty string or 0. Always return the JSON object, even if only
  part of the data could be read.
"""

DOCUMENT_KINDS = {
    "invoice", "order_confirmation", "packing_slip", "quote", "statement", "receipt", "credit_memo", "other",
}


def _get_openai_client():
    """Lazy-import and create OpenAI client."""
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not configured. Set it in .env file.")
    return OpenAI(api_key=api_key)


def _pdf_pages_to_images(pdf_bytes: bytes, max_pages: int | None = None) -> list[str]:
    """
    Convert PDF bytes to list of base64-encoded PNG images (one per page,
    first `max_pages` pages when given — long statements are not worth
    a vision call per page). Uses PyMuPDF if available, otherwise falls
    back to sending the PDF directly.
    """
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        images = []
        for index, page in enumerate(doc):
            if max_pages is not None and index >= max_pages:
                break
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            images.append(base64.standard_b64encode(img_bytes).decode("ascii"))
        doc.close()
        return images
    except ImportError:
        # Fallback: send raw PDF as base64 (GPT-4o supports PDF in some modes)
        return [base64.standard_b64encode(pdf_bytes).decode("ascii")]


def parse_invoice(file_bytes: bytes, content_type: str, *, max_pages: int | None = None) -> dict[str, Any]:
    """
    Parse an invoice file (PDF or image) and return extracted data.

    Returns dict with keys: vendor_name, invoice_number, invoice_date,
    document_kind, items, total. Raises ValueError on configuration or
    parsing errors.
    """
    client = _get_openai_client()

    # Build image content parts for the API
    image_parts = []

    if content_type == "application/pdf":
        # Convert PDF to images
        page_images = _pdf_pages_to_images(file_bytes, max_pages=max_pages)
        for img_b64 in page_images:
            image_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{img_b64}",
                    "detail": "high",
                },
            })
    else:
        # Direct image (JPEG, PNG, etc.)
        b64 = base64.standard_b64encode(file_bytes).decode("ascii")
        mime = content_type or "image/jpeg"
        image_parts.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime};base64,{b64}",
                "detail": "high",
            },
        })

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Extract all data from this vendor invoice:"},
                *image_parts,
            ],
        },
    ]

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        max_tokens=2000,
        temperature=0,
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        raw = "\n".join(lines)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("OpenAI returned invalid JSON: %s", raw[:500])
        raise ValueError(f"AI returned invalid response. Please try again.") from exc

    # Normalize items
    items = result.get("items") or []
    normalized_items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized_items.append({
            "part_number": str(item.get("part_number") or "").strip(),
            "description": str(item.get("description") or "").strip(),
            "quantity": max(int(item.get("quantity") or 1), 1),
            "price": round(float(item.get("price") or 0), 2),
        })

    return {
        "vendor_name": str(result.get("vendor_name") or "").strip(),
        "vendor_address": str(result.get("vendor_address") or "").strip(),
        "vendor_phone": str(result.get("vendor_phone") or "").strip(),
        "vendor_email": str(result.get("vendor_email") or "").strip(),
        "vendor_website": str(result.get("vendor_website") or "").strip(),
        "vendor_contact_first_name": str(result.get("vendor_contact_first_name") or "").strip(),
        "vendor_contact_last_name": str(result.get("vendor_contact_last_name") or "").strip(),
        "invoice_number": str(result.get("invoice_number") or "").strip(),
        "invoice_date": str(result.get("invoice_date") or "").strip(),
        "document_kind": _document_kind(result.get("document_kind")),
        "items": normalized_items,
        "total": round(float(result.get("total") or 0), 2),
    }


def _document_kind(value) -> str:
    kind = str(value or "").strip().lower()
    return kind if kind in DOCUMENT_KINDS else "other"
