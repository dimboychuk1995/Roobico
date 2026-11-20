"""
Запрет дублей при создании/переименовании: parts (part_number),
customers (название), units (VIN в рамках клиента), vendors (имя).
Неактивный дубль — отдельное сообщение с предложением реактивировать.
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
def dup_seed(seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = _now()
    base = {"shop_id": shop_a["_id"], "tenant_id": seed["tenant_a"]["_id"],
            "created_at": now, "updated_at": now}

    docs = {
        "part_active": {"_id": ObjectId(), **base, "part_number": "DUP-100",
                        "description": "Filter", "is_active": True, "in_stock": 0},
        "part_inactive": {"_id": ObjectId(), **base, "part_number": "DUP-GONE",
                          "is_active": False, "in_stock": 0},
        "part_other": {"_id": ObjectId(), **base, "part_number": "OTHER-1",
                       "is_active": True, "in_stock": 0},
        "vendor_active": {"_id": ObjectId(), **base, "name": "Acme Supply",
                          "is_active": True},
        "vendor_inactive": {"_id": ObjectId(), **base, "name": "Old Vendor",
                            "is_active": False},
        "customer_company": {"_id": ObjectId(), **base, "company_name": "Dup Trucking",
                             "contacts": [], "is_active": True},
        "customer_contact": {"_id": ObjectId(), **base, "company_name": None,
                             "contacts": [{"first_name": "John", "last_name": "Smith",
                                           "is_main": True}],
                             "is_active": True},
        "customer_inactive": {"_id": ObjectId(), **base, "company_name": "Ghost LLC",
                              "contacts": [], "is_active": False},
    }
    shop_db.parts.insert_many([docs["part_active"], docs["part_inactive"], docs["part_other"]])
    shop_db.vendors.insert_many([docs["vendor_active"], docs["vendor_inactive"]])
    shop_db.customers.insert_many([
        docs["customer_company"], docs["customer_contact"], docs["customer_inactive"]])

    unit = {"_id": ObjectId(), **base, "customer_id": docs["customer_company"]["_id"],
            "vin": "1FTVIN00000000001", "unit_number": "T-1", "is_active": True}
    unit2 = {"_id": ObjectId(), **base, "customer_id": docs["customer_company"]["_id"],
             "vin": "1FTVIN00000000002", "unit_number": "T-2", "is_active": True}
    shop_db.units.insert_many([unit, unit2])
    docs["unit"] = unit
    docs["unit2"] = unit2

    yield docs

    shop_db.parts.delete_many({"part_number": {"$in": ["DUP-100", "DUP-GONE", "OTHER-1", "NEW-1"]}})
    shop_db.vendors.delete_many({"name": {"$in": ["Acme Supply", "Old Vendor"]}})
    shop_db.units.delete_many({"customer_id": {"$in": [
        docs["customer_company"]["_id"], docs["customer_contact"]["_id"]]}})
    shop_db.customers.delete_many({"_id": {"$in": [
        docs["customer_company"]["_id"], docs["customer_contact"]["_id"],
        docs["customer_inactive"]["_id"]]}})


# ── Parts ───────────────────────────────────────────────────────────────


def test_part_create_duplicate_blocked(client, dup_seed, mongo):
    login(client)
    token = get_csrf_token(client)
    resp = client.post("/parts/api/create", json={"part_number": " dup-100 "},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 409
    assert "already exists" in resp.get_json()["error"]
    # новый док не создан — остался только исходный в верхнем регистре
    assert mongo[SHOP_A_DB].parts.count_documents(
        {"part_number": {"$regex": "dup-100", "$options": "i"}}) == 1
    assert mongo[SHOP_A_DB].parts.count_documents({"part_number": "DUP-100"}) == 1


def test_part_create_inactive_duplicate_mentions_parts_order(client, dup_seed):
    """Партам нет кнопки Restore: подсказка ведёт в парт-ордер."""
    login(client)
    token = get_csrf_token(client)
    resp = client.post("/parts/api/create", json={"part_number": "dup-gone"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 409
    err = resp.get_json()["error"]
    assert "inactive" in err and "parts order" in err


def test_part_rename_to_existing_blocked(client, dup_seed):
    login(client)
    token = get_csrf_token(client)
    other_id = str(dup_seed["part_other"]["_id"])
    resp = client.post(f"/parts/api/{other_id}/update",
                       json={"part_number": "DUP-100"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 409
    assert "already exists" in resp.get_json()["error"]


def test_part_update_keeping_own_number_ok(client, dup_seed):
    login(client)
    token = get_csrf_token(client)
    own_id = str(dup_seed["part_active"]["_id"])
    resp = client.post(f"/parts/api/{own_id}/update",
                       json={"part_number": "DUP-100", "description": "Filter v2"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["ok"] is True


# ── Vendors ─────────────────────────────────────────────────────────────


def test_vendor_create_duplicate_blocked(client, dup_seed, mongo):
    login(client)
    token = get_csrf_token(client)
    resp = client.post("/vendors/api/create", json={"name": "ACME supply"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 409
    assert "already exists" in resp.get_json()["error"]
    assert mongo[SHOP_A_DB].vendors.count_documents(
        {"name": {"$regex": "^acme supply$", "$options": "i"}}) == 1


def test_vendor_create_inactive_duplicate_mentions_deactivated(client, dup_seed):
    login(client)
    token = get_csrf_token(client)
    resp = client.post("/vendors/api/create", json={"name": "old vendor"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 409
    err = resp.get_json()["error"]
    assert "deactivated" in err and "Reactivate" in err


def test_vendor_rename_to_existing_blocked_but_self_ok(client, dup_seed):
    login(client)
    token = get_csrf_token(client)
    acme_id = str(dup_seed["vendor_active"]["_id"])
    # Своё имя (то же самое) — можно
    resp = client.post(f"/vendors/api/{acme_id}/update", json={"name": "Acme Supply"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 200 and resp.get_json()["ok"] is True
    # Имя другого вендора — нельзя
    resp = client.post(f"/vendors/api/{acme_id}/update", json={"name": "Old Vendor"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 409


def test_vendor_mobile_create_duplicate_blocked(client, dup_seed):
    login(client)
    token = get_csrf_token(client)
    resp = client.post("/api/mobile/vendors", json={"name": "acme SUPPLY"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 409
    data = resp.get_json()
    assert "already exists" in data["message"]
    assert "already exists" in data["error"]


# ── Customers ───────────────────────────────────────────────────────────


def test_customer_create_duplicate_blocked(client, dup_seed, mongo):
    login(client)
    token = get_csrf_token(client)
    resp = client.post("/customers/create", data={
        "company_name": "DUP trucking", "address": "123 Main Street",
        "csrf_token": token,
    })
    assert resp.status_code == 302
    assert mongo[SHOP_A_DB].customers.count_documents(
        {"company_name": {"$regex": "^dup trucking$", "$options": "i"}}) == 1


def test_customer_mobile_create_inactive_duplicate(client, dup_seed):
    login(client)
    token = get_csrf_token(client)
    resp = client.post("/api/mobile/customers",
                       json={"company_name": "ghost llc", "address": "456 Oak Avenue"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 409
    msg = resp.get_json()["message"]
    assert "deactivated" in msg and "Reactivate" in msg


def test_customer_duplicate_by_contact_name(seed, dup_seed, mongo, app):
    from app.utils.duplicates import find_duplicate_customer
    with app.app_context():
        found = find_duplicate_customer(
            mongo[SHOP_A_DB], seed["shop_a"]["_id"], "",
            [{"first_name": "john", "last_name": "SMITH", "is_main": True}],
        )
    assert found is not None
    assert found["_id"] == dup_seed["customer_contact"]["_id"]


def test_customer_update_keeping_own_name_ok(client, dup_seed):
    login(client)
    token = get_csrf_token(client)
    cid = str(dup_seed["customer_company"]["_id"])
    resp = client.post(f"/customers/api/{cid}/update",
                       json={"company_name": "Dup Trucking", "address": "123 Main Street"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["ok"] is True


def test_customer_rename_to_existing_blocked(client, dup_seed):
    login(client)
    token = get_csrf_token(client)
    cid = str(dup_seed["customer_contact"]["_id"])
    resp = client.post(f"/customers/api/{cid}/update",
                       json={"company_name": "dup trucking", "address": "789 Pine Road"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 409
    assert "already exists" in resp.get_json()["error"]


# ── Units ───────────────────────────────────────────────────────────────


def test_unit_same_vin_same_customer_blocked(client, dup_seed, mongo):
    login(client)
    token = get_csrf_token(client)
    cid = str(dup_seed["customer_company"]["_id"])
    resp = client.post("/work_orders/units/create", data={
        "customer_id": cid, "vin": "1ftvin00000000001", "unit_number": "T-9",
        "csrf_token": token,
    })
    assert resp.status_code == 302
    assert mongo[SHOP_A_DB].units.count_documents(
        {"customer_id": dup_seed["customer_company"]["_id"],
         "vin": {"$regex": "^1ftvin00000000001$", "$options": "i"}}) == 1


def test_unit_same_vin_other_customer_allowed(client, dup_seed, mongo):
    login(client)
    token = get_csrf_token(client)
    other_cid = str(dup_seed["customer_contact"]["_id"])
    resp = client.post("/work_orders/units/create", data={
        "customer_id": other_cid, "vin": "1FTVIN00000000001", "unit_number": "J-1",
        "csrf_token": token,
    })
    assert resp.status_code == 302
    assert mongo[SHOP_A_DB].units.count_documents(
        {"customer_id": dup_seed["customer_contact"]["_id"],
         "vin": "1FTVIN00000000001"}) == 1


def test_unit_update_vin_conflict_blocked(client, dup_seed, mongo):
    login(client)
    token = get_csrf_token(client)
    cid = str(dup_seed["customer_company"]["_id"])
    uid2 = str(dup_seed["unit2"]["_id"])
    resp = client.post(f"/customers/{cid}/units/{uid2}/update", data={
        "vin": "1FTVIN00000000001", "unit_number": "T-2", "csrf_token": token,
    })
    assert resp.status_code == 302
    # VIN юнита-2 не изменился
    fresh = mongo[SHOP_A_DB].units.find_one({"_id": dup_seed["unit2"]["_id"]})
    assert fresh["vin"] == "1FTVIN00000000002"


def test_unit_mobile_create_duplicate_readable_error(client, dup_seed):
    """Веб-режим механика читает поле error — там должен быть человеческий текст."""
    login(client)
    token = get_csrf_token(client)
    resp = client.post("/api/mobile/units", json={
        "customer_id": str(dup_seed["customer_company"]["_id"]),
        "vin": "1FTVIN00000000001",
    }, headers={"X-CSRFToken": token})
    assert resp.status_code == 409
    data = resp.get_json()
    assert "already exists for this customer" in data["error"]
    assert data["error"] == data["message"]


# ── Близнецы: парт с тем же номером уже есть в базе ─────────────────────
# Так базу оставлял двойной клик по Save (два парта в одну секунду) и старые
# данные. Раньше правка ЛЮБОГО из близнецов отклонялась как «дубль номера».


@pytest.fixture()
def twin_seed(seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    now = _now()
    base = {"shop_id": seed["shop_a"]["_id"], "tenant_id": seed["tenant_a"]["_id"],
            "created_at": now, "updated_at": now, "do_not_track_inventory": False}
    docs = {
        "active": {"_id": ObjectId(), **base, "part_number": "TWIN-1", "description": "NOX sensor",
                   "average_cost": 319.0, "is_active": True, "in_stock": 4},
        "inactive": {"_id": ObjectId(), **base, "part_number": "TWIN-1", "description": "NOX sensor",
                     "average_cost": 319.0, "is_active": False, "in_stock": 4},
        "other": {"_id": ObjectId(), **base, "part_number": "TWIN-OTHER", "is_active": True, "in_stock": 0},
    }
    shop_db.parts.insert_many(list(docs.values()))
    yield docs
    ids = [d["_id"] for d in docs.values()]
    shop_db.parts.delete_many({"_id": {"$in": ids}})
    shop_db.inventory_movements.delete_many({"part_id": {"$in": ids}})
    shop_db.part_location_stock.delete_many({"part_id": {"$in": ids}})


def test_part_with_a_twin_can_still_be_edited(client, twin_seed, mongo):
    login(client)
    token = get_csrf_token(client)
    parts = mongo[SHOP_A_DB].parts
    active_id, inactive_id = twin_seed["active"]["_id"], twin_seed["inactive"]["_id"]

    # Активный близнец: себестоимость и описание правятся, номер тот же.
    resp = client.post(f"/parts/api/{active_id}/update", headers={"X-CSRFToken": token}, json={
        "part_number": "TWIN-1", "description": "NOX sensor v2", "average_cost": 299.99, "in_stock": 4,
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    fresh = parts.find_one({"_id": active_id})
    assert fresh["average_cost"] == 299.99 and fresh["description"] == "NOX sensor v2" and fresh["in_stock"] == 4

    # Тот же номер в другом регистре и с пробелами — не «смена номера».
    resp = client.post(f"/parts/api/{active_id}/update", headers={"X-CSRFToken": token}, json={
        "part_number": "  twin-1 ", "average_cost": 299.99, "in_stock": 4,
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)

    # Деактивированная копия тоже правится — ей можно дать другой номер.
    resp = client.post(f"/parts/api/{inactive_id}/update", headers={"X-CSRFToken": token}, json={
        "part_number": "TWIN-1-OLD", "average_cost": 319, "in_stock": 4,
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert parts.find_one({"_id": inactive_id})["part_number"] == "TWIN-1-OLD"


def test_part_number_change_to_a_taken_one_is_still_blocked(client, twin_seed, mongo):
    login(client)
    token = get_csrf_token(client)
    other_id = twin_seed["other"]["_id"]
    resp = client.post(f"/parts/api/{other_id}/update", headers={"X-CSRFToken": token},
                       json={"part_number": "twin-1", "in_stock": 0})
    assert resp.status_code == 409 and "already exists" in resp.get_json()["error"]
    assert mongo[SHOP_A_DB].parts.find_one({"_id": other_id})["part_number"] == "TWIN-OTHER"


def test_same_text_follows_the_duplicate_rules():
    from app.utils.duplicates import same_text

    assert same_text("RF-V22303390", " rf-v22303390 ") and same_text("A  B", "a b")
    assert not same_text("RF-1", "RF-10") and not same_text("A B", "AB")
    assert same_text(None, "") and not same_text(None, "x")


# ── Двойной клик: два запроса на создание одного номера одновременно ────


@pytest.fixture()
def claim_cleanup(seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    yield shop_db
    ids = [p["_id"] for p in shop_db.parts.find({"part_number": {"$regex": "^CLAIM-"}}, {"_id": 1})]
    shop_db.parts.delete_many({"_id": {"$in": ids}})
    shop_db.inventory_movements.delete_many({"part_id": {"$in": ids}})
    shop_db.part_location_stock.delete_many({"part_id": {"$in": ids}})
    shop_db.creation_claims.delete_many({})


def test_second_simultaneous_create_is_refused_while_the_first_is_running(client, seed, claim_cleanup, app):
    """Заявку держит «первый запрос» — второй не создаёт парт и говорит почему."""
    from app.utils.duplicates import CREATION_CLAIM_TTL_SECONDS, ensure_creation_claim_indexes

    shop_db = claim_cleanup
    ensure_creation_claim_indexes(shop_db)
    held = shop_db.creation_claims.insert_one({
        "shop_id": seed["shop_a"]["_id"], "kind": "part", "key": "claim-1", "created_at": _now(),
    }).inserted_id

    login(client)
    token = get_csrf_token(client)
    resp = client.post("/parts/api/create", json={"part_number": " CLAIM-1 ", "in_stock": 4, "average_cost": 10},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 409
    assert "being saved right now" in resp.get_json()["error"]

    resp = client.post("/parts/create", data={"part_number": "claim-1", "in_stock": "4", "average_cost": "10",
                                              "csrf_token": token})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/parts/")
    with client.session_transaction() as sess:
        flashes = sess.get("_flashes") or []
    assert any(cat == "info" and "being saved right now" in msg for cat, msg in flashes), flashes

    assert shop_db.parts.count_documents({"part_number": {"$regex": "^claim-1$", "$options": "i"}}) == 0
    # Проигравший чужую заявку не снимает.
    assert shop_db.creation_claims.count_documents({"_id": held}) == 1

    # Заявка снята — создание проходит и свою заявку за собой убирает.
    shop_db.creation_claims.delete_one({"_id": held})
    resp = client.post("/parts/api/create", json={"part_number": "CLAIM-1", "in_stock": 4, "average_cost": 10},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert shop_db.parts.count_documents({"part_number": "CLAIM-1"}) == 1
    assert shop_db.creation_claims.count_documents({}) == 0

    # Отказ по валидации внутри запроса заявку тоже не оставляет.
    resp = client.post("/parts/api/create", json={"part_number": "CLAIM-2", "average_cost": -1},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 400
    assert shop_db.creation_claims.count_documents({}) == 0

    # Индексы: уникальный ключ и TTL; ensure_* из extensions с ними не спорит.
    info = shop_db.creation_claims.index_information()
    assert info["uniq_creation_claims_shop_kind_key"]["unique"] is True
    assert info["ttl_creation_claims_created"]["expireAfterSeconds"] == CREATION_CLAIM_TTL_SECONDS == 120
    from app.extensions import ensure_shop_collections_indexes

    with app.app_context():
        ensure_shop_collections_indexes(shop_db)
    assert shop_db.creation_claims.index_information().keys() == info.keys()


def test_double_click_creates_one_part(app, seed, claim_cleanup):
    """Настоящая гонка: два клиента одновременно шлют один и тот же новый
    номер (форма и AJAX вперемешку). В каждом раунде — ровно один парт и
    один стартовый остаток, а не два."""
    import threading

    shop_db = claim_cleanup
    clients = []
    for _ in range(2):
        c = app.test_client()
        assert login(c).status_code == 302
        clients.append((c, get_csrf_token(c)))

    rounds = 12
    for i in range(rounds):
        number = f"CLAIM-RACE-{i}"
        barrier = threading.Barrier(2)
        results: list = [None, None]

        def fire(slot: int, use_form: bool):
            c, token = clients[slot]
            barrier.wait(timeout=10)
            if use_form:
                results[slot] = c.post("/parts/create", data={
                    "part_number": number, "in_stock": "4", "average_cost": "25", "csrf_token": token,
                }).status_code
            else:
                results[slot] = c.post("/parts/api/create", headers={"X-CSRFToken": token}, json={
                    "part_number": number, "in_stock": 4, "average_cost": 25,
                }).status_code

        threads = [threading.Thread(target=fire, args=(0, i % 2 == 0)), threading.Thread(target=fire, args=(1, i % 3 == 0))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        created = list(shop_db.parts.find({"part_number": number}))
        assert len(created) == 1, (number, results, len(created))
        assert created[0]["in_stock"] == 4
        assert shop_db.inventory_movements.count_documents({"part_id": created[0]["_id"], "type": "initial"}) == 1
    assert shop_db.creation_claims.count_documents({}) == 0
