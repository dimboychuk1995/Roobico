"""
AI-assisted matcher between internal users and uAttend timecard employees.

Given a list of internal users (name + email) and a list of uAttend employees
(uattend_user_id + name + email), asks OpenAI to return pairs that look like
the same physical person across the two systems (e.g. "Vitaliy Golyk"
internal == "Vitalii Golyk" in uAttend).

Returns a dict mapping `uattend_user_id` (int) -> matched internal user info
{"internal_id": str, "internal_name": str, "internal_email": str,
 "confidence": float}.

Falls back to a deterministic exact email / normalized-name match if AI is
not available or fails.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from typing import Any, Iterable

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a data reconciliation assistant for a US truck \
repair shop. You will receive two lists of people:

  • INTERNAL: users from the shop's own user management system.
  • UATTEND : employees pulled from the uAttend timecard system.

The same physical person can appear in BOTH lists with slight spelling \
differences (transliteration, missing accents, different name order, \
nicknames). Emails may match, may differ, or may be missing on either side.

Your job: for every UATTEND employee, decide whether they correspond to \
exactly one INTERNAL user.

Rules:
  1. Match only if you are confident the two records refer to the same \
     physical person. When uncertain, do NOT produce a match.
  2. Each INTERNAL user can be matched at most once across the whole result.
  3. Identical or near-identical emails are a strong signal.
  4. Treat transliteration variants as the same name \
     (e.g. "Vitaliy" ≈ "Vitalii", "Sergei" ≈ "Serghei", "Yurii" ≈ "Yuriy").
  5. Return STRICT JSON only — no markdown, no commentary.

Output shape:
{
  "matches": [
    {
      "uattend_user_id": <number>,
      "internal_id": "<string id from INTERNAL>",
      "confidence": <number between 0 and 1>
    },
    ...
  ]
}
Include only confident matches (confidence >= 0.7). Omit everything else.
"""


# ── Helpers ─────────────────────────────────────────────────────────────
def _norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z\s]", " ", s).lower()
    return " ".join(sorted(s.split()))


def _norm_email(s: str) -> str:
    return str(s or "").strip().lower()


def _deterministic_match(
    internal_users: list[dict],
    uattend_employees: list[dict],
) -> dict[int, dict]:
    """Exact-email + normalized-name match as a safe fallback."""
    by_email: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for u in internal_users:
        em = _norm_email(u.get("email"))
        nm = _norm_name(u.get("name"))
        if em:
            by_email.setdefault(em, u)
        if nm:
            by_name.setdefault(nm, u)

    out: dict[int, dict] = {}
    used_internal_ids: set[str] = set()
    for emp in uattend_employees:
        try:
            uid = int(emp.get("uattend_user_id"))
        except (TypeError, ValueError):
            continue
        em = _norm_email(emp.get("email"))
        nm = _norm_name(emp.get("name"))
        target = None
        if em and em in by_email:
            target = by_email[em]
        elif nm and nm in by_name:
            target = by_name[nm]
        if not target:
            continue
        iid = str(target.get("internal_id") or "")
        if iid and iid in used_internal_ids:
            continue
        used_internal_ids.add(iid)
        out[uid] = {
            "internal_id": iid,
            "internal_name": target.get("name") or "",
            "internal_email": target.get("email") or "",
            "confidence": 1.0,
        }
    return out


# ── Public API ──────────────────────────────────────────────────────────
def match_employees(
    internal_users: Iterable[dict],
    uattend_employees: Iterable[dict],
) -> dict[int, dict]:
    """
    Args:
      internal_users:  list of {"internal_id", "name", "email"}.
      uattend_employees: list of {"uattend_user_id", "name", "email"}.
    Returns:
      Dict[uattend_user_id:int -> {"internal_id", "internal_name",
                                   "internal_email", "confidence":float}].
    """
    internal_list = [u for u in (internal_users or []) if u]
    uattend_list = [e for e in (uattend_employees or []) if e]
    if not internal_list or not uattend_list:
        return {}

    # Always compute deterministic match first as a baseline.
    base = _deterministic_match(internal_list, uattend_list)

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return base

    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover
        logger.warning("openai SDK missing: %s", exc)
        return base

    payload = {
        "INTERNAL": [
            {
                "internal_id": str(u.get("internal_id") or ""),
                "name": str(u.get("name") or ""),
                "email": str(u.get("email") or ""),
            }
            for u in internal_list
        ],
        "UATTEND": [
            {
                "uattend_user_id": int(e.get("uattend_user_id")),
                "name": str(e.get("name") or ""),
                "email": str(e.get("email") or ""),
            }
            for e in uattend_list
            if e.get("uattend_user_id") is not None
        ],
    }

    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.0,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        parsed = json.loads(raw) if raw else {}
        matches = parsed.get("matches") or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI employee match failed, using deterministic only: %s", exc)
        return base

    internal_by_id: dict[str, dict] = {
        str(u.get("internal_id") or ""): u for u in internal_list
    }

    out: dict[int, dict] = dict(base)
    used_internal_ids: set[str] = {v["internal_id"] for v in out.values() if v.get("internal_id")}

    for m in matches:
        if not isinstance(m, dict):
            continue
        try:
            uid = int(m.get("uattend_user_id"))
        except (TypeError, ValueError):
            continue
        iid = str(m.get("internal_id") or "")
        try:
            conf = float(m.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        if conf < 0.7 or not iid:
            continue
        target = internal_by_id.get(iid)
        if not target:
            continue
        if iid in used_internal_ids and uid not in out:
            # Avoid mapping two uAttend employees to the same internal user.
            continue
        used_internal_ids.add(iid)
        out[uid] = {
            "internal_id": iid,
            "internal_name": target.get("name") or "",
            "internal_email": target.get("email") or "",
            "confidence": conf,
        }

    return out


def cache_key(
    internal_users: Iterable[dict],
    uattend_employees: Iterable[dict],
) -> str:
    """Stable hash of the inputs so callers can cache match results."""
    import hashlib

    def fp(items, fields):
        rows = []
        for it in items or []:
            rows.append("|".join(str(it.get(f) or "").strip().lower() for f in fields))
        rows.sort()
        return "\n".join(rows)

    h = hashlib.sha1()
    h.update(b"INTERNAL\n")
    h.update(fp(internal_users, ("internal_id", "name", "email")).encode("utf-8"))
    h.update(b"\nUATTEND\n")
    h.update(fp(uattend_employees, ("uattend_user_id", "name", "email")).encode("utf-8"))
    return h.hexdigest()
