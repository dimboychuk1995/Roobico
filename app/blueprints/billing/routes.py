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

import json
from datetime import datetime, timedelta

import stripe
from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError
from flask import (
    abort, current_app, flash, redirect, render_template, request, url_for, jsonify
)

from app import PAST_DUE_GRACE_DAYS
from app.extensions import get_master_db, csrf
from app.utils.admin_audit import log_admin_action
from app.utils.admin_auth import admin_required, get_current_admin
from app.utils.email_sender import send_email
from app.utils.stripe_client import (
    charge_saved_card,
    create_billing_invoice,
    describe_payment_method,
    ensure_default_payment_method,
    stripe_configured,
)
from . import billing_bp

# Default subscription period when an invoice carries no period metadata
# (e.g. created straight from the Stripe dashboard).
DEFAULT_PERIOD_DAYS = 30


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
            days_until_due=7,
            purpose="admin_manual",
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
        f"Invoice created: ${result['amount_cents']/100:,.2f} "
        f"(Stripe id {result['invoice_id']}). "
        + (f"Hosted page: {result['hosted_url']}" if result.get('hosted_url') else ""),
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
        result = charge_saved_card(tenant, purpose="admin_manual")
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
@csrf.exempt
def stripe_webhook():
    """
    Receives Stripe webhook events. Verifies signature with the configured
    STRIPE_WEBHOOK_SECRET; rejects everything else.

    Handled events:
      invoice.paid              → extend subscription_until, status=active
      invoice.payment_failed    → status=past_due + dunning email
      invoice.voided /
      invoice.marked_uncollectible → close the invoice in our ledger
      charge.refunded           → roll back the extension on full refund
      customer.updated          → keep default_payment_method label in sync

    Idempotency (two layers):
      * event level — every event id is recorded in master.stripe_events
        before its handler runs; a redelivered event is acknowledged
        without re-running the handler.
      * invoice level — subscription extension is guarded by an atomic
        flip of `extended` on the billing_invoices doc, so invoice.paid +
        legacy invoice.payment_succeeded for the same invoice extend once.
    """
    secret = current_app.config.get("STRIPE_WEBHOOK_SECRET") or ""
    if not secret:
        current_app.logger.warning("Stripe webhook hit but STRIPE_WEBHOOK_SECRET not set")
        return jsonify({"ok": False, "error": "webhook secret not configured"}), 503

    payload = request.get_data(as_text=False)
    sig = request.headers.get("Stripe-Signature", "")

    try:
        stripe.Webhook.construct_event(payload, sig, secret)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        current_app.logger.warning("Stripe webhook signature verification failed: %s", e)
        return jsonify({"ok": False, "error": "bad signature"}), 400

    # construct_event возвращает StripeObject без dict-интерфейса (в новых
    # SDK это не dict, .get() падает) — подпись проверена выше, событие
    # разбираем из сырого payload в обычные dict'ы.
    try:
        event = json.loads(payload)
    except ValueError:
        return jsonify({"ok": False, "error": "bad payload"}), 400

    etype = event.get("type") or ""
    obj = (event.get("data") or {}).get("object") or {}

    handler_map = {
        "invoice.paid": _handle_invoice_paid,
        "invoice.payment_succeeded": _handle_invoice_paid,  # legacy alias
        "invoice.payment_failed": _handle_invoice_failed,
        "invoice.voided": _handle_invoice_closed,
        "invoice.marked_uncollectible": _handle_invoice_closed,
        "charge.refunded": _handle_charge_refunded,
        "customer.updated": _handle_customer_updated,
        "payment_method.attached": _handle_payment_method_attached,
    }
    handler = handler_map.get(etype)
    if handler:
        master = get_master_db()
        event_id = event.get("id") or ""
        if event_id:
            try:
                master.stripe_events.insert_one({
                    "_id": event_id,
                    "type": etype,
                    "created_at": datetime.utcnow(),
                })
            except DuplicateKeyError:
                # Stripe redelivered an event we already processed.
                return jsonify({"ok": True, "type": etype, "duplicate": True}), 200
        try:
            handler(obj, event)
        except Exception:
            current_app.logger.exception("Stripe webhook handler crashed for %s", etype)
            # Forget the event so the retry Stripe is about to send
            # actually re-runs the handler instead of hitting the dedup.
            if event_id:
                master.stripe_events.delete_one({"_id": event_id})
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


