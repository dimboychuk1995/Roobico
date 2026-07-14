"""
Автопродление подписок: ядро ночного cron'а (app/scripts/billing_renewals.py).

Модель:
  * За RENEWAL_LEAD_DAYS до конца оплаченного периода тенанту создаётся
    Stripe-инвойс: с сохранённой картой — charge_automatically (списание
    сразу), без карты — send_invoice (письмо с hosted-страницей, где тенант
    платит и карта сохраняется на будущее).
  * Продление subscription_until делает НЕ этот код, а webhook
    invoice.paid — здесь только создание инвойса.
  * Защита от двойного биллинга: пока по тенанту есть незакрытый инвойс
    (billing_invoices.status in open/draft), новый не создаётся. Statuses
    ведут webhook-хендлеры (paid/void/uncollectible/refunded).

Идемпотентен: повторный запуск за ту же ночь не создаёт второй инвойс.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import stripe
from flask import current_app

from app.extensions import get_master_db
from app.utils.stripe_client import (
    apply_billing_discount,
    charge_saved_card,
    compute_amount_cents,
    count_billable,
    create_billing_invoice,
    stripe_configured,
)

# За сколько дней до конца периода выставляем инвойс на продление.
RENEWAL_LEAD_DAYS = 3
# Открытый инвойс старше этого срока — сигнал, что цикл завис (тенант не
# платит, Stripe исчерпал ретраи): пишем warning, но второй инвойс всё
# равно не создаём — разруливается руками из админки.
STALE_OPEN_INVOICE_DAYS = 14


def run_renewals(*, now: datetime | None = None, dry_run: bool = False,
                 tenant_slug: str = "") -> dict:
    """
    Прогоняет все активные тенанты, возвращает счётчики по исходам.
    Требует app context и настроенный Stripe.
    """
    if not stripe_configured():
        raise RuntimeError("Stripe is not configured (missing STRIPE_SECRET_KEY).")

    master = get_master_db()
    now = now or datetime.utcnow()
    horizon = now + timedelta(days=RENEWAL_LEAD_DAYS)
    stats = {
        "checked": 0, "charged": 0, "invoiced": 0, "errors": 0,
        "skipped_not_due": 0, "skipped_open_invoice": 0,
        "skipped_no_subscription": 0, "skipped_no_billable": 0,
    }

    query = {"status": "active"}
    if tenant_slug:
        query["slug"] = tenant_slug

    for tenant in master.tenants.find(query).sort("slug", 1):
        stats["checked"] += 1
        slug = tenant.get("slug") or str(tenant["_id"])

        until = tenant.get("subscription_until")
        if not isinstance(until, datetime):
            # Legacy-тенант без подписки: не биллим втихую, но и не молчим —
            # такие закрываются скриптом backfill_subscriptions.
            stats["skipped_no_subscription"] += 1
            current_app.logger.warning(
                "renewals: tenant %s has no subscription_until — run "
                "app.scripts.backfill_subscriptions", slug,
            )
            continue

        if until > horizon:
            stats["skipped_not_due"] += 1
            continue

        open_inv = master.billing_invoices.find_one(
            {"tenant_id": tenant["_id"], "status": {"$in": ["open", "draft"]}},
            sort=[("created_at", -1)],
        )
        if open_inv:
            stats["skipped_open_invoice"] += 1
            created = open_inv.get("created_at")
            if isinstance(created, datetime) and \
                    created <= now - timedelta(days=STALE_OPEN_INVOICE_DAYS):
                current_app.logger.warning(
                    "renewals: tenant %s has open invoice %s older than %s days "
                    "— needs manual attention in the admin panel",
                    slug, open_inv["_id"], STALE_OPEN_INVOICE_DAYS,
                )
            continue

        counts = count_billable(tenant["_id"])
        amount_cents, _ = apply_billing_discount(compute_amount_cents(counts), tenant)
        if amount_cents <= 0:
            # Нет billable-юнитов либо админ поставил фикс $0 (бесплатный тенант).
            stats["skipped_no_billable"] += 1
            continue

        if dry_run:
            # Не трогаем ни Stripe, ни базу — только считаем как due.
            stats["charged"] += 1
            current_app.logger.info("renewals[dry-run]: tenant %s is due", slug)
            continue

        try:
            try:
                result = charge_saved_card(tenant, purpose="auto_renewal")
                method = "auto_charge"
                stats["charged"] += 1
            except ValueError:
                # Нет сохранённой карты (или нет billable — отсечено выше):
                # шлём hosted-инвойс письмом, карта сохранится при оплате.
                result = create_billing_invoice(
                    tenant, auto_charge=False, purpose="auto_renewal",
                )
                method = "invoice_email"
                stats["invoiced"] += 1
        except stripe.error.StripeError:
            stats["errors"] += 1
            current_app.logger.exception(
                "renewals: Stripe error while billing tenant %s", slug
            )
            continue

        master.admin_audit.insert_one({
            "admin_id": None,
            "admin_email": "billing-renewal",
            "action": "tenant.billing.auto_renewal",
            "target_type": "tenant",
            "target_id": tenant["_id"],
            "before": {"subscription_until": until},
            "after": None,  # продление сделает webhook invoice.paid
            "extra": {
                "tenant_name": tenant.get("name"),
                "method": method,
                "invoice_id": result["invoice_id"],
                "amount_cents": result["amount_cents"],
            },
            "ts": datetime.utcnow(),
        })

    return stats
