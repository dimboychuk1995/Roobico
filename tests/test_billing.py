"""
Биллинг подписок (Stripe): идемпотентность webhook, продление/откат периода,
dunning, grace-логика блокировки и ночной renewal-цикл.

Stripe не вызывается: construct_event подменяется на разбор JSON, а в
renewal-тестах подменяются charge_saved_card / create_billing_invoice.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
import stripe
from bson import ObjectId

from app import PAST_DUE_GRACE_DAYS, _is_tenant_subscription_blocked


# ---------------------------------------------------------------------------
# Helpers / fixtures.
# ---------------------------------------------------------------------------

@pytest.fixture()
def webhook_secret(app):
    app.config["STRIPE_WEBHOOK_SECRET"] = "whsec_test"
    yield
    app.config["STRIPE_WEBHOOK_SECRET"] = ""


@pytest.fixture()
def fake_signature(monkeypatch):
    """Подменяет проверку подписи Stripe: событие — это просто JSON payload."""
    monkeypatch.setattr(
        stripe.Webhook, "construct_event",
        lambda payload, sig, secret: json.loads(payload),
    )


@pytest.fixture()
def sent_emails(monkeypatch):
    sent = []

    def _capture(to_address, subject, html_body, **kwargs):
        sent.append({"to": to_address, "subject": subject, "html": html_body})

    monkeypatch.setattr("app.blueprints.billing.routes.send_email", _capture)
    return sent


def _post_event(client, event):
    return client.post(
        "/billing/stripe/webhook",
        data=json.dumps(event),
        content_type="application/json",
        headers={"Stripe-Signature": "t=1,v1=fake"},
    )


def _make_tenant(master, slug, **overrides):
    doc = {
        "_id": ObjectId(),
        "slug": slug,
        "name": f"Billing {slug}",
        "db_name": f"roobico_test_billing_{slug}",
        "email": f"{slug}@test.local",
        "billing_email": f"billing-{slug}@test.local",
        "created_at": datetime.utcnow(),
    }
    doc.update(overrides)
    master.tenants.insert_one(doc)
    return doc


def _invoice_event(event_id, etype, tenant, invoice_id, **obj_overrides):
    obj = {
        "id": invoice_id,
        "metadata": {"tenant_id": str(tenant["_id"]), "period_days": "30"},
        "amount_paid": 17500,
        "amount_due": 17500,
        "hosted_invoice_url": "https://invoice.stripe.com/i/test",
    }
    obj.update(obj_overrides)
    return {"id": event_id, "type": etype, "data": {"object": obj}}


# ---------------------------------------------------------------------------
# Webhook: paid → продление, дедуп по event и по invoice.
# ---------------------------------------------------------------------------

def test_invoice_paid_extends_subscription(client, app, webhook_secret, fake_signature):
    from app.extensions import get_master_db
    with app.app_context():
        master = get_master_db()
        until = datetime.utcnow() + timedelta(days=2)
        tenant = _make_tenant(
            master, "paid-extend",
            subscription_status="trial", subscription_until=until,
        )

    resp = _post_event(client, _invoice_event("evt_paid_1", "invoice.paid", tenant, "in_paid_1"))
    assert resp.status_code == 200

    with app.app_context():
        master = get_master_db()
        fresh = master.tenants.find_one({"_id": tenant["_id"]})
        # Продление от конца текущего периода, не от "сейчас".
        expected = until + timedelta(days=30)
        assert abs((fresh["subscription_until"] - expected).total_seconds()) < 60
        assert fresh["subscription_status"] == "active"
        assert fresh["last_invoice_id"] == "in_paid_1"

        ledger = master.billing_invoices.find_one({"_id": "in_paid_1"})
        assert ledger["extended"] is True
        assert ledger["status"] == "paid"
        assert master.admin_audit.count_documents(
            {"action": "tenant.billing.invoice_paid", "target_id": tenant["_id"]}
        ) == 1


def test_invoice_paid_from_expired_extends_from_now(client, app, webhook_secret, fake_signature):
    from app.extensions import get_master_db
    with app.app_context():
        master = get_master_db()
        tenant = _make_tenant(
            master, "paid-late",
            subscription_status="past_due",
            subscription_until=datetime.utcnow() - timedelta(days=3),
        )

    resp = _post_event(client, _invoice_event("evt_late_1", "invoice.paid", tenant, "in_late_1"))
    assert resp.status_code == 200

    with app.app_context():
        fresh = get_master_db().tenants.find_one({"_id": tenant["_id"]})
        expected = datetime.utcnow() + timedelta(days=30)
        assert abs((fresh["subscription_until"] - expected).total_seconds()) < 60
        assert fresh["subscription_status"] == "active"


def test_webhook_event_redelivery_is_deduped(client, app, webhook_secret, fake_signature):
    from app.extensions import get_master_db
    with app.app_context():
        master = get_master_db()
        tenant = _make_tenant(
            master, "dedup-event",
            subscription_status="active",
            subscription_until=datetime.utcnow() + timedelta(days=5),
        )

    event = _invoice_event("evt_dedup_1", "invoice.paid", tenant, "in_dedup_1")
    assert _post_event(client, event).status_code == 200

    with app.app_context():
        until_after_first = get_master_db().tenants.find_one(
            {"_id": tenant["_id"]})["subscription_until"]

    # Stripe передоставил то же событие: 200, но продления нет.
    resp = _post_event(client, event)
    assert resp.status_code == 200
    assert resp.get_json().get("duplicate") is True

    with app.app_context():
        fresh = get_master_db().tenants.find_one({"_id": tenant["_id"]})
        assert fresh["subscription_until"] == until_after_first


def test_paid_and_legacy_succeeded_extend_once(client, app, webhook_secret, fake_signature):
    """invoice.paid и legacy invoice.payment_succeeded за один платёж —
    разные event id, но продление должно случиться один раз."""
    from app.extensions import get_master_db
    with app.app_context():
        master = get_master_db()
        tenant = _make_tenant(
            master, "dedup-invoice",
            subscription_status="active",
            subscription_until=datetime.utcnow() + timedelta(days=5),
        )

    assert _post_event(client, _invoice_event(
        "evt_pair_1", "invoice.paid", tenant, "in_pair_1")).status_code == 200
    with app.app_context():
        until_after_first = get_master_db().tenants.find_one(
            {"_id": tenant["_id"]})["subscription_until"]

    assert _post_event(client, _invoice_event(
        "evt_pair_2", "invoice.payment_succeeded", tenant, "in_pair_1")).status_code == 200

    with app.app_context():
        fresh = get_master_db().tenants.find_one({"_id": tenant["_id"]})
        assert fresh["subscription_until"] == until_after_first


def test_webhook_rejects_bad_signature(client, app, webhook_secret):
    # Без подмены construct_event настоящая проверка подписи должна дать 400.
    resp = _post_event(client, {"id": "evt_bad", "type": "invoice.paid"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Webhook: payment_failed → past_due + dunning-письмо.
# ---------------------------------------------------------------------------

def test_invoice_failed_sets_past_due_and_emails(client, app, webhook_secret,
                                                 fake_signature, sent_emails):
    from app.extensions import get_master_db
    with app.app_context():
        master = get_master_db()
        tenant = _make_tenant(
            master, "dunning",
            subscription_status="active",
            subscription_until=datetime.utcnow() + timedelta(days=1),
        )

    event = _invoice_event("evt_fail_1", "invoice.payment_failed", tenant, "in_fail_1",
                           attempt_count=1)
    assert _post_event(client, event).status_code == 200

    with app.app_context():
        fresh = get_master_db().tenants.find_one({"_id": tenant["_id"]})
        assert fresh["subscription_status"] == "past_due"

    assert len(sent_emails) == 1
    assert sent_emails[0]["to"] == tenant["billing_email"]
    assert "https://invoice.stripe.com/i/test" in sent_emails[0]["html"]


# ---------------------------------------------------------------------------
# Webhook: refund → откат продления, идемпотентно.
# ---------------------------------------------------------------------------

def _refund_event(event_id, invoice_id, *, amount=17500, refunded_amount=17500,
                  full=True):
    return {
        "id": event_id,
        "type": "charge.refunded",
        "data": {"object": {
            "id": "ch_test_1",
            "invoice": invoice_id,
            "amount": amount,
            "amount_refunded": refunded_amount,
            "refunded": full,
        }},
    }


def test_full_refund_rolls_back_extension(client, app, webhook_secret, fake_signature):
    from app.extensions import get_master_db
    until = datetime.utcnow() + timedelta(days=40)
    with app.app_context():
        master = get_master_db()
        tenant = _make_tenant(
            master, "refund-full",
            subscription_status="active", subscription_until=until,
        )
        master.billing_invoices.insert_one({
            "_id": "in_refund_1", "tenant_id": tenant["_id"],
            "period_days": 30, "extended": True, "status": "paid",
            "created_at": datetime.utcnow(),
        })

    assert _post_event(client, _refund_event("evt_ref_1", "in_refund_1")).status_code == 200

    with app.app_context():
        master = get_master_db()
        fresh = master.tenants.find_one({"_id": tenant["_id"]})
        expected = until - timedelta(days=30)
        assert abs((fresh["subscription_until"] - expected).total_seconds()) < 60
        assert master.billing_invoices.find_one({"_id": "in_refund_1"})["status"] == "refunded"

    # Повторное refund-событие (другой event id) не откатывает второй раз.
    assert _post_event(client, _refund_event("evt_ref_2", "in_refund_1")).status_code == 200
    with app.app_context():
        fresh = get_master_db().tenants.find_one({"_id": tenant["_id"]})
        assert abs((fresh["subscription_until"] - expected).total_seconds()) < 60


def test_full_refund_of_current_period_expires_tenant(client, app, webhook_secret,
                                                      fake_signature):
    from app.extensions import get_master_db
    until = datetime.utcnow() + timedelta(days=10)  # минус 30 → в прошлом
    with app.app_context():
        master = get_master_db()
        tenant = _make_tenant(
            master, "refund-expire",
            subscription_status="active", subscription_until=until,
        )
        master.billing_invoices.insert_one({
            "_id": "in_refund_2", "tenant_id": tenant["_id"],
            "period_days": 30, "extended": True, "status": "paid",
            "created_at": datetime.utcnow(),
        })

    assert _post_event(client, _refund_event("evt_ref_3", "in_refund_2")).status_code == 200

    with app.app_context():
        fresh = get_master_db().tenants.find_one({"_id": tenant["_id"]})
        assert fresh["subscription_status"] == "expired"


def test_partial_refund_keeps_subscription(client, app, webhook_secret, fake_signature):
    from app.extensions import get_master_db
    until = datetime.utcnow() + timedelta(days=40)
    with app.app_context():
        master = get_master_db()
        tenant = _make_tenant(
            master, "refund-partial",
            subscription_status="active", subscription_until=until,
        )
        master.billing_invoices.insert_one({
            "_id": "in_refund_3", "tenant_id": tenant["_id"],
            "period_days": 30, "extended": True, "status": "paid",
            "created_at": datetime.utcnow(),
        })

    event = _refund_event("evt_ref_4", "in_refund_3",
                          refunded_amount=5000, full=False)
    assert _post_event(client, event).status_code == 200

    with app.app_context():
        master = get_master_db()
        fresh = master.tenants.find_one({"_id": tenant["_id"]})
        # Mongo хранит даты с точностью до миллисекунд — сравниваем с допуском.
        assert abs((fresh["subscription_until"] - until).total_seconds()) < 1
        assert master.billing_invoices.find_one({"_id": "in_refund_3"})["status"] == "paid"
        assert master.admin_audit.count_documents(
            {"action": "tenant.billing.partial_refund", "target_id": tenant["_id"]}
        ) == 1


def test_invoice_voided_closes_ledger(client, app, webhook_secret, fake_signature):
    from app.extensions import get_master_db
    with app.app_context():
        master = get_master_db()
        tenant = _make_tenant(master, "voided")
        master.billing_invoices.insert_one({
            "_id": "in_void_1", "tenant_id": tenant["_id"],
            "period_days": 30, "extended": False, "status": "open",
            "created_at": datetime.utcnow(),
        })

    event = _invoice_event("evt_void_1", "invoice.voided", tenant, "in_void_1")
    assert _post_event(client, event).status_code == 200

    with app.app_context():
        ledger = get_master_db().billing_invoices.find_one({"_id": "in_void_1"})
        assert ledger["status"] == "void"


# ---------------------------------------------------------------------------
# Блокировка: grace для past_due.
# ---------------------------------------------------------------------------

def test_subscription_block_rules():
    now = datetime.utcnow()
    # Нет полей подписки → пропускаем (legacy, закрывается бэкфиллом).
    assert _is_tenant_subscription_blocked({}) is False
    assert _is_tenant_subscription_blocked(
        {"subscription_until": now + timedelta(days=5)}) is False
    # Истёк trial / активная — блок сразу.
    assert _is_tenant_subscription_blocked(
        {"subscription_status": "trial",
         "subscription_until": now - timedelta(hours=1)}) is True
    # past_due в пределах grace — пускаем (Stripe ретраит карту).
    assert _is_tenant_subscription_blocked(
        {"subscription_status": "past_due",
         "subscription_until": now - timedelta(days=PAST_DUE_GRACE_DAYS - 1)}) is False
    # past_due после grace — блок.
    assert _is_tenant_subscription_blocked(
        {"subscription_status": "past_due",
         "subscription_until": now - timedelta(days=PAST_DUE_GRACE_DAYS + 1)}) is True
    # Явный expired — блок независимо от дат.
    assert _is_tenant_subscription_blocked(
        {"subscription_status": "expired",
         "subscription_until": now + timedelta(days=30)}) is True


# ---------------------------------------------------------------------------
# Renewal-цикл (Stripe подменён).
# ---------------------------------------------------------------------------

@pytest.fixture()
def renewal_env(app, monkeypatch):
    """Stripe 'настроен', charge/invoice подменены и записывают вызовы."""
    app.config["STRIPE_SECRET_KEY"] = "sk_test_fake"
    calls = []

    import app.blueprints.billing.services.renewals as renewals_mod

    def fake_charge(tenant, **kwargs):
        if not tenant.get("_test_has_card"):
            raise ValueError("no saved payment method")
        calls.append(("charge", tenant["slug"]))
        return {"invoice_id": "in_fake_charge", "amount_cents": 10000}

    def fake_invoice(tenant, **kwargs):
        calls.append(("invoice", tenant["slug"]))
        return {"invoice_id": "in_fake_email", "amount_cents": 10000}

    monkeypatch.setattr(renewals_mod, "charge_saved_card", fake_charge)
    monkeypatch.setattr(renewals_mod, "create_billing_invoice", fake_invoice)
    yield calls
    app.config["STRIPE_SECRET_KEY"] = ""


def _make_billable_tenant(master, slug, **overrides):
    tenant = _make_tenant(master, slug, status="active", **overrides)
    master.shops.insert_one({
        "_id": ObjectId(), "tenant_id": tenant["_id"],
        "name": f"Shop {slug}", "db_name": f"roobico_test_billing_shop_{slug}",
        "is_active": True, "created_at": datetime.utcnow(),
    })
    return tenant


def _run(app, slug, **kwargs):
    from app.blueprints.billing.services.renewals import run_renewals
    with app.app_context():
        return run_renewals(tenant_slug=slug, **kwargs)


def test_renewal_charges_saved_card(app, seed, renewal_env):
    from app.extensions import get_master_db
    with app.app_context():
        master = get_master_db()
        _make_billable_tenant(
            master, "renew-card",
            subscription_status="active",
            subscription_until=datetime.utcnow() + timedelta(days=1),
            _test_has_card=True,
        )

    stats = _run(app, "renew-card")
    assert stats["charged"] == 1 and stats["invoiced"] == 0
    assert renewal_env == [("charge", "renew-card")]

    with app.app_context():
        audit = get_master_db().admin_audit.find_one(
            {"action": "tenant.billing.auto_renewal",
             "extra.tenant_name": "Billing renew-card"})
        assert audit and audit["extra"]["method"] == "auto_charge"


def test_renewal_falls_back_to_email_invoice(app, seed, renewal_env):
    from app.extensions import get_master_db
    with app.app_context():
        _make_billable_tenant(
            get_master_db(), "renew-nocard",
            subscription_status="active",
            subscription_until=datetime.utcnow() + timedelta(days=1),
        )

    stats = _run(app, "renew-nocard")
    assert stats["charged"] == 0 and stats["invoiced"] == 1
    assert renewal_env == [("invoice", "renew-nocard")]


def test_renewal_skips_not_due_tenant(app, seed, renewal_env):
    from app.extensions import get_master_db
    with app.app_context():
        _make_billable_tenant(
            get_master_db(), "renew-notdue",
            subscription_status="active",
            subscription_until=datetime.utcnow() + timedelta(days=20),
            _test_has_card=True,
        )

    stats = _run(app, "renew-notdue")
    assert stats["skipped_not_due"] == 1
    assert renewal_env == []


def test_renewal_skips_open_invoice(app, seed, renewal_env):
    """Пока висит незакрытый инвойс — второй не создаём (нет двойного биллинга)."""
    from app.extensions import get_master_db
    with app.app_context():
        master = get_master_db()
        tenant = _make_billable_tenant(
            master, "renew-open",
            subscription_status="active",
            subscription_until=datetime.utcnow() + timedelta(days=1),
            _test_has_card=True,
        )
        master.billing_invoices.insert_one({
            "_id": "in_open_1", "tenant_id": tenant["_id"],
            "period_days": 30, "extended": False, "status": "open",
            "created_at": datetime.utcnow() - timedelta(days=1),
        })

    stats = _run(app, "renew-open")
    assert stats["skipped_open_invoice"] == 1
    assert renewal_env == []


def test_renewal_skips_tenant_without_subscription(app, seed, renewal_env):
    from app.extensions import get_master_db
    with app.app_context():
        _make_billable_tenant(get_master_db(), "renew-nosub",
                              _test_has_card=True)

    stats = _run(app, "renew-nosub")
    assert stats["skipped_no_subscription"] == 1
    assert renewal_env == []


def test_renewal_skips_tenant_without_billable_units(app, seed, renewal_env):
    from app.extensions import get_master_db
    with app.app_context():
        # Тенант без магазинов и юзеров — сумма 0, инвойс не создаём.
        _make_tenant(
            get_master_db(), "renew-empty", status="active",
            subscription_status="active",
            subscription_until=datetime.utcnow() + timedelta(days=1),
        )

    stats = _run(app, "renew-empty")
    assert stats["skipped_no_billable"] == 1
    assert renewal_env == []


def test_renewal_dry_run_touches_nothing(app, seed, renewal_env):
    from app.extensions import get_master_db
    with app.app_context():
        _make_billable_tenant(
            get_master_db(), "renew-dry",
            subscription_status="active",
            subscription_until=datetime.utcnow() + timedelta(days=1),
            _test_has_card=True,
        )

    stats = _run(app, "renew-dry", dry_run=True)
    assert stats["charged"] == 1
    assert renewal_env == []  # Stripe не тронут

    with app.app_context():
        assert get_master_db().admin_audit.count_documents(
            {"action": "tenant.billing.auto_renewal",
             "extra.tenant_name": "Billing renew-dry"}) == 0


def test_renewal_requires_stripe_config(app, seed):
    from app.blueprints.billing.services.renewals import run_renewals
    app.config["STRIPE_SECRET_KEY"] = ""
    with app.app_context():
        with pytest.raises(RuntimeError):
            run_renewals()


# ---------------------------------------------------------------------------
# Тенантская страница биллинга (Settings → Billing, owner-only).
# ---------------------------------------------------------------------------

def _expire_tenant_a(app, seed):
    from app.extensions import get_master_db
    with app.app_context():
        get_master_db().tenants.update_one(
            {"_id": seed["tenant_a"]["_id"]},
            {"$set": {"subscription_status": "trial",
                      "subscription_until": datetime.utcnow() - timedelta(days=1)}},
        )


def _restore_tenant_a(app, seed):
    """Seed-тенант живёт всю сессию — возвращаем как было (без подписки)."""
    from app.extensions import get_master_db
    with app.app_context():
        get_master_db().tenants.update_one(
            {"_id": seed["tenant_a"]["_id"]},
            {"$unset": {"subscription_status": "", "subscription_until": ""}},
        )


def test_owner_sees_billing_page(client, app, seed):
    from tests.conftest import login
    login(client)
    resp = client.get("/settings/billing")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Monthly price" in html
    assert "Payment method" in html


def test_billing_card_on_settings_landing(client, seed):
    from tests.conftest import login
    login(client)
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "/settings/billing" in resp.get_data(as_text=True)


def test_setup_card_redirects_to_checkout(client, app, seed, monkeypatch):
    from tests.conftest import login, get_csrf_token
    import app.blueprints.billing.tenant_routes as tenant_routes

    login(client)
    app.config["STRIPE_SECRET_KEY"] = "sk_test_fake"
    monkeypatch.setattr(
        tenant_routes, "create_card_setup_session",
        lambda tenant, success_url, cancel_url: "https://checkout.stripe.com/c/test-session",
    )
    try:
        token = get_csrf_token(client)
        resp = client.post("/settings/billing/setup-card", data={"csrf_token": token})
    finally:
        app.config["STRIPE_SECRET_KEY"] = ""
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("https://checkout.stripe.com/")


def test_blocked_owner_is_locked_to_billing_page(client, app, seed):
    from tests.conftest import login
    login(client)
    _expire_tenant_a(app, seed)
    try:
        # Любая страница приложения → редирект на биллинг.
        resp = client.get("/dashboard")
        assert resp.status_code == 302
        assert "/settings/billing" in resp.headers["Location"]
        # Сама страница биллинга доступна и показывает предупреждение.
        resp = client.get("/settings/billing")
        assert resp.status_code == 200
        assert "expired" in resp.get_data(as_text=True).lower()
    finally:
        _restore_tenant_a(app, seed)


def test_blocked_owner_login_lands_on_billing(client, app, seed):
    _expire_tenant_a(app, seed)
    try:
        from tests.conftest import login
        resp = login(client)
        assert resp.status_code == 302
        assert "/settings/billing" in resp.headers["Location"]
    finally:
        _restore_tenant_a(app, seed)


def test_blocked_non_owner_cannot_login(client, app, seed):
    from werkzeug.security import generate_password_hash
    from app.extensions import get_master_db
    with app.app_context():
        get_master_db().users.insert_one({
            "_id": ObjectId(),
            "email": "mechanic-billing@test.local",
            "password_hash": generate_password_hash("password123"),
            "is_active": True,
            "tenant_id": seed["tenant_a"]["_id"],
            "shop_ids": [str(seed["shop_a"]["_id"])],
            "role": "mechanic",
            "created_at": datetime.utcnow(),
        })
    _expire_tenant_a(app, seed)
    try:
        from tests.conftest import login
        resp = login(client, email="mechanic-billing@test.local")
        assert resp.status_code == 302
        assert "/settings/billing" not in (resp.headers.get("Location") or "")
        # Сессии нет — страница биллинга недоступна.
        resp = client.get("/settings/billing")
        assert resp.status_code in (302, 401, 403)
    finally:
        _restore_tenant_a(app, seed)
