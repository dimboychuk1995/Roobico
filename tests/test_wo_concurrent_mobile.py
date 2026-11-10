"""
Конкурентные правки WO из механик-режима (веб-страница механика, мобильное
приложение механика) и мобильного редактора менеджера: CAS по labors_rev,
трёхсторонний merge (base × current × mine), edit views, ответы сохранений.

Первая часть — регрессия «без конфликтов»: сохранение, при котором никто
другой WO не трогал, даёт те же labors/totals, что и старый алгоритм
(его копия ниже — оракул). Клиенты имитируются ровно так, как их шлют
текущие сборки (work-order-form.tsx, mechanic_wo.js): GET деталей → правка
→ POST.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, SHOP_A_DB, TENANT_A_DB, TEST_MONGO_URI, get_csrf_token, login

PASSWORD = "password123"
MECH_A = "cm.mech.a@test.local"
MECH_B = "cm.mech.b@test.local"


def _now():
    return datetime.now(timezone.utc)


# ───────────────────────── окружение ─────────────────────────


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture(scope="module")
def cm(app, seed, mongo):
    """Два механика, клиент/юнит и каталожные запчасти в магазине A;
    shop supply 10% (детерминированные тоталы с округлением по блокам)."""
    from app.constants.permissions import build_default_roles

    master = mongo["roobico_test_master"]
    tdb = mongo[TENANT_A_DB]
    shop_db = mongo[SHOP_A_DB]
    shop = seed["shop_a"]
    now = _now()

    mechanic_role = next(r for r in build_default_roles() if r["key"] == "mechanic")
    tdb.roles.update_one({"key": "mechanic"}, {"$set": mechanic_role}, upsert=True)

    users = {}
    for email, first in ((MECH_A, "Andy"), (MECH_B, "Bella")):
        u = {
            "_id": ObjectId(), "email": email, "password_hash": generate_password_hash(PASSWORD),
            "first_name": first, "last_name": "Concurrent", "is_active": True,
            "tenant_id": seed["tenant_a"]["_id"], "shop_ids": [str(shop["_id"])],
            "role": "mechanic", "created_at": now,
        }
        master.users.insert_one(u)
        users[email] = u

    customer = {
        "_id": ObjectId(), "shop_id": shop["_id"], "tenant_id": seed["tenant_a"]["_id"],
        "company_name": "Concurrent Fleet", "taxable": False, "is_active": True, "created_at": now,
    }
    shop_db.customers.insert_one(customer)
    unit = {
        "_id": ObjectId(), "shop_id": shop["_id"], "customer_id": customer["_id"],
        "vin": "CONC1234567890000", "unit_number": "C-1", "mileage": 50000,
        "is_active": True, "created_at": now,
    }
    shop_db.units.insert_one(unit)

    parts = {
        "pads": {
            "_id": ObjectId(), "shop_id": shop["_id"], "part_number": "CM-PADS",
            "description": "Brake pads", "average_cost": 40.0, "has_selling_price": True,
            "selling_price": 99.5, "in_stock": 100, "is_active": True, "created_at": now,
        },
        "battery": {
            "_id": ObjectId(), "shop_id": shop["_id"], "part_number": "CM-BATT",
            "description": "Battery", "average_cost": 50.0, "has_selling_price": True,
            "selling_price": 120.0, "core_has_charge": True, "core_cost": 15.0,
            "misc_has_charge": True,
            "misc_charges": [{"description": "EPA fee", "price": 3.35, "taxable": True}],
            "in_stock": 100, "is_active": True, "created_at": now,
        },
        "oil": {
            "_id": ObjectId(), "shop_id": shop["_id"], "part_number": "CM-OIL-GAL",
            "description": "Engine oil", "average_cost": 12.0, "has_selling_price": True,
            "selling_price": 21.99, "unit_of_measure": "gallon", "in_stock": 100,
            "is_active": True, "created_at": now,
        },
    }
    shop_db.parts.insert_many(list(parts.values()))

    supply_prev = shop_db.shop_supply_amount_rules.find_one({"shop_id": shop["_id"]})
    shop_db.shop_supply_amount_rules.update_one(
        {"shop_id": shop["_id"]},
        {"$set": {"shop_supply_procentage": 10, "is_active": True}},
        upsert=True,
    )

    yield {
        "shop": shop, "users": users, "customer": customer, "unit": unit, "parts": parts,
        "owner": seed["owner"],
    }

    shop_db.work_orders.update_many({"customer_id": customer["_id"]}, {"$set": {"is_active": False}})
    shop_db.wo_time_logs.delete_many({"user_id": {"$in": [u["_id"] for u in users.values()]}})
    # Механики модуля не должны попадать в списки механиков следующих модулей.
    master.users.update_many({"_id": {"$in": [u["_id"] for u in users.values()]}}, {"$set": {"is_active": False}})
    shop_db.parts.delete_many({"_id": {"$in": [p["_id"] for p in parts.values()]}})
    if supply_prev is None:
        shop_db.shop_supply_amount_rules.delete_one({"shop_id": shop["_id"]})
    else:
        shop_db.shop_supply_amount_rules.replace_one({"_id": supply_prev["_id"]}, supply_prev)


class Device:
    """Один телефон/браузер: своя cookie-сессия (свой sid edit views)."""

    def __init__(self, app, email, password=PASSWORD, *, mobile=True):
        self.c = app.test_client()
        if mobile:
            resp = self.c.post(
                "/api/mobile/login", json={"email": email, "password": password},
                environ_base={"REMOTE_ADDR": "127.0.0.31"},
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


def _stock(mongo, part):
    return mongo[SHOP_A_DB].parts.find_one({"_id": part["_id"]})["in_stock"]


def _create_mech_wo(dev, cm, jobs):
    resp = dev.post("/work_orders/api/mechanic/work_orders", {
        "customer_id": str(cm["customer"]["_id"]),
        "unit_id": str(cm["unit"]["_id"]),
        "unit_mileage": 50000,
        "allow_duplicate_unit_wo": True,
        "labors": jobs,
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["id"]


# ── как шлют текущие (легаси) сборки клиентов ──


def _app_mechanic_rows(details):
    """work-order-form.tsx (механик): загрузка → stripMoney → фильтр пустых."""
    rows = []
    for l in details["labors"]:
        rows.append({
            "labor_id": l["labor_id"],
            "description": l["description"],
            "hours": "",
            "rate_code": "",
            "issue_description": l["issue_description"],
            "parts": [{
                "part_id": p.get("part_id") or "",
                "part_number": p.get("part_number") or "",
                "description": p.get("description") or "",
                "qty": p.get("qty") or 0,
                "one_time_part": bool(p.get("one_time_part")),
            } for p in l["parts"]],
        })
    return rows


def _app_send(rows):
    return [r for r in rows if (r.get("description") or "").strip() or r.get("parts")]


def _web_mechanic_rows(details):
    """mechanic_wo.js: loadDetails → buildLaborsPayload (qty || 1)."""
    rows = []
    for l in details["labors"]:
        rows.append({
            "labor_id": l.get("labor_id") or "",
            "description": l.get("description") or "",
            "issue_description": l.get("issue_description") or "",
            "preset_id": "",
            "parts": [{
                "part_id": p.get("part_id") or "",
                "part_number": p.get("part_number") or "",
                "description": p.get("description") or "",
                "qty": p.get("qty") or 1,
            } for p in l["parts"]],
        })
    return rows


def _app_manager_rows(details):
    """work-order-form.tsx (менеджер): raw_labors → форма."""
    rows = []
    for l in details["raw_labors"]:
        rows.append({
            "labor_id": l.get("labor_id") or "",
            "description": l["description"],
            "hours": l["hours"],
            "rate_code": l["rate_code"],
            "labor_total": l.get("labor_base") or None,
            "issue_description": l["issue_description"],
            "parts": [{
                "part_id": p.get("part_id") or "",
                "part_number": p.get("part_number") or "",
                "description": p.get("description") or "",
                "qty": p.get("qty") or 0,
                "cost": p.get("cost") or 0,
                "price": p.get("price") or 0,
                "core_charge": p.get("core_charge") or 0,
                "misc_charge": p.get("misc_charge") or 0,
                "misc_charge_description": p.get("misc_charge_description") or "",
                "one_time_part": bool(p.get("one_time_part")),
            } for p in (l.get("parts") or [])],
        })
    return rows


# ───────────────────────── оракул: старый алгоритм ─────────────────────────


def _legacy_merge_mechanic_edit(shop_db, shop, existing_wo, payload_labors):
    """Копия merge_mechanic_edit до merge-ядра (absence = delete)."""
    from app.blueprints.work_orders.services.common import i32
    from app.blueprints.work_orders.services.mechanic_editor import (
        _attach_catalog_misc_items,
        _autofill_part,
        _stored_block_to_payload,
        _stored_part_key,
        build_mechanic_labors_payload,
    )

    customer_id = existing_wo.get("customer_id")
    totals_doc = existing_wo.get("totals") if isinstance(existing_wo.get("totals"), dict) else {}
    totals_blocks = totals_doc.get("labors") if isinstance(totals_doc.get("labors"), list) else []
    existing_by_id = {}
    for i, block in enumerate(existing_wo.get("labors") or []):
        if not isinstance(block, dict):
            continue
        labor_id = str(block.get("labor_id") or "").strip()
        block_totals = totals_blocks[i] if i < len(totals_blocks) and isinstance(totals_blocks[i], dict) else {}
        if labor_id:
            existing_by_id[labor_id] = _stored_block_to_payload(block, block_totals)
    out = []
    for raw in payload_labors or []:
        if not isinstance(raw, dict):
            continue
        labor_id = str(raw.get("labor_id") or "").strip()
        base = existing_by_id.get(labor_id)
        if base is None:
            out.append(build_mechanic_labors_payload(shop_db, shop, customer_id, [raw])[0])
            continue
        existing_parts_by_key = {_stored_part_key(p): p for p in base["parts"]}
        merged_parts = []
        for p in raw.get("parts") or []:
            if not isinstance(p, dict):
                continue
            if not (
                str(p.get("part_id") or "").strip()
                or str(p.get("part_number") or "").strip()
                or str(p.get("description") or "").strip()
            ):
                continue
            existing_part = existing_parts_by_key.get(_stored_part_key(p))
            if existing_part is not None:
                merged = dict(existing_part)
                merged["qty"] = i32(p.get("qty")) or 0
                incoming_desc = str(p.get("description") or "").strip()
                if incoming_desc:
                    merged["description"] = incoming_desc
                merged_parts.append(merged)
            else:
                merged_parts.append(_autofill_part(shop_db, shop["_id"], customer_id, p))
        _attach_catalog_misc_items(merged_parts)
        out.append({
            **base,
            "description": str(raw.get("description") or "").strip(),
            "issue_description": str(raw.get("issue_description") or "").strip(),
            "parts": merged_parts,
        })
    return out


def _legacy_totals(shop_db, shop, wo, rows, *, is_taxable=None):
    from app.blueprints.work_orders.services.mobile_editor import compute_labors_and_totals
    from app.blueprints.work_orders.services.totals import (
        _apply_sales_tax_to_totals,
        _get_shop_sales_tax_context,
        _is_customer_taxable,
        align_totals_with_labors,
        normalize_totals_payload,
    )

    labors, totals_raw = compute_labors_and_totals(shop_db, shop, rows)
    totals = align_totals_with_labors(normalize_totals_payload(totals_raw), labors)
    existing = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    rate = existing.get("sales_tax_rate")
    if rate is None:
        rate = _get_shop_sales_tax_context(shop, shop_db).get("rate") or 0
    if is_taxable is None:
        is_taxable = existing.get("is_taxable")
        if is_taxable is None:
            is_taxable = _is_customer_taxable(shop_db, wo.get("customer_id"))
    totals = _apply_sales_tax_to_totals(totals, rate, bool(is_taxable))
    return labors, totals


def _comparable(labors, new_positions=()):
    """labors без меток merge (minted/client_key) и без случайных id новых строк."""
    out = []
    for i, b in enumerate(labors or []):
        b = copy.deepcopy(b)
        b.pop("minted", None)
        b.pop("client_key", None)
        if i in new_positions:
            b["labor_id"] = "<new>"
        out.append(b)
    return out


def _assert_same_as_legacy(app, mongo, cm, wo_before, actual_wo, payload_rows, *, manager=False):
    shop_db = mongo[SHOP_A_DB]
    shop = cm["shop"]
    with app.app_context():
        rows = copy.deepcopy(payload_rows) if manager else _legacy_merge_mechanic_edit(
            shop_db, shop, wo_before, copy.deepcopy(payload_rows))
        exp_labors, exp_totals = _legacy_totals(shop_db, shop, wo_before, rows)
    new_positions = {i for i, r in enumerate(payload_rows) if not str(r.get("labor_id") or "").strip()}
    assert _comparable(actual_wo["labors"], new_positions) == _comparable(exp_labors, new_positions)
    assert actual_wo["totals"] == exp_totals


# ───────────────────────── регрессия: без конфликтов ─────────────────────────


def _rich_mechanic_wo(app, mongo, cm):
    """WO механика с двумя работами; офис уже проставил часы/ставку/цену."""
    dev = Device(app, MECH_A)
    pads = cm["parts"]["pads"]
    wo_id = _create_mech_wo(dev, cm, [
        {"description": "Front brakes", "parts": [{"part_id": str(pads["_id"]), "qty": 2}]},
        {"description": "Inspect suspension", "parts": []},
    ])
    # Офис раньше выставил часы, ставку и ручную цену первой работы.
    mongo[SHOP_A_DB].work_orders.update_one(
        {"_id": ObjectId(wo_id)},
        {"$set": {
            "labors.0.labor.hours": "1.5",
            "labors.0.labor.rate_code": "standard",
            "labors.0.parts.0.price": 120.0,
            "totals.labors.0.labor": 150.0,
        }},
    )
    return dev, wo_id


def _edit_like_mechanic(rows, cm):
    """Типичная правка механика: описание, qty, новая запчасть с core/misc,
    новая работа с галлонной и одноразовой запчастью."""
    batt, oil = cm["parts"]["battery"], cm["parts"]["oil"]
    rows[0]["description"] = "Front brakes and rotors"
    rows[0]["parts"][0]["qty"] = 3
    rows[0]["parts"].append({"part_id": str(batt["_id"]), "part_number": batt["part_number"],
                             "description": "", "qty": 1, "one_time_part": False})
    rows[1]["description"] = "Inspect suspension, replace bushing"
    rows.append({
        "labor_id": "", "description": "Oil service", "hours": "", "rate_code": "",
        "issue_description": "Due for service",
        "parts": [
            {"part_id": str(oil["_id"]), "part_number": oil["part_number"], "description": "",
             "qty": 4, "one_time_part": False},
            {"part_id": "", "part_number": "OT-FILTER", "description": "Oil filter (local)",
             "qty": 1, "one_time_part": True},
        ],
    })
    return rows


def test_regression_mobile_mechanic_save_matches_legacy(app, mongo, cm):
    dev, wo_id = _rich_mechanic_wo(app, mongo, cm)
    details = dev.get(f"/api/mobile/work_orders/{wo_id}").get_json()
    rows = _app_send(_edit_like_mechanic(_app_mechanic_rows(details), cm))
    stock = {k: _stock(mongo, p) for k, p in cm["parts"].items()}
    before = _wo(mongo, wo_id)

    resp = dev.post(f"/api/mobile/work_orders/{wo_id}", {
        "status": "in_progress", "labors": rows, "mechanic_state": "in_progress", "unit_mileage": "50000",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)

    after = _wo(mongo, wo_id)
    _assert_same_as_legacy(app, mongo, cm, before, after, rows)
    assert [b["labor"]["description"] for b in after["labors"]] == [
        "Front brakes and rotors", "Inspect suspension, replace bushing", "Oil service"]
    assert after["labors"][0]["labor"]["hours"] == "1.5"          # часы офиса целы
    assert after["labors"][0]["parts"][0]["price"] == 120.0       # ручная цена офиса цела
    assert after["labors"][2]["parts"][0]["unit"] == "gallon"
    assert _stock(mongo, cm["parts"]["pads"]) == stock["pads"] - 1
    assert _stock(mongo, cm["parts"]["battery"]) == stock["battery"] - 1
    assert _stock(mongo, cm["parts"]["oil"]) == stock["oil"] - 4


def test_regression_web_mechanic_save_matches_legacy(app, mongo, cm):
    dev, wo_id = _rich_mechanic_wo(app, mongo, cm)
    web = Device(app, MECH_A, mobile=False)
    details = web.get(f"/work_orders/api/mechanic/work_orders/{wo_id}").get_json()
    rows = _edit_like_mechanic(_web_mechanic_rows(details), cm)
    for r in rows:  # веб шлёт ровно эти ключи
        r.pop("hours", None)
        r.pop("rate_code", None)
        for p in r["parts"]:
            p.pop("one_time_part", None)
    before = _wo(mongo, wo_id)

    resp = web.post(f"/work_orders/api/mechanic/work_orders/{wo_id}", {"labors": rows, "unit_mileage": "50000"})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    after = _wo(mongo, wo_id)
    _assert_same_as_legacy(app, mongo, cm, before, after, rows)


def test_regression_mechanic_second_save_and_delete_after_get(app, mongo, cm):
    """Механик открыл WO (GET), сохранил, потом удалил работу: удаление
    увиденной строки по-прежнему работает; результат — как у старого кода."""
    dev, wo_id = _rich_mechanic_wo(app, mongo, cm)
    details = dev.get(f"/api/mobile/work_orders/{wo_id}").get_json()
    rows = _app_send(_app_mechanic_rows(details))
    rows[0]["description"] = "Front brakes (checked)"
    before = _wo(mongo, wo_id)
    resp = dev.post(f"/api/mobile/work_orders/{wo_id}", {
        "status": "in_progress", "labors": rows, "mechanic_state": "in_progress", "unit_mileage": "50000",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    _assert_same_as_legacy(app, mongo, cm, before, _wo(mongo, wo_id), rows)

    pads_stock = _stock(mongo, cm["parts"]["pads"])
    rows2 = [copy.deepcopy(rows[1])]          # «Front brakes» удалена механиком
    before2 = _wo(mongo, wo_id)
    resp = dev.post(f"/api/mobile/work_orders/{wo_id}", {
        "status": "in_progress", "labors": rows2, "mechanic_state": "in_progress", "unit_mileage": "50000",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    after = _wo(mongo, wo_id)
    _assert_same_as_legacy(app, mongo, cm, before2, after, rows2)
    assert [b["labor"]["description"] for b in after["labors"]] == ["Inspect suspension"]
    assert _stock(mongo, cm["parts"]["pads"]) == pads_stock + 2   # запчасти удалённой работы вернулись


def test_regression_mobile_manager_save_matches_legacy(app, mongo, cm):
    owner = Device(app, OWNER_EMAIL, OWNER_PASSWORD)
    pads, batt = cm["parts"]["pads"], cm["parts"]["battery"]
    resp = owner.post("/api/mobile/work_orders", {
        "customer_id": str(cm["customer"]["_id"]), "unit_id": str(cm["unit"]["_id"]), "status": "open",
        "labors": [
            {"description": "Diagnostics", "hours": "1.25", "rate_code": "standard",
             "parts": [{"part_id": str(pads["_id"]), "part_number": pads["part_number"],
                        "qty": 2, "price": 99.5, "cost": 40.0}]},
            {"description": "Flat fee job", "hours": "", "rate_code": "standard", "labor_total": 75.0,
             "parts": []},
        ],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    wo_id = resp.get_json()["id"]

    details = owner.get(f"/api/mobile/work_orders/{wo_id}").get_json()
    rows = _app_send(_app_manager_rows(details))
    rows[0]["hours"] = "2"
    rows[0]["labor_total"] = None            # форма обнуляет базу при правке часов
    rows[0]["parts"][0]["price"] = 105.0
    rows[0]["parts"][0]["qty"] = 1
    rows[1]["labor_total"] = 80.0
    rows[1]["parts"].append({"part_id": str(batt["_id"]), "part_number": batt["part_number"],
                             "description": "Battery", "qty": 1, "cost": 50.0, "price": 120.0,
                             "core_charge": 15.0, "misc_charge": 0, "misc_charge_description": "",
                             "one_time_part": False})
    rows.append({"labor_id": "", "description": "Wash", "hours": "0.5", "rate_code": "standard",
                 "labor_total": None, "issue_description": "", "parts": []})
    before = _wo(mongo, wo_id)
    stock = _stock(mongo, pads)

    resp = owner.post(f"/api/mobile/work_orders/{wo_id}", {"status": "open", "labors": rows})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    after = _wo(mongo, wo_id)
    _assert_same_as_legacy(app, mongo, cm, before, after, rows, manager=True)
    assert resp.get_json()["grand_total"] == after["totals"]["grand_total"]
    assert _stock(mongo, pads) == stock + 1


# ───────────────────────── хелперы сценариев ─────────────────────────


def _mobile_details(dev, wo_id, *, mp=False):
    resp = dev.get(f"/api/mobile/work_orders/{wo_id}" + ("?mp=1" if mp else ""))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def _web_details(dev, wo_id, *, mp=False):
    resp = dev.get(f"/work_orders/api/mechanic/work_orders/{wo_id}" + ("?mp=1" if mp else ""))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def _app_save(dev, wo_id, rows, **extra):
    """Автосейв приложения механика текущей (легаси) сборки."""
    payload = {
        "status": "in_progress", "labors": _app_send(rows),
        "mechanic_state": "in_progress", "unit_mileage": "50000",
    }
    payload.update(extra)
    resp = dev.post(f"/api/mobile/work_orders/{wo_id}", payload)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def _new_row(description, *, client_key=None, parts=None):
    row = {"labor_id": "", "description": description, "hours": "", "rate_code": "",
           "issue_description": "", "parts": parts or []}
    if client_key:
        row["client_key"] = client_key
    return row


def _descs(mongo, wo_id):
    return [b["labor"]["description"] for b in _wo(mongo, wo_id)["labors"]]


def _rows_like(wo, prefix):
    return [b for b in wo["labors"] if b["labor"]["description"].startswith(prefix)]


def _views(mongo, wo_id, **flt):
    return list(mongo[SHOP_A_DB].wo_edit_views.find(
        {"work_order_id": ObjectId(str(wo_id)), **flt}).sort("created_at", 1))


def _backdate_timer(mongo, user, seconds):
    shop_db = mongo[SHOP_A_DB]
    log = shop_db.wo_time_logs.find_one({"user_id": user["_id"], "stopped_at": None})
    assert log, "no running timer"
    shop_db.wo_time_logs.update_one(
        {"_id": log["_id"]}, {"$set": {"started_at": log["started_at"] - timedelta(seconds=seconds)}})


class NewApp:
    """Клиент merge_protocol 1 (новая сборка): view_id из GET ?mp=1,
    client_key у новых строк, шлёт все строки с labor_id; если сервер
    сообщил о чужих правках (merged_foreign) — подхватывает labors ответа
    и merged_view_id, иначе его base — sent view (view_id)."""

    def __init__(self, dev, wo_id):
        self.dev, self.wo_id = dev, wo_id
        d = _mobile_details(dev, wo_id, mp=True)
        self.view_id = d["view_id"]
        self.rev = d["labors_rev"]
        self.rows = self._rows(d["labors"])
        self._ck = 0

    @staticmethod
    def _rows(labors):
        return _app_mechanic_rows({"labors": labors})

    def add(self, description, parts=None):
        self._ck += 1
        row = _new_row(description, client_key=f"ck-{id(self)}-{self._ck}", parts=parts)
        self.rows.append(row)
        return row

    def save(self, **extra):
        payload = {
            "merge_protocol": 1, "view_id": self.view_id, "status": "in_progress",
            "labors": self.rows, "mechanic_state": "in_progress", "unit_mileage": "50000",
        }
        payload.update(extra)
        resp = self.dev.post(f"/api/mobile/work_orders/{self.wo_id}", payload)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        j = resp.get_json()
        for r in self.rows:  # id новых строк — по client_key, не по индексу
            if not r["labor_id"] and r.get("client_key") in j["client_keys"]:
                r["labor_id"] = j["client_keys"][r["client_key"]]
        if j["merged_foreign"]:
            self.rows = self._rows(j["labors"])
            self.view_id = j["merged_view_id"]
        else:
            self.view_id = j["view_id"]
        self.rev = j["labors_rev"]
        return j


# ───────────────────────── пинг-понг двух механиков (LTS) ─────────────────────────


def test_legacy_app_ping_pong_keeps_both_mechanics_work(app, mongo, cm):
    """Сценарий LTS на текущих сборках приложения: два механика, у каждого
    своя (устаревшая) копия WO, автосейвы по очереди — ни одна работа не
    пропадает, правки друг друга не откатываются."""
    a, b = Device(app, MECH_A), Device(app, MECH_B)
    wo_id = _create_mech_wo(a, cm, [{"description": "Inspect brakes", "parts": []}])
    a_rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    b_rows = _app_mechanic_rows(_mobile_details(b, wo_id))

    # A добавляет работу; приложение A перечитывает WO за id и подхватывает его.
    a_rows.append(_new_row("Replace wiper blades"))
    _app_save(a, wo_id, a_rows)
    a_rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    assert [r["description"] for r in a_rows] == ["Inspect brakes", "Replace wiper blades"]

    # B (форма открыта с самого начала) правит свою работу — работа A цела.
    b_rows[0]["description"] = "Inspect brakes - pads at 3mm"
    _app_save(b, wo_id, b_rows)
    assert _descs(mongo, wo_id) == ["Inspect brakes - pads at 3mm", "Replace wiper blades"]

    # A правит свою работу из своей копии — правка B не откатывается.
    a_rows[1]["description"] = "Replace wiper blades (both)"
    _app_save(a, wo_id, a_rows)
    assert _descs(mongo, wo_id) == ["Inspect brakes - pads at 3mm", "Replace wiper blades (both)"]

    # B снова сохраняет устаревшую форму — работа и правка A остаются.
    b_rows[0]["description"] = "Inspect brakes - pads at 3mm, rotors ok"
    _app_save(b, wo_id, b_rows)
    assert _descs(mongo, wo_id) == ["Inspect brakes - pads at 3mm, rotors ok", "Replace wiper blades (both)"]


def test_protocol1_ping_pong_merges_and_reports_foreign_changes(app, mongo, cm):
    """То же на клиентах merge_protocol 1: правки сливаются, клиенту, в чей
    Save попали чужие правки, сервер говорит merged_foreign + заметку."""
    dev_a = Device(app, MECH_A)
    wo_id = _create_mech_wo(dev_a, cm, [{"description": "Job one", "parts": []}])
    a = NewApp(dev_a, wo_id)
    b = NewApp(Device(app, MECH_B), wo_id)

    a.add("Job two")
    ja = a.save()
    assert ja["merged_foreign"] is False and ja["merge_notes"] == []
    assert a.rows[1]["labor_id"] == ja["client_keys"][a.rows[1]["client_key"]]

    b.rows[0]["description"] = "Job one - started"
    jb = b.save()
    assert jb["merged_foreign"] is True
    assert any('Kept job "Job two" added by another user.' == n for n in jb["merge_notes"])
    assert [r["description"] for r in b.rows] == ["Job one - started", "Job two"]   # подхватил ответ

    a.rows[1]["description"] = "Job two - done"
    ja = a.save()
    assert ja["merged_foreign"] is True                       # «Job one» взят у B
    assert _descs(mongo, wo_id) == ["Job one - started", "Job two - done"]

    b.rows[0]["description"] = "Job one - done"
    b.save()
    assert _descs(mongo, wo_id) == ["Job one - done", "Job two - done"]

    # Удалить работу, которую клиент видел, по-прежнему можно.
    b.rows = [b.rows[0]]
    b.save()
    assert _descs(mongo, wo_id) == ["Job one - done"]


# ───────────────────────── легаси-приложение: выбор base ─────────────────────────


def test_legacy_app_unadopted_refetch_does_not_delete_colleague_job(app, mongo, cm):
    """Ревью: приложение A сохранило новую работу, перечитало WO за id, но
    пользователь печатал — результат выброшен. Следующий автосейв не должен
    ни удалить работу B, ни заново создать работу A."""
    a, b = Device(app, MECH_A), Device(app, MECH_B)
    wo_id = _create_mech_wo(a, cm, [{"description": "Diagnose leak", "parts": []}])
    a_rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    b_rows = _app_mechanic_rows(_mobile_details(b, wo_id))

    b_rows.append(_new_row("Replace hose"))
    _app_save(b, wo_id, b_rows)
    _mobile_details(b, wo_id)                 # рефетч B (подхвачен)

    a_rows.append(_new_row("Oil chan"))
    _app_save(a, wo_id, a_rows)
    n_id = _rows_like(_wo(mongo, wo_id), "Oil")[0]["labor_id"]
    _mobile_details(a, wo_id)                 # рефетч A — приложение его выбросило
    a_rows[1]["description"] = "Oil change"   # строка в приложении всё ещё без id
    _app_save(a, wo_id, a_rows)

    wo = _wo(mongo, wo_id)
    assert "Replace hose" in _descs(mongo, wo_id)
    oil = _rows_like(wo, "Oil")
    assert len(oil) == 1
    assert oil[0]["labor_id"] == n_id and oil[0]["labor"]["description"] == "Oil change"


def _backdate_views(mongo, wo_id, user, seconds):
    """Все вьюхи пользователя на WO — на seconds раньше (порядок тот же)."""
    mongo[SHOP_A_DB].wo_edit_views.update_many(
        {"work_order_id": ObjectId(str(wo_id)), "user_id": str(user["_id"])},
        [{"$set": {"created_at": {"$subtract": ["$created_at", int(seconds * 1000)]}}}],
    )


@pytest.mark.parametrize("delay", [0, 20, 600])
def test_legacy_app_slow_discarded_refetch_never_deletes_colleague_job(app, mongo, cm, delay):
    """Ревью D1-01 / D4-03: рефетч старого приложения дошёл до сервера через
    20 с / 10 мин после автосейва (плохая сеть, приложение в фоне), а
    пользователь печатал — приложение его выбросило. Раньше такой рефетч
    после 15 с считался «новым открытием формы»: работа коллеги, попавшая в
    него, удалялась, а своя новая работа создавалась заново с новым id."""
    a, b = Device(app, MECH_A), Device(app, MECH_B)
    wo_id = _create_mech_wo(a, cm, [{"description": "Diagnose leak", "parts": []}])
    a_rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    a_rows.append(_new_row("Oil chan"))
    _app_save(a, wo_id, a_rows)
    n_id = _rows_like(_wo(mongo, wo_id), "Oil")[0]["labor_id"]
    _backdate_views(mongo, wo_id, cm["users"][MECH_A], delay)

    b_rows = _app_mechanic_rows(_mobile_details(b, wo_id))
    b_rows.append(_new_row("Replace hose"))
    _app_save(b, wo_id, b_rows)
    _mobile_details(b, wo_id)                 # рефетч B (подхвачен)

    _mobile_details(a, wo_id)                 # рефетч A — приложение его выбросило
    a_rows[1]["description"] = "Oil change"   # строка в приложении всё ещё без id
    j = _app_save(a, wo_id, a_rows)

    assert _descs(mongo, wo_id) == ["Diagnose leak", "Oil change", "Replace hose"]
    oil = _rows_like(_wo(mongo, wo_id), "Oil")
    assert len(oil) == 1 and oil[0]["labor_id"] == n_id
    assert j["merged_foreign"] is True


def test_legacy_app_reopened_form_recreates_a_job_the_office_deleted(app, mongo, cm):
    """Перестраховка правила D1-01: N (новую работу механика) офис удалил,
    механик позже открыл форму заново и добавил такую же работу — она
    создаётся, а не выбрасывается как «удалённая другим»."""
    a = Device(app, MECH_A)
    owner = Device(app, OWNER_EMAIL, OWNER_PASSWORD)
    wo_id = _create_mech_wo(a, cm, [{"description": "Diagnose leak", "parts": []}])
    a_rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    a_rows.append(_new_row("Oil change"))
    _app_save(a, wo_id, a_rows)                              # S: minted [N]
    _mobile_details(a, wo_id)                                # рефетч подхвачен, механик ушёл

    form = _app_send(_app_manager_rows(_mobile_details(owner, wo_id)))
    resp = owner.post(f"/api/mobile/work_orders/{wo_id}",
                      {"status": "in_progress", "labors": [r for r in form if r["description"] != "Oil change"]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _descs(mongo, wo_id) == ["Diagnose leak"]
    _backdate_views(mongo, wo_id, cm["users"][MECH_A], 120)

    a_rows = _app_mechanic_rows(_mobile_details(a, wo_id))  # форма открыта заново
    a_rows.append(_new_row("Oil change"))
    _app_save(a, wo_id, a_rows)
    assert _descs(mongo, wo_id) == ["Diagnose leak", "Oil change"]


def test_legacy_app_typing_through_refetches_keeps_an_office_deletion(app, mongo, cm):
    """Ревью F1-RR-02: механик добавил работу и всё время печатает — каждый
    рефетч старого приложения выброшен, строка у него так и остаётся без id.
    Офис эту работу удалил: следующий автосейв её пропускает, и рефетч после
    него (уже без работы) тоже выброшен. Раньше этот рефетч считался «новым
    открытием формы», и через автосейв удалённая офисом работа молча
    создавалась заново. Открыв форму заново, механик может добавить её снова."""
    a = Device(app, MECH_A)
    owner = Device(app, OWNER_EMAIL, OWNER_PASSWORD)
    wo_id = _create_mech_wo(a, cm, [{"description": "Diagnose leak", "parts": []}])
    a_rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    a_rows.append(_new_row("Oil change"))
    _app_save(a, wo_id, a_rows)                                   # S1: minted [N]
    _mobile_details(a, wo_id)                                     # рефетч выброшен (печатает)

    form = _app_send(_app_manager_rows(_mobile_details(owner, wo_id)))
    resp = owner.post(f"/api/mobile/work_orders/{wo_id}",
                      {"status": "in_progress", "labors": [r for r in form if r["description"] != "Oil change"]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _descs(mongo, wo_id) == ["Diagnose leak"]

    a_rows[0]["description"] = "Diagnose leak - hose"
    j = _app_save(a, wo_id, a_rows)                               # S2: работа пропущена
    assert _descs(mongo, wo_id) == ["Diagnose leak - hose"]
    assert 'Job "Oil change" was removed by another user.' in j["merge_notes"]
    _mobile_details(a, wo_id)                                     # рефетч без работы — выброшен
    a_rows[0]["description"] = "Diagnose leak - hose clamp"
    j = _app_save(a, wo_id, a_rows)                               # S3
    assert _descs(mongo, wo_id) == ["Diagnose leak - hose clamp"]
    assert 'Job "Oil change" was removed by another user.' in j["merge_notes"]

    _mobile_details(a, wo_id)                                     # рефетч S3 подхвачен: работы нет
    a_rows = _app_mechanic_rows(_mobile_details(a, wo_id))        # форма открыта заново
    a_rows.append(_new_row("Oil change"))
    _app_save(a, wo_id, a_rows)
    assert _descs(mongo, wo_id) == ["Diagnose leak - hose clamp", "Oil change"]


def test_legacy_app_adopted_refetch_allows_deleting_a_seen_job(app, mongo, cm):
    a, b = Device(app, MECH_A), Device(app, MECH_B)
    wo_id = _create_mech_wo(a, cm, [{"description": "Diagnose noise", "parts": []}])
    a_rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    b_rows = _app_mechanic_rows(_mobile_details(b, wo_id))
    b_rows.append(_new_row("Possible duplicate"))
    _app_save(b, wo_id, b_rows)

    a_rows.append(_new_row("Replace belt"))
    _app_save(a, wo_id, a_rows)
    # Рефетч подхвачен: приложение A теперь видит все три работы с id.
    a_rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    assert sorted(r["description"] for r in a_rows) == ["Diagnose noise", "Possible duplicate", "Replace belt"]

    # A удаляет работу B, которую видел (корзина в приложении).
    _app_save(a, wo_id, [r for r in a_rows if r["description"] != "Possible duplicate"])
    assert _descs(mongo, wo_id) == ["Diagnose noise", "Replace belt"]


def test_two_devices_one_login_do_not_delete_each_others_jobs(app, mongo, cm):
    """Один логин механика на двух планшетах: у каждого устройства своя
    cookie-сессия (sid), base выбирается только из вьюх этого устройства."""
    p1, p2 = Device(app, MECH_A), Device(app, MECH_A)
    wo_id = _create_mech_wo(p1, cm, [{"description": "Shared tablet job", "parts": []}])
    p1_rows = _app_mechanic_rows(_mobile_details(p1, wo_id))
    p2_rows = _app_mechanic_rows(_mobile_details(p2, wo_id))

    p2_rows.append(_new_row("Second tablet job"))
    _app_save(p2, wo_id, p2_rows)
    _mobile_details(p2, wo_id)                # рефетч второго планшета

    p1_rows[0]["description"] = "Shared tablet job - done"
    _app_save(p1, wo_id, p1_rows)
    assert _descs(mongo, wo_id) == ["Shared tablet job - done", "Second tablet job"]

    user_views = _views(mongo, wo_id, user_id=str(cm["users"][MECH_A]["_id"]))
    assert len({v["sid"] for v in user_views}) == 2


def test_overlapping_legacy_saves_do_not_duplicate_a_new_job(app, mongo, cm):
    """Старое приложение: автосейв с новой строкой и «Done» уходят
    одновременно — второй запрос не видит sent-вьюху первого. Метка minted в
    самом WO привязывает строку — дубля нет, склад списан один раз."""
    pads = cm["parts"]["pads"]
    a = Device(app, MECH_A)
    wo_id = _create_mech_wo(a, cm, [{"description": "Base job", "parts": []}])
    rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    rows.append(_new_row("Brake fluid flush", parts=[{
        "part_id": str(pads["_id"]), "part_number": pads["part_number"], "description": "",
        "qty": 1, "one_time_part": False}]))
    stock = _stock(mongo, pads)
    _app_save(a, wo_id, rows)

    # Второй запрос стартовал раньше, чем первый записал свою sent-вьюху.
    mongo[SHOP_A_DB].wo_edit_views.update_many(
        {"work_order_id": ObjectId(wo_id), "kind": "sent"},
        {"$set": {"created_at": _now() + timedelta(minutes=1)}},
    )
    _app_save(a, wo_id, rows, mechanic_state="done")

    wo = _wo(mongo, wo_id)
    assert len(_rows_like(wo, "Brake fluid flush")) == 1
    assert _stock(mongo, pads) == stock - 1
    assert wo["mechanic_done"] is True


def test_consecutive_idless_legacy_saves_keep_one_job_and_others_changes(app, mongo, cm):
    """Пользователь всё время печатает — приложение так и не узнаёт id новой
    строки. Каждый автосейв привязывается к той же строке (дубля нет, id не
    меняется), чужие изменения этой строки (часы офиса) не теряются."""
    a = Device(app, MECH_A)
    wo_id = _create_mech_wo(a, cm, [{"description": "First job", "parts": []}])
    rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    rows.append(_new_row("Coolant"))
    _app_save(a, wo_id, rows)
    first_id = _rows_like(_wo(mongo, wo_id), "Coolant")[0]["labor_id"]

    for i, text in enumerate(["Coolant flush", "Coolant flush and fill", "Coolant flush and fill, tested"]):
        if i == 1:
            # Офис тем временем проставил этой работе часы.
            mongo[SHOP_A_DB].work_orders.update_one(
                {"_id": ObjectId(wo_id)},
                {"$set": {"labors.$[b].labor.hours": "2"}, "$inc": {"labors_rev": 1}},
                array_filters=[{"b.labor_id": first_id}],
            )
        rows[1]["description"] = text
        _app_save(a, wo_id, rows)
        coolant = _rows_like(_wo(mongo, wo_id), "Coolant")
        assert len(coolant) == 1 and coolant[0]["labor_id"] == first_id, text
        assert coolant[0]["labor"]["description"] == text
    assert coolant[0]["labor"]["hours"] == "2"


def test_timer_refresh_keeps_merge_markers_so_idless_row_still_maps(app, mongo, cm):
    """Новую строку старого приложения коллега успел затрекать: стоп таймера
    переписывает labors (refresh) — метки minted/client_key обязаны пережить
    пересчёт, иначе следующий автосейв приложения задублировал бы работу."""
    a, b = Device(app, MECH_A), Device(app, MECH_B)
    wo_id = _create_mech_wo(a, cm, [{"description": "First job", "parts": []}])
    rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    rows.append(_new_row("Replace sensor"))
    _app_save(a, wo_id, rows)
    sensor = _rows_like(_wo(mongo, wo_id), "Replace sensor")[0]
    assert sensor["minted"]["by"] == str(cm["users"][MECH_A]["_id"])

    assert b.post("/work_orders/api/mechanic/timers/start",
                  {"work_order_id": wo_id, "labor_id": sensor["labor_id"]}).status_code == 200
    _backdate_timer(mongo, cm["users"][MECH_B], 1800)
    b.post("/work_orders/api/mechanic/timers/stop", {})
    refreshed = _rows_like(_wo(mongo, wo_id), "Replace sensor")[0]
    assert refreshed["labor"]["hours_source"] == "tracked"
    assert refreshed["minted"] == sensor["minted"]

    rows[1]["description"] = "Replace sensor (O2)"     # id в приложении так и не появился
    _app_save(a, wo_id, rows)
    sensors = _rows_like(_wo(mongo, wo_id), "Replace sensor")
    assert len(sensors) == 1 and sensors[0]["labor_id"] == sensor["labor_id"]
    assert sensors[0]["labor"]["hours"] == "0.5"         # часы B не потерялись


def test_failed_load_with_old_view_does_not_wipe_the_wo(app, mongo, cm):
    """Старое приложение не смогло загрузить WO (пустая форма), механик
    набрал работу: в payload нет ни одного labor_id — по старой вьюхе ничего
    не удаляется."""
    a = Device(app, MECH_A)
    wo_id = _create_mech_wo(a, cm, [{"description": "Job A", "parts": []}, {"description": "Job B", "parts": []}])
    _mobile_details(a, wo_id)                  # вчерашнее открытие
    j = _app_save(a, wo_id, [_new_row("Typed into empty form")])
    # Строки, которых клиент «не знает», — в конец (его индексы не съезжают).
    assert _descs(mongo, wo_id) == ["Typed into empty form", "Job A", "Job B"]
    assert j["merged_foreign"] is True


def test_stale_legacy_save_does_not_resurrect_a_job_deleted_by_colleague(app, mongo, cm):
    a, b = Device(app, MECH_A), Device(app, MECH_B)
    wo_id = _create_mech_wo(a, cm, [{"description": "Wheel bearing", "parts": []},
                                    {"description": "Old note job", "parts": []}])
    a_rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    b_rows = _app_mechanic_rows(_mobile_details(b, wo_id))

    _app_save(b, wo_id, [b_rows[0]])           # B удалил вторую работу
    assert _descs(mongo, wo_id) == ["Wheel bearing"]

    a_rows[0]["description"] = "Wheel bearing - front left"
    _app_save(a, wo_id, a_rows)                # A её не трогал — не воскрешаем
    assert _descs(mongo, wo_id) == ["Wheel bearing - front left"]

    a_rows[1]["description"] = "Old note job - actually needed"
    _app_save(a, wo_id, a_rows)                # A её поменял — возвращается
    assert _descs(mongo, wo_id) == ["Wheel bearing - front left", "Old note job - actually needed"]


def _misc_items_of(block):
    import json as _json

    parts = block.get("parts") or []
    raw = str((parts[0].get("misc_charge_description") if parts else "") or "").strip()
    return _json.loads(raw) if raw.startswith("[") else []


def _block_like(wo, prefix):
    return next(b for b in wo["labors"] if b["labor"]["description"].startswith(prefix))


def test_job_restored_from_the_mechanics_sent_view_keeps_its_epa_fee(app, mongo, cm):
    """Ревью D4-01: base механика — его sent-вьюха (после автосейва). Коллега
    удалил работу с аккумулятором, механик её поправил — работа
    возвращается вместе с EPA-сбором каталога (раньше sent-вьюха misc не
    хранила, и восстановленная работа теряла сбор — недобор в счёте)."""
    batt = cm["parts"]["battery"]
    a, b = Device(app, MECH_A), Device(app, MECH_B)
    wo_id = _create_mech_wo(a, cm, [
        {"description": "Main job", "parts": []},
        {"description": "Battery swap", "parts": [{"part_id": str(batt["_id"]), "qty": 1}]},
    ])
    grand_before = _wo(mongo, wo_id)["totals"]["grand_total"]
    assert [i["description"] for i in _misc_items_of(_block_like(_wo(mongo, wo_id), "Battery"))] == ["EPA fee"]

    a_rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    a_rows[0]["description"] = "Main job - started"
    _app_save(a, wo_id, a_rows)                               # base A — его sent-вьюха
    b_rows = _app_mechanic_rows(_mobile_details(b, wo_id))
    _app_save(b, wo_id, [r for r in b_rows if r["description"] != "Battery swap"])
    assert _descs(mongo, wo_id) == ["Main job - started"]

    a_rows[1]["description"] = "Battery swap - tested"
    j = _app_save(a, wo_id, a_rows)
    wo = _wo(mongo, wo_id)
    blk = _block_like(wo, "Battery")
    assert any(n.startswith('Restored job "Battery swap - tested"') for n in j["merge_notes"])
    assert [(i["description"], i["quantity"]) for i in _misc_items_of(blk)] == [("EPA fee", 1)]
    assert wo["totals"]["grand_total"] == grand_before


@pytest.mark.parametrize("base_is_sent_view", [True, False])
def test_part_restored_after_a_colleague_removed_it_keeps_its_epa_fee(app, mongo, cm, base_is_sent_view):
    """Ревью D4-01: коллега убрал аккумулятор (единственную запчасть работы),
    механик (устаревшая копия) поменял его qty — запчасть возвращается со
    своим EPA-сбором; base — его sent-вьюха или load-вьюха."""
    batt = cm["parts"]["battery"]
    a, b = Device(app, MECH_A), Device(app, MECH_B)
    wo_id = _create_mech_wo(a, cm, [
        {"description": "Battery swap", "parts": [{"part_id": str(batt["_id"]), "qty": 1}]},
    ])
    a_rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    if base_is_sent_view:
        a_rows[0]["description"] = "Battery swap - started"
        _app_save(a, wo_id, a_rows)
    b_rows = _app_mechanic_rows(_mobile_details(b, wo_id))
    b_rows[0]["parts"] = []
    _app_save(b, wo_id, b_rows)
    assert _wo(mongo, wo_id)["labors"][0]["parts"] == []

    a_rows[0]["parts"][0]["qty"] = 2
    j = _app_save(a, wo_id, a_rows)
    blk = _wo(mongo, wo_id)["labors"][0]
    assert [(str(p["part_id"]), p["qty"]) for p in blk["parts"]] == [(str(batt["_id"]), 2)]
    assert [i["description"] for i in _misc_items_of(blk)] == ["EPA fee"]
    assert any(n.startswith('Restored part "Battery"') for n in j["merge_notes"])


# ───────────────────────── ретраи, таймеры, пустые строки ─────────────────────────


def test_client_key_retry_after_lost_response_does_not_duplicate(app, mongo, cm):
    pads = cm["parts"]["pads"]
    a = Device(app, MECH_A)
    wo_id = _create_mech_wo(a, cm, [{"description": "Existing job", "parts": []}])
    d = _mobile_details(a, wo_id, mp=True)
    rows = _app_mechanic_rows(d)
    rows.append(_new_row("Alignment", client_key="ck-align-1", parts=[{
        "part_id": str(pads["_id"]), "part_number": pads["part_number"], "description": "",
        "qty": 1, "one_time_part": False}]))
    payload = {"merge_protocol": 1, "view_id": d["view_id"], "status": "in_progress",
               "labors": rows, "mechanic_state": "in_progress", "unit_mileage": "50000"}
    stock = _stock(mongo, pads)

    first = a.post(f"/api/mobile/work_orders/{wo_id}", payload).get_json()   # ответ «потерялся»
    second = a.post(f"/api/mobile/work_orders/{wo_id}", payload).get_json()  # тот же запрос ещё раз

    assert first["client_keys"]["ck-align-1"] == second["client_keys"]["ck-align-1"]
    assert first["labor_ids"] == second["labor_ids"]
    assert _descs(mongo, wo_id) == ["Existing job", "Alignment"]
    assert _stock(mongo, pads) == stock - 1
    assert not any("another user" in n for n in second["merge_notes"])


def test_job_with_running_timer_is_not_deleted_by_a_stale_save(app, mongo, cm):
    a, b = Device(app, MECH_A), Device(app, MECH_B)
    wo_id = _create_mech_wo(a, cm, [{"description": "Brakes", "parts": []},
                                    {"description": "Exhaust", "parts": []}])
    a_rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    exhaust_id = a_rows[1]["labor_id"]
    resp = b.post("/work_orders/api/mechanic/timers/start", {"work_order_id": wo_id, "labor_id": exhaust_id})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    try:
        # A (старая сборка) удаляет «Exhaust», пока B на ней работает.
        _app_save(a, wo_id, [a_rows[0]])
        assert _descs(mongo, wo_id) == ["Brakes", "Exhaust"]

        # Новый клиент получает заметку и перечитывает WO.
        app_a = NewApp(a, wo_id)
        app_a.rows = [app_a.rows[0]]
        j = app_a.save()
        assert _descs(mongo, wo_id) == ["Brakes", "Exhaust"]
        assert j["merged_foreign"] is True
        assert any("someone is working on it" in n for n in j["merge_notes"])
    finally:
        b.post("/work_orders/api/mechanic/timers/stop", {})


def test_issue_only_row_is_kept_silently_for_the_legacy_app(app, mongo, cm):
    """Строка только с описанием проблемы (нет описания работы и запчастей):
    старое приложение её не шлёт — это не удаление и не «чужая правка»."""
    a = Device(app, MECH_A)
    wo_id = _create_mech_wo(a, cm, [{"description": "Main job", "parts": []}])
    blank_id = str(ObjectId())
    mongo[SHOP_A_DB].work_orders.update_one({"_id": ObjectId(wo_id)}, {
        "$push": {"labors": {"labor_id": blank_id, "labor": {
            "description": "", "hours": "", "rate_code": "standard",
            "issue_description": "Customer: rattle at idle", "assigned_mechanics": []}, "parts": []}},
        "$inc": {"labors_rev": 1},
    })
    rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    for text in ("Main job - started", "Main job - halfway"):
        rows[0]["description"] = text
        j = _app_save(a, wo_id, rows)
        wo = _wo(mongo, wo_id)
        assert [b["labor_id"] for b in wo["labors"]][1] == blank_id
        assert wo["labors"][1]["labor"]["issue_description"] == "Customer: rattle at idle"
        assert j["merged_foreign"] is False and j["merge_notes"] == []


# ───────────────────────── CAS: гонки, исчерпание, guard'ы в фильтре ─────────────────────────


def test_refresh_time_derived_fields_retries_instead_of_overwriting_a_save(app, mongo, cm, monkeypatch):
    from app.blueprints.work_orders.services import time_tracking
    from app.blueprints.work_orders.services.mechanic_editor import refresh_time_derived_fields

    shop_db = mongo[SHOP_A_DB]
    a = Device(app, MECH_A)
    wo_id = _create_mech_wo(a, cm, [{"description": "Tracked job", "parts": []}])
    lid = _wo(mongo, wo_id)["labors"][0]["labor_id"]
    now = _now()
    shop_db.wo_time_logs.insert_one({
        "shop_id": cm["shop"]["_id"], "work_order_id": ObjectId(wo_id), "labor_id": lid,
        "user_id": cm["users"][MECH_A]["_id"], "user_name": "Andy Concurrent",
        "started_at": now, "stopped_at": now, "seconds": 3600, "stop_source": "user",
        "created_at": now, "updated_at": now,
    })
    real = time_tracking.summarize_wo_time
    calls = {"n": 0}

    def racing_summary(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # Между чтением WO и записью пересчёта успевает сохранение механика.
            shop_db.work_orders.update_one({"_id": ObjectId(wo_id)}, {
                "$push": {"labors": {"labor_id": str(ObjectId()), "labor": {
                    "description": "Added during refresh", "hours": "", "rate_code": "standard",
                    "assigned_mechanics": [], "issue_description": ""}, "parts": []}},
                "$inc": {"labors_rev": 1},
            })
        return real(*args, **kwargs)

    monkeypatch.setattr(time_tracking, "summarize_wo_time", racing_summary)
    with app.app_context():
        assert refresh_time_derived_fields(shop_db, cm["shop"], wo_id) is True
    assert calls["n"] == 2
    wo = _wo(mongo, wo_id)
    assert _descs(mongo, wo_id) == ["Tracked job", "Added during refresh"]
    assert wo["labors"][0]["labor"]["hours"] == "1.0"
    assert wo["labors"][0]["labor"]["hours_source"] == "tracked"


def test_refresh_gives_up_quietly_when_rev_keeps_moving(app, mongo, cm, monkeypatch):
    from app.blueprints.work_orders.services import time_tracking
    from app.blueprints.work_orders.services.mechanic_editor import refresh_time_derived_fields

    shop_db = mongo[SHOP_A_DB]
    a = Device(app, MECH_A)
    wo_id = _create_mech_wo(a, cm, [{"description": "Busy refresh", "parts": []}])
    lid = _wo(mongo, wo_id)["labors"][0]["labor_id"]
    now = _now()
    shop_db.wo_time_logs.insert_one({
        "shop_id": cm["shop"]["_id"], "work_order_id": ObjectId(wo_id), "labor_id": lid,
        "user_id": cm["users"][MECH_A]["_id"], "user_name": "Andy Concurrent",
        "started_at": now, "stopped_at": now, "seconds": 1800, "stop_source": "user",
        "created_at": now, "updated_at": now,
    })
    real = time_tracking.summarize_wo_time

    def always_racing(*args, **kwargs):
        shop_db.work_orders.update_one({"_id": ObjectId(wo_id)}, {"$inc": {"labors_rev": 1}})
        return real(*args, **kwargs)

    monkeypatch.setattr(time_tracking, "summarize_wo_time", always_racing)
    with app.app_context():
        assert refresh_time_derived_fields(shop_db, cm["shop"], wo_id) is False
    assert _wo(mongo, wo_id)["labors"][0]["labor"]["hours"] == ""


def test_save_gives_up_with_409_and_no_side_effects_when_rev_keeps_moving(app, mongo, cm, monkeypatch):
    from app.blueprints.work_orders.services import edit_views

    pads = cm["parts"]["pads"]
    a = Device(app, MECH_A)
    wo_id = _create_mech_wo(a, cm, [{"description": "Contended job",
                                     "parts": [{"part_id": str(pads["_id"]), "qty": 1}]}])
    rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    rows[0]["parts"][0]["qty"] = 3
    real = edit_views.running_labor_ids

    def always_racing(shop_db, shop_id, work_order_id):
        shop_db.work_orders.update_one({"_id": ObjectId(str(work_order_id))}, {"$inc": {"labors_rev": 1}})
        return real(shop_db, shop_id, work_order_id)

    monkeypatch.setattr(edit_views, "running_labor_ids", always_racing)
    stock = _stock(mongo, pads)
    before = _wo(mongo, wo_id)
    resp = a.post(f"/api/mobile/work_orders/{wo_id}", {
        "status": "in_progress", "labors": rows, "mechanic_state": "in_progress", "unit_mileage": "50000"})
    assert resp.status_code == 409
    assert resp.get_json() == edit_views.CONCURRENT_UPDATE_RESPONSE
    after = _wo(mongo, wo_id)
    assert after["labors"] == before["labors"]
    assert _stock(mongo, pads) == stock           # склад — только после успешной записи


def test_save_retries_and_merges_a_concurrent_write(app, mongo, cm, monkeypatch):
    from app.blueprints.work_orders.services import edit_views

    pads = cm["parts"]["pads"]
    a = Device(app, MECH_A)
    wo_id = _create_mech_wo(a, cm, [{"description": "Retry job",
                                     "parts": [{"part_id": str(pads["_id"]), "qty": 1}]}])
    rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    rows[0]["parts"][0]["qty"] = 3
    real = edit_views.running_labor_ids
    calls = {"n": 0}

    def race_once(shop_db, shop_id, work_order_id):
        calls["n"] += 1
        if calls["n"] == 1:
            shop_db.work_orders.update_one({"_id": ObjectId(str(work_order_id))}, {
                "$push": {"labors": {"labor_id": str(ObjectId()), "labor": {
                    "description": "Added by office", "hours": "1", "rate_code": "standard",
                    "assigned_mechanics": [], "issue_description": ""}, "parts": []}},
                "$inc": {"labors_rev": 1},
            })
        return real(shop_db, shop_id, work_order_id)

    monkeypatch.setattr(edit_views, "running_labor_ids", race_once)
    stock = _stock(mongo, pads)
    resp = a.post(f"/api/mobile/work_orders/{wo_id}", {
        "status": "in_progress", "labors": rows, "mechanic_state": "in_progress", "unit_mileage": "50000"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    j = resp.get_json()
    wo = _wo(mongo, wo_id)
    assert calls["n"] == 2
    assert _descs(mongo, wo_id) == ["Retry job", "Added by office"]
    assert wo["labors"][0]["parts"][0]["qty"] == 3
    assert _stock(mongo, pads) == stock - 2       # разница qty списана один раз
    assert j["labors_rev"] == wo["labors_rev"]
    assert j["merged_foreign"] is True


@pytest.mark.parametrize("change, channel, status_code, error", [
    ({"status": "paid"}, "mobile", 400, "paid_cannot_edit"),
    ({"manager_confirmed": True}, "mobile", 403, "wo_confirmed"),
    ({"status": "estimate"}, "mobile", 400, "estimate_web_only"),
    ({"status": "paid"}, "web", 400, "paid_cannot_edit"),
    ({"manager_confirmed": True}, "web", 403, "wo_confirmed"),
    ({"status": "estimate"}, "web", 400, "estimate_locked_for_mechanics"),
])
def test_lock_between_read_and_write_is_caught_by_the_cas_filter(app, mongo, cm, monkeypatch,
                                                                change, channel, status_code, error):
    """Оплата / Confirm / смета ревизию не двигают — их ловит сам фильтр
    записи; повторная попытка видит новое состояние и отвечает как guard."""
    from app.blueprints.work_orders.services import edit_views

    dev = Device(app, MECH_A, mobile=(channel == "mobile"))
    wo_id = _create_mech_wo(dev, cm, [{"description": "Locked meanwhile", "parts": []}])
    if channel == "mobile":
        rows = _app_mechanic_rows(_mobile_details(dev, wo_id))
        url, payload = f"/api/mobile/work_orders/{wo_id}", {
            "status": "in_progress", "labors": rows, "mechanic_state": "in_progress", "unit_mileage": "50000"}
    else:
        rows = _web_mechanic_rows(_web_details(dev, wo_id))
        url, payload = f"/work_orders/api/mechanic/work_orders/{wo_id}", {"labors": rows, "unit_mileage": "50000"}
    rows[0]["description"] = "Should not be written"
    real = edit_views.running_labor_ids
    calls = {"n": 0}

    def lock_once(shop_db, shop_id, work_order_id):
        calls["n"] += 1
        if calls["n"] == 1:
            shop_db.work_orders.update_one({"_id": ObjectId(str(work_order_id))}, {"$set": change})
        return real(shop_db, shop_id, work_order_id)

    monkeypatch.setattr(edit_views, "running_labor_ids", lock_once)
    before = _wo(mongo, wo_id)
    resp = dev.post(url, payload)
    assert resp.status_code == status_code, resp.get_data(as_text=True)
    assert resp.get_json()["error"] == error
    after = _wo(mongo, wo_id)
    assert after["labors"] == before["labors"]
    assert after.get("labors_rev") == before.get("labors_rev")
    mongo[SHOP_A_DB].work_orders.update_one({"_id": ObjectId(wo_id)}, {"$set": {"is_active": False}})


def test_timer_start_does_not_reopen_a_wo_paid_meanwhile(app, mongo, cm, monkeypatch):
    """WO оплатили между проверкой старта и вставкой лога: фиксация старта
    (после вставки) видит paid — старт отклоняется, лог удаляется, WO
    остаётся оплаченным, ревизия не двигается. Текст механику — без статуса
    paid (он ему не отдаётся): WO просто «закрыт»."""
    from app.blueprints.work_orders.services import time_tracking

    a = Device(app, MECH_A, mobile=False)
    wo_id = _create_mech_wo(a, cm, [{"description": "Paid race", "parts": []}])
    shop_db = mongo[SHOP_A_DB]
    shop_db.work_orders.update_one({"_id": ObjectId(wo_id)}, {"$set": {"status": "open"}})
    lid = _wo(mongo, wo_id)["labors"][0]["labor_id"]
    rev = _wo(mongo, wo_id)["labors_rev"]
    real = time_tracking._stop_open_logs

    def paid_meanwhile(shop_db_, shop_id, user_id, stop_source):
        shop_db_.work_orders.update_one({"_id": ObjectId(wo_id)}, {"$set": {"status": "paid"}})
        return real(shop_db_, shop_id, user_id, stop_source)

    monkeypatch.setattr(time_tracking, "_stop_open_logs", paid_meanwhile)
    try:
        resp = a.post("/work_orders/api/mechanic/timers/start", {"work_order_id": wo_id, "labor_id": lid})
    finally:
        monkeypatch.undo()
        a.post("/work_orders/api/mechanic/timers/stop", {})
    assert resp.status_code == 400, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["error"] == "paid_cannot_track"
    assert body["message"] == "This work order is locked. Time can't be tracked on it."
    wo = _wo(mongo, wo_id)
    assert wo["status"] == "paid"
    assert wo["labors_rev"] == rev
    assert shop_db.wo_time_logs.count_documents({"work_order_id": ObjectId(wo_id)}) == 0


# ───────────────────────── мобильный менеджер ─────────────────────────


def test_mobile_manager_save_keeps_assignments_tracked_hours_and_units(app, mongo, cm):
    oil = cm["parts"]["oil"]
    a = Device(app, MECH_A)
    owner = Device(app, OWNER_EMAIL, OWNER_PASSWORD)
    wo_id = _create_mech_wo(a, cm, [{"description": "Oil service",
                                     "parts": [{"part_id": str(oil["_id"]), "qty": 5}]}])
    lid = _wo(mongo, wo_id)["labors"][0]["labor_id"]
    assert a.post("/work_orders/api/mechanic/timers/start", {"work_order_id": wo_id, "labor_id": lid}).status_code == 200
    _backdate_timer(mongo, cm["users"][MECH_A], 3600)
    a.post("/work_orders/api/mechanic/timers/stop", {})
    lab = _wo(mongo, wo_id)["labors"][0]["labor"]
    assert lab["hours"] == "1.0" and lab["hours_source"] == "tracked"

    form = _app_send(_app_manager_rows(_mobile_details(owner, wo_id)))   # форма менеджера открыта

    # Механик работает ещё полчаса — часы растут, пока форма открыта.
    a.post("/work_orders/api/mechanic/timers/start", {"work_order_id": wo_id, "labor_id": lid})
    _backdate_timer(mongo, cm["users"][MECH_A], 1800)
    a.post("/work_orders/api/mechanic/timers/stop", {})
    assert _wo(mongo, wo_id)["labors"][0]["labor"]["hours"] == "1.5"

    # Менеджер правит описание и добавляет ещё масло (часы в форме старые).
    form[0]["description"] = "Oil service + top-off"
    form[0]["parts"].append({"part_id": str(oil["_id"]), "part_number": oil["part_number"],
                             "description": "Engine oil", "qty": 1, "cost": 12.0, "price": 21.99,
                             "core_charge": 0, "misc_charge": 0, "misc_charge_description": "",
                             "one_time_part": False})
    resp = owner.post(f"/api/mobile/work_orders/{wo_id}", {"status": "in_progress", "labors": form})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    j = resp.get_json()
    assert "labors" not in j and isinstance(j["grand_total"], float)

    block = _wo(mongo, wo_id)["labors"][0]
    lab = block["labor"]
    assert lab["description"] == "Oil service + top-off"
    assert lab["hours"] == "1.5" and lab["hours_source"] == "tracked"
    assert [str(x["user_id"]) for x in lab["assigned_mechanics"]] == [str(cm["users"][MECH_A]["_id"])]
    assert [p["unit"] for p in block["parts"]] == ["gallon", "gallon"]

    # Осознанная правка часов менеджером — часы ручные, назначения остаются.
    form = _app_send(_app_manager_rows(_mobile_details(owner, wo_id)))
    form[0]["hours"] = "3"
    form[0]["labor_total"] = None
    assert owner.post(f"/api/mobile/work_orders/{wo_id}", {"status": "in_progress", "labors": form}).status_code == 200
    lab = _wo(mongo, wo_id)["labors"][0]["labor"]
    assert lab["hours"] == "3" and lab["hours_source"] == ""
    assert [str(x["user_id"]) for x in lab["assigned_mechanics"]] == [str(cm["users"][MECH_A]["_id"])]


def test_mobile_manager_details_tap_over_open_form_does_not_delete(app, mongo, cm):
    """Старая сборка менеджера: форма открыта, пуш «WO finished» открывает
    поверх неё экран деталей (тот же GET). Сохранение устаревшей формы не
    удаляет работу механика, появившуюся между этими GET."""
    a = Device(app, MECH_A)
    owner = Device(app, OWNER_EMAIL, OWNER_PASSWORD)
    wo_id = _create_mech_wo(a, cm, [{"description": "Approve me", "parts": []}])
    form = _app_send(_app_manager_rows(_mobile_details(owner, wo_id)))

    a_rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    a_rows.append(_new_row("Replace mirror"))
    _app_save(a, wo_id, a_rows)
    _mobile_details(owner, wo_id)               # экран деталей по пушу

    form[0]["description"] = "Approve me - approved"
    assert owner.post(f"/api/mobile/work_orders/{wo_id}", {"status": "open", "labors": form}).status_code == 200
    assert _descs(mongo, wo_id) == ["Approve me - approved", "Replace mirror"]


def test_mobile_manager_form_open_for_long_then_push_tap_does_not_delete(app, mongo, cm):
    """Ревью D1-02: форма менеджера (старая сборка, первое сохранение этого
    WO с телефона — sent-вьюхи нет) простояла открытой 40 минут, пуш открыл
    поверх экран деталей. Раньше в консенсус шли только load за 30 минут —
    base становилась одна вьюха деталей, и работа механика удалялась."""
    owner = Device(app, OWNER_EMAIL, OWNER_PASSWORD)
    mech = Device(app, MECH_A)
    wo_id = _create_mech_wo(mech, cm, [{"description": "Inspect brakes", "parts": []}])
    form = _app_send(_app_manager_rows(_mobile_details(owner, wo_id)))
    _backdate_views(mongo, wo_id, cm["owner"], 40 * 60)
    m_rows = _app_mechanic_rows(_mobile_details(mech, wo_id))
    m_rows.append(_new_row("Replace caliper"))
    _app_save(mech, wo_id, m_rows)
    _mobile_details(owner, wo_id)                          # пуш → экран деталей (тот же GET)

    form[0]["description"] = "Inspect brakes (pads 3mm)"
    resp = owner.post(f"/api/mobile/work_orders/{wo_id}", {"status": "open", "labors": form})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _descs(mongo, wo_id) == ["Inspect brakes (pads 3mm)", "Replace caliper"]


def test_mobile_manager_stale_form_under_a_newer_one_does_not_delete(app, mongo, cm):
    """Ревью D1-V1: форма F1 открыта, механик добавил работу, пуш → детали →
    правка → форма F2 сохранена → назад (детали перечитаны) → назад к
    устаревшей F1 → Save. Работа механика (и правка F2 в ней) остаются."""
    owner = Device(app, OWNER_EMAIL, OWNER_PASSWORD)
    mech = Device(app, MECH_A)
    wo_id = _create_mech_wo(mech, cm, [{"description": "Inspect brakes", "parts": []}])
    f1 = _app_send(_app_manager_rows(_mobile_details(owner, wo_id)))
    m_rows = _app_mechanic_rows(_mobile_details(mech, wo_id))
    m_rows.append(_new_row("Replace caliper"))
    _app_save(mech, wo_id, m_rows)
    _mobile_details(owner, wo_id)                          # пуш → детали
    f2 = _app_send(_app_manager_rows(_mobile_details(owner, wo_id)))
    f2[1]["hours"] = "1.5"
    f2[1]["labor_total"] = None
    resp = owner.post(f"/api/mobile/work_orders/{wo_id}", {"status": "open", "labors": f2})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    _mobile_details(owner, wo_id)                          # назад — детали перечитаны

    f1[0]["description"] = "Inspect brakes (pads 3mm)"
    resp = owner.post(f"/api/mobile/work_orders/{wo_id}", {"status": "open", "labors": f1})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    wo = _wo(mongo, wo_id)
    assert _descs(mongo, wo_id) == ["Inspect brakes (pads 3mm)", "Replace caliper"]
    assert wo["labors"][1]["labor"]["hours"] == "1.5"


@pytest.mark.parametrize("added_by", ["newer_form", "mechanic_while_newer_form_open"])
def test_mobile_manager_stale_form_keeps_a_job_added_on_or_during_the_newer_form(app, mongo, cm, added_by):
    """Ревью F1-RR-03: та же стопка форм, но работу добавили в самой F2 или
    механик, пока F2 была открыта (load F1 и F2 держат одни и те же id).
    Save устаревшей F1 эту работу не удаляет; новая форма после этого — может."""
    owner = Device(app, OWNER_EMAIL, OWNER_PASSWORD)
    mech = Device(app, MECH_A)
    wo_id = _create_mech_wo(mech, cm, [{"description": "Inspect brakes", "parts": []}])
    f1 = _app_send(_app_manager_rows(_mobile_details(owner, wo_id)))      # F1 открыта
    _mobile_details(owner, wo_id)                                          # пуш → детали
    f2 = _app_send(_app_manager_rows(_mobile_details(owner, wo_id)))      # правка → F2
    if added_by == "newer_form":
        f2.append(_typed_manager_row("Replace caliper", "1.5"))
    else:
        m_rows = _app_mechanic_rows(_mobile_details(mech, wo_id))
        m_rows.append(_new_row("Replace caliper"))
        _app_save(mech, wo_id, m_rows)
        f2[0]["hours"] = "2"
        f2[0]["labor_total"] = None
    resp = owner.post(f"/api/mobile/work_orders/{wo_id}", {"status": "open", "labors": f2})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _descs(mongo, wo_id) == ["Inspect brakes", "Replace caliper"]
    _mobile_details(owner, wo_id)                                          # назад — детали перечитаны

    f1[0]["description"] = "Inspect brakes (pads 3mm)"
    resp = owner.post(f"/api/mobile/work_orders/{wo_id}", {"status": "open", "labors": f1})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _descs(mongo, wo_id) == ["Inspect brakes (pads 3mm)", "Replace caliper"]

    _mobile_details(owner, wo_id)                                          # назад — детали
    form = _app_send(_app_manager_rows(_mobile_details(owner, wo_id)))    # новая форма
    resp = owner.post(f"/api/mobile/work_orders/{wo_id}", {"status": "open", "labors": form[:1]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _descs(mongo, wo_id) == ["Inspect brakes (pads 3mm)"]


def test_mobile_manager_adds_a_job_and_deletes_it_from_the_next_form(app, mongo, cm):
    """Обычный путь старой сборки менеджера: детали → форма → добавил работу →
    Save (форма закрылась, детали перечитаны) → снова форма → удалил её."""
    owner = Device(app, OWNER_EMAIL, OWNER_PASSWORD)
    wo_id = _create_mech_wo(Device(app, MECH_A), cm, [{"description": "Inspect brakes", "parts": []}])
    _mobile_details(owner, wo_id)                                          # детали
    form = _app_send(_app_manager_rows(_mobile_details(owner, wo_id)))    # форма
    form.append(_typed_manager_row("Wrong job", "1"))
    assert owner.post(f"/api/mobile/work_orders/{wo_id}", {"status": "open", "labors": form}).status_code == 200
    assert _descs(mongo, wo_id) == ["Inspect brakes", "Wrong job"]
    _mobile_details(owner, wo_id)                                          # детали после Save
    form = _app_send(_app_manager_rows(_mobile_details(owner, wo_id)))    # форма снова
    assert owner.post(f"/api/mobile/work_orders/{wo_id}", {"status": "open", "labors": form[:1]}).status_code == 200
    assert _descs(mongo, wo_id) == ["Inspect brakes"]


@pytest.mark.parametrize("bad_parts", [5, True])
def test_non_list_parts_in_a_save_are_ignored_not_a_500(app, mongo, cm, bad_parts):
    """Ревью D1-06: parts не списком в строке сохранения — прежний код
    менеджера молча считал это «нет запчастей» (200), новый падал в 500.
    Ни менеджеру (с вьюхами и без), ни механику — не 500."""
    owner = Device(app, OWNER_EMAIL, OWNER_PASSWORD)
    mech = Device(app, MECH_A)
    pads = cm["parts"]["pads"]
    wo_id = _create_mech_wo(mech, cm, [{"description": "Inspect brakes",
                                        "parts": [{"part_id": str(pads["_id"]), "qty": 1}]}])
    form = _app_send(_app_manager_rows(_mobile_details(owner, wo_id)))
    form[0]["parts"] = bad_parts
    form.append({"labor_id": "", "description": "Odd row", "hours": "1", "rate_code": "standard",
                 "labor_total": None, "issue_description": "", "parts": bad_parts})
    for drop_views in (False, True):
        if drop_views:
            mongo[SHOP_A_DB].wo_edit_views.delete_many({"work_order_id": ObjectId(wo_id),
                                                        "user_id": str(cm["owner"]["_id"])})
        resp = owner.post(f"/api/mobile/work_orders/{wo_id}", {"status": "open", "labors": form})
        assert resp.status_code == 200, resp.get_data(as_text=True)

    rows = _app_mechanic_rows(_mobile_details(mech, wo_id))
    rows[0]["parts"] = bad_parts
    rows.append(dict(_new_row("Mechanic odd row"), parts=bad_parts))
    resp = mech.post(f"/api/mobile/work_orders/{wo_id}", {
        "status": "in_progress", "labors": rows, "mechanic_state": "in_progress", "unit_mileage": "50000"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert "Mechanic odd row" in _descs(mongo, wo_id)


def test_mobile_manager_can_still_delete_a_job_it_saw(app, mongo, cm):
    owner = Device(app, OWNER_EMAIL, OWNER_PASSWORD)
    a = Device(app, MECH_A)
    wo_id = _create_mech_wo(a, cm, [{"description": "Keep", "parts": []}, {"description": "Drop", "parts": []}])
    form = _app_send(_app_manager_rows(_mobile_details(owner, wo_id)))
    assert owner.post(f"/api/mobile/work_orders/{wo_id}", {"status": "open", "labors": [form[0]]}).status_code == 200
    assert _descs(mongo, wo_id) == ["Keep"]


def test_legacy_wo_without_labor_ids_gets_ids_on_open_and_saves_without_duplicates(app, mongo, cm):
    owner = Device(app, OWNER_EMAIL, OWNER_PASSWORD)
    shop_db = mongo[SHOP_A_DB]
    wo_id = ObjectId()
    shop_db.work_orders.insert_one({
        "_id": wo_id, "shop_id": cm["shop"]["_id"], "wo_number": 990101,
        "customer_id": cm["customer"]["_id"], "unit_id": cm["unit"]["_id"], "status": "open",
        "is_active": True, "created_at": _now(), "updated_at": _now(),
        "labors": [
            {"labor": {"description": "Legacy A", "hours": "1", "rate_code": "standard"}, "parts": []},
            {"labor_id": "DUP", "labor": {"description": "Legacy B", "hours": "", "rate_code": ""}, "parts": []},
            {"labor_id": "DUP", "labor": {"description": "Legacy C", "hours": "", "rate_code": ""}, "parts": []},
        ],
        "totals": {"labors": [{"labor": 100.0}, {"labor": 0}, {"labor": 0}], "grand_total": 100.0},
    })
    d = _mobile_details(owner, wo_id)
    ids = [l["labor_id"] for l in d["raw_labors"]]
    assert all(ids) and len(set(ids)) == 3 and ids[1] == "DUP"
    stored = _wo(mongo, wo_id)
    assert [b["labor_id"] for b in stored["labors"]] == ids        # ответ — из записанного документа
    assert stored["labors_rev"] == 1 and d["labors_rev"] == 1
    view = shop_db.wo_edit_views.find_one({"_id": ObjectId(d["view_id"])})
    assert [r["labor_id"] for r in view["labors"]] == ids

    form = _app_send(_app_manager_rows(d))
    form[0]["description"] = "Legacy A edited"
    assert owner.post(f"/api/mobile/work_orders/{wo_id}", {"status": "open", "labors": form}).status_code == 200
    assert _descs(mongo, wo_id) == ["Legacy A edited", "Legacy B", "Legacy C"]
    shop_db.work_orders.update_one({"_id": wo_id}, {"$set": {"is_active": False}})


@pytest.mark.parametrize("ids_saved_meanwhile", [False, True])
def test_pre_deploy_manager_form_on_a_legacy_wo_does_not_duplicate_jobs(app, mongo, cm, ids_saved_meanwhile):
    """Ревью D4-02: форма менеджера открыта до деплоя (прежний GET отдавал
    строкам легаси-WO labor_id "" и id не сохранял), сохранена после. Вьюх
    нет — консервативный merge; ни одна строка формы не узнаётся по id.
    Раньше каждая работа задваивалась (тоталы ×2, склад списан ещё раз);
    теперь — как прежняя запись массива целиком, в т.ч. если id тем
    временем сохранил GET механика."""
    pads = cm["parts"]["pads"]
    owner = Device(app, OWNER_EMAIL, OWNER_PASSWORD)
    shop_db = mongo[SHOP_A_DB]
    wo_id = ObjectId()
    shop_db.work_orders.insert_one({
        "_id": wo_id, "shop_id": cm["shop"]["_id"], "wo_number": 990777,
        "customer_id": cm["customer"]["_id"], "unit_id": cm["unit"]["_id"], "status": "open",
        "is_active": True, "created_at": _now(), "updated_at": _now(),
        "labors": [
            {"labor": {"description": "Legacy A", "hours": "1", "rate_code": "standard"}, "parts": []},
            {"labor": {"description": "Legacy B", "hours": "2", "rate_code": "standard"},
             "parts": [{"part_id": pads["_id"], "part_number": "CM-PADS", "description": "Brake pads",
                        "qty": 1, "cost": 40.0, "price": 99.5, "core_charge": 0, "misc_charge": 0,
                        "misc_charge_description": ""}]},
        ],
        "totals": {"labors": [{"labor": 100.0}, {"labor": 200.0}], "grand_total": 429.5},
    })
    form = [
        {"labor_id": "", "description": "Legacy A edited", "hours": "1", "rate_code": "standard",
         "labor_total": 100.0, "issue_description": "", "parts": []},
        {"labor_id": "", "description": "Legacy B", "hours": "2", "rate_code": "standard",
         "labor_total": 200.0, "issue_description": "",
         "parts": [{"part_id": str(pads["_id"]), "part_number": "CM-PADS", "description": "Brake pads", "qty": 1,
                    "cost": 40.0, "price": 99.5, "core_charge": 0, "misc_charge": 0,
                    "misc_charge_description": "", "one_time_part": False}]},
    ]
    try:
        if ids_saved_meanwhile:
            _mobile_details(Device(app, MECH_A), wo_id)          # GET после деплоя сохранил id
            assert all(b.get("labor_id") for b in _wo(mongo, wo_id)["labors"])
        before = _wo(mongo, wo_id)
        stock = _stock(mongo, pads)
        resp = owner.post(f"/api/mobile/work_orders/{wo_id}", {"status": "open", "labors": form})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        after = _wo(mongo, wo_id)
        assert _descs(mongo, wo_id) == ["Legacy A edited", "Legacy B"]
        assert resp.get_json()["merge_notes"] == []
        assert _stock(mongo, pads) == stock
        if ids_saved_meanwhile:
            assert [b["labor_id"] for b in after["labors"]] == [b["labor_id"] for b in before["labors"]]
        # Деньги — ровно как у прежней записи формы целиком.
        with app.app_context():
            _labors, exp_totals = _legacy_totals(shop_db, cm["shop"], before, copy.deepcopy(form))
        assert after["totals"] == exp_totals
    finally:
        shop_db.work_orders.update_one({"_id": wo_id}, {"$set": {"is_active": False}})


def _typed_manager_row(description, hours):
    """Строка, набранная в форме менеджера старой сборки (emptyLabor + ввод)."""
    return {"labor_id": "", "description": description, "hours": hours, "rate_code": "standard",
            "labor_total": None, "issue_description": "", "parts": []}


@pytest.mark.parametrize("with_view", [False, True])
def test_failed_load_manager_form_never_overwrites_an_unrelated_job(app, mongo, cm, with_view):
    """Ревью F1-RR-01: GET формы менеджера упал (пустая форма), менеджер набрал
    «Oil change» и «Wipers» и сохранил. Страховка D4-02 сопоставляла «Wipers»
    по позиции с работой «Brakes»: та молча становилась «Wipers» на 0.5 ч без
    жалобы клиента (недобор в счёте, тайм-логи — на чужой работе). Теперь
    строки WO не трогаются; с вьюхой (failed_load) и без вьюх (no_candidates)."""
    mech = Device(app, MECH_A)
    owner = Device(app, OWNER_EMAIL, OWNER_PASSWORD)
    wo_id = _create_mech_wo(mech, cm, [{"description": "Oil change", "parts": []},
                                       {"description": "Brakes", "parts": []}])
    mongo[SHOP_A_DB].work_orders.update_one(
        {"_id": ObjectId(wo_id)},
        {"$set": {"labors.1.labor.issue_description": "Squeal at low speed", "labors.1.labor.hours": "3"},
         "$inc": {"labors_rev": 1}},
    )
    assert not any(isinstance(b.get("minted"), dict) for b in _wo(mongo, wo_id)["labors"])
    if with_view:
        _mobile_details(owner, wo_id)                     # раньше открывал экран деталей
    before = {b["labor_id"]: b["labor"] for b in _wo(mongo, wo_id)["labors"]}

    resp = owner.post(f"/api/mobile/work_orders/{wo_id}", {
        "status": "open", "labors": [_typed_manager_row("Oil change", "1.5"), _typed_manager_row("Wipers", "0.5")]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    after = {b["labor_id"]: b["labor"] for b in _wo(mongo, wo_id)["labors"]}
    for lid, lab in before.items():
        assert after[lid]["description"] == lab["description"], after[lid]
        assert after[lid]["issue_description"] == lab["issue_description"]
        assert after[lid]["hours"] == lab["hours"]
    assert after[list(before)[1]]["issue_description"] == "Squeal at low speed"
    assert sorted(_descs(mongo, wo_id)) == ["Brakes", "Oil change", "Oil change", "Wipers"]


# ───────────────────────── веб-страница механика ─────────────────────────


def test_mechanic_web_old_js_never_deletes_by_absence_new_protocol_does(app, mongo, cm):
    web = Device(app, MECH_A, mobile=False)
    wo_id = _create_mech_wo(web, cm, [{"description": "Keep", "parts": []}, {"description": "Remove", "parts": []}])
    rows = _web_mechanic_rows(_web_details(web, wo_id))
    url = f"/work_orders/api/mechanic/work_orders/{wo_id}"

    # Старый JS без view_id: строка пропала из payload — не удаляется, клиенту заметка.
    j = web.post(url, {"labors": [rows[0]], "unit_mileage": "50000"}).get_json()
    assert _descs(mongo, wo_id) == ["Keep", "Remove"]
    assert j["merged_foreign"] is True
    assert any("not in your copy" in n for n in j["merge_notes"])
    assert j["view_id"] == ""                      # легаси office/web: вьюхи не пишутся

    # Новый JS (merge_protocol 1 + view_id из GET): удаление увиденной строки работает.
    d = _web_details(web, wo_id, mp=True)
    rows = _web_mechanic_rows(d)
    j = web.post(url, {"merge_protocol": 1, "view_id": d["view_id"], "labors": [rows[0]],
                       "unit_mileage": "50000"}).get_json()
    assert _descs(mongo, wo_id) == ["Keep"]
    assert j["merged_foreign"] is False


def test_unresolvable_view_id_falls_back_to_conservative_merge(app, mongo, cm):
    web = Device(app, MECH_A, mobile=False)
    wo_id = _create_mech_wo(web, cm, [{"description": "Stays", "parts": []}, {"description": "Also stays", "parts": []}])
    rows = _web_mechanic_rows(_web_details(web, wo_id))
    for bad in ("", str(ObjectId())):
        j = web.post(f"/work_orders/api/mechanic/work_orders/{wo_id}", {
            "merge_protocol": 1, "view_id": bad, "labors": [rows[0]], "unit_mileage": "50000"}).get_json()
        assert _descs(mongo, wo_id) == ["Stays", "Also stays"]
        assert j["merged_foreign"] is True
        assert any("Couldn't confirm what you started from" in n for n in j["merge_notes"])


# ───────────────────────── ответы, вьюхи, ревизии ─────────────────────────


def test_save_responses_carry_merge_keys(app, mongo, cm):
    import json as _json

    pads = cm["parts"]["pads"]
    web = Device(app, MECH_A, mobile=False)
    wo_id = _create_mech_wo(web, cm, [{"description": "Resp job",
                                       "parts": [{"part_id": str(pads["_id"]), "qty": 1}]}])
    d = _web_details(web, wo_id, mp=True)
    rows = _web_mechanic_rows(d)
    rows.append({"labor_id": "", "client_key": "ck-resp", "description": "Resp new",
                 "issue_description": "", "preset_id": "", "parts": []})
    j = web.post(f"/work_orders/api/mechanic/work_orders/{wo_id}", {
        "merge_protocol": 1, "view_id": d["view_id"], "labors": rows, "unit_mileage": "50000"}).get_json()

    for key in ("view_id", "merged_view_id", "labors_rev", "labor_ids", "client_keys",
                "merged_foreign", "merge_notes", "warnings", "labors"):
        assert key in j, key
    wo = _wo(mongo, wo_id)
    assert j["labors_rev"] == wo["labors_rev"]
    assert j["labor_ids"] == [b["labor_id"] for b in wo["labors"]]
    assert j["client_keys"] == {"ck-resp": wo["labors"][1]["labor_id"]}
    assert wo["labors"][1]["client_key"] == "ck-resp"
    assert wo["labors"][1]["minted"]["by"] == str(cm["users"][MECH_A]["_id"])
    assert j["merged_foreign"] is False and j["merge_notes"] == [] and j["warnings"] == []
    assert j["view_id"] and j["merged_view_id"] == j["view_id"]
    # labors — ровно форма деталей механика (с бакетами времени), без денег.
    assert [l["labor_id"] for l in j["labors"]] == j["labor_ids"]
    assert set(j["labors"][0]) == {"labor_id", "description", "issue_description", "parts", "time"}
    text = _json.dumps(j["labors"])
    assert "price" not in text and "cost" not in text
    sent = mongo[SHOP_A_DB].wo_edit_views.find_one({"_id": ObjectId(j["view_id"])})
    assert (sent["kind"], sent["protocol"], sent["channel"]) == ("sent", 1, "mechanic_web")
    assert sent["labors_rev"] == j["labors_rev"]
    assert sent["minted"] == [wo["labors"][1]["labor_id"]]

    # Приложение механика: тотал механику не отдаём.
    a = Device(app, MECH_A)
    rows = _app_mechanic_rows(_mobile_details(a, wo_id))
    ja = _app_save(a, wo_id, rows)
    assert ja["grand_total"] is None and "labors" in ja
    # Мобильный менеджер: тотал есть, безденежной формы строк нет.
    owner = Device(app, OWNER_EMAIL, OWNER_PASSWORD)
    form = _app_send(_app_manager_rows(_mobile_details(owner, wo_id)))
    jm = owner.post(f"/api/mobile/work_orders/{wo_id}", {"status": "in_progress", "labors": form}).get_json()
    assert jm["grand_total"] == _wo(mongo, wo_id)["totals"]["grand_total"] and "labors" not in jm
    assert jm["labors_rev"] == _wo(mongo, wo_id)["labors_rev"]


def test_details_get_records_load_views_per_device_channel_and_protocol(app, mongo, cm, seed):
    a = Device(app, MECH_A)
    wo_id = _create_mech_wo(a, cm, [{"description": "View job", "parts": []}])
    d0 = _mobile_details(a, wo_id)
    d1 = _mobile_details(a, wo_id, mp=True)
    v0 = mongo[SHOP_A_DB].wo_edit_views.find_one({"_id": ObjectId(d0["view_id"])})
    v1 = mongo[SHOP_A_DB].wo_edit_views.find_one({"_id": ObjectId(d1["view_id"])})
    assert (v0["channel"], v0["kind"], v0["protocol"]) == ("mobile", "load", 0)
    assert v1["protocol"] == 1
    assert v0["sid"] and v0["sid"] == v1["sid"]
    assert v0["user_id"] == str(cm["users"][MECH_A]["_id"])
    assert d0["labors_rev"] == _wo(mongo, wo_id)["labors_rev"] == v0["labors_rev"]

    web = Device(app, MECH_A, mobile=False)
    dw = _web_details(web, wo_id)
    vw = mongo[SHOP_A_DB].wo_edit_views.find_one({"_id": ObjectId(dw["view_id"])})
    assert vw["channel"] == "mechanic_web" and vw["sid"] != v0["sid"]

    # Тот, кто не может сохранить WO, вьюх не плодит.
    tdb = mongo[TENANT_A_DB]
    tdb.roles.update_one({"key": "cm_readonly"}, {"$set": {
        "key": "cm_readonly", "name": "CM read only",
        "permissions": ["work_orders.view", "work_orders.view_costs"]}}, upsert=True)
    reader = {"_id": ObjectId(), "email": "cm.reader@test.local", "password_hash": generate_password_hash(PASSWORD),
              "is_active": True, "tenant_id": seed["tenant_a"]["_id"], "shop_ids": [str(cm["shop"]["_id"])],
              "role": "cm_readonly", "created_at": _now()}
    mongo["roobico_test_master"].users.insert_one(reader)
    try:
        r = Device(app, reader["email"])
        assert _mobile_details(r, wo_id)["view_id"] == ""
        assert not _views(mongo, wo_id, user_id=str(reader["_id"]))
    finally:
        mongo["roobico_test_master"].users.delete_one({"_id": reader["_id"]})
        tdb.roles.delete_one({"key": "cm_readonly"})

    # Оплаченный WO — вьюх нет.
    mongo[SHOP_A_DB].work_orders.update_one({"_id": ObjectId(wo_id)}, {"$set": {"status": "paid"}})
    assert _mobile_details(a, wo_id)["view_id"] == ""


def test_rev_endpoints_hide_paid_from_mechanics_and_404(app, mongo, cm, seed):
    web = Device(app, MECH_A, mobile=False)
    app_dev = Device(app, MECH_A)
    owner = Device(app, OWNER_EMAIL, OWNER_PASSWORD)
    wo_id = _create_mech_wo(web, cm, [{"description": "Rev job", "parts": []}])
    web_url = f"/work_orders/api/mechanic/work_orders/{wo_id}/rev"
    app_url = f"/api/mobile/work_orders/{wo_id}/rev"
    rev = _wo(mongo, wo_id)["labors_rev"]

    for dev, url in ((web, web_url), (app_dev, app_url)):
        assert dev.get(url).get_json() == {"ok": True, "labors_rev": rev, "locked": False, "mechanic_done": False}
    for url in (web_url, app_url):
        assert owner.get(url).get_json() == {
            "ok": True, "labors_rev": rev, "status": "in_progress", "manager_confirmed": False}

    # Сохранение двигает ревизию — опрос это видит.
    _app_save(app_dev, wo_id, _app_mechanic_rows(_mobile_details(app_dev, wo_id)), mechanic_state="done")
    j = app_dev.get(app_url).get_json()
    assert j["labors_rev"] == rev + 1 and j["mechanic_done"] is True

    # Оплаченный WO: механику — locked, слова paid в ответе нет.
    mongo[SHOP_A_DB].work_orders.update_one({"_id": ObjectId(wo_id)}, {"$set": {"status": "paid"}})
    for dev, url in ((web, web_url), (app_dev, app_url)):
        resp = dev.get(url)
        assert resp.get_json()["locked"] is True
        assert "paid" not in resp.get_data(as_text=True)
    assert owner.get(app_url).get_json()["status"] == "paid"

    # Нет такого WO / чужой магазин / удалён — 404 work_order_not_found.
    foreign = ObjectId()
    mongo[SHOP_A_DB].work_orders.insert_one({"_id": foreign, "shop_id": ObjectId(), "status": "open",
                                             "is_active": True, "labors_rev": 7})
    for missing in (str(ObjectId()), str(foreign)):
        for dev, base in ((web, "/work_orders/api/mechanic/work_orders"), (app_dev, "/api/mobile/work_orders")):
            resp = dev.get(f"{base}/{missing}/rev")
            assert resp.status_code == 404 and resp.get_json()["error"] == "work_order_not_found"
    mongo[SHOP_A_DB].work_orders.delete_one({"_id": foreign})
    mongo[SHOP_A_DB].work_orders.update_one({"_id": ObjectId(wo_id)}, {"$set": {"is_active": False}})
    assert app_dev.get(app_url).status_code == 404

    # Права: без work_orders.view — 403; без сессии приложения — 401.
    tdb = mongo[TENANT_A_DB]
    tdb.roles.update_one({"key": "cm_calendar_only"}, {"$set": {
        "key": "cm_calendar_only", "name": "CM calendar", "permissions": ["calendar.view"]}}, upsert=True)
    outsider = {"_id": ObjectId(), "email": "cm.outsider@test.local",
                "password_hash": generate_password_hash(PASSWORD), "is_active": True,
                "tenant_id": seed["tenant_a"]["_id"], "shop_ids": [str(cm["shop"]["_id"])],
                "role": "cm_calendar_only", "created_at": _now()}
    mongo["roobico_test_master"].users.insert_one(outsider)
    try:
        o = Device(app, outsider["email"])
        assert o.get(app_url).status_code == 403
        assert o.get(web_url).status_code == 403
    finally:
        mongo["roobico_test_master"].users.delete_one({"_id": outsider["_id"]})
        tdb.roles.delete_one({"key": "cm_calendar_only"})
    assert app.test_client().get(app_url).status_code == 401


def test_timer_and_time_log_responses_report_the_final_labors_rev(app, mongo, cm):
    a = Device(app, MECH_A, mobile=False)
    owner = Device(app, OWNER_EMAIL, OWNER_PASSWORD, mobile=False)
    wo_id = _create_mech_wo(a, cm, [{"description": "Rev timer job", "parts": []}])
    lid = _wo(mongo, wo_id)["labors"][0]["labor_id"]
    rows = _web_mechanic_rows(_web_details(a, wo_id))
    assert a.post(f"/work_orders/api/mechanic/work_orders/{wo_id}", {
        "labors": rows, "unit_mileage": "50000", "mechanic_state": "done"}).status_code == 200
    rev_done = _wo(mongo, wo_id)["labors_rev"]

    j = a.post("/work_orders/api/mechanic/timers/start", {"work_order_id": wo_id, "labor_id": lid}).get_json()
    # Старт снял отметку Done (часть редактируемого состояния) — ревизия сдвинулась.
    assert j["labors_rev"] == _wo(mongo, wo_id)["labors_rev"] == rev_done + 1
    _backdate_timer(mongo, cm["users"][MECH_A], 600)
    j = a.post("/work_orders/api/mechanic/timers/stop", {}).get_json()
    assert j["labors_rev"] == _wo(mongo, wo_id)["labors_rev"] > rev_done + 1

    log = mongo[SHOP_A_DB].wo_time_logs.find_one({"labor_id": lid})
    j = owner.post(f"/work_orders/api/time_logs/{log['_id']}/update", {"seconds": 1800}).get_json()
    assert j["ok"] is True and j["labors_rev"] == _wo(mongo, wo_id)["labors_rev"]
    j = owner.post(f"/work_orders/api/time_logs/{log['_id']}/delete", {}).get_json()
    assert j["ok"] is True and j["labors_rev"] == _wo(mongo, wo_id)["labors_rev"]


def test_assignment_sync_bumps_rev_only_on_a_real_change(app, mongo, cm):
    from app.blueprints.work_orders.services import time_tracking

    shop_db = mongo[SHOP_A_DB]
    a = Device(app, MECH_A)
    wo_id = _create_mech_wo(a, cm, [{"description": "Sync job", "parts": []}])
    lid = _wo(mongo, wo_id)["labors"][0]["labor_id"]
    now = _now()
    shop_db.wo_time_logs.insert_one({
        "shop_id": cm["shop"]["_id"], "work_order_id": ObjectId(wo_id), "labor_id": lid,
        "user_id": cm["users"][MECH_A]["_id"], "user_name": "Andy Concurrent",
        "started_at": now, "stopped_at": now, "seconds": 1200, "stop_source": "user",
        "created_at": now, "updated_at": now,
    })
    rev0 = _wo(mongo, wo_id)["labors_rev"]
    assert time_tracking.sync_labor_assignments_from_time(shop_db, cm["shop"], wo_id) is True
    rev1 = _wo(mongo, wo_id)["labors_rev"]
    assert rev1 == rev0 + 1
    assert time_tracking.sync_labor_assignments_from_time(shop_db, cm["shop"], wo_id) is False
    assert _wo(mongo, wo_id)["labors_rev"] == rev1


def test_new_work_orders_start_at_revision_one(app, mongo, cm):
    a = Device(app, MECH_A)
    web_id = _create_mech_wo(a, cm, [{"description": "Rev one", "parts": []}])
    assert _wo(mongo, web_id)["labors_rev"] == 1
    resp = a.post("/api/mobile/work_orders", {
        "customer_id": str(cm["customer"]["_id"]), "unit_id": str(cm["unit"]["_id"]),
        "status": "in_progress", "labors": [{"description": "Mobile rev one", "client_key": "ck-create",
                                             "parts": []}],
        "mechanic_state": "in_progress", "allow_duplicate_unit_wo": True, "unit_mileage": "50000",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    j = resp.get_json()
    wo = _wo(mongo, j["id"])
    assert wo["labors_rev"] == 1 and j["labors_rev"] == 1
    assert j["client_keys"] == {"ck-create": wo["labors"][0]["labor_id"]}


# ───────────────────────── скрипты (CAS / $inc) ─────────────────────────


@pytest.fixture()
def scratch_db(mongo):
    db = mongo["roobico_test_c2m_scripts"]
    mongo.drop_database(db.name)
    yield db
    mongo.drop_database(db.name)


def test_backfill_wo_labor_ids_is_cas_and_never_reverts_a_racing_save(scratch_db, monkeypatch):
    from app.scripts import backfill_wo_labor_ids as script

    with_rev, no_rev = ObjectId(), ObjectId()
    scratch_db.work_orders.insert_many([
        {"_id": with_rev, "labors_rev": 5, "labors": [
            {"labor": {"description": "No id"}, "parts": []},
            {"labor_id": "KEEP", "labor": {"description": "Has id"}, "parts": []},
        ]},
        {"_id": no_rev, "labors": [{"labor": {"description": "Legacy, no rev"}, "parts": []}]},
    ])
    real = script.ev.rev_filter
    calls = {"n": 0}

    def racing(rev):
        calls["n"] += 1
        if calls["n"] == 1:
            # Сохранение WO успевает между чтением скрипта и его записью.
            scratch_db.work_orders.update_one({"_id": with_rev}, {
                "$push": {"labors": {"labor_id": "SAVED", "labor": {"description": "Saved meanwhile"}, "parts": []}},
                "$inc": {"labors_rev": 1},
            })
        return real(rev)

    monkeypatch.setattr(script.ev, "rev_filter", racing)
    assert script.backfill_shop(scratch_db) == (2, 2)

    doc = scratch_db.work_orders.find_one({"_id": with_rev})
    assert [b["labor"]["description"] for b in doc["labors"]] == ["No id", "Has id", "Saved meanwhile"]
    assert doc["labors"][0]["labor_id"] and doc["labors"][1]["labor_id"] == "KEEP"
    assert doc["labors_rev"] == 7                      # 5 → сохранение 6 → бэкфилл 7
    legacy = scratch_db.work_orders.find_one({"_id": no_rev})
    assert legacy["labors"][0]["labor_id"] and legacy["labors_rev"] == 1


def test_backfill_labor_hourly_rate_writes_with_cas(scratch_db):
    from app.scripts.backfill_labor_hourly_rate import _process_shop_db

    scratch_db.labor_rates.insert_one({"code": "standard", "hourly_rate": 100.0})
    wo_id = ObjectId()
    scratch_db.work_orders.insert_one({
        "_id": wo_id, "labors_rev": 3,
        "labors": [{"labor_id": "L1", "labor": {"description": "Old job", "rate_code": "standard", "hours": ""},
                    "parts": []}],
        "totals": {"labors": [{"labor": 150.0}]},
    })
    dry = _process_shop_db(scratch_db, apply=False)
    assert dry["wo_updated"] == 1 and scratch_db.work_orders.find_one({"_id": wo_id})["labors_rev"] == 3
    stats = _process_shop_db(scratch_db, apply=True)
    assert stats["wo_updated"] == 1 and stats["wo_skipped"] == 0
    doc = scratch_db.work_orders.find_one({"_id": wo_id})
    assert doc["labors"][0]["labor"]["hourly_rate"] == 100.0 and doc["labors"][0]["labor"]["hours"] == 1.5
    assert doc["labors_rev"] == 4
    assert _process_shop_db(scratch_db, apply=True)["wo_updated"] == 0     # идемпотентен


def test_merge_duplicate_parts_bumps_labors_rev_of_touched_work_orders(scratch_db):
    from app.scripts.merge_duplicate_parts import merge_dupe_into

    shop_id = ObjectId()
    survivor = {"_id": ObjectId(), "shop_id": shop_id, "part_number": "CM-1", "in_stock": 1,
                "is_active": True, "created_at": 1}
    dupe = {"_id": ObjectId(), "shop_id": shop_id, "part_number": "cm-1", "in_stock": 2,
            "is_active": True, "created_at": 2}
    scratch_db.parts.insert_many([survivor, dupe])
    touched, untouched = ObjectId(), ObjectId()
    scratch_db.work_orders.insert_many([
        {"_id": touched, "shop_id": shop_id, "labors_rev": 3,
         "labors": [{"labor_id": "A", "labor": {}, "parts": [{"part_id": dupe["_id"], "qty": 1}]}]},
        {"_id": untouched, "shop_id": shop_id, "labors_rev": 3,
         "labors": [{"labor_id": "B", "labor": {}, "parts": [{"part_id": survivor["_id"], "qty": 1}]}]},
    ])
    merge_dupe_into(scratch_db, shop_id, survivor, dupe)
    doc = scratch_db.work_orders.find_one({"_id": touched})
    assert doc["labors"][0]["parts"][0]["part_id"] == survivor["_id"]
    assert doc["labors_rev"] == 4
    assert scratch_db.work_orders.find_one({"_id": untouched})["labors_rev"] == 3
