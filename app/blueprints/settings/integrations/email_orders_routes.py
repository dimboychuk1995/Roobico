"""Settings → Integrations → "Email orders inbox" (per location).

The location gets a unique mailbox address; the owner forwards vendor mail
there. This module manages the address / enabled flag / mode and exposes
the inbox history so a human can re-run, force-create or ignore emails.
Processing itself lives in app/blueprints/parts/services/email_orders.py.
"""
from __future__ import annotations

from flask import jsonify, request, session

from app.blueprints.parts.services.email_orders import (
    FORCEABLE_STATUSES,
    STATUS_IGNORED,
    STATUS_ORDER_CREATED,
    STATUS_LINKED,
    process_inbound_email,
    serialize_inbound_email,
)
from app.blueprints.settings import settings_bp
from app.extensions import get_master_db
from app.utils.auth import SESSION_USER_ID, login_required
from app.utils.integrations.email_inbox import (
    MODES,
    address_for_token,
    ensure_shop_token,
    get_email_orders_settings,
    get_shop_token,
    rotate_shop_token,
    save_email_orders_settings,
)
from app.utils.permissions import permission_required
from app.utils.tenant import get_shop_db, oid as _oid

INBOX_LIST_LIMIT = 100


def build_email_orders_state(master, shop_db, shop: dict) -> dict:
    """UI state for the integrations card / modal."""
    settings = get_email_orders_settings(shop_db, shop["_id"])
    token = get_shop_token(shop)
    return {
        **settings,
        "address": address_for_token(token) if token else "",
        "has_address": bool(token),
        "last_email_at": settings["last_email_at"].isoformat() if settings.get("last_email_at") else None,
        "created_at": settings["created_at"].isoformat() if settings.get("created_at") else None,
        "updated_at": settings["updated_at"].isoformat() if settings.get("updated_at") else None,
    }


def _ctx():
    """(master, shop_db, shop, error_response) for the active shop, tenant-checked."""
    master = get_master_db()
    shop_db, shop = get_shop_db(master)
    if shop_db is None or shop is None:
        return None, None, None, (jsonify({"ok": False, "error": "No active shop in session."}), 400)
    return master, shop_db, shop, None


@settings_bp.get("/integrations/email_orders/state")
@login_required
@permission_required("settings.manage_integrations")
def integrations_email_orders_state():
    master, shop_db, shop, err = _ctx()
    if err:
        return err
    return jsonify({"ok": True, "state": build_email_orders_state(master, shop_db, shop)})


@settings_bp.post("/integrations/email_orders/settings")
@login_required
@permission_required("settings.manage_integrations")
def integrations_email_orders_save():
    """Body: {"enabled": bool, "mode": "auto"|"suggest"}. Enabling for the
    first time issues the location's mailbox address."""
    master, shop_db, shop, err = _ctx()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    mode = data.get("mode")
    if mode is not None and str(mode).lower() not in MODES:
        return jsonify({"ok": False, "error": "Unknown mode."}), 400

    actor = _oid(session.get(SESSION_USER_ID))
    if enabled:
        token = ensure_shop_token(master, shop["_id"], shop.get("tenant_id"))
        if not token:
            return jsonify({"ok": False, "error": "Could not issue a mailbox address for this location."}), 500
    save_email_orders_settings(
        shop_db, shop["_id"],
        enabled=bool(enabled) if enabled is not None else None,
        mode=str(mode).lower() if mode is not None else None,
        actor_user_id=actor,
    )
    shop = master.shops.find_one({"_id": shop["_id"]}) or shop
    return jsonify({"ok": True, "state": build_email_orders_state(master, shop_db, shop)})


@settings_bp.post("/integrations/email_orders/rotate")
@login_required
@permission_required("settings.manage_integrations")
def integrations_email_orders_rotate():
    """Issue a new address; the old one stops routing immediately (use when
    the address leaked to spammers)."""
    master, shop_db, shop, err = _ctx()
    if err:
        return err
    token = rotate_shop_token(master, shop["_id"], shop.get("tenant_id"))
    if not token:
        return jsonify({"ok": False, "error": "Could not rotate the mailbox address."}), 500
    shop = master.shops.find_one({"_id": shop["_id"]}) or shop
    return jsonify({"ok": True, "state": build_email_orders_state(master, shop_db, shop)})


@settings_bp.get("/integrations/email_orders/inbox")
@login_required
@permission_required("settings.manage_integrations")
def integrations_email_orders_inbox():
    master, shop_db, shop, err = _ctx()
    if err:
        return err
    try:
        limit = max(1, min(INBOX_LIST_LIMIT, int(request.args.get("limit") or 50)))
    except (TypeError, ValueError):
        limit = 50
    rows = list(
        shop_db.inbound_emails.find(
            {"shop_id": shop["_id"]},
            {"attachments.data": 0, "text": 0},
        ).sort("received_at", -1).limit(limit)
    )
    order_ids = [r.get("parts_order_id") for r in rows if r.get("parts_order_id")]
    orders_map = {}
    if order_ids:
        for o in shop_db.parts_orders.find({"_id": {"$in": order_ids}}, {"order_number": 1, "is_active": 1}):
            orders_map[o["_id"]] = o
    return jsonify({
        "ok": True,
        "emails": [serialize_inbound_email(r, orders_map=orders_map) for r in rows],
        "counts": {
            "total": shop_db.inbound_emails.count_documents({"shop_id": shop["_id"]}),
            "orders": shop_db.inbound_emails.count_documents(
                {"shop_id": shop["_id"], "status": {"$in": [STATUS_ORDER_CREATED, STATUS_LINKED]}}
            ),
        },
    })


