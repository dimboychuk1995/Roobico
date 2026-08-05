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


def test_part_create_inactive_duplicate_mentions_deactivated(client, dup_seed):
    login(client)
    token = get_csrf_token(client)
    resp = client.post("/parts/api/create", json={"part_number": "dup-gone"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 409
    err = resp.get_json()["error"]
    assert "deactivated" in err and "Reactivate" in err


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
