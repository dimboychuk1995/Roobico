"""Inbound email webhook: the location mailbox → `inbound_emails`.

Contract (implemented by deploy/cloudflare_email_worker.js, but any relay
that can POST a raw RFC 822 message works):

    POST /inbound/email
    X-Inbound-Secret: <INBOUND_EMAIL_WEBHOOK_SECRET>
    X-Envelope-From:  <SMTP MAIL FROM>
    X-Envelope-To:    <SMTP RCPT TO>   ← orders-<token>@<domain>
    Content-Type:     message/rfc822
    <raw message bytes>

The token in the envelope recipient (fallback: To/Cc/Delivered-To headers)
identifies tenant + location. Unknown tokens and disabled inboxes are
acknowledged with 200 and dropped — we never tell a sender whether an
address exists. Token-authenticated endpoint, hence the CSRF exemption.
"""
from __future__ import annotations

import hmac
import threading

from flask import current_app, jsonify, request

from app.extensions import csrf, get_master_db, get_mongo_client
from app.utils.inbound_mime import parse_inbound_mime
from app.utils.integrations.email_inbox import (
    extract_tokens,
    get_email_orders_settings,
    resolve_shop_by_token,
)
from app.utils.tenant import shop_db_name

from . import inbound_email_bp


def _secret_ok() -> bool:
    expected = str(current_app.config.get("INBOUND_EMAIL_WEBHOOK_SECRET") or "")
    provided = str(request.headers.get("X-Inbound-Secret") or "")
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)


def _process_in_background(app, shop: dict, db_name: str, email_id) -> None:
    from app.blueprints.parts.services.email_orders import process_inbound_email

    with app.app_context():
        try:
            master = get_master_db()
            shop_db = get_mongo_client()[db_name]
            process_inbound_email(master, shop, shop_db, email_id)
        except Exception:  # noqa: BLE001 — cron retries; never crash the thread silently
            app.logger.exception("inbound_email: inline processing failed for %s", email_id)


@inbound_email_bp.post("/email")
@csrf.exempt
def inbound_email_webhook():
    if not current_app.config.get("INBOUND_EMAIL_WEBHOOK_SECRET"):
        current_app.logger.warning("inbound_email: webhook hit but INBOUND_EMAIL_WEBHOOK_SECRET not set")
        return jsonify({"ok": False, "error": "webhook secret not configured"}), 503
    if not _secret_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    raw = request.get_data(cache=False) or b""
    if not raw:
        return jsonify({"ok": False, "error": "empty body"}), 400

    envelope_from = str(request.headers.get("X-Envelope-From") or "").strip().lower()
    envelope_to = str(request.headers.get("X-Envelope-To") or "").strip().lower()

    try:
        parsed = parse_inbound_mime(raw)
    except Exception:  # noqa: BLE001 — log the broken message, acknowledge so the relay stops retrying
        current_app.logger.exception("inbound_email: failed to parse MIME (envelope_to=%s)", envelope_to)
        return jsonify({"ok": True, "routed": False, "reason": "unparseable"}), 200

    tokens = extract_tokens(envelope_to, parsed.get("header_blob"), parsed.get("to"), parsed.get("cc"))
    master = get_master_db()
    shop = None
    for tok in tokens:
        shop = resolve_shop_by_token(master, tok)
        if shop:
            break
    if not shop:
        current_app.logger.warning(
            "inbound_email: unrouted message (envelope_to=%s from=%s subject=%r)",
            envelope_to, parsed.get("from_email"), (parsed.get("subject") or "")[:80],
        )
        return jsonify({"ok": True, "routed": False, "reason": "unknown address"}), 200

    db_name = shop_db_name(shop)
    if not db_name:
        current_app.logger.error("inbound_email: shop %s has no db_name", shop.get("_id"))
        return jsonify({"ok": True, "routed": False, "reason": "shop not configured"}), 200

    shop_db = get_mongo_client()[db_name]
    settings = get_email_orders_settings(shop_db, shop["_id"])
    if not settings["enabled"]:
        return jsonify({"ok": True, "routed": False, "reason": "inbox disabled"}), 200

    from app.blueprints.parts.services.email_orders import store_inbound_email

    doc, created = store_inbound_email(
        shop_db, shop, parsed,
        envelope_from=envelope_from, envelope_to=envelope_to, provider="cloudflare",
    )
    if not created:
        return jsonify({"ok": True, "routed": True, "duplicate": True, "email_id": str(doc["_id"])}), 200

    if current_app.config.get("INBOUND_EMAIL_PROCESS_INLINE"):
        app_obj = current_app._get_current_object()
        threading.Thread(
            target=_process_in_background,
            args=(app_obj, shop, db_name, doc["_id"]),
            daemon=True,
            name=f"inbound-email-{doc['_id']}",
        ).start()

    return jsonify({"ok": True, "routed": True, "duplicate": False, "email_id": str(doc["_id"])}), 200