def _billing_audit(action: str, tenant: dict, *, before, after, extra: dict) -> None:
    """Mirror billing events into admin audit, alongside manual extensions."""
    extra = dict(extra)
    extra.setdefault("tenant_name", tenant.get("name"))
    get_master_db().admin_audit.insert_one({
        "admin_id": None,
        "admin_email": "stripe-webhook",
        "action": action,
        "target_type": "tenant",
        "target_id": tenant["_id"],
        "before": before,
        "after": after,
        "extra": extra,
        "ts": datetime.utcnow(),
    })


def _invoice_period_days(inv: dict, ledger_doc: dict | None) -> int:
    raw = (inv.get("metadata") or {}).get("period_days")
    if raw:
        try:
            days = int(raw)
            if days > 0:
                return days
        except (TypeError, ValueError):
            pass
    if ledger_doc and isinstance(ledger_doc.get("period_days"), int):
        return ledger_doc["period_days"]
    return DEFAULT_PERIOD_DAYS


def _handle_invoice_paid(inv: dict, event: dict) -> None:
    master = get_master_db()
    tenant = _tenant_from_invoice(inv)
    if not tenant:
        current_app.logger.warning("invoice.paid for unknown tenant: %s", inv.get("id"))
        return

    inv_id = inv.get("id") or ""
    now = datetime.utcnow()
    ledger_doc = master.billing_invoices.find_one({"_id": inv_id}) if inv_id else None
    period_days = _invoice_period_days(inv, ledger_doc)

    # Atomic per-invoice guard: only the first paid-like event for this
    # invoice flips `extended` (invoice.paid and the legacy
    # invoice.payment_succeeded arrive as separate events for one payment).
    if inv_id:
        try:
            res = master.billing_invoices.update_one(
                {"_id": inv_id, "extended": {"$ne": True}},
                {
                    "$set": {
                        "status": "paid",
                        "extended": True,
                        "paid_at": now,
                        "amount_paid_cents": inv.get("amount_paid"),
                    },
                    "$setOnInsert": {
                        "tenant_id": tenant["_id"],
                        "period_days": period_days,
                        "created_at": now,
                    },
                },
                upsert=True,
            )
        except DuplicateKeyError:
            # Doc exists with extended=True → upsert collided on _id.
            return
        if res.matched_count == 0 and res.upserted_id is None:
            return

    # Extend subscription from the later of (now, current end).
    current_until = tenant.get("subscription_until")
    base = current_until if (isinstance(current_until, datetime) and current_until > now) else now
    new_until = base + timedelta(days=period_days)

    master.tenants.update_one(
        {"_id": tenant["_id"]},
        {"$set": {
            "subscription_until": new_until,
            "subscription_status": "active",
            "last_paid_at": now,
            "last_invoice_id": inv_id,
            "last_invoice_amount_cents": inv.get("amount_paid") or inv.get("amount_due"),
            "updated_at": now,
        }},
    )
    _billing_audit(
        "tenant.billing.invoice_paid", tenant,
        before={"subscription_until": current_until},
        after={"subscription_until": new_until},
        extra={
            "invoice_id": inv_id,
            "amount_paid": inv.get("amount_paid"),
            "period_days": period_days,
            "hosted_invoice_url": inv.get("hosted_invoice_url"),
        },
    )

    # Если тенант сохранил карту при оплате — делаем её дефолтной, чтобы
    # следующее продление прошло автосписанием. Не критично для самого
    # платежа, поэтому ошибки не роняют webhook.
    if stripe_configured():
        try:
            cached = ensure_default_payment_method(tenant, inv)
        except stripe.error.StripeError:
            current_app.logger.exception(
                "Failed to promote saved card to default for tenant %s",
                tenant.get("slug"),
            )
            cached = None
        if cached:
            master.tenants.update_one(
                {"_id": tenant["_id"]},
                {"$set": {"stripe_default_card": cached,
                          "updated_at": datetime.utcnow()}},
            )


