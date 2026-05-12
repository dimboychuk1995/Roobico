"""
AI helper to polish a free-form labor issue description.

Detects the input language, translates non-English text into clear, concise
shop English, or — when the text is already English — fixes grammar and
phrasing so it reads as a professional issue description for a customer
authorization email.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior service writer at a US truck repair shop.

You will receive a short free-form description of a vehicle/truck issue,
written by a mechanic or manager. The text may be in any language and may
contain typos, slang, or rough notes.

Your job:
  1. Detect the input language (return ISO 639-1 code, e.g. "en", "ru", "es").
  2. Produce a clean, professional English version of the description that
     can be sent to the customer in an authorization email:
       - if the input is NOT English -> translate it into clear shop English;
       - if the input IS English -> rewrite it with proper grammar, fix typos,
         and make it concise and professional, but DO NOT invent details
         that are not in the original text.
  3. Keep the polished text short (1-3 sentences). Do not add greetings,
     sign-offs, prices, time estimates or repair recommendations.
  4. Preserve part numbers, codes and units exactly as written.

Return ONLY a JSON object of this exact shape, with no markdown:
{
  "language": "en",
  "polished": "string"
}
"""


def _get_openai_client():
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not configured.")
    return OpenAI(api_key=api_key)


def polish_issue_description(text: str) -> dict[str, Any]:
    """
    Returns: {"language": "en", "polished": "...", "is_english": bool, "original": "..."}
    Raises ValueError on configuration or empty input.
    """
    original = (text or "").strip()
    if not original:
        raise ValueError("Text is empty.")
    if len(original) > 4000:
        original = original[:4000]

    client = _get_openai_client()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": original},
    ]

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.2,
        max_tokens=500,
        response_format={"type": "json_object"},
    )
    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        raise ValueError("AI returned an empty response.")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("polish_issue_description: invalid JSON from AI: %s", raw[:300])
        raise ValueError("AI returned invalid response.") from exc

    language = str(parsed.get("language") or "").strip().lower() or "en"
    polished = str(parsed.get("polished") or "").strip()
    if not polished:
        raise ValueError("AI returned an empty polished text.")

    return {
        "language": language,
        "polished": polished,
        "is_english": language == "en",
        "original": original,
    }
