"""Per-location inbound email inbox for parts orders ("email_orders" integration).

Every location (shop) can get a unique mailbox address
``orders-<token>@<INBOUND_EMAIL_DOMAIN>``. The owner forwards the location's
vendor mail there (or gives the address to vendors as CC). Only the AI reads
this mailbox: every message is classified, parts orders are created with
``needs_confirmation=True``, everything else is kept in the inbox list as
"ignored".

Storage:

* ``master.shops.inbound_email_token`` — the unique token (global lookup from
  the webhook: token → tenant + shop). Unique sparse index.
* ``<shop_db>.integrations`` (provider ``email_orders``) — enabled flag and
  processing mode (``auto`` — create orders, ``suggest`` — only extract and
  wait for a human to click "Create order"). No API key involved.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from flask import current_app

PROVIDER = "email_orders"
MODE_AUTO = "auto"
MODE_SUGGEST = "suggest"
MODES = (MODE_AUTO, MODE_SUGGEST)

_TOKEN_RE = re.compile(r"^[a-z0-9]{8,32}$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── address helpers ──────────────────────────────────────────────────────────

def inbound_domain() -> str:
    return str(current_app.config.get("INBOUND_EMAIL_DOMAIN") or "roobico.com").strip().lower()


def inbound_prefix() -> str:
    return str(current_app.config.get("INBOUND_EMAIL_LOCAL_PREFIX") or "orders-").strip().lower()


def address_for_token(token: str) -> str:
    return f"{inbound_prefix()}{token}@{inbound_domain()}"


def extract_tokens(*sources: str | list | None) -> list[str]:
    """Pull every ``<prefix><token>@<domain>`` token out of arbitrary text
    (envelope recipient, To/Cc/Delivered-To headers, ...). Order preserved,
    duplicates removed."""
    prefix = re.escape(inbound_prefix())
    domain = re.escape(inbound_domain())
    pattern = re.compile(prefix + r"([a-z0-9]{8,32})@" + domain, re.IGNORECASE)
    found: list[str] = []
    for src in sources:
        if not src:
            continue
        if isinstance(src, (list, tuple)):
            blob = " ".join(str(x) for x in src if x)
        else:
            blob = str(src)
        for m in pattern.finditer(blob):
            tok = m.group(1).lower()
            if tok not in found:
                found.append(tok)
    return found


# ── token lifecycle (master.shops) ───────────────────────────────────────────

def get_shop_token(shop: dict | None) -> str:
    tok = str((shop or {}).get("inbound_email_token") or "").strip().lower()
    return tok if _TOKEN_RE.match(tok) else ""


def ensure_shop_token(master, shop_id: ObjectId, tenant_id) -> str:
    """Return the shop's inbound token, generating one on first use.

    The lookup is scoped by tenant so a foreign shop id can never get a
    token issued through another tenant's session."""
    query = {"_id": shop_id}
    if tenant_id is not None:
        query["tenant_id"] = tenant_id
    shop = master.shops.find_one(query, {"inbound_email_token": 1})
    if not shop:
        return ""
    existing = get_shop_token(shop)
    if existing:
        return existing
    for _ in range(5):
        token = secrets.token_hex(6)  # 12 lowercase hex chars
        res = master.shops.update_one(
            {"_id": shop_id, "inbound_email_token": {"$exists": False}},
            {"$set": {"inbound_email_token": token, "inbound_email_token_created_at": _utcnow()}},
        )
        if res.modified_count:
            return token
        shop = master.shops.find_one({"_id": shop_id}, {"inbound_email_token": 1})
        existing = get_shop_token(shop)
        if existing:
            return existing
    return ""


def rotate_shop_token(master, shop_id: ObjectId, tenant_id) -> str:
    """Issue a fresh address (old one stops routing immediately)."""
    query = {"_id": shop_id}
    if tenant_id is not None:
        query["tenant_id"] = tenant_id
    token = secrets.token_hex(6)
    res = master.shops.update_one(
        query,
        {"$set": {"inbound_email_token": token, "inbound_email_token_created_at": _utcnow()}},
    )
    return token if res.matched_count else ""


def resolve_shop_by_token(master, token: str) -> Optional[dict]:
    token = str(token or "").strip().lower()
    if not _TOKEN_RE.match(token):
        return None
    return master.shops.find_one({"inbound_email_token": token})


# ── settings (shop_db.integrations, provider=email_orders) ───────────────────

def get_email_orders_settings(shop_db, shop_id: ObjectId) -> dict:
    doc = shop_db.integrations.find_one({"shop_id": shop_id, "provider": PROVIDER}) or {}
    mode = str(doc.get("mode") or MODE_AUTO).lower()
    if mode not in MODES:
        mode = MODE_AUTO
    return {
        "configured": bool(doc),
        "enabled": bool(doc.get("enabled")),
        "mode": mode,
        "last_email_at": doc.get("last_email_at"),
        "emails_received": int(doc.get("emails_received") or 0),
        "orders_created": int(doc.get("orders_created") or 0),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def save_email_orders_settings(
    shop_db,
    shop_id: ObjectId,
    *,
    enabled: Optional[bool] = None,
    mode: Optional[str] = None,
    actor_user_id=None,
) -> dict:
    now = _utcnow()
    update: dict = {"updated_at": now}
    if actor_user_id is not None:
        update["updated_by"] = actor_user_id
    if enabled is not None:
        update["enabled"] = bool(enabled)
    if mode is not None:
        m = str(mode).lower().strip()
        if m not in MODES:
            raise ValueError("Unknown mode. Use 'auto' or 'suggest'.")
        update["mode"] = m
    shop_db.integrations.update_one(
        {"shop_id": shop_id, "provider": PROVIDER},
        {
            "$set": update,
            "$setOnInsert": {
                "shop_id": shop_id,
                "provider": PROVIDER,
                "created_at": now,
                "created_by": actor_user_id,
                "emails_received": 0,
                "orders_created": 0,
            },
        },
        upsert=True,
    )
    return get_email_orders_settings(shop_db, shop_id)


def bump_email_orders_stats(shop_db, shop_id: ObjectId, *, emails: int = 0, orders: int = 0) -> None:
    inc = {}
    if emails:
        inc["emails_received"] = int(emails)
    if orders:
        inc["orders_created"] = int(orders)
    update: dict = {"$set": {"updated_at": _utcnow()}}
    if emails:
        update["$set"]["last_email_at"] = _utcnow()
    if inc:
        update["$inc"] = inc
    shop_db.integrations.update_one({"shop_id": shop_id, "provider": PROVIDER}, update)
