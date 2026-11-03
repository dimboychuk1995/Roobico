"""
Минимальный остаток (reorder point) и единица измерения запчасти:
состояние below / near / None, валидация на создании и обновлении (форма,
JSON), фильтр «Stock level» и сводка на странице Parts, поля в /api/search
и GET /api/<id>, экспорт и импорт колонок Min Stock / Unit.
"""
from __future__ import annotations

import csv
import io
import json
from contextlib import contextmanager

import pytest
from bson import ObjectId
from flask import template_rendered
from pymongo import MongoClient

from app.blueprints.parts.services.min_stock import min_stock_state, near_limit, stock_level_filter
from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, get_csrf_token, login

PREFIX = "MINS-"


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def env(seed, mongo):
    db = mongo[SHOP_A_DB]
    yield {"db": db, "shop_id": seed["shop_a"]["_id"]}
    ids = [p["_id"] for p in db.parts.find({"part_number": {"$regex": "^" + PREFIX}}, {"_id": 1})]
    db.parts.delete_many({"_id": {"$in": ids}})
    db.part_location_stock.delete_many({"part_id": {"$in": ids}})
    db.inventory_movements.delete_many({"part_id": {"$in": ids}})


@contextmanager
def captured_templates(app):
    """Контексты отрендеренных шаблонов — проверяем счётчики сводки."""
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template, context))

    template_rendered.connect(record, app)
    try:
        yield recorded
    finally:
        template_rendered.disconnect(record, app)


def _create(client, payload, expect=200):
    token = get_csrf_token(client)
    resp = client.post("/parts/api/create", json=payload, headers={"X-CSRFToken": token})
    assert resp.status_code == expect, resp.get_data(as_text=True)
    return resp.get_json()


def _update(client, part_id, payload, expect=200):
    token = get_csrf_token(client)
    resp = client.post(f"/parts/api/{part_id}/update", json=payload, headers={"X-CSRFToken": token})
    assert resp.status_code == expect, resp.get_data(as_text=True)
    return resp.get_json()


def _update_base(part_number):
    """Полный payload обновления (эндпоинт требует все поля формы)."""
    return {
        "part_number": part_number, "description": "", "reference": "",
        "vendor_id": "", "category_id": "", "location_id": "",
        "average_cost": 0, "has_selling_price": False, "selling_price": 0,
        "core_has_charge": False, "core_cost": 0, "misc_has_charge": False, "misc_charges": [],
    }


def _seed_parts(client):
    """below (0 из мин. 5, галлоны), near (5 из 5), healthy (20 из 5), без минимума (0)."""
    below = _create(client, {"part_number": PREFIX + "BELOW", "description": "below part", "in_stock": 0,
                             "min_stock_enabled": True, "min_stock": 5, "unit_of_measure": "gal"})["part_id"]
    near = _create(client, {"part_number": PREFIX + "NEAR", "description": "near part", "in_stock": 5,
                            "min_stock_enabled": True, "min_stock": 5})["part_id"]
    healthy = _create(client, {"part_number": PREFIX + "HEALTHY", "description": "healthy part", "in_stock": 20,
                               "min_stock_enabled": True, "min_stock": 5})["part_id"]
    plain = _create(client, {"part_number": PREFIX + "PLAIN", "description": "no minimum", "in_stock": 0})["part_id"]
    return below, near, healthy, plain


# ── Сервис: пороги ─────────────────────────────────────────────────────


@pytest.mark.parametrize("minimum, expected", [(0, 1), (1, 2), (5, 6), (10, 12), (25, 30)])
def test_near_limit(minimum, expected):
    assert near_limit(minimum) == expected


@pytest.mark.parametrize("in_stock, minimum, expected", [
    (0, 0, "near"), (1, 0, "near"), (2, 0, None),
    (0, 1, "below"), (1, 1, "near"), (2, 1, "near"), (3, 1, None),
    (0, 5, "below"), (4, 5, "below"), (5, 5, "near"), (6, 5, "near"), (7, 5, None),
    (9, 10, "below"), (10, 10, "near"), (12, 10, "near"), (13, 10, None),
    (24, 25, "below"), (25, 25, "near"), (30, 25, "near"), (31, 25, None),
])
def test_min_stock_state_boundaries(in_stock, minimum, expected):
    assert min_stock_state({"min_stock_enabled": True, "min_stock": minimum, "in_stock": in_stock}) == expected


