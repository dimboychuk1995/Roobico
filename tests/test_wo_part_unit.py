"""
Единица измерения строки запчасти в work order (`unit` — ключ каталога
each/gallon/quart…): сохраняется с веб-формы создания и через JSON-апдейт
(алиасы любого регистра нормализуются, неизвестная → each, сохранение не
падает), отдаётся странице WO (initial_labors), поиску запчастей и
механик-режиму, печатается у количества в PDF и на клиентском портале
(«2 gal»); старые WO без поля печатаются как раньше (просто «2»).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from tests.conftest import SHOP_A_DB, get_csrf_token, login


@pytest.fixture()
def logged_in(client):
    assert login(client).status_code == 302
    return client


@pytest.fixture()
def env(app, seed):
    from app.extensions import get_mongo_client

    with app.app_context():
        db = get_mongo_client()[SHOP_A_DB]
        shop_id = seed["shop_a"]["_id"]
        now = datetime.now(timezone.utc)

        customer_id = ObjectId()
        db.customers.insert_one({
            "_id": customer_id, "shop_id": shop_id, "company_name": "Gallon Fleet",
            "contacts": [], "is_active": True, "created_at": now,
        })
        unit_id = ObjectId()
        db.units.insert_one({
            "_id": unit_id, "shop_id": shop_id, "customer_id": customer_id,
            "unit_number": "U-GAL", "make": "Volvo", "model": "VNL", "year": "2020",
            "vin": "VINUNITGAL0001", "is_active": True, "created_at": now,
        })
        # Каталожный парт в галлонах — то, что выберут из поиска на странице WO
        part = {
            "_id": ObjectId(), "shop_id": shop_id,
            "part_number": "OIL-15W40-GAL", "description": "Engine oil 15W-40",
            "unit_of_measure": "gallon", "in_stock": 50, "average_cost": 12.0,
            "is_active": True, "created_at": now,
        }
        db.parts.insert_one(part)

        yield {"db": db, "shop_id": shop_id, "customer_id": customer_id,
               "unit_id": unit_id, "part": part}

        db.work_orders.delete_many({"customer_id": customer_id})
        db.parts.delete_one({"_id": part["_id"]})
        db.units.delete_one({"_id": unit_id})
        db.customers.delete_one({"_id": customer_id})
        db.cores.delete_many({"part_id": part["_id"]})
        db.inventory_movements.delete_many({"part_id": part["_id"]})
        db.part_location_stock.delete_many({"part_id": part["_id"]})


def _create_wo(client, env, unit_value="gallon", qty="2"):
    """Создание WO веб-формой (как serializeBlocks/submit страницы деталей):
    скрытое поле labors[i][parts][j][unit] несёт ключ единицы измерения."""
    token = get_csrf_token(client)
    data = {
        "csrf_token": token,
        "customer_id": str(env["customer_id"]),
        "unit_id": str(env["unit_id"]),
        "create_status": "open",
        "labors[0][labor_description]": "Oil change",
        "labors[0][labor_full_total]": "100",
        "labors[0][parts][0][part_number]": env["part"]["part_number"],
        "labors[0][parts][0][part_id]": str(env["part"]["_id"]),
        "labors[0][parts][0][description]": env["part"]["description"],
        "labors[0][parts][0][qty]": qty,
        "labors[0][parts][0][price]": "20",
    }
    if unit_value is not None:
        data["labors[0][parts][0][unit]"] = unit_value
    resp = client.post("/work_orders/create", data=data)
    assert resp.status_code in (200, 302), resp.get_data(as_text=True)[:500]
    wo = env["db"].work_orders.find_one({"customer_id": env["customer_id"], "is_active": True})
    assert wo is not None
    return wo


def _part_line(env, qty=2, **extra):
    line = {
        "part_id": str(env["part"]["_id"]), "one_time_part": False,
        "part_number": env["part"]["part_number"], "description": env["part"]["description"],
        "qty": qty, "cost": 12, "price": 20, "core_charge": 0, "misc_charge": 0,
        "misc_charge_description": "",
    }
    line.update(extra)
    return line


def _update(client, wo_id, parts):
    """Как браузер: открыть страницу WO (view_id + id строки) и сохранить форму."""
    html = client.get(f"/work_orders/details?work_order_id={wo_id}").get_data(as_text=True)
    created = json.loads(re.search(r'id="workOrderCreatedData">(.*?)</script>', html, re.S).group(1))
    initial = json.loads(re.search(r'id="workOrderInitialData">(.*?)</script>', html, re.S).group(1))
    token = get_csrf_token(client)
    resp = client.post(
        f"/work_orders/api/work_orders/{wo_id}/update",
        json={
            "labors": [{
                "labor_id": initial[0]["labor_id"],
                "labor_description": "Oil change", "labor_hours": 0, "labor_rate_code": "",
                "labor_full_total": 100, "assigned_mechanics": [], "issue_description": "",
                "parts": parts,
            }],
            "totals": {},
            "save_status": "open",
            "merge_protocol": 1,
            "view_id": created["view_id"],
        },
        headers={"X-CSRFToken": token},
    )
    data = resp.get_json()
    assert data and data.get("ok") is True, data
    return data


def _saved_part(env, wo_id):
    saved = env["db"].work_orders.find_one({"_id": wo_id})
    assert len(saved["labors"]) == 1 and len(saved["labors"][0]["parts"]) == 1   # без дублей строк
    return saved["labors"][0]["parts"][0]


def _pdf_html(client, wo_id, monkeypatch):
    """GET download-pdf с перехватом HTML, из которого рендерится PDF."""
    import app.blueprints.work_orders.routes as wo_routes

    captured = {}

    def fake_render(html):
        captured["html"] = html
        return b"%PDF-fake"

    monkeypatch.setattr(wo_routes, "render_html_to_pdf", fake_render)
    resp = client.get(f"/work_orders/api/work_orders/{wo_id}/download-pdf")
    assert resp.status_code == 200
    return captured["html"]


# ---------------- сохранение ----------------

def test_create_form_persists_unit(logged_in, env):
    wo = _create_wo(logged_in, env, unit_value="gallon")
    part = wo["labors"][0]["parts"][0]
    assert part["unit"] == "gallon"
    assert part["qty"] == 2
    assert part["part_id"] == env["part"]["_id"]


def test_create_form_unknown_unit_falls_back_to_each(logged_in, env):
    """Неизвестная единица не роняет сохранение — строка становится each."""
    wo = _create_wo(logged_in, env, unit_value="furlong")
    assert wo["labors"][0]["parts"][0]["unit"] == "each"


def test_create_form_without_unit_is_each(logged_in, env):
    """Старый клиент без поля unit — each (ручные/one-time строки тоже)."""
    wo = _create_wo(logged_in, env, unit_value=None)
    assert wo["labors"][0]["parts"][0]["unit"] == "each"


def test_update_json_normalizes_unit(logged_in, env):
    wo = _create_wo(logged_in, env)

    # Алиас любого регистра/числа → ключ каталога
    _update(logged_in, wo["_id"], [_part_line(env, unit="Gallons")])
    assert _saved_part(env, wo["_id"])["unit"] == "gallon"

    _update(logged_in, wo["_id"], [_part_line(env, unit="qt")])
    assert _saved_part(env, wo["_id"])["unit"] == "quart"

    # Неизвестная единица — each, сохранение проходит
    _update(logged_in, wo["_id"], [_part_line(env, unit="bogus-unit")])
    assert _saved_part(env, wo["_id"])["unit"] == "each"

    # Поле не прислали — each
    _update(logged_in, wo["_id"], [_part_line(env)])
    assert _saved_part(env, wo["_id"])["unit"] == "each"


# ---------------- отдача на страницу WO и в поиск ----------------

def test_search_returns_unit_of_measure(logged_in, env):
    """Поиск запчастей на странице WO отдаёт unit (ключ) + unit_short —
    по ним строка получает скрытое поле и подпись у количества."""
    resp = logged_in.get("/work_orders/api/parts/search?q=OIL-15W40")
    assert resp.status_code == 200
    items = resp.get_json()["items"]
    item = next(i for i in items if i["id"] == str(env["part"]["_id"]))
    assert item["unit"] == "gallon"
    assert item["unit_short"] == "gal"


def test_details_page_exposes_unit_for_saved_rows(logged_in, env):
    wo = _create_wo(logged_in, env)
    resp = logged_in.get(f"/work_orders/details?work_order_id={wo['_id']}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    match = re.search(r'id="workOrderInitialData">(.*?)</script>', html, re.S)
    assert match, "initial labors JSON not found"
    initial = json.loads(match.group(1))
    part = initial[0]["parts"][0]
    assert part["unit"] == "gallon"
    assert part["unit_short"] == "gal"

    # Статическая первая строка таблицы: скрытое поле unit и подпись у количества
    assert 'name="labors[0][parts][0][unit]"' in html
    assert "part-unit-label" in html


def test_mechanic_payload_keeps_unit_without_money(logged_in, env):
    """Механик-режим вычищает деньги, но единица измерения — не деньги."""
    wo = _create_wo(logged_in, env)
    resp = logged_in.get(f"/work_orders/api/mechanic/work_orders/{wo['_id']}")
    assert resp.status_code == 200
    part = resp.get_json()["labors"][0]["parts"][0]
    assert part["unit"] == "gallon"
    assert part["unit_short"] == "gal"
    assert "price" not in part and "cost" not in part


def test_mechanic_new_row_takes_unit_from_catalog(app, env):
    """Новая строка механика (мобилка/веб механика): единица — из каталога;
    парт не из каталога — ручная строка each."""
    from app.blueprints.work_orders.services.mechanic_editor import _autofill_part

    with app.app_context():
        row = _autofill_part(env["db"], env["shop_id"], env["customer_id"],
                             {"part_id": str(env["part"]["_id"]), "qty": 1})
        manual = _autofill_part(env["db"], env["shop_id"], env["customer_id"],
                                {"part_number": "NOPE-NOT-IN-CATALOG", "qty": 1})
    assert row["unit"] == "gallon"
    assert manual["unit"] == "each"
    assert manual["one_time_part"] is True


# ---------------- печать: PDF, портал, контекст ----------------

def test_pdf_prints_qty_with_unit(logged_in, env, monkeypatch):
    wo = _create_wo(logged_in, env)
    html = _pdf_html(logged_in, wo["_id"], monkeypatch)
    assert '<td class="tr">2 gal</td>' in html


def test_pdf_each_prints_plain_qty(logged_in, env, monkeypatch):
    """each печатается как раньше — просто число, без «ea»."""
    wo = _create_wo(logged_in, env, unit_value="each")
    html = _pdf_html(logged_in, wo["_id"], monkeypatch)
    assert '<td class="tr">2</td>' in html
    assert "2 ea" not in html


def test_portal_prints_qty_with_unit(logged_in, env, app, seed):
    from app.blueprints.customer_portal.routes import get_or_create_portal_token

    wo = _create_wo(logged_in, env)
    with app.app_context():
        token = get_or_create_portal_token(seed["shop_a"], env["customer_id"])["token"]
    resp = logged_in.get(f"/portal/{token}/work-orders/{wo['_id']}")
    assert resp.status_code == 200
    assert '<td class="text-end">2 gal</td>' in resp.get_data(as_text=True)


def test_print_context_qty_label_and_legacy_lines(app, env, seed):
    """Контекст печати (PDF/письма/авторизация/портал): qty_label «3 qt» для
    не-штучной единицы; старая строка без поля unit — each и просто «2»."""
    from app.blueprints.work_orders.services.pdf_contexts import _build_wo_pdf_context
    from app.blueprints.work_orders.services.totals import normalize_saved_labors

    now = datetime.now(timezone.utc)
    wo = {
        "_id": ObjectId(), "shop_id": env["shop_id"], "customer_id": env["customer_id"],
        "unit_id": env["unit_id"], "wo_number": 77001, "status": "open", "is_active": True,
        "work_order_date": now, "created_at": now,
        "labors": [{
            "labor_id": "L1", "labor": {"description": "Coolant top-up"},
            "parts": [
                {"part_number": "COOLANT-QT", "description": "Coolant", "qty": 3, "price": 5, "unit": "quart"},
                {"part_number": "LEGACY-1", "description": "Legacy part", "qty": 2, "price": 5},
            ],
        }],
    }
    env["db"].work_orders.insert_one(wo)

    with app.app_context():
        ctx = _build_wo_pdf_context(env["db"], seed["shop_a"], wo)
        rows = normalize_saved_labors(wo["labors"], shop_db=env["db"])

    quart, legacy = ctx["labors"][0]["parts"]
    assert quart["unit"] == "quart" and quart["unit_short"] == "qt" and quart["qty_label"] == "3 qt"
    assert legacy["unit"] == "each" and legacy["qty_label"] == "2"

    assert rows[0]["parts"][0]["unit"] == "quart" and rows[0]["parts"][0]["unit_short"] == "qt"
    assert rows[0]["parts"][1]["unit"] == "each" and rows[0]["parts"][1]["unit_short"] == "ea"