def _handle_invoice_failed(inv: dict, event: dict) -> None:
    master = get_master_db()
    tenant = _tenant_from_invoice(inv)
    if not tenant:
        return
    now = datetime.utcnow()
    inv_id = inv.get("id") or ""
    master.tenants.update_one(
        {"_id": tenant["_id"]},
        {"$set": {
            "subscription_status": "past_due",
            "last_invoice_id": inv_id,
            "last_invoice_amount_cents": inv.get("amount_due"),
            "updated_at": now,
        }},
    )
    if inv_id:
        master.billing_invoices.update_one(
            {"_id": inv_id},
            {"$set": {
                "last_failed_at": now,
                "attempt_count": inv.get("attempt_count"),
            }},
        )
    _billing_audit(
        "tenant.billing.invoice_failed", tenant,
        before=None,
        after={"subscription_status": "past_due"},
        extra={
            "invoice_id": inv_id,
            "amount_due": inv.get("amount_due"),
            "attempt_count": inv.get("attempt_count"),
            "next_payment_attempt": inv.get("next_payment_attempt"),
        },
    )
    _send_payment_failed_email(tenant, inv)


def _send_payment_failed_email(tenant: dict, inv: dict) -> None:
    """Dunning: tell the tenant their card failed and give them the hosted
    invoice link to pay / update the card. Never fatal for the webhook."""
    to_address = (tenant.get("billing_email") or tenant.get("email") or "").strip()
    if not to_address:
        current_app.logger.warning(
            "Payment failed for tenant %s but no billing email on file", tenant.get("_id")
        )
        return
    amount_due = inv.get("amount_due") or 0
    try:
        html = render_template(
            "emails/billing_payment_failed.html",
            tenant_name=tenant.get("name") or "",
            amount_due=f"${amount_due / 100:,.2f}",
            hosted_invoice_url=inv.get("hosted_invoice_url"),
            grace_days=PAST_DUE_GRACE_DAYS,
        )
        send_email(to_address, "Roobico — payment failed, action required", html)
    except Exception:
        current_app.logger.exception(
            "Failed to send payment-failed email to %s", to_address
        )


def _handle_invoice_closed(inv: dict, event: dict) -> None:
    """invoice.voided / invoice.marked_uncollectible: close the invoice in
    our ledger so the renewal cron stops treating it as an open cycle."""
    master = get_master_db()
    inv_id = inv.get("id") or ""
    if not inv_id:
        return
    status = "void" if (event.get("type") == "invoice.voided") else "uncollectible"
    master.billing_invoices.update_one(
        {"_id": inv_id, "status": {"$ne": "paid"}},
        {"$set": {"status": status, "closed_at": datetime.utcnow()}},
    )
    tenant = _tenant_from_invoice(inv)
    if tenant:
        _billing_audit(
            "tenant.billing.invoice_closed", tenant,
            before=None,
            after={"invoice_status": status},
            extra={"invoice_id": inv_id, "amount_due": inv.get("amount_due")},
        )