def test_min_stock_state_disabled_or_untracked():
    assert min_stock_state(None) is None
    assert min_stock_state({"min_stock": 5, "in_stock": 0}) is None
    assert min_stock_state({"min_stock_enabled": False, "min_stock": 5, "in_stock": 0}) is None
    assert min_stock_state({"min_stock_enabled": True, "min_stock": 5, "in_stock": 0,
                            "do_not_track_inventory": True}) is None
    # Отсутствующий in_stock — ноль.
    assert min_stock_state({"min_stock_enabled": True, "min_stock": 5}) == "below"


def test_stock_level_filter_matches_state(client, env):
    """Mongo-фильтр и min_stock_state выделяют один и тот же набор."""
    login(client)
    _seed_parts(client)
    db = env["db"]
    base = {"shop_id": env["shop_id"], "part_number": {"$regex": "^" + PREFIX}}
    below = {p["part_number"] for p in db.parts.find({"$and": [base, stock_level_filter("below")]})}
    assert below == {PREFIX + "BELOW"}
    near = {p["part_number"] for p in db.parts.find({"$and": [base, stock_level_filter("near")]})}
    assert near == {PREFIX + "BELOW", PREFIX + "NEAR"}
    assert stock_level_filter("bogus") is None
    assert stock_level_filter("") is None


# ── Создание / обновление ──────────────────────────────────────────────


def test_create_with_min_stock_and_unit(client, env):
    login(client)
    data = _create(client, {"part_number": PREFIX + "OIL", "description": "Engine oil", "in_stock": 2,
                            "min_stock_enabled": True, "min_stock": 5, "unit_of_measure": "GAL"})
    assert data["ok"] is True
    doc = env["db"].parts.find_one({"_id": ObjectId(data["part_id"])})
    assert doc["min_stock_enabled"] is True and doc["min_stock"] == 5
    assert doc["unit_of_measure"] == "gallon"
    assert doc["in_stock"] == 2

    item = client.get(f"/parts/api/{data['part_id']}").get_json()["item"]
    assert item["min_stock_enabled"] is True and item["min_stock"] == 5
    assert item["unit_of_measure"] == "gallon" and item["unit"] == "gallon"
    assert item["unit_short"] == "gal" and item["unit_label"] == "Gallon"
    assert item["stock_state"] == "below"


def test_create_enabled_without_number_is_rejected(client, env):
    login(client)
    number = PREFIX + "NOMIN"
    data = _create(client, {"part_number": number, "min_stock_enabled": True}, expect=400)
    assert data["ok"] is False
    assert data["error"] == "Minimum stock is required when the part requires minimum stock"
    data = _create(client, {"part_number": number, "min_stock_enabled": True, "min_stock": ""}, expect=400)
    assert "required" in data["error"]
    for bad in ("abc", -1, 2.5, "7.5"):
        data = _create(client, {"part_number": number, "min_stock_enabled": True, "min_stock": bad}, expect=400)
        assert data["error"] == "Minimum stock must be a whole number (0 or more)", bad
    assert env["db"].parts.count_documents({"part_number": number}) == 0

    # Ноль — допустимый минимум; строка "5" и "5.0" — целые.
    data = _create(client, {"part_number": number, "min_stock_enabled": True, "min_stock": "0"})
    doc = env["db"].parts.find_one({"_id": ObjectId(data["part_id"])})
    assert doc["min_stock_enabled"] is True and doc["min_stock"] == 0
    data = _create(client, {"part_number": number + "-B", "min_stock_enabled": "1", "min_stock": "5.0"})
    doc = env["db"].parts.find_one({"_id": ObjectId(data["part_id"])})
    assert doc["min_stock"] == 5


