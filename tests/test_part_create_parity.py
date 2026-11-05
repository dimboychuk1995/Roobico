"""
Паритет создания парта: JSON-роут POST /parts/api/create (полная модалка
«Create new part» в режиме заказа — Create & Add у несматченных строк AI
Order Reader и заказов из почты) должен давать тот же документ, что форма
POST /parts/create (Add Part на странице Parts): misc charges, единица,
минимальный остаток, selling price, core, ссылки vendor/category/location,
стартовый остаток и движение «initial». Плюс строгие 400 и рендер единой
модалки #createPartModal на страницах Parts и Work Order.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, get_csrf_token, login

PREFIX = "PARITY-"

# Отличаются по построению (id, номер, метки времени); остальное — 1:1.
IGNORED_KEYS = {"_id", "part_number", "search_terms", "created_at", "updated_at"}


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def env(seed, mongo):
    db = mongo[SHOP_A_DB]
    shop_id = seed["shop_a"]["_id"]
    now = datetime.now(timezone.utc)
    vendor = {"_id": ObjectId(), "shop_id": shop_id, "name": "Parity Vendor",
              "is_active": True, "created_at": now}
    category = {"_id": ObjectId(), "shop_id": shop_id, "name": "Parity Category",
                "is_active": True, "created_at": now}
    location = {"_id": ObjectId(), "shop_id": shop_id, "name": "Parity Shelf", "parent_id": None,
                "is_active": True, "created_at": now}
    db.vendors.insert_one(vendor)
    db.parts_categories.insert_one(category)
    db.parts_locations.insert_one(location)
    yield {"db": db, "shop_id": shop_id, "vendor": vendor, "category": category, "location": location}
    ids = [p["_id"] for p in db.parts.find({"part_number": {"$regex": "^" + PREFIX}}, {"_id": 1})]
    db.parts.delete_many({"_id": {"$in": ids}})
    db.part_location_stock.delete_many({"part_id": {"$in": ids}})
    db.inventory_movements.delete_many({"part_id": {"$in": ids}})
    db.vendors.delete_one({"_id": vendor["_id"]})
    db.parts_categories.delete_one({"_id": category["_id"]})
    db.parts_locations.delete_one({"_id": location["_id"]})


def _json_payload(env, number, **overrides):
    """Все поля формы — как их шлёт collectPartFormData() из parts.js."""
    payload = {
        "part_number": number,
        "description": "Parity engine oil",
        "reference": "OEM-PARITY-1",
        "vendor_id": str(env["vendor"]["_id"]),
        "category_id": str(env["category"]["_id"]),
        "location_id": str(env["location"]["_id"]),
        "in_stock": 3,
        "average_cost": 12.5,
        "do_not_track_inventory": False,
        "unit_of_measure": "gallon",
        "min_stock_enabled": True,
        "min_stock": 4,
        "has_selling_price": True,
        "selling_price": 19.99,
        "core_has_charge": True,
        "core_cost": 25,
        "misc_has_charge": True,
        "misc_charges": [
            {"description": "Shop fee", "price": 2.5, "taxable": True},
            {"description": "Eco fee", "price": 1, "taxable": False},
        ],
    }
    payload.update(overrides)
    return payload


def _form_payload(env, number, token):
    """Тот же ввод через классическую форму (чекбоксы «1», таблица misc)."""
    return {
        "csrf_token": token,
        "part_number": number,
        "description": "Parity engine oil",
        "reference": "OEM-PARITY-1",
        "vendor_id": str(env["vendor"]["_id"]),
        "category_id": str(env["category"]["_id"]),
        "location_id": str(env["location"]["_id"]),
        "in_stock": "3",
        "average_cost": "12.5",
        "unit_of_measure": "gallon",
        "min_stock_enabled": "1",
        "min_stock": "4",
        "has_selling_price": "1",
        "selling_price": "19.99",
        "core_has_charge": "1",
        "core_cost": "25",
        "misc_has_charge": "1",
        "misc_charges[0][description]": "Shop fee",
        "misc_charges[0][price]": "2.5",
        "misc_charges[0][taxable]": "1",
        "misc_charges[1][description]": "Eco fee",
        "misc_charges[1][price]": "1",
    }


def _api_create(client, payload, expect=200):
    token = get_csrf_token(client)
    resp = client.post("/parts/api/create", json=payload, headers={"X-CSRFToken": token})
    assert resp.status_code == expect, resp.get_data(as_text=True)
    return resp.get_json()


def _form_create(client, env, number, **overrides):
    token = get_csrf_token(client)
    form = _form_payload(env, number, token)
    form.update(overrides)
    resp = client.post("/parts/create", data=form)
    assert resp.status_code == 302, resp.get_data(as_text=True)
    doc = env["db"].parts.find_one({"part_number": number})
    assert doc is not None, "form route did not create the part"
    return doc


def _assert_same_document(form_doc, api_doc):
    assert sorted(form_doc.keys()) == sorted(api_doc.keys())
    for key in sorted(form_doc.keys()):
        if key in IGNORED_KEYS:
            continue
        assert form_doc[key] == api_doc[key], key


# ── JSON-роут хранит каждое поле формы ─────────────────────────────────


def test_api_create_stores_every_field(client, env):
    login(client)
    number = PREFIX + "FULL"
    data = _api_create(client, _json_payload(env, number))
    assert data["ok"] is True and data["part_id"]

    doc = env["db"].parts.find_one({"_id": ObjectId(data["part_id"])})
    assert doc is not None
    assert doc["shop_id"] == env["shop_id"]
    assert doc["part_number"] == number
    assert doc["description"] == "Parity engine oil"
    assert doc["reference"] == "OEM-PARITY-1"
    assert doc["vendor_id"] == env["vendor"]["_id"]
    assert doc["category_id"] == env["category"]["_id"]
    assert doc["location_id"] == env["location"]["_id"]
    assert doc["do_not_track_inventory"] is False
    assert doc["in_stock"] == 3
    assert doc["average_cost"] == 12.5
    assert doc["unit_of_measure"] == "gallon"
    assert doc["min_stock_enabled"] is True and doc["min_stock"] == 4
    assert doc["has_selling_price"] is True and doc["selling_price"] == 19.99
    assert doc["core_has_charge"] is True and doc["core_cost"] == 25.0
    assert doc["misc_has_charge"] is True
    assert doc["misc_charges"] == [
        {"description": "Shop fee", "price": 2.5, "taxable": True},
        {"description": "Eco fee", "price": 1.0, "taxable": False},
    ]
    assert doc["is_active"] is True

    # Стартовый остаток проведён через сервис остатков: движение «initial»
    # и строка локации парта.
    movement = env["db"].inventory_movements.find_one({"part_id": doc["_id"], "type": "initial"})
    assert movement is not None
    assert movement["qty_delta"] == 3 and movement["stock_after"] == 3
    assert movement["location_id"] == env["location"]["_id"]
    row = env["db"].part_location_stock.find_one(
        {"part_id": doc["_id"], "location_id": env["location"]["_id"]}
    )
    assert row is not None and row["qty"] == 3


def test_api_create_minimal_payload_keeps_defaults(client, env):
    """Старые клиенты шлют пару полей — дефолты как раньше."""
    login(client)
    data = _api_create(client, {"part_number": PREFIX + "MIN"})
    doc = env["db"].parts.find_one({"_id": ObjectId(data["part_id"])})
    assert doc["description"] is None and doc["reference"] is None
    assert doc["vendor_id"] is None and doc["category_id"] is None and doc["location_id"] is None
    assert doc["in_stock"] == 0 and doc["average_cost"] == 0.0
    assert doc["do_not_track_inventory"] is False
    assert doc["unit_of_measure"] == "each"
    assert doc["min_stock_enabled"] is False and doc["min_stock"] == 0
    assert doc["has_selling_price"] is False and doc["selling_price"] is None
    assert doc["core_has_charge"] is False and doc["core_cost"] is None
    assert doc["misc_has_charge"] is False and doc["misc_charges"] == []
    assert env["db"].inventory_movements.count_documents({"part_id": doc["_id"]}) == 0


# ── Форма и JSON дают одинаковый документ ──────────────────────────────


def test_form_and_api_routes_produce_equal_documents(client, env):
    login(client)
    form_doc = _form_create(client, env, PREFIX + "FORM")
    data = _api_create(client, _json_payload(env, PREFIX + "JSON"))
    api_doc = env["db"].parts.find_one({"_id": ObjectId(data["part_id"])})
    assert api_doc is not None

    _assert_same_document(form_doc, api_doc)
    # Не два пустых документа: ключевые поля действительно заполнены.
    assert api_doc["misc_charges"] and api_doc["in_stock"] == 3 and api_doc["core_cost"] == 25.0
    assert form_doc["vendor_id"] == env["vendor"]["_id"]


def test_do_not_track_drops_core_and_stock_on_both_routes(client, env):
    login(client)
    data = _api_create(client, _json_payload(env, PREFIX + "UNTRACKED", do_not_track_inventory=True))
    api_doc = env["db"].parts.find_one({"_id": ObjectId(data["part_id"])})
    assert api_doc["do_not_track_inventory"] is True
    assert "in_stock" not in api_doc
    assert api_doc["core_has_charge"] is False and api_doc["core_cost"] is None
    assert api_doc["min_stock_enabled"] is False and api_doc["min_stock"] == 0
    assert api_doc["misc_has_charge"] is True and len(api_doc["misc_charges"]) == 2
    assert env["db"].inventory_movements.count_documents({"part_id": api_doc["_id"]}) == 0

    form_doc = _form_create(client, env, PREFIX + "UNTRACKED-FORM", do_not_track_inventory="1")
    _assert_same_document(form_doc, api_doc)


# ── Строгие 400 как у формы ────────────────────────────────────────────


def test_api_create_rejects_bad_refs_and_negative_values(client, env):
    login(client)
    db = env["db"]
    number = PREFIX + "BAD"

    data = _api_create(client, _json_payload(env, number, vendor_id="not-an-id"), expect=400)
    assert data == {"ok": False, "error": "Invalid Vendor id."}
    data = _api_create(client, _json_payload(env, number, vendor_id=str(ObjectId())), expect=400)
    assert data["error"] == "Vendor not found."
    data = _api_create(client, _json_payload(env, number, category_id=str(ObjectId())), expect=400)
    assert data["error"] == "Category not found."
    data = _api_create(client, _json_payload(env, number, location_id="zzz"), expect=400)
    assert data["error"] == "Invalid Location id."

    data = _api_create(
        client, _json_payload(env, number, misc_charges=[{"description": "Bad", "price": -1}]), expect=400
    )
    assert data["error"] == "Misc charge price cannot be negative."
    data = _api_create(client, _json_payload(env, number, average_cost=-1), expect=400)
    assert data["error"] == "Average cost cannot be negative."
    data = _api_create(client, _json_payload(env, number, selling_price=-5), expect=400)
    assert data["error"] == "Selling price cannot be negative."
    data = _api_create(client, _json_payload(env, number, core_cost=-5), expect=400)
    assert data["error"] == "Core cost cannot be negative."
    data = _api_create(client, _json_payload(env, number, in_stock=-1), expect=400)
    assert data["error"] == "In stock cannot be negative."
    data = _api_create(client, _json_payload(env, number, misc_charges="oops"), expect=400)
    assert data["error"] == "Invalid misc charges"
    assert db.parts.count_documents({"part_number": number}) == 0

    # Misc charge с ценой, но без описания — 400 (как flash у формы), парт не создан.
    data = _api_create(client, _json_payload(
        env, number, misc_charges=[{"description": "", "price": 3}, {"description": "Kept", "price": 1}]
    ), expect=400)
    assert data["error"] == "Misc charge description is required."
    assert db.parts.count_documents({"part_number": number}) == 0

    # Выключенный misc → [] (список игнорируется целиком).
    data = _api_create(client, _json_payload(
        env, number + "-2", misc_has_charge=False, misc_charges=[{"description": "Ignored", "price": 9}]
    ))
    doc = db.parts.find_one({"_id": ObjectId(data["part_id"])})
    assert doc["misc_has_charge"] is False and doc["misc_charges"] == []


# ── Misc charges: одно правило для формы и JSON ────────────────────────


def test_misc_charge_rows_follow_one_rule_on_both_routes(client, env):
    """Описание обязательно; строка, где ничего не введено, игнорируется;
    цена без описания — отказ на обоих роутах (400 / flash без документа);
    строка только с описанием хранится с ценой 0 — одинаково на обоих."""
    login(client)
    db = env["db"]

    # Форма: цена без описания → flash + redirect, парт не создан.
    token = get_csrf_token(client)
    form = _form_payload(env, PREFIX + "MISC-FORM-BAD", token)
    form.update({"misc_charges[2][description]": "", "misc_charges[2][price]": "4"})
    resp = client.post("/parts/create", data=form)
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        flashes = list(sess.get("_flashes") or [])
    assert ("error", "Misc charge description is required.") in flashes
    assert db.parts.count_documents({"part_number": PREFIX + "MISC-FORM-BAD"}) == 0

    # JSON: пустая строка игнорируется, строка только с описанием → цена 0.
    rows_json = [
        {"description": "", "price": ""},
        {"description": "", "price": None},
        {"description": "Handling", "price": ""},
        {"description": "Eco fee", "price": 1, "taxable": False},
    ]
    data = _api_create(client, _json_payload(env, PREFIX + "MISC-JSON", misc_charges=rows_json))
    api_doc = db.parts.find_one({"_id": ObjectId(data["part_id"])})
    assert api_doc["misc_charges"] == [
        {"description": "Handling", "price": 0.0, "taxable": True},
        {"description": "Eco fee", "price": 1.0, "taxable": False},
    ]

    # Форма с тем же вводом даёт тот же документ.
    token = get_csrf_token(client)
    form = _form_payload(env, PREFIX + "MISC-FORM", token)
    for key in list(form):
        if key.startswith("misc_charges["):
            form.pop(key)
    form.update({
        "misc_charges[0][description]": "", "misc_charges[0][price]": "",
        "misc_charges[1][description]": "Handling", "misc_charges[1][price]": "", "misc_charges[1][taxable]": "1",
        "misc_charges[2][description]": "Eco fee", "misc_charges[2][price]": "1",
    })
    resp = client.post("/parts/create", data=form)
    assert resp.status_code == 302
    form_doc = db.parts.find_one({"part_number": PREFIX + "MISC-FORM"})
    assert form_doc is not None
    assert form_doc["misc_charges"] == api_doc["misc_charges"]
    _assert_same_document(form_doc, api_doc)

    # Правка парта (parts_api_update) — то же правило: цена без описания → 400,
    # документ не изменён.
    token = get_csrf_token(client)
    resp = client.post(
        f"/parts/api/{api_doc['_id']}/update",
        json=_json_payload(env, PREFIX + "MISC-JSON", misc_charges=[{"description": "", "price": 2}]),
        headers={"X-CSRFToken": token},
    )
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert resp.get_json()["error"] == "Misc charge description is required."
    assert db.parts.find_one({"_id": api_doc["_id"]})["misc_charges"] == api_doc["misc_charges"]


# ── Одна полная модалка парта на Parts и на странице WO ────────────────


def test_pages_render_single_full_part_modal(client, env):
    login(client)
    resp = client.get("/parts/?tab=parts")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert html.count('id="createPartModal"') == 1
    assert 'id="partUnitSelect"' in html
    assert "scanPartModal" not in html

    # Страница WO (создание): модалка заказа + та же полная модалка парта.
    resp = client.get("/work_orders/details")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="orderModal"' in html
    assert html.count('id="createPartModal"') == 1
    assert 'id="partUnitSelect"' in html
    assert '<option value="gallon">Gallon (gal)</option>' in html
    assert 'name="misc_has_charge"' in html and 'id="miscChargesBody"' in html
    assert 'name="min_stock_enabled"' in html and 'name="has_selling_price"' in html
    assert "Parity Vendor" in html  # вендоры страницы WO попали в селект модалки
    assert "scanPartModal" not in html
