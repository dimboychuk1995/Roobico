"""
Тенантская страница подписки: Settings → Billing (owner-only через
permission `settings.manage_billing`).

  * GET  /settings/billing            — статус подписки, цена, карта, инвойсы
  * POST /settings/billing/setup-card — hosted-страница Stripe Checkout
        (mode=setup) для привязки/замены карты; после привязки webhook
        payment_method.attached делает карту дефолтной.

ВАЖНО: endpoint'ы без префикса `admin_` — enforce_host_split считает
`billing.admin_*` админскими и не пускает их на app-хост.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import stripe
from bson import ObjectId
from bson.errors import InvalidId
from flask import current_app, flash, redirect, request, session, url_for

from app import PAST_DUE_GRACE_DAYS
from app.blueprints.main.routes import NAV_ITEMS
from app.extensions import get_master_db
from app.utils.auth import login_required, SESSION_TENANT_ID
from app.utils.display_datetime import format_date_mmddyyyy
from app.utils.layout import render_internal_page
from app.utils.permissions import filter_nav_items, permission_required
from app.utils.stripe_client import (
    ANNUAL_DISCOUNT_PERCENT,
    PRICE_PER_FULL_USER_CENTS,
    PRICE_PER_LOCATION_CENTS,
    PRICE_PER_MECHANIC_CENTS,
    annual_amount_cents,
    apply_billing_discount,
    billing_period,
    compute_amount_cents,
    count_billable,
    extra_counts,
    create_billing_invoice,
    create_card_setup_session,
    create_payment_checkout_session,
    stripe_configured,
)
from . import billing_bp


def _session_tenant(master):
    raw = session.get(SESSION_TENANT_ID)
    try:
        tid = ObjectId(str(raw))
    except (InvalidId, TypeError):
        return None
    return master.tenants.find_one({"_id": tid})


def _subscription_view(tenant: dict) -> dict:
    """Статус подписки для страницы: state ∈ active / ending_soon /
    past_due / grace / expired / none."""
    now = datetime.utcnow()
    until = tenant.get("subscription_until")
    status = (tenant.get("subscription_status") or "").lower()
    days_left = None
    if isinstance(until, datetime):
        seconds_left = (until - now).total_seconds()
        days_left = int(seconds_left // 86400)
        if seconds_left <= 0:
            in_grace = (
                status == "past_due"
                and until + timedelta(days=PAST_DUE_GRACE_DAYS) > now
            )
            state = "grace" if in_grace else "expired"
        elif status == "past_due":
            state = "past_due"
        elif days_left <= 7:
            state = "ending_soon"
        else:
            state = "active"
    else:
        state = "expired" if status == "expired" else "none"
    return {
        "until": until,
        "until_display": format_date_mmddyyyy(until) if until else None,
        "days_left": days_left,
        "state": state,
        "status_raw": status,
        "is_trial": status == "trial",
    }


@billing_bp.get("/settings/billing")
@login_required
@permission_required("settings.manage_billing")
def subscription_page():
    master = get_master_db()
    tenant = _session_tenant(master)
    if not tenant:
        flash("Session data mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    counts = count_billable(tenant["_id"])
    extra = extra_counts(counts)
    computed_cents = compute_amount_cents(counts)
    effective_cents, discount_desc = apply_billing_discount(computed_cents, tenant)
    period = billing_period(tenant)
    annual_cents = annual_amount_cents(effective_cents)
    due_cents = annual_cents if period == "annual" else effective_cents
    price = {
        # Период биллинга и суммы к оплате: годовая = 12 месячных − 20%.
        "period": period,
        "annual_total": annual_cents / 100,
        "annual_full": effective_cents * 12 / 100,
        "annual_savings": (effective_cents * 12 - annual_cents) / 100,
        "annual_discount_percent": ANNUAL_DISCOUNT_PERCENT,
        "due_total": due_cents / 100,
        "locations": counts["locations_active"],
        "full_users": counts["full_active"],
        "mechanics": counts["mech_active"],
        # Бесплатный тир: первая локация и первый полный юзер — $0;
        # в строках разбивки показываем только «лишние» юниты.
        "extra_locations": extra["extra_locations"],
        "extra_full_users": extra["extra_full"],
        "is_free": effective_cents <= 0,
        "locations_cost": extra["extra_locations"] * PRICE_PER_LOCATION_CENTS / 100,
        "full_cost": extra["extra_full"] * PRICE_PER_FULL_USER_CENTS / 100,
        "mech_cost": counts["mech_active"] * PRICE_PER_MECHANIC_CENTS / 100,
        "per_location": PRICE_PER_LOCATION_CENTS / 100,
        "per_full_user": PRICE_PER_FULL_USER_CENTS / 100,
        "per_mechanic": PRICE_PER_MECHANIC_CENTS / 100,
        "base_total": computed_cents / 100,
        "discount_desc": discount_desc,
        "monthly_total": effective_cents / 100,
    }

    invoices = list(
        master.billing_invoices
        .find({"tenant_id": tenant["_id"]})
        .sort("created_at", -1)
        .limit(12)
    )
    for inv in invoices:
        inv["date_display"] = format_date_mmddyyyy(inv.get("created_at"))
    open_invoice = next((i for i in invoices if i.get("status") in ("open", "draft")), None)

    return render_internal_page(
        "public/settings/billing.html",
        filter_nav_items(NAV_ITEMS),
        "settings",
        tenant=tenant,
        subscription=_subscription_view(tenant),
        price=price,
        invoices=invoices,
        open_invoice=open_invoice,
        stripe_ready=stripe_configured(),
        grace_days=PAST_DUE_GRACE_DAYS,
        card_saved=request.args.get("card") == "saved",
        payment_done=request.args.get("paid") == "1",
    )


@billing_bp.post("/settings/billing/period")
@login_required
@permission_required("settings.manage_billing")
def set_billing_period():
    """
    Переключение периода подписки владельцем: monthly ↔ annual.
    Влияет на будущие инвойсы (Pay now и авто-продление); уже выставленные
    открытые инвойсы не пересчитываются.
    """
    master = get_master_db()
    tenant = _session_tenant(master)
    if not tenant:
        flash("Session data mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    period = (request.form.get("period") or "").strip().lower()
    if period not in ("monthly", "annual"):
        flash("Invalid billing period.", "error")
        return redirect(url_for("billing.subscription_page"))

    master.tenants.update_one(
        {"_id": tenant["_id"]},
        {"$set": {"billing_period": period, "updated_at": datetime.utcnow()}},
    )
    if period == "annual":
        flash(
            f"Switched to annual billing — you save {ANNUAL_DISCOUNT_PERCENT}% "
            "on every renewal.",
            "success",
        )
    else:
        flash("Switched to monthly billing.", "success")
    return redirect(url_for("billing.subscription_page"))


@billing_bp.post("/settings/billing/pay-now")
@login_required
@permission_required("settings.manage_billing")
def pay_now():
    """
    Оплатить подписку прямо сейчас: если по тенанту уже висит открытый
    инвойс — ведём на его hosted-страницу; иначе создаём новый на текущую
    сумму и ведём платить. Оплата через webhook продлевает подписку —
    заблокированный owner разблокируется сам.
    """
    if not stripe_configured():
        flash("Online payments are not available right now. Please contact support.", "error")
        return redirect(url_for("billing.subscription_page"))

    master = get_master_db()
    tenant = _session_tenant(master)
    if not tenant:
        flash("Session data mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    open_invoice = master.billing_invoices.find_one(
        {"tenant_id": tenant["_id"], "status": {"$in": ["open", "draft"]}},
        sort=[("created_at", -1)],
    )
    if open_invoice and open_invoice.get("hosted_invoice_url"):
        return redirect(open_invoice["hosted_invoice_url"])

    base = url_for("billing.subscription_page", _external=True)
    try:
        if tenant.get("stripe_default_card"):
            # Карта уже на файле — обычный инвойс, hosted-страница даст
            # оплатить сохранённой картой в один клик.
            result = create_billing_invoice(
                tenant, auto_charge=False, purpose="self_service", days_until_due=7,
            )
            target_url = result.get("hosted_url")
        else:
            # Карты нет — Checkout с setup_future_usage: hosted-инвойс карту
            # не сохраняет, а Checkout прикрепит её для автосписаний.
            target_url = create_payment_checkout_session(
                tenant,
                success_url=base + "?paid=1",
                cancel_url=base,
            )
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("billing.subscription_page"))
    except stripe.error.StripeError:
        current_app.logger.exception(
            "Failed to start self-service payment for tenant %s", tenant.get("slug")
        )
        flash("Could not start the payment. Please try again later.", "error")
        return redirect(url_for("billing.subscription_page"))

    if not target_url:
        flash("Invoice created — check your email for the payment link.", "success")
        return redirect(url_for("billing.subscription_page"))
    return redirect(target_url)


@billing_bp.post("/settings/billing/setup-card")
@login_required
@permission_required("settings.manage_billing")
def setup_card():
    if not stripe_configured():
        flash("Online payments are not available right now. Please contact support.", "error")
        return redirect(url_for("billing.subscription_page"))

    master = get_master_db()
    tenant = _session_tenant(master)
    if not tenant:
        flash("Session data mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    base = url_for("billing.subscription_page", _external=True)
    try:
        checkout_url = create_card_setup_session(
            tenant,
            success_url=base + "?card=saved",
            cancel_url=base,
        )
    except stripe.error.StripeError:
        current_app.logger.exception(
            "Failed to create card setup session for tenant %s", tenant.get("slug")
        )
        flash("Could not open the card setup page. Please try again later.", "error")
        return redirect(url_for("billing.subscription_page"))

    return redirect(checkout_url)
