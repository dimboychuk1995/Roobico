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

SYSTEM_PROMPT = """You are an expert invoice data extractor for a truck parts and repair shop.
Given an invoice image, extract the following data as JSON.

Return ONLY valid JSON (no markdown, no explanation) with this exact structure:
{
  "vendor_name": "string — the company that issued the invoice (seller / supplier)",
  "vendor_address": "string — full address of the vendor/seller (street, city, state, zip)",
  "vendor_phone": "string — vendor phone number",
  "vendor_email": "string — vendor email address",
  "vendor_website": "string — vendor website URL if visible",
  "vendor_contact_first_name": "string — contact person first name (e.g. from Invoiced By or salesperson field)",
  "vendor_contact_last_name": "string — contact person last name",
  "invoice_number": "string — invoice or bill number",
  "invoice_date": "string — date in MM/DD/YYYY format",
  "items": [
    {
      "part_number": "string — part number / SKU (e.g. DR 8600310, PEXR955337)",
      "description": "string — part description",
      "quantity": 1,
      "price": 0.00
    }
  ],
  "total": 0.00
}

Rules:
- QUANTITY: Look carefully at the Qty / Quantity / Ord / Ship / QTY Shipped columns. Each line item has a quantity — it is NOT always 1. Read the actual number from the invoice. If there are separate "Ordered" and "Shipped" columns, use the "Shipped" quantity.
- PRICE: Use the UNIT price, NOT the extended/total price for the line. If the invoice shows both "List Price" and "Net Price" (or "Your Price", "Sale Price", "Disc Price"), always use the NET / discounted price as the unit price. The net price is the actual price the buyer pays after discounts.
- If a line has a Supplier column, ignore it — it's internal to the vendor.
- Combine part prefix and number into one part_number field (e.g. "DR 8600310").
- Do NOT include tax lines, freight lines, payment info, or signature blocks.
- vendor_address is the seller/remit-to address, NOT the bill-to or ship-to address.
- vendor_phone and vendor_email are the seller's contact info, NOT the buyer's.
- If you cannot determine a field, use empty string or 0.
- Always return the JSON object, even if you can only partially extract data.
"""


def _get_openai_client():
    """Lazy-import and create OpenAI client."""
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not configured. Set it in .env file.")
    return OpenAI(api_key=api_key)


def _pdf_pages_to_images(pdf_bytes: bytes) -> list[str]:
    """
    Convert PDF bytes to list of base64-encoded PNG images (one per page).
    Uses pdf2image if available, otherwise falls back to sending PDF directly.
    """
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        images = []
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            images.append(base64.standard_b64encode(img_bytes).decode("ascii"))
        doc.close()
        return images
    except ImportError:
        # Fallback: send raw PDF as base64 (GPT-4o supports PDF in some modes)
        return [base64.standard_b64encode(pdf_bytes).decode("ascii")]


def parse_invoice(file_bytes: bytes, content_type: str) -> dict[str, Any]:
    """
    Parse an invoice file (PDF or image) and return extracted data.

    Returns dict with keys: vendor_name, invoice_number, invoice_date, items, total
    Raises ValueError on configuration or parsing errors.
    """
    client = _get_openai_client()

    # Build image content parts for the API
    image_parts = []

    if content_type == "application/pdf":
        # Convert PDF to images
        page_images = _pdf_pages_to_images(file_bytes)
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
        "items": normalized_items,
        "total": round(float(result.get("total") or 0), 2),
    }
