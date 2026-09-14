"""
Ящик локации → парт-ордеры: webhook (секрет, роутинг по токену, изоляция
тенантов, идемпотентность), обработка с замоканным AI (ордер с флагом
not confirmed, игнор мусора, линковка к существующему заказу, suggest-режим),
confirm/reject, блокировка receive/pay до подтверждения, настройки и история.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, SHOP_B_DB, TEST_MONGO_URI, get_csrf_token, login

SECRET = "test-inbound-secret"
TOKEN_A = "aaaa11112222"
TOKEN_B = "bbbb33334444"


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def inbox_env(app, seed, mongo, monkeypatch):
    """Ящики включены у обеих локаций; вендор + деталь у магазина A."""
    from app.extensions import get_master_db

    monkeypatch.setitem(app.config, "INBOUND_EMAIL_WEBHOOK_SECRET", SECRET)
    monkeypatch.setitem(app.config, "INBOUND_EMAIL_DOMAIN", "roobico.com")
    monkeypatch.setitem(app.config, "INBOUND_EMAIL_LOCAL_PREFIX", "orders-")
    # Обработка в тестах — явная, без фонового потока.
    monkeypatch.setitem(app.config, "INBOUND_EMAIL_PROCESS_INLINE", False)
    monkeypatch.setitem(app.config, "PUSH_SYNC", True)

    db_a = mongo[SHOP_A_DB]
    db_b = mongo[SHOP_B_DB]
    shop_a = seed["shop_a"]
    shop_b = seed["shop_b"]
    now = _now()

    with app.app_context():
        master = get_master_db()
        master.shops.update_one({"_id": shop_a["_id"]}, {"$set": {"inbound_email_token": TOKEN_A}})
        master.shops.update_one({"_id": shop_b["_id"]}, {"$set": {"inbound_email_token": TOKEN_B}})

    for db, shop in ((db_a, shop_a), (db_b, shop_b)):
        db.integrations.update_one(
            {"shop_id": shop["_id"], "provider": "email_orders"},
            {"$set": {"enabled": True, "mode": "auto", "updated_at": now},
             "$setOnInsert": {"created_at": now, "emails_received": 0, "orders_created": 0}},
            upsert=True,
        )

    vendor_id = db_a.vendors.insert_one({
        "shop_id": shop_a["_id"], "name": "Acme Truck Parts", "is_active": True,
        "email": "sales@acmetruck.com", "created_at": now,
    }).inserted_id
    part_id = db_a.parts.insert_one({
        "shop_id": shop_a["_id"], "part_number": "ACME-100", "description": "Brake drum",
        "in_stock": 0, "average_cost": 50.0, "is_active": True, "created_at": now,
    }).inserted_id

    yield {
        "db_a": db_a, "db_b": db_b, "shop_a": shop_a, "shop_b": shop_b,
        "vendor_id": vendor_id, "part_id": part_id,
    }

    for db, shop in ((db_a, shop_a), (db_b, shop_b)):
        db.inbound_emails.delete_many({"shop_id": shop["_id"]})
        db.parts_orders.delete_many({"shop_id": shop["_id"]})
        db.attachments.delete_many({"shop_id": shop["_id"]})
        db.integrations.delete_many({"shop_id": shop["_id"], "provider": "email_orders"})
    db_a.vendors.delete_many({"shop_id": shop_a["_id"]})
    db_a.parts.delete_many({"shop_id": shop_a["_id"]})
    with app.app_context():
        master = get_master_db()
        master.shops.update_many(
            {"_id": {"$in": [shop_a["_id"], shop_b["_id"]]}},
            {"$unset": {"inbound_email_token": "", "inbound_email_token_created_at": ""}},
        )


def _mime(*, subject="Order confirmation #INV-1", sender="Sales <sales@acmetruck.com>",
          to="orders-" + TOKEN_A + "@roobico.com", body="Thanks for your order.\nACME-100 x2 @ 55.00",
          message_id=None, attach_pdf=False, extra_headers=None) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = message_id or f"<{ObjectId()}@acmetruck.com>"
    for k, v in (extra_headers or {}).items():
        msg[k] = v
    msg.set_content(body)
    if attach_pdf:
        msg.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="invoice.pdf")
    return msg.as_bytes()


def _post_webhook(client, raw: bytes, *, secret=SECRET, envelope_to="orders-" + TOKEN_A + "@roobico.com"):
    headers = {"Content-Type": "message/rfc822", "X-Envelope-From": "sales@acmetruck.com"}
    if secret is not None:
        headers["X-Inbound-Secret"] = secret
    if envelope_to is not None:
        headers["X-Envelope-To"] = envelope_to
    return client.post("/inbound/email", data=raw, headers=headers)


class FakeAI:
    """Замена email_orders_ai: без сети, поведение задаётся в тесте."""

    def __init__(self, *, is_order=True, items=None, vendor_name="Acme Truck Parts",
                 invoice_number="INV-1", attachment_items=None):
        self.is_order = is_order
        self.items = items if items is not None else [
            {"part_number": "acme 100", "description": "Brake drum", "quantity": 2, "price": 55.0},
            {"part_number": "NEW-999", "description": "Mystery bracket", "quantity": 1, "price": 12.5},
        ]
        self.vendor_name = vendor_name
        self.invoice_number = invoice_number
        self.attachment_items = attachment_items
        self.classify_calls = 0
        self.text_calls = 0
        self.attachment_calls = 0

    def classify_email(self, **kwargs):
        self.classify_calls += 1
        return {"kind": "invoice" if self.is_order else "promo", "is_parts_order": self.is_order,
                "confidence": 0.93 if self.is_order else 0.1, "vendor_name": self.vendor_name,
                "order_reference": self.invoice_number, "reason": "fake"}

    attachment_kind = "invoice"

    def _extraction(self, items, kind="invoice"):
        return {"document_kind": kind, "vendor_name": self.vendor_name, "vendor_address": "1 Main St",
                "vendor_phone": "555-0100", "vendor_email": "", "vendor_website": "",
                "vendor_contact_first_name": "Ann", "vendor_contact_last_name": "Lee",
                "invoice_number": self.invoice_number, "invoice_date": "09/01/2026",
                "items": items, "total": 122.5}

    def extract_order_from_text(self, **kwargs):
        self.text_calls += 1
        return self._extraction(self.items)

    def extract_order_from_attachment(self, data, content_type):
        self.attachment_calls += 1
        if self.attachment_items is None:
            raise ValueError("unreadable")
        return self._extraction(self.attachment_items, self.attachment_kind)


@pytest.fixture()
def fake_ai(monkeypatch):
    from app.blueprints.parts.services import email_orders

    fake = FakeAI()
    monkeypatch.setattr(email_orders.ai, "classify_email", fake.classify_email)
    monkeypatch.setattr(email_orders.ai, "extract_order_from_text", fake.extract_order_from_text)
    monkeypatch.setattr(email_orders.ai, "extract_order_from_attachment", fake.extract_order_from_attachment)
    return fake


def _process(app, env, email_id, **kwargs):
    from app.blueprints.parts.services.email_orders import process_inbound_email
    from app.extensions import get_master_db

    with app.app_context():
        master = get_master_db()
        shop = master.shops.find_one({"_id": env["shop_a"]["_id"]})
        return process_inbound_email(master, shop, env["db_a"], email_id, **kwargs)


# ── webhook ──────────────────────────────────────────────────────────────

def test_webhook_requires_secret(client, inbox_env, app, monkeypatch):
    assert _post_webhook(client, _mime(), secret="wrong").status_code == 401
    assert _post_webhook(client, _mime(), secret=None).status_code == 401
    monkeypatch.setitem(app.config, "INBOUND_EMAIL_WEBHOOK_SECRET", "")
    assert _post_webhook(client, _mime()).status_code == 503
    assert inbox_env["db_a"].inbound_emails.count_documents({}) == 0


def test_webhook_unknown_token_is_dropped(client, inbox_env):
    resp = _post_webhook(client, _mime(to="orders-zzzz99998888@roobico.com"),
                         envelope_to="orders-zzzz99998888@roobico.com")
    assert resp.status_code == 200
    assert resp.get_json()["routed"] is False
    assert inbox_env["db_a"].inbound_emails.count_documents({}) == 0
    assert inbox_env["db_b"].inbound_emails.count_documents({}) == 0


def test_webhook_routes_by_token_and_isolates_tenants(client, inbox_env):
    raw = _mime(message_id="<dup-1@acmetruck.com>")
    resp = _post_webhook(client, raw)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["routed"] is True and data["duplicate"] is False

    doc = inbox_env["db_a"].inbound_emails.find_one({"message_id": "dup-1@acmetruck.com"})
    assert doc and doc["shop_id"] == inbox_env["shop_a"]["_id"] and doc["status"] == "received"
    assert doc["from_email"] == "sales@acmetruck.com"
    assert "ACME-100" in doc["text"]
    assert inbox_env["db_b"].inbound_emails.count_documents({}) == 0

    # Повтор той же доставки — не дублируется.
    resp2 = _post_webhook(client, raw)
    assert resp2.get_json()["duplicate"] is True
    assert inbox_env["db_a"].inbound_emails.count_documents({"message_id": "dup-1@acmetruck.com"}) == 1

    # Токен магазина B попадает в базу B, а не A.
    raw_b = _mime(to="orders-" + TOKEN_B + "@roobico.com", message_id="<b-1@acmetruck.com>")
    _post_webhook(client, raw_b, envelope_to="orders-" + TOKEN_B + "@roobico.com")
    assert inbox_env["db_b"].inbound_emails.count_documents({"message_id": "b-1@acmetruck.com"}) == 1
    assert inbox_env["db_a"].inbound_emails.count_documents({"message_id": "b-1@acmetruck.com"}) == 0


def test_webhook_token_from_forwarding_headers(client, inbox_env):
    """Автопересылка: envelope нет, To — исходный ящик владельца, наш адрес
    только в Delivered-To."""
    raw = _mime(to="owner@example.com", message_id="<fwd-1@acmetruck.com>",
                extra_headers={"Delivered-To": "orders-" + TOKEN_A + "@roobico.com"})
    resp = _post_webhook(client, raw, envelope_to=None)
    assert resp.get_json()["routed"] is True
    assert inbox_env["db_a"].inbound_emails.count_documents({"message_id": "fwd-1@acmetruck.com"}) == 1


def test_webhook_disabled_inbox_drops(client, inbox_env):
    inbox_env["db_a"].integrations.update_one(
        {"shop_id": inbox_env["shop_a"]["_id"], "provider": "email_orders"}, {"$set": {"enabled": False}}
    )
    resp = _post_webhook(client, _mime(message_id="<off-1@acmetruck.com>"))
    assert resp.get_json()["routed"] is False
    assert inbox_env["db_a"].inbound_emails.count_documents({"message_id": "off-1@acmetruck.com"}) == 0


# ── processing ───────────────────────────────────────────────────────────

def test_process_creates_unconfirmed_order(client, app, inbox_env, fake_ai):
    fake_ai.attachment_items = [
        {"part_number": "ACME-100", "description": "Brake drum", "quantity": 2, "price": 55.0},
        {"part_number": "NEW-999", "description": "Mystery bracket", "quantity": 1, "price": 12.5},
    ]
    _post_webhook(client, _mime(message_id="<proc-1@acmetruck.com>", attach_pdf=True))
    email = inbox_env["db_a"].inbound_emails.find_one({"message_id": "proc-1@acmetruck.com"})
    assert len(email["attachments"]) == 1 and email["attachments"][0]["data"]

    result = _process(app, inbox_env, email["_id"])
    assert result["ok"] is True and result["status"] == "order_created", result
    # PDF решает всё сам: текст письма не классифицируется и не читается.
    assert fake_ai.attachment_calls == 1 and fake_ai.text_calls == 0 and fake_ai.classify_calls == 0

    order = inbox_env["db_a"].parts_orders.find_one({"_id": ObjectId(result["order_id"])})
    assert order["source"]["extraction_source"] == "attachment:invoice.pdf"
    assert order["needs_confirmation"] is True
    assert order["status"] == "ordered"
    assert order["vendor_id"] == inbox_env["vendor_id"]          # matched by sender domain
    assert order["vendor_bill"] == "INV-1"
    assert order["source"]["kind"] == "email" and order["source"]["inbound_email_id"] == email["_id"]
    assert [i["part_id"] for i in order["items"]] == [inbox_env["part_id"]]
    assert order["items"][0]["quantity"] == 2 and order["items"][0]["price"] == 55.0
    assert order["unmatched_items"] == [
        {"part_number": "NEW-999", "description": "Mystery bracket", "quantity": 1, "price": 12.5}
    ]
    assert order["remaining_balance"] == 110.0

    # Вложение переехало на заказ, байты из письма вычищены.
    att = inbox_env["db_a"].attachments.find_one({"entity_type": "parts_order", "entity_id": order["_id"]})
    assert att and att["filename"] == "invoice.pdf"
    email = inbox_env["db_a"].inbound_emails.find_one({"_id": email["_id"]})
    assert email["status"] == "order_created" and email["parts_order_id"] == order["_id"]
    assert "data" not in email["attachments"][0]

    stats = inbox_env["db_a"].integrations.find_one({"shop_id": inbox_env["shop_a"]["_id"], "provider": "email_orders"})
    assert stats["orders_created"] == 1 and stats["emails_received"] == 1


def test_process_falls_back_to_text_and_creates_vendor(client, app, inbox_env, fake_ai):
    fake_ai.vendor_name = "Brand New Supply Co"
    fake_ai.invoice_number = "SO-77"
    _post_webhook(client, _mime(sender="Orders <orders@brandnewsupply.com>", message_id="<proc-2@x.com>"))
    email = inbox_env["db_a"].inbound_emails.find_one({"message_id": "proc-2@x.com"})

    result = _process(app, inbox_env, email["_id"])
    assert result["status"] == "order_created", result
    assert fake_ai.text_calls == 1

    order = inbox_env["db_a"].parts_orders.find_one({"_id": ObjectId(result["order_id"])})
    vendor = inbox_env["db_a"].vendors.find_one({"_id": order["vendor_id"]})
    assert vendor["name"] == "Brand New Supply Co" and vendor["created_from_email"] is True
    assert vendor["contacts"][0]["email"] == "orders@brandnewsupply.com"
    assert order["source"]["vendor_created"] is True


def test_pdf_decides_even_when_body_says_nothing(client, app, inbox_env, fake_ai):
    """Тело «see attached», по тексту ордер не распознать — но PDF-инвойс
    внутри превращается в заказ без классификации текста."""
    fake_ai.is_order = False
    fake_ai.attachment_items = [
        {"part_number": "ACME-100", "description": "Brake drum", "quantity": 3, "price": 51.0},
    ]
    _post_webhook(client, _mime(subject="FW: doc", body="See attached.", message_id="<pdf-1@acmetruck.com>",
                                attach_pdf=True))
    email = inbox_env["db_a"].inbound_emails.find_one({"message_id": "pdf-1@acmetruck.com"})
    result = _process(app, inbox_env, email["_id"])
    assert result["status"] == "order_created", result
    assert fake_ai.classify_calls == 0 and fake_ai.text_calls == 0
    email = inbox_env["db_a"].inbound_emails.find_one({"_id": email["_id"]})
    assert email["classification"]["decided_by"] == "attachment"
    assert email["attachment_summary"][0]["filename"] == "invoice.pdf"


def test_pdf_quote_is_not_an_order_even_if_body_looks_like_one(client, app, inbox_env, fake_ai):
    fake_ai.is_order = True  # текст письма выглядел бы как заказ…
    fake_ai.attachment_kind = "quote"  # …но PDF — коммерческое предложение
    fake_ai.attachment_items = [
        {"part_number": "ACME-100", "description": "Brake drum", "quantity": 3, "price": 51.0},
    ]
    _post_webhook(client, _mime(message_id="<pdf-2@acmetruck.com>", attach_pdf=True))
    email = inbox_env["db_a"].inbound_emails.find_one({"message_id": "pdf-2@acmetruck.com"})
    result = _process(app, inbox_env, email["_id"])
    assert result["status"] == "ignored"
    assert fake_ai.classify_calls == 0
    assert inbox_env["db_a"].parts_orders.count_documents({"shop_id": inbox_env["shop_a"]["_id"]}) == 0
    email = inbox_env["db_a"].inbound_emails.find_one({"_id": email["_id"]})
    assert email["classification"]["kind"] == "quote"
    # Человек настаивает → заказ создаётся из уже прочитанных строк PDF без повторного AI.
    calls = fake_ai.attachment_calls
    forced = _process(app, inbox_env, email["_id"], force=True)
    assert forced["status"] == "order_created" and fake_ai.attachment_calls == calls


def test_unreadable_pdf_falls_back_to_body(client, app, inbox_env, fake_ai):
    _post_webhook(client, _mime(message_id="<pdf-3@acmetruck.com>", attach_pdf=True))
    email = inbox_env["db_a"].inbound_emails.find_one({"message_id": "pdf-3@acmetruck.com"})
    result = _process(app, inbox_env, email["_id"])  # attachment_items=None → "unreadable"
    assert result["status"] == "order_created"
    assert fake_ai.attachment_calls == 1 and fake_ai.classify_calls == 1 and fake_ai.text_calls == 1
    email = inbox_env["db_a"].inbound_emails.find_one({"_id": email["_id"]})
    assert email["classification"]["decided_by"] == "body"
    assert "error" in email["attachment_summary"][0]


def test_process_ignores_non_orders(client, app, inbox_env, fake_ai):
    fake_ai.is_order = False
    _post_webhook(client, _mime(subject="20% off this week!", message_id="<junk-1@acmetruck.com>"))
    email = inbox_env["db_a"].inbound_emails.find_one({"message_id": "junk-1@acmetruck.com"})

    result = _process(app, inbox_env, email["_id"])
    assert result["status"] == "ignored"
    assert fake_ai.text_calls == 0
    assert inbox_env["db_a"].parts_orders.count_documents({"shop_id": inbox_env["shop_a"]["_id"]}) == 0
    email = inbox_env["db_a"].inbound_emails.find_one({"_id": email["_id"]})
    assert email["status"] == "ignored" and email["classification"]["kind"] == "promo"

    # Повторная обработка без force — письмо уже разобрано.
    assert _process(app, inbox_env, email["_id"])["ok"] is False
    # Человек настаивает: force создаёт заказ, классификация не вызывается.
    calls_before = fake_ai.classify_calls
    forced = _process(app, inbox_env, email["_id"], force=True)
    assert forced["status"] == "order_created"
    assert fake_ai.classify_calls == calls_before


def test_process_links_to_existing_order_by_vendor_bill(client, app, inbox_env, fake_ai):
    existing_id = inbox_env["db_a"].parts_orders.insert_one({
        "shop_id": inbox_env["shop_a"]["_id"], "vendor_id": inbox_env["vendor_id"], "order_number": 5001,
        "vendor_bill": "inv-1", "items": [], "non_inventory_amounts": [], "status": "ordered",
        "is_active": True, "created_at": _now(), "order_date": _now(),
    }).inserted_id
    _post_webhook(client, _mime(message_id="<link-1@acmetruck.com>", attach_pdf=True))
    email = inbox_env["db_a"].inbound_emails.find_one({"message_id": "link-1@acmetruck.com"})

    result = _process(app, inbox_env, email["_id"])
    assert result["status"] == "linked" and result["order_id"] == str(existing_id)
    assert inbox_env["db_a"].parts_orders.count_documents({"shop_id": inbox_env["shop_a"]["_id"]}) == 1
    linked = inbox_env["db_a"].parts_orders.find_one({"_id": existing_id})
    assert linked["source_emails"][0]["inbound_email_id"] == email["_id"]
    assert "needs_confirmation" not in linked
    assert inbox_env["db_a"].attachments.count_documents({"entity_id": existing_id}) == 1


def test_process_suggest_mode_waits_for_human(client, app, inbox_env, fake_ai):
    inbox_env["db_a"].integrations.update_one(
        {"shop_id": inbox_env["shop_a"]["_id"], "provider": "email_orders"}, {"$set": {"mode": "suggest"}}
    )
    _post_webhook(client, _mime(message_id="<sug-1@acmetruck.com>"))
    email = inbox_env["db_a"].inbound_emails.find_one({"message_id": "sug-1@acmetruck.com"})
    result = _process(app, inbox_env, email["_id"])
    assert result["status"] == "suggested"
    assert inbox_env["db_a"].parts_orders.count_documents({"shop_id": inbox_env["shop_a"]["_id"]}) == 0

    # Через UI: "Create order" → force. Извлечение повторно не вызывается.
    login(client)
    token = get_csrf_token(client)
    resp = client.post(f"/settings/integrations/email_orders/inbox/{email['_id']}/process",
                       json={"force": True}, headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["status"] == "order_created" and data["email"]["order_number"]
    assert fake_ai.text_calls == 1
    order = inbox_env["db_a"].parts_orders.find_one({"_id": ObjectId(data["order_id"])})
    assert order["needs_confirmation"] is True


def test_process_marks_error_when_ai_fails(client, app, inbox_env, fake_ai, monkeypatch):
    from app.blueprints.parts.services import email_orders

    def boom(**kwargs):
        raise RuntimeError("openai down")

    monkeypatch.setattr(email_orders.ai, "classify_email", boom)
    _post_webhook(client, _mime(message_id="<err-1@acmetruck.com>"))
    email = inbox_env["db_a"].inbound_emails.find_one({"message_id": "err-1@acmetruck.com"})
    result = _process(app, inbox_env, email["_id"])
    assert result["ok"] is False and result["status"] == "error"
    email = inbox_env["db_a"].inbound_emails.find_one({"_id": email["_id"]})
    assert email["status"] == "error" and "openai down" in email["error"] and email["attempts"] == 1


def test_cron_runner_processes_pending(client, app, inbox_env, fake_ai):
    from app.blueprints.parts.services.email_orders import run_inbound_processing
    from app.extensions import get_master_db

    _post_webhook(client, _mime(message_id="<cron-1@acmetruck.com>"))
    _post_webhook(client, _mime(message_id="<cron-2@acmetruck.com>", subject="promo"))
    with app.app_context():
        summary = run_inbound_processing(get_master_db(), shop_id=inbox_env["shop_a"]["_id"])
    assert summary["processed"] == 2 and summary["orders"] >= 1
    assert inbox_env["db_a"].inbound_emails.count_documents({"status": "received"}) == 0


# ── confirm / reject / guards ────────────────────────────────────────────

def _make_email_order(client, app, inbox_env, message_id="<c-1@acmetruck.com>"):
    _post_webhook(client, _mime(message_id=message_id))
    email = inbox_env["db_a"].inbound_emails.find_one({"message_id": message_id.strip("<>")})
    result = _process(app, inbox_env, email["_id"])
    assert result["status"] == "order_created", result
    return ObjectId(result["order_id"]), email["_id"]


def test_unconfirmed_order_blocks_receive_and_pay(client, app, inbox_env, fake_ai):
    order_id, _ = _make_email_order(client, app, inbox_env)
    login(client)
    token = get_csrf_token(client)
    resp = client.post(f"/parts/api/orders/{order_id}/receive", json={}, headers={"X-CSRFToken": token})
    assert resp.status_code == 400 and "not confirmed" in resp.get_json()["error"]
    resp = client.post(f"/parts/api/orders/{order_id}/payment", json={"amount": 10},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 400 and "not confirmed" in resp.get_json()["error"]
    resp = client.get(f"/parts/api/orders/{order_id}/return-context")
    assert resp.status_code == 400

    # Деталка отдаёт контекст письма и несматченные строки.
    resp = client.get(f"/parts/api/orders/{order_id}")
    data = resp.get_json()["order"]
    assert data["needs_confirmation"] is True
    assert data["source"]["from_email"] == "sales@acmetruck.com"
    assert data["unmatched_items"][0]["part_number"] == "NEW-999"


def test_confirm_requires_resolving_unmatched_then_clears_flag(client, app, inbox_env, fake_ai):
    order_id, email_id = _make_email_order(client, app, inbox_env, "<c-2@acmetruck.com>")
    login(client)
    token = get_csrf_token(client)

    resp = client.post(f"/parts/api/orders/{order_id}/confirm", json={}, headers={"X-CSRFToken": token})
    assert resp.status_code == 400 and "not matched" in resp.get_json()["error"]

    resp = client.post(f"/parts/api/orders/{order_id}/confirm", json={"drop_unmatched": True},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    order = inbox_env["db_a"].parts_orders.find_one({"_id": order_id})
    assert order["needs_confirmation"] is False and "unmatched_items" not in order
    assert order["dropped_unmatched_items"][0]["part_number"] == "NEW-999"
    assert order["confirmed_by"] is not None

    # Отправитель запомнен у вендора → следующий раз матч по адресу.
    vendor = inbox_env["db_a"].vendors.find_one({"_id": inbox_env["vendor_id"]})
    assert "sales@acmetruck.com" in vendor["email_senders"]
    assert "acmetruck.com" in vendor["email_domains"]

    # Теперь receive работает.
    resp = client.post(f"/parts/api/orders/{order_id}/receive", json={}, headers={"X-CSRFToken": token})
    assert resp.status_code == 200 and resp.get_json()["ok"] is True
    # Повторный confirm — idempotent.
    resp = client.post(f"/parts/api/orders/{order_id}/confirm", json={}, headers={"X-CSRFToken": token})
    assert resp.status_code == 200


def test_reject_soft_deletes_and_deactivates_auto_vendor(client, app, inbox_env, fake_ai):
    fake_ai.vendor_name = "One Off Vendor"
    _post_webhook(client, _mime(sender="x@oneoffvendor.com", message_id="<r-1@x.com>"))
    email = inbox_env["db_a"].inbound_emails.find_one({"message_id": "r-1@x.com"})
    result = _process(app, inbox_env, email["_id"])
    order_id = ObjectId(result["order_id"])
    vendor_id = inbox_env["db_a"].parts_orders.find_one({"_id": order_id})["vendor_id"]

    login(client)
    token = get_csrf_token(client)
    resp = client.post(f"/parts/api/orders/{order_id}/reject", headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    order = inbox_env["db_a"].parts_orders.find_one({"_id": order_id})
    assert order["is_active"] is False and order["rejected_by"] is not None
    assert inbox_env["db_a"].inbound_emails.find_one({"_id": email["_id"]})["status"] == "rejected"
    assert inbox_env["db_a"].vendors.find_one({"_id": vendor_id})["is_active"] is False

    # Подтверждённый (обычный) заказ отклонить нельзя.
    manual_id = inbox_env["db_a"].parts_orders.insert_one({
        "shop_id": inbox_env["shop_a"]["_id"], "vendor_id": inbox_env["vendor_id"], "order_number": 5100,
        "items": [], "non_inventory_amounts": [], "status": "ordered", "is_active": True,
        "created_at": _now(), "order_date": _now(),
    }).inserted_id
    resp = client.post(f"/parts/api/orders/{manual_id}/reject", headers={"X-CSRFToken": token})
    assert resp.status_code == 400


def test_orders_tab_shows_not_confirmed_filter_and_banner(client, app, inbox_env, fake_ai):
    _make_email_order(client, app, inbox_env, "<list-1@acmetruck.com>")
    login(client)
    html = client.get("/parts/?tab=orders&date_preset=all_time").get_data(as_text=True)
    assert "waiting for your confirmation" in html
    assert "confirmOrderBtn" in html and "Not confirmed" in html
    html = client.get("/parts/?tab=orders&paid_status=not_confirmed&date_preset=all_time").get_data(as_text=True)
    assert "rejectOrderBtn" in html and "waiting for your confirmation" not in html


def test_vendor_balances_exclude_unconfirmed(client, app, inbox_env, fake_ai):
    _make_email_order(client, app, inbox_env, "<bal-1@acmetruck.com>")
    login(client)
    resp = client.get(f"/vendors/api/balances?ids={inbox_env['vendor_id']}")
    assert resp.status_code == 200
    assert resp.get_json()["balances"].get(str(inbox_env["vendor_id"]), 0) == 0


# ── settings UI ──────────────────────────────────────────────────────────

def test_settings_enable_issues_address_and_lists_inbox(client, app, inbox_env, fake_ai):
    from app.extensions import get_master_db

    with app.app_context():
        get_master_db().shops.update_one({"_id": inbox_env["shop_a"]["_id"]}, {"$unset": {"inbound_email_token": ""}})

    login(client)
    token = get_csrf_token(client)
    resp = client.post("/settings/integrations/email_orders/settings",
                       json={"enabled": True, "mode": "suggest"}, headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    state = resp.get_json()["state"]
    assert state["enabled"] is True and state["mode"] == "suggest"
    assert state["address"].startswith("orders-") and state["address"].endswith("@roobico.com")

    # Тот же адрес при повторном сохранении; ротация меняет его.
    resp = client.post("/settings/integrations/email_orders/settings", json={"enabled": True},
                       headers={"X-CSRFToken": token})
    assert resp.get_json()["state"]["address"] == state["address"]
    resp = client.post("/settings/integrations/email_orders/rotate", headers={"X-CSRFToken": token})
    new_address = resp.get_json()["state"]["address"]
    assert new_address != state["address"]

    # Письмо на новый адрес принимается, на старый — нет.
    new_token = new_address.split("@")[0].replace("orders-", "")
    _post_webhook(client, _mime(to=new_address, message_id="<set-1@acmetruck.com>"), envelope_to=new_address)
    old = state["address"]
    _post_webhook(client, _mime(to=old, message_id="<set-2@acmetruck.com>"), envelope_to=old)
    assert inbox_env["db_a"].inbound_emails.count_documents({"message_id": "set-1@acmetruck.com"}) == 1
    assert inbox_env["db_a"].inbound_emails.count_documents({"message_id": "set-2@acmetruck.com"}) == 0
    assert len(new_token) == 12

    resp = client.get("/settings/integrations/email_orders/inbox")
    data = resp.get_json()
    assert data["ok"] is True and data["counts"]["total"] == 1
    assert data["emails"][0]["subject"] == "Order confirmation #INV-1"
    assert data["emails"][0]["status"] == "received"

    email_id = data["emails"][0]["id"]
    resp = client.get(f"/settings/integrations/email_orders/inbox/{email_id}")
    assert "ACME-100" in resp.get_json()["email"]["text"]

    resp = client.post(f"/settings/integrations/email_orders/inbox/{email_id}/ignore", headers={"X-CSRFToken": token})
    assert resp.status_code == 200 and resp.get_json()["email"]["status"] == "ignored"

    html = client.get("/settings/integrations").get_data(as_text=True)
    assert "Email orders inbox" in html and new_address in html


def test_settings_inbox_is_tenant_scoped(client, app, inbox_env):
    """Письмо магазина B не видно из сессии тенанта A."""
    _post_webhook(client, _mime(to="orders-" + TOKEN_B + "@roobico.com", message_id="<iso-1@acmetruck.com>"),
                  envelope_to="orders-" + TOKEN_B + "@roobico.com")
    email_b = inbox_env["db_b"].inbound_emails.find_one({"message_id": "iso-1@acmetruck.com"})
    login(client)
    resp = client.get("/settings/integrations/email_orders/inbox")
    ids = [e["id"] for e in resp.get_json()["emails"]]
    assert str(email_b["_id"]) not in ids
    assert client.get(f"/settings/integrations/email_orders/inbox/{email_b['_id']}").status_code == 404
