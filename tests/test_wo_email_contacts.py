"""
Email-модалка WO: правка/удаление контактов кастомера
(/work_orders/api/customers/<id>/email-contacts/*) и право
work_orders.manage_email_contacts.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import SHOP_A_DB, TEST_MASTER, TEST_MONGO_URI, get_csrf_token, login

DENIED_EMAIL = "no-contact-edit@test.local"
DENIED_PASSWORD = "password123"


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def contact_customer(seed, mongo):
    """Кастомер с двумя контактами; пересоздаётся для каждого теста."""
    shop_db = mongo[SHOP_A_DB]
    customer = {
        "_id": ObjectId(),
        "shop_id": seed["shop_a"]["_id"],
        "tenant_id": seed["tenant_a"]["_id"],
        "company_name": "Contact Fleet LLC",
        "contacts": [
            {"first_name": "Main", "last_name": "Person", "phone": "111",
             "email": "main@fleet.test", "is_main": True},
            {"first_name": "Second", "last_name": "Person", "phone": "222",
             "email": "second@fleet.test", "is_main": False},
        ],
        "first_name": "Main", "last_name": "Person",
        "phone": "111", "email": "main@fleet.test",
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }
    shop_db.customers.insert_one(customer)
    yield customer
    shop_db.customers.delete_one({"_id": customer["_id"]})


@pytest.fixture(scope="module")
def denied_user(seed, mongo):
    """Пользователь, которому право выключено через deny_permissions."""
    user = {
        "_id": ObjectId(),
        "email": DENIED_EMAIL,
        "password_hash": generate_password_hash(DENIED_PASSWORD),
        "is_active": True,
        "tenant_id": seed["tenant_a"]["_id"],
        "shop_ids": [str(seed["shop_a"]["_id"])],
        "role": "owner",
        "deny_permissions": ["work_orders.manage_email_contacts"],
        "created_at": datetime.now(timezone.utc),
    }
    mongo[TEST_MASTER].users.insert_one(user)
    return user


def _post_json(client, url, payload):
    token = get_csrf_token(client)
    return client.post(url, json=payload, headers={"X-CSRFToken": token})


def _url(customer, action):
    return f"/work_orders/api/customers/{customer['_id']}/email-contacts/{action}"


def test_update_contact_fields_and_search_terms(client, contact_customer, mongo):
    from app.utils.entity_search import build_customer_search_terms

    login(client)
    resp = _post_json(client, _url(contact_customer, "update"), {
        "index": 1,
        "original_email": "second@fleet.test",
        "first_name": "Renamed",
        "last_name": "Contact",
        "phone": "333",
        "email": "Renamed@Fleet.TEST",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["ok"] is True
    assert data["contacts"][1]["email"] == "renamed@fleet.test"

    doc = mongo[SHOP_A_DB].customers.find_one({"_id": contact_customer["_id"]})
    c = doc["contacts"][1]
    assert (c["first_name"], c["last_name"], c["phone"]) == ("Renamed", "Contact", "333")
    assert c["email"] == "renamed@fleet.test"  # email нормализуется в lower
    assert c["is_main"] is False
    # Легаси-поля главного контакта не тронуты (правили не main).
    assert doc["email"] == "main@fleet.test"
    # Поисковый индекс пересобран по новому составу контактов.
    assert doc["search_terms"] == build_customer_search_terms(doc)


def test_update_main_contact_syncs_legacy_fields(client, contact_customer, mongo):
    login(client)
    resp = _post_json(client, _url(contact_customer, "update"), {
        "index": 0,
        "original_email": "main@fleet.test",
        "first_name": "Boss",
        "last_name": "Person",
        "phone": "999",
        "email": "boss@fleet.test",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)

    doc = mongo[SHOP_A_DB].customers.find_one({"_id": contact_customer["_id"]})
    assert doc["contacts"][0]["is_main"] is True
    assert doc["email"] == "boss@fleet.test"
    assert doc["first_name"] == "Boss"
    assert doc["phone"] == "999"


def test_update_rejects_duplicate_email(client, contact_customer):
    login(client)
    resp = _post_json(client, _url(contact_customer, "update"), {
        "index": 1,
        "original_email": "second@fleet.test",
        "email": "main@fleet.test",
    })
    assert resp.status_code == 400
    assert "already" in resp.get_json()["error"]


def test_delete_main_contact_promotes_next(client, contact_customer, mongo):
    login(client)
    resp = _post_json(client, _url(contact_customer, "delete"), {
        "index": 0,
        "original_email": "main@fleet.test",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert len(data["contacts"]) == 1

    doc = mongo[SHOP_A_DB].customers.find_one({"_id": contact_customer["_id"]})
    assert len(doc["contacts"]) == 1
    assert doc["contacts"][0]["email"] == "second@fleet.test"
    assert doc["contacts"][0]["is_main"] is True
    # Легаси-поля переключились на нового главного.
    assert doc["email"] == "second@fleet.test"
    assert doc["first_name"] == "Second"


def test_stale_index_falls_back_to_email_match(client, contact_customer, mongo):
    """Если список в модалке устарел (index не совпал), контакт ищется по email."""
    login(client)
    resp = _post_json(client, _url(contact_customer, "delete"), {
        "index": 0,  # неверный индекс: там main@, а original_email — second@
        "original_email": "second@fleet.test",
    })
    assert resp.status_code == 200
    doc = mongo[SHOP_A_DB].customers.find_one({"_id": contact_customer["_id"]})
    emails = [c["email"] for c in doc["contacts"]]
    assert emails == ["main@fleet.test"]


def test_unknown_contact_404(client, contact_customer):
    login(client)
    resp = _post_json(client, _url(contact_customer, "delete"), {
        "original_email": "ghost@fleet.test",
    })
    assert resp.status_code == 404


def test_permission_can_be_revoked(client, contact_customer, denied_user):
    login(client, email=DENIED_EMAIL, password=DENIED_PASSWORD)
    for action in ("update", "delete"):
        resp = _post_json(client, _url(contact_customer, action), {
            "index": 1,
            "original_email": "second@fleet.test",
            "email": "x@fleet.test",
        })
        assert resp.status_code == 403, action
        assert resp.get_json()["required"] == "work_orders.manage_email_contacts"


def test_default_roles_have_permission():
    """Право включено по умолчанию у всех системных ролей — отключаемо, но 'все дают'."""
    from app.constants.permissions import build_default_roles

    for role in build_default_roles():
        assert "work_orders.manage_email_contacts" in role["permissions"], role["key"]
