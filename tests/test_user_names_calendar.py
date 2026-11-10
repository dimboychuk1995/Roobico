"""
Календарь после переименования механика (по образцу
test_calendar_api.test_labels_come_from_db_and_follow_renames для клиентов):
бригада записи, legacy mechanic_id/mechanic_name, снимок WO для новой записи
и карточка «Scheduled» на WO — живые имена из master.users; сохранённый
снимок — запас для удалённых; документы в базе на чтении не меняются; PUT
с той же бригадой не пишет в журнал фантомное «Mechanic: старое → новое».
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MASTER, TEST_MONGO_URI, get_csrf_token, login

WO_NUMBER = 98201  # 98xxx — номера модулей test_user_names_*
DAY = "2032-03-03"  # своя неделя, вдали от недель других модулей
RANGE = "start=2032-03-01T00:00:00&end=2032-03-08T00:00:00"
CAL_ID = ObjectId()
CAL2_ID = ObjectId()
GHOST_ID = ObjectId()


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


def _at(hour: int) -> datetime:
    return datetime.fromisoformat(f"{DAY}T{hour:02d}:00:00")


@pytest.fixture()
def cal_seed(seed, mongo):
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
        _user(CAL_ID, "cal.names@test.local", "Cal", "Current"),
        _user(CAL2_ID, "cal2.names@test.local", "Cal", "Second"),
    ])
    wo = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "wo_number": WO_NUMBER, "status": "open", "is_active": True,
        "labors": [{"labor_id": "C1", "labor": {"description": "Brakes", "hours": "2", "assigned_mechanics": [
            {"user_id": CAL_ID, "name": "Cal Oldname", "role": "mechanic", "percent": 100.0}]}, "parts": []}],
        "totals": {"grand_total": 0, "labors": []}, "work_order_date": now, "created_at": now, "updated_at": now,
    }
    shop_db.work_orders.insert_one(wo)

    base = {
        "shop_id": shop_a["_id"], "tenant_id": seed["tenant_a"]["_id"], "status": "scheduled",
        "customer_id": None, "customer_label": "", "unit_id": None, "unit_label": "", "presets": [],
        "work_order_id": None, "wo_number": None, "created_at": now, "updated_at": now,
    }
    crew = {
        **base, "_id": ObjectId(), "title": "Crew", "start_time": _at(8), "end_time": _at(9),
        "mechanics": [{"id": CAL_ID, "name": "Cal Oldname"}, {"id": CAL2_ID, "name": "Cal Second"}],
        "mechanic_ids": [CAL_ID, CAL2_ID], "mechanic_id": CAL_ID, "mechanic_name": "Cal Oldname",
        "work_order_id": wo["_id"], "wo_number": WO_NUMBER,
    }
    legacy = {
        **base, "_id": ObjectId(), "title": "Legacy", "start_time": _at(10), "end_time": _at(11),
        "mechanic_id": CAL_ID, "mechanic_name": "Cal Oldname",
    }
    ghost = {
        **base, "_id": ObjectId(), "title": "Ghost", "start_time": _at(12), "end_time": _at(13),
        "mechanics": [{"id": GHOST_ID, "name": "Ghost Snap"}], "mechanic_ids": [GHOST_ID],
        "mechanic_id": GHOST_ID, "mechanic_name": "Ghost Snap",
    }
    shop_db.calendar_events.insert_many([crew, legacy, ghost])

    yield {"shop": shop_a, "wo": wo, "crew": crew, "legacy": legacy, "ghost": ghost}

    event_ids = [crew["_id"], legacy["_id"], ghost["_id"]]
    shop_db.calendar_events.delete_many({"_id": {"$in": event_ids}})
    shop_db.work_orders.delete_one({"_id": wo["_id"]})
    master.users.delete_many({"_id": {"$in": [CAL_ID, CAL2_ID]}})
    master.audit_journal.delete_many({"entity_type": "calendar_event", "entity_id": {"$in": [str(i) for i in event_ids]}})


def _events(client) -> dict:
    resp = client.get(f"/calendar/api/events?{RANGE}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return {r["id"]: r for r in resp.get_json()}


def test_calendar_crew_names_follow_renames(app, client, cal_seed, mongo):
    from app.blueprints.calendar.services.work_orders import linked_appointments_for_wo

    login(client)
    shop_db = mongo[SHOP_A_DB]
    crew_id, legacy_id, ghost_id = (str(cal_seed[k]["_id"]) for k in ("crew", "legacy", "ghost"))

    rows = _events(client)
    assert rows[crew_id]["mechanics"] == [
        {"id": str(CAL_ID), "name": "Cal Current"}, {"id": str(CAL2_ID), "name": "Cal Second"},
    ]
    assert rows[crew_id]["mechanic_name"] == "Cal Current, Cal Second"
    assert rows[legacy_id]["mechanics"] == [{"id": str(CAL_ID), "name": "Cal Current"}]
    assert rows[legacy_id]["mechanic_name"] == "Cal Current"
    # Удалённый пользователь — сохранённый снимок.
    assert rows[ghost_id]["mechanics"] == [{"id": str(GHOST_ID), "name": "Ghost Snap"}]
    # На чтении база не меняется.
    assert shop_db.calendar_events.find_one({"_id": cal_seed["crew"]["_id"]})["mechanic_name"] == "Cal Oldname"

    # Снимок WO для новой записи («Schedule» со страницы WO).
    resp = client.get(f"/calendar/api/work_orders/{cal_seed['wo']['_id']}/snapshot")
    assert resp.status_code == 200
    assert resp.get_json()["mechanics"] == [{"id": str(CAL_ID), "name": "Cal Current"}]

    # Карточка «Scheduled» на WO (офис, механик, мобилка) — и вне запроса:
    # тенант берётся из документа магазина.
    with app.app_context():
        linked = linked_appointments_for_wo(shop_db, cal_seed["shop"], cal_seed["wo"]["_id"])
    assert [a["id"] for a in linked] == [crew_id]
    assert [m["name"] for m in linked[0]["mechanics"]] == ["Cal Current", "Cal Second"]

    # Ещё одно переименование видно сразу.
    mongo[TEST_MASTER].users.update_one({"_id": CAL_ID}, {"$set": {"first_name": "Calvin", "name": "Calvin Current"}})
    assert _events(client)[legacy_id]["mechanic_name"] == "Calvin Current"


def test_calendar_put_with_same_crew_logs_no_phantom_mechanic_change(client, cal_seed, mongo):
    login(client)
    eid = cal_seed["crew"]["_id"]
    resp = client.put(
        f"/calendar/api/events/{eid}",
        json={"title": "Crew, new note", "mechanic_ids": [str(CAL_ID), str(CAL2_ID)]},
        headers={"X-CSRFToken": get_csrf_token(client)},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["mechanic_name"] == "Cal Current, Cal Second"

    entry = mongo[TEST_MASTER].audit_journal.find_one(
        {"entity_type": "calendar_event", "entity_id": str(eid), "action": "update"}, sort=[("created_at", -1)],
    )
    assert entry is not None
    fields = {c["field"] for c in entry["changes"]}
    assert "title" in fields and "mechanic_name" not in fields
    # Сохранение переписало снимок живым именем (build_mechanics).
    saved = mongo[SHOP_A_DB].calendar_events.find_one({"_id": eid})
    assert saved["mechanic_name"] == "Cal Current"
    assert saved["mechanics"][0] == {"id": CAL_ID, "name": "Cal Current"}