def test_create_unit_normalized_unknown_rejected(client, env):
    login(client)
    data = _create(client, {"part_number": PREFIX + "ZZZ", "unit_of_measure": "zzz"}, expect=400)
    assert data["error"] == "Unknown unit of measure"
    assert env["db"].parts.count_documents({"part_number": PREFIX + "ZZZ"}) == 0

    data = _create(client, {"part_number": PREFIX + "EACH"})
    doc = env["db"].parts.find_one({"_id": ObjectId(data["part_id"])})
    assert doc["unit_of_measure"] == "each"
    assert doc["min_stock_enabled"] is False and doc["min_stock"] == 0

    data = _create(client, {"part_number": PREFIX + "QT", "unit_of_measure": "Quarts"})
    doc = env["db"].parts.find_one({"_id": ObjectId(data["part_id"])})
    assert doc["unit_of_measure"] == "quart"


def test_create_do_not_track_forces_min_stock_off(client, env):
    login(client)
    data = _create(client, {"part_number": PREFIX + "UNTRACKED", "do_not_track_inventory": True,
                            "min_stock_enabled": True, "min_stock": 3, "unit_of_measure": "box"})
    doc = env["db"].parts.find_one({"_id": ObjectId(data["part_id"])})
    assert doc["do_not_track_inventory"] is True
    assert doc["min_stock_enabled"] is False and doc["min_stock"] == 0
    assert doc["unit_of_measure"] == "box"
    item = client.get(f"/parts/api/{data['part_id']}").get_json()["item"]
    assert item["stock_state"] is None


