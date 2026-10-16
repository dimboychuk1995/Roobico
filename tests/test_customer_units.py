"""
Создание юнита со страницы клиента: POST /customers/<id>/units/create
(право customers.manage_units), дубль VIN в пределах клиента, неактивный
клиент, кнопка и модалка на вкладке Units, общая модалка на странице WO.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from app.utils.entity_search import build_unit_search_terms
from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, get_csrf_token, login


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def cu_seed(seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    now = datetime.now(timezone.utc)
    base = {"shop_id": seed["shop_a"]["_id"], "tenant_id": seed["tenant_a"]["_id"], "created_at": now, "updated_at": now}
    active = {"_id": ObjectId(), **base, "company_name": "Unit Page Fleet", "contacts": [], "is_active": True}
    inactive = {"_id": ObjectId(), **base, "company_name": "Unit Page Gone", "contacts": [], "is_active": False}
    shop_db.customers.insert_many([active, inactive])
    unit = {"_id": ObjectId(), **base, "customer_id": active["_id"], "vin": "CUVIN00000000001", "unit_number": "CU-1", "is_active": True}
    shop_db.units.insert_one(unit)
    yield {"active": active, "inactive": inactive, "unit": unit}
    shop_db.units.delete_many({"customer_id": {"$in": [active["_id"], inactive["_id"]]}})
    shop_db.customers.delete_many({"_id": {"$in": [active["_id"], inactive["_id"]]}})


def _post(client, url, data):
    data = dict(data)
    data["csrf_token"] = get_csrf_token(client)
    return client.post(url, data=data)


def test_create_unit_from_customer_page(client, cu_seed, mongo):
    login(client)
    cid = str(cu_seed["active"]["_id"])
    resp = _post(client, f"/customers/{cid}/units/create", {
        "unit_number": "CU-2", "vin": "cuvin00000000002", "make": "Volvo", "model": "VNL", "year": "2021", "mileage": "120,000",
    })
    assert resp.status_code == 302, resp.get_data(as_text=True)
    unit = mongo[SHOP_A_DB].units.find_one({"unit_number": "CU-2"})
    assert unit is not None
    assert unit["customer_id"] == cu_seed["active"]["_id"]
    assert unit["vin"] == "CUVIN00000000002" and unit["year"] == 2021 and unit["mileage"] == 120000
    assert unit["is_active"] is True and unit["shop_id"] == cu_seed["active"]["shop_id"]
    assert unit["search_terms"] == build_unit_search_terms(unit)
    assert f"/customers/{cid}/units/{unit['_id']}" in resp.headers["Location"]


def test_create_unit_guards(client, cu_seed, mongo):
    login(client)
    shop_db = mongo[SHOP_A_DB]
    cid = str(cu_seed["active"]["_id"])
    # Дубль VIN у того же клиента — не создаётся.
    resp = _post(client, f"/customers/{cid}/units/create", {"unit_number": "CU-9", "vin": "CUVIN00000000001"})
    assert resp.status_code == 302
    assert shop_db.units.count_documents({"customer_id": cu_seed["active"]["_id"], "vin": {"$regex": "^CUVIN00000000001$", "$options": "i"}}) == 1
    # Ни номера, ни VIN — не создаётся.
    resp = _post(client, f"/customers/{cid}/units/create", {"make": "Volvo"})
    assert resp.status_code == 302
    assert shop_db.units.count_documents({"customer_id": cu_seed["active"]["_id"]}) == 1
    # Мусорные числа — не 500, а отказ.
    for bad in ("inf", "1e30", "-5"):
        resp = _post(client, f"/customers/{cid}/units/create", {"unit_number": f"CU-BAD-{bad}", "mileage": bad})
        assert resp.status_code == 302, bad
        assert shop_db.units.find_one({"unit_number": f"CU-BAD-{bad}"}).get("mileage") is None
    # Деактивированный клиент — не создаётся.
    gone = str(cu_seed["inactive"]["_id"])
    resp = _post(client, f"/customers/{gone}/units/create", {"unit_number": "GONE-1"})
    assert resp.status_code == 302
    assert shop_db.units.count_documents({"customer_id": cu_seed["inactive"]["_id"]}) == 0


def test_units_tab_has_add_unit_modal_and_wo_page_keeps_one(client, cu_seed, seed, mongo):
    login(client)
    cid = str(cu_seed["active"]["_id"])
    html = client.get(f"/customers/{cid}?tab=units").get_data(as_text=True)
    assert "Add Unit" in html and html.count('id="createUnitModal"') == 1
    assert "unit_create_modal.js" in html
    assert f"/customers/{cid}/units/create" in html
    # Неактивному клиенту кнопку не показываем.
    gone = str(cu_seed["inactive"]["_id"])
    html = client.get(f"/customers/{gone}?tab=units").get_data(as_text=True)
    assert 'id="createUnitModal"' not in html
    # Страница WO после выноса модалки в компонент — ровно одна модалка, свой action.
    html = client.get(f"/work_orders/details?customer_id={cid}").get_data(as_text=True)
    assert html.count('id="createUnitModal"') == 1
    assert "/work_orders/units/create" in html and "unit_create_modal.js" not in html
