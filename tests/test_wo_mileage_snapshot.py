"""
Пробег на WO — снимок на момент работы: страница старого WO показывает его,
а не текущий одометр юнита; Save без изменения пробега юнит не откатывает,
одометр юнита двигается только вперёд.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, get_csrf_token, login


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


def _post_json(client, url, payload):
    token = get_csrf_token(client)
    return client.post(url, json=payload, headers={"X-CSRFToken": token})


def _update_payload(mileage):
    return {
        "labors": [{"labor_description": "Brakes", "labor_hours": 1, "labor_rate_code": "standard",
                    "labor_full_total": 100, "assigned_mechanics": [], "issue_description": "", "parts": []}],
        "totals": {},
        "save_status": "open",
        "unit_mileage": mileage,
    }


def test_old_work_order_keeps_its_own_mileage(client, seed, mongo):
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = datetime.now(timezone.utc)
    customer = {"_id": ObjectId(), "shop_id": shop_a["_id"], "company_name": "Odometer Fleet", "is_active": True, "created_at": now}
    unit = {"_id": ObjectId(), "shop_id": shop_a["_id"], "customer_id": customer["_id"], "unit_number": "ODO-1",
            "make": "Volvo", "model": "VNL", "year": 2020, "mileage": 120000, "is_active": True, "created_at": now}
    wo = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "wo_number": 97101, "customer_id": customer["_id"], "unit_id": unit["_id"],
        "status": "open", "is_active": True, "mileage": 100000,
        "labors": [{"labor_id": str(ObjectId()), "labor": {"description": "Brakes", "hours": "1", "rate_code": "standard", "assigned_mechanics": []}, "parts": []}],
        "totals": {"labor_total": 100.0, "parts_total": 0, "sales_tax_total": 0, "grand_total": 100.0},
        "work_order_date": now, "created_at": now, "updated_at": now,
    }
    shop_db.customers.insert_one(customer)
    shop_db.units.insert_one(unit)
    shop_db.work_orders.insert_one(wo)
    try:
        # Страница старого WO отдаёт его собственный пробег, а не одометр юнита.
        html = client.get(f"/work_orders/details?work_order_id={wo['_id']}").get_data(as_text=True)
        assert 'data-wo-mileage="100000"' in html

        def _unit_mileage():
            return shop_db.units.find_one({"_id": unit["_id"]})["mileage"]

        def _wo_mileage():
            return shop_db.work_orders.find_one({"_id": wo["_id"]}).get("mileage")

        # Save без изменения пробега — юнит не откатывается на 100000.
        resp = _post_json(client, f"/work_orders/api/work_orders/{wo['_id']}/update", _update_payload("100000"))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert _unit_mileage() == 120000 and _wo_mileage() == 100000

        # Поправили пробег ниже текущего одометра — снимок на WO меняется, юнит нет.
        resp = _post_json(client, f"/work_orders/api/work_orders/{wo['_id']}/update", _update_payload("110000"))
        assert resp.status_code == 200
        assert _wo_mileage() == 110000 and _unit_mileage() == 120000

        # Пробег выше одометра — двигает и юнит.
        resp = _post_json(client, f"/work_orders/api/work_orders/{wo['_id']}/update", _update_payload("125000"))
        assert resp.status_code == 200
        assert _wo_mileage() == 125000 and _unit_mileage() == 125000

        # Старый WO без снимка: страница отдаёт пустое значение (фронт возьмёт юнит).
        shop_db.work_orders.update_one({"_id": wo["_id"]}, {"$unset": {"mileage": ""}})
        html = client.get(f"/work_orders/details?work_order_id={wo['_id']}").get_data(as_text=True)
        assert 'data-wo-mileage=""' in html
    finally:
        shop_db.work_orders.delete_one({"_id": wo["_id"]})
        shop_db.units.delete_one({"_id": unit["_id"]})
        shop_db.customers.delete_one({"_id": customer["_id"]})


def test_office_created_work_order_stores_mileage_snapshot(client, seed, mongo):
    """WO из офиса тоже хранит снимок пробега (int) и двигает одометр юнита только вперёд."""
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = datetime.now(timezone.utc)
    customer = {"_id": ObjectId(), "shop_id": shop_a["_id"], "company_name": "Office Odometer", "is_active": True, "created_at": now}
    unit = {"_id": ObjectId(), "shop_id": shop_a["_id"], "customer_id": customer["_id"], "unit_number": "ODO-2",
            "mileage": 120000, "is_active": True, "created_at": now}
    shop_db.customers.insert_one(customer)
    shop_db.units.insert_one(unit)
    try:
        def _create(mileage):
            token = get_csrf_token(client)
            resp = client.post("/work_orders/create", data={
                "csrf_token": token, "customer_id": str(customer["_id"]), "unit_id": str(unit["_id"]),
                "unit_mileage": mileage,
                "labors[0][labor_description]": "Office job", "labors[0][labor_full_total]": "100",
            })
            assert resp.status_code in (200, 302), resp.get_data(as_text=True)
            return shop_db.work_orders.find_one({"customer_id": customer["_id"]}, sort=[("created_at", -1)])

        wo = _create("95,000")
        assert wo["mileage"] == 95000
        assert shop_db.units.find_one({"_id": unit["_id"]})["mileage"] == 120000  # назад не откатили
        html = client.get(f"/work_orders/details?work_order_id={wo['_id']}").get_data(as_text=True)
        assert 'data-wo-mileage="95000"' in html
        wo2 = _create("130000")
        assert wo2["mileage"] == 130000
        assert shop_db.units.find_one({"_id": unit["_id"]})["mileage"] == 130000
    finally:
        shop_db.work_orders.delete_many({"customer_id": customer["_id"]})
        shop_db.units.delete_one({"_id": unit["_id"]})
        shop_db.customers.delete_one({"_id": customer["_id"]})
