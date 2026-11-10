"""
Мобилка: «+» у юнита в поиске механика (сборки ≤ 1.0.2) грузит карточку
клиента /api/mobile/customers/<id>. Механику (нет customers.view, есть
work_orders.create) отдаём урезанную карточку — имя и юниты, без контактов;
раньше был 403 forbidden и WO из поиска юнита создать было нельзя.
"""
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from werkzeug.security import generate_password_hash

from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, SHOP_A_DB, TENANT_A_DB

MECH_EMAIL = "brief-mech@test.local"
NOCREATE_EMAIL = "brief-viewer@test.local"
PASSWORD = "Brief-Pass-123"


def _mobile_login(client, email, password):
    return client.post(
        "/api/mobile/login",
        json={"email": email, "password": password},
        environ_base={"REMOTE_ADDR": "127.0.0.9"},
    )


@pytest.fixture(scope="module")
def brief_seed(app, seed):
    from app.constants.permissions import build_default_roles
    from app.extensions import get_master_db, get_mongo_client

    now = datetime.now(timezone.utc)
    with app.app_context():
        master = get_master_db()
        mongo = get_mongo_client()
        tdb = mongo[TENANT_A_DB]
        shop_db = mongo[SHOP_A_DB]
        shop_a = seed["shop_a"]

        mechanic_role = next(r for r in build_default_roles() if r["key"] == "mechanic")
        tdb.roles.update_one({"key": "mechanic"}, {"$set": mechanic_role}, upsert=True)
        tdb.roles.update_one(
            {"key": "brief_wo_viewer"},
            {"$set": {"key": "brief_wo_viewer", "name": "WO viewer", "permissions": ["work_orders.view"],
                      "is_system": False}},
            upsert=True,
        )

        users = []
        for email, role in ((MECH_EMAIL, "mechanic"), (NOCREATE_EMAIL, "brief_wo_viewer")):
            doc = {
                "_id": ObjectId(), "email": email, "password_hash": generate_password_hash(PASSWORD),
                "first_name": "Brief", "last_name": role, "is_active": True,
                "tenant_id": seed["tenant_a"]["_id"], "shop_ids": [str(shop_a["_id"])],
                "role": role, "created_at": now,
            }
            master.users.insert_one(doc)
            users.append(doc)

        customer = {
            "_id": ObjectId(), "shop_id": shop_a["_id"], "tenant_id": seed["tenant_a"]["_id"],
            "company_name": "Brief Fleet LLC", "address": "1 Secret St", "taxable": True,
            "contacts": [{"first_name": "Ann", "last_name": "Boss", "phone": "+1 555 000 1111",
                          "email": "ann@brief.example", "is_main": True}],
            "is_active": True, "created_at": now,
        }
        shop_db.customers.insert_one(customer)
        unit = {
            "_id": ObjectId(), "shop_id": shop_a["_id"], "customer_id": customer["_id"],
            "unit_number": "BR-7", "vin": "BRIEF000000000007", "mileage": 54321,
            "is_active": True, "created_at": now,
        }
        shop_db.units.insert_one(unit)

    yield {"customer": customer, "unit": unit}

    with app.app_context():
        master = get_master_db()
        mongo = get_mongo_client()
        master.users.delete_many({"email": {"$in": [MECH_EMAIL, NOCREATE_EMAIL]}})
        mongo[SHOP_A_DB].customers.delete_one({"_id": customer["_id"]})
        mongo[SHOP_A_DB].units.delete_one({"_id": unit["_id"]})
        mongo[TENANT_A_DB].roles.delete_one({"key": "brief_wo_viewer"})


def test_mechanic_gets_brief_customer_card(client, brief_seed):
    assert _mobile_login(client, MECH_EMAIL, PASSWORD).status_code == 200
    cid = str(brief_seed["customer"]["_id"])
    resp = client.get(f"/api/mobile/customers/{cid}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["id"] == cid
    assert data["label"] == "Brief Fleet LLC"
    assert [u["id"] for u in data["units"]] == [str(brief_seed["unit"]["_id"])]
    assert data["units"][0]["mileage"] == 54321
    # Ничего лишнего: контакты, адрес, налоговый флаг механику не отдаются.
    body = resp.get_data(as_text=True)
    assert data["contacts"] == []
    assert "ann@brief.example" not in body
    assert "1 Secret St" not in body
    assert "taxable" not in data
    assert "address" not in data


def test_owner_still_gets_full_customer_card(client, brief_seed):
    assert _mobile_login(client, OWNER_EMAIL, OWNER_PASSWORD).status_code == 200
    resp = client.get(f"/api/mobile/customers/{brief_seed['customer']['_id']}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["contacts"][0]["email"] == "ann@brief.example"
    assert data["address"] == "1 Secret St"
    assert data["taxable"] is True
    assert data["units"][0]["vin"] == "BRIEF000000000007"


def test_user_without_customers_view_and_create_is_forbidden(client, brief_seed):
    assert _mobile_login(client, NOCREATE_EMAIL, PASSWORD).status_code == 200
    resp = client.get(f"/api/mobile/customers/{brief_seed['customer']['_id']}")
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "forbidden"
