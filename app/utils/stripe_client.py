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


def apply_billing_discount(amount_cents: int, tenant: dict) -> tuple:
    """
    Индивидуальная цена тенанта. tenant.billing_discount (ставится из
    админки, admin_panel.tenant_set_discount):
      {"type": "percent", "value": 20}  → скидка 20% от расчётной суммы
      {"type": "fixed",   "value": 250} → фикс $250/мес, расчёт игнорируется

    Возвращает (amount_cents_после_скидки, описание | None). Невалидная
    или отсутствующая скидка — сумма без изменений.
    """
    discount = tenant.get("billing_discount") or {}
    dtype = discount.get("type")
    try:
        value = float(discount.get("value"))
    except (TypeError, ValueError):
        return amount_cents, None
    if dtype == "percent" and 0 < value <= 100:
        discounted = int(round(amount_cents * (100.0 - value) / 100.0))
        return discounted, f"{value:g}% discount"
    if dtype == "fixed" and value >= 0:
        return int(round(value * 100)), "custom price"
    return amount_cents, None


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
    period_days: int = 30,
    days_until_due: int = 7,
    purpose: str = "manual",
) -> dict:
    """
    Create + finalize a one-shot invoice for the tenant's current billable
    units. If `auto_charge` is True, Stripe attempts to charge the saved
    default payment method immediately. Otherwise it sends a hosted
    invoice email.

    Every invoice is mirrored into `master.billing_invoices` — the webhook
    uses that doc as an idempotency guard when extending the subscription,
    and the renewal cron uses it to avoid double-billing a cycle.

    Returns: {"invoice_id": "in_...", "amount_cents": int, "hosted_url": str|None,
              "status": "open"|"paid"|...}
    """
    s = _stripe()

    customer_id = get_or_create_customer(tenant)
    counts = count_billable(tenant["_id"])
    amount, discount_desc = apply_billing_discount(compute_amount_cents(counts), tenant)
    if amount <= 0:
        raise ValueError(
            "Tenant has no billable amount (no active locations/users, "
            "or the custom price is $0)."
        )

    period_label = f"{period_days} days"
    line_description = _line_description(counts, period_label)
    if discount_desc:
        line_description += f" — {discount_desc}"

    # Step 1: pending invoice item attached to the customer.
    s.InvoiceItem.create(
        customer=customer_id,
        amount=amount,
        currency=BILLING_CURRENCY,
        description=line_description,
        metadata={
            "tenant_id": str(tenant["_id"]),
            "locations_active": str(counts["locations_active"]),
            "full_active": str(counts["full_active"]),
            "mech_active": str(counts["mech_active"]),
        },
    )

    # Step 2: invoice that consumes pending items.
    # NOTE: automatic_tax is intentionally OFF for now — Stripe Tax requires
    # a verified business head office address which sandbox/test accounts
    # often lack. Re-enable by setting STRIPE_AUTOMATIC_TAX=true once the
    # live account is fully onboarded.
    automatic_tax_enabled = (
        (current_app.config.get("STRIPE_AUTOMATIC_TAX") or "").lower() == "true"
    )
    invoice_kwargs = dict(
        customer=customer_id,
        auto_advance=True,                 # finalize automatically
        automatic_tax={"enabled": automatic_tax_enabled},
        # Pull the pending InvoiceItem we just created. Stripe API >= 2022-08-01
        # defaults to "exclude", which silently produced $0 invoices.
        pending_invoice_items_behavior="include",
        metadata={
            "tenant_id": str(tenant["_id"]),
            "tenant_slug": tenant.get("slug") or "",
            "period_label": period_label,
            "period_days": str(period_days),
            "purpose": purpose,
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

    if auto_charge:
        # Stripe сам пытается оплатить charge_automatically-инвойс лишь
        # ~через час после финализации — списываем сразу, чтобы админский
        # charge-now и ночной renewal получали мгновенный результат.
        # Отказ карты не роняет вызов: инвойс остаётся open, Stripe будет
        # ретраить, а invoice.payment_failed запускает dunning.
        try:
            inv = s.Invoice.pay(inv.id)
        except stripe.error.CardError:
            current_app.logger.exception(
                "Immediate charge failed for invoice %s (tenant %s)",
                inv.id, tenant.get("slug"),
            )
            inv = s.Invoice.retrieve(inv.id)

    # Mirror into our ledger. `status`/`extended` are $setOnInsert only:
    # a charge_automatically invoice can be paid synchronously during
    # finalize, so the invoice.paid webhook may have upserted this doc
    # already — we must not overwrite what the handler wrote.
    get_master_db().billing_invoices.update_one(
        {"_id": inv.id},
        {
            "$set": {
                "tenant_id": tenant["_id"],
                "amount_cents": amount,
                "period_days": period_days,
                "collection_method": invoice_kwargs["collection_method"],
                "purpose": purpose,
                "discount": discount_desc,
                "hosted_invoice_url": getattr(inv, "hosted_invoice_url", None),
            },
            "$setOnInsert": {
                "status": getattr(inv, "status", None) or "open",
                "extended": False,
                "created_at": datetime.utcnow(),
            },
        },
        upsert=True,
    )

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


def charge_saved_card(
    tenant: dict, period_days: int = 30, purpose: str = "manual"
) -> dict:
    """
    Convenience wrapper for the renewal path: create an invoice with
    `charge_automatically` so Stripe immediately bills the saved card.
    Raises ValueError if no default payment method is on file.
    """
    s = _stripe()
    customer_id = get_or_create_customer(tenant)
    cust = s.Customer.retrieve(customer_id)
    # StripeObject в SDK >= 15 — не dict (.get() нет), только атрибуты.
    inv_settings = getattr(cust, "invoice_settings", None)
    default_pm = (
        (getattr(inv_settings, "default_payment_method", None) if inv_settings else None)
        or getattr(cust, "default_source", None)
    )
    if not default_pm:
        raise ValueError(
            "Tenant has no saved payment method. Send an invoice first so "
            "the customer can add a card on the hosted page."
        )
    return create_billing_invoice(
        tenant, auto_charge=True, period_days=period_days, purpose=purpose
    )


def ensure_default_payment_method(
    tenant: dict,
    inv: Optional[dict] = None,
    preferred_pm_id: Optional[str] = None,
    force: bool = False,
) -> Optional[dict]:
    """
    Если у клиента ещё нет default payment method, но карта прикреплена
    (attached PM) — делаем её дефолтной, иначе renewal-cron никогда не
    сможет списывать автоматически (Stripe сохраняет карту, но дефолтной
    сам не ставит). Вызывается из webhook'ов invoice.paid (inv) и
    payment_method.attached (preferred_pm_id).

    force=True — сделать preferred_pm_id дефолтной, даже если дефолтная
    уже есть: тенант явно добавил новую карту («Update card»), списания
    должны переехать на неё, а не остаться на старой.

    Возвращает кэш карты для tenant-дока ({"pm_id", "brand", "last4", ...})
    либо None, если делать нечего.
    """
    s = _stripe()
    customer_id = (tenant.get("stripe_customer_id") or "").strip() \
        or ((inv or {}).get("customer") or "")
    if not customer_id:
        return None

    cust = s.Customer.retrieve(customer_id)
    inv_settings = getattr(cust, "invoice_settings", None)
    current_default = getattr(inv_settings, "default_payment_method", None) \
        if inv_settings else None
    if current_default and not force:
        return None  # дефолтная карта уже есть
    if current_default and force and current_default == preferred_pm_id:
        return None  # уже дефолтная — нечего делать

    pms = s.Customer.list_payment_methods(customer_id, type="card")
    pm_list = list(getattr(pms, "data", None) or [])
    if not pm_list:
        return None  # тенант не сохранял карту — остаёмся на инвойсах письмом

    target = None
    if preferred_pm_id:
        target = next((pm for pm in pm_list if pm.id == preferred_pm_id), None)
    # Предпочитаем карту, которой оплатили именно этот инвойс.
    pi_id = (inv or {}).get("payment_intent")
    if target is None and pi_id and isinstance(pi_id, str):
        try:
            pi = s.PaymentIntent.retrieve(pi_id)
            paying_pm = getattr(pi, "payment_method", None)
            target = next((pm for pm in pm_list if pm.id == paying_pm), None)
        except stripe.error.StripeError:
            current_app.logger.exception(
                "Could not resolve paying card for invoice %s", (inv or {}).get("id")
            )
    if target is None and not force and len(pm_list) == 1:
        target = pm_list[0]
    if target is None:
        # force без найденной preferred-карты — существующую дефолтную не трогаем.
        return None

    s.Customer.modify(
        customer_id,
        invoice_settings={"default_payment_method": target.id},
    )
    return _payment_method_cache_entry(target)


def _payment_method_cache_entry(pm) -> dict:
    """PaymentMethod (StripeObject) → dict для tenant.stripe_default_card."""
    card = getattr(pm, "card", None)
    cached = {"pm_id": pm.id}
    if card is not None:
        cached.update({
            "brand": getattr(card, "brand", None),
            "last4": getattr(card, "last4", None),
            "exp_month": getattr(card, "exp_month", None),
            "exp_year": getattr(card, "exp_year", None),
        })
    return cached


def describe_payment_method(pm_id: str) -> dict:
    """
    Кэш-запись карты по pm-id (для webhook'ов, где Stripe отдаёт
    default_payment_method строкой без деталей). Ошибка Stripe не
    скрывается — раскручивается наверх, вызывающий решает сам.
    """
    s = _stripe()
    return _payment_method_cache_entry(s.PaymentMethod.retrieve(pm_id))


def create_card_setup_session(
    tenant: dict, success_url: str, cancel_url: str
) -> str:
    """
    Hosted-страница Stripe Checkout (mode=setup) для привязки карты: тенант
    вводит карту, Stripe прикрепляет её к Customer (событие
    payment_method.attached), наш webhook делает её дефолтной. Возвращает
    URL страницы — хендлер делает redirect.
    """
    s = _stripe()
    customer_id = get_or_create_customer(tenant)
    sess = s.checkout.Session.create(
        mode="setup",
        customer=customer_id,
        payment_method_types=["card"],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"tenant_id": str(tenant["_id"]), "purpose": "card_setup"},
    )
    return sess.url
