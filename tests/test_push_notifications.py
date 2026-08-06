"""
Push-уведомления (Expo): регистрация токена устройства и WO-события механика
«взял в работу» / «закончил» — пуши офисным пользователям, не механикам.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import (
    SHOP_A_DB,
    TENANT_A_DB,
    TEST_MASTER,
    TEST_MONGO_URI,
    get_csrf_token,
    login,
)

MECHANIC_EMAIL = "pushmech@test.local"
MECHANIC_PASSWORD = "password123"
OWNER_TOKEN = "ExponentPushToken[owner-device-1]"
MECHANIC_TOKEN = "ExponentPushToken[mech-device-1]"


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture(scope="module")
def push_seed(app, seed, mongo):
    """Механик + клиент/юнит/парт в магазине A (владелец — офисный получатель)."""
    from app.constants.permissions import build_default_roles

    master = mongo[TEST_MASTER]
    tdb = mongo[TENANT_A_DB]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = _now()

    mechanic_role = next(r for r in build_default_roles() if r["key"] == "mechanic")
    tdb.roles.update_one({"key": "mechanic"}, {"$set": mechanic_role}, upsert=True)

    mechanic_user = {
        "_id": ObjectId(),
        "email": MECHANIC_EMAIL,
        "password_hash": generate_password_hash(MECHANIC_PASSWORD),
        "first_name": "Pete",
        "last_name": "Torque",
        "name": "Pete Torque",
        "is_active": True,
        "tenant_id": seed["tenant_a"]["_id"],
        "shop_ids": [str(shop_a["_id"])],
        "role": "mechanic",
        "created_at": now,
    }
    master.users.insert_one(mechanic_user)

    customer = {
        "_id": ObjectId(), "shop_id": shop_a["_id"],
        "tenant_id": seed["tenant_a"]["_id"], "company_name": "Push Fleet LLC",
        "taxable": False, "is_active": True, "created_at": now,
    }
    shop_db.customers.insert_one(customer)
    unit = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "customer_id": customer["_id"],
        "vin": "PUSH1234567890000", "unit_number": "P-1",
        "is_active": True, "created_at": now,
    }
    shop_db.units.insert_one(unit)
    part = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "part_number": "PUSH-PART-01",
        "description": "Push part", "average_cost": 10.0, "in_stock": 50,
        "is_active": True, "created_at": now,
        "search_terms": ["push", "part", "01", "pushpart01", "push-part-01"],
    }
    shop_db.parts.insert_one(part)

    return {
        "master": master,
        "shop_db": shop_db,
        "owner": seed["owner"],
        "mechanic": mechanic_user,
        "customer": customer,
        "unit": unit,
        "part": part,
    }


@pytest.fixture()
def push_capture(app, monkeypatch, push_seed):
    """Синхронная доставка + перехват HTTP к Expo; токены владельца и механика."""
    from app.utils import push_notifications

    sent: list[dict] = []

    def fake_post_expo(messages):
        sent.extend(messages)
        return [{"status": "ok"}] * len(messages)

    monkeypatch.setattr(push_notifications, "_post_expo", fake_post_expo)
    monkeypatch.setitem(app.config, "PUSH_SYNC", True)

    master = push_seed["master"]
    master.push_tokens.delete_many({})
    now = _now()
    master.push_tokens.insert_many([
        {"token": OWNER_TOKEN, "user_id": push_seed["owner"]["_id"],
         "platform": "ios", "created_at": now, "updated_at": now},
        {"token": MECHANIC_TOKEN, "user_id": push_seed["mechanic"]["_id"],
         "platform": "ios", "created_at": now, "updated_at": now},
    ])
    yield sent
    master.push_tokens.delete_many({})


def _post_json(client, url, payload):
    token = get_csrf_token(client)
    return client.post(url, json=payload, headers={"X-CSRFToken": token})


def _login_mechanic(client):
    return login(client, email=MECHANIC_EMAIL, password=MECHANIC_PASSWORD)


def _insert_wo(push_seed, status="open", labor_id="PL1", wo_number=910001, **extra):
    doc = {
        "_id": ObjectId(),
        "shop_id": push_seed["customer"]["shop_id"],
        "wo_number": wo_number,
        "customer_id": push_seed["customer"]["_id"],
        "unit_id": push_seed["unit"]["_id"],
        "status": status,
        "labors": [{
            "labor_id": labor_id,
            "labor": {"description": "Job", "hours": "1", "rate_code": "standard", "rate": 100},
            "parts": [],
        }],
        "totals": {"labors": [{"labor": 100.0}], "labor_total": 100.0,
                   "sales_tax_rate": 0, "is_taxable": False},
        "is_active": True,
        "created_at": _now(),
        "updated_at": _now(),
    }
    doc.update(extra)
    push_seed["shop_db"].work_orders.insert_one(doc)
    return doc


# ── регистрация токена ──────────────────────────────────────────────


def test_register_push_token_endpoint(client, push_seed):
    login(client)
    token = "ExponentPushToken[endpoint-test-1]"
    resp = _post_json(client, "/api/mobile/push-token", {"token": token, "platform": "ios"})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    doc = push_seed["master"].push_tokens.find_one({"token": token})
    assert doc is not None
    assert doc["user_id"] == push_seed["owner"]["_id"]
    assert doc["platform"] == "ios"

    # Перелогин другого пользователя на том же устройстве переносит токен
    _login_mechanic(client)
    resp = _post_json(client, "/api/mobile/push-token", {"token": token, "platform": "ios"})
    assert resp.status_code == 200
    doc = push_seed["master"].push_tokens.find_one({"token": token})
    assert doc["user_id"] == push_seed["mechanic"]["_id"]
    assert push_seed["master"].push_tokens.count_documents({"token": token}) == 1

    # Мусорный токен — отказ
    resp = _post_json(client, "/api/mobile/push-token", {"token": "not-a-token"})
    assert resp.status_code == 400

    push_seed["master"].push_tokens.delete_many({"token": token})


def test_logout_removes_push_token(client, push_seed):
    login(client)
    token = "ExponentPushToken[logout-test-1]"
    _post_json(client, "/api/mobile/push-token", {"token": token, "platform": "android"})
    assert push_seed["master"].push_tokens.count_documents({"token": token}) == 1

    resp = _post_json(client, "/api/mobile/logout", {"push_token": token})
    assert resp.status_code == 200
    assert push_seed["master"].push_tokens.count_documents({"token": token}) == 0


# ── события WO ──────────────────────────────────────────────────────


def test_timer_start_pushes_wo_taken(client, push_seed, push_capture):
    wo = _insert_wo(push_seed, status="open", wo_number=910010)
    _login_mechanic(client)

    resp = _post_json(client, "/work_orders/api/mechanic/timers/start",
                      {"work_order_id": str(wo["_id"]), "labor_id": "PL1"})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    # Пуш ушёл только владельцу (офис), не механику
    assert [m["to"] for m in push_capture] == [OWNER_TOKEN]
    msg = push_capture[0]
    assert msg["data"]["type"] == "wo_taken"
    assert msg["data"]["work_order_id"] == str(wo["_id"])
    assert msg["data"]["shop_id"] == str(push_seed["customer"]["shop_id"])
    assert "910010" in msg["title"]
    assert "Pete Torque" in msg["body"]

    # Повторный старт таймера: WO уже in_progress — нового пуша нет
    push_capture.clear()
    resp = _post_json(client, "/work_orders/api/mechanic/timers/start",
                      {"work_order_id": str(wo["_id"]), "labor_id": "PL1"})
    assert resp.status_code == 200
    assert push_capture == []


def test_mechanic_done_pushes_wo_finished(client, push_seed, push_capture):
    wo = _insert_wo(push_seed, status="in_progress", labor_id="PL2", wo_number=910020)
    _login_mechanic(client)

    save_payload = {
        "labors": [{"labor_id": "PL2", "description": "Job", "parts": []}],
        "mechanic_state": "done",
    }
    resp = _post_json(client, f"/work_orders/api/mechanic/work_orders/{wo['_id']}", save_payload)
    assert resp.status_code == 200, resp.get_data(as_text=True)

    assert [m["to"] for m in push_capture] == [OWNER_TOKEN]
    msg = push_capture[0]
    assert msg["data"]["type"] == "wo_finished"
    assert "910020" in msg["title"]
    assert "finished" in msg["body"]

    # Повторное сохранение с тем же done — без нового пуша (нет перехода)
    push_capture.clear()
    resp = _post_json(client, f"/work_orders/api/mechanic/work_orders/{wo['_id']}", save_payload)
    assert resp.status_code == 200
    assert push_capture == []

    # Механик снова взялся (таймер сбрасывает done) — снова «взял в работу»
    resp = _post_json(client, "/work_orders/api/mechanic/timers/start",
                      {"work_order_id": str(wo["_id"]), "labor_id": "PL2"})
    assert resp.status_code == 200
    assert [m["data"]["type"] for m in push_capture] == ["wo_taken"]


def test_mechanic_create_pushes_wo_taken(client, push_seed, push_capture):
    _login_mechanic(client)
    resp = _post_json(client, "/work_orders/api/mechanic/work_orders", {
        "customer_id": str(push_seed["customer"]["_id"]),
        "unit_id": str(push_seed["unit"]["_id"]),
        "labors": [{"description": "New job",
                    "parts": [{"part_id": str(push_seed["part"]["_id"]), "qty": 1}]}],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)

    assert [m["to"] for m in push_capture] == [OWNER_TOKEN]
    assert push_capture[0]["data"]["type"] == "wo_taken"

    push_seed["shop_db"].work_orders.delete_one({"_id": ObjectId(resp.get_json()["id"])})


def test_office_user_actions_do_not_push(client, push_seed, push_capture):
    """Тот же переход в in_progress, но от владельца — событие не механика."""
    wo = _insert_wo(push_seed, status="open", labor_id="PL3", wo_number=910030)
    login(client)  # владелец

    resp = _post_json(client, "/work_orders/api/mechanic/timers/start",
                      {"work_order_id": str(wo["_id"]), "labor_id": "PL3"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert push_capture == []


def test_dead_tokens_are_cleaned_up(app, push_seed, monkeypatch):
    """DeviceNotRegistered от Expo удаляет токен из базы."""
    from app.utils import push_notifications

    master = push_seed["master"]
    dead = "ExponentPushToken[dead-device-1]"
    now = _now()
    master.push_tokens.insert_one({
        "token": dead, "user_id": push_seed["owner"]["_id"],
        "platform": "ios", "created_at": now, "updated_at": now,
    })

    monkeypatch.setattr(
        push_notifications, "_post_expo",
        lambda messages: [
            {"status": "error", "message": "gone", "details": {"error": "DeviceNotRegistered"}}
        ] * len(messages),
    )
    monkeypatch.setitem(app.config, "PUSH_SYNC", True)

    with app.test_request_context("/"):
        push_notifications.send_push_to_users(
            master, [push_seed["owner"]["_id"]], "t", "b", {}
        )

    assert master.push_tokens.count_documents({"token": dead}) == 0
    master.push_tokens.delete_many({})