@settings_bp.get("/integrations/email_orders/inbox/<email_id>")
@login_required
@permission_required("settings.manage_integrations")
def integrations_email_orders_view(email_id: str):
    """One email with its text body (forwarding confirmation codes / links
    from Gmail, Yahoo, iCloud land here — the owner reads them in this view)."""
    master, shop_db, shop, err = _ctx()
    if err:
        return err
    eid = _oid(email_id)
    if not eid:
        return jsonify({"ok": False, "error": "Invalid email id."}), 400
    doc = shop_db.inbound_emails.find_one({"_id": eid, "shop_id": shop["_id"]}, {"attachments.data": 0})
    if not doc:
        return jsonify({"ok": False, "error": "Email not found."}), 404
    orders_map = {}
    if doc.get("parts_order_id"):
        o = shop_db.parts_orders.find_one({"_id": doc["parts_order_id"]}, {"order_number": 1, "is_active": 1})
        if o:
            orders_map[o["_id"]] = o
    payload = serialize_inbound_email(doc, orders_map=orders_map)
    payload["text"] = str(doc.get("text") or "")[:20000]
    payload["to"] = list(doc.get("to") or [])
    payload["extracted_items"] = list(((doc.get("extracted") or {}).get("items")) or [])[:100]
    return jsonify({"ok": True, "email": payload})


def _load_email(shop_db, shop: dict, email_id: str):
    eid = _oid(email_id)
    if not eid:
        return None, (jsonify({"ok": False, "error": "Invalid email id."}), 400)
    doc = shop_db.inbound_emails.find_one({"_id": eid, "shop_id": shop["_id"]}, {"_id": 1, "status": 1})
    if not doc:
        return None, (jsonify({"ok": False, "error": "Email not found."}), 404)
    return doc, None


@settings_bp.post("/integrations/email_orders/inbox/<email_id>/process")
@login_required
@permission_required("settings.manage_integrations")
def integrations_email_orders_process(email_id: str):
    """Re-run the pipeline for one email. Body {"force": true} skips the AI
    classification and creates an order even from an ignored / suggested /
    failed email (a human decided it IS an order)."""
    master, shop_db, shop, err = _ctx()
    if err:
        return err
    doc, err = _load_email(shop_db, shop, email_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    force = bool(data.get("force"))
    if force and doc.get("status") not in FORCEABLE_STATUSES:
        return jsonify({"ok": False, "error": f"Email in status '{doc.get('status')}' cannot be turned into an order."}), 400
    result = process_inbound_email(
        master, shop, shop_db, doc["_id"], force=force,
        actor_user_id=_oid(session.get(SESSION_USER_ID)),
    )
    fresh = shop_db.inbound_emails.find_one({"_id": doc["_id"]}, {"attachments.data": 0, "text": 0})
    orders_map = {}
    if fresh and fresh.get("parts_order_id"):
        o = shop_db.parts_orders.find_one({"_id": fresh["parts_order_id"]}, {"order_number": 1, "is_active": 1})
        if o:
            orders_map[o["_id"]] = o
    status_code = 200 if result.get("ok") else 400
    return jsonify({
        "ok": bool(result.get("ok")),
        "error": None if result.get("ok") else result.get("message"),
        "message": result.get("message"),
        "status": result.get("status"),
        "order_id": result.get("order_id"),
        "email": serialize_inbound_email(fresh or {}, orders_map=orders_map),
    }), status_code


@settings_bp.post("/integrations/email_orders/inbox/<email_id>/ignore")
@login_required
@permission_required("settings.manage_integrations")
def integrations_email_orders_ignore(email_id: str):
    """A human says "this is not an order" for a pending / suggested / failed email."""
    master, shop_db, shop, err = _ctx()
    if err:
        return err
    doc, err = _load_email(shop_db, shop, email_id)
    if err:
        return err
    if doc.get("status") in (STATUS_ORDER_CREATED, STATUS_LINKED):
        return jsonify({"ok": False, "error": "An order was already created from this email — reject the order instead."}), 400
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    shop_db.inbound_emails.update_one(
        {"_id": doc["_id"]},
        {"$set": {"status": STATUS_IGNORED, "updated_at": now, "processed_at": now,
                  "ignored_by": _oid(session.get(SESSION_USER_ID)), "error": None,
                  "classification": {"kind": "other", "is_parts_order": False, "confidence": 1.0,
                                     "reason": "Marked as not an order by a user."}},
         "$unset": {"attachments.$[].data": ""}},
    )
    fresh = shop_db.inbound_emails.find_one({"_id": doc["_id"]}, {"attachments.data": 0, "text": 0})
    return jsonify({"ok": True, "email": serialize_inbound_email(fresh or {})})
