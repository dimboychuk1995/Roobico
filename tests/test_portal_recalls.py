"""Рекаллы NHTSA в клиентском портале.

Портальный эндпоинт read-only: не трогает снапшоты recalls_seen (модалка
сотрудников) и recalls_notified (ночная рассылка). NEW — кампании, о которых
клиента ещё не уведомляли по email.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from tests.conftest import SHOP_A_DB, TEST_MONGO_URI

from pymongo import MongoClient


CAMPAIGN_A = "19V066000"
CAMPAIGN_B = "25V999000"


def _recall_row(campaign, component="AIR BAGS"):
    return {
        "NHTSACampaignNumber": campaign,
        "ReportReceivedDate": "07/02/2019",
        "Component": component,
        "Summary": "Summary text.",
        "Consequence": "Consequence text.",
        "Remedy": "Remedy text.",
    }


@pytest.fixture()
def portal_env(app, seed, monkeypatch):
    """Клиент с юнитом + портальный токен + подменённая NHTSA."""
    from app.blueprints.customer_portal.routes import get_or_create_portal_token
    from app.blueprints.work_orders import recalls_api

    mongo = MongoClient(TEST_MONGO_URI)
    db = mongo[SHOP_A_DB]
    shop = seed["shop_a"]
    now = datetime.now(timezone.utc)

    customer = {
        "_id": ObjectId(), "shop_id": shop["_id"], "company_name": "Portal Fleet",
        "contacts": [], "is_active": True, "created_at": now,
    }
    db.customers.insert_one(customer)

    unit = {
        "_id": ObjectId(), "shop_id": shop["_id"], "customer_id": customer["_id"],
        "unit_number": "T-1", "make": "FREIGHTLINER", "model": "CASCADIA",
        "year": 2020, "vin": "VIN0001", "is_active": True, "created_at": now,
    }
    db.units.insert_one(unit)

    state = {"rows": [_recall_row(CAMPAIGN_A)]}
    monkeypatch.setattr(recalls_api, "_fetch_model_catalog", lambda make, year: [])
    monkeypatch.setattr(
        recalls_api, "_fetch_recalls",
        lambda make, model, year: {"Count": len(state["rows"]), "results": state["rows"]},
    )

    with app.app_context():
        token = get_or_create_portal_token(shop, customer["_id"])["token"]

    yield {"db": db, "customer": customer, "unit": unit, "token": token, "state": state}

    db.customers.delete_one({"_id": customer["_id"]})
    db.units.delete_one({"_id": unit["_id"]})
    from app.extensions import get_master_db
    with app.app_context():
        get_master_db().customer_portal_tokens.delete_many({"customer_id": customer["_id"]})
    mongo.close()


def _recalls_url(env, unit_id=None):
    return f"/portal/{env['token']}/units/{unit_id or env['unit']['_id']}/recalls"


def test_portal_recalls_returns_list_without_touching_snapshots(client, portal_env):
    resp = client.get(_recalls_url(portal_env))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["count"] == 1
    assert data["recalls"][0]["campaign_number"] == CAMPAIGN_A
    assert data["recalls"][0]["is_new"] is False  # уведомлений ещё не было — не пугаем
    assert data["checked_note"]

    # Read-only: снапшоты магазина и рассылки не появились.
    doc = portal_env["db"].units.find_one({"_id": portal_env["unit"]["_id"]})
    assert "recalls_seen" not in doc
    assert "recalls_notified" not in doc


def test_portal_new_badge_relative_to_notified_snapshot(client, portal_env):
    portal_env["db"].units.update_one(
        {"_id": portal_env["unit"]["_id"]},
        {"$set": {"recalls_notified": {
            "campaigns": [CAMPAIGN_A],
            "checked_at": datetime.now(timezone.utc),
        }}},
    )
    portal_env["state"]["rows"] = [_recall_row(CAMPAIGN_A), _recall_row(CAMPAIGN_B, "BRAKES")]

    data = client.get(_recalls_url(portal_env)).get_json()
    assert data["ok"] is True
    flags = {r["campaign_number"]: r["is_new"] for r in data["recalls"]}
    assert flags == {CAMPAIGN_A: False, CAMPAIGN_B: True}
    assert data["new_count"] == 1

    # Снапшот рассылки не изменился — ночной джоб всё равно отправит письмо.
    doc = portal_env["db"].units.find_one({"_id": portal_env["unit"]["_id"]})
    assert doc["recalls_notified"]["campaigns"] == [CAMPAIGN_A]


def test_portal_rejects_foreign_unit(client, portal_env, seed):
    foreign_unit = {
        "_id": ObjectId(), "shop_id": seed["shop_a"]["_id"], "customer_id": ObjectId(),
        "unit_number": "X-9", "make": "VOLVO", "model": "VNL", "year": 2021,
        "is_active": True, "created_at": datetime.now(timezone.utc),
    }
    portal_env["db"].units.insert_one(foreign_unit)
    try:
        resp = client.get(_recalls_url(portal_env, unit_id=foreign_unit["_id"]))
        assert resp.status_code == 404
    finally:
        portal_env["db"].units.delete_one({"_id": foreign_unit["_id"]})


def test_portal_rejects_bad_token(client, portal_env):
    resp = client.get(f"/portal/{'x' * 43}/units/{portal_env['unit']['_id']}/recalls")
    assert resp.status_code == 404


def test_portal_recalls_rate_limited(client, portal_env):
    url = _recalls_url(portal_env)
    for _ in range(30):
        assert client.get(url).status_code == 200
    resp = client.get(url)
    assert resp.status_code == 429
    assert resp.get_json()["ok"] is False


def test_units_tab_has_recalls_button(client, portal_env):
    resp = client.get(f"/portal/{portal_env['token']}/tab/units")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "js-open-recalls-modal" in html
    assert f"/units/{portal_env['unit']['_id']}/recalls" in html


def test_dashboard_has_recalls_modal_and_script(client, portal_env):
    resp = client.get(f"/portal/{portal_env['token']}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "unitRecallsModal" in html
    assert "unit_recalls.js" in html


def test_work_order_page_has_recalls_button(client, portal_env):
    now = datetime.now(timezone.utc)
    wo = {
        "_id": ObjectId(),
        "shop_id": portal_env["unit"]["shop_id"],
        "customer_id": portal_env["customer"]["_id"],
        "unit_id": portal_env["unit"]["_id"],
        "is_active": True,
        "status": "open",
        "wo_number": 9301,
        "labors": [],
        "totals": {"grand_total": 100.0},
        "work_order_date": (now - timedelta(days=1)).replace(tzinfo=None),
        "created_at": now,
    }
    portal_env["db"].work_orders.insert_one(wo)
    try:
        resp = client.get(f"/portal/{portal_env['token']}/work-orders/{wo['_id']}")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "js-open-recalls-modal" in html
        assert "unitRecallsModal" in html
        assert f"/units/{portal_env['unit']['_id']}/recalls" in html
    finally:
        portal_env["db"].work_orders.delete_one({"_id": wo["_id"]})
