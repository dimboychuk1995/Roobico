"""
VIN со сканера/фото: нормализация сырой строки (баркод Code 39 несёт
служебный префикс "I") и OCR-извлечение VIN из фотографии таблички
через OpenAI vision (тот же клиент, что и AI-скан бумажных WO).
"""
from __future__ import annotations

import base64
import json
import logging
import re

logger = logging.getLogger(__name__)

_VIN_FORBIDDEN = ("I", "O", "Q")


def normalize_scanned_vin(raw) -> str | None:
    """Сырая строка со сканера/OCR → валидный 17-символьный VIN или None."""
    s = re.sub(r"[^A-Za-z0-9]", "", str(raw or "")).upper()
    # VIN-баркоды (Code 39) часто несут ведущий служебный символ "I"/"O"/"Q":
    # сам VIN этих букв не содержит, поэтому такой префикс безопасно срезать.
    if len(s) == 18 and s[0] in _VIN_FORBIDDEN:
        s = s[1:]
    if len(s) != 17 or any(c in s for c in _VIN_FORBIDDEN):
        return None
    return s


_OCR_SYSTEM_PROMPT = """You extract a VIN (Vehicle Identification Number) from a photo.

The photo shows a vehicle VIN: a dashboard plate, door-jamb sticker, trailer
plate, registration/title document, or handwriting.

Rules:
- A VIN is EXACTLY 17 characters: capital letters and digits, never I, O or Q.
- Fix obvious OCR look-alikes: letter I -> digit 1, letters O/Q -> digit 0.
- If several candidate strings are visible, pick the one that is a valid VIN.
- Respond with STRICT JSON only: {"vin": "<17 characters>"} when found,
  {"vin": null} when no plausible VIN is visible. No other keys, no prose.
"""


def extract_vin_from_image(file_bytes: bytes, content_type: str) -> str | None:
    """Фото → VIN (нормализованный) или None, если AI ничего не разобрал."""
    from app.utils.wo_parser import _get_openai_client

    client = _get_openai_client()
    b64 = base64.standard_b64encode(file_bytes).decode("ascii")
    url = f"data:{content_type or 'image/jpeg'};base64,{b64}"

    messages = [
        {"role": "system", "content": _OCR_SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": "Read the VIN from this photo."},
            {"type": "image_url", "image_url": {"url": url, "detail": "high"}},
        ]},
    ]

    raw_text = ""
    for model in ("gpt-4o-mini", "gpt-4o"):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
                max_tokens=100,
                response_format={"type": "json_object"},
            )
            raw_text = (resp.choices[0].message.content or "").strip()
            if raw_text:
                break
        except Exception as exc:
            logger.warning("VIN OCR %s failed: %s", model, exc)
            continue

    if not raw_text:
        return None

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.warning("VIN OCR returned invalid JSON: %s", raw_text[:200])
        return None

    return normalize_scanned_vin(parsed.get("vin")) if isinstance(parsed, dict) else None
