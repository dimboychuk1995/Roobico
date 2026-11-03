"""
Календарь ↔ work orders: полная синхронизация записи, привязанной к WO.

Статус записи следует за любой записью WO (оплата, снятие оплаты, смена
статуса, подтверждение, офисное сохранение, удаление WO); клиент/юнит —
за переносом WO; бригада будущих записей — за переназначением строк WO, и
наоборот: перетаскивание записи между дорожками переназначает строки WO.
Плюс правила привязанной записи в API календаря (409 на смену клиента,
ручной статус не перебивает факт, отвязка руками возвращает план), деньги
или работа на WO не дают «open» откатить запись, обратный синк бригады
только с правом редактировать WO, чужой id механика не сохраняется,
live-сигнатура и защита системных статусов.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import SHOP_A_DB, TENANT_A_DB, TEST_MONGO_URI, get_csrf_token, login

MECHANIC_EMAIL = "cal-hooks-mech@test.local"
MECHANIC2_EMAIL = "cal-hooks-mech-2@test.local"
DISPATCHER_EMAIL = "cal-hooks-dispatcher@test.local"
MECHANIC_PASSWORD = "password123"
CUSTOMER_NAME = "Hooks Fleet LLC"
TARGET_NAME = "Hooks Target LLC"
# Номера WO модуля — 94001…94099: 95001 занят tests/test_permissions_enforcement.py,
# 90000+ — tests/test_calendar_wo_sync.py (поиск по номеру идёт по общей shop-базе).
# Записи, созданные через API, собираются сюда — teardown удаляет их по id
# (у записи без клиента и без WO другого ключа нет).
_EVENT_IDS: list[ObjectId] = []


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


def _mechanic_doc(seed, shop_a, email, first, last):
    return {
        "_id": ObjectId(), "email": email,
        "password_hash": generate_password_hash(MECHANIC_PASSWORD),
        "first_name": first, "last_name": last, "is_active": True,
        "tenant_id": seed["tenant_a"]["_id"], "shop_ids": [str(shop_a["_id"])],
        "role": "mechanic", "created_at": _now(),
    }


@pytest.fixture(scope="module")
def hooks_seed(app, seed, mongo):
    """Два механика, клиент с юнитом (VIN — для переноса) и целевой клиент в магазине A."""
    from app.constants.permissions import build_default_roles

    master = mongo["roobico_test_master"]
    tdb = mongo[TENANT_A_DB]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = _now()

    mechanic_role = next(r for r in build_default_roles() if r["key"] == "mechanic")
    tdb.roles.update_one({"key": "mechanic"}, {"$set": mechanic_role}, upsert=True)

    mechanic = _mechanic_doc(seed, shop_a, MECHANIC_EMAIL, "Hook", "Mechanic")
    second = _mechanic_doc(seed, shop_a, MECHANIC2_EMAIL, "Second", "Hooks")
    master.users.insert_many([mechanic, second])

    customer = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "tenant_id": seed["tenant_a"]["_id"],
        "company_name": CUSTOMER_NAME, "taxable": False, "is_active": True, "created_at": now,
    }
    target = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "tenant_id": seed["tenant_a"]["_id"],
        "company_name": TARGET_NAME, "taxable": False, "is_active": True, "created_at": now,
    }
    shop_db.customers.insert_many([customer, target])
    unit = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "customer_id": customer["_id"],
        "vin": "HOOKSVIN000000001", "unit_number": "HK-1", "year": 2021, "make": "Volvo", "model": "VNL",
        "mileage": 150000, "is_active": True, "created_at": now,
    }
    shop_db.units.insert_one(unit)

    yield {"mechanic": mechanic, "second": second, "customer": customer, "target": target, "unit": unit, "shop": shop_a}

    customer_ids = [customer["_id"], target["_id"]]
    wo_ids = [w["_id"] for w in shop_db.work_orders.find({"customer_id": {"$in": customer_ids}}, {"_id": 1})]
    shop_db.calendar_events.delete_many({"$or": [
        {"_id": {"$in": list(_EVENT_IDS)}}, {"customer_id": {"$in": customer_ids}}, {"work_order_id": {"$in": wo_ids}},
    ]})
    shop_db.work_order_payments.delete_many({"work_order_id": {"$in": wo_ids}})
    shop_db.wo_time_logs.delete_many({"work_order_id": {"$in": wo_ids}})
    shop_db.work_orders.delete_many({"_id": {"$in": wo_ids}})
    shop_db.units.delete_many({"customer_id": {"$in": customer_ids}})
    shop_db.customers.delete_many({"_id": {"$in": customer_ids}})
    shop_db.calendar_settings.delete_one({"key": "statuses"})
    master.users.delete_many({"_id": {"$in": [mechanic["_id"], second["_id"]]}})


def _json(client, method, url, payload=None):
    token = get_csrf_token(client)
    fn = getattr(client, method)
    return fn(url, json=payload if payload is not None else {}, headers={"X-CSRFToken": token})


def _future_weekday(weeks_ahead: int, weekday: int = 2, hour: int = 12) -> datetime:
    """День недели через N недель (отрицательное N — в прошлом). У каждого
    теста своя неделя, чтобы бригада не упиралась в проверку занятости."""
    today = _now().replace(hour=hour, minute=0, second=0, microsecond=0)
    monday = today - timedelta(days=today.weekday())
    return monday + timedelta(weeks=weeks_ahead, days=weekday)


def _week_range(day: datetime):
    monday = day - timedelta(days=day.weekday())
    return monday.strftime("%Y-%m-%dT00:00:00"), (monday + timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")


def _labor(number, suffix="", assigned=None, **extra):
    labor = {
        "description": "Replace brakes", "hours": "2", "rate_code": "standard",
        "assigned_mechanics": assigned or [],
    }
    labor.update(extra)
    return {"labor_id": f"L{number}{suffix}", "labor": labor, "parts": []}


def _assigned(mechanic, percent=100.0):
    return [{"user_id": mechanic["_id"], "name": f"{mechanic['first_name']} {mechanic['last_name']}", "role": "mechanic", "percent": percent}]


def _insert_wo(shop_db, hooks_seed, number, status="open", labors=None, customer=None, unit=None, **extra):
    now = _now()
    customer = customer or hooks_seed["customer"]
    doc = {
        "_id": ObjectId(), "shop_id": hooks_seed["shop"]["_id"], "wo_number": number,
        "customer_id": customer["_id"], "unit_id": (unit or hooks_seed["unit"])["_id"],
        "status": status, "is_active": True,
        "labors": labors if labors is not None else [_labor(number, assigned=_assigned(hooks_seed["mechanic"]))],
        "totals": {"grand_total": 300.0, "labors": []},
        "work_order_date": now, "created_at": now, "updated_at": now,
    }
    doc.update(extra)
    shop_db.work_orders.insert_one(doc)
    return doc


def _create_event(client, day, hour, *, work_order_id=None, mechanic_ids=None, title="", hour_len=2, **extra):
    """Запись календаря от имени владельца (возвращает JSON ответа)."""
    assert login(client).status_code == 302
    d = day.strftime("%Y-%m-%d")
    body = {"start_time": f"{d}T{hour:02d}:00:00", "end_time": f"{d}T{hour + hour_len:02d}:00:00", "title": title}
    if work_order_id:
        body["work_order_id"] = str(work_order_id)
    if mechanic_ids is not None:
        body["mechanic_ids"] = [str(m) for m in mechanic_ids]
    body.update(extra)
    resp = _json(client, "post", "/calendar/api/events", body)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    created = resp.get_json()
    _EVENT_IDS.append(ObjectId(created["id"]))
    return created


def _event(shop_db, event_id):
    return shop_db.calendar_events.find_one({"_id": ObjectId(event_id)})


def _update_payload(wo, assigned_user_ids=None, save_status=None):
    """Payload офисной формы (serializeBlocks): плоские поля + назначения."""
    labors = []
    for block in wo["labors"]:
        labors.append({
            "labor_id": block["labor_id"], "labor_description": block["labor"]["description"],
            "labor_hours": block["labor"]["hours"], "labor_rate_code": "standard", "labor_full_total": 200,
            "assigned_mechanics": [{"user_id": str(uid), "percent": 100} for uid in (assigned_user_ids or [])],
            "issue_description": "", "parts": [],
        })
    payload = {"labors": labors, "totals": {}}
    if save_status:
        payload["save_status"] = save_status
    return payload


# ── статус записи следует за деньгами и статусом WO ──────────────────

def test_payment_completes_appointment_and_unpaid_reverts_it(client, hooks_seed, mongo):
    """Оплата через эндпоинт платежа → completed; «отметить неоплаченным» и
    удаление платежа возвращают WO в open → запись снова scheduled."""
    shop_db = mongo[SHOP_A_DB]
    wo = _insert_wo(shop_db, hooks_seed, 94001)
    event_id = _create_event(client, _future_weekday(40), 9, work_order_id=wo["_id"])["id"]
    assert _event(shop_db, event_id)["status"] == "scheduled"

    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/payment", {"amount": 300, "payment_method": "cash"})
    assert resp.status_code == 200 and resp.get_json()["status"] == "paid", resp.get_data(as_text=True)
    assert _event(shop_db, event_id)["status"] == "completed"

    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/status", {"status": "open"})
    assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_data(as_text=True)
    assert _event(shop_db, event_id)["status"] == "scheduled"

    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/payment", {"amount": 300, "payment_method": "cash"})
    assert resp.get_json()["status"] == "paid"
    assert _event(shop_db, event_id)["status"] == "completed"
    payment_id = resp.get_json()["payment_id"]
    resp = _json(client, "post", f"/work_orders/api/payments/{payment_id}/delete")
    assert resp.status_code == 200 and resp.get_json()["status"] == "open", resp.get_data(as_text=True)
    assert _event(shop_db, event_id)["status"] == "scheduled"

    # Ручной scheduled/confirmed синк не трогает; paid через статус — completed.
    _json(client, "put", f"/calendar/api/events/{event_id}", {"status": "confirmed"})
    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/status", {"status": "paid"})
    assert resp.status_code == 200
    assert _event(shop_db, event_id)["status"] == "completed"


def test_bulk_payment_syncs_every_paid_work_order(client, hooks_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    wo_a = _insert_wo(shop_db, hooks_seed, 94002)
    wo_b = _insert_wo(shop_db, hooks_seed, 94003)
    day = _future_weekday(41)
    ev_a = _create_event(client, day, 9, work_order_id=wo_a["_id"])["id"]
    ev_b = _create_event(client, day, 13, work_order_id=wo_b["_id"])["id"]

    resp = _json(client, "post", "/work_orders/api/bulk-payments", {
        "customer_id": str(hooks_seed["customer"]["_id"]), "payment_method": "check",
        "allocations": [{"work_order_id": str(wo_a["_id"]), "amount": 300}, {"work_order_id": str(wo_b["_id"]), "amount": 300}],
    })
    assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_data(as_text=True)
    assert resp.get_json()["invoices_paid_in_full"] == 2
    assert _event(shop_db, ev_a)["status"] == "completed"
    assert _event(shop_db, ev_b)["status"] == "completed"


def test_confirm_and_unconfirm_follow(client, hooks_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    wo = _insert_wo(shop_db, hooks_seed, 94004)
    event_id = _create_event(client, _future_weekday(42), 9, work_order_id=wo["_id"])["id"]

    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/confirm", {"confirmed": True})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _event(shop_db, event_id)["status"] == "completed"

    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/confirm", {"confirmed": False})
    assert resp.status_code == 200
    assert _event(shop_db, event_id)["status"] == "scheduled"


def test_office_save_in_progress_then_open(client, hooks_seed, mongo):
    """«Save In Progress» в офисе → in_progress; обычный Save (open) возвращает план."""
    shop_db = mongo[SHOP_A_DB]
    wo = _insert_wo(shop_db, hooks_seed, 94005)
    event_id = _create_event(client, _future_weekday(43), 9, work_order_id=wo["_id"])["id"]
    mech_id = hooks_seed["mechanic"]["_id"]

    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/update", _update_payload(wo, [mech_id], save_status="in_progress"))
    assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "in_progress"
    assert _event(shop_db, event_id)["status"] == "in_progress"

    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/update", _update_payload(wo, [mech_id], save_status="open"))
    assert resp.status_code == 200 and resp.get_json()["status"] == "open"
    assert _event(shop_db, event_id)["status"] == "scheduled"


# ── клиент/юнит записи следуют за WO ─────────────────────────────────

def test_transfer_moves_linked_appointment_to_new_customer_and_unit(client, hooks_seed, mongo):
    """Перенос WO деактивирует исходный юнит (копия уезжает к новому клиенту),
    поэтому у теста свой юнит — общий HK-1 нужен остальным живым."""
    shop_db = mongo[SHOP_A_DB]
    own_unit = {
        "_id": ObjectId(), "shop_id": hooks_seed["shop"]["_id"], "customer_id": hooks_seed["customer"]["_id"],
        "vin": "HOOKSVIN000000002", "unit_number": "HK-T", "year": 2020, "make": "Mack", "model": "Anthem",
        "mileage": 90000, "is_active": True, "created_at": _now(),
    }
    shop_db.units.insert_one(own_unit)
    wo = _insert_wo(shop_db, hooks_seed, 94006, unit=own_unit)
    created = _create_event(client, _future_weekday(44), 9, work_order_id=wo["_id"])
    assert created["customer_id"] == str(hooks_seed["customer"]["_id"])
    assert created["customer_label"] == CUSTOMER_NAME
    assert "HK-T" in created["unit_label"]

    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/transfer", {"customer_id": str(hooks_seed["target"]["_id"])})
    assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["unit_action"] == "moved"

    event = _event(shop_db, created["id"])
    assert event["customer_id"] == hooks_seed["target"]["_id"]
    assert event["customer_label"] == TARGET_NAME
    assert event["unit_id"] == ObjectId(body["unit_id"]) and event["unit_id"] != own_unit["_id"]
    assert "HK-T" in event["unit_label"]
    assert event["work_order_id"] == wo["_id"]
    # Общий юнит модуля не тронут.
    assert shop_db.units.find_one({"_id": hooks_seed["unit"]["_id"]})["is_active"] is True


def test_wo_customer_change_in_office_refreshes_appointment(client, hooks_seed, mongo):
    """Смена юнита/клиента на самом WO (api_work_order_update) приезжает в запись."""
    shop_db = mongo[SHOP_A_DB]
    other_unit = {
        "_id": ObjectId(), "shop_id": hooks_seed["shop"]["_id"], "customer_id": hooks_seed["customer"]["_id"],
        "unit_number": "HK-2", "year": 2019, "make": "Kenworth", "model": "T680", "is_active": True, "created_at": _now(),
    }
    shop_db.units.insert_one(other_unit)
    wo = _insert_wo(shop_db, hooks_seed, 94007)
    event_id = _create_event(client, _future_weekday(45), 9, work_order_id=wo["_id"])["id"]
    assert _event(shop_db, event_id)["unit_id"] == hooks_seed["unit"]["_id"]

    payload = _update_payload(wo, [hooks_seed["mechanic"]["_id"]])
    payload.update({"customer_id": str(hooks_seed["customer"]["_id"]), "unit_id": str(other_unit["_id"])})
    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/update", payload)
    assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_data(as_text=True)
    event = _event(shop_db, event_id)
    assert event["unit_id"] == other_unit["_id"]
    assert "HK-2" in event["unit_label"] and "Kenworth" in event["unit_label"]


def test_api_events_shows_live_customer_label(client, hooks_seed, mongo):
    """Переименованный клиент виден в календаре новым именем; title без заметки следует за ним."""
    shop_db = mongo[SHOP_A_DB]
    day = _future_weekday(46)
    created = _create_event(
        client, day, 9, customer_id=str(hooks_seed["customer"]["_id"]), customer_label=CUSTOMER_NAME,
    )
    assert created["title"] == CUSTOMER_NAME
    shop_db.customers.update_one({"_id": hooks_seed["customer"]["_id"]}, {"$set": {"company_name": "Hooks Fleet Renamed"}})
    try:
        start, end = _week_range(day)
        rows = client.get(f"/calendar/api/events?start={start}&end={end}").get_json()
        ev = next(e for e in rows if e["id"] == created["id"])
        assert ev["customer_label"] == "Hooks Fleet Renamed"
        assert ev["title"] == "Hooks Fleet Renamed"
    finally:
        shop_db.customers.update_one({"_id": hooks_seed["customer"]["_id"]}, {"$set": {"company_name": CUSTOMER_NAME}})


# ── бригада: WO → будущие записи и обратно ───────────────────────────

def test_office_reassignment_moves_future_appointment_unless_busy(client, hooks_seed, mongo):
    """Офис переназначил строку на другого механика: будущая запись переезжает
    в его дорожку, занятый слот — остаётся, прошлая запись не трогается."""
    shop_db = mongo[SHOP_A_DB]
    m1, m2 = hooks_seed["mechanic"], hooks_seed["second"]
    wo_free = _insert_wo(shop_db, hooks_seed, 94008)
    wo_busy = _insert_wo(shop_db, hooks_seed, 94009)
    day = _future_weekday(47)
    free_ev = _create_event(client, day, 9, work_order_id=wo_free["_id"], mechanic_ids=[m1["_id"]])["id"]
    past_ev = _create_event(client, _future_weekday(-2), 9, work_order_id=wo_free["_id"], mechanic_ids=[m1["_id"]])["id"]
    busy_ev = _create_event(client, day + timedelta(days=1), 9, work_order_id=wo_busy["_id"], mechanic_ids=[m1["_id"]])["id"]
    # m2 занят в это время другой записью — план по wo_busy к нему не переедет.
    _create_event(client, day + timedelta(days=1), 10, mechanic_ids=[m2["_id"]], title="Other job", hour_len=1)

    for wo in (wo_free, wo_busy):
        resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/update", _update_payload(wo, [m2["_id"]]))
        assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_data(as_text=True)

    moved = _event(shop_db, free_ev)
    assert moved["mechanic_ids"] == [m2["_id"]]
    assert moved["mechanic_id"] == m2["_id"] and moved["mechanic_name"] == "Second Hooks"
    assert [m["id"] for m in moved["mechanics"]] == [m2["_id"]]
    assert _event(shop_db, past_ev)["mechanic_ids"] == [m1["_id"]]
    assert _event(shop_db, busy_ev)["mechanic_ids"] == [m1["_id"]]


def test_calendar_drag_updates_wo_assignments(client, hooks_seed, mongo):
    """PUT mechanic_ids на привязанной записи → назначения строк WO: без
    назначений и на прежнюю бригаду — переписываются, чужие и tracked — нет;
    updated_at WO сдвигается. WO с завершённым временем не трогаем."""
    shop_db = mongo[SHOP_A_DB]
    m1, m2 = hooks_seed["mechanic"], hooks_seed["second"]
    stranger = ObjectId()
    wo = _insert_wo(shop_db, hooks_seed, 94010, labors=[
        _labor(94010, "a", assigned=_assigned(m1)),
        _labor(94010, "b"),
        _labor(94010, "c", assigned=[{"user_id": stranger, "name": "Someone", "role": "mechanic", "percent": 100.0}]),
        _labor(94010, "d", assigned=_assigned(m1), hours_source="tracked"),
    ])
    before = shop_db.work_orders.find_one({"_id": wo["_id"]})["updated_at"]
    event_id = _create_event(client, _future_weekday(48), 9, work_order_id=wo["_id"], mechanic_ids=[m1["_id"]])["id"]

    resp = _json(client, "put", f"/calendar/api/events/{event_id}", {"mechanic_ids": [str(m2["_id"])]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["mechanic_ids"] == [str(m2["_id"])]

    saved = shop_db.work_orders.find_one({"_id": wo["_id"]})
    by_id = {b["labor_id"]: b["labor"]["assigned_mechanics"] for b in saved["labors"]}
    assert [(a["user_id"], a["percent"], a["name"]) for a in by_id["L94010a"]] == [(m2["_id"], 100.0, "Second Hooks")]
    assert [(a["user_id"], a["percent"]) for a in by_id["L94010b"]] == [(m2["_id"], 100.0)]
    assert [a["user_id"] for a in by_id["L94010c"]] == [stranger]
    assert [a["user_id"] for a in by_id["L94010d"]] == [m1["_id"]]
    assert saved["updated_at"] > before

    # Бригада из двух: строки, назначенные на прежнюю бригаду (m2), делятся поровну.
    resp = _json(client, "put", f"/calendar/api/events/{event_id}", {"mechanic_ids": [str(m1["_id"]), str(m2["_id"])]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    saved = shop_db.work_orders.find_one({"_id": wo["_id"]})
    crew = {a["user_id"]: a["percent"] for a in next(b for b in saved["labors"] if b["labor_id"] == "L94010a")["labor"]["assigned_mechanics"]}
    assert crew == {m1["_id"]: 50.0, m2["_id"]: 50.0}

    # Работа уже шла (завершённый тайм-лог) — назначения WO драг не переписывает.
    worked = _insert_wo(shop_db, hooks_seed, 94011)
    started = _now() - timedelta(days=1)
    shop_db.wo_time_logs.insert_one({
        "shop_id": hooks_seed["shop"]["_id"], "work_order_id": worked["_id"], "wo_number": 94011,
        "labor_id": "L94011", "user_id": m1["_id"], "user_name": "Hook Mechanic",
        "started_at": started, "stopped_at": started + timedelta(hours=1), "seconds": 3600,
        "created_at": started, "updated_at": started,
    })
    worked_ev = _create_event(client, _future_weekday(48), 13, work_order_id=worked["_id"], mechanic_ids=[m1["_id"]])["id"]
    resp = _json(client, "put", f"/calendar/api/events/{worked_ev}", {"mechanic_ids": [str(m2["_id"])]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    saved = shop_db.work_orders.find_one({"_id": worked["_id"]})
    assert [a["user_id"] for a in saved["labors"][0]["labor"]["assigned_mechanics"]] == [m1["_id"]]


# ── удаление WO, правила привязанной записи в API календаря ──────────

def test_wo_delete_clears_number_and_reverts_status(client, hooks_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    wo = _insert_wo(shop_db, hooks_seed, 94012, status="in_progress")
    created = _create_event(client, _future_weekday(49), 9, work_order_id=wo["_id"])
    assert created["status"] == "in_progress" and created["wo_number"] == 94012

    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/delete")
    assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_data(as_text=True)
    event = _event(shop_db, created["id"])
    assert event["work_order_id"] is None and event["wo_number"] is None
    assert event["status"] == "scheduled"


def test_linked_event_customer_or_unit_change_returns_409(client, hooks_seed, mongo):
    wo = _insert_wo(mongo[SHOP_A_DB], hooks_seed, 94013)
    event_id = _create_event(client, _future_weekday(50), 9, work_order_id=wo["_id"])["id"]

    resp = _json(client, "put", f"/calendar/api/events/{event_id}", {"customer_id": str(hooks_seed["target"]["_id"]), "customer_label": TARGET_NAME})
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert resp.get_json()["error"] == "This appointment is linked to WO #94013 — change the customer or unit on the work order"
    resp = _json(client, "put", f"/calendar/api/events/{event_id}", {"unit_id": str(ObjectId())})
    assert resp.status_code == 409
    # Те же клиент/юнит, что у WO (модалка шлёт все поля) — обычная правка.
    resp = _json(client, "put", f"/calendar/api/events/{event_id}", {
        "customer_id": str(hooks_seed["customer"]["_id"]), "unit_id": str(hooks_seed["unit"]["_id"]), "title": "Renamed",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["title"] == "Renamed"
    # Не привязанная запись клиента меняет свободно.
    free = _create_event(client, _future_weekday(50), 13, customer_id=str(hooks_seed["customer"]["_id"]), customer_label=CUSTOMER_NAME)
    resp = _json(client, "put", f"/calendar/api/events/{free['id']}", {"customer_id": str(hooks_seed["target"]["_id"]), "customer_label": TARGET_NAME})
    assert resp.status_code == 200 and resp.get_json()["customer_id"] == str(hooks_seed["target"]["_id"])


def test_manual_status_on_running_wo_keeps_derived(client, hooks_seed, mongo):
    """Привязанная запись идущего WO: ручной completed заменяется фактом
    (in_progress), confirmed при создании — тоже; cancelled — всегда можно."""
    wo = _insert_wo(mongo[SHOP_A_DB], hooks_seed, 94014, status="in_progress")
    created = _create_event(client, _future_weekday(51), 9, work_order_id=wo["_id"], status="confirmed")
    assert created["status"] == "in_progress"

    resp = _json(client, "put", f"/calendar/api/events/{created['id']}", {"status": "completed"})
    assert resp.status_code == 200 and resp.get_json()["status"] == "in_progress", resp.get_data(as_text=True)
    resp = _json(client, "put", f"/calendar/api/events/{created['id']}", {"status": "cancelled"})
    assert resp.status_code == 200 and resp.get_json()["status"] == "cancelled"
    # Отменённую запись синк не трогает.
    _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/status", {"status": "paid"})
    assert _event(mongo[SHOP_A_DB], created["id"])["status"] == "cancelled"


def test_link_refuses_event_already_linked_to_another_active_wo(app, hooks_seed, mongo):
    from app.blueprints.calendar.routes import link_event_to_work_order

    shop_db = mongo[SHOP_A_DB]
    wo_a = _insert_wo(shop_db, hooks_seed, 94015)
    wo_b = _insert_wo(shop_db, hooks_seed, 94016, status="in_progress")
    day = _future_weekday(52)
    event = {
        "_id": ObjectId(), "shop_id": hooks_seed["shop"]["_id"], "title": CUSTOMER_NAME,
        "start_time": day.replace(hour=9, tzinfo=None), "end_time": day.replace(hour=10, tzinfo=None),
        "status": "scheduled", "customer_id": hooks_seed["customer"]["_id"], "customer_label": CUSTOMER_NAME,
        "unit_id": None, "unit_label": "", "mechanics": [], "mechanic_ids": [], "mechanic_id": None, "mechanic_name": "",
        "presets": [], "work_order_id": wo_a["_id"], "wo_number": 94015, "created_at": _now(), "updated_at": _now(),
    }
    shop_db.calendar_events.insert_one(event)
    with app.app_context():
        assert link_event_to_work_order(shop_db, hooks_seed["shop"], event["_id"], wo_b["_id"], 94016) is False
        saved = _event(shop_db, event["_id"])
        assert saved["work_order_id"] == wo_a["_id"] and saved["wo_number"] == 94015
        # Старый WO удалён — перепривязка разрешена, клиент/юнит и статус — по новому WO.
        shop_db.work_orders.update_one({"_id": wo_a["_id"]}, {"$set": {"is_active": False}})
        assert link_event_to_work_order(shop_db, hooks_seed["shop"], event["_id"], wo_b["_id"], 94016) is True
    saved = _event(shop_db, event["_id"])
    assert saved["work_order_id"] == wo_b["_id"] and saved["wo_number"] == 94016
    assert saved["unit_id"] == hooks_seed["unit"]["_id"] and "HK-1" in saved["unit_label"]
    assert saved["status"] == "in_progress"


# ── список записей на странице WO, live-сигнатура, системные статусы ──

def test_linked_appointments_exposed_on_work_order_payloads(client, hooks_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    m1 = hooks_seed["mechanic"]
    wo = _insert_wo(shop_db, hooks_seed, 94017, status="in_progress")
    day = _future_weekday(53)
    noted = _create_event(client, day, 9, work_order_id=wo["_id"], mechanic_ids=[m1["_id"]], title="Customer waits",
                          presets=[{"id": str(ObjectId()), "name": "Oil change"}])
    _create_event(client, day, 14, work_order_id=wo["_id"], status="cancelled")
    plain = _create_event(client, day + timedelta(days=1), 9, work_order_id=wo["_id"], title="")
    # title без заметки = лейбл клиента → note пустая.
    shop_db.calendar_events.update_one({"_id": ObjectId(plain["id"])}, {"$set": {"title": CUSTOMER_NAME}})

    def _check(items):
        assert [i["id"] for i in items] == [noted["id"], plain["id"]]  # отменённая не отдаётся, порядок по времени
        first = items[0]
        assert first["start_time"] == f"{day.strftime('%Y-%m-%d')}T09:00:00" and first["end_time"].endswith("T11:00:00")
        assert first["status"] == "in_progress" and first["note"] == "Customer waits" and first["title"] == "Customer waits"
        assert first["mechanics"] == [{"id": str(m1["_id"]), "name": "Hook Mechanic"}]
        assert first["presets"][0]["name"] == "Oil change"
        assert items[1]["note"] == "" and items[1]["title"] == CUSTOMER_NAME
        assert set(first) == {"id", "start_time", "end_time", "status", "title", "note", "mechanics", "presets"}

    # Офисная мобильная ветка.
    resp = client.get(f"/api/mobile/work_orders/{wo['_id']}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    _check(resp.get_json()["appointments"])
    # Офисная страница деталей рендерится с контекстом.
    assert client.get(f"/work_orders/details?work_order_id={wo['_id']}").status_code == 200

    # Механик: веб-эндпоинт и мобильная ветка — безденежный payload с записями.
    assert login(client, email=MECHANIC_EMAIL, password=MECHANIC_PASSWORD).status_code == 302
    for url in (f"/work_orders/api/mechanic/work_orders/{wo['_id']}", f"/api/mobile/work_orders/{wo['_id']}"):
        resp = client.get(url)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        _check(body["appointments"])
        assert "grand_total" not in body and "price" not in str(body["appointments"])
    assert login(client).status_code == 302


def test_live_signature_changes_after_event_and_wo_edits(client, hooks_seed, mongo):
    wo = _insert_wo(mongo[SHOP_A_DB], hooks_seed, 94018)
    day = _future_weekday(54)
    created = _create_event(client, day, 9, work_order_id=wo["_id"])
    start, end = _week_range(day)
    url = f"/calendar/api/live_signature?start={start}&end={end}"

    resp = client.get(url)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    first = resp.get_json()["signature"]
    assert first and client.get(url).get_json()["signature"] == first  # стабильна без изменений

    assert _json(client, "put", f"/calendar/api/events/{created['id']}", {"title": "Edited"}).status_code == 200
    second = client.get(url).get_json()["signature"]
    assert second != first

    assert _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/status", {"status": "in_progress"}).status_code == 200
    third = client.get(url).get_json()["signature"]
    assert third != second
    # Без диапазона — тоже работает (вся база магазина).
    assert client.get("/calendar/api/live_signature").get_json()["signature"]


def test_save_statuses_refuses_to_drop_system_keys(client, hooks_seed, mongo):
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    keep = [
        {"key": "scheduled", "label": "Booked", "color": "#111111"},
        {"key": "in_progress", "label": "Working", "color": "#222222"},
        {"key": "cancelled", "label": "Cancelled", "color": "#333333"},
    ]
    resp = _json(client, "put", "/calendar/api/statuses", {"statuses": keep})
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert "completed" in resp.get_json()["error"]
    assert shop_db.calendar_settings.find_one({"key": "statuses"}) is None

    # Переименовать/перекрасить системные и убрать confirmed — можно.
    resp = _json(client, "put", "/calendar/api/statuses", {"statuses": keep + [{"key": "completed", "label": "Done", "color": "#444444"}]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert [s["key"] for s in resp.get_json()] == ["scheduled", "in_progress", "cancelled", "completed"]
    assert {s["key"]: s["label"] for s in client.get("/calendar/api/statuses").get_json()}["scheduled"] == "Booked"
    shop_db.calendar_settings.delete_one({"key": "statuses"})


def _update_payload_crew(wo, mechanics):
    """Payload офисной формы с бригадой на каждой строке — доли поровну."""
    payload = _update_payload(wo)
    share = round(100.0 / len(mechanics), 2)
    for labor in payload["labors"]:
        labor["assigned_mechanics"] = [{"user_id": str(m["_id"]), "percent": share} for m in mechanics]
    return payload


def test_office_reassignment_respects_split_crew_plans(client, hooks_seed, mongo, seed):
    """Разбивка «m1 в среду, m2 в четверг» не склеивается в общую бригаду:
    сохранение WO с той же бригадой ничего не двигает; замена m2 → m3
    переезжает только запись m2 — к m3, а не к уже запланированному m1;
    снятие m3 с WO отдаёт его запись оставшемуся m1."""
    shop_db = mongo[SHOP_A_DB]
    master = mongo["roobico_test_master"]
    m1, m2 = hooks_seed["mechanic"], hooks_seed["second"]
    m3 = _mechanic_doc(seed, hooks_seed["shop"], "cal-hooks-mech-3@test.local", "Third", "Hooks")
    master.users.insert_one(m3)
    try:
        wo = _insert_wo(shop_db, hooks_seed, 94030, labors=[_labor(94030, assigned=_assigned(m1, 50.0) + _assigned(m2, 50.0))])
        day = _future_weekday(56)
        ev1 = _create_event(client, day, 9, work_order_id=wo["_id"], mechanic_ids=[m1["_id"]])["id"]
        ev2 = _create_event(client, day + timedelta(days=1), 9, work_order_id=wo["_id"], mechanic_ids=[m2["_id"]])["id"]
        url = f"/work_orders/api/work_orders/{wo['_id']}/update"

        # Та же бригада — записи не трогаем.
        resp = _json(client, "post", url, _update_payload_crew(wo, [m1, m2]))
        assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_data(as_text=True)
        assert _event(shop_db, ev1)["mechanic_ids"] == [m1["_id"]]
        assert _event(shop_db, ev2)["mechanic_ids"] == [m2["_id"]]

        # m2 заменили на m3: запись m2 уходит к m3 (m1 уже запланирован своей записью).
        resp = _json(client, "post", url, _update_payload_crew(wo, [m1, m3]))
        assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_data(as_text=True)
        assert _event(shop_db, ev1)["mechanic_ids"] == [m1["_id"]]
        moved = _event(shop_db, ev2)
        assert moved["mechanic_ids"] == [m3["_id"]]
        assert moved["mechanic_name"] == "Third Hooks"

        # m3 сняли с WO совсем: его запись достаётся оставшемуся m1.
        resp = _json(client, "post", url, _update_payload_crew(wo, [m1]))
        assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_data(as_text=True)
        assert _event(shop_db, ev1)["mechanic_ids"] == [m1["_id"]]
        assert _event(shop_db, ev2)["mechanic_ids"] == [m1["_id"]]
    finally:
        master.users.delete_one({"_id": m3["_id"]})


def test_timer_stop_does_not_move_future_appointments(client, hooks_seed, mongo):
    """Назначения по таймеру (сессия m2 на WO, запланированном m1) меняют
    статус записи, но не её бригаду: план переезжает только при явном
    переназначении офисом."""
    shop_db = mongo[SHOP_A_DB]
    m1 = hooks_seed["mechanic"]
    wo = _insert_wo(shop_db, hooks_seed, 94031, status="in_progress", labors=[_labor(94031, assigned=_assigned(m1))])
    future_ev = _create_event(client, _future_weekday(57), 9, work_order_id=wo["_id"], mechanic_ids=[m1["_id"]])["id"]
    assert login(client, email=MECHANIC2_EMAIL, password=MECHANIC_PASSWORD).status_code == 302
    resp = _json(client, "post", "/work_orders/api/mechanic/timers/start", {"work_order_id": str(wo["_id"]), "labor_id": "L94031"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    resp = _json(client, "post", "/work_orders/api/mechanic/timers/stop", {})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    ev = _event(shop_db, future_ev)
    assert ev["mechanic_ids"] == [m1["_id"]]
    assert ev["status"] == "in_progress"


# ── деньги/работа на WO: «open» не откатывает запись ─────────────────

def test_partial_payment_or_time_log_keeps_appointment_in_progress(client, hooks_seed, mongo):
    """Частичная оплата переводит WO в open (payments._sync_work_order_payment_state),
    но работа идёт — запись остаётся in_progress; снятие платежа при идущем
    таймере — тоже; и только open без денег и без тайм-логов возвращает план
    в scheduled."""
    shop_db = mongo[SHOP_A_DB]
    m1 = hooks_seed["mechanic"]
    wo = _insert_wo(shop_db, hooks_seed, 94019, status="in_progress")
    event_id = _create_event(client, _future_weekday(58), 9, work_order_id=wo["_id"])["id"]
    assert _event(shop_db, event_id)["status"] == "in_progress"

    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/payment", {"amount": 100, "payment_method": "cash"})
    assert resp.status_code == 200 and resp.get_json()["status"] == "open", resp.get_data(as_text=True)
    payment_id = resp.get_json()["payment_id"]
    assert shop_db.work_orders.find_one({"_id": wo["_id"]})["status"] == "open"
    assert _event(shop_db, event_id)["status"] == "in_progress"

    # Платёж сняли, но таймер механика идёт — работа есть, запись не откатывается.
    started = _now() - timedelta(minutes=30)
    log_id = shop_db.wo_time_logs.insert_one({
        "shop_id": hooks_seed["shop"]["_id"], "work_order_id": wo["_id"], "wo_number": 94019,
        "labor_id": "L94019", "user_id": m1["_id"], "user_name": "Hook Mechanic",
        "started_at": started, "stopped_at": None, "seconds": None,
        "created_at": started, "updated_at": started,
    }).inserted_id
    try:
        resp = _json(client, "post", f"/work_orders/api/payments/{payment_id}/delete")
        assert resp.status_code == 200 and resp.get_json()["status"] == "open", resp.get_data(as_text=True)
        assert shop_db.work_order_payments.count_documents({"work_order_id": wo["_id"], "is_active": True}) == 0
        assert _event(shop_db, event_id)["status"] == "in_progress"
    finally:
        shop_db.wo_time_logs.delete_one({"_id": log_id})

    # Ни денег, ни работы: open — это «не начат», план возвращается.
    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/status", {"status": "open"})
    assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_data(as_text=True)
    assert _event(shop_db, event_id)["status"] == "scheduled"


def test_manual_unlink_reverts_synced_status_unless_explicit(client, hooks_seed, mongo):
    """Отвязка записи от WO руками (PUT work_order_id "") — как при удалении
    WO: номер снимается, in_progress/completed от синка → scheduled; явный
    статус в том же запросе важнее."""
    shop_db = mongo[SHOP_A_DB]
    wo = _insert_wo(shop_db, hooks_seed, 94020, status="in_progress")
    day = _future_weekday(59)
    first = _create_event(client, day, 9, work_order_id=wo["_id"])
    assert first["status"] == "in_progress" and first["wo_number"] == 94020

    resp = _json(client, "put", f"/calendar/api/events/{first['id']}", {"work_order_id": ""})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["work_order_id"] == "" and body["wo_number"] is None and body["status"] == "scheduled"
    saved = _event(shop_db, first["id"])
    assert saved["work_order_id"] is None and saved["wo_number"] is None and saved["status"] == "scheduled"

    second = _create_event(client, day, 13, work_order_id=wo["_id"])
    assert second["status"] == "in_progress"
    resp = _json(client, "put", f"/calendar/api/events/{second['id']}", {"work_order_id": "", "status": "completed"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "completed" and resp.get_json()["work_order_id"] == ""
    assert _event(shop_db, second["id"])["status"] == "completed"


def test_crew_sync_to_wo_requires_work_orders_edit(client, hooks_seed, mongo, seed):
    """Диспетчер с calendar.edit, но без work_orders.edit: перетаскивание
    привязанной записи меняет её бригаду, назначения строк WO (вход в
    payroll) остаются как были."""
    shop_db = mongo[SHOP_A_DB]
    master = mongo["roobico_test_master"]
    tdb = mongo[TENANT_A_DB]
    m1, m2 = hooks_seed["mechanic"], hooks_seed["second"]
    tdb.roles.update_one(
        {"key": "cal_dispatcher"},
        {"$set": {"key": "cal_dispatcher", "name": "Dispatcher", "is_system": False,
                  "permissions": ["calendar.view", "calendar.create", "calendar.edit", "work_orders.view"]}},
        upsert=True,
    )
    dispatcher = {
        "_id": ObjectId(), "email": DISPATCHER_EMAIL, "password_hash": generate_password_hash(MECHANIC_PASSWORD),
        "first_name": "Dispatch", "last_name": "Hooks", "is_active": True,
        "tenant_id": seed["tenant_a"]["_id"], "shop_ids": [str(hooks_seed["shop"]["_id"])],
        "role": "cal_dispatcher", "created_at": _now(),
    }
    master.users.insert_one(dispatcher)
    try:
        wo = _insert_wo(shop_db, hooks_seed, 94021)
        before = shop_db.work_orders.find_one({"_id": wo["_id"]})["updated_at"]
        event_id = _create_event(client, _future_weekday(60), 9, work_order_id=wo["_id"], mechanic_ids=[m1["_id"]])["id"]

        assert login(client, email=DISPATCHER_EMAIL, password=MECHANIC_PASSWORD).status_code == 302
        resp = _json(client, "put", f"/calendar/api/events/{event_id}", {"mechanic_ids": [str(m2["_id"])]})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["mechanic_ids"] == [str(m2["_id"])]
        assert _event(shop_db, event_id)["mechanic_ids"] == [m2["_id"]]
        saved = shop_db.work_orders.find_one({"_id": wo["_id"]})
        assert [a["user_id"] for a in saved["labors"][0]["labor"]["assigned_mechanics"]] == [m1["_id"]]
        assert saved["updated_at"] == before
    finally:
        master.users.delete_one({"_id": dispatcher["_id"]})
        tdb.roles.delete_one({"key": "cal_dispatcher"})


def test_calendar_drag_replaces_previous_crew_inside_wider_labor_crew(client, hooks_seed, mongo, seed):
    """Строка [m1, m2], запись [m1] перетащена к m3: в строке m1 уступает
    место m3, m2 остаётся, доли поровну; идущий (не только завершённый)
    таймер блокирует переписывание назначений."""
    shop_db = mongo[SHOP_A_DB]
    master = mongo["roobico_test_master"]
    m1, m2 = hooks_seed["mechanic"], hooks_seed["second"]
    m3 = _mechanic_doc(seed, hooks_seed["shop"], "cal-hooks-mech-4@test.local", "Fourth", "Hooks")
    master.users.insert_one(m3)
    log_id = None
    try:
        wo = _insert_wo(shop_db, hooks_seed, 94022, labors=[_labor(94022, assigned=_assigned(m1, 50.0) + _assigned(m2, 50.0))])
        day = _future_weekday(61)
        event_id = _create_event(client, day, 9, work_order_id=wo["_id"], mechanic_ids=[m1["_id"]])["id"]
        resp = _json(client, "put", f"/calendar/api/events/{event_id}", {"mechanic_ids": [str(m3["_id"])]})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assigned = shop_db.work_orders.find_one({"_id": wo["_id"]})["labors"][0]["labor"]["assigned_mechanics"]
        assert [(a["user_id"], a["percent"]) for a in assigned] == [(m3["_id"], 50.0), (m2["_id"], 50.0)]
        assert assigned[0]["name"] == "Fourth Hooks"

        # Таймер m1 идёт — работа началась, назначения WO драг не трогает.
        running = _insert_wo(shop_db, hooks_seed, 94023)
        started = _now() - timedelta(minutes=10)
        log_id = shop_db.wo_time_logs.insert_one({
            "shop_id": hooks_seed["shop"]["_id"], "work_order_id": running["_id"], "wo_number": 94023,
            "labor_id": "L94023", "user_id": m1["_id"], "user_name": "Hook Mechanic",
            "started_at": started, "stopped_at": None, "seconds": None,
            "created_at": started, "updated_at": started,
        }).inserted_id
        running_ev = _create_event(client, day, 13, work_order_id=running["_id"], mechanic_ids=[m1["_id"]])["id"]
        resp = _json(client, "put", f"/calendar/api/events/{running_ev}", {"mechanic_ids": [str(m3["_id"])]})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["mechanic_ids"] == [str(m3["_id"])]
        saved = shop_db.work_orders.find_one({"_id": running["_id"]})
        assert [a["user_id"] for a in saved["labors"][0]["labor"]["assigned_mechanics"]] == [m1["_id"]]
    finally:
        if log_id is not None:
            shop_db.wo_time_logs.delete_one({"_id": log_id})
        master.users.delete_one({"_id": m3["_id"]})


def test_office_reassignment_leaves_unassigned_appointment_alone(client, hooks_seed, mongo):
    """Запись без бригады (колонка «Unassigned») — осознанный план: офисное
    сохранение WO с другим механиком её не докомплектовывает; запись с
    выбывшим механиком при этом переезжает."""
    shop_db = mongo[SHOP_A_DB]
    m1, m2 = hooks_seed["mechanic"], hooks_seed["second"]
    wo = _insert_wo(shop_db, hooks_seed, 94024)
    day = _future_weekday(62)
    unassigned = _create_event(client, day, 9, work_order_id=wo["_id"], keep_unassigned=True)["id"]
    assert _event(shop_db, unassigned)["mechanic_ids"] == []
    planned = _create_event(client, day, 13, work_order_id=wo["_id"], mechanic_ids=[m1["_id"]])["id"]

    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/update", _update_payload(wo, [m2["_id"]]))
    assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_data(as_text=True)
    saved = _event(shop_db, unassigned)
    assert saved["mechanic_ids"] == [] and saved["mechanics"] == [] and saved["mechanic_id"] is None
    assert _event(shop_db, planned)["mechanic_ids"] == [m2["_id"]]


def test_unknown_mechanic_id_is_not_stored(client, hooks_seed, mongo):
    """Чужой/несуществующий id механика в POST — не назначаемый механик
    магазина: в бригаду записи не попадает ни он, ни присланное имя;
    настоящие механики из того же списка остаются."""
    shop_db = mongo[SHOP_A_DB]
    m1 = hooks_seed["mechanic"]
    stranger = str(ObjectId())
    day = _future_weekday(63)
    created = _create_event(client, day, 9, mechanics=[{"id": stranger, "name": "Nobody"}], title="Ghost crew")
    assert created["mechanics"] == [] and created["mechanic_ids"] == [] and created["mechanic_id"] == ""
    saved = _event(shop_db, created["id"])
    assert saved["mechanics"] == [] and saved["mechanic_ids"] == []
    assert saved["mechanic_id"] is None and saved["mechanic_name"] == ""

    legacy = _create_event(client, day, 11, mechanic_id=stranger, mechanic_name="Nobody", title="Legacy ghost")
    assert legacy["mechanic_ids"] == [] and legacy["mechanic_name"] == ""

    mixed = _create_event(client, day, 13, mechanic_ids=[stranger, m1["_id"]], title="Mixed crew")
    assert mixed["mechanic_ids"] == [str(m1["_id"])]
    assert [m["name"] for m in mixed["mechanics"]] == ["Hook Mechanic"]
