"""
Один юнит в цеху — один WO. Второй механик сканирует VIN или выбирает
клиента и юнит руками, а по юниту уже идёт работа (другой механик с
таймером или просто in_progress) → ему открывается тот WO, дубль не
создаётся: эндпоинт проверки при выборе юнита + 409 на создании.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import SHOP_A_DB, TENANT_A_DB, TEST_MONGO_URI, get_csrf_token, login

MECH_EMAIL = "active-wo-mechanic@test.local"
MECH2_EMAIL = "active-wo-mechanic-2@test.local"
PASSWORD = "password123"
ACTIVE_URL = "/work_orders/api/mechanic/units/{}/active_work_order"


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture(scope="module")
def unit_seed(app, seed, mongo):
    """Два механика + клиент с одним юнитом (свой, чтобы чужие WO не мешали)."""
    from app.constants.permissions import build_default_roles

    master = mongo["roobico_test_master"]
    tdb = mongo[TENANT_A_DB]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = datetime.now(timezone.utc)

    mechanic_role = next(r for r in build_default_roles() if r["key"] == "mechanic")
    tdb.roles.update_one({"key": "mechanic"}, {"$set": mechanic_role}, upsert=True)

    users = []
    for email, first in ((MECH_EMAIL, "Ivan"), (MECH2_EMAIL, "Petr")):
        doc = {
            "_id": ObjectId(), "email": email, "password_hash": generate_password_hash(PASSWORD),
            "first_name": first, "last_name": "Scanner", "name": f"{first} Scanner", "is_active": True,
            "tenant_id": seed["tenant_a"]["_id"], "shop_ids": [str(shop_a["_id"])], "role": "mechanic",
            "created_at": now,
        }
        master.users.insert_one(doc)
        users.append(doc)

    customer = {"_id": ObjectId(), "shop_id": shop_a["_id"], "tenant_id": seed["tenant_a"]["_id"],
                "company_name": "Active WO Fleet", "taxable": False, "is_active": True, "created_at": now}
    shop_db.customers.insert_one(customer)
    unit = {"_id": ObjectId(), "shop_id": shop_a["_id"], "customer_id": customer["_id"],
            "vin": "ACTIVEWO000000001", "unit_number": "AW-1", "mileage": 5000, "is_active": True, "created_at": now}
    shop_db.units.insert_one(unit)

    yield {"users": users, "customer": customer, "unit": unit}

    shop_db.work_orders.delete_many({"unit_id": unit["_id"]})
    shop_db.wo_time_logs.delete_many({"user_id": {"$in": [u["_id"] for u in users]}})
    shop_db.units.delete_one({"_id": unit["_id"]})
    shop_db.customers.delete_one({"_id": customer["_id"]})
    master.users.delete_many({"email": {"$in": [MECH_EMAIL, MECH2_EMAIL]}})


def _post_json(client, url, payload):
    token = get_csrf_token(client)
    return client.post(url, json=payload, headers={"X-CSRFToken": token})


def _login(client, email=MECH_EMAIL):
    assert login(client, email=email, password=PASSWORD).status_code == 302


def _create_payload(unit_seed, **extra):
    payload = {
        "customer_id": str(unit_seed["customer"]["_id"]),
        "unit_id": str(unit_seed["unit"]["_id"]),
        "unit_mileage": 5000,
        "labors": [{"description": "Brake job", "parts": []}],
    }
    payload.update(extra)
    return payload


def _create(client, unit_seed, **extra):
    resp = _post_json(client, "/work_orders/api/mechanic/work_orders", _create_payload(unit_seed, **extra))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def _first_labor_id(client, wo_id):
    return client.get(f"/work_orders/api/mechanic/work_orders/{wo_id}").get_json()["labors"][0]["labor_id"]


def _park_unit(mongo, unit_id):
    """Освободить юнит: все его WO — неактивны."""
    mongo[SHOP_A_DB].work_orders.update_many({"unit_id": unit_id}, {"$set": {"is_active": False}})


def test_second_mechanic_lands_on_existing_wo(client, unit_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    unit_id = unit_seed["unit"]["_id"]
    _park_unit(mongo, unit_id)

    # Свободный юнит: WO нет, первый механик создаёт.
    _login(client, MECH_EMAIL)
    resp = client.get(ACTIVE_URL.format(unit_id))
    assert resp.status_code == 200 and resp.get_json()["work_order"] is None
    first = _create(client, unit_seed)

    # Второй механик «сканирует» тот же юнит: проверка отдаёт WO коллеги.
    _login(client, MECH2_EMAIL)
    data = client.get(ACTIVE_URL.format(unit_id)).get_json()
    assert data["work_order"]["id"] == first["id"]
    assert data["work_order"]["wo_number"] == first["wo_number"]
    assert data["work_order"]["working_now"] == []
    assert f"#{first['wo_number']}" in data["message"]

    # Попытка завести второй WO — 409 с тем же WO; веб и мобильный одинаково,
    # ничего не создано.
    before = shop_db.work_orders.count_documents({"unit_id": unit_id, "is_active": True})
    resp = _post_json(client, "/work_orders/api/mechanic/work_orders", _create_payload(unit_seed))
    assert resp.status_code == 409, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["error"] == "unit_has_active_work_order" and body["work_order"]["id"] == first["id"]
    resp = _post_json(client, "/api/mobile/work_orders", {
        **_create_payload(unit_seed), "status": "in_progress", "labors": [], "mechanic_state": "in_progress",
    })
    assert resp.status_code == 409 and resp.get_json()["work_order"]["id"] == first["id"]
    assert shop_db.work_orders.count_documents({"unit_id": unit_id, "is_active": True}) == before

    # Первый механик запустил таймер — второй видит, кто работает.
    _login(client, MECH_EMAIL)
    labor_id = _first_labor_id(client, first["id"])
    assert _post_json(client, "/work_orders/api/mechanic/timers/start",
                      {"work_order_id": first["id"], "labor_id": labor_id}).status_code == 200
    _login(client, MECH2_EMAIL)
    data = client.get(ACTIVE_URL.format(unit_id)).get_json()
    assert data["work_order"]["working_now"] == ["Ivan Scanner"]
    assert "Ivan Scanner working on it" in data["message"]

    # Явный обход (автоматизация/тесты) — дубль создаётся; из двух WO в
    # работе проверка предпочитает тот, где идёт таймер.
    second = _create(client, unit_seed, allow_duplicate_unit_wo=True)
    assert client.get(ACTIVE_URL.format(unit_id)).get_json()["work_order"]["id"] == first["id"]
    _login(client, MECH_EMAIL)
    assert _post_json(client, "/work_orders/api/mechanic/timers/stop", {}).status_code == 200
    # Таймеров нет — самый свежий (второй) WO.
    assert client.get(ACTIVE_URL.format(unit_id)).get_json()["work_order"]["id"] == second["id"]

    _park_unit(mongo, unit_id)


def test_active_unit_wo_ignores_confirmed_finished_and_inactive(client, unit_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    unit_id = unit_seed["unit"]["_id"]
    _park_unit(mongo, unit_id)
    _login(client, MECH_EMAIL)
    wo = _create(client, unit_seed)
    wo_oid = ObjectId(wo["id"])

    # Подтверждённый менеджером WO для механика закрыт — юнит считается свободным.
    shop_db.work_orders.update_one({"_id": wo_oid}, {"$set": {"manager_confirmed": True}})
    assert client.get(ACTIVE_URL.format(unit_id)).get_json()["work_order"] is None
    extra = _create(client, unit_seed)
    shop_db.work_orders.update_one({"_id": ObjectId(extra["id"])}, {"$set": {"is_active": False}})

    # Законченный (open) и оплаченный — свободный юнит.
    for status in ("open", "paid"):
        shop_db.work_orders.update_one({"_id": wo_oid}, {"$set": {"status": status, "manager_confirmed": False}})
        assert client.get(ACTIVE_URL.format(unit_id)).get_json()["work_order"] is None

    # Снова in_progress — находится; неактивный — нет.
    shop_db.work_orders.update_one({"_id": wo_oid}, {"$set": {"status": "in_progress"}})
    assert client.get(ACTIVE_URL.format(unit_id)).get_json()["work_order"]["id"] == wo["id"]
    shop_db.work_orders.update_one({"_id": wo_oid}, {"$set": {"is_active": False}})
    assert client.get(ACTIVE_URL.format(unit_id)).get_json()["work_order"] is None

    # Несуществующий юнит — 404, битый id — 400.
    assert client.get(ACTIVE_URL.format(ObjectId())).status_code == 404
    assert client.get(ACTIVE_URL.format("not-an-id")).status_code == 400


def test_manager_is_not_blocked_by_active_unit_wo(client, unit_seed, mongo):
    """Правило — для механик-режима: менеджер заводит WO осознанно."""
    unit_id = unit_seed["unit"]["_id"]
    _park_unit(mongo, unit_id)
    _login(client, MECH_EMAIL)
    first = _create(client, unit_seed)

    assert login(client).status_code == 302  # owner видит деньги → не механик-режим
    resp = _post_json(client, "/api/mobile/work_orders", {
        "customer_id": str(unit_seed["customer"]["_id"]),
        "unit_id": str(unit_seed["unit"]["_id"]),
        "status": "open",
        "labors": [{"description": "Manager job", "hours": 1, "rate_code": "standard", "parts": []}],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["id"] != first["id"]
    # Эндпоинт проверки доступен и менеджеру (work_orders.view).
    assert client.get(ACTIVE_URL.format(unit_id)).get_json()["work_order"]["id"] == first["id"]

    _park_unit(mongo, unit_id)
