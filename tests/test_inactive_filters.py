"""
Общий фильтр статуса Active / Inactive / All на списках: клиенты, юниты
(вкладка Units клиента), вендоры, пользователи; кнопка Activate у
неактивного пользователя. Парты покрыты в test_parts_inactive.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, get_csrf_token, login


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def env(app, seed, mongo):
    from app.extensions import get_master_db

    db = mongo[SHOP_A_DB]
    shop_id = seed["shop_a"]["_id"]
    tenant_id = seed["tenant_a"]["_id"]
    now = _now()

    cust_active = db.customers.insert_one({
        "shop_id": shop_id, "tenant_id": tenant_id, "company_name": "INACTF Active Co",
        "is_active": True, "created_at": now, "contacts": [],
    }).inserted_id
    cust_inactive = db.customers.insert_one({
        "shop_id": shop_id, "tenant_id": tenant_id, "company_name": "INACTF Gone Co",
        "is_active": False, "created_at": now, "contacts": [],
    }).inserted_id
    unit_active = db.units.insert_one({
        "shop_id": shop_id, "tenant_id": tenant_id, "customer_id": cust_active,
        "unit_number": "INACTU-ON", "vin": "1INACTUON000000001", "make": "Volvo", "model": "VNL",
        "year": 2020, "is_active": True, "created_at": now,
    }).inserted_id
    unit_inactive = db.units.insert_one({
        "shop_id": shop_id, "tenant_id": tenant_id, "customer_id": cust_active,
        "unit_number": "INACTU-OFF", "vin": "1INACTUOFF00000002", "make": "Volvo", "model": "VNL",
        "year": 2019, "is_active": False, "created_at": now,
    }).inserted_id
    vendor_active = db.vendors.insert_one({
        "shop_id": shop_id, "name": "INACTV Active Vendor", "is_active": True, "created_at": now,
    }).inserted_id
    vendor_inactive = db.vendors.insert_one({
        "shop_id": shop_id, "name": "INACTV Gone Vendor", "is_active": False, "created_at": now,
    }).inserted_id

    with app.app_context():
        master = get_master_db()
        user_inactive = master.users.insert_one({
            "email": "inactf-gone@test.local", "name": "Inactf Gone", "first_name": "Inactf",
            "last_name": "Gone", "role": "mechanic", "is_active": False, "tenant_id": tenant_id,
            "shop_ids": [str(shop_id)], "created_at": now,
        }).inserted_id

    yield {
        "db": db, "shop_id": shop_id, "cust_active": cust_active, "cust_inactive": cust_inactive,
        "unit_active": unit_active, "unit_inactive": unit_inactive,
        "vendor_active": vendor_active, "vendor_inactive": vendor_inactive,
        "user_inactive": user_inactive,
    }

    db.customers.delete_many({"_id": {"$in": [cust_active, cust_inactive]}})
    db.units.delete_many({"_id": {"$in": [unit_active, unit_inactive]}})
    db.vendors.delete_many({"_id": {"$in": [vendor_active, vendor_inactive]}})
    with app.app_context():
        get_master_db().users.delete_one({"_id": user_inactive})


def _html(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, f"{url} -> {resp.status_code}"
    return resp.get_data(as_text=True)


def test_customers_status_filter(client, env):
    login(client)
    html = _html(client, "/customers?q=INACTF")
    assert "INACTF Active Co" in html and "INACTF Gone Co" not in html
    assert 'name="status"' in html

    html = _html(client, "/customers?q=INACTF&status=inactive")
    assert "INACTF Gone Co" in html and "INACTF Active Co" not in html

    html = _html(client, "/customers?q=INACTF&status=all")
    assert "INACTF Gone Co" in html and "INACTF Active Co" in html


def test_units_tab_status_filter(client, env):
    login(client)
    base = f"/customers/{env['cust_active']}?tab=units&q=INACTU"
    html = _html(client, base)
    assert "INACTU-ON" in html and "INACTU-OFF" not in html

    html = _html(client, base + "&status=inactive")
    assert "INACTU-OFF" in html and "INACTU-ON" not in html
    assert ">Inactive<" in html

    html = _html(client, base + "&status=all")
    assert "INACTU-OFF" in html and "INACTU-ON" in html


def test_vendors_status_filter(client, env):
    login(client)
    html = _html(client, "/vendors/?q=INACTV")
    assert "INACTV Active Vendor" in html and "INACTV Gone Vendor" not in html

    html = _html(client, "/vendors/?q=INACTV&status=inactive")
    assert "INACTV Gone Vendor" in html and "INACTV Active Vendor" not in html
    assert "/restore" in html  # у вендоров восстановление прямо из списка

    html = _html(client, "/vendors/?q=INACTV&status=all")
    assert "INACTV Gone Vendor" in html and "INACTV Active Vendor" in html


def test_users_status_filter_and_activate(app, client, env):
    from app.extensions import get_master_db

    login(client)
    html = _html(client, "/settings/users?q=inactf")
    assert "inactf-gone@test.local" not in html

    html = _html(client, "/settings/users?q=inactf&status=inactive")
    assert "inactf-gone@test.local" in html
    assert f"/settings/users/{env['user_inactive']}/activate" in html

    token = get_csrf_token(client)
    resp = client.post(f"/settings/users/{env['user_inactive']}/activate",
                       data={"csrf_token": token, "q": "inactf", "status": "inactive"})
    assert resp.status_code in (302, 303)
    with app.app_context():
        user = get_master_db().users.find_one({"_id": env["user_inactive"]})
    assert user["is_active"] is True

    html = _html(client, "/settings/users?q=inactf")
    assert "inactf-gone@test.local" in html
