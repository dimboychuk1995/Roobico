"""
Дашборд: график часов Actual / Invoiced / uAttend (блок mechanic-hours)
и парсеры ответа uAttend /reports/punch.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, login

ALICE_ID = ObjectId()
BOB_ID = ObjectId()


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


def _utc(y, m, d, hh=12):
    # 12:00 UTC = утро по таймзоне магазина (America/Chicago) — середина
    # локального дня, никаких сюрпризов на границах суток.
    return datetime(y, m, d, hh, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def hours_seed(seed, mongo):
    """WO с labor-часами + завершённые таймер-логи в марте 2020 —
    далёкое окно, чтобы данные других тестов (created_at = сейчас) не попадали.

    Alice и Bob — механики в master.users: в Actual идут только таймеры
    пользователей с ролью механика."""
    master = mongo["roobico_test_master"]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]

    mechanics = [
        {
            "_id": ALICE_ID,
            "email": "alice.mech@test.local",
            "first_name": "Alice",
            "last_name": "Wrench",
            "is_active": True,
            "tenant_id": seed["tenant_a"]["_id"],
            "shop_ids": [str(shop_a["_id"])],
            "role": "mechanic",
            "created_at": _utc(2020, 1, 1),
        },
        {
            "_id": BOB_ID,
            "email": "bob.mech@test.local",
            "first_name": "Bob",
            "last_name": "Spanner",
            "is_active": True,
            "tenant_id": seed["tenant_a"]["_id"],
            "shop_ids": [str(shop_a["_id"])],
            "role": "senior_mechanic",
            "created_at": _utc(2020, 1, 1),
        },
    ]
    master.users.insert_many(mechanics)

    wo = {
        "_id": ObjectId(),
        "shop_id": shop_a["_id"],
        "is_active": True,
        "status": "in_progress",
        "work_order_date": _utc(2020, 3, 3),
        "created_at": _utc(2020, 3, 3),
        "labors": [
            {
                "labor_id": "L1",
                "labor": {
                    "description": "Brakes",
                    "hours": 5,
                    "assigned_mechanics": [
                        {"user_id": str(ALICE_ID), "name": "Alice", "percent": 50},
                        {"user_id": str(BOB_ID), "name": "Bob", "percent": 25},
                    ],
                },
            }
        ],
    }
    shop_db.work_orders.insert_one(wo)

    logs = [
        {
            "shop_id": shop_a["_id"],
            "work_order_id": wo["_id"],
            "labor_id": "L1",
            "user_id": str(ALICE_ID),
            "user_name": "Alice",
            "started_at": _utc(2020, 3, 4),
            "stopped_at": _utc(2020, 3, 4, 13),
            "seconds": 3600,
        },
        {
            "shop_id": shop_a["_id"],
            "work_order_id": wo["_id"],
            "labor_id": "L1",
            "user_id": str(BOB_ID),
            "user_name": "Bob",
            "started_at": _utc(2020, 3, 4),
            "stopped_at": _utc(2020, 3, 4, 13),
            "seconds": 1800,
        },
        # Не механик (нет в master.users с ролью механика) — не должен
        # попадать ни в Actual, ни в строки summary.
        {
            "shop_id": shop_a["_id"],
            "work_order_id": wo["_id"],
            "labor_id": "L1",
            "user_id": str(ObjectId()),
            "user_name": "Manager Mike",
            "started_at": _utc(2020, 3, 4),
            "stopped_at": _utc(2020, 3, 4, 13),
            "seconds": 7200,
        },
    ]
    shop_db.wo_time_logs.insert_many(logs)

    yield {"wo": wo}

    shop_db.work_orders.delete_one({"_id": wo["_id"]})
    shop_db.wo_time_logs.delete_many({"work_order_id": wo["_id"]})
    master.users.delete_many({"_id": {"$in": [ALICE_ID, BOB_ID]}})


def _fetch_block(client, query=""):
    resp = client.get(f"/dashboard/api/metrics/mechanic-hours{query}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["ok"] is True
    return data["data"]


def test_hours_chart_actual_and_invoiced_series(client, hours_seed):
    login(client)
    data = _fetch_block(
        client, "?date_preset=custom&date_from=2020-03-02&date_to=2020-03-08"
    )

    chart = data["hours_chart"]
    assert chart["bucket"] == "day"
    assert chart["labels"][0] == "2020-03-02"
    assert len(chart["labels"]) == 7

    invoiced_idx = chart["labels"].index("2020-03-03")
    actual_idx = chart["labels"].index("2020-03-04")
    assert chart["invoiced"][invoiced_idx] == 5.0
    # Таймер "Manager Mike" (не механик) не считается: 1.0 + 0.5, без его 2.0
    assert chart["actual"][actual_idx] == 1.5
    # uAttend не подключён: линии нет, флаг False
    assert chart["uattend"] is None
    assert chart["uattend_connected"] is False

    summary = chart["summary"]
    assert summary["invoiced_total"] == 5.0
    assert summary["actual_total"] == 1.5
    assert summary["uattend_total"] is None
    assert summary["efficiency_percent"] == 333.33
    assert summary["utilization_percent"] is None

    rows = {r["name"]: r for r in chart["rows"]}
    # Имя из списка механиков магазина (master.users), не из WO
    assert rows["Alice Wrench"]["actual"] == 1.0
    assert rows["Alice Wrench"]["invoiced"] == 2.5
    assert rows["Bob Spanner"]["actual"] == 0.5
    assert rows["Bob Spanner"]["invoiced"] == 1.25
    assert "Manager Mike" not in rows
    # 25% часов никому не назначены — отдельная строка, чтобы цифры сходились
    assert rows["Unassigned labor"]["invoiced"] == 1.25

    # Легаси-набор (назначенные часы) остаётся в ответе
    legacy = {r["name"]: r["hours"] for r in data["mechanic_hours_rows"]}
    assert legacy == {"Alice": 2.5, "Bob": 1.25}


def test_hours_chart_uattend_series_when_connected(client, hours_seed, mongo, monkeypatch):
    import app.utils.integrations.uattend_hours as uattend_hours

    # Матч-кэш: uid 88 — менеджер (не механик, должен быть исключён),
    # uid 99 — Alice (механик, часы вливаются в её строку).
    shop_db = mongo[SHOP_A_DB]
    manager_id = ObjectId()
    cache_doc_id = shop_db.uattend_match_cache.insert_one({
        "shop_id": hours_seed["wo"]["shop_id"],
        "key": "test-key",
        "matches": {
            "88": {"internal_id": str(manager_id), "internal_name": "Greg Manager"},
            "99": {"internal_id": str(ALICE_ID), "internal_name": "Alice Wrench"},
        },
    }).inserted_id

    def fake_load(shop_db, shop_id, date_from, date_to, exclude_uids=None):
        assert date_from == "2020-03-02"
        assert date_to == "2020-03-08"
        # Заматченный на менеджера uid исключается ещё до запроса к API
        assert set(exclude_uids or ()) == {88}
        return {
            "connected": True,
            "by_day": {"2020-03-05": 8.0},
            "by_uid": {77: 5.0, 99: 3.0},
            "error": None,
        }

    monkeypatch.setattr(uattend_hours, "load_uattend_period_hours", fake_load)

    login(client)
    try:
        data = _fetch_block(
            client, "?date_preset=custom&date_from=2020-03-02&date_to=2020-03-08"
        )
    finally:
        shop_db.uattend_match_cache.delete_one({"_id": cache_doc_id})

    chart = data["hours_chart"]
    assert chart["uattend_connected"] is True
    uattend_idx = chart["labels"].index("2020-03-05")
    assert chart["uattend"][uattend_idx] == 8.0

    summary = chart["summary"]
    assert summary["uattend_total"] == 8.0
    assert summary["utilization_percent"] == 18.75  # 1.5 / 8.0

    rows = {r["name"]: r for r in chart["rows"]}
    # Незаматченный сотрудник uAttend — отдельной строкой
    assert rows["uAttend #77"]["uattend"] == 5.0
    assert rows["uAttend #77"]["actual"] is None
    # Заматченный на механика — часы в строке механика
    assert rows["Alice Wrench"]["uattend"] == 3.0
    assert rows["Alice Wrench"]["actual"] == 1.0
    assert "Greg Manager" not in rows


def test_hours_chart_daily_buckets_for_long_ranges(client, hours_seed):
    """Шаг графика всегда один день, даже на полугодовом диапазоне."""
    login(client)
    data = _fetch_block(
        client, "?date_preset=custom&date_from=2020-01-01&date_to=2020-06-30"
    )

    chart = data["hours_chart"]
    assert chart["bucket"] == "day"
    assert chart["labels"][0] == "2020-01-01"
    assert chart["labels"][-1] == "2020-06-30"
    assert len(chart["labels"]) == 182  # 2020 — високосный
    assert chart["invoiced"][chart["labels"].index("2020-03-03")] == 5.0
    assert chart["actual"][chart["labels"].index("2020-03-04")] == 1.5


# ── Парсеры ответа uAttend ──────────────────────────────────────────────


def test_aggregate_hours_by_day_line_items():
    from app.utils.integrations.uattend_hours import aggregate_hours_by_day

    report = {
        "Body": {
            "PunchReportLineItems": [
                {"UserId": 1, "Tot": "8:30", "Date": "03/05/2020"},
                {"UserId": 2, "Tot": 2, "Date": "2020-03-06"},
                {"UserId": 3, "Tot": "0:00", "Date": "03/05/2020"},
            ]
        }
    }
    assert aggregate_hours_by_day(report) == {"2020-03-05": 8.5, "2020-03-06": 2.0}


def test_aggregate_hours_by_day_punch_pairs():
    from app.utils.integrations.uattend_hours import aggregate_hours_by_day

    report = {
        "Punches": [
            {
                "UserId": 3,
                "PunchIn": "2020-03-07T08:00:00Z",
                "PunchOut": "2020-03-07T12:00:00Z",
            }
        ]
    }
    assert aggregate_hours_by_day(report) == {"2020-03-07": 4.0}


def test_aggregate_hours_by_user_line_items():
    from app.utils.integrations.uattend_hours import aggregate_hours_by_user

    report = {
        "Body": {
            "PunchReportLineItems": [
                {"UserId": 1, "Tot": "8:30", "Date": "03/05/2020"},
                {"UserId": 1, "Tot": "1:30", "Date": "03/06/2020"},
                {"UserId": 2, "Tot": 2, "Date": "2020-03-06"},
            ]
        }
    }
    assert aggregate_hours_by_user(report) == {1: 10.0, 2: 2.0}
