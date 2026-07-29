"""
Подтверждение WO менеджером (manager_confirmed): лок для механика,
отмена подтверждения, скрытие paid WO от механика, глобальный поиск юнитов.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import SHOP_A_DB, TENANT_A_DB, TEST_MONGO_URI, get_csrf_token, login

MECHANIC_EMAIL = "mechanic-confirm@test.local"
MECHANIC_PASSWORD = "password123"


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture(scope="module")
def conf_seed(app, seed, mongo):
    """Механик + клиент/юнит (с search_terms)/парт в магазине A."""
    from app.constants.permissions import build_default_roles
    from app.utils.entity_search import build_unit_search_terms

    master = mongo["roobico_test_master"]
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
        "first_name": "Carl",
        "last_name": "Confirm",
        "is_active": True,
        "tenant_id": seed["tenant_a"]["_id"],
        "shop_ids": [str(shop_a["_id"])],
        "role": "mechanic",
        "created_at": now,
    }
    master.users.insert_one(mechanic_user)

    customer = {
        "_id": ObjectId(),
        "shop_id": shop_a["_id"],
        "tenant_id": seed["tenant_a"]["_id"],
        "company_name": "Conf Fleet LLC",
        "taxable": False,
        "is_active": True,
        "created_at": now,
    }
    shop_db.customers.insert_one(customer)

    unit = {
        "_id": ObjectId(),
        "shop_id": shop_a["_id"],
        "customer_id": customer["_id"],
        "vin": "CONF1234567890000",
        "unit_number": "CONF-7",
        "is_active": True,
        "created_at": now,
    }
    unit["search_terms"] = build_unit_search_terms(unit)
    shop_db.units.insert_one(unit)

    part = {
        "_id": ObjectId(),
        "shop_id": shop_a["_id"],
        "part_number": "CONF-PART-01",
        "description": "Oil filter",
        "average_cost": 10.0,
        "has_selling_price": True,
        "selling_price": 25.0,
        "in_stock": 20,
        "is_active": True,
        "created_at": now,
    }
    shop_db.parts.insert_one(part)

    return {"user": mechanic_user, "customer": customer, "unit": unit, "part": part}


def login_mechanic(client):
    return login(client, email=MECHANIC_EMAIL, password=MECHANIC_PASSWORD)


def _post_json(client, url, payload):
    token = get_csrf_token(client)
    return client.post(url, json=payload, headers={"X-CSRFToken": token})


def _create_wo_as_mechanic(client, conf_seed, description="Change oil"):
    resp = _post_json(client, "/work_orders/api/mechanic/work_orders", {
        "customer_id": str(conf_seed["customer"]["_id"]),
        "unit_id": str(conf_seed["unit"]["_id"]),
        "labors": [{
            "description": description,
            "parts": [{"part_id": str(conf_seed["part"]["_id"]), "qty": 1}],
        }],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["ok"] is True
    return data


def _set_confirmed(client, wo_id, confirmed):
    return _post_json(
        client, f"/work_orders/api/work_orders/{wo_id}/confirm", {"confirmed": confirmed}
    )


# ── подтверждение и лок ─────────────────────────────────────────────


def test_confirm_locks_mechanic_and_unconfirm_reopens(client, conf_seed):
    login_mechanic(client)
    wo = _create_wo_as_mechanic(client, conf_seed)
    wo_id = wo["id"]

    # labor_id понадобится для проверки таймера.
    details = client.get(f"/api/mobile/work_orders/{wo_id}").get_json()
    labor_id = details["labors"][0]["labor_id"]

    # Менеджер (owner) подтверждает.
    login(client)
    resp = _set_confirmed(client, wo_id, True)
    assert resp.status_code == 200
    assert resp.get_json()["manager_confirmed"] is True

    # Owner по-прежнему видит детали, включая флаг.
    resp = client.get(f"/api/mobile/work_orders/{wo_id}")
    assert resp.status_code == 200
    assert resp.get_json()["manager_confirmed"] is True

    # Механик: детали закрыты (мобильный и веб-эндпоинты).
    login_mechanic(client)
    resp = client.get(f"/api/mobile/work_orders/{wo_id}")
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "wo_confirmed"

    resp = client.get(f"/work_orders/api/mechanic/work_orders/{wo_id}")
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "wo_confirmed"

    # Механик: правка запрещена (оба эндпоинта).
    edit_payload = {"labors": [{"description": "Sneaky edit", "parts": []}]}
    resp = _post_json(client, f"/api/mobile/work_orders/{wo_id}", edit_payload)
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "wo_confirmed"

    resp = _post_json(client, f"/work_orders/api/mechanic/work_orders/{wo_id}", edit_payload)
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "wo_confirmed"

    # Механик: таймер не стартует.
    resp = _post_json(client, "/work_orders/api/mechanic/timers/start", {
        "work_order_id": wo_id,
        "labor_id": labor_id,
    })
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "wo_confirmed"

    # В списке WO остаётся виден с флагом (открыть нельзя — решает клиент).
    resp = client.get("/api/mobile/work_orders")
    items = resp.get_json()["items"]
    row = next(i for i in items if i["id"] == wo_id)
    assert row["manager_confirmed"] is True

    # Менеджер снимает подтверждение — механику снова можно всё.
    login(client)
    resp = _set_confirmed(client, wo_id, False)
    assert resp.status_code == 200
    assert resp.get_json()["manager_confirmed"] is False

    login_mechanic(client)
    resp = client.get(f"/api/mobile/work_orders/{wo_id}")
    assert resp.status_code == 200
    resp = _post_json(client, f"/api/mobile/work_orders/{wo_id}", {
        "labors": [{"labor_id": labor_id, "description": "Change oil + filter", "parts": []}],
    })
    assert resp.status_code == 200


def test_mechanic_cannot_confirm(client, conf_seed):
    login_mechanic(client)
    wo = _create_wo_as_mechanic(client, conf_seed, description="No self-confirm")
    resp = _set_confirmed(client, wo["id"], True)
    assert resp.status_code == 403


# ── paid WO скрыты от механика ──────────────────────────────────────


def test_mechanic_list_excludes_paid(client, conf_seed):
    login_mechanic(client)
    wo = _create_wo_as_mechanic(client, conf_seed, description="Will be paid")
    wo_id = wo["id"]

    login(client)
    resp = _post_json(client, f"/work_orders/api/work_orders/{wo_id}/status", {"status": "paid"})
    assert resp.status_code == 200

    # Owner видит paid WO даже с явным фильтром.
    resp = client.get("/api/mobile/work_orders?paid_status=paid")
    assert any(i["id"] == wo_id for i in resp.get_json()["items"])

    # Механик — нет, даже если попросит paid явно.
    login_mechanic(client)
    for url in (
        "/api/mobile/work_orders",
        "/api/mobile/work_orders?paid_status=paid",
        "/work_orders/api/mechanic/work_orders",
    ):
        items = client.get(url).get_json()["items"]
        assert all(i["id"] != wo_id for i in items), url
        assert all(not i.get("is_paid") for i in items), url


# ── глобальный поиск юнитов ─────────────────────────────────────────


def test_units_global_search_returns_customer(client, conf_seed):
    login_mechanic(client)
    resp = client.get("/api/mobile/units?q=CONF-7")
    assert resp.status_code == 200
    items = resp.get_json()["items"]
    row = next(i for i in items if i["id"] == str(conf_seed["unit"]["_id"]))
    assert row["customer_label"] == "Conf Fleet LLC"
    assert row["unit_number"] == "CONF-7"

    # Поиск по VIN тоже работает.
    resp = client.get("/api/mobile/units?q=CONF1234567890000")
    ids = [i["id"] for i in resp.get_json()["items"]]
    assert str(conf_seed["unit"]["_id"]) in ids
