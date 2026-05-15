"""
Stripe integration for Roobico subscription billing.

Architecture:
  * Roobico is the source of truth for the *amount*: we count active
    locations / full users / mechanics in the master DB and compute the
    monthly total ourselves (PRICE_PER_* constants in admin_panel.routes).
  * Stripe is just the payment processor: we create one Customer per
    tenant, then per billing cycle we create a single Invoice with one
    line item describing the breakdown.
  * The first invoice (right after the 30-day trial) is sent via
    `collection_method=send_invoice` — Stripe emails the tenant a hosted
    invoice page where they pay and the card gets saved.
  * Subsequent invoices are `collection_method=charge_automatically`
    against the saved default payment method.
  * Stripe Tax computes US sales tax on the invoice automatically.

This module is intentionally thin: route handlers and cron call into
`get_or_create_customer`, `create_billing_invoice`, etc.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from flask import current_app

import stripe

from app.extensions import get_master_db


# ---------------------------------------------------------------------------
# Pricing (kept in sync with admin_panel.routes.PRICE_PER_*).
# ---------------------------------------------------------------------------
PRICE_PER_LOCATION_CENTS = 100_00   # $100/mo per active location
PRICE_PER_FULL_USER_CENTS = 50_00   # $50/mo per active full user
PRICE_PER_MECHANIC_CENTS = 25_00    # $25/mo per active mechanic
BILLING_CURRENCY = "usd"


# ---------------------------------------------------------------------------
# Stripe client init.
# ---------------------------------------------------------------------------

def _stripe():
    """
    Return the configured `stripe` module (singleton). Sets api_key on
    every call so config changes (key rotation) are picked up without a
    restart.
    """
    key = current_app.config.get("STRIPE_SECRET_KEY") or ""
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")
    stripe.api_key = key
    # Pin API version so behaviour is reproducible across SDK upgrades.
    stripe.api_version = "2024-06-20"
    return stripe


def stripe_configured() -> bool:
    return bool(current_app.config.get("STRIPE_SECRET_KEY"))


def dashboard_url(path: str) -> str:
    """Build a Stripe Dashboard link, respecting test/live mode."""
    base = "https://dashboard.stripe.com"
    if current_app.config.get("STRIPE_TEST_MODE"):
        base += "/test"
    if not path.startswith("/"):
        path = "/" + path
    return base + path


# ---------------------------------------------------------------------------
# Billing math (mirrors admin_panel.routes computation, in cents).
# ---------------------------------------------------------------------------

def count_billable(tenant_id) -> dict:
    """
    Returns active counts for the given tenant_id from master DB.
    """
    master = get_master_db()
    MECHANIC_ROLES = {"mechanic", "senior_mechanic"}

    locations_active = master.shops.count_documents(
        {"tenant_id": tenant_id, "is_active": True}
    )
    users = list(master.users.find(
        {"tenant_id": tenant_id, "is_active": True},
        {"role": 1},
    ))
    full_active = mech_active = 0
    for u in users:
        role = (u.get("role") or "").strip().lower()
        if role in MECHANIC_ROLES:
            mech_active += 1
        else:
            full_active += 1
    return {
        "locations_active": locations_active,
        "full_active": full_active,
        "mech_active": mech_active,
    }


def compute_amount_cents(counts: dict) -> int:
    return (
        counts["locations_active"] * PRICE_PER_LOCATION_CENTS
        + counts["full_active"] * PRICE_PER_FULL_USER_CENTS
        + counts["mech_active"] * PRICE_PER_MECHANIC_CENTS
    )


def describe_breakdown(counts: dict) -> str:
    parts = []
    if counts["locations_active"]:
        parts.append(f"{counts['locations_active']} location(s) × $100")
    if counts["full_active"]:
        parts.append(f"{counts['full_active']} full user(s) × $50")
    if counts["mech_active"]:
        parts.append(f"{counts['mech_active']} mechanic(s) × $25")
    return " + ".join(parts) if parts else "no billable units"


# ---------------------------------------------------------------------------
# Customer.
# ---------------------------------------------------------------------------

def get_or_create_customer(tenant: dict) -> str:
    """
    Returns the Stripe customer id for `tenant`, creating it if missing.
    Persists `stripe_customer_id` on the tenant doc.
    """
    s = _stripe()
    master = get_master_db()

    cid = (tenant.get("stripe_customer_id") or "").strip()
    if cid:
        return cid

    cust = s.Customer.create(
        name=tenant.get("name") or "(unnamed)",
        email=(tenant.get("billing_email") or tenant.get("email") or "").strip() or None,
        phone=(tenant.get("billing_phone") or tenant.get("phone") or "").strip() or None,
        metadata={
            "tenant_id": str(tenant["_id"]),
            "tenant_slug": tenant.get("slug") or "",
        },
    )
    master.tenants.update_one(
        {"_id": tenant["_id"]},
        {"$set": {
            "stripe_customer_id": cust.id,
            "updated_at": datetime.utcnow(),
        }},
    )
    return cust.id


# ---------------------------------------------------------------------------
# Invoice creation.
# ---------------------------------------------------------------------------

def _line_description(counts: dict, period_label: str) -> str:
    return f"Roobico subscription — {period_label} ({describe_breakdown(counts)})"


def create_billing_invoice(
    tenant: dict,
    *,
    auto_charge: bool,
    period_label: str = "30 days",
    days_until_due: int = 7,
) -> dict:
    """
    Create + finalize a one-shot invoice for the tenant's current billable
    units. If `auto_charge` is True, Stripe attempts to charge the saved
    default payment method immediately. Otherwise it sends a hosted
    invoice email.

    Returns: {"invoice_id": "in_...", "amount_cents": int, "hosted_url": str|None,
              "status": "open"|"paid"|...}
    """
    s = _stripe()

    customer_id = get_or_create_customer(tenant)
    counts = count_billable(tenant["_id"])
    amount = compute_amount_cents(counts)
    if amount <= 0:
        raise ValueError("Tenant has no billable units (no active locations/users).")

    # Step 1: pending invoice item attached to the customer.
    s.InvoiceItem.create(
        customer=customer_id,
        amount=amount,
        currency=BILLING_CURRENCY,
        description=_line_description(counts, period_label),
        metadata={
            "tenant_id": str(tenant["_id"]),
            "locations_active": str(counts["locations_active"]),
            "full_active": str(counts["full_active"]),
            "mech_active": str(counts["mech_active"]),
        },
    )

    # Step 2: invoice that consumes pending items.
    invoice_kwargs = dict(
        customer=customer_id,
        auto_advance=True,                 # finalize automatically
        automatic_tax={"enabled": True},   # Stripe Tax computes sales tax
        metadata={
            "tenant_id": str(tenant["_id"]),
            "tenant_slug": tenant.get("slug") or "",
            "period_label": period_label,
        },
    )
    if auto_charge:
        invoice_kwargs["collection_method"] = "charge_automatically"
    else:
        invoice_kwargs["collection_method"] = "send_invoice"
        invoice_kwargs["days_until_due"] = days_until_due

    inv = s.Invoice.create(**invoice_kwargs)
    # Finalize so it gets a hosted_invoice_url and number.
    inv = s.Invoice.finalize_invoice(inv.id)

    if not auto_charge:
        # Trigger Stripe to email the tenant the hosted invoice link.
        try:
            s.Invoice.send_invoice(inv.id)
        except stripe.error.StripeError:
            # Not fatal — admin can resend manually from Stripe dashboard.
            current_app.logger.exception("Failed to send Stripe invoice email")

    return {
        "invoice_id": inv.id,
        "amount_cents": amount,
        "hosted_url": getattr(inv, "hosted_invoice_url", None),
        "status": getattr(inv, "status", None),
        "counts": counts,
    }


def charge_saved_card(tenant: dict, period_label: str = "30 days") -> dict:
    """
    Convenience wrapper for the renewal path: create an invoice with
    `charge_automatically` so Stripe immediately bills the saved card.
    Raises if no default payment method is on file.
    """
    s = _stripe()
    customer_id = get_or_create_customer(tenant)
    cust = s.Customer.retrieve(customer_id)
    default_pm = (
        (cust.get("invoice_settings") or {}).get("default_payment_method")
        or cust.get("default_source")
    )
    if not default_pm:
        raise ValueError(
            "Tenant has no saved payment method. Send an invoice first so "
            "the customer can add a card on the hosted page."
        )
    return create_billing_invoice(tenant, auto_charge=True, period_label=period_label)
