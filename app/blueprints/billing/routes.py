"""
Billing routes:

  * POST /billing/stripe/webhook
        Public endpoint, signature-verified. Handles `invoice.paid`,
        `invoice.payment_failed`, etc.
  * POST /admin/tenants/<id>/billing/send-invoice
  * POST /admin/tenants/<id>/billing/charge-now
        Admin-only triggers (used from the tenant detail page in the
        admin panel). They live here (instead of in admin_panel.routes)
        to keep all Stripe-touching code in one place.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import stripe
from bson import ObjectId
from bson.errors import InvalidId
from flask import (
    abort, current_app, flash, redirect, request, url_for, jsonify
)

from app.extensions import get_master_db
from app.utils.admin_audit import log_admin_action
from app.utils.admin_auth import admin_required, get_current_admin
from app.utils.stripe_client import (
    charge_saved_card,
    compute_amount_cents,
    count_billable,
    create_billing_invoice,
    stripe_configured,
)
from . import billing_bp


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        abort(404)


# ---------------------------------------------------------------------------
# Admin-triggered actions.
# ---------------------------------------------------------------------------

@billing_bp.post("/admin/tenants/<tenant_id>/billing/send-invoice")
@admin_required
def admin_send_invoice(tenant_id: str):
    if not stripe_configured():
        flash("Stripe is not configured (missing STRIPE_SECRET_KEY).", "error")
        return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))

    admin = get_current_admin()
    master = get_master_db()
    tid = _oid(tenant_id)
    tenant = master.tenants.find_one({"_id": tid})
    if not tenant:
        abort(404)

    try:
        result = create_billing_invoice(
            tenant,
            auto_charge=False,
            period_label="30 days",
            days_until_due=7,
        )
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))
    except stripe.error.StripeError as e:
        current_app.logger.exception("Stripe error sending invoice")
        flash(f"Stripe error: {e.user_message or str(e)}", "error")
        return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))

    log_admin_action(
        admin,
        action="tenant.billing.send_invoice",
        target_type="tenant",
        target_id=tid,
        before=None,
        after={"invoice_id": result["invoice_id"], "amount_cents": result["amount_cents"]},
        extra={
            "tenant_name": tenant.get("name"),
            "counts": result["counts"],
            "hosted_url": result.get("hosted_url"),
        },
    )
    flash(
        f"Invoice sent: ${result['amount_cents']/100:,.2f} "
        f"(Stripe id {result['invoice_id']}).",
        "success",
    )
    return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))


@billing_bp.post("/admin/tenants/<tenant_id>/billing/charge-now")
@admin_required
def admin_charge_now(tenant_id: str):
    if not stripe_configured():
        flash("Stripe is not configured (missing STRIPE_SECRET_KEY).", "error")
        return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))

    admin = get_current_admin()
    master = get_master_db()
    tid = _oid(tenant_id)
    tenant = master.tenants.find_one({"_id": tid})
    if not tenant:
        abort(404)

    try:
        result = charge_saved_card(tenant, period_label="30 days")
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))
    except stripe.error.StripeError as e:
        current_app.logger.exception("Stripe error charging saved card")
        flash(f"Stripe error: {e.user_message or str(e)}", "error")
        return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))

    log_admin_action(
        admin,
        action="tenant.billing.charge_now",
        target_type="tenant",
        target_id=tid,
        before=None,
        after={"invoice_id": result["invoice_id"], "amount_cents": result["amount_cents"]},
        extra={"tenant_name": tenant.get("name"), "counts": result["counts"]},
    )
    flash(
        f"Charge attempted: ${result['amount_cents']/100:,.2f} "
        f"(invoice {result['invoice_id']}, status {result['status']}).",
        "success",
    )
    return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))


# ---------------------------------------------------------------------------
# Stripe webhook.
# ---------------------------------------------------------------------------

@billing_bp.post("/billing/stripe/webhook")
def stripe_webhook():
    """
    Receives Stripe webhook events. Verifies signature with the configured
    STRIPE_WEBHOOK_SECRET; rejects everything else.

    Handled events:
      invoice.paid              → bump subscription_until +30 days, status=active
      invoice.payment_failed    → status=past_due
      invoice.finalized         → log only
      customer.updated          → keep default_payment_method label in sync
    """
    secret = current_app.config.get("STRIPE_WEBHOOK_SECRET") or ""
    if not secret:
        current_app.logger.warning("Stripe webhook hit but STRIPE_WEBHOOK_SECRET not set")
        return jsonify({"ok": False, "error": "webhook secret not configured"}), 503

    payload = request.get_data(as_text=False)
    sig = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, secret)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        current_app.logger.warning("Stripe webhook signature verification failed: %s", e)
        return jsonify({"ok": False, "error": "bad signature"}), 400

    etype = event.get("type") or ""
    obj = (event.get("data") or {}).get("object") or {}

    handler_map = {
        "invoice.paid": _handle_invoice_paid,
        "invoice.payment_succeeded": _handle_invoice_paid,  # legacy alias
        "invoice.payment_failed": _handle_invoice_failed,
        "customer.updated": _handle_customer_updated,
    }
    handler = handler_map.get(etype)
    if handler:
        try:
            handler(obj, event)
        except Exception:
            current_app.logger.exception("Stripe webhook handler crashed for %s", etype)
            # 500 makes Stripe retry; we want that.
            return jsonify({"ok": False}), 500
    # Unhandled events: 200 so Stripe stops retrying.
    return jsonify({"ok": True, "type": etype}), 200


# ---------------------------------------------------------------------------
# Webhook handlers.
# ---------------------------------------------------------------------------

def _tenant_from_invoice(inv: dict):
    """
    Resolve tenant doc by invoice metadata.tenant_id (set when we created
    the invoice) or by stripe_customer_id as fallback.
    """
    master = get_master_db()
    tid_raw = (inv.get("metadata") or {}).get("tenant_id")
    if tid_raw:
        try:
            return master.tenants.find_one({"_id": ObjectId(tid_raw)})
        except (InvalidId, TypeError):
            pass
    customer_id = inv.get("customer")
    if customer_id:
        return master.tenants.find_one({"stripe_customer_id": customer_id})
    return None


def _handle_invoice_paid(inv: dict, event: dict) -> None:
    master = get_master_db()
    tenant = _tenant_from_invoice(inv)
    if not tenant:
        current_app.logger.warning("invoice.paid for unknown tenant: %s", inv.get("id"))
        return

    # Extend subscription by 30 days from the later of (now, current end).
    now = datetime.utcnow()
    current_until = tenant.get("subscription_until")
    base = current_until if (isinstance(current_until, datetime) and current_until > now) else now
    new_until = base + timedelta(days=30)

    master.tenants.update_one(
        {"_id": tenant["_id"]},
        {"$set": {
            "subscription_until": new_until,
            "subscription_status": "active",
            "last_paid_at": now,
            "last_invoice_id": inv.get("id"),
            "last_invoice_amount_cents": inv.get("amount_paid") or inv.get("amount_due"),
            "updated_at": now,
        }},
    )
    # Mirror into our admin audit so we can see payment events alongside
    # manual extensions.
    master.admin_audit.insert_one({
        "admin_id": None,
        "admin_email": "stripe-webhook",
        "action": "tenant.billing.invoice_paid",
        "target_type": "tenant",
        "target_id": tenant["_id"],
        "before": {"subscription_until": current_until},
        "after": {"subscription_until": new_until},
        "extra": {
            "invoice_id": inv.get("id"),
            "amount_paid": inv.get("amount_paid"),
            "hosted_invoice_url": inv.get("hosted_invoice_url"),
            "tenant_name": tenant.get("name"),
        },
        "ts": now,
    })


def _handle_invoice_failed(inv: dict, event: dict) -> None:
    master = get_master_db()
    tenant = _tenant_from_invoice(inv)
    if not tenant:
        return
    now = datetime.utcnow()
    master.tenants.update_one(
        {"_id": tenant["_id"]},
        {"$set": {
            "subscription_status": "past_due",
            "last_invoice_id": inv.get("id"),
            "last_invoice_amount_cents": inv.get("amount_due"),
            "updated_at": now,
        }},
    )
    master.admin_audit.insert_one({
        "admin_id": None,
        "admin_email": "stripe-webhook",
        "action": "tenant.billing.invoice_failed",
        "target_type": "tenant",
        "target_id": tenant["_id"],
        "before": None,
        "after": {"subscription_status": "past_due"},
        "extra": {
            "invoice_id": inv.get("id"),
            "amount_due": inv.get("amount_due"),
            "attempt_count": inv.get("attempt_count"),
            "next_payment_attempt": inv.get("next_payment_attempt"),
            "tenant_name": tenant.get("name"),
        },
        "ts": now,
    })


def _handle_customer_updated(cust: dict, event: dict) -> None:
    """
    Cache the default card brand/last4 so the admin UI can show it
    without an extra Stripe API call.
    """
    master = get_master_db()
    customer_id = cust.get("id")
    if not customer_id:
        return
    tenant = master.tenants.find_one({"stripe_customer_id": customer_id})
    if not tenant:
        return

    default_pm = (cust.get("invoice_settings") or {}).get("default_payment_method")
    update = {"updated_at": datetime.utcnow()}
    if isinstance(default_pm, dict):
        card = default_pm.get("card") or {}
        update["stripe_default_card"] = {
            "brand": card.get("brand"),
            "last4": card.get("last4"),
            "exp_month": card.get("exp_month"),
            "exp_year": card.get("exp_year"),
            "pm_id": default_pm.get("id"),
        }
    elif isinstance(default_pm, str):
        update["stripe_default_card"] = {"pm_id": default_pm}

    master.tenants.update_one({"_id": tenant["_id"]}, {"$set": update})
