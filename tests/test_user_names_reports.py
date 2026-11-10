"""
Отчёты после переименования механика: Mechanic Hours (строки, график по id —
тёзки не сливаются, неактивный помечен), General Revenue (секция Mechanic
Hours) и бейдж «↔ Internal» в Timecard / Salary — везде ТЕКУЩЕЕ имя из
master.users, а не снимок из WO / тайм-лога / кэша матчинга uAttend.
Окно — февраль–март 2018: вдали от данных других модулей.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MASTER, TEST_MONGO_URI, login

MARTA_ID = ObjectId()
TWIN1_ID = ObjectId()
TWIN2_ID = ObjectId()
GONE_ID = ObjectId()
WINDOW = "?date_preset=custom&date_from=2018-02-01&date_to=2018-03-31"


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


def _utc(y, m, d, hh=12):
    return datetime(y, m, d, hh, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def report_seed(seed, mongo):
    """Марта переименована (в WO снимки «Marta Oldname» / «Marta Middle», id
    строкой и ObjectId; в тайм-логе — «Marta Oldname»); два разных механика с
    одинаковым именем «Tom Twin»; уволенная Gena."""
    master = mongo[TEST_MASTER]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]

    def _user(_id, email, first, last, is_active=True):
        return {
            "_id": _id, "email": email, "first_name": first, "last_name": last, "name": f"{first} {last}",
            "is_active": is_active, "tenant_id": seed["tenant_a"]["_id"], "shop_ids": [str(shop_a["_id"])],
            "role": "mechanic", "created_at": _utc(2018, 1, 1),
        }

    master.users.insert_many([
        _user(MARTA_ID, "marta.rep@test.local", "Marta", "Renamed"),
        _user(TWIN1_ID, "tom.twin1@test.local", "Tom", "Twin"),
        _user(TWIN2_ID, "tom.twin2@test.local", "Tom", "Twin"),
        _user(GONE_ID, "gena.rep@test.local", "Gena", "Gone", is_active=False),
    ])

    def _labor(labor_id, hours, user_id, snapshot):
        return {"labor_id": labor_id, "labor": {"description": labor_id, "hours": hours, "assigned_mechanics": [
            {"user_id": user_id, "name": snapshot, "percent": 100}]}}

    feb = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "is_active": True, "status": "open",
        "work_order_date": _utc(2018, 2, 10), "created_at": _utc(2018, 2, 10),
        "labors": [
            _labor("F1", 2, str(MARTA_ID), "Marta Oldname"),
            _labor("F2", 4, TWIN1_ID, "Tom Twin"),
            _labor("F3", 1, GONE_ID, "Gena Gone"),
        ],
    }
    mar = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "is_active": True, "status": "open",
        "work_order_date": _utc(2018, 3, 10), "created_at": _utc(2018, 3, 10),
        "labors": [
            _labor("M1", 3, MARTA_ID, "Marta Middle"),
            _labor("M2", 6, TWIN2_ID, "Tom Twin"),
        ],
    }
    shop_db.work_orders.insert_many([feb, mar])
    shop_db.wo_time_logs.insert_one({
        "shop_id": shop_a["_id"], "work_order_id": feb["_id"], "labor_id": "F1",
        "user_id": MARTA_ID, "user_name": "Marta Oldname",
        "started_at": _utc(2018, 2, 10), "stopped_at": _utc(2018, 2, 10, 13), "seconds": 1800,
    })

    yield {"shop": shop_a, "wo_ids": [feb["_id"], mar["_id"]]}

    shop_db.work_orders.delete_many({"_id": {"$in": [feb["_id"], mar["_id"]]}})
    shop_db.wo_time_logs.delete_many({"work_order_id": {"$in": [feb["_id"], mar["_id"]]}})
    master.users.delete_many({"_id": {"$in": [MARTA_ID, TWIN1_ID, TWIN2_ID, GONE_ID]}})


def test_mechanic_hours_report_uses_current_names_and_ids(client, report_seed):
    login(client)
    resp = client.get(f"/reports/api/standard/mechanic_hours{WINDOW}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    rd = resp.get_json()["report_data"]

    rows = {r["mechanic_id"]: r for r in rd["rows"]}
    marta = rows[str(MARTA_ID)]
    assert marta["mechanic_name"] == "Marta Renamed"
    assert marta["total_hours"] == 5.0 and marta["tracked_hours"] == 0.5 and marta["wo_count"] == 2
    assert rows[str(TWIN1_ID)]["mechanic_name"] == rows[str(TWIN2_ID)]["mechanic_name"] == "Tom Twin"
    assert rows[str(GONE_ID)]["mechanic_name"] == "Gena Gone (inactive)"
    names = {r["mechanic_name"] for r in rd["rows"]}
    assert not names & {"Marta Oldname", "Marta Middle"}

    # График: по линии на строку, данные — по id. Тёзки не сливаются
    # (раньше обе линии «Tom Twin» показывали сумму 4 + 6).
    chart = rd["chart_data"]
    assert chart["labels"] == ["2018-02", "2018-03"]
    by_label = {}
    for ds in chart["datasets"]:
        by_label.setdefault(ds["label"], []).append(ds["data"])
    assert by_label["Marta Renamed"] == [[2.0, 3.0]]
    assert sorted(by_label["Tom Twin"]) == [[0.0, 6.0], [4.0, 0.0]]
    assert by_label["Gena Gone (inactive)"] == [[1.0, 0.0]]


def test_general_revenue_mechanic_section_uses_current_names(app, report_seed, mongo):
    """Секция Mechanic Hours: строки — по user_id, подписи — живые имена
    (тенант — из сессии, как в запросе страницы отчёта)."""
    from flask import session

    import app.blueprints.reports.audit.routes as R
    from app.utils.auth import SESSION_TENANT_ID

    date_ctx = {"created_from": _utc(2018, 2, 1, 0), "created_to_exclusive": _utc(2018, 4, 1, 0)}
    with app.test_request_context("/"):
        session[SESSION_TENANT_ID] = str(report_seed["shop"]["tenant_id"])
        result = R._report_general_revenue(mongo[SHOP_A_DB], report_seed["shop"]["_id"], date_ctx)

    mech_rows = [
        (r["category"], r["amount"]) for r in result["rows"]
        if r.get("is_hours") and r["category"] != "All mechanics — total"
    ]
    assert sorted(mech_rows) == sorted([
        ("Marta Renamed", 5.0), ("Tom Twin", 4.0), ("Tom Twin", 6.0), ("Gena Gone (inactive)", 1.0),
    ])
    assert result["summary"]["total_mech_hours"] == 16.0


def test_timecard_internal_badge_uses_current_name(client, seed, mongo, monkeypatch):
    """Почасовой uAttend-сотрудник, заматченный на механика: бейдж
    «↔ Internal» — текущее имя пользователя, а не internal_name из кэша
    матчинга (кэш переживает, например, смену регистра букв)."""
    import app.utils.integrations.storage as storage
    import app.utils.integrations.uattend_client as uattend_client
    import app.utils.integrations.uattend_match as uattend_match

    master = mongo[TEST_MASTER]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    ostap_id = ObjectId()
    master.users.insert_one({
        "_id": ostap_id, "email": "ostap.badge@test.local", "first_name": "Ostap", "last_name": "Badgeman",
        "name": "Ostap Badgeman", "is_active": True, "tenant_id": seed["tenant_a"]["_id"],
        "shop_ids": [str(shop_a["_id"])], "role": "mechanic", "pay_type": "hourly",
    })
    shop_db.integrations.insert_one({"shop_id": shop_a["_id"], "provider": "uattend", "enabled": True})
    shop_db.uattend_employees.insert_one({
        "shop_id": shop_a["_id"], "uattend_user_id": 601, "first_name": "O.", "last_name": "Badgeman",
        "email": "", "is_active": True, "selected": True, "hourly_rate": 25.0,
    })

    class FakeClient:
        def __init__(self, api_key):
            pass

        def get_punches(self, date_from, date_to, user_ids=None):
            return {"Punches": [{"UserId": 601, "Hours": 7.0}]}

    def fake_match(internal_for_ai, uattend_for_ai):
        return {601: {"internal_id": str(ostap_id), "internal_name": "Stale Cache Name",
                      "internal_email": "stale@test.local", "confidence": 0.95}}

    monkeypatch.setattr(storage, "get_decrypted_api_key", lambda *a, **k: "test-key")
    monkeypatch.setattr(uattend_client, "UAttendClient", FakeClient)
    monkeypatch.setattr(uattend_match, "match_employees", fake_match)

    login(client)
    try:
        resp = client.get("/reports/timecard-salary?date_preset=custom&date_from=2018-02-05&date_to=2018-02-11")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
    finally:
        master.users.delete_one({"_id": ostap_id})
        shop_db.integrations.delete_many({"shop_id": shop_a["_id"], "provider": "uattend"})
        shop_db.uattend_employees.delete_many({"shop_id": shop_a["_id"]})
        shop_db.uattend_match_cache.delete_many({"shop_id": shop_a["_id"]})

    assert "↔ Internal: Ostap Badgeman" in html
    assert "Stale Cache Name" not in html
