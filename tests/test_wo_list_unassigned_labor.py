"""
Список WO: бейдж «Unassigned labor» рядом со статусом, если в WO есть
строка работ без назначенного механика.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from app.blueprints.work_orders.services.listing import has_unassigned_labor
from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, login


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


def _labor(description, assigned=None, hours="1"):
    return {
        "labor_id": str(ObjectId()),
        "labor": {"description": description, "hours": hours, "assigned_mechanics": assigned or []},
        "parts": [],
    }


def _wo(seed, number, labors, status="open"):
    now = datetime.now(timezone.utc)
    return {
        "_id": ObjectId(), "shop_id": seed["shop_a"]["_id"], "wo_number": number, "status": status,
        "is_active": True, "labors": labors, "work_order_date": now, "created_at": now, "updated_at": now,
        "totals": {"labor_total": 0, "parts_total": 0, "sales_tax_total": 0, "grand_total": 0},
    }


def test_has_unassigned_labor_rules():
    mech = [{"user_id": ObjectId(), "name": "Mech One", "percent": 100}]
    assert has_unassigned_labor({"labors": [_labor("Brakes")]}) is True
    assert has_unassigned_labor({"labors": [_labor("Brakes", mech), _labor("Oil")]}) is True
    assert has_unassigned_labor({"labors": [_labor("Brakes", mech)]}) is False
    # Пустая заготовка строки (ни описания, ни часов, ни запчастей) — не работа.
    assert has_unassigned_labor({"labors": [_labor("", hours="0")]}) is False
    assert has_unassigned_labor({"labors": []}) is False
    # Строка с затреканным временем — механик определяется таймером.
    tracked = _labor("Brakes")
    assert has_unassigned_labor({"labors": [tracked]}, {tracked["labor_id"]}) is False
    assert has_unassigned_labor({"labors": [tracked, _labor("Oil")]}, {tracked["labor_id"]}) is True


def test_work_orders_list_shows_unassigned_labor_badge(client, seed, mongo):
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    mech = [{"user_id": ObjectId(), "name": "Mech One", "percent": 100}]
    flagged = _wo(seed, 97001, [_labor("Brakes", mech), _labor("Oil change")])
    clean = _wo(seed, 97002, [_labor("Brakes", mech)])
    paid = _wo(seed, 97003, [_labor("Brakes")], status="paid")
    # Строка без assigned_mechanics, но с таймером механика (идёт прямо сейчас,
    # синхронизация назначений ещё не было) — механик есть, бейджа нет.
    timed = _wo(seed, 97004, [_labor("Brakes", mech), _labor("Air suspension")])
    shop_db.work_orders.insert_many([flagged, clean, paid, timed])
    now = datetime.now(timezone.utc)
    shop_db.wo_time_logs.insert_one({
        "shop_id": seed["shop_a"]["_id"], "work_order_id": timed["_id"], "wo_number": 97004,
        "labor_id": timed["labors"][1]["labor_id"], "user_id": mech[0]["user_id"], "user_name": "Mech One",
        "started_at": now, "stopped_at": None, "seconds": 0, "created_at": now, "updated_at": now,
    })
    try:
        html = client.get("/work_orders").get_data(as_text=True)
        rows = {}
        for chunk in html.split("<tr>"):
            for number in (97001, 97002, 97003, 97004):
                if f">{number}<" in chunk:
                    rows[number] = chunk
        assert set(rows) == {97001, 97002, 97003, 97004}, "all four WOs must be in the list"
        assert "Unassigned labor" in rows[97001]
        assert "Unassigned labor" not in rows[97002]
        assert "Unassigned labor" not in rows[97003]
        assert "Unassigned labor" not in rows[97004]
    finally:
        shop_db.work_orders.delete_many({"wo_number": {"$in": [97001, 97002, 97003, 97004]}})
        shop_db.wo_time_logs.delete_many({"work_order_id": timed["_id"]})