def _handle_charge_refunded(charge: dict, event: dict) -> None:
    """
    Full refund of a subscription invoice → roll the extension back so the
    tenant doesn't keep the paid period. Partial refunds are audited only
    (the subscription stays as is; handle case-by-case from the admin).
    """
    master = get_master_db()
    inv_id = charge.get("invoice") or ""
    if not inv_id:
        return
    ledger_doc = master.billing_invoices.find_one({"_id": inv_id})
    if not ledger_doc:
        current_app.logger.warning("charge.refunded for unknown invoice: %s", inv_id)
        return
    tenant = master.tenants.find_one({"_id": ledger_doc.get("tenant_id")})
    if not tenant:
        return

    amount = charge.get("amount") or 0
    amount_refunded = charge.get("amount_refunded") or 0
    full_refund = bool(charge.get("refunded")) or (amount and amount_refunded >= amount)
    now = datetime.utcnow()

    if not full_refund:
        _billing_audit(
            "tenant.billing.partial_refund", tenant,
            before=None,
            after=None,
            extra={
                "invoice_id": inv_id,
                "amount_refunded": amount_refunded,
                "charge_id": charge.get("id"),
            },
        )
        return

    # Idempotency: only the first full-refund event rolls the period back.
    res = master.billing_invoices.update_one(
        {"_id": inv_id, "status": {"$ne": "refunded"}},
        {"$set": {"status": "refunded", "refunded_at": now}},
    )
    if res.modified_count == 0:
        return

    update = {"updated_at": now}
    before = {"subscription_until": tenant.get("subscription_until")}
    after = {}
    if ledger_doc.get("extended"):
        period_days = ledger_doc.get("period_days") or DEFAULT_PERIOD_DAYS
        current_until = tenant.get("subscription_until")
        if isinstance(current_until, datetime):
            new_until = current_until - timedelta(days=period_days)
            update["subscription_until"] = new_until
            after["subscription_until"] = new_until
            if new_until <= now:
                update["subscription_status"] = "expired"
                after["subscription_status"] = "expired"
    master.tenants.update_one({"_id": tenant["_id"]}, {"$set": update})
    _billing_audit(
        "tenant.billing.refunded", tenant,
        before=before,
        after=after,
        extra={
            "invoice_id": inv_id,
            "amount_refunded": amount_refunded,
            "charge_id": charge.get("id"),
        },
    )


def _handle_payment_method_attached(pm: dict, event: dict) -> None:
    """
    Карта прикрепилась к клиенту (любым путём: чекбокс на hosted-странице,
    режим save=always, setup-ссылка) → если дефолтной ещё нет, ставим эту
    и кэшируем на тенанте, чтобы заработало автосписание.
    """
    if not stripe_configured():
        return
    master = get_master_db()
    customer_id = pm.get("customer") or ""
    if not customer_id:
        return
    tenant = master.tenants.find_one({"stripe_customer_id": customer_id})
    if not tenant:
        return
    try:
        # force: явно добавленная карта (setup-страница / чекбокс на инвойсе)
        # становится дефолтной, даже если старая карта уже была — «Update
        # card» должен реально переключать списания на новую.
        cached = ensure_default_payment_method(
            tenant, preferred_pm_id=pm.get("id"), force=True
        )
    except stripe.error.StripeError:
        current_app.logger.exception(
            "Failed to promote attached card for tenant %s", tenant.get("slug")
        )
        return
    if cached:
        master.tenants.update_one(
            {"_id": tenant["_id"]},
            {"$set": {"stripe_default_card": cached,
                      "updated_at": datetime.utcnow()}},
        )


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
        # В webhook-объекте default_payment_method — просто id. Не
        # затираем уже закешированные brand/last4 той же карты, а для
        # новой карты дотягиваем детали отдельным запросом.
        existing = tenant.get("stripe_default_card") or {}
        if existing.get("pm_id") == default_pm and existing.get("last4"):
            return
        cached = {"pm_id": default_pm}
        if stripe_configured():
            try:
                cached = describe_payment_method(default_pm)
            except stripe.error.StripeError:
                current_app.logger.exception(
                    "Could not fetch card details for %s", default_pm
                )
        update["stripe_default_card"] = cached

    master.tenants.update_one({"_id": tenant["_id"]}, {"$set": update})
