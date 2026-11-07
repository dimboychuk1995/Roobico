"""
Календарь ↔ work orders через реальные хуки.

Статус привязанной записи следует за WO: таймер механика → in_progress,
Done механика → completed, оплата → completed, менеджер вернул WO в open —
ничего не откатывается, cancelled не трогаем, нет целевого ключа среди
статусов магазина — синк молчит. Клиент/юнит записи следуют за переносом
WO и сменой юнита в офисе. Удаление WO снимает привязку (статус остаётся).
link_event_to_work_order не перепривязывает к другому живому WO. Список
записей на payload'ах WO (офис, механик, мобильное). Создание WO из
календаря (веб) и «Start work» из приложения механика привязывают запись.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import SHOP_A_DB, TENANT_A_DB, TEST_MONGO_URI, get_csrf_token, login

MECHANIC_EMAIL = "cal-sync-mech@test.local"
MECHANIC2_EMAIL = "cal-sync-mech-2@test.local"
PASSWORD = "password123"
CUSTOMER_NAME = "Sync Fleet LLC"
TARGET_NAME = "Sync Target LLC"
# Номера WO модуля — 94001…94099 (96xxx — test_calendar_api, 95001 — permissions).
WO_NUMBER_RANGE = {"$gte": 94000, "$lt": 94100}
# Недели модуля — январь 2032 и дальше (2031 — test_calendar_api).
BASE_MONDAY = date(2032, 1, 5)
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
        "_id": ObjectId(), "email": email, "password_hash": generate_password_hash(PASSWORD),
        "first_name": first, "last_name": last, "is_active": True,
        "tenant_id": seed["tenant_a"]["_id"], "shop_ids": [str(shop_a["_id"])],
        "role": "mechanic", "created_at": _now(),
    }


@pytest.fixture(scope="module")
def sync_seed(app, seed, mongo):
    """Два механика, клиент с юнитом (VIN — для переноса) и целевой клиент в магазине A."""
    from app.constants.permissions import build_default_roles

    master = mongo["roobico_test_master"]
    tdb = mongo[TENANT_A_DB]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = _now()

    mechanic_role = next(r for r in build_default_roles() if r["key"] == "mechanic")
    tdb.roles.update_one({"key": "mechanic"}, {"$set": mechanic_role}, upsert=True)

    mechanic = _mechanic_doc(seed, shop_a, MECHANIC_EMAIL, "Sync", "Mechanic")
    second = _mechanic_doc(seed, shop_a, MECHANIC2_EMAIL, "Second", "Sync")
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
        "vin": "SYNCVIN0000000001", "unit_number": "SY-1", "year": 2021, "make": "Volvo", "model": "VNL",
        "mileage": 150000, "is_active": True, "created_at": now,
    }
    shop_db.units.insert_one(unit)

    yield {"mechanic": mechanic, "second": second, "customer": customer, "target": target, "unit": unit, "shop": shop_a}

    customer_ids = [customer["_id"], target["_id"]]
    wo_ids = [w["_id"] for w in shop_db.work_orders.find(
        {"$or": [{"customer_id": {"$in": customer_ids}}, {"wo_number": WO_NUMBER_RANGE}]}, {"_id": 1}
    )]
    shop_db.calendar_events.delete_many({"$or": [
        {"_id": {"$in": list(_EVENT_IDS)}}, {"customer_id": {"$in": customer_ids}}, {"work_order_id": {"$in": wo_ids}},
    ]})
    shop_db.work_order_payments.delete_many({"work_order_id": {"$in": wo_ids}})
    shop_db.wo_time_logs.delete_many({"$or": [{"work_order_id": {"$in": wo_ids}}, {"user_id": {"$in": [mechanic["_id"], second["_id"]]}}]})
    shop_db.work_orders.delete_many({"_id": {"$in": wo_ids}})
    shop_db.units.delete_many({"customer_id": {"$in": customer_ids}})
    shop_db.customers.delete_many({"_id": {"$in": customer_ids}})
    shop_db.calendar_settings.delete_one({"key": "statuses"})
    master.users.delete_many({"_id": {"$in": [mechanic["_id"], second["_id"]]}})


# ── helpers ──────────────────────────────────────────────────

def _json(client, method, url, payload=None):
    token = get_csrf_token(client)
    return getattr(client, method)(url, json=payload if payload is not None else {}, headers={"X-CSRFToken": token})


def _login_owner(client):
    assert login(client).status_code == 302


def _login_mechanic(client, email=MECHANIC_EMAIL):
    assert login(client, email=email, password=PASSWORD).status_code == 302


def _day(week: int, weekday: int = 2) -> str:
    return (BASE_MONDAY + timedelta(weeks=week, days=weekday)).isoformat()


def _range(day: str) -> tuple[str, str]:
    d = date.fromisoformat(day)
    monday = d - timedelta(days=d.weekday())
    return f"{monday.isoformat()}T00:00:00", f"{(monday + timedelta(days=7)).isoformat()}T00:00:00"


def _events(client, day: str) -> list[dict]:
    start, end = _range(day)
    resp = client.get(f"/calendar/api/events?start={start}&end={end}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def _labor(number, suffix="", assigned=None, **extra):
    labor = {"description": "Replace brakes", "hours": "2", "rate_code": "standard", "assigned_mechanics": assigned or []}
    labor.update(extra)
    return {"labor_id": f"L{number}{suffix}", "labor": labor, "parts": []}


def _assigned(mechanic, percent=100.0):
    return [{"user_id": mechanic["_id"], "name": f"{mechanic['first_name']} {mechanic['last_name']}", "role": "mechanic", "percent": percent}]


def _insert_wo(shop_db, sync_seed, number, status="open", labors=None, customer=None, unit=None, **extra):
    now = _now()
    customer = customer or sync_seed["customer"]
    doc = {
        "_id": ObjectId(), "shop_id": sync_seed["shop"]["_id"], "wo_number": number,
        "customer_id": customer["_id"], "unit_id": (unit or sync_seed["unit"])["_id"],
        "status": status, "is_active": True,
        "labors": labors if labors is not None else [_labor(number, assigned=_assigned(sync_seed["mechanic"]))],
        "totals": {"grand_total": 300.0, "labors": []},
        "work_order_date": now, "created_at": now, "updated_at": now,
    }
    doc.update(extra)
    shop_db.work_orders.insert_one(doc)
    return doc


def _create_event(client, day: str, hour: int, *, work_order_id=None, mechanic_ids=None, title="", hour_len=2, **extra) -> dict:
    """Запись от имени владельца; возвращает JSON ответа."""
    _login_owner(client)
    body = {"start_time": f"{day}T{hour:02d}:00:00", "end_time": f"{day}T{hour + hour_len:02d}:00:00", "title": title}
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


def _status(shop_db, event_id):
    return _event(shop_db, event_id)["status"]


def _update_payload(wo, assigned_user_ids=None, save_status=None, **fields):
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
    payload.update(fields)
    return payload


def _mechanic_save_payload(wo, mechanic_state=None):
    """Сохранение механика (веб-эндпоинт механика / мобильное): строки + пробег."""
    payload = {
        "labors": [{"labor_id": b["labor_id"], "description": b["labor"]["description"], "parts": []} for b in wo["labors"]],
        "unit_mileage": 150000,
    }
    if mechanic_state:
        payload["mechanic_state"] = mechanic_state
    return payload


# ── статус следует за WO ─────────────────────────────────────

def test_status_follows_work_order_through_real_hooks(client, sync_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    wo = _insert_wo(shop_db, sync_seed, 94001)
    day = _day(0)
    plan = _create_event(client, day, 9, work_order_id=wo["_id"], title="Plan")
    cancelled = _create_event(client, day, 13, work_order_id=wo["_id"], title="Cancelled plan", status="cancelled")
    assert plan["status"] == "scheduled" and plan["wo_state"] == "open" and plan["wo_number"] == 94001
    assert cancelled["status"] == "cancelled"

    # Механик стартует таймер → WO in_progress → запись in_progress; отменённую не трогаем.
    _login_mechanic(client)
    resp = _json(client, "post", "/work_orders/api/mechanic/timers/start", {"work_order_id": str(wo["_id"]), "labor_id": "L94001"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert shop_db.work_orders.find_one({"_id": wo["_id"]})["status"] == "in_progress"
    assert _status(shop_db, plan["id"]) == "in_progress" and _status(shop_db, cancelled["id"]) == "cancelled"
    assert _json(client, "post", "/work_orders/api/mechanic/timers/stop", {}).status_code == 200
    assert _status(shop_db, plan["id"]) == "in_progress"

    # Механик отметил Done (статус WO остаётся in_progress) → запись completed.
    resp = _json(client, "post", f"/work_orders/api/mechanic/work_orders/{wo['_id']}", _mechanic_save_payload(wo, "done"))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert shop_db.work_orders.find_one({"_id": wo["_id"]})["mechanic_done"] is True
    assert _status(shop_db, plan["id"]) == "completed"

    # Менеджер вернул WO в работу (Done снимается) → снова in_progress.
    _login_owner(client)
    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/status", {"status": "in_progress"})
    assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_data(as_text=True)
    assert _status(shop_db, plan["id"]) == "in_progress"

    # Ручной статус записи календарь сам не перебивает…
    resp = _json(client, "put", f"/calendar/api/events/{plan['id']}", {"status": "confirmed"})
    assert resp.status_code == 200 and resp.get_json()["status"] == "confirmed"
    assert _status(shop_db, plan["id"]) == "confirmed"
    # …а следующая запись WO снова подтягивает факт: оплата полностью → paid → completed.
    # Сохранение механика пересчитало тоталы — платим живой grand_total.
    grand_total = shop_db.work_orders.find_one({"_id": wo["_id"]})["totals"]["grand_total"]
    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/payment",
                 {"amount": grand_total, "payment_method": "cash"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json().get("status") == "paid", resp.get_data(as_text=True)
    assert _status(shop_db, plan["id"]) == "completed"
    row = next(e for e in _events(client, day) if e["id"] == plan["id"])
    assert row["wo_state"] == "paid" and row["status"] == "completed"

    # WO вернули в open (не начат) — запись НЕ откатывается.
    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/status", {"status": "open"})
    assert resp.status_code == 200 and resp.get_json()["ok"] is True
    assert _status(shop_db, plan["id"]) == "completed" and _status(shop_db, cancelled["id"]) == "cancelled"

    # Подтверждение менеджера → completed; снятие подтверждения ничего не меняет.
    assert _json(client, "put", f"/calendar/api/events/{plan['id']}", {"status": "scheduled"}).status_code == 200
    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/confirm", {"confirmed": True})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _status(shop_db, plan["id"]) == "completed"
    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/confirm", {"confirmed": False})
    assert resp.status_code == 200
    assert _status(shop_db, plan["id"]) == "completed"

    # Офисное сохранение «In Progress» → in_progress; обычное сохранение (open) ничего не откатывает.
    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/update",
                 _update_payload(wo, [sync_seed["mechanic"]["_id"]], save_status="in_progress"))
    assert resp.status_code == 200 and resp.get_json()["status"] == "in_progress", resp.get_data(as_text=True)
    assert _status(shop_db, plan["id"]) == "in_progress"
    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/update",
                 _update_payload(wo, [sync_seed["mechanic"]["_id"]], save_status="open"))
    assert resp.status_code == 200 and resp.get_json()["status"] == "open"
    assert _status(shop_db, plan["id"]) == "in_progress"


def test_bulk_payment_completes_every_linked_appointment(client, sync_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    wo_a = _insert_wo(shop_db, sync_seed, 94002)
    wo_b = _insert_wo(shop_db, sync_seed, 94003)
    day = _day(1)
    ev_a = _create_event(client, day, 9, work_order_id=wo_a["_id"])["id"]
    ev_b = _create_event(client, day, 13, work_order_id=wo_b["_id"])["id"]

    resp = _json(client, "post", "/work_orders/api/bulk-payments", {
        "customer_id": str(sync_seed["customer"]["_id"]), "payment_method": "check",
        "allocations": [{"work_order_id": str(wo_a["_id"]), "amount": 300}, {"work_order_id": str(wo_b["_id"]), "amount": 300}],
    })
    assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_data(as_text=True)
    assert resp.get_json()["invoices_paid_in_full"] == 2
    assert _status(shop_db, ev_a) == "completed" and _status(shop_db, ev_b) == "completed"


def test_sync_skips_when_shop_statuses_lack_the_target_key(app, client, sync_seed, mongo):
    """Статусы магазина настраиваемые; системный ключ API удалить не даёт, но
    если его нет (старые данные) — синк ничего не ломает и молчит."""
    from app.blueprints.calendar.services.work_orders import sync_linked_appointments_status

    shop_db = mongo[SHOP_A_DB]
    wo = _insert_wo(shop_db, sync_seed, 94004)
    plan = _create_event(client, _day(2), 9, work_order_id=wo["_id"])["id"]
    shop_db.calendar_settings.update_one({"key": "statuses"}, {"$set": {"statuses": [
        {"key": "scheduled", "label": "Scheduled", "color": "#1a73e8"},
        {"key": "completed", "label": "Completed", "color": "#5f6368"},
        {"key": "cancelled", "label": "Cancelled", "color": "#d93025"},
    ]}}, upsert=True)
    try:
        resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/status", {"status": "in_progress"})
        assert resp.status_code == 200 and resp.get_json()["ok"] is True
        assert _status(shop_db, plan) == "scheduled"
        with app.app_context():
            assert sync_linked_appointments_status(shop_db, sync_seed["shop"], wo["_id"]) == 0
    finally:
        shop_db.calendar_settings.delete_one({"key": "statuses"})
    with app.app_context():
        assert sync_linked_appointments_status(shop_db, sync_seed["shop"], wo["_id"]) == 1
        assert sync_linked_appointments_status(shop_db, sync_seed["shop"], wo["_id"]) == 0  # уже синхронно
        assert sync_linked_appointments_status(shop_db, sync_seed["shop"], ObjectId()) == 0
        assert sync_linked_appointments_status(shop_db, sync_seed["shop"], "garbage") == 0
    assert _status(shop_db, plan) == "in_progress"


# ── клиент/юнит следуют за WO ────────────────────────────────

def test_transfer_moves_linked_appointment_to_new_customer_and_unit(client, sync_seed, mongo):
    """Перенос WO деактивирует исходный юнит (копия уезжает к новому клиенту),
    поэтому у теста свой юнит; отменённую запись перенос не трогает."""
    shop_db = mongo[SHOP_A_DB]
    own_unit = {
        "_id": ObjectId(), "shop_id": sync_seed["shop"]["_id"], "customer_id": sync_seed["customer"]["_id"],
        "vin": "SYNCVIN0000000002", "unit_number": "SY-T", "year": 2020, "make": "Mack", "model": "Anthem",
        "mileage": 90000, "is_active": True, "created_at": _now(),
    }
    shop_db.units.insert_one(own_unit)
    wo = _insert_wo(shop_db, sync_seed, 94005, unit=own_unit)
    day = _day(3)
    created = _create_event(client, day, 9, work_order_id=wo["_id"])
    cancelled = _create_event(client, day, 13, work_order_id=wo["_id"], status="cancelled")
    assert created["customer_id"] == str(sync_seed["customer"]["_id"]) and created["customer_label"] == CUSTOMER_NAME
    assert created["unit_label"] == "SY-T 2020 Mack Anthem"

    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/transfer", {"customer_id": str(sync_seed["target"]["_id"])})
    assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["unit_action"] == "moved"

    event = _event(shop_db, created["id"])
    assert event["customer_id"] == sync_seed["target"]["_id"] and event["customer_label"] == TARGET_NAME
    assert event["unit_id"] == ObjectId(body["unit_id"]) and event["unit_id"] != own_unit["_id"]
    assert event["unit_label"] == "SY-T 2020 Mack Anthem"
    assert event["work_order_id"] == wo["_id"] and event["status"] == "scheduled"
    untouched = _event(shop_db, cancelled["id"])
    assert untouched["customer_id"] == sync_seed["customer"]["_id"] and untouched["unit_id"] == own_unit["_id"]
    assert shop_db.units.find_one({"_id": sync_seed["unit"]["_id"]})["is_active"] is True
    row = next(e for e in _events(client, day) if e["id"] == created["id"])
    assert row["customer_label"] == TARGET_NAME and row["unit_id"] == body["unit_id"]


def test_office_unit_change_refreshes_appointment(client, sync_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    other_unit = {
        "_id": ObjectId(), "shop_id": sync_seed["shop"]["_id"], "customer_id": sync_seed["customer"]["_id"],
        "unit_number": "SY-2", "year": 2019, "make": "Kenworth", "model": "T680", "is_active": True, "created_at": _now(),
    }
    shop_db.units.insert_one(other_unit)
    wo = _insert_wo(shop_db, sync_seed, 94006)
    m1, m2 = sync_seed["mechanic"], sync_seed["second"]
    event_id = _create_event(client, _day(4), 9, work_order_id=wo["_id"], mechanic_ids=[m1["_id"]])["id"]
    assert _event(shop_db, event_id)["unit_id"] == sync_seed["unit"]["_id"]

    # Офис меняет юнит WO и переназначает строку на второго механика.
    payload = _update_payload(wo, [m2["_id"]],
                              customer_id=str(sync_seed["customer"]["_id"]), unit_id=str(other_unit["_id"]))
    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/update", payload)
    assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_data(as_text=True)
    event = _event(shop_db, event_id)
    assert event["unit_id"] == other_unit["_id"] and event["unit_label"] == "SY-2 2019 Kenworth T680"
    # Бригада записи за назначениями строк WO не следует.
    assert event["mechanic_ids"] == [m1["_id"]] and event["mechanic_name"] == "Sync Mechanic"


# ── удаление WO, прямая привязка ─────────────────────────────

def test_wo_delete_unlinks_and_keeps_status(client, sync_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    wo = _insert_wo(shop_db, sync_seed, 94007, status="in_progress")
    day = _day(5)
    # Статус при создании — что прислали (привязка его не трогает); дальше он должен уцелеть.
    created = _create_event(client, day, 9, work_order_id=wo["_id"], title="Brakes", status="in_progress")
    assert created["status"] == "in_progress" and created["wo_number"] == 94007

    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/delete")
    assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_data(as_text=True)
    event = _event(shop_db, created["id"])
    assert event["work_order_id"] is None and event["wo_number"] is None
    assert event["status"] == "in_progress" and event["title"] == "Brakes"
    row = next(e for e in _events(client, day) if e["id"] == created["id"])
    assert row["work_order_id"] == "" and row["wo_number"] is None and row["wo_state"] == "" and row["labor_summary"] == []


def test_link_refuses_event_already_linked_to_another_active_wo(app, sync_seed, mongo):
    from app.blueprints.calendar.routes import link_event_to_work_order, unlink_work_order_events

    shop_db = mongo[SHOP_A_DB]
    wo_a = _insert_wo(shop_db, sync_seed, 94008)
    wo_b = _insert_wo(shop_db, sync_seed, 94009, status="in_progress")
    day = date.fromisoformat(_day(6))
    event = {
        "_id": ObjectId(), "shop_id": sync_seed["shop"]["_id"], "title": "",
        "start_time": datetime(day.year, day.month, day.day, 9), "end_time": datetime(day.year, day.month, day.day, 10),
        "status": "scheduled", "customer_id": sync_seed["customer"]["_id"], "customer_label": CUSTOMER_NAME,
        "unit_id": None, "unit_label": "", "mechanics": [], "mechanic_ids": [], "mechanic_id": None, "mechanic_name": "",
        "presets": [], "work_order_id": wo_a["_id"], "wo_number": 94008, "created_at": _now(), "updated_at": _now(),
    }
    shop_db.calendar_events.insert_one(event)
    _EVENT_IDS.append(event["_id"])
    with app.app_context():
        assert link_event_to_work_order(shop_db, sync_seed["shop"], event["_id"], wo_b["_id"], 94009) is False
        saved = _event(shop_db, event["_id"])
        assert saved["work_order_id"] == wo_a["_id"] and saved["wo_number"] == 94008 and saved["status"] == "scheduled"
        # Старый WO удалён — перепривязка разрешена: клиент/юнит и статус — по новому WO.
        shop_db.work_orders.update_one({"_id": wo_a["_id"]}, {"$set": {"is_active": False}})
        assert link_event_to_work_order(shop_db, sync_seed["shop"], event["_id"], wo_b["_id"], 94009) is True
        saved = _event(shop_db, event["_id"])
        assert saved["work_order_id"] == wo_b["_id"] and saved["wo_number"] == 94009
        assert saved["unit_id"] == sync_seed["unit"]["_id"] and saved["unit_label"] == "SY-1 2021 Volvo VNL"
        assert saved["status"] == "in_progress"
        # Кривые аргументы и чужая запись — False без исключений.
        assert link_event_to_work_order(shop_db, sync_seed["shop"], ObjectId(), wo_b["_id"], 94009) is False
        assert link_event_to_work_order(shop_db, sync_seed["shop"], "garbage", wo_b["_id"], 94009) is False
        assert link_event_to_work_order(None, sync_seed["shop"], event["_id"], wo_b["_id"], 94009) is False
        # Отвязка всех записей WO: ссылка снимается, статус остаётся.
        assert unlink_work_order_events(shop_db, sync_seed["shop"], wo_b["_id"]) == 1
        saved = _event(shop_db, event["_id"])
        assert saved["work_order_id"] is None and saved["wo_number"] is None and saved["status"] == "in_progress"
        assert unlink_work_order_events(shop_db, sync_seed["shop"], wo_b["_id"]) == 0


# ── список записей на payload'ах WO ──────────────────────────

def test_linked_appointments_exposed_on_work_order_payloads(client, sync_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    m1 = sync_seed["mechanic"]
    wo = _insert_wo(shop_db, sync_seed, 94010, status="in_progress")
    day = _day(7)
    noted = _create_event(client, day, 9, work_order_id=wo["_id"], mechanic_ids=[m1["_id"]], title="Customer waits",
                          presets=[{"id": str(ObjectId()), "name": "Oil change"}], status="in_progress")
    _create_event(client, day, 14, work_order_id=wo["_id"], status="cancelled")
    plain = _create_event(client, _day(7, weekday=3), 9, work_order_id=wo["_id"], title="")
    # title без заметки = лейбл клиента (старые записи) → note пустая.
    shop_db.calendar_events.update_one({"_id": ObjectId(plain["id"])}, {"$set": {"title": CUSTOMER_NAME}})

    def _check(items):
        assert [i["id"] for i in items] == [noted["id"], plain["id"]]  # отменённая не отдаётся, порядок по времени
        first = items[0]
        assert set(first) == {"id", "start_time", "end_time", "status", "title", "note", "mechanics", "presets"}
        assert first["start_time"] == f"{day}T09:00:00" and first["end_time"] == f"{day}T11:00:00"
        assert first["status"] == "in_progress" and first["note"] == "Customer waits" and first["title"] == "Customer waits"
        assert first["mechanics"] == [{"id": str(m1["_id"]), "name": "Sync Mechanic"}]
        assert first["presets"][0]["name"] == "Oil change"
        assert items[1]["note"] == "" and items[1]["title"] == CUSTOMER_NAME

    # Офисная мобильная ветка и страница деталей.
    resp = client.get(f"/api/mobile/work_orders/{wo['_id']}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    _check(resp.get_json()["appointments"])
    assert client.get(f"/work_orders/details?work_order_id={wo['_id']}").status_code == 200

    # Механик: веб-эндпоинт и мобильная ветка — безденежный payload с записями.
    _login_mechanic(client)
    for url in (f"/work_orders/api/mechanic/work_orders/{wo['_id']}", f"/api/mobile/work_orders/{wo['_id']}"):
        resp = client.get(url)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        _check(body["appointments"])
        assert "grand_total" not in body and "price" not in str(body["appointments"])
    _login_owner(client)


# ── создание WO из записи ────────────────────────────────────

def test_web_create_wo_from_appointment_links_it(client, sync_seed, mongo, seed):
    shop_db = mongo[SHOP_A_DB]
    day = _day(8)
    created = _create_event(client, day, 13, hour_len=1, title="Oil change",
                            customer_id=str(sync_seed["customer"]["_id"]), unit_id=str(sync_seed["unit"]["_id"]),
                            mechanic_id=str(sync_seed["mechanic"]["_id"]))
    event_id = created["id"]
    assert created["customer_label"] == CUSTOMER_NAME and created["mechanic_ids"] == [str(sync_seed["mechanic"]["_id"])]

    # Страница WO из календаря содержит скрытое поле calendar_event_id.
    page = client.get(
        f"/work_orders/details?customer_id={sync_seed['customer']['_id']}&unit_id={sync_seed['unit']['_id']}"
        f"&mechanic_id={sync_seed['mechanic']['_id']}&note=Oil%20change&calendar_event_id={event_id}"
    )
    assert page.status_code == 200
    assert f'name="calendar_event_id" value="{event_id}"' in page.get_data(as_text=True)

    token = get_csrf_token(client)
    resp = client.post("/work_orders/create", data={
        "csrf_token": token,
        "customer_id": str(sync_seed["customer"]["_id"]),
        "unit_id": str(sync_seed["unit"]["_id"]),
        "calendar_event_id": event_id,
        "labors[0][labor_description]": "Oil change",
        "labors[0][labor_hours]": "1",
        "labors[0][labor_rate_code]": seed["labor_rate"]["code"],
    })
    assert resp.status_code == 302, resp.get_data(as_text=True)

    event = _event(shop_db, event_id)
    assert event["work_order_id"] is not None
    wo = shop_db.work_orders.find_one({"_id": event["work_order_id"]})
    assert wo and event["wo_number"] == wo["wo_number"]
    assert event["customer_id"] == sync_seed["customer"]["_id"] and event["unit_id"] == sync_seed["unit"]["_id"]

    row = next(e for e in _events(client, day) if e["id"] == event_id)
    assert row["wo_number"] == wo["wo_number"] and row["wo_state"] == "open" and row["labor_summary"] == ["Oil change"]
    assert row["title"] == "Oil change" and row["status"] == "scheduled"

    # Удаление WO снимает привязку, запись остаётся.
    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo['_id']}/delete")
    assert resp.status_code == 200 and resp.get_json()["ok"] is True
    assert _event(shop_db, event_id)["work_order_id"] is None
    row = next(e for e in _events(client, day) if e["id"] == event_id)
    assert row["work_order_id"] == "" and row["wo_state"] == ""


def test_mobile_start_work_from_appointment_links_and_syncs(client, sync_seed, mongo):
    """Механик стартует работу из записи (POST /api/mobile/work_orders с
    calendar_event_id): WO создаётся in_progress, запись привязана и стала
    in_progress. По юниту WO уже идёт — 409, но вторая запись привязана к нему."""
    shop_db = mongo[SHOP_A_DB]
    unit = {
        "_id": ObjectId(), "shop_id": sync_seed["shop"]["_id"], "customer_id": sync_seed["customer"]["_id"],
        "unit_number": "SY-M", "year": 2021, "make": "Kenworth", "model": "T680", "mileage": 120000,
        "is_active": True, "created_at": _now(),
    }
    shop_db.units.insert_one(unit)
    try:
        day = _day(9)
        base = {"customer_id": str(sync_seed["customer"]["_id"]), "unit_id": str(unit["_id"]),
                "mechanic_id": str(sync_seed["mechanic"]["_id"])}
        first = _create_event(client, day, 13, hour_len=1, title="Brake job", **base)
        second = _create_event(client, day, 15, hour_len=1, title="Follow-up", **base)
        assert first["unit_label"] == "SY-M 2021 Kenworth T680"

        _login_mechanic(client)
        wo_payload = {
            "customer_id": str(sync_seed["customer"]["_id"]), "unit_id": str(unit["_id"]),
            "status": "in_progress", "labors": [], "mechanic_state": "in_progress", "unit_mileage": 120000,
        }
        resp = _json(client, "post", "/api/mobile/work_orders", {**wo_payload, "calendar_event_id": first["id"]})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        wo_id = resp.get_json()["id"]
        ev = _event(shop_db, first["id"])
        assert str(ev["work_order_id"]) == wo_id and ev["status"] == "in_progress"
        wo = shop_db.work_orders.find_one({"_id": ObjectId(wo_id)})
        assert ev["wo_number"] == wo["wo_number"]
        # Заметка записи стала строкой работы (appointment_prefill).
        assert wo["labors"][0]["labor"]["description"] == "Brake job"

        resp = _json(client, "post", "/api/mobile/work_orders", {**wo_payload, "calendar_event_id": second["id"]})
        assert resp.status_code == 409, resp.get_data(as_text=True)
        assert resp.get_json()["work_order"]["id"] == wo_id
        ev2 = _event(shop_db, second["id"])
        assert str(ev2["work_order_id"]) == wo_id and ev2["status"] == "in_progress"

        # Детали WO в приложении отдают обе записи.
        detail = client.get(f"/api/mobile/work_orders/{wo_id}")
        assert detail.status_code == 200
        assert [a["id"] for a in detail.get_json()["appointments"]] == [first["id"], second["id"]]
    finally:
        shop_db.work_orders.delete_many({"unit_id": unit["_id"]})
        shop_db.units.delete_one({"_id": unit["_id"]})
