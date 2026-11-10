"""
Старт таймера против одновременных сохранений WO и таймер на смете.

Старт = открытый лог + одна атомарная запись WO ПОСЛЕ вставки лога:
строка всё ещё в WO, WO можно трекать (условия — как у сохранения того же
пользователя), $inc labors_rev и снятие Done механика. Поэтому:
  - сохранение, прочитавшее «идущие таймеры» / «кто работал» до вставки, не
    проходит CAS и пересобирает merge уже с таймером: работу, на которой
    идёт таймер, не удаляет, WO «все закончили» не помечает;
  - сохранение, удалившее строку раньше, срывает старт: лог удаляется,
    labor_not_found (прежний таймер, закрытый auto_switch, пересчитывается);
  - смету старт не конвертирует никогда: механику — 400
    estimate_cannot_track, менеджер трекает время без конверсии (склад не
    трогается, устаревшая вкладка офиса ничего не «возвращает» на склад).
Отказы несут message — старые сборки приложения показывают его в тосте.

Гонки воспроизводятся monkeypatch-ем точек между чтением и записью
(edit_views.running_labor_ids, concurrent_save.mechanic_done_fields,
time_tracking._stop_open_logs).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, SHOP_A_DB, TENANT_A_DB, TEST_MONGO_URI, get_csrf_token, login

PASSWORD = "password123"
MECH_A = "tr.mech.a@test.local"
MECH_B = "tr.mech.b@test.local"
START_URL = "/work_orders/api/mechanic/timers/start"
STOP_URL = "/work_orders/api/mechanic/timers/stop"


def _now():
    return datetime.now(timezone.utc)


# ───────────────────────── окружение ─────────────────────────


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture(scope="module")
def tr(app, seed, mongo):
    """Два механика, клиент/юнит и каталожные колодки (in_stock 100) в магазине A."""
    from app.constants.permissions import build_default_roles

    master = mongo["roobico_test_master"]
    tdb = mongo[TENANT_A_DB]
    shop_db = mongo[SHOP_A_DB]
    shop = seed["shop_a"]
    now = _now()

    mechanic_role = next(r for r in build_default_roles() if r["key"] == "mechanic")
    tdb.roles.update_one({"key": "mechanic"}, {"$set": mechanic_role}, upsert=True)

    users = {}
    for email, first in ((MECH_A, "Tina"), (MECH_B, "Toby")):
        u = {
            "_id": ObjectId(), "email": email, "password_hash": generate_password_hash(PASSWORD),
            "first_name": first, "last_name": "Race", "is_active": True,
            "tenant_id": seed["tenant_a"]["_id"], "shop_ids": [str(shop["_id"])],
            "role": "mechanic", "created_at": now,
        }
        master.users.insert_one(u)
        users[email] = u

    customer = {
        "_id": ObjectId(), "shop_id": shop["_id"], "tenant_id": seed["tenant_a"]["_id"],
        "company_name": "Timer Race Fleet", "taxable": False, "is_active": True, "created_at": now,
    }
    shop_db.customers.insert_one(customer)
    unit = {
        "_id": ObjectId(), "shop_id": shop["_id"], "customer_id": customer["_id"],
        "vin": "TRACE123456789000", "unit_number": "TR-1", "mileage": 50000,
        "is_active": True, "created_at": now,
    }
    shop_db.units.insert_one(unit)
    part = {
        "_id": ObjectId(), "shop_id": shop["_id"], "part_number": "TR-PADS",
        "description": "Brake pads", "average_cost": 40.0, "has_selling_price": True,
        "selling_price": 99.5, "in_stock": 100, "is_active": True, "created_at": now,
    }
    shop_db.parts.insert_one(part)

    yield {"shop": shop, "users": users, "customer": customer, "unit": unit, "part": part, "owner": seed["owner"]}

    wo_ids = [w["_id"] for w in shop_db.work_orders.find({"customer_id": customer["_id"]}, {"_id": 1})]
    shop_db.work_orders.update_many({"_id": {"$in": wo_ids}}, {"$set": {"is_active": False}})
    shop_db.wo_time_logs.delete_many({"$or": [
        {"work_order_id": {"$in": wo_ids}},
        {"user_id": {"$in": [u["_id"] for u in users.values()]}},
    ]})
    # Механики модуля не должны попадать в списки механиков следующих модулей.
    master.users.update_many({"_id": {"$in": [u["_id"] for u in users.values()]}}, {"$set": {"is_active": False}})
    shop_db.units.update_one({"_id": unit["_id"]}, {"$set": {"is_active": False}})
    shop_db.parts.delete_one({"_id": part["_id"]})


class Dev:
    """Телефон (mobile=True: вход приложения) или браузер: своя cookie-сессия."""

    def __init__(self, app, email, password=PASSWORD, *, mobile=True):
        self.c = app.test_client()
        if mobile:
            resp = self.c.post(
                "/api/mobile/login", json={"email": email, "password": password},
                environ_base={"REMOTE_ADDR": "127.0.0.41"},
            )
            assert resp.status_code == 200, resp.get_data(as_text=True)
            self.token = resp.get_json()["csrf_token"]
        else:
            assert login(self.c, email=email, password=password).status_code == 302
            self.token = get_csrf_token(self.c)

    def get(self, url):
        return self.c.get(url)

    def post(self, url, payload):
        return self.c.post(url, json=payload, headers={"X-CSRFToken": self.token})


def _wo(mongo, wo_id):
    return mongo[SHOP_A_DB].work_orders.find_one({"_id": ObjectId(str(wo_id))})


def _descs(mongo, wo_id):
    return [b["labor"]["description"] for b in _wo(mongo, wo_id)["labors"]]


def _stock(mongo, tr):
    return mongo[SHOP_A_DB].parts.find_one({"_id": tr["part"]["_id"]})["in_stock"]


def _open_logs(mongo, user, wo_id=None):
    query = {"user_id": user["_id"], "stopped_at": None}
    if wo_id is not None:
        query["work_order_id"] = ObjectId(str(wo_id))
    return list(mongo[SHOP_A_DB].wo_time_logs.find(query))


def _close_open_logs(mongo, *users):
    """Уборка после теста: никаких идущих таймеров за пределами теста."""
    now = _now()
    mongo[SHOP_A_DB].wo_time_logs.update_many(
        {"user_id": {"$in": [u["_id"] for u in users]}, "stopped_at": None},
        {"$set": {"stopped_at": now, "seconds": 0, "stop_source": "test_cleanup", "updated_at": now}},
    )


def _backdate(mongo, user, seconds):
    shop_db = mongo[SHOP_A_DB]
    log = shop_db.wo_time_logs.find_one({"user_id": user["_id"], "stopped_at": None})
    assert log, "no running timer"
    shop_db.wo_time_logs.update_one(
        {"_id": log["_id"]}, {"$set": {"started_at": log["started_at"] - timedelta(seconds=seconds)}})


def _create_wo(dev, tr, descriptions):
    resp = dev.post("/work_orders/api/mechanic/work_orders", {
        "customer_id": str(tr["customer"]["_id"]),
        "unit_id": str(tr["unit"]["_id"]),
        "unit_mileage": 50000,
        "allow_duplicate_unit_wo": True,
        "labors": [{"description": d, "parts": []} for d in descriptions],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["id"]


def _start(dev, wo_id, labor_id):
    return dev.post(START_URL, {"work_order_id": str(wo_id), "labor_id": labor_id})


def _insert_estimate(app, mongo, tr):
    """Смета с 4 колодками: склад по ней не списан (inventory_deducted False)."""
    from app.blueprints.work_orders.services.mobile_editor import compute_labors_and_totals
    from app.blueprints.work_orders.services.totals import align_totals_with_labors, normalize_totals_payload

    shop_db = mongo[SHOP_A_DB]
    rows = [{
        "labor_id": str(ObjectId()), "description": "Brakes (quote)", "hours": "1", "rate_code": "standard",
        "issue_description": "", "assigned_mechanics": [],
        "parts": [{"part_id": tr["part"]["_id"], "part_number": "TR-PADS", "description": "Brake pads",
                   "qty": 4, "cost": 40.0, "price": 99.5, "core_charge": 0, "misc_charge": 0,
                   "misc_charge_description": "", "unit": "each"}],
    }]
    with app.app_context():
        labors, totals_raw = compute_labors_and_totals(shop_db, tr["shop"], rows)
        totals = align_totals_with_labors(normalize_totals_payload(totals_raw), labors)
    now = _now()
    doc = {
        "_id": ObjectId(), "shop_id": tr["shop"]["_id"], "tenant_id": tr["shop"].get("tenant_id"),
        "wo_number": int(ObjectId().binary[-3:].hex(), 16) % 800000 + 100000,
        "customer_id": tr["customer"]["_id"], "unit_id": tr["unit"]["_id"],
        "status": "estimate", "labors": labors, "totals": totals, "labors_rev": 1,
        "inventory_deducted": False, "mileage": 50000, "is_active": True,
        "work_order_date": now, "created_at": now, "updated_at": now,
    }
    shop_db.work_orders.insert_one(doc)
    return doc


class AppForm:
    """Форма приложения механика (merge_protocol 1): view_id из GET ?mp=1,
    шлёт все строки с labor_id; без денег (как stripMoney)."""

    def __init__(self, dev, wo_id):
        self.dev, self.wo_id = dev, str(wo_id)
        resp = dev.get(f"/api/mobile/work_orders/{self.wo_id}?mp=1")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        details = resp.get_json()
        self.view_id = details["view_id"]
        self.rows = [{
            "labor_id": l["labor_id"], "description": l["description"], "hours": "", "rate_code": "",
            "issue_description": l.get("issue_description") or "",
            "parts": [{"part_id": p.get("part_id") or "", "part_number": p.get("part_number") or "",
                       "description": p.get("description") or "", "qty": p.get("qty") or 0,
                       "one_time_part": bool(p.get("one_time_part"))} for p in l.get("parts") or []],
        } for l in details["labors"]]

    def save(self, **extra):
        payload = {
            "merge_protocol": 1, "view_id": self.view_id, "status": "in_progress",
            "labors": self.rows, "mechanic_state": "in_progress", "unit_mileage": "50000",
        }
        payload.update(extra)
        resp = self.dev.post(f"/api/mobile/work_orders/{self.wo_id}", payload)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["ok"] is True, body
        return body


def _script_json(html, element_id):
    m = re.search(r'<script type="application/json" id="%s">(.*?)</script>' % re.escape(element_id), html, re.S)
    assert m, f"{element_id} not found"
    return json.loads(m.group(1))


def _js_number(v):
    """Number(значение поля формы): '' → 0, целое → int."""
    try:
        n = float(str(v if v is not None else "").strip() or 0)
    except ValueError:
        return 0
    return int(n) if n.is_integer() else n


class OfficeTab:
    """Вкладка веб-редактора WO, открытая раньше (то, что отрисовал сервер);
    Save без правок — как serializeBlocks + снимок тоталов страницы."""

    PART_KEYS = ("part_id", "one_time_part", "part_number", "description", "unit", "qty",
                 "cost", "price", "core_charge", "misc_charge", "misc_charge_description")

    def __init__(self, client, wo_id):
        self.client, self.wo_id = client, str(wo_id)
        resp = client.get(f"/work_orders/details?work_order_id={self.wo_id}")
        assert resp.status_code == 200, resp.status_code
        html = resp.get_data(as_text=True)
        self.view_id = _script_json(html, "workOrderCreatedData").get("view_id", "")
        self.totals = _script_json(html, "workOrderInitialTotalsData")
        self.blocks = _script_json(html, "workOrderInitialData")

    def save(self, save_status):
        snap_blocks = list(self.totals.get("labors") or [])
        labors = []
        for i, b in enumerate(self.blocks):
            lab = b["labor"]
            snap = snap_blocks[i] if i < len(snap_blocks) else {}
            labors.append({
                "labor_id": b["labor_id"],
                "labor_description": lab["description"],
                "labor_hours": _js_number(lab["hours"]),
                "hours_edited": False,
                "labor_rate_code": lab["rate_code"],
                "labor_full_total": snap.get("labor_full_total") or 0,
                "assigned_mechanics": [
                    {"user_id": a["user_id"], "name": a["name"], "role": a["role"], "percent": a["percent"]}
                    for a in lab["assigned_mechanics"]
                ],
                "issue_description": lab["issue_description"],
                "parts": [{
                    **{k: p.get(k) for k in self.PART_KEYS},
                    **{k: _js_number(p.get(k)) for k in ("qty", "cost", "price", "core_charge", "misc_charge")},
                    "part_id": p.get("part_id") or "", "one_time_part": bool(p.get("one_time_part")),
                } for p in b["parts"]],
            })
        payload = {
            "labors": labors, "totals": self.totals, "save_status": save_status,
            "merge_protocol": 1, "view_id": self.view_id, "shop_supply_overridden": False,
        }
        resp = self.client.post(
            f"/work_orders/api/work_orders/{self.wo_id}/update", json=payload,
            headers={"X-CSRFToken": get_csrf_token(self.client)},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        return resp.get_json()


# ───────────────────────── гонки старта с сохранениями ─────────────────────────


def test_save_that_read_running_timers_before_a_start_keeps_the_timed_job(app, mongo, tr, monkeypatch):
    """A удаляет «Exhaust»; его сохранение уже прочитало идущие таймеры (их
    не было), и тут B жмёт Start на «Exhaust». Старт сдвигает labors_rev
    после вставки лога — CAS сохранения A не проходит, merge пересобирается
    с таймером B: работа остаётся, A получает заметку."""
    from app.blueprints.work_orders.services import edit_views, time_tracking

    b_user = tr["users"][MECH_B]
    a = Dev(app, MECH_A)
    wo_id = _create_wo(a, tr, ["Brakes", "Exhaust"])
    form = AppForm(a, wo_id)
    exhaust_id = form.rows[1]["labor_id"]
    form.rows = [form.rows[0]]                 # A сознательно убирает «Exhaust»
    real = edit_views.running_labor_ids
    calls = {"n": 0}

    def racing(shop_db_, shop_id, work_order_id):
        out = real(shop_db_, shop_id, work_order_id)
        calls["n"] += 1
        if calls["n"] == 1:
            calls["start"] = time_tracking.start_timer(
                shop_db_, tr["shop"], b_user["_id"], "Toby Race", wo_id, exhaust_id, mechanic_mode=True,
            )
        return out

    monkeypatch.setattr(edit_views, "running_labor_ids", racing)
    try:
        body = form.save()
    finally:
        monkeypatch.undo()
    try:
        log, _prev, error = calls["start"]
        assert error is None and log is not None
        assert _descs(mongo, wo_id) == ["Brakes", "Exhaust"]
        assert 'Kept job "Exhaust": someone is working on it.' in body["merge_notes"]
        assert body["merged_foreign"] is True
        assert [l["labor_id"] for l in _open_logs(mongo, b_user, wo_id)] == [exhaust_id]
    finally:
        _close_open_logs(mongo, b_user)


def test_start_is_refused_when_a_save_removed_the_job_first(app, mongo, tr, monkeypatch):
    """Удаление «Exhaust» сохранением A закоммичено после проверки старта B,
    но до вставки его лога: фиксация старта видит, что строки нет — старт
    отклоняется (labor_not_found), лога нет, ревизия не двигается, отметка
    Done B (неудачный старт) не снимается."""
    from app.blueprints.work_orders.services import time_tracking

    shop_db = mongo[SHOP_A_DB]
    b_user = tr["users"][MECH_B]
    a = Dev(app, MECH_A)
    wo_id = _create_wo(a, tr, ["Brakes", "Exhaust"])
    shop_db.work_orders.update_one(
        {"_id": ObjectId(wo_id)},
        {"$set": {"mechanic_done_marks": [{"user_id": b_user["_id"], "name": "Toby Race", "at": _now()}]},
         "$inc": {"labors_rev": 1}},
    )
    form = AppForm(a, wo_id)
    exhaust_id = form.rows[1]["labor_id"]
    form.rows = [form.rows[0]]
    real = time_tracking._stop_open_logs
    calls = {"n": 0}

    def save_meanwhile(shop_db_, shop_id, user_id, stop_source):
        calls["n"] += 1
        if calls["n"] == 1:
            calls["save"] = form.save()
        return real(shop_db_, shop_id, user_id, stop_source)

    monkeypatch.setattr(time_tracking, "_stop_open_logs", save_meanwhile)
    try:
        with app.app_context():
            log, prev, error = time_tracking.start_timer(
                shop_db, tr["shop"], b_user["_id"], "Toby Race", wo_id, exhaust_id, mechanic_mode=True,
            )
    finally:
        monkeypatch.undo()
    try:
        assert (log, prev, error) == (None, [], "labor_not_found")
        assert _descs(mongo, wo_id) == ["Brakes"]
        assert shop_db.wo_time_logs.count_documents({"work_order_id": ObjectId(wo_id)}) == 0
        wo = _wo(mongo, wo_id)
        assert wo["labors_rev"] == calls["save"]["labors_rev"]
        assert [m["user_id"] for m in wo["mechanic_done_marks"]] == [b_user["_id"]]
    finally:
        _close_open_logs(mongo, b_user)


def test_start_commits_only_after_its_log_is_inserted(app, mongo, tr, monkeypatch):
    """Порядок «вставить лог → зафиксировать старт»: сохранение A, удаляющее
    «Exhaust», проходит целиком прямо перед вставкой лога B (таймера ещё не
    видно). Фиксация после вставки видит, что строки нет, — старт отклонён;
    фиксация до вставки пропустила бы таймер на удалённую работу."""
    from pymongo.collection import Collection

    from app.blueprints.work_orders.services import time_tracking

    shop_db = mongo[SHOP_A_DB]
    b_user = tr["users"][MECH_B]
    a = Dev(app, MECH_A)
    wo_id = _create_wo(a, tr, ["Brakes", "Exhaust"])
    form = AppForm(a, wo_id)
    exhaust_id = form.rows[1]["labor_id"]
    form.rows = [form.rows[0]]
    real_insert = Collection.insert_one
    calls = {"n": 0}

    def insert_one(self, document, *args, **kwargs):
        if self.name == "wo_time_logs" and document.get("user_id") == b_user["_id"]:
            calls["n"] += 1
            if calls["n"] == 1:
                calls["save"] = form.save()
        return real_insert(self, document, *args, **kwargs)

    monkeypatch.setattr(Collection, "insert_one", insert_one)
    try:
        with app.app_context():
            log, _prev, error = time_tracking.start_timer(
                shop_db, tr["shop"], b_user["_id"], "Toby Race", wo_id, exhaust_id, mechanic_mode=True,
            )
    finally:
        monkeypatch.undo()
    try:
        assert calls["n"] == 1
        assert (log, error) == (None, "labor_not_found")
        assert _descs(mongo, wo_id) == ["Brakes"]
        assert shop_db.wo_time_logs.count_documents({"work_order_id": ObjectId(wo_id)}) == 0
    finally:
        _close_open_logs(mongo, b_user)


def test_done_save_racing_a_colleagues_first_timer_does_not_mark_the_wo_done(app, mongo, tr, monkeypatch):
    """A сохраняет Done; «кто работал» уже посчитан ({A}), и тут B стартует
    свой первый таймер на WO. Старт сдвигает ревизию — CAS Done-сохранения не
    проходит, пересчёт видит B: WO не «все закончили», отметка A — на месте."""
    from app.blueprints.work_orders.services import concurrent_save, time_tracking

    a_user, b_user = tr["users"][MECH_A], tr["users"][MECH_B]
    a = Dev(app, MECH_A)
    wo_id = _create_wo(a, tr, ["Solo job"])
    form = AppForm(a, wo_id)
    lid = form.rows[0]["labor_id"]
    real = concurrent_save.mechanic_done_fields
    calls = {"n": 0}

    def racing(shop_db_, shop_, wo, data, user_id, now):
        out = real(shop_db_, shop_, wo, data, user_id, now)
        calls["n"] += 1
        if calls["n"] == 1:
            calls["start"] = time_tracking.start_timer(
                shop_db_, shop_, b_user["_id"], "Toby Race", wo_id, lid, mechanic_mode=True,
            )
        return out

    monkeypatch.setattr(concurrent_save, "mechanic_done_fields", racing)
    try:
        form.save(mechanic_state="done")
    finally:
        monkeypatch.undo()
    try:
        assert calls["start"][2] is None
        wo = _wo(mongo, wo_id)
        assert wo["mechanic_done"] is False
        assert [m["user_id"] for m in wo["mechanic_done_marks"]] == [a_user["_id"]]
        assert _open_logs(mongo, b_user, wo_id)
    finally:
        _close_open_logs(mongo, b_user)


def test_start_refused_after_auto_switch_reports_it_and_refreshes_the_previous_wo(app, mongo, tr, monkeypatch):
    """У B идёт таймер на первом WO; он жмёт Start на работе второго WO, а её
    в ту же секунду удалили. Старт отклонён (404 + текст), но прежний таймер
    auto_switch уже закрыл: ответ говорит об этом, а часы первого WO
    пересчитаны (время не теряется)."""
    from app.blueprints.work_orders.services import time_tracking

    shop_db = mongo[SHOP_A_DB]
    b_user = tr["users"][MECH_B]
    b = Dev(app, MECH_B)
    first_id = _create_wo(b, tr, ["Diagnose no-start"])
    second_id = _create_wo(b, tr, ["Replace starter"])
    first = _wo(mongo, first_id)
    first_lid = first["labors"][0]["labor_id"]
    second_lid = _wo(mongo, second_id)["labors"][0]["labor_id"]
    try:
        assert _start(b, first_id, first_lid).status_code == 200
        _backdate(mongo, b_user, 1800)
        real = time_tracking._stop_open_logs

        def removed_meanwhile(shop_db_, shop_id, user_id, stop_source):
            # Сохранение, удалившее работу, закоммичено сразу после проверки старта.
            shop_db_.work_orders.update_one(
                {"_id": ObjectId(second_id)},
                {"$pull": {"labors": {"labor_id": second_lid}}, "$inc": {"labors_rev": 1}},
            )
            return real(shop_db_, shop_id, user_id, stop_source)

        rev_second = _wo(mongo, second_id)["labors_rev"]
        monkeypatch.setattr(time_tracking, "_stop_open_logs", removed_meanwhile)
        try:
            resp = _start(b, second_id, second_lid)
        finally:
            monkeypatch.undo()

        assert resp.status_code == 404, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["ok"] is False and body["error"] == "labor_not_found"
        assert body["message"] == (
            "This job is no longer on the work order. Refresh the work order and try again. "
            f"Your timer on WO #{first['wo_number']} was stopped."
        )
        assert body["stopped_previous"]["work_order_id"] == first_id
        assert _open_logs(mongo, b_user) == []
        assert shop_db.wo_time_logs.count_documents({"work_order_id": ObjectId(second_id)}) == 0
        assert _wo(mongo, second_id)["labors_rev"] == rev_second + 1    # только само удаление
        lab = _wo(mongo, first_id)["labors"][0]["labor"]
        assert (lab["hours"], lab["hours_source"]) == ("0.5", "tracked")
    finally:
        _close_open_logs(mongo, b_user)


def test_start_gives_up_with_a_readable_409_when_the_wo_keeps_changing(app, mongo, tr, monkeypatch):
    """Фиксация старта ни разу не совпала, хотя WO в порядке (Done всё
    время меняется между вариантами записи): после MAX_CAS_ATTEMPTS — 409
    concurrent_update с текстом, лог удалён, ревизия на месте."""
    from pymongo.collection import Collection

    from app.blueprints.work_orders.services.edit_views import MAX_CAS_ATTEMPTS
    from app.blueprints.work_orders.services.time_tracking import TIMER_ERROR_MESSAGES

    shop_db = mongo[SHOP_A_DB]
    a_user = tr["users"][MECH_A]
    a = Dev(app, MECH_A)
    wo_id = _create_wo(a, tr, ["Busy job"])
    lid = _wo(mongo, wo_id)["labors"][0]["labor_id"]
    rev = _wo(mongo, wo_id)["labors_rev"]
    real = Collection.find_one_and_update
    calls = {"n": 0}

    def never_matches(self, *args, **kwargs):
        if self.name == "work_orders":
            calls["n"] += 1
            return None
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Collection, "find_one_and_update", never_matches)
    try:
        resp = _start(a, wo_id, lid)
    finally:
        monkeypatch.undo()
        _close_open_logs(mongo, a_user)
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert resp.get_json() == {
        "ok": False, "error": "concurrent_update", "message": TIMER_ERROR_MESSAGES["concurrent_update"],
    }
    assert calls["n"] == 2 * MAX_CAS_ATTEMPTS
    assert _wo(mongo, wo_id)["labors_rev"] == rev
    assert shop_db.wo_time_logs.count_documents({"work_order_id": ObjectId(wo_id)}) == 0


def test_failed_commit_write_leaves_no_open_timer(app, mongo, tr, monkeypatch):
    """Запись фиксации упала с ошибкой базы: ошибка не глотается (500 /
    исключение), но открытого лога — таймера, которого нет, — не остаётся."""
    from pymongo.collection import Collection
    from pymongo.errors import OperationFailure

    shop_db = mongo[SHOP_A_DB]
    a_user = tr["users"][MECH_A]
    a = Dev(app, MECH_A)
    wo_id = _create_wo(a, tr, ["Crash job"])
    lid = _wo(mongo, wo_id)["labors"][0]["labor_id"]
    real = Collection.find_one_and_update

    def boom(self, *args, **kwargs):
        if self.name == "work_orders":
            raise OperationFailure("simulated write failure")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Collection, "find_one_and_update", boom)
    try:
        try:
            resp = _start(a, wo_id, lid)
            assert resp.status_code == 500, resp.get_data(as_text=True)
        except OperationFailure:
            pass                                # приложение пробрасывает исключения в тест
    finally:
        monkeypatch.undo()
    try:
        assert _open_logs(mongo, a_user) == []
        assert shop_db.wo_time_logs.count_documents({"work_order_id": ObjectId(wo_id)}) == 0
    finally:
        _close_open_logs(mongo, a_user)


def test_every_timer_start_moves_labors_rev_once(app, mongo, tr):
    """Контракт для клиентов: каждый успешный Start сдвигает labors_rev
    (ровно на 1 без пересчёта прежних WO), ответ отдаёт ревизию после него."""
    a_user, b_user = tr["users"][MECH_A], tr["users"][MECH_B]
    a, b = Dev(app, MECH_A), Dev(app, MECH_B)
    wo_id = _create_wo(a, tr, ["Shared job"])
    lid = _wo(mongo, wo_id)["labors"][0]["labor_id"]
    try:
        for dev in (a, b):                     # A стартует, B подключается к той же работе
            rev = _wo(mongo, wo_id)["labors_rev"]
            resp = _start(dev, wo_id, lid)
            assert resp.status_code == 200, resp.get_data(as_text=True)
            assert resp.get_json()["labors_rev"] == _wo(mongo, wo_id)["labors_rev"] == rev + 1
    finally:
        _close_open_logs(mongo, a_user, b_user)


# ───────────────────────── смета ─────────────────────────


def test_mechanic_cannot_start_a_timer_on_an_estimate(app, mongo, tr):
    """Механик (экран открыт до «Save as Estimate») жмёт Start на смете: 400
    estimate_cannot_track с текстом для тоста; смета не тронута, а его
    таймер на другом WO продолжает идти (отказ — до переключения)."""
    from app.blueprints.work_orders.services.time_tracking import TIMER_ERROR_MESSAGES

    shop_db = mongo[SHOP_A_DB]
    a_user = tr["users"][MECH_A]
    a = Dev(app, MECH_A)
    other_id = _create_wo(a, tr, ["Other job"])
    estimate = _insert_estimate(app, mongo, tr)
    stock = _stock(mongo, tr)
    try:
        assert _start(a, other_id, _wo(mongo, other_id)["labors"][0]["labor_id"]).status_code == 200
        resp = _start(a, estimate["_id"], estimate["labors"][0]["labor_id"])
        assert resp.status_code == 400, resp.get_data(as_text=True)
        assert resp.get_json() == {
            "ok": False, "error": "estimate_cannot_track",
            "message": TIMER_ERROR_MESSAGES["estimate_cannot_track"],
        }
        after = _wo(mongo, estimate["_id"])
        assert after["status"] == "estimate"
        assert after["labors_rev"] == estimate["labors_rev"]
        assert shop_db.wo_time_logs.count_documents({"work_order_id": estimate["_id"]}) == 0
        assert [str(l["work_order_id"]) for l in _open_logs(mongo, a_user)] == [other_id]
        assert _stock(mongo, tr) == stock
    finally:
        _close_open_logs(mongo, a_user)


def test_manager_tracks_time_on_an_estimate_without_converting_it(app, mongo, tr):
    """Менеджер со страницы механика трекает время на смете: старт проходит,
    смета остаётся сметой (склад не списан). Офисная вкладка, открытая до
    старта, жмёт обычный Save («estimate») — на склад ничего не
    «возвращается»: смету никто не конвертировал."""
    owner_user = tr["owner"]
    estimate = _insert_estimate(app, mongo, tr)
    wo_id = str(estimate["_id"])
    lid = estimate["labors"][0]["labor_id"]
    owner = Dev(app, OWNER_EMAIL, OWNER_PASSWORD, mobile=False)
    tab = OfficeTab(owner.c, wo_id)
    stock = _stock(mongo, tr)
    try:
        rev = _wo(mongo, wo_id)["labors_rev"]
        resp = _start(owner, wo_id, lid)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        mid = _wo(mongo, wo_id)
        assert mid["status"] == "estimate"
        assert mid["inventory_deducted"] is False
        assert resp.get_json()["labors_rev"] == mid["labors_rev"] == rev + 1
        _backdate(mongo, owner_user, 600)
        assert owner.post(STOP_URL, {}).status_code == 200
        assert _wo(mongo, wo_id)["status"] == "estimate"

        body = tab.save("estimate")
        assert body["ok"] is True, body
        after = _wo(mongo, wo_id)
        assert after["status"] == "estimate"
        assert _stock(mongo, tr) == stock
    finally:
        _close_open_logs(mongo, owner_user)


@pytest.mark.parametrize("change, status_code, error", [
    ({"status": "estimate"}, 400, "estimate_cannot_track"),
    ({"manager_confirmed": True}, 403, "wo_confirmed"),
])
def test_wo_locked_for_the_mechanic_during_a_start_refuses_it(app, mongo, tr, monkeypatch, change, status_code, error):
    """Офис сделал WO сметой / подтвердил его между проверкой старта механика
    и вставкой лога: фиксация старта (фильтр как у сохранения механика) это
    видит — отказ с текстом, лога нет, статус не переписан, ревизия на месте."""
    from app.blueprints.work_orders.services import time_tracking

    shop_db = mongo[SHOP_A_DB]
    a_user = tr["users"][MECH_A]
    a = Dev(app, MECH_A)
    wo_id = _create_wo(a, tr, ["Locked race"])
    lid = _wo(mongo, wo_id)["labors"][0]["labor_id"]
    rev = _wo(mongo, wo_id)["labors_rev"]
    real = time_tracking._stop_open_logs

    def locked_meanwhile(shop_db_, shop_id, user_id, stop_source):
        shop_db_.work_orders.update_one({"_id": ObjectId(wo_id)}, {"$set": change})
        return real(shop_db_, shop_id, user_id, stop_source)

    monkeypatch.setattr(time_tracking, "_stop_open_logs", locked_meanwhile)
    try:
        resp = _start(a, wo_id, lid)
    finally:
        monkeypatch.undo()
        _close_open_logs(mongo, a_user)
    assert resp.status_code == status_code, resp.get_data(as_text=True)
    assert resp.get_json() == {
        "ok": False, "error": error, "message": time_tracking.TIMER_ERROR_MESSAGES[error],
    }
    wo = _wo(mongo, wo_id)
    assert wo["status"] == change.get("status", "in_progress")
    assert wo["labors_rev"] == rev
    assert shop_db.wo_time_logs.count_documents({"work_order_id": ObjectId(wo_id)}) == 0


# ───────────────────────── тексты отказов для старых сборок ─────────────────────────


def test_timer_errors_carry_a_readable_message_for_old_app_builds(app, mongo, tr):
    """Старое приложение показывает в тосте message ответа (а без него —
    сырой код): у каждого отказа старта/стопа он есть и это фраза, не код.
    Механику ни один текст не называет статус paid: оплаченный WO для него
    «закрыт» (код тот же — по нему клиенты узнают замок)."""
    from app.blueprints.work_orders.services.time_tracking import TIMER_ERROR_MESSAGES

    shop_db = mongo[SHOP_A_DB]
    a = Dev(app, MECH_A)
    wo_id = _create_wo(a, tr, ["Message job"])
    paid_id = _create_wo(a, tr, ["Paid job"])
    shop_db.work_orders.update_one({"_id": ObjectId(paid_id)}, {"$set": {"status": "paid"}})
    paid_lid = _wo(mongo, paid_id)["labors"][0]["labor_id"]

    cases = [
        ({"work_order_id": str(ObjectId()), "labor_id": "L1"}, 404, "work_order_not_found",
         TIMER_ERROR_MESSAGES["work_order_not_found"]),
        ({"work_order_id": wo_id, "labor_id": "no-such-job"}, 404, "labor_not_found",
         TIMER_ERROR_MESSAGES["labor_not_found"]),
        ({"work_order_id": wo_id, "labor_id": ""}, 400, "invalid_arguments",
         TIMER_ERROR_MESSAGES["invalid_arguments"]),
        ({"work_order_id": paid_id, "labor_id": paid_lid}, 400, "paid_cannot_track",
         "This work order is locked. Time can't be tracked on it."),
    ]
    for payload, status_code, error, message in cases:
        resp = a.post(START_URL, payload)
        assert resp.status_code == status_code, (error, resp.get_data(as_text=True))
        body = resp.get_json()
        assert body == {"ok": False, "error": error, "message": message}
        assert " " in body["message"] and body["message"] != error
        assert "paid" not in body["message"].lower()

    resp = a.post(STOP_URL, {})
    assert resp.status_code == 400
    assert resp.get_json() == {"ok": False, "error": "no_running_timer", "message": "No timer is running."}
    assert shop_db.wo_time_logs.count_documents({"work_order_id": {"$in": [ObjectId(wo_id), ObjectId(paid_id)]}}) == 0


def test_paid_refusal_names_the_status_only_to_users_who_see_it(app, mongo, tr):
    """Start на WO, оплаченном, пока экран был открыт. Механик (приложение и
    веб-страница механика) статус paid не видит нигде — rev-опрос отдаёт ему
    locked, — и отказ говорит «locked»; менеджер/владелец статус видит и
    получает «paid». Код один — paid_cannot_track; таймер не стартует, WO и
    ревизия не тронуты."""
    shop_db = mongo[SHOP_A_DB]
    a_user, owner_user = tr["users"][MECH_A], tr["owner"]
    phone = Dev(app, MECH_A)
    web = Dev(app, MECH_A, mobile=False)
    owner = Dev(app, OWNER_EMAIL, OWNER_PASSWORD, mobile=False)
    wo_id = _create_wo(phone, tr, ["Paid while open"])
    lid = _wo(mongo, wo_id)["labors"][0]["labor_id"]
    shop_db.work_orders.update_one({"_id": ObjectId(wo_id)}, {"$set": {"status": "paid"}})
    rev = _wo(mongo, wo_id)["labors_rev"]

    poll = phone.get(f"/api/mobile/work_orders/{wo_id}/rev").get_json()
    assert poll["locked"] is True and "status" not in poll

    locked = {"ok": False, "error": "paid_cannot_track",
              "message": "This work order is locked. Time can't be tracked on it."}
    paid = {"ok": False, "error": "paid_cannot_track",
            "message": "This work order is paid. Time can't be tracked on it."}
    try:
        for who, dev, expected in (("app mechanic", phone, locked), ("web mechanic", web, locked),
                                   ("owner", owner, paid)):
            resp = _start(dev, wo_id, lid)
            assert resp.status_code == 400, (who, resp.get_data(as_text=True))
            assert resp.get_json() == expected, who
        after = _wo(mongo, wo_id)
        assert after["status"] == "paid"
        assert after["labors_rev"] == rev
        assert shop_db.wo_time_logs.count_documents({"work_order_id": ObjectId(wo_id)}) == 0
    finally:
        _close_open_logs(mongo, a_user, owner_user)


def test_paid_race_after_auto_switch_tells_the_mechanic_locked_and_that_the_timer_stopped(app, mongo, tr, monkeypatch):
    """Гонка: у механика идёт таймер на первом WO, он жмёт Start на втором, а
    его в ту же секунду оплатили. Отказ (после auto_switch) — тем же
    нейтральным текстом, с припиской об остановленном таймере."""
    from app.blueprints.work_orders.services import time_tracking

    b_user = tr["users"][MECH_B]
    b = Dev(app, MECH_B)
    first_id = _create_wo(b, tr, ["Running before"])
    second_id = _create_wo(b, tr, ["Paid in the same second"])
    first = _wo(mongo, first_id)
    second_lid = _wo(mongo, second_id)["labors"][0]["labor_id"]
    real = time_tracking._stop_open_logs

    def paid_meanwhile(shop_db_, shop_id, user_id, stop_source):
        shop_db_.work_orders.update_one({"_id": ObjectId(second_id)}, {"$set": {"status": "paid"}})
        return real(shop_db_, shop_id, user_id, stop_source)

    try:
        assert _start(b, first_id, first["labors"][0]["labor_id"]).status_code == 200
        monkeypatch.setattr(time_tracking, "_stop_open_logs", paid_meanwhile)
        try:
            resp = _start(b, second_id, second_lid)
        finally:
            monkeypatch.undo()
        assert resp.status_code == 400, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["error"] == "paid_cannot_track"
        assert body["message"] == (
            "This work order is locked. Time can't be tracked on it. "
            f"Your timer on WO #{first['wo_number']} was stopped."
        )
        assert body["stopped_previous"]["work_order_id"] == first_id
        assert _open_logs(mongo, b_user) == []
        assert _wo(mongo, second_id)["status"] == "paid"
    finally:
        _close_open_logs(mongo, b_user)
