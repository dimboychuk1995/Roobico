"""
Список WO после переименования механика: назначенные, «работают сейчас»
(открытый таймер) и отметки Done — живые имена по user_id, один человек —
одна подпись, даже если его снимки в WO / тайм-логе / отметке разные.
Контракт списков строк для мобильного приложения не меняется.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MASTER, TEST_MONGO_URI, login

WO_NUMBER = 98101  # 98xxx — номера модулей test_user_names_*
LEO_ID = ObjectId()
DINA_ID = ObjectId()


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def list_seed(seed, mongo):
    master = mongo[TEST_MASTER]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = datetime.now(timezone.utc)

    def _user(_id, email, first, last):
        return {
            "_id": _id, "email": email, "first_name": first, "last_name": last, "name": f"{first} {last}",
            "is_active": True, "tenant_id": seed["tenant_a"]["_id"], "shop_ids": [str(shop_a["_id"])],
            "role": "mechanic", "created_at": now,
        }

    master.users.insert_many([
        _user(LEO_ID, "leo.list@test.local", "Leo", "Current"),
        _user(DINA_ID, "dina.list@test.local", "Dina", "Current"),
    ])

    def _labor(labor_id, assigned):
        return {"labor_id": labor_id, "labor": {"description": f"Job {labor_id}", "hours": "1",
                                                "assigned_mechanics": assigned}, "parts": []}

    wo = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "wo_number": WO_NUMBER, "status": "in_progress",
        "is_active": True, "mechanic_done": False, "work_order_date": now, "created_at": now, "updated_at": now,
        "totals": {"labor_total": 0, "parts_total": 0, "sales_tax_total": 0, "grand_total": 0},
        "labors": [
            _labor("L1", [{"user_id": str(LEO_ID), "name": "Leo Oldname", "percent": 100}]),
            _labor("L2", [{"user_id": LEO_ID, "name": "Leo Middle", "percent": 100}]),
            _labor("L3", [{"user_id": DINA_ID, "name": "Dina Old", "percent": 100}]),
            _labor("L4", [{"name": "Legacy Larry", "percent": 100}]),
        ],
        "mechanic_done_marks": [{"user_id": DINA_ID, "name": "Dina Old", "at": now}],
    }
    shop_db.work_orders.insert_one(wo)
    shop_db.wo_time_logs.insert_one({
        "shop_id": shop_a["_id"], "work_order_id": wo["_id"], "wo_number": WO_NUMBER, "labor_id": "L1",
        "user_id": LEO_ID, "user_name": "Leo Oldname", "started_at": now, "stopped_at": None, "seconds": None,
        "created_at": now, "updated_at": now,
    })

    yield wo

    shop_db.work_orders.delete_one({"_id": wo["_id"]})
    shop_db.wo_time_logs.delete_many({"work_order_id": wo["_id"]})
    master.users.delete_many({"_id": {"$in": [LEO_ID, DINA_ID]}})


def test_list_names_follow_current_user_names(client, list_seed):
    login(client)
    resp = client.get(f"/api/mobile/work_orders?paid_status=in_progress&q={WO_NUMBER}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    item = next(i for i in resp.get_json()["items"] if i["id"] == str(list_seed["_id"]))

    # Leo — одна подпись на три разных снимка (id строкой и ObjectId);
    # легаси-назначение без id — по снимку.
    assert item["mechanics"] == ["Leo Current", "Dina Current", "Legacy Larry"]
    assert item["working_now"] == ["Leo Current"]
    assert item["mechanics_done"] == ["Dina Current"]

    # Офисный список (группа In Work): те же подписи, старых имён нет.
    html = client.get("/work_orders").get_data(as_text=True)
    assert "&#9679; Leo Current" in html and "&#10003; Dina Current" in html
    for stale in ("Leo Oldname", "Leo Middle", "Dina Old"):
        assert stale not in html


def test_list_falls_back_to_snapshots_for_deleted_users(client, list_seed, mongo):
    mongo[TEST_MASTER].users.delete_many({"_id": {"$in": [LEO_ID, DINA_ID]}})
    login(client)
    resp = client.get(f"/api/mobile/work_orders?paid_status=in_progress&q={WO_NUMBER}")
    item = next(i for i in resp.get_json()["items"] if i["id"] == str(list_seed["_id"]))
    # Пользователей нет — снимки; один id — всё равно одна подпись (первый снимок).
    assert item["mechanics"] == ["Leo Oldname", "Dina Old", "Legacy Larry"]
    assert item["working_now"] == ["Leo Oldname"]
    assert item["mechanics_done"] == ["Dina Old"]
