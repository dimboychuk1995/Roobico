"""
Деактивация клиента (не удаление): activate/deactivate эндпоинты и пометка
«inactive» везде, где всплывают его сущности — списки WO, поиск юнитов,
платежи, детали WO.
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
def deact_seed(app, seed, mongo):
    """Клиент + юнит + WO с платежом в магазине A; после теста — подчистка."""
    from app.utils.entity_search import build_customer_search_terms, build_unit_search_terms

    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = _now()

    customer = {
        "_id": ObjectId(),
        "shop_id": shop_a["_id"],
        "tenant_id": seed["tenant_a"]["_id"],
        "company_name": "Deacto Fleet LLC",
        "contacts": [{"first_name": "Dina", "last_name": "Deacto",
                      "phone": "+1 (555) 777-0001", "email": "dina@deacto.example",
                      "is_main": True}],
        "taxable": False,
        "is_active": True,
        "created_at": now,
    }
    customer["search_terms"] = build_customer_search_terms(customer)
    shop_db.customers.insert_one(customer)

    unit = {
        "_id": ObjectId(),
        "shop_id": shop_a["_id"],
        "customer_id": customer["_id"],
        "unit_number": "DEACTO-1",
        "vin": "DEACTO12345678901",
        "make": "Ford", "model": "F-350", "year": 2024,
        "is_active": True,
        "created_at": now,
    }
    unit["search_terms"] = build_unit_search_terms(unit)
    shop_db.units.insert_one(unit)

    wo = {
        "_id": ObjectId(),
        "shop_id": shop_a["_id"],
        "customer_id": customer["_id"],
        "unit_id": unit["_id"],
        "wo_number": 99901,
        "work_order_date": now,
        "status": "open",
        "labors": [],
        "totals": {"labor_total": 0, "parts_total": 0, "grand_total": 100.0},
        "is_active": True,
        "created_at": now,
    }
    shop_db.work_orders.insert_one(wo)

    payment = {
        "_id": ObjectId(),
        "shop_id": shop_a["_id"],
        "work_order_id": wo["_id"],
        "amount": 40.0,
        "payment_method": "cash",
        "payment_date": now,
        "is_active": True,
        "created_at": now,
    }
    shop_db.work_order_payments.insert_one(payment)

    yield {"customer": customer, "unit": unit, "wo": wo}

    shop_db.work_order_payments.delete_one({"_id": payment["_id"]})
    shop_db.work_orders.delete_one({"_id": wo["_id"]})
    shop_db.units.delete_one({"_id": unit["_id"]})
    shop_db.customers.delete_one({"_id": customer["_id"]})


def _post(client, url):
    token = get_csrf_token(client)
    return client.post(url, data={"csrf_token": token})


def test_deactivate_and_activate_roundtrip(client, deact_seed, mongo):
    login(client)
    cid = str(deact_seed["customer"]["_id"])
    shop_db = mongo[SHOP_A_DB]

    resp = _post(client, f"/customers/{cid}/deactivate")
    assert resp.status_code == 302
    doc = shop_db.customers.find_one({"_id": deact_seed["customer"]["_id"]})
    assert doc["is_active"] is False
    assert doc["deactivated_at"] is not None

    # Детали клиента открываются и после деактивации.
    assert client.get(f"/customers/{cid}").status_code == 200

    resp = _post(client, f"/customers/{cid}/activate")
    assert resp.status_code == 302
    doc = shop_db.customers.find_one({"_id": deact_seed["customer"]["_id"]})
    assert doc["is_active"] is True
    assert doc["deactivated_at"] is None


def test_inactive_customer_marked_everywhere(client, deact_seed, mongo):
    login(client)
    cid = str(deact_seed["customer"]["_id"])
    _post(client, f"/customers/{cid}/deactivate")

    # Список WO (менеджер): флаг в items + WO ищется по имени клиента.
    rows = client.get("/api/mobile/work_orders?date_preset=all_time&q=Deacto").get_json()["items"]
    row = next(r for r in rows if r["id"] == str(deact_seed["wo"]["_id"]))
    assert "(inactive)" in row["customer"]

    # Страница списка WO: бейдж Inactive в строке.
    html = client.get("/work_orders?date_preset=all_time&q=Deacto").get_data(as_text=True)
    assert "This customer is deactivated" in html

    # Глобальный поиск юнитов: клиент помечен.
    units = client.get("/api/mobile/units?q=DEACTO-1").get_json()["items"]
    unit_row = next(u for u in units if u["id"] == str(deact_seed["unit"]["_id"]))
    assert unit_row["customer_inactive"] is True
    assert "(inactive)" in unit_row["customer_label"]

    # Платежи: флаг у строки платежа этого клиента.
    pays = client.get(
        "/work_orders/api/work_orders/all-payments?date_preset=all_time&q=Deacto"
    ).get_json()["payments"]
    assert pays and all(p["customer_inactive"] for p in pays if "Deacto" in p["customer"])

    # Детали WO (страница менеджера) открываются, клиент помечен.
    html = client.get(f"/work_orders/details?work_order_id={deact_seed['wo']['_id']}").get_data(as_text=True)
    assert "(inactive)" in html

    _post(client, f"/customers/{cid}/activate")
