"""
Коры: ручная правка количества, кредиты вендоров (возвраты запчастей и
коров) на табе Payments, баланс вендора не уходит в минус от кредитов.
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
def core_env(seed, mongo):
    db = mongo[SHOP_A_DB]
    shop_id = seed["shop_a"]["_id"]
    now = _now()
    core = {
        "_id": ObjectId(), "shop_id": shop_id, "part_id": ObjectId(),
        "part_number": "CORE-PN-9", "description": "Core test",
        "quantity": 5, "core_cost": 45.0, "is_active": True,
        "created_at": now, "updated_at": now,
    }
    db.cores.insert_one(core)
    yield {"db": db, "shop_id": shop_id, "core": core}
    db.cores.delete_one({"_id": core["_id"]})
    db.core_returns.delete_many({"shop_id": shop_id, "part_number": "CORE-PN-9"})
    db.parts_orders.delete_many({"shop_id": shop_id, "vendor_bill": "CREDIT-PAY-1"})
    db.vendors.delete_many({"shop_id": shop_id, "name": "Core Credit Vendor"})


def test_core_quantity_manual_edit(client, core_env):
    login(client)
    token = get_csrf_token(client)
    core_id = str(core_env["core"]["_id"])

    resp = client.post(f"/parts/api/cores/{core_id}/quantity",
                       json={"quantity": 3}, headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["quantity"] == 3
    assert core_env["db"].cores.find_one({"_id": core_env["core"]["_id"]})["quantity"] == 3

    # Ноль можно (кор списали) — отрицательное и мусор нельзя
    assert client.post(f"/parts/api/cores/{core_id}/quantity",
                       json={"quantity": 0}, headers={"X-CSRFToken": token}).status_code == 200
    assert client.post(f"/parts/api/cores/{core_id}/quantity",
                       json={"quantity": -1}, headers={"X-CSRFToken": token}).status_code == 400
    assert client.post(f"/parts/api/cores/{core_id}/quantity",
                       json={"quantity": "x"}, headers={"X-CSRFToken": token}).status_code == 400
    assert client.post(f"/parts/api/cores/{str(ObjectId())}/quantity",
                       json={"quantity": 1}, headers={"X-CSRFToken": token}).status_code == 404


def test_payments_tab_shows_vendor_credits(client, core_env):
    login(client)
    db, shop_id = core_env["db"], core_env["shop_id"]
    now = _now()
    vendor_id = db.vendors.insert_one({
        "shop_id": shop_id, "name": "Core Credit Vendor", "is_active": True,
        "created_at": now,
    }).inserted_id

    db.parts_orders.insert_one({
        "shop_id": shop_id, "order_number": 98101, "is_return": True,
        "return_for_order_number": 98100, "vendor_id": vendor_id,
        "vendor_bill": "CREDIT-PAY-1", "status": "returned",
        "payment_status": "credit", "items": [], "credit_total": 8.0,
        "non_inventory_amounts": [], "order_date": now,
        "is_active": True, "created_at": now,
    })
    db.core_returns.insert_one({
        "shop_id": shop_id, "core_id": core_env["core"]["_id"],
        "part_id": core_env["core"]["part_id"], "part_number": "CORE-PN-9",
        "description": "Core test", "quantity": 2, "core_cost": 45.0,
        "credit_total": 90.0, "vendor_id": vendor_id,
        "vendor_name": "Core Credit Vendor", "notes": "RMA 555",
        "returned_at": now, "is_active": True, "created_at": now,
    })

    page = client.get("/parts/?tab=payments&date_preset=all_time").get_data(as_text=True)
    assert "Vendor credits" in page
    assert "Parts return" in page
    assert "Core return" in page
    assert "+$8.00" in page
    assert "+$90.00" in page
    assert "R-98101 (order #98100)" in page
    assert "CORE-PN-9 × 2" in page
    # Тотал кредитов
    assert "$98.00" in page


def test_vendor_balance_not_negative_from_credits(client, core_env):
    """Возвраты — кредит вендора, но его баланс от них в минус не уходит."""
    login(client)
    db, shop_id = core_env["db"], core_env["shop_id"]
    now = _now()
    vendor_id = db.vendors.insert_one({
        "shop_id": shop_id, "name": "Core Credit Vendor", "is_active": True,
        "created_at": now,
    }).inserted_id
    db.parts_orders.insert_one({
        "shop_id": shop_id, "order_number": 98102, "is_return": True,
        "vendor_id": vendor_id, "vendor_bill": "CREDIT-PAY-1",
        "status": "returned", "payment_status": "credit", "items": [],
        "credit_total": 500.0, "non_inventory_amounts": [],
        "order_date": now, "is_active": True, "created_at": now,
    })

    resp = client.get(f"/vendors/api/balances?ids={vendor_id}")
    assert resp.status_code == 200
    balances = resp.get_json()["balances"]
    assert balances.get(str(vendor_id), 0) >= 0
