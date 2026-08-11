"""Timecard / Salary: uAttend-сотрудник, заматченный на ПОЧАСОВОГО
внутреннего пользователя (механика), не должен пропадать из отчёта —
скрывается только двойник salary-пользователя (он влит в его строку)."""
from __future__ import annotations

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, login


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def timecard_seed(seed, mongo):
    """Почасовой механик + salary-менеджер, оба с двойниками в uAttend,
    плюс несматченный uAttend-сотрудник. Имена уникальные — матчер ищет
    по всем users тенанта, тёзки из других модулей перехватят матч."""
    master = mongo["roobico_test_master"]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]

    taras_id, petro_id = ObjectId(), ObjectId()
    master.users.insert_many([
        {
            "_id": taras_id,
            "email": "taras.hodynnyk@test.local",
            "first_name": "Taras",
            "last_name": "Hodynnyk",
            "is_active": True,
            "tenant_id": seed["tenant_a"]["_id"],
            "shop_ids": [str(shop_a["_id"])],
            "role": "mechanic",
            "pay_type": "hourly",
        },
        {
            "_id": petro_id,
            "email": "petro.zarplatnyk@test.local",
            "first_name": "Petro",
            "last_name": "Zarplatnyk",
            "is_active": True,
            "tenant_id": seed["tenant_a"]["_id"],
            "shop_ids": [str(shop_a["_id"])],
            "role": "general_manager",
            "pay_type": "salary",
            "salary_amount": 1000.0,
        },
    ])

    shop_db.integrations.insert_one({
        "shop_id": shop_a["_id"], "provider": "uattend", "enabled": True,
    })
    shop_db.uattend_employees.insert_many([
        {"shop_id": shop_a["_id"], "uattend_user_id": 501, "first_name": "Taras",
         "last_name": "Hodynnyk", "email": "", "is_active": True,
         "selected": True, "hourly_rate": 30.0},
        {"shop_id": shop_a["_id"], "uattend_user_id": 502, "first_name": "Petro",
         "last_name": "Zarplatnyk", "email": "", "is_active": True,
         "selected": True, "hourly_rate": None},
        {"shop_id": shop_a["_id"], "uattend_user_id": 503, "first_name": "Free",
         "last_name": "Puncher", "email": "", "is_active": True,
         "selected": True, "hourly_rate": 20.0},
    ])

    yield {"shop_id": shop_a["_id"]}

    master.users.delete_many({"_id": {"$in": [taras_id, petro_id]}})
    shop_db.integrations.delete_many({"shop_id": shop_a["_id"], "provider": "uattend"})
    shop_db.uattend_employees.delete_many({"shop_id": shop_a["_id"]})
    shop_db.uattend_match_cache.delete_many({"shop_id": shop_a["_id"]})


def test_matched_hourly_user_stays_in_report(client, timecard_seed, monkeypatch):
    import app.utils.integrations.storage as storage
    import app.utils.integrations.uattend_client as uattend_client

    monkeypatch.setattr(storage, "get_decrypted_api_key", lambda *a, **k: "test-key")

    class FakeClient:
        def __init__(self, api_key):
            pass

        def get_punches(self, date_from, date_to, user_ids=None):
            assert set(user_ids or ()) == {501, 502, 503}
            return {"Punches": [
                {"UserId": 501, "Hours": 9.5},
                {"UserId": 502, "Hours": 8.0},
                {"UserId": 503, "Hours": 4.0},
            ]}

    monkeypatch.setattr(uattend_client, "UAttendClient", FakeClient)

    login(client)
    resp = client.get(
        "/reports/timecard-salary"
        "?date_preset=custom&date_from=2021-04-05&date_to=2021-04-11"
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Механик с матчем в uAttend — в отчёте, со своими часами и ролью
    assert "Taras Hodynnyk" in html
    assert "9.50" in html
    # Несматченный uAttend-сотрудник — как раньше
    assert "Free Puncher" in html
    # Salary-пользователь один: его uAttend-двойник влит в строку,
    # отдельной строки «uAttend employee» для него нет
    assert html.count("Petro Zarplatnyk") >= 1
    assert html.count("uAttend employee") == 1  # только Free Puncher
    # Итог по почасовым: 9.5×30 + 4×20 = 365
    assert "365.00" in html
