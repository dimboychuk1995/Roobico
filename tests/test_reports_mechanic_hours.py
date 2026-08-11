"""Standard report «Mechanic Hours»: billed/tracked из WO и таймеров +
uAttend-часы за период (матч на механиков, менеджеры исключаются,
несматченные сотрудники — отдельными строками)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, login

# Имя нарочно уникальное: матчер uAttend ищет по всем users тенанта, и
# тёзка из другого тестового модуля (см. test_mechanic_mode) перехватил бы матч.
MYKOLA_ID = ObjectId()


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


def _utc(y, m, d, hh=12):
    return datetime(y, m, d, hh, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def mech_seed(seed, mongo):
    """WO с назначенными часами + таймер-лог механика в марте 2021 —
    окно вдали от данных других тестов."""
    master = mongo["roobico_test_master"]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]

    master.users.insert_one({
        "_id": MYKOLA_ID,
        "email": "mykola.klyuch@test.local",
        "first_name": "Mykola",
        "last_name": "Klyuch",
        "is_active": True,
        "tenant_id": seed["tenant_a"]["_id"],
        "shop_ids": [str(shop_a["_id"])],
        "role": "mechanic",
        "created_at": _utc(2021, 1, 1),
    })

    wo = {
        "_id": ObjectId(),
        "shop_id": shop_a["_id"],
        "is_active": True,
        "status": "in_progress",
        "work_order_date": _utc(2021, 3, 3),
        "created_at": _utc(2021, 3, 3),
        "labors": [
            {
                "labor_id": "L1",
                "labor": {
                    "description": "Brakes",
                    "hours": 4,
                    "assigned_mechanics": [
                        {"user_id": str(MYKOLA_ID), "name": "Mykola Klyuch", "percent": 100},
                    ],
                },
            }
        ],
    }
    shop_db.work_orders.insert_one(wo)

    shop_db.wo_time_logs.insert_one({
        "shop_id": shop_a["_id"],
        "work_order_id": wo["_id"],
        "labor_id": "L1",
        "user_id": str(MYKOLA_ID),
        "user_name": "Mykola Klyuch",
        "started_at": _utc(2021, 3, 4),
        "stopped_at": _utc(2021, 3, 4, 13),
        "seconds": 5400,
    })

    yield {"wo": wo}

    shop_db.work_orders.delete_one({"_id": wo["_id"]})
    shop_db.wo_time_logs.delete_many({"work_order_id": wo["_id"]})
    master.users.delete_one({"_id": MYKOLA_ID})


def _fetch(client, query=""):
    resp = client.get(f"/reports/api/standard/mechanic_hours{query}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["ok"] is True
    return data["report_data"]


def test_mechanic_hours_without_uattend(client, mech_seed):
    """Интеграции нет: ключей uAttend в ответе нет вовсе."""
    login(client)
    rd = _fetch(client, "?date_preset=custom&date_from=2021-03-01&date_to=2021-03-31")

    assert "total_uattend_hours" not in rd["summary"]
    rows = {r["mechanic_name"]: r for r in rd["rows"]}
    assert rows["Mykola Klyuch"]["total_hours"] == 4.0
    assert rows["Mykola Klyuch"]["tracked_hours"] == 1.5
    assert "uattend_hours" not in rows["Mykola Klyuch"]


def test_mechanic_hours_with_uattend(client, mech_seed, seed, mongo, monkeypatch):
    """uid 11 «Greg Manager» матчится на менеджера — исключается ещё до
    запроса; uid 22 «Mykola Klyuch» вливается в строку механика; uid 33
    «Solo Person» без аккаунта — отдельная строка."""
    import app.utils.integrations.uattend_hours as uattend_hours

    master = mongo["roobico_test_master"]
    shop_db = mongo[SHOP_A_DB]
    shop_id = mech_seed["wo"]["shop_id"]

    greg_id = ObjectId()
    master.users.insert_one({
        "_id": greg_id,
        "email": "greg.manager@test.local",
        "first_name": "Greg",
        "last_name": "Manager",
        "is_active": True,
        "tenant_id": seed["tenant_a"]["_id"],
        "role": "general_manager",
    })
    shop_db.uattend_employees.insert_many([
        {"shop_id": shop_id, "uattend_user_id": 11, "first_name": "Greg",
         "last_name": "Manager", "email": "", "is_active": True, "selected": True},
        {"shop_id": shop_id, "uattend_user_id": 22, "first_name": "Mykola",
         "last_name": "Klyuch", "email": "", "is_active": True, "selected": True},
        {"shop_id": shop_id, "uattend_user_id": 33, "first_name": "Solo",
         "last_name": "Person", "email": "", "is_active": True, "selected": True},
    ])

    def fake_load(shop_db, shop_id, date_from, date_to, exclude_uids=None):
        assert date_from == "2021-03-01"
        assert date_to == "2021-03-31"
        assert set(exclude_uids or ()) == {11}
        return {
            "connected": True,
            "by_day": {"2021-03-05": 60.0},
            "by_uid": {22: 40.0, 33: 20.0},
            "error": None,
        }

    monkeypatch.setattr(uattend_hours, "load_uattend_period_hours", fake_load)

    login(client)
    try:
        rd = _fetch(client, "?date_preset=custom&date_from=2021-03-01&date_to=2021-03-31")
    finally:
        shop_db.uattend_match_cache.delete_many({"shop_id": shop_id})
        shop_db.uattend_employees.delete_many({"shop_id": shop_id})
        master.users.delete_one({"_id": greg_id})

    assert rd["summary"]["total_uattend_hours"] == 60.0

    rows = {r["mechanic_name"]: r for r in rd["rows"]}
    # Часы заматченного сотрудника — в строке механика, billed/tracked целы
    assert rows["Mykola Klyuch"]["uattend_hours"] == 40.0
    assert rows["Mykola Klyuch"]["total_hours"] == 4.0
    assert rows["Mykola Klyuch"]["tracked_hours"] == 1.5
    # Несматченный — отдельная строка без billed/tracked
    assert rows["Solo Person"]["uattend_hours"] == 20.0
    assert rows["Solo Person"]["total_hours"] == 0.0
    assert rows["Solo Person"]["tracked_hours"] == 0.0
    # Заматченный на менеджера в отчёт не попадает
    assert "Greg Manager" not in rows
