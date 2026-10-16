"""
Календарь ↔ work orders: WO попадают в календарь сами (по дате WO или по
дням тайм-логов механиков), привязка записи к WO при создании WO из
календаря, drag WO на сетку = запись с привязкой, механик-режим без paid.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import SHOP_A_DB, TENANT_A_DB, TEST_MONGO_URI, get_csrf_token, login

MECHANIC_EMAIL = "cal-mech@test.local"
MECHANIC_PASSWORD = "password123"


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture(scope="module")
def cal_seed(app, seed, mongo):
    """Механик + клиент/юнит в магазине A; WO создаются в тестах."""
    from app.constants.permissions import build_default_roles

    master = mongo["roobico_test_master"]
    tdb = mongo[TENANT_A_DB]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = _now()

    mechanic_role = next(r for r in build_default_roles() if r["key"] == "mechanic")
    tdb.roles.update_one({"key": "mechanic"}, {"$set": mechanic_role}, upsert=True)

    mechanic = {
        "_id": ObjectId(), "email": MECHANIC_EMAIL,
        "password_hash": generate_password_hash(MECHANIC_PASSWORD),
        "first_name": "Cal", "last_name": "Mechanic", "is_active": True,
        "tenant_id": seed["tenant_a"]["_id"], "shop_ids": [str(shop_a["_id"])],
        "role": "mechanic", "created_at": now,
    }
    master.users.insert_one(mechanic)

    customer = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "tenant_id": seed["tenant_a"]["_id"],
        "company_name": "Calendar Fleet LLC", "is_active": True, "created_at": now,
    }
    shop_db.customers.insert_one(customer)
    unit = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "customer_id": customer["_id"],
        "unit_number": "CAL-1", "year": 2020, "make": "Volvo", "model": "VNL",
        "is_active": True, "created_at": now,
    }
    shop_db.units.insert_one(unit)

    yield {"mechanic": mechanic, "customer": customer, "unit": unit, "shop": shop_a}

    shop_db.work_orders.delete_many({"wo_number": {"$gte": 90000}})
    shop_db.wo_time_logs.delete_many({"user_id": mechanic["_id"]})
    shop_db.calendar_events.delete_many({"shop_id": shop_a["_id"]})
    master.users.delete_one({"_id": mechanic["_id"]})


def _json(client, method, url, payload=None):
    token = get_csrf_token(client)
    fn = getattr(client, method)
    return fn(url, json=payload if payload is not None else {}, headers={"X-CSRFToken": token})


def _insert_wo(shop_db, cal_seed, number, status="open", work_order_date=None, created_at=None, **extra):
    now = _now()
    doc = {
        "_id": ObjectId(), "shop_id": cal_seed["shop"]["_id"], "wo_number": number,
        "customer_id": cal_seed["customer"]["_id"], "unit_id": cal_seed["unit"]["_id"],
        "status": status, "is_active": True,
        "labors": [{
            "labor_id": f"L{number}",
            "labor": {
                "description": "Replace brakes", "hours": "2",
                "assigned_mechanics": [{"user_id": cal_seed["mechanic"]["_id"], "name": "Cal Mechanic", "role": "mechanic", "percent": 100}],
            },
            "parts": [],
        }],
        "work_order_date": work_order_date,
        "created_at": created_at or now,
        "updated_at": now,
    }
    doc.update(extra)
    shop_db.work_orders.insert_one(doc)
    return doc


def _future_weekday(weeks_ahead: int, weekday: int = 1, hour: int = 12) -> datetime:
    """Будущий день недели (0=Пн … 6=Вс) через N недель — чтобы соседние дни
    теста гарантированно попадали в одну календарную неделю."""
    today = _now().replace(hour=hour, minute=0, second=0, microsecond=0)
    monday = today - timedelta(days=today.weekday())
    return monday + timedelta(weeks=weeks_ahead, days=weekday)


def _week_range(day: datetime):
    monday = day - timedelta(days=day.weekday())
    start = monday.strftime("%Y-%m-%dT00:00:00")
    end = (monday + timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
    return start, end


def _events(client, day, include_wo=True):
    start, end = _week_range(day)
    url = f"/calendar/api/events?start={start}&end={end}"
    if include_wo:
        url += "&include_work_orders=1"
    resp = client.get(url)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def test_work_order_without_logs_appears_on_its_date(client, cal_seed, mongo):
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    # Дата WO — среда следующей недели (UTC-полночь, как пишет shop_local_date_to_utc).
    target = _future_weekday(1, weekday=2)
    wo = _insert_wo(shop_db, cal_seed, 90001, work_order_date=target.replace(hour=6))

    events = _events(client, target)
    mine = [e for e in events if e.get("work_order_id") == str(wo["_id"])]
    assert len(mine) == 1, events
    ev = mine[0]
    assert ev["kind"] == "work_order" and ev["is_virtual"] is True
    assert ev["wo_number"] == 90001
    assert ev["wo_state"] == "open"
    assert ev["customer_label"] == "Calendar Fleet LLC"
    assert "CAL-1" in ev["unit_label"]
    assert ev["mechanic_ids"] == [str(cal_seed["mechanic"]["_id"])]
    assert ev["labor_summary"] == ["Replace brakes"]
    # 2 часа работ → событие длиной 2 часа
    st = datetime.fromisoformat(ev["start_time"])
    en = datetime.fromisoformat(ev["end_time"])
    assert (en - st) == timedelta(hours=2)
    assert ev["id"].startswith("wo:")
    # Без таймеров и расписания WO «не расписан» — фронт кладёт его в полосу, не на сетку.
    assert ev["unscheduled"] is True and ev["source"] == "work_order_date"

    # Без флага include_work_orders (мобильное приложение) WO не отдаются.
    assert not [e for e in _events(client, target, include_wo=False) if e.get("is_virtual")]


def test_estimates_are_not_shown(client, cal_seed, mongo):
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    target = _future_weekday(2, weekday=2)
    wo = _insert_wo(shop_db, cal_seed, 90002, status="estimate", work_order_date=target.replace(hour=6))
    events = _events(client, target)
    assert not [e for e in events if e.get("work_order_id") == str(wo["_id"])]


def test_time_logs_split_work_order_by_day_with_workers(client, cal_seed, mongo):
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    # WO датирован далеко в прошлом; механик работал два дня в этом окне.
    base = _future_weekday(3, weekday=1, hour=15)  # вторник + среда одной недели
    wo = _insert_wo(shop_db, cal_seed, 90003, status="in_progress", work_order_date=base - timedelta(days=60))
    mech = cal_seed["mechanic"]
    for offset in (0, 1):
        started = base + timedelta(days=offset)
        shop_db.wo_time_logs.insert_one({
            "shop_id": cal_seed["shop"]["_id"], "work_order_id": wo["_id"], "wo_number": 90003,
            "labor_id": "L90003", "user_id": mech["_id"], "user_name": "Cal Mechanic",
            "started_at": started, "stopped_at": started + timedelta(hours=1, minutes=30),
            "seconds": 5400, "created_at": started, "updated_at": started,
        })

    events = _events(client, base)
    mine = sorted([e for e in events if e.get("work_order_id") == str(wo["_id"])], key=lambda e: e["start_time"])
    assert len(mine) == 2, mine
    for ev in mine:
        assert ev["source"] == "time_logs"
        assert ev["wo_state"] == "in_progress"
        assert ev["worked"] and ev["worked"][0]["name"] == "Cal Mechanic"
        assert ev["worked"][0]["seconds"] == 5400
        st = datetime.fromisoformat(ev["start_time"])
        en = datetime.fromisoformat(ev["end_time"])
        assert (en - st) == timedelta(hours=1, minutes=30)
    days = {datetime.fromisoformat(e["start_time"]).date() for e in mine}
    assert len(days) == 2


def test_wo_states_paid_and_completed(client, cal_seed, mongo):
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    target = _future_weekday(4, weekday=2)
    paid = _insert_wo(shop_db, cal_seed, 90004, status="paid", work_order_date=target.replace(hour=6))
    done = _insert_wo(shop_db, cal_seed, 90005, status="open", work_order_date=target.replace(hour=6), mechanic_done=True)
    awaiting = _insert_wo(shop_db, cal_seed, 90006, status="in_progress", work_order_date=target.replace(hour=6), mechanic_done=True)

    by_wo = {e.get("work_order_id"): e for e in _events(client, target) if e.get("work_order_id")}
    assert by_wo[str(paid["_id"])]["wo_state"] == "paid"
    assert by_wo[str(done["_id"])]["wo_state"] == "completed"
    assert by_wo[str(awaiting["_id"])]["wo_state"] == "mechanic_done"

    states = client.get("/calendar/api/wo_states").get_json()
    assert [s["key"] for s in states] == ["open", "in_progress", "mechanic_done", "completed", "paid"]


def test_mechanic_mode_masks_paid(client, cal_seed, mongo):
    assert login(client, email=MECHANIC_EMAIL, password=MECHANIC_PASSWORD).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    target = _future_weekday(5, weekday=2)
    paid = _insert_wo(shop_db, cal_seed, 90007, status="paid", work_order_date=target.replace(hour=6))

    by_wo = {e.get("work_order_id"): e for e in _events(client, target) if e.get("work_order_id")}
    ev = by_wo[str(paid["_id"])]
    assert ev["wo_state"] == "completed"
    assert ev["wo_status"] == "completed"
    assert "paid" not in str(ev).lower()

    states = client.get("/calendar/api/wo_states").get_json()
    assert "paid" not in {s["key"] for s in states}

    # Страница календаря механику открывается, кнопки Create WO нет.
    page = client.get("/calendar")
    assert page.status_code == 200
    assert 'data-can-create-wo="0"' in page.get_data(as_text=True)


def test_drag_virtual_wo_creates_linked_appointment_and_hides_virtual(client, cal_seed, mongo):
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    target = _future_weekday(6, weekday=2)
    wo = _insert_wo(shop_db, cal_seed, 90008, work_order_date=target.replace(hour=6))
    day = target.strftime("%Y-%m-%d")

    resp = _json(client, "post", "/calendar/api/events", {
        "start_time": f"{day}T09:00:00", "end_time": f"{day}T11:00:00",
        "work_order_id": str(wo["_id"]),
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    created = resp.get_json()
    assert created["work_order_id"] == str(wo["_id"])
    assert created["wo_number"] == 90008
    assert created["wo_state"] == "open"
    # Клиент/юнит/механик/описание подтянулись с WO.
    assert created["customer_id"] == str(cal_seed["customer"]["_id"])
    assert created["unit_id"] == str(cal_seed["unit"]["_id"])
    assert created["mechanic_id"] == str(cal_seed["mechanic"]["_id"])
    assert created["title"] == "Replace brakes"

    events = _events(client, target)
    mine = [e for e in events if e.get("work_order_id") == str(wo["_id"])]
    # WO теперь в плане: чип «не расписан» исчез, осталась одна привязанная запись.
    assert len(mine) == 1 and mine[0]["is_virtual"] is False and mine[0]["id"] == created["id"]
    assert mine[0]["superseded"] is False
    # План — на своего механика, а не на всех назначенных по WO.
    assert mine[0]["mechanic_ids"] == [str(cal_seed["mechanic"]["_id"])]

    # Отвязка: work_order_id="" — запись остаётся, WO снова виден чипом.
    resp = _json(client, "put", f"/calendar/api/events/{created['id']}", {"work_order_id": ""})
    assert resp.status_code == 200
    assert resp.get_json()["work_order_id"] == ""
    events = _events(client, target)
    kinds = sorted(e["is_virtual"] for e in events if e.get("work_order_id") == str(wo["_id"]) or e["id"] == created["id"])
    assert kinds == [False, True]

    # Несуществующий WO — 404.
    resp = _json(client, "post", "/calendar/api/events", {
        "start_time": f"{day}T09:00:00", "end_time": f"{day}T11:00:00",
        "work_order_id": str(ObjectId()),
    })
    assert resp.status_code == 404


def test_create_wo_from_calendar_links_event_and_delete_unlinks(client, cal_seed, mongo, seed):
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    target = _future_weekday(7, weekday=2)
    day = target.strftime("%Y-%m-%d")

    resp = _json(client, "post", "/calendar/api/events", {
        "start_time": f"{day}T13:00:00", "end_time": f"{day}T14:00:00",
        "customer_id": str(cal_seed["customer"]["_id"]), "customer_label": "Calendar Fleet LLC",
        "unit_id": str(cal_seed["unit"]["_id"]), "unit_label": "CAL-1",
        "mechanic_id": str(cal_seed["mechanic"]["_id"]), "mechanic_name": "Cal Mechanic",
        "title": "Oil change",
    })
    assert resp.status_code == 201
    event_id = resp.get_json()["id"]

    # Страница WO из календаря содержит скрытое поле calendar_event_id.
    page = client.get(
        f"/work_orders/details?customer_id={cal_seed['customer']['_id']}&unit_id={cal_seed['unit']['_id']}"
        f"&mechanic_id={cal_seed['mechanic']['_id']}&note=Oil%20change&calendar_event_id={event_id}"
    )
    assert page.status_code == 200
    assert f'name="calendar_event_id" value="{event_id}"' in page.get_data(as_text=True)

    token = get_csrf_token(client)
    resp = client.post("/work_orders/create", data={
        "csrf_token": token,
        "customer_id": str(cal_seed["customer"]["_id"]),
        "unit_id": str(cal_seed["unit"]["_id"]),
        "calendar_event_id": event_id,
        "labors[0][labor_description]": "Oil change",
        "labors[0][labor_hours]": "1",
        "labors[0][labor_rate_code]": seed["labor_rate"]["code"],
    })
    assert resp.status_code == 302, resp.get_data(as_text=True)

    event = shop_db.calendar_events.find_one({"_id": ObjectId(event_id)})
    assert event["work_order_id"] is not None
    wo = shop_db.work_orders.find_one({"_id": event["work_order_id"]})
    assert wo and event["wo_number"] == wo["wo_number"]

    # В календаре запись показывает живой статус WO и номер.
    events = _events(client, target)
    ev = next(e for e in events if e["id"] == event_id)
    assert ev["wo_number"] == wo["wo_number"]
    assert ev["wo_state"] == "open"
    assert ev["labor_summary"] == ["Oil change"]

    # Удаление WO снимает привязку, запись остаётся.
    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/delete")
    assert resp.status_code == 200 and resp.get_json()["ok"] is True
    event = shop_db.calendar_events.find_one({"_id": ObjectId(event_id)})
    assert event["work_order_id"] is None
    ev = next(e for e in _events(client, target) if e["id"] == event_id)
    assert ev["work_order_id"] == "" and ev["wo_state"] == ""
    shop_db.work_orders.delete_one({"_id": wo["_id"]})


def test_event_validation(client, cal_seed):
    assert login(client).status_code == 302
    resp = _json(client, "post", "/calendar/api/events", {"start_time": "2030-01-01T10:00:00", "end_time": "2030-01-01T09:00:00"})
    assert resp.status_code == 400
    resp = _json(client, "post", "/calendar/api/events", {"start_time": "nope", "end_time": "2030-01-01T09:00:00"})
    assert resp.status_code == 400


def test_one_mechanic_two_work_orders_sessions_do_not_overlap(client, cal_seed, mongo):
    """Один механик, два WO в один день: блоки — по реальным сессиям таймеров,
    а не «от первого старта до последнего стопа», поэтому не пересекаются."""
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    base = _future_weekday(8, weekday=1, hour=14)  # 14:00 UTC → утро по времени магазина
    mech = cal_seed["mechanic"]
    wo_a = _insert_wo(shop_db, cal_seed, 90010, status="in_progress", work_order_date=base - timedelta(days=30))
    wo_b = _insert_wo(shop_db, cal_seed, 90011, status="in_progress", work_order_date=base - timedelta(days=30))

    def _log(wo, start_offset_min, minutes):
        started = base + timedelta(minutes=start_offset_min)
        shop_db.wo_time_logs.insert_one({
            "shop_id": cal_seed["shop"]["_id"], "work_order_id": wo["_id"], "wo_number": wo["wo_number"],
            "labor_id": f"L{wo['wo_number']}", "user_id": mech["_id"], "user_name": "Cal Mechanic",
            "started_at": started, "stopped_at": started + timedelta(minutes=minutes),
            "seconds": minutes * 60, "created_at": started, "updated_at": started,
        })

    # A: 0–60, затем B: 70–130, затем снова A: 140–200 (пауза 80 мин → отдельный блок)
    _log(wo_a, 0, 60)
    _log(wo_b, 70, 60)
    _log(wo_a, 140, 60)
    # Короткий стоп/старт на B (пауза 5 мин) — склеивается в один блок: 210–240 + 245–260
    _log(wo_b, 210, 30)
    _log(wo_b, 245, 15)

    events = [e for e in _events(client, base) if e.get("work_order_id") in (str(wo_a["_id"]), str(wo_b["_id"]))]
    blocks = sorted(
        ((datetime.fromisoformat(e["start_time"]), datetime.fromisoformat(e["end_time"]), e["work_order_id"]) for e in events),
        key=lambda t: t[0],
    )
    assert len(blocks) == 4, blocks
    for (s1, e1, _), (s2, e2, _) in zip(blocks, blocks[1:]):
        assert e1 <= s2, f"blocks overlap: {s1}-{e1} vs {s2}-{e2}"
    # Склеенный блок B длится 50 минут (210→260), а не 30 и 15 по отдельности.
    merged = [b for b in blocks if b[2] == str(wo_b["_id"])][-1]
    assert (merged[1] - merged[0]) == timedelta(minutes=50)
    for e in events:
        assert e["mechanic_ids"] == [str(mech["_id"])]
        assert e["worked"][0]["name"] == "Cal Mechanic"


def test_mechanic_cannot_be_double_booked(client, cal_seed, mongo):
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    target = _future_weekday(9, weekday=2)
    day = target.strftime("%Y-%m-%d")
    mech_id = str(cal_seed["mechanic"]["_id"])
    base_payload = {
        "customer_id": str(cal_seed["customer"]["_id"]), "customer_label": "Calendar Fleet LLC",
        "mechanic_id": mech_id, "mechanic_name": "Cal Mechanic", "title": "Slot A",
    }

    resp = _json(client, "post", "/calendar/api/events", {**base_payload, "start_time": f"{day}T09:00:00", "end_time": f"{day}T11:00:00"})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    first_id = resp.get_json()["id"]

    # Пересечение по времени у того же механика — 409 с понятным текстом.
    resp = _json(client, "post", "/calendar/api/events", {**base_payload, "title": "Slot B", "start_time": f"{day}T10:00:00", "end_time": f"{day}T12:00:00"})
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert "already booked" in resp.get_json()["error"]
    assert shop_db.calendar_events.count_documents({"shop_id": cal_seed["shop"]["_id"], "title": "Slot B"}) == 0

    # Встык (11:00–12:00) — можно.
    resp = _json(client, "post", "/calendar/api/events", {**base_payload, "title": "Slot C", "start_time": f"{day}T11:00:00", "end_time": f"{day}T12:00:00"})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    second_id = resp.get_json()["id"]

    # Другой механик / без механика в то же время — можно.
    resp = _json(client, "post", "/calendar/api/events", {**base_payload, "mechanic_id": "", "mechanic_name": "", "title": "No mech", "start_time": f"{day}T09:30:00", "end_time": f"{day}T10:30:00"})
    assert resp.status_code == 201

    # Перенос второй записи на время первой — 409; свою же запись двигать внутри себя — можно.
    resp = _json(client, "put", f"/calendar/api/events/{second_id}", {"start_time": f"{day}T10:30:00", "end_time": f"{day}T11:30:00"})
    assert resp.status_code == 409
    resp = _json(client, "put", f"/calendar/api/events/{first_id}", {"start_time": f"{day}T09:15:00", "end_time": f"{day}T11:00:00"})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    # Отменённая запись место не держит.
    resp = _json(client, "put", f"/calendar/api/events/{first_id}", {"status": "cancelled"})
    assert resp.status_code == 200
    resp = _json(client, "post", "/calendar/api/events", {**base_payload, "title": "Slot D", "start_time": f"{day}T09:00:00", "end_time": f"{day}T10:00:00"})
    assert resp.status_code == 201, resp.get_data(as_text=True)


def test_plan_is_superseded_by_actual_work_on_same_wo(client, cal_seed, mongo):
    """Факты важнее плана: сессия таймера по WO показывается всегда, а пересёкшаяся
    с ней запись-план по тому же WO помечается superseded (фронт её прячет)."""
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    base = _future_weekday(10, weekday=3, hour=15)
    day = base.strftime("%Y-%m-%d")
    wo = _insert_wo(shop_db, cal_seed, 90012, status="in_progress", work_order_date=base - timedelta(days=10))
    mech = cal_seed["mechanic"]
    shop_db.wo_time_logs.insert_one({
        "shop_id": cal_seed["shop"]["_id"], "work_order_id": wo["_id"], "wo_number": 90012,
        "labor_id": "L90012", "user_id": mech["_id"], "user_name": "Cal Mechanic",
        "started_at": base, "stopped_at": base + timedelta(minutes=45), "seconds": 2700,
        "created_at": base, "updated_at": base,
    })
    resp = _json(client, "post", "/calendar/api/events", {
        "start_time": f"{day}T08:00:00", "end_time": f"{day}T12:00:00", "work_order_id": str(wo["_id"]),
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    events = _events(client, base)
    mine = [e for e in events if e.get("work_order_id") == str(wo["_id"])]
    sessions = [e for e in mine if e["is_virtual"]]
    plans = [e for e in mine if not e["is_virtual"]]
    assert len(sessions) == 1 and sessions[0]["source"] == "time_logs"
    assert len(plans) == 1 and plans[0]["superseded"] is True
    assert plans[0]["worked"] and plans[0]["worked"][0]["seconds"] == 2700

    # План на другой день (без фактов) — не superseded.
    other_day = (base + timedelta(days=1)).strftime("%Y-%m-%d")
    resp = _json(client, "post", "/calendar/api/events", {
        "start_time": f"{other_day}T08:00:00", "end_time": f"{other_day}T09:00:00", "work_order_id": str(wo["_id"]),
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    plans = [e for e in _events(client, base) if not e["is_virtual"] and e.get("work_order_id") == str(wo["_id"])]
    assert sorted(p["superseded"] for p in plans) == [False, True]


def test_plan_of_another_mechanic_is_not_superseded(client, cal_seed, mongo):
    """Факт одного механика заменяет только ЕГО план по этому WO: план второго
    механика на то же время остаётся на сетке — свою часть он ещё не сделал."""
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    base = _future_weekday(12, weekday=2, hour=15)
    day = base.strftime("%Y-%m-%d")
    wo = _insert_wo(shop_db, cal_seed, 90015, status="in_progress", work_order_date=base - timedelta(days=3))
    mech = cal_seed["mechanic"]
    other_mech_id = str(ObjectId())
    shop_db.wo_time_logs.insert_one({
        "shop_id": cal_seed["shop"]["_id"], "work_order_id": wo["_id"], "wo_number": 90015,
        "labor_id": "L90015", "user_id": mech["_id"], "user_name": "Cal Mechanic",
        "started_at": base, "stopped_at": base + timedelta(hours=1), "seconds": 3600,
        "created_at": base, "updated_at": base,
    })
    for mechanic_id, name in ((str(mech["_id"]), "Cal Mechanic"), (other_mech_id, "Other Mechanic")):
        resp = _json(client, "post", "/calendar/api/events", {
            "start_time": f"{day}T06:00:00", "end_time": f"{day}T20:00:00", "work_order_id": str(wo["_id"]),
            "mechanic_id": mechanic_id, "mechanic_name": name,
        })
        assert resp.status_code == 201, resp.get_data(as_text=True)
    plans = [e for e in _events(client, base) if not e["is_virtual"] and e.get("work_order_id") == str(wo["_id"])]
    by_mech = {p["mechanic_id"]: p["superseded"] for p in plans}
    assert by_mech == {str(mech["_id"]): True, other_mech_id: False}, by_mech


def test_appointment_conflicts_with_actual_timer_work(client, cal_seed, mongo):
    """Нельзя поставить механика на слот, в который он по таймеру реально работал
    над другим WO; на тот же WO — можно (план совпадает с фактом)."""
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    mech = cal_seed["mechanic"]
    base = _future_weekday(11, weekday=2, hour=16)  # 16:00 UTC
    wo_a = _insert_wo(shop_db, cal_seed, 90013, status="in_progress", work_order_date=base - timedelta(days=5))
    wo_b = _insert_wo(shop_db, cal_seed, 90014, status="open", work_order_date=base - timedelta(days=5))
    shop_db.wo_time_logs.insert_one({
        "shop_id": cal_seed["shop"]["_id"], "work_order_id": wo_a["_id"], "wo_number": 90013,
        "labor_id": "L90013", "user_id": mech["_id"], "user_name": "Cal Mechanic",
        "started_at": base, "stopped_at": base + timedelta(hours=2), "seconds": 7200,
        "created_at": base, "updated_at": base,
    })
    # Настенное время магазина (тестовый магазин без timezone_location → America/Chicago).
    local = base.astimezone(ZoneInfo("America/Chicago")).replace(tzinfo=None)
    slot_start = (local + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S")
    slot_end = (local + timedelta(hours=1, minutes=30)).strftime("%Y-%m-%dT%H:%M:%S")
    payload = {"start_time": slot_start, "end_time": slot_end, "mechanic_id": str(mech["_id"]), "mechanic_name": "Cal Mechanic"}

    # Другой WO в то время, когда механик реально работал над A — 409 с указанием WO и времени.
    resp = _json(client, "post", "/calendar/api/events", {**payload, "work_order_id": str(wo_b["_id"])})
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert "WO #90013" in resp.get_json()["error"] and "working" in resp.get_json()["error"]
    # Запись без WO в то же время — тоже конфликт.
    assert _json(client, "post", "/calendar/api/events", {**payload, "title": "Oil change"}).status_code == 409
    # Тот же WO — план совпадает с фактом, можно.
    resp = _json(client, "post", "/calendar/api/events", {**payload, "work_order_id": str(wo_a["_id"])})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    # Запись на WO B без явного механика берёт механика с WO B (тот же Cal) — всё ещё конфликт.
    resp = _json(client, "post", "/calendar/api/events", {**payload, "work_order_id": str(wo_b["_id"]), "mechanic_id": "", "mechanic_name": ""})
    assert resp.status_code == 409, resp.get_data(as_text=True)
    # Запись без механика и без WO в это время — можно (некого проверять).
    resp = _json(client, "post", "/calendar/api/events", {**payload, "mechanic_id": "", "mechanic_name": "", "title": "No mechanic"})
    assert resp.status_code == 201, resp.get_data(as_text=True)


# ── после ревью: таймеры, конфликты, чипы, пересечения ─────────────────

def _chicago_local(dt_utc):
    """Настенное время тестового магазина (без timezone_location → America/Chicago)."""
    return dt_utc.astimezone(ZoneInfo("America/Chicago")).replace(tzinfo=None)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _log(shop_db, cal_seed, wo, started, stopped):
    mech = cal_seed["mechanic"]
    shop_db.wo_time_logs.insert_one({
        "shop_id": cal_seed["shop"]["_id"], "work_order_id": wo["_id"], "wo_number": wo["wo_number"],
        "labor_id": f"L{wo['wo_number']}", "user_id": mech["_id"], "user_name": "Cal Mechanic",
        "started_at": started, "stopped_at": stopped,
        "seconds": int((stopped - started).total_seconds()) if stopped else 0,
        "created_at": started, "updated_at": started,
    })


def test_running_timer_blocks_only_until_now(client, cal_seed, mongo):
    """Работающий таймер занимает [старт, сейчас): слот прямо сейчас — конфликт,
    завтрашний слот — свободен. Блок на сетке помечен running."""
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    mech = cal_seed["mechanic"]
    now = _now().replace(second=0, microsecond=0)
    wo_a = _insert_wo(shop_db, cal_seed, 90016, status="in_progress", work_order_date=now - timedelta(days=1))
    wo_b = _insert_wo(shop_db, cal_seed, 90017, status="open", work_order_date=now - timedelta(days=1))
    started = now - timedelta(hours=2)
    _log(shop_db, cal_seed, wo_a, started, None)
    try:
        local_now = _chicago_local(now)
        tomorrow = (local_now + timedelta(days=1)).replace(hour=10, minute=0)
        payload = {"work_order_id": str(wo_b["_id"]), "mechanic_id": str(mech["_id"]), "mechanic_name": "Cal Mechanic"}
        resp = _json(client, "post", "/calendar/api/events",
                     {**payload, "start_time": _iso(tomorrow), "end_time": _iso(tomorrow + timedelta(hours=1))})
        assert resp.status_code == 201, resp.get_data(as_text=True)
        resp = _json(client, "post", "/calendar/api/events",
                     {**payload, "start_time": _iso(local_now - timedelta(minutes=30)), "end_time": _iso(local_now + timedelta(minutes=30))})
        assert resp.status_code == 409, resp.get_data(as_text=True)
        assert "is working" in resp.get_json()["error"]

        blocks = [e for e in _events(client, _chicago_local(started)) if e.get("work_order_id") == str(wo_a["_id"]) and e["is_virtual"]]
        assert len(blocks) == 1, blocks
        assert blocks[0]["running"] is True and blocks[0]["worked"][0]["running"] is True
        assert blocks[0]["source"] == "time_logs" and not blocks[0]["unscheduled"]
    finally:
        shop_db.wo_time_logs.delete_many({"work_order_id": wo_a["_id"]})
        shop_db.calendar_events.delete_many({"work_order_id": wo_b["_id"]})


def test_non_booking_edits_skip_conflict_check(client, cal_seed, mongo):
    """Старая запись, во время которой механик по факту работал над другим WO,
    остаётся редактируемой: заголовок/статус — можно, сдвиг времени — 409."""
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    mech = cal_seed["mechanic"]
    base = _future_weekday(14, weekday=2, hour=16)
    local = _chicago_local(base)
    resp = _json(client, "post", "/calendar/api/events", {
        "start_time": _iso(local), "end_time": _iso(local + timedelta(hours=2)),
        "mechanic_id": str(mech["_id"]), "mechanic_name": "Cal Mechanic", "title": "Legacy plan",
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    event_id = resp.get_json()["id"]
    wo = _insert_wo(shop_db, cal_seed, 90018, status="in_progress", work_order_date=base - timedelta(days=1))
    _log(shop_db, cal_seed, wo, base + timedelta(minutes=15), base + timedelta(minutes=45))

    assert _json(client, "put", f"/calendar/api/events/{event_id}", {"title": "Renamed"}).status_code == 200
    assert _json(client, "put", f"/calendar/api/events/{event_id}", {"status": "confirmed"}).status_code == 200
    resp = _json(client, "put", f"/calendar/api/events/{event_id}", {"start_time": _iso(local + timedelta(minutes=15))})
    assert resp.status_code == 409, resp.get_data(as_text=True)


def test_wo_worked_in_another_week_has_no_chip(client, cal_seed, mongo):
    """WO с датой на неделе N, над которым работали на неделе N+1: на неделе N
    чипа «не расписан» нет, на неделе N+1 — ровно один блок по таймеру."""
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    week_n = _future_weekday(15, weekday=2, hour=14)
    week_n1 = week_n + timedelta(days=7)
    wo = _insert_wo(shop_db, cal_seed, 90019, status="in_progress", work_order_date=week_n)
    _log(shop_db, cal_seed, wo, week_n1, week_n1 + timedelta(hours=1))
    assert [e for e in _events(client, week_n) if e.get("work_order_id") == str(wo["_id"])] == []
    later = [e for e in _events(client, week_n1) if e.get("work_order_id") == str(wo["_id"])]
    assert len(later) == 1 and later[0]["source"] == "time_logs" and not later[0]["unscheduled"]


def test_cancelled_plan_brings_the_chip_back(client, cal_seed, mongo):
    """Отменённая запись слот не держит и планом не считается: чип возвращается."""
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    target = _future_weekday(17, weekday=2)
    wo = _insert_wo(shop_db, cal_seed, 90020, work_order_date=target.replace(hour=6))
    day = target.strftime("%Y-%m-%d")

    def _chips():
        return [e for e in _events(client, target) if e.get("work_order_id") == str(wo["_id"]) and e.get("unscheduled")]

    assert len(_chips()) == 1
    resp = _json(client, "post", "/calendar/api/events",
                 {"start_time": f"{day}T09:00:00", "end_time": f"{day}T10:00:00", "work_order_id": str(wo["_id"])})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    event_id = resp.get_json()["id"]
    assert _chips() == []
    assert _json(client, "put", f"/calendar/api/events/{event_id}", {"status": "cancelled"}).status_code == 200
    assert len(_chips()) == 1
    assert _json(client, "delete", f"/calendar/api/events/{event_id}").status_code == 200
    assert len(_chips()) == 1


def test_one_mechanic_sessions_never_overlap_across_work_orders(client, cal_seed, mongo):
    """Авто-переключение таймера (70 секунд на A, сразу B) и вручную удлинённая
    сессия не дают пересечений у одного механика: конец режется по началу следующей."""
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    base = _future_weekday(18, weekday=1, hour=14)
    wo_a = _insert_wo(shop_db, cal_seed, 90021, status="in_progress", work_order_date=base - timedelta(days=2))
    wo_b = _insert_wo(shop_db, cal_seed, 90022, status="in_progress", work_order_date=base - timedelta(days=2))
    _log(shop_db, cal_seed, wo_a, base, base + timedelta(seconds=70))                      # ошибочный старт
    _log(shop_db, cal_seed, wo_b, base + timedelta(seconds=70), base + timedelta(hours=2))
    _log(shop_db, cal_seed, wo_a, base + timedelta(minutes=90), base + timedelta(hours=3))  # поправили руками — наехало на B
    blocks = sorted(
        ((datetime.fromisoformat(e["start_time"]), datetime.fromisoformat(e["end_time"]), e["work_order_id"])
         for e in _events(client, base) if e.get("work_order_id") in (str(wo_a["_id"]), str(wo_b["_id"]))),
        key=lambda t: t[0],
    )
    assert len(blocks) == 3, blocks
    for (s1, e1, _), (s2, e2, _) in zip(blocks, blocks[1:]):
        assert e1 <= s2, f"blocks overlap: {s1}-{e1} vs {s2}-{e2}"
    assert all(e > s for s, e, _ in blocks)


def test_lunch_break_on_same_wo_is_one_block(client, cal_seed, mongo):
    """Пауза до часа на том же WO без чужой работы — один блок; с чужой работой
    между — два."""
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    base = _future_weekday(23, weekday=1, hour=14)
    wo_a = _insert_wo(shop_db, cal_seed, 90028, status="in_progress", work_order_date=base - timedelta(days=2))
    wo_b = _insert_wo(shop_db, cal_seed, 90029, status="in_progress", work_order_date=base - timedelta(days=2))
    _log(shop_db, cal_seed, wo_a, base, base + timedelta(hours=1))                                   # 0–60
    _log(shop_db, cal_seed, wo_a, base + timedelta(minutes=100), base + timedelta(minutes=160))       # обед 40 мин → склейка
    _log(shop_db, cal_seed, wo_b, base + timedelta(minutes=170), base + timedelta(minutes=200))       # чужая работа
    _log(shop_db, cal_seed, wo_a, base + timedelta(minutes=210), base + timedelta(minutes=240))       # после B — новый блок
    mine = [e for e in _events(client, base) if e.get("work_order_id") == str(wo_a["_id"])]
    assert len(mine) == 2, [(e["start_time"], e["end_time"]) for e in mine]
    merged = min(mine, key=lambda e: e["start_time"])
    assert datetime.fromisoformat(merged["end_time"]) - datetime.fromisoformat(merged["start_time"]) == timedelta(minutes=160)
    assert merged["worked"][0]["seconds"] == 120 * 60


def test_superseded_plan_does_not_hold_the_slot(client, cal_seed, mongo):
    """План, который «сбылся» (механик по таймеру работал над этим WO в его окно),
    спрятан с сетки и не мешает поставить тому же механику другой WO позже."""
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    mech = cal_seed["mechanic"]
    base = _future_weekday(19, weekday=2, hour=15)
    local = _chicago_local(base)
    wo_x = _insert_wo(shop_db, cal_seed, 90023, status="in_progress", work_order_date=base - timedelta(days=2))
    wo_y = _insert_wo(shop_db, cal_seed, 90024, status="open", work_order_date=base - timedelta(days=2))
    who = {"mechanic_id": str(mech["_id"]), "mechanic_name": "Cal Mechanic"}
    resp = _json(client, "post", "/calendar/api/events",
                 {**who, "start_time": _iso(local), "end_time": _iso(local + timedelta(hours=3)), "work_order_id": str(wo_x["_id"])})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    _log(shop_db, cal_seed, wo_x, base, base + timedelta(hours=1))

    resp = _json(client, "post", "/calendar/api/events",
                 {**who, "start_time": _iso(local + timedelta(hours=2)), "end_time": _iso(local + timedelta(hours=3)), "work_order_id": str(wo_y["_id"])})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    plans = [e for e in _events(client, base) if not e["is_virtual"] and e.get("work_order_id") == str(wo_x["_id"])]
    assert len(plans) == 1 and plans[0]["superseded"] is True


def test_midnight_session_stays_on_start_day_and_virtual_ids_are_read_only(client, cal_seed, mongo):
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    day = _future_weekday(20, weekday=2)
    local_start = _chicago_local(day).replace(hour=23, minute=30)
    start_utc = local_start.replace(tzinfo=ZoneInfo("America/Chicago")).astimezone(timezone.utc)
    wo = _insert_wo(shop_db, cal_seed, 90025, status="in_progress", work_order_date=day - timedelta(days=1))
    _log(shop_db, cal_seed, wo, start_utc, start_utc + timedelta(hours=1))
    mine = [e for e in _events(client, day) if e.get("work_order_id") == str(wo["_id"])]
    assert len(mine) == 1, mine
    assert mine[0]["start_time"] == _iso(local_start) and mine[0]["end_time"] == _iso(local_start.replace(hour=23, minute=59))
    # Виртуальные события менять/удалять нельзя.
    assert _json(client, "put", f"/calendar/api/events/{mine[0]['id']}", {"title": "x"}).status_code == 404
    assert _json(client, "delete", f"/calendar/api/events/{mine[0]['id']}").status_code == 404


def test_keep_unassigned_skips_wo_mechanic_fallback(client, cal_seed, mongo):
    """Бросили чип в колонку «Unassigned»: механика с WO не подставляем; для
    отображения такой план наследует назначенных по WO."""
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    target = _future_weekday(21, weekday=2)
    day = target.strftime("%Y-%m-%d")
    wo = _insert_wo(shop_db, cal_seed, 90026, work_order_date=target.replace(hour=6))
    resp = _json(client, "post", "/calendar/api/events", {
        "start_time": f"{day}T09:00:00", "end_time": f"{day}T10:00:00", "work_order_id": str(wo["_id"]),
        "mechanic_id": "", "mechanic_name": "", "keep_unassigned": True,
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    created = resp.get_json()
    assert created["mechanic_id"] == ""
    assert created["mechanic_ids"] == [str(cal_seed["mechanic"]["_id"])]
    assert shop_db.calendar_events.find_one({"_id": ObjectId(created["id"])})["mechanic_id"] is None


def test_mechanic_mode_masks_paid_on_linked_appointments(client, cal_seed, mongo):
    """Мобильный путь (диапазон с Z, без include_work_orders): привязанная к
    оплаченному WO запись отдаётся механику как completed."""
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    target = _future_weekday(22, weekday=2)
    day = target.strftime("%Y-%m-%d")
    paid = _insert_wo(shop_db, cal_seed, 90027, status="paid", work_order_date=target.replace(hour=6))
    resp = _json(client, "post", "/calendar/api/events",
                 {"start_time": f"{day}T09:00:00", "end_time": f"{day}T10:00:00", "work_order_id": str(paid["_id"])})
    assert resp.status_code == 201, resp.get_data(as_text=True)

    assert login(client, email=MECHANIC_EMAIL, password=MECHANIC_PASSWORD).status_code == 302
    start, end = _week_range(target)
    resp = client.get(f"/calendar/api/events?start={start}Z&end={end}Z")
    assert resp.status_code == 200
    mine = [e for e in resp.get_json() if e.get("work_order_id") == str(paid["_id"])]
    assert len(mine) == 1 and mine[0]["wo_state"] == "completed" and mine[0]["wo_status"] == "completed"
    assert "paid" not in str(mine[0]).lower()
    assert login(client).status_code == 302
