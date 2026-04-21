"""
Handwritten Work Order parser via OpenAI gpt-4o vision.

Mechanic fills out a preprinted blank with up to 5 labor blocks. We extract
labor description, hours, and parts (PN, description, qty). Customer / unit
fields are intentionally ignored — the user enters those manually.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert at reading HANDWRITTEN repair shop work orders.

The image is a preprinted Work Order blank with up to 5 LABOR blocks. Each
block contains:
  - "Labor Description" line (free text)
  - "Hours" field (number, may be decimal like 1.5 or 0.75)
  - A small parts table with three columns: Part Number, Part Description, Qty

IGNORE these fields entirely:
  Work Order No, Mechanic Name, Time Check In/Out, Date, Year, Customer,
  Make/Model, Truck#, Trailer#, VIN.

Return ONLY valid JSON of this exact shape:
{
  "labors": [
    {
      "labor_description": "string — exactly what the mechanic wrote",
      "labor_hours": 0,
      "parts": [
        {
          "part_number": "string — exactly as written, no inventing prefixes",
          "description": "string — exactly as written",
          "qty": 1
        }
      ]
    }
  ]
}

CRITICAL rules:
- Read EXACTLY what is written. Do NOT invent or autocomplete part numbers.
- Use English/Latin characters even if a stroke looks ambiguous. Do NOT output
  Cyrillic, Greek, or other non-Latin letters — this is a US shop form.
- Part numbers usually contain digits, letters (uppercase), dashes and slashes.
- If a row has only a part number OR only a description, fill what's there and
  leave the other empty. Skip rows where BOTH are empty.
- qty defaults to 1 if not written.
- Skip totally empty labor blocks (no description, no hours, no parts).
- Return {"labors": []} if nothing usable was found.
"""


def _get_openai_client():
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not configured.")
    return OpenAI(api_key=api_key)


def _pdf_pages_to_pngs(pdf_bytes: bytes) -> list[bytes]:
    """Render every PDF page to a PNG byte-string at 300 DPI."""
    try:
        import fitz
    except ImportError:
        return [pdf_bytes]
    out = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page in doc:
        pix = page.get_pixmap(dpi=300)
        out.append(pix.tobytes("png"))
    doc.close()
    return out


def _build_image_parts(file_bytes: bytes, content_type: str) -> tuple[list[dict], list[str]]:
    """
    Returns (vision_message_parts, preview_data_urls).

    preview_data_urls is what the frontend can show as <img src=...> alongside
    the recognized fields so the user can compare with the original handwriting.
    """
    parts: list[dict] = []
    previews: list[str] = []

    if content_type == "application/pdf":
        for png in _pdf_pages_to_pngs(file_bytes):
            b64 = base64.standard_b64encode(png).decode("ascii")
            url = f"data:image/png;base64,{b64}"
            parts.append({
                "type": "image_url",
                "image_url": {"url": url, "detail": "high"},
            })
            previews.append(url)
    else:
        b64 = base64.standard_b64encode(file_bytes).decode("ascii")
        mime = content_type or "image/jpeg"
        url = f"data:{mime};base64,{b64}"
        parts.append({
            "type": "image_url",
            "image_url": {"url": url, "detail": "high"},
        })
        previews.append(url)

    return parts, previews


def _normalize_parsed(raw: dict[str, Any]) -> dict[str, Any]:
    out_labors = []
    for block in (raw.get("labors") or []):
        if not isinstance(block, dict):
            continue
        try:
            hours = float(block.get("labor_hours") or 0)
        except (TypeError, ValueError):
            hours = 0.0
        if hours < 0:
            hours = 0.0
        out_parts = []
        for p in (block.get("parts") or []):
            if not isinstance(p, dict):
                continue
            pn = str(p.get("part_number") or "").strip()
            desc = str(p.get("description") or "").strip()
            try:
                qty = int(float(p.get("qty") or 1))
            except (TypeError, ValueError):
                qty = 1
            if qty <= 0:
                qty = 1
            if not pn and not desc:
                continue
            out_parts.append({
                "part_number": pn,
                "description": desc,
                "qty": qty,
            })
        desc_text = str(block.get("labor_description") or "").strip()
        if not desc_text and hours <= 0 and not out_parts:
            continue
        out_labors.append({
            "labor_description": desc_text,
            "labor_hours": hours,
            "parts": out_parts,
        })
    return {"labors": out_labors}


def parse_work_order(file_bytes: bytes, content_type: str) -> dict[str, Any]:
    """
    Returns:
      {
        "labors": [...],
        "preview_image_urls": ["data:image/png;base64,..."]  # one per page
      }
    """
    client = _get_openai_client()
    image_parts, previews = _build_image_parts(file_bytes, content_type)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": "Extract labors and parts from this handwritten work order."},
            *image_parts,
        ]},
    ]

    last_err: Exception | None = None
    raw_text = ""
    for model in ("gpt-4o", "gpt-4o-mini"):
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": 0,
                "max_tokens": 3000,
                "response_format": {"type": "json_object"},
            }
            resp = client.chat.completions.create(**kwargs)
            raw_text = (resp.choices[0].message.content or "").strip()
            if raw_text:
                break
        except Exception as exc:
            last_err = exc
            logger.warning("OpenAI vision %s failed: %s", model, exc)
            continue

    if not raw_text:
        raise ValueError(f"AI did not return a response. {last_err or ''}".strip())

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.error("OpenAI returned invalid JSON: %s", raw_text[:500])
        raise ValueError("AI returned invalid response. Please try again.") from exc

    result = _normalize_parsed(parsed)
    result["preview_image_urls"] = previews
    return result