def test_form_create_persists_min_stock_and_unit(client, env):
    """Классическая форма (POST /parts/create): чекбокс «1», число, единица."""
    login(client)
    token = get_csrf_token(client)
    resp = client.post("/parts/create", data={
        "csrf_token": token, "part_number": PREFIX + "FORM", "in_stock": "1",
        "min_stock_enabled": "1", "min_stock": "3", "unit_of_measure": "qt",
    })
    assert resp.status_code == 302
    doc = env["db"].parts.find_one({"part_number": PREFIX + "FORM"})
    assert doc["min_stock_enabled"] is True and doc["min_stock"] == 3
    assert doc["unit_of_measure"] == "quart"
    assert doc["in_stock"] == 1

    # Включено без числа — парт не создаётся, пользователь видит причину.
    resp = client.post("/parts/create", data={
        "csrf_token": token, "part_number": PREFIX + "FORM2", "min_stock_enabled": "1",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert env["db"].parts.find_one({"part_number": PREFIX + "FORM2"}) is None
    assert "Minimum stock is required" in resp.get_data(as_text=True)

    # Чекбокс не отмечен (поля нет в форме) — минимум выключен, единица each.
    resp = client.post("/parts/create", data={"csrf_token": token, "part_number": PREFIX + "FORM3"})
    assert resp.status_code == 302
    doc = env["db"].parts.find_one({"part_number": PREFIX + "FORM3"})
    assert doc["min_stock_enabled"] is False and doc["min_stock"] == 0 and doc["unit_of_measure"] == "each"


def test_update_validates_and_tracking_off_clears_flag(client, env, app):
    login(client)
    number = PREFIX + "UPD"
    pid = _create(client, {"part_number": number, "in_stock": 1,
                           "min_stock_enabled": True, "min_stock": 4, "unit_of_measure": "quart"})["part_id"]
    base = _update_base(number)

    # Поля не присланы (старый клиент) — настройка не трогается.
    _update(client, pid, {**base, "in_stock": 1, "do_not_track_inventory": False})
    doc = env["db"].parts.find_one({"_id": ObjectId(pid)})
    assert doc["min_stock_enabled"] is True and doc["min_stock"] == 4 and doc["unit_of_measure"] == "quart"

    # Включено без числа — 400, ничего не меняется.
    res = _update(client, pid, {**base, "in_stock": 1, "do_not_track_inventory": False,
                                "min_stock_enabled": True, "min_stock": ""}, expect=400)
    assert res["error"] == "Minimum stock is required when the part requires minimum stock"
    res = _update(client, pid, {**base, "in_stock": 1, "do_not_track_inventory": False,
                                "min_stock_enabled": True, "min_stock": "x"}, expect=400)
    assert res["error"] == "Minimum stock must be a whole number (0 or more)"
    doc = env["db"].parts.find_one({"_id": ObjectId(pid)})
    assert doc["min_stock"] == 4

    # Новый минимум и единица (название во множественном числе).
    _update(client, pid, {**base, "in_stock": 1, "do_not_track_inventory": False,
                          "min_stock_enabled": True, "min_stock": 7, "unit_of_measure": "Gallons"})
    doc = env["db"].parts.find_one({"_id": ObjectId(pid)})
    assert doc["min_stock"] == 7 and doc["unit_of_measure"] == "gallon"

    # Журнал изменений упоминает новые поля.
    from app.extensions import get_master_db
    with app.app_context():
        entry = get_master_db().audit_journal.find_one(
            {"entity_type": "part", "entity_id": pid, "action": "update"}, sort=[("created_at", -1)]
        )
    assert entry is not None
    changed = {c.get("field"): c for c in entry.get("changes") or []}
    assert changed["min_stock"]["old"] == 4 and changed["min_stock"]["new"] == 7
    assert changed["unit_of_measure"]["new"] == "gallon"

    # Выключить минимум явно — False / 0.
    _update(client, pid, {**base, "in_stock": 1, "do_not_track_inventory": False,
                          "min_stock_enabled": False, "min_stock": 7})
    doc = env["db"].parts.find_one({"_id": ObjectId(pid)})
    assert doc["min_stock_enabled"] is False and doc["min_stock"] == 0

    # Снова включить, затем выключить учёт склада — минимум сбрасывается,
    # даже если клиент прислал его включённым.
    _update(client, pid, {**base, "in_stock": 1, "do_not_track_inventory": False,
                          "min_stock_enabled": True, "min_stock": 7})
    _update(client, pid, {**base, "in_stock": 0, "do_not_track_inventory": True,
                          "min_stock_enabled": True, "min_stock": 7})
    doc = env["db"].parts.find_one({"_id": ObjectId(pid)})
    assert doc["do_not_track_inventory"] is True
    assert doc["min_stock_enabled"] is False and doc["min_stock"] == 0

    # Неизвестная единица — 400.
    res = _update(client, pid, {**base, "in_stock": 0, "do_not_track_inventory": True,
                                "unit_of_measure": "bogus"}, expect=400)
    assert res["error"] == "Unknown unit of measure"


# ── Страница Parts ─────────────────────────────────────────────────────


def test_parts_page_highlights_and_summary(client, env, app):
    login(client)
    _seed_parts(client)
    with captured_templates(app) as templates:
        resp = client.get("/parts/?tab=parts&q=" + PREFIX)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    for number in ("BELOW", "NEAR", "HEALTHY", "PLAIN"):
        assert PREFIX + number in html
    # Подсветка строк и бейджи — ровно по одному на состояние.
    assert html.count('class="stock-row--below"') == 1
    assert html.count('class="stock-row--near"') == 1
    assert html.count(">Below min</span>") == 1
    assert html.count(">Near min</span>") == 1
    assert "In stock 0 · minimum 5 (near = up to 6)" in html
    assert "In stock 5 · minimum 5 (near = up to 6)" in html
    # Количество с единицей (не each) и «min N».
    assert '0 <span class="text-muted small">gal</span>' in html
    assert "min 5 gal" in html
    # Сводка над таблицей со ссылками-фильтрами.
    assert 'id="partsStockSummary"' in html
    assert "1 below minimum" in html and "1 near minimum" in html
    assert "stock_level=below" in html and "stock_level=near" in html

    ctx = next(c for t, c in templates if t.name == "public/parts.html")
    assert ctx["stock_level"] == "all"
    assert ctx["stock_counts"] == {"below": 1, "near": 1, "highlighted": 2}
    assert any(u["key"] == "gallon" for u in ctx["part_units"])


def test_parts_page_stock_level_filter(client, env, app):
    login(client)
    _seed_parts(client)

    with captured_templates(app) as templates:
        resp = client.get("/parts/?tab=parts&stock_level=below&q=" + PREFIX)
    html = resp.get_data(as_text=True)
    assert PREFIX + "BELOW" in html
    for number in ("NEAR", "HEALTHY", "PLAIN"):
        assert PREFIX + number not in html
    ctx = next(c for t, c in templates if t.name == "public/parts.html")
    assert ctx["stock_level"] == "below"
    assert ctx["pagination"]["total"] == 1
    # Счётчики считаются по выборке до фильтра уровня.
    assert ctx["stock_counts"] == {"below": 1, "near": 1, "highlighted": 2}
    # Селект показывает выбранный уровень, Reset доступен.
    assert 'value="below" selected' in html
    assert ">Reset</a>" in html

    with captured_templates(app) as templates:
        resp = client.get("/parts/?tab=parts&stock_level=near&q=" + PREFIX)
    html = resp.get_data(as_text=True)
    assert PREFIX + "BELOW" in html and PREFIX + "NEAR" in html
    assert PREFIX + "HEALTHY" not in html and PREFIX + "PLAIN" not in html
    ctx = next(c for t, c in templates if t.name == "public/parts.html")
    assert ctx["pagination"]["total"] == 2

    # Неизвестное значение — как «all».
    with captured_templates(app) as templates:
        resp = client.get("/parts/?tab=parts&stock_level=bogus&q=" + PREFIX)
    ctx = next(c for t, c in templates if t.name == "public/parts.html")
    assert ctx["stock_level"] == "all" and ctx["pagination"]["total"] == 4


def test_parts_page_filter_empty_state(client, env):
    login(client)
    _create(client, {"part_number": PREFIX + "OK", "in_stock": 50, "min_stock_enabled": True, "min_stock": 5})
    html = client.get("/parts/?tab=parts&stock_level=below&q=" + PREFIX).get_data(as_text=True)
    assert "No parts below minimum" in html
    assert 'id="partsStockSummary"' not in html


def test_edit_payload_carries_min_stock_and_unit(client, env):
    login(client)
    _seed_parts(client)
    html = client.get("/parts/?tab=parts&stock_level=below&q=" + PREFIX).get_data(as_text=True)
    assert "&#34;min_stock_enabled&#34;: true" in html
    assert "&#34;min_stock&#34;: 5" in html
    assert "&#34;unit_of_measure&#34;: &#34;gallon&#34;" in html
    # Селект единиц в модалке.
    assert 'id="partUnitSelect"' in html and '<option value="gallon">Gallon (gal)</option>' in html
    assert 'id="minStockToggle"' in html and 'name="min_stock"' in html


# ── Поиск (страница WO, модалка заказа) ────────────────────────────────


def test_search_returns_unit_and_stock_state(client, env):
    login(client)
    _seed_parts(client)
    items = client.get("/parts/api/search?q=" + PREFIX + "BELOW").get_json()["items"]
    item = next(i for i in items if i["part_number"] == PREFIX + "BELOW")
    assert item["unit"] == "gallon" and item["unit_short"] == "gal"
    assert item["min_stock_enabled"] is True and item["min_stock"] == 5
    assert item["stock_state"] == "below"
    assert item["do_not_track_inventory"] is False

    items = client.get("/parts/api/search?q=" + PREFIX + "HEALTHY").get_json()["items"]
    item = next(i for i in items if i["part_number"] == PREFIX + "HEALTHY")
    assert item["stock_state"] is None and item["min_stock_enabled"] is True
    assert item["unit"] == "each" and item["unit_short"] == "ea"

    items = client.get("/parts/api/search?q=" + PREFIX + "PLAIN").get_json()["items"]
    item = next(i for i in items if i["part_number"] == PREFIX + "PLAIN")
    assert item["stock_state"] is None and item["min_stock_enabled"] is False and item["min_stock"] == 0


# ── Импорт / экспорт ───────────────────────────────────────────────────


def test_export_parts_has_min_stock_and_unit_columns(client, env):
    login(client)
    _seed_parts(client)
    resp = client.get("/import-export/export/parts?fmt=csv")
    assert resp.status_code == 200
    rows = list(csv.reader(io.StringIO(resp.get_data(as_text=True).lstrip("﻿"))))
    header = rows[0]
    # Существующие колонки и их порядок не меняются, новые — в конце.
    assert header[:7] == ["Part Number", "Description", "Reference", "In Stock",
                          "Average Cost", "Selling Price", "Active"]
    assert header[7:] == ["Min Stock", "Unit"]
    by_number = {r[0]: r for r in rows[1:]}
    assert by_number[PREFIX + "BELOW"][7] == "5" and by_number[PREFIX + "BELOW"][8] == "gal"
    assert by_number[PREFIX + "NEAR"][7] == "5" and by_number[PREFIX + "NEAR"][8] == "ea"
    assert by_number[PREFIX + "PLAIN"][7] == "" and by_number[PREFIX + "PLAIN"][8] == "ea"


def test_import_parts_sets_min_stock_and_unit(client, env):
    login(client)
    csv_text = ("Part Number,Description,In Stock,Min Stock,Unit\n"
                f"{PREFIX}IMP1,Imported oil,2,5,Gallons\n"
                f"{PREFIX}IMP2,Imported hose,3,,\n"
                f"{PREFIX}IMP3,Imported grease,1,0,zzz\n")
    token = get_csrf_token(client)
    resp = client.post("/import-export/import", data={
        "csrf_token": token,
        "entity_type": "parts",
        "mapping": json.dumps({"Part Number": "part_number", "Description": "description",
                               "In Stock": "in_stock", "Min Stock": "min_stock", "Unit": "unit_of_measure"}),
        "file": (io.BytesIO(csv_text.encode("utf-8")), "parts.csv"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    result = resp.get_json()
    assert result["imported"] == 3 and result["skipped"] == 0, result
    # Неизвестная единица не отбрасывает строку, но попадает в предупреждения.
    assert any("zzz" in w for w in result.get("warnings", [])), result

    db = env["db"]
    p1 = db.parts.find_one({"part_number": PREFIX + "IMP1"})
    assert p1["min_stock_enabled"] is True and p1["min_stock"] == 5 and p1["unit_of_measure"] == "gallon"
    assert p1["in_stock"] == 2 and min_stock_state(p1) == "below"
    p2 = db.parts.find_one({"part_number": PREFIX + "IMP2"})
    assert p2["min_stock_enabled"] is False and p2["min_stock"] == 0 and p2["unit_of_measure"] == "each"
    p3 = db.parts.find_one({"part_number": PREFIX + "IMP3"})
    assert p3["min_stock_enabled"] is True and p3["min_stock"] == 0 and p3["unit_of_measure"] == "each"


def test_export_import_roundtrip(client, env):
    """Экспортированный файл маппится обратно и восстанавливает минимум и единицу."""
    login(client)
    _seed_parts(client)
    text = client.get("/import-export/export/parts?fmt=csv").get_data(as_text=True).lstrip("﻿")
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[0]
    kept = [header] + [r for r in rows[1:] if r[0] == PREFIX + "BELOW"]
    # Переименовываем, чтобы не упереться в дубль.
    kept[1][0] = PREFIX + "BELOW-COPY"
    out = io.StringIO()
    csv.writer(out).writerows(kept)

    token = get_csrf_token(client)
    resp = client.post("/import-export/import", data={
        "csrf_token": token,
        "entity_type": "parts",
        "mapping": json.dumps({"Part Number": "part_number", "Description": "description",
                               "In Stock": "in_stock", "Min Stock": "min_stock", "Unit": "unit_of_measure"}),
        "file": (io.BytesIO(out.getvalue().encode("utf-8")), "parts.csv"),
    }, content_type="multipart/form-data")
    result = resp.get_json()
    assert resp.status_code == 200 and result["imported"] == 1, result
    copy = env["db"].parts.find_one({"part_number": PREFIX + "BELOW-COPY"})
    assert copy["min_stock_enabled"] is True and copy["min_stock"] == 5
    assert copy["unit_of_measure"] == "gallon"
    assert "warnings" not in result
