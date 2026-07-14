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
    PRICE_PER_FULL_USER_CENTS,
    PRICE_PER_LOCATION_CENTS,
    PRICE_PER_MECHANIC_CENTS,
    compute_amount_cents,
    count_billable,
    create_card_setup_session,
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
    price = {
        "locations": counts["locations_active"],
        "full_users": counts["full_active"],
        "mechanics": counts["mech_active"],
        "locations_cost": counts["locations_active"] * PRICE_PER_LOCATION_CENTS / 100,
        "full_cost": counts["full_active"] * PRICE_PER_FULL_USER_CENTS / 100,
        "mech_cost": counts["mech_active"] * PRICE_PER_MECHANIC_CENTS / 100,
        "per_location": PRICE_PER_LOCATION_CENTS / 100,
        "per_full_user": PRICE_PER_FULL_USER_CENTS / 100,
        "per_mechanic": PRICE_PER_MECHANIC_CENTS / 100,
        "monthly_total": compute_amount_cents(counts) / 100,
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
        card=tenant.get("stripe_default_card"),
        invoices=invoices,
        open_invoice=open_invoice,
        stripe_ready=stripe_configured(),
        grace_days=PAST_DUE_GRACE_DAYS,
        card_saved=request.args.get("card") == "saved",
    )


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
