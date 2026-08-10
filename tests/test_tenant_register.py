"""Анти-бот защита публичной регистрации /tenant/register."""
from __future__ import annotations

import time

import pytest

from tests.conftest import get_csrf_token


def _register_form(client, **overrides):
    """Валидная человеческая заявка; overrides позволяют «испортить» отдельные поля."""
    form = {
        "csrf_token": get_csrf_token(client),
        "company_name": "Acme Garage Test",
        "company_address": "123 Main St, Austin, TX 78701",
        "company_phone": "+1 555 123 4567",
        "first_name": "John",
        "last_name": "Smith",
        "email": "owner@acme-garage-test.local",
        "password": "password123",
        "password_confirm": "password123",
        "website": "",  # honeypot — человек его не заполняет
        "form_ts": str(int(time.time()) - 30),
    }
    form.update(overrides)
    return form


def _post_register(client, form, remote_addr="127.0.0.1"):
    return client.post("/tenant/register", data=form, environ_base={"REMOTE_ADDR": remote_addr})


@pytest.fixture()
def _cleanup_registered(app):
    """Успешная регистрация создаёт реальные базы — дропаем их после теста."""
    yield
    from app.extensions import get_master_db, get_mongo_client
    with app.app_context():
        master = get_master_db()
        client = get_mongo_client()
        tenant = master.tenants.find_one({"slug": "acme-garage-test"})
        if tenant:
            master.users.delete_many({"tenant_id": tenant["_id"]})
            for shop in master.shops.find({"tenant_id": tenant["_id"]}):
                if shop.get("db_name"):
                    client.drop_database(shop["db_name"])
            master.shops.delete_many({"tenant_id": tenant["_id"]})
            if tenant.get("db_name"):
                client.drop_database(tenant["db_name"])
            master.tenants.delete_one({"_id": tenant["_id"]})


def test_register_ok_for_human_like_submit(client, _cleanup_registered):
    resp = _post_register(client, _register_form(client))
    assert resp.status_code == 201
    assert resp.get_json()["ok"] is True


def test_register_rejects_filled_honeypot(client):
    resp = _post_register(client, _register_form(client, website="http://spam.example"))
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_register_rejects_missing_form_ts(client):
    form = _register_form(client)
    form.pop("form_ts")
    resp = _post_register(client, form)
    assert resp.status_code == 400


def test_register_rejects_instant_submit(client):
    resp = _post_register(client, _register_form(client, form_ts=str(int(time.time()))))
    assert resp.status_code == 400


@pytest.mark.parametrize("field,value", [
    ("company_name", '<a href="http://spam.example">$3,222 payment</a>'),
    ("company_name", "Visit https://spam.example now"),
    ("first_name", "www.spam.example"),
    ("company_address", "click http://spam.example"),
])
def test_register_rejects_spam_content(client, field, value):
    resp = _post_register(client, _register_form(client, **{field: value}))
    assert resp.status_code == 400


def test_register_rate_limited_per_ip(client):
    # Лимит 3/час с IP: даже невалидные заявки съедают попытки.
    form = _register_form(client, website="bot")  # отклоняется, но попытка считается
    for _ in range(3):
        assert _post_register(client, form, remote_addr="10.9.9.9").status_code == 400
    resp = _post_register(client, form, remote_addr="10.9.9.9")
    assert resp.status_code == 429
    # Другой IP не задет.
    assert _post_register(client, form, remote_addr="10.9.9.10").status_code == 400
