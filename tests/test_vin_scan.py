"""
VIN-скан механика (mobile API): резолв VIN в юнит+клиента, автосоздание
юнита под системным "NEW Customer", выбор из нескольких компаний,
защита системного клиента от деактивации.
"""
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, SHOP_A_DB, get_csrf_token, login

VIN = "1FT8W3DT5KED11111"  # валидный формат: 17 символов, без I/O/Q


def _mobile_login(client):
    resp = client.post(
        "/api/mobile/login",
        json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
        environ_base={"REMOTE_ADDR": "127.0.0.9"},
    )
    assert resp.status_code == 200
    return resp.get_json()


def _resolve(client, csrf, vin):
    return client.post(
        "/api/mobile/vin/resolve",
        json={"vin": vin},
        headers={"X-CSRFToken": csrf},
    )


@pytest.fixture()
def no_vpic(monkeypatch):
    """Не ходим в NHTSA из тестов."""
    import app.blueprints.mobile_api.vin_scan as vin_scan

    monkeypatch.setattr(
        vin_scan, "_decode_vin_best_effort",
        lambda vin: {"make": "FORD", "model": "F-350", "year": "2019", "type": "TRUCK"},
    )


@pytest.fixture()
def cleanup(app, seed):
    from app.extensions import get_mongo_client

    with app.app_context():
        db = get_mongo_client()[SHOP_A_DB]
        created_customer_ids = []
        yield {"db": db, "created_customer_ids": created_customer_ids,
               "shop_id": seed["shop_a"]["_id"]}
        db.units.delete_many({"vin": VIN})
        db.customers.delete_many({"is_system": True, "shop_id": seed["shop_a"]["_id"]})
        db.customers.delete_many({"_id": {"$in": created_customer_ids}})


def test_unknown_vin_creates_unit_under_system_customer(client, cleanup, no_vpic):
    data = _mobile_login(client)
    csrf = data["csrf_token"]
    db = cleanup["db"]

    # Баркод Code 39 несёт ведущий "I" — сервер должен его срезать.
    resp = _resolve(client, csrf, "I" + VIN)
    body = resp.get_json()
    assert body["ok"] is True, body
    assert body["vin"] == VIN
    assert body["created"] is True
    assert len(body["matches"]) == 1
    match = body["matches"][0]
    assert match["customer_label"] == "NEW Customer"
    assert match["is_system_customer"] is True

    sys_customer = db.customers.find_one({"is_system": True, "shop_id": cleanup["shop_id"]})
    assert sys_customer is not None and sys_customer["is_active"] is True
    unit = db.units.find_one({"_id": ObjectId(match["unit_id"])})
    assert unit["vin"] == VIN
    assert unit["customer_id"] == sys_customer["_id"]
    assert unit["make"] == "FORD", "vPIC-декод заполняет make/model/year"

    # Повторный скан того же VIN — юнит уже есть, дублей и второго
    # системного клиента не появляется.
    body2 = _resolve(client, csrf, VIN).get_json()
    assert body2["created"] is False
    assert len(body2["matches"]) == 1
    assert body2["matches"][0]["unit_id"] == match["unit_id"]
    assert db.customers.count_documents({"is_system": True, "shop_id": cleanup["shop_id"]}) == 1
    assert db.units.count_documents({"vin": VIN}) == 1


def test_vin_on_multiple_companies_returns_choices(client, cleanup, no_vpic):
    data = _mobile_login(client)
    csrf = data["csrf_token"]
    db = cleanup["db"]
    now = datetime.now(timezone.utc)

    for name in ("Fleet Alpha", "Fleet Beta"):
        cid = ObjectId()
        cleanup["created_customer_ids"].append(cid)
        db.customers.insert_one({
            "_id": cid, "shop_id": cleanup["shop_id"], "company_name": name,
            "contacts": [], "is_active": True, "created_at": now,
        })
        db.units.insert_one({
            "_id": ObjectId(), "shop_id": cleanup["shop_id"], "customer_id": cid,
            "vin": VIN, "unit_number": name[-5:], "is_active": True, "created_at": now,
        })

    body = _resolve(client, csrf, VIN).get_json()
    assert body["ok"] is True
    assert body["created"] is False
    labels = sorted(m["customer_label"] for m in body["matches"])
    assert labels == ["Fleet Alpha", "Fleet Beta"], "юнит на двух компаниях — выбор за механиком"


def test_invalid_vin_rejected(client, cleanup):
    data = _mobile_login(client)
    body = _resolve(client, data["csrf_token"], "NOT-A-VIN").get_json()
    assert body["ok"] is False
    assert body["error"] == "vin_invalid"


def test_system_customer_cannot_be_deactivated(client, cleanup, no_vpic):
    data = _mobile_login(client)
    _resolve(client, data["csrf_token"], VIN)
    db = cleanup["db"]
    sys_customer = db.customers.find_one({"is_system": True, "shop_id": cleanup["shop_id"]})
    assert sys_customer is not None

    # Веб-логин овнера: деактивация системного клиента отклоняется.
    assert login(client).status_code == 302
    token = get_csrf_token(client)
    resp = client.post(
        f"/customers/api/{sys_customer['_id']}/deactivate",
        headers={"X-CSRFToken": token},
    )
    body = resp.get_json()
    assert body["ok"] is False
    assert "system customer" in body["error"]
    fresh = db.customers.find_one({"_id": sys_customer["_id"]})
    assert fresh["is_active"] is True
