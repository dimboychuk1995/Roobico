"""
Transfer WO на другого клиента (юнит следует за WO по VIN) и правка
фактических часов механиков (permission work_orders.edit_time_logs).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import SHOP_A_DB, TENANT_A_DB, TEST_MONGO_URI, get_csrf_token, login

MECH_EMAIL = "transfer-mech@test.local"
MECH_PASSWORD = "password123"


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture(scope="module")
def wo_seed(app, seed, mongo):
    """Механик + клиенты для трансфера: A (юнит), B (без юнитов),
    A2 (юнит с VIN, который уже есть у C)."""
    from app.constants.permissions import build_default_roles

    master = mongo["roobico_test_master"]
    tdb = mongo[TENANT_A_DB]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = _now()

    mechanic_role = next(r for r in build_default_roles() if r["key"] == "mechanic")
    tdb.roles.update_one({"key": "mechanic"}, {"$set": mechanic_role}, upsert=True)

    mech_user = {
        "_id": ObjectId(),
        "email": MECH_EMAIL,
        "password_hash": generate_password_hash(MECH_PASSWORD),
        "first_name": "Tim", "last_name": "Transfer",
        "is_active": True,
        "tenant_id": seed["tenant_a"]["_id"],
        "shop_ids": [str(shop_a["_id"])],
        "role": "mechanic",
        "created_at": now,
    }
    master.users.insert_one(mech_user)

    def _customer(name):
        doc = {
            "_id": ObjectId(), "shop_id": shop_a["_id"],
            "tenant_id": seed["tenant_a"]["_id"],
            "company_name": name, "taxable": False,
            "is_active": True, "created_at": now,
        }
        shop_db.customers.insert_one(doc)
        return doc

    cust_a = _customer("Transfer Source LLC")
    cust_b = _customer("Transfer Target LLC")
    cust_a2 = _customer("Transfer Source Two LLC")
    cust_c = _customer("Transfer Existing-VIN LLC")

    def _unit(customer, vin, unit_number, mileage):
        doc = {
            "_id": ObjectId(), "shop_id": shop_a["_id"],
            "customer_id": customer["_id"],
            "vin": vin, "unit_number": unit_number,
            "make": "Freightliner", "model": "Cascadia", "year": 2020,
            "mileage": mileage,
            "is_active": True, "created_at": now,
        }
        shop_db.units.insert_one(doc)
        return doc

    unit_a = _unit(cust_a, "TRFVIN00000000001", "T-1", 120000)
    unit_a2 = _unit(cust_a2, "TRFVIN00000000002", "T-2", 90000)
    # У клиента C уже есть юнит с VIN юнита A2 (другой регистр — матч по VIN
    # регистронезависимый).
    unit_c = _unit(cust_c, "trfvin00000000002", "C-2", 80000)

    part = {
        "_id": ObjectId(), "shop_id": shop_a["_id"],
        "part_number": "TRF-PART-01", "description": "Transfer part",
        "average_cost": 10.0, "in_stock": 50, "is_active": True,
        "search_terms": ["trf", "part", "01"], "created_at": now,
    }
    shop_db.parts.insert_one(part)

    return {
        "mech": mech_user,
        "cust_a": cust_a, "cust_b": cust_b, "cust_a2": cust_a2, "cust_c": cust_c,
        "unit_a": unit_a, "unit_a2": unit_a2, "unit_c": unit_c,
        "part": part,
    }


def _post_json(client, url, payload):
    token = get_csrf_token(client)
    return client.post(url, json=payload, headers={"X-CSRFToken": token})


def _create_wo(client, wo_seed, customer, unit, description="Fix brakes"):
    """WO создаётся механиком (реальный кейс: механик завёл не на того клиента)."""
    login(client, email=MECH_EMAIL, password=MECH_PASSWORD)
    resp = _post_json(client, "/work_orders/api/mechanic/work_orders", {
        "customer_id": str(customer["_id"]),
        "unit_id": str(unit["_id"]),
        "labors": [{"description": description, "parts": []}],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["ok"] is True
    return data


# ─────────────────────────── Transfer WO ───────────────────────────

def test_transfer_moves_unit_to_target(client, app, wo_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    wo = _create_wo(client, wo_seed, wo_seed["cust_a"], wo_seed["unit_a"])

    login(client)
    resp = _post_json(
        client,
        f"/work_orders/api/work_orders/{wo['id']}/transfer",
        {"customer_id": str(wo_seed["cust_b"]["_id"])},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True
    assert body["unit_action"] == "moved"

    fresh = shop_db.work_orders.find_one({"_id": ObjectId(wo["id"])})
    assert fresh["customer_id"] == wo_seed["cust_b"]["_id"]

    # Исходный юнит деактивирован, у целевого клиента — активная копия
    # со всеми полями, включая пробег и search_terms.
    src = shop_db.units.find_one({"_id": wo_seed["unit_a"]["_id"]})
    assert src["is_active"] is False

    moved = shop_db.units.find_one({"_id": fresh["unit_id"]})
    assert moved["customer_id"] == wo_seed["cust_b"]["_id"]
    assert moved["vin"] == "TRFVIN00000000001"
    assert moved["unit_number"] == "T-1"
    assert moved["mileage"] == 120000
    assert moved["is_active"] is True
    assert moved.get("search_terms")


def test_transfer_links_existing_vin_unit(client, app, wo_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    wo = _create_wo(client, wo_seed, wo_seed["cust_a2"], wo_seed["unit_a2"])

    login(client)
    resp = _post_json(
        client,
        f"/work_orders/api/work_orders/{wo['id']}/transfer",
        {"customer_id": str(wo_seed["cust_c"]["_id"])},
    )
    body = resp.get_json()
    assert body["ok"] is True, body
    assert body["unit_action"] == "linked_existing"

    fresh = shop_db.work_orders.find_one({"_id": ObjectId(wo["id"])})
    assert fresh["customer_id"] == wo_seed["cust_c"]["_id"]
    assert fresh["unit_id"] == wo_seed["unit_c"]["_id"]

    # Исходный юнит клиента A2 остаётся как был (у цели свой юнит).
    src = shop_db.units.find_one({"_id": wo_seed["unit_a2"]["_id"]})
    assert src["is_active"] is True


def test_transfer_guards(client, app, wo_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    wo = _create_wo(client, wo_seed, wo_seed["cust_a2"], wo_seed["unit_a2"])
    wo_oid = ObjectId(wo["id"])

    # Механику трансфер недоступен (нет work_orders.edit).
    resp = _post_json(
        client,
        f"/work_orders/api/work_orders/{wo['id']}/transfer",
        {"customer_id": str(wo_seed["cust_b"]["_id"])},
    )
    assert resp.status_code == 403

    login(client)
    # На того же клиента — отказ.
    resp = _post_json(
        client,
        f"/work_orders/api/work_orders/{wo['id']}/transfer",
        {"customer_id": str(wo_seed["cust_a2"]["_id"])},
    )
    assert resp.get_json()["error"] == "same_customer"

    # Оплаченный WO не переносится.
    shop_db.work_orders.update_one({"_id": wo_oid}, {"$set": {"status": "paid"}})
    try:
        resp = _post_json(
            client,
            f"/work_orders/api/work_orders/{wo['id']}/transfer",
            {"customer_id": str(wo_seed["cust_b"]["_id"])},
        )
        assert resp.get_json()["error"] == "paid_cannot_edit"
    finally:
        shop_db.work_orders.update_one({"_id": wo_oid}, {"$set": {"status": "open"}})


# ─────────────────── Правка фактических часов ───────────────────────

def _make_completed_session(client, wo_seed, mongo):
    """WO + завершённая сессия таймера механика. Возвращает (wo_id, labor_id, log)."""
    shop_db = mongo[SHOP_A_DB]
    wo = _create_wo(client, wo_seed, wo_seed["cust_a2"], wo_seed["unit_a2"],
                    description="Timer job")
    doc = shop_db.work_orders.find_one({"_id": ObjectId(wo["id"])})
    labor_id = doc["labors"][0]["labor_id"]

    # механик уже залогинен после _create_wo
    resp = _post_json(client, "/work_orders/api/mechanic/timers/start",
                      {"work_order_id": wo["id"], "labor_id": labor_id})
    assert resp.get_json()["ok"] is True
    resp = _post_json(client, "/work_orders/api/mechanic/timers/stop", {})
    assert resp.get_json()["ok"] is True

    log = shop_db.wo_time_logs.find_one(
        {"work_order_id": ObjectId(wo["id"]), "labor_id": labor_id,
         "stopped_at": {"$ne": None}},
    )
    assert log is not None
    return wo["id"], labor_id, log


def test_owner_edits_and_deletes_time_log(client, app, wo_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    wo_id, labor_id, log = _make_completed_session(client, wo_seed, mongo)

    login(client)  # owner — protected роль получает новое право автоматически

    # Список сессий по строке.
    resp = client.get(f"/work_orders/api/work_orders/{wo_id}/time_logs?labor_id={labor_id}")
    body = resp.get_json()
    assert body["ok"] is True
    sessions = body["sessions"]
    assert any(s["id"] == str(log["_id"]) for s in sessions)

    # Правка длительности: 1.5 часа.
    resp = _post_json(client, f"/work_orders/api/time_logs/{log['_id']}/update",
                      {"seconds": 5400})
    body = resp.get_json()
    assert body["ok"] is True, body

    fresh = shop_db.wo_time_logs.find_one({"_id": log["_id"]})
    assert fresh["seconds"] == 5400
    assert fresh["stopped_at"] > fresh["started_at"]
    assert abs((fresh["stopped_at"] - fresh["started_at"]).total_seconds() - 5400) < 1
    assert fresh.get("edited_by") is not None
    assert fresh.get("original_seconds") == int(log.get("seconds") or 0)

    # Невалидная длительность.
    resp = _post_json(client, f"/work_orders/api/time_logs/{log['_id']}/update",
                      {"seconds": 0})
    assert resp.get_json()["error"] == "invalid_seconds"

    # Удаление сессии.
    resp = _post_json(client, f"/work_orders/api/time_logs/{log['_id']}/delete", {})
    assert resp.get_json()["ok"] is True
    assert shop_db.wo_time_logs.find_one({"_id": log["_id"]}) is None


def test_time_log_edit_requires_permission(client, app, wo_seed, mongo):
    wo_id, labor_id, log = _make_completed_session(client, wo_seed, mongo)

    # Механик (без work_orders.edit_time_logs) — 403 на всех эндпоинтах.
    resp = client.get(f"/work_orders/api/work_orders/{wo_id}/time_logs")
    assert resp.status_code == 403
    resp = _post_json(client, f"/work_orders/api/time_logs/{log['_id']}/update",
                      {"seconds": 100})
    assert resp.status_code == 403
    resp = _post_json(client, f"/work_orders/api/time_logs/{log['_id']}/delete", {})
    assert resp.status_code == 403


def test_time_log_edit_blocked_on_paid_wo(client, app, wo_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    wo_id, labor_id, log = _make_completed_session(client, wo_seed, mongo)
    shop_db.work_orders.update_one(
        {"_id": ObjectId(wo_id)}, {"$set": {"status": "paid"}}
    )
    try:
        login(client)
        resp = _post_json(client, f"/work_orders/api/time_logs/{log['_id']}/update",
                          {"seconds": 999})
        assert resp.get_json()["error"] == "paid_wo_locked"
    finally:
        shop_db.work_orders.update_one(
            {"_id": ObjectId(wo_id)}, {"$set": {"status": "open"}}
        )
