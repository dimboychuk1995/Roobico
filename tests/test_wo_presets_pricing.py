"""Цены в сервис-темплейтах (wo_presets).

Правило: цена, введённая вручную в пресете (price_overridden), фиксируется и
применяется в WO вместо динамической (selling price парта / прайс-матрица).
Строки без фиксации продолжают следовать живым ценам каталога.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, get_csrf_token, login


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture()
def pricing_seed(seed, mongo):
    """Парт с фиксированной ценой, парт без неё и дефолтная матрица markup 50%."""
    sdb = mongo[SHOP_A_DB]
    shop_id = seed["shop_a"]["_id"]

    part_fixed = {
        "_id": ObjectId(),
        "shop_id": shop_id,
        "part_number": "PRESET-FIX-01",
        "description": "Part with fixed selling price",
        "average_cost": 40.0,
        "has_selling_price": True,
        "selling_price": 77.0,
        "is_active": True,
        "created_at": _now(),
    }
    part_matrix = {
        "_id": ObjectId(),
        "shop_id": shop_id,
        "part_number": "PRESET-MTX-01",
        "description": "Part priced by matrix",
        "average_cost": 100.0,
        "has_selling_price": False,
        "is_active": True,
        "created_at": _now(),
    }
    sdb.parts.insert_many([part_fixed, part_matrix])

    rules_id = sdb.parts_pricing_rules.insert_one({
        "shop_id": shop_id,
        "mode": "markup",
        "rules": [{"from": 0, "to": None, "value_percent": 50}],
        "is_default": True,
        "is_active": True,
        "created_at": _now(),
    }).inserted_id

    yield {"part_fixed": part_fixed, "part_matrix": part_matrix}

    sdb.parts.delete_many({"_id": {"$in": [part_fixed["_id"], part_matrix["_id"]]}})
    sdb.parts_pricing_rules.delete_one({"_id": rules_id})
    sdb.wo_presets.delete_many({"name": {"$regex": "^PYTEST-PRICING"}})


def _create_preset(client, name, parts):
    token = get_csrf_token(client)
    resp = client.post(
        "/settings/wo_presets/create",
        data={
            "name": name,
            "description": "",
            "labor_hours": "1",
            "labor_rate_code": "standard",
            "parts_json": json.dumps(parts),
            "csrf_token": token,
        },
    )
    assert resp.status_code == 302, resp.status_code
    return resp


def test_create_stores_price_overridden_flag(client, pricing_seed, mongo):
    login(client)
    _create_preset(client, "PYTEST-PRICING-FLAGS", [
        {
            "part_id": str(pricing_seed["part_fixed"]["_id"]),
            "part_number": "PRESET-FIX-01",
            "description": "x",
            "qty": 1,
            "cost": 40.0,
            "price": 123.45,
            "price_overridden": True,
        },
        {
            "part_id": str(pricing_seed["part_matrix"]["_id"]),
            "part_number": "PRESET-MTX-01",
            "description": "y",
            "qty": 2,
            "cost": 100.0,
            "price": 150.0,
            "price_overridden": False,
        },
    ])

    doc = mongo[SHOP_A_DB].wo_presets.find_one({"name": "PYTEST-PRICING-FLAGS"})
    assert doc is not None
    assert doc["parts"][0]["price_overridden"] is True
    assert doc["parts"][0]["price"] == 123.45
    assert doc["parts"][1]["price_overridden"] is False


def test_wo_preset_apply_uses_pinned_price(client, pricing_seed, mongo):
    login(client)
    _create_preset(client, "PYTEST-PRICING-APPLY", [
        {
            # Фиксация побеждает selling price парта (77).
            "part_id": str(pricing_seed["part_fixed"]["_id"]),
            "part_number": "PRESET-FIX-01",
            "description": "x",
            "qty": 1,
            "cost": 40.0,
            "price": 123.45,
            "price_overridden": True,
        },
        {
            # Без фиксации и без selling price -> None (WO посчитает по матрице).
            "part_id": str(pricing_seed["part_matrix"]["_id"]),
            "part_number": "PRESET-MTX-01",
            "description": "y",
            "qty": 1,
            "cost": 100.0,
            "price": 150.0,
            "price_overridden": False,
        },
    ])
    doc = mongo[SHOP_A_DB].wo_presets.find_one({"name": "PYTEST-PRICING-APPLY"})

    body = client.get(f"/work_orders/api/presets/{doc['_id']}").get_json()
    by_pn = {p["part_number"]: p for p in body["parts"]}
    assert by_pn["PRESET-FIX-01"]["price"] == 123.45
    assert by_pn["PRESET-MTX-01"]["price"] is None

    # Без фиксации selling price парта применяется как раньше.
    _create_preset(client, "PYTEST-PRICING-APPLY-AUTO", [{
        "part_id": str(pricing_seed["part_fixed"]["_id"]),
        "part_number": "PRESET-FIX-01",
        "description": "x",
        "qty": 1,
        "cost": 40.0,
        "price": 77.0,
        "price_overridden": False,
    }])
    doc2 = mongo[SHOP_A_DB].wo_presets.find_one({"name": "PYTEST-PRICING-APPLY-AUTO"})
    body2 = client.get(f"/work_orders/api/presets/{doc2['_id']}").get_json()
    assert body2["parts"][0]["price"] == 77.0


def test_settings_detail_keeps_pinned_price(client, pricing_seed, mongo):
    login(client)
    _create_preset(client, "PYTEST-PRICING-DETAIL", [
        {
            "part_id": str(pricing_seed["part_matrix"]["_id"]),
            "part_number": "PRESET-MTX-01",
            "description": "pinned",
            "qty": 1,
            "cost": 100.0,
            "price": 199.99,
            "price_overridden": True,
        },
        {
            "part_id": str(pricing_seed["part_matrix"]["_id"]),
            "part_number": "PRESET-MTX-01",
            "description": "auto",
            "qty": 1,
            "cost": 100.0,
            "price": 150.0,
            "price_overridden": False,
        },
    ])
    doc = mongo[SHOP_A_DB].wo_presets.find_one({"name": "PYTEST-PRICING-DETAIL"})

    body = client.get(f"/settings/wo_presets/{doc['_id']}").get_json()
    pinned = next(p for p in body["parts"] if p["description"] == "pinned")
    auto = next(p for p in body["parts"] if p["description"] == "auto")

    # cost 100, markup 50% -> автоцена 150
    assert pinned["price"] == 199.99
    assert pinned["price_overridden"] is True
    assert pinned["auto_price"] == 150.0
    assert auto["price"] == 150.0
    assert auto["price_overridden"] is False


# ── Карточка и редактор темплейта показывают то, что поставит WO ─────────
#
# Регрессия: страница Settings → Service Templates считала цены собственной
# копией правила и расходилась с work order — показывала цену-снимок, когда
# автоцену посчитать нельзя (cost $0), брала первую активную шкалу вместо
# дефолтной и не находила перезаведённую запчасть по номеру.

def _card_lines(html, preset_name):
    """({part_number: текст строки}, html карточки) темплейта preset_name."""
    start = html.index(f'<h2 class="h6 mb-0">{preset_name}</h2>')
    card = html[start: html.index("(Estimated)", start)]
    lines = {}
    for li in re.findall(r"<li[^>]*>(.*?)</li>", card, flags=re.S):
        text = re.sub(r"<[^>]+>", " ", li)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            lines[text.split(" ")[0]] = text
    return lines, card


@pytest.fixture()
def card_seed(pricing_seed, seed, mongo):
    """Темплейт со строками всех видов: шкала, фикс. цена ниже cost, cost $0,
    закреплённая цена, запчасть не из каталога, перезаведённая запчасть."""
    sdb = mongo[SHOP_A_DB]
    shop_id = seed["shop_a"]["_id"]

    def _part(number, cost, **extra):
        return {
            "_id": ObjectId(), "shop_id": shop_id, "part_number": number,
            "description": number, "average_cost": cost, "has_selling_price": False,
            "is_active": True, "created_at": _now(), **extra,
        }

    parts = {
        "oil": _part("CARD-OIL", 7.1017, has_selling_price=True, selling_price=6.0),
        "zero": _part("CARD-ZERO", 0.0),
        "pin": _part("CARD-PIN", 67.46),
        "renum": _part("CARD-RENUM", 10.0),
        "blank_fixed": _part("CARD-BLANK", 20.0, has_selling_price=True, selling_price=0),
    }
    sdb.parts.insert_many(list(parts.values()))

    def _row(part_id, number, qty, cost, price, pinned=False):
        return {
            "part_id": str(part_id), "part_number": number, "description": "row",
            "qty": qty, "cost": cost, "price": price, "price_overridden": pinned,
            "misc_charges": [],
        }

    preset_id = sdb.wo_presets.insert_one({
        "shop_id": shop_id, "name": "PYTEST-PRICING-CARD", "description": "",
        "labor_hours": None, "labor_rate_code": None, "allow_discount": True,
        "is_active": True, "created_at": _now(), "updated_at": _now(),
        "parts": [
            # Шкала 50% от текущей cost 100, а не снимок 111.
            _row(pricing_seed["part_matrix"]["_id"], "PRESET-MTX-01", 2.0, 70.0, 111.0),
            # Фиксированная Selling price запчасти ниже текущей cost.
            _row(parts["oil"]["_id"], "CARD-OIL", 39.0, 4.28, 6.0),
            # cost $0: снимок 65 показывать нельзя — WO такой цены не поставит.
            _row(parts["zero"]["_id"], "CARD-ZERO", 1.0, 57.03, 65.0),
            # Закреплённая в темплейте цена.
            _row(parts["pin"]["_id"], "CARD-PIN", 1.0, 83.65, 101.29, pinned=True),
            # Запчасти в каталоге нет: остаётся сохранённая цена.
            _row(ObjectId(), "CARD-GONE", 1.0, 5.0, 12.34),
            # Старый part_id, но номер жив: находим по номеру.
            _row(ObjectId(), "CARD-RENUM", 1.0, 1.0, 1.5),
        ],
    }).inserted_id

    yield {"parts": parts, "preset_id": preset_id, "sdb": sdb, "shop_id": shop_id}

    sdb.parts.delete_many({"_id": {"$in": [p["_id"] for p in parts.values()]}})
    sdb.wo_presets.delete_many({"_id": preset_id})


def test_card_shows_what_a_work_order_gets(client, card_seed):
    login(client)
    resp = client.get("/settings/wo_presets")
    assert resp.status_code == 200
    lines, card = _card_lines(resp.get_data(as_text=True), "PYTEST-PRICING-CARD")

    # Шкала от текущей себестоимости, без пометок.
    assert lines["PRESET-MTX-01"].endswith("@ $150.00")

    # Фиксированная цена запчасти: видно, почему она не следует за cost.
    assert "@ $6.00" in lines["CARD-OIL"]
    assert "fixed price" in lines["CARD-OIL"] and "below cost" in lines["CARD-OIL"]

    # cost $0 — цены нет (раньше карточка показывала снимок $65.00).
    assert "65.00" not in lines["CARD-ZERO"]
    assert "no price" in lines["CARD-ZERO"]

    assert "@ $101.29" in lines["CARD-PIN"] and "custom price" in lines["CARD-PIN"]
    assert "below cost" not in lines["CARD-PIN"]

    assert "@ $12.34" in lines["CARD-GONE"] and "not in catalog" in lines["CARD-GONE"]

    # 2×150 + 39×6 + 0 + 101.29 + 12.34 + 15 (шестая строка, найдена по номеру)
    assert "$662.63" in card
    assert "and 1 more" in card


def test_card_heals_stale_part_reference(client, card_seed):
    login(client)
    client.get("/settings/wo_presets")
    doc = card_seed["sdb"].wo_presets.find_one({"_id": card_seed["preset_id"]})
    renum = next(p for p in doc["parts"] if p["part_number"] == "CARD-RENUM")
    assert renum["part_id"] == str(card_seed["parts"]["renum"]["_id"])
    # Сохранённые деньги строки лечение не трогает.
    assert renum["price"] == 1.5


def test_card_price_equals_server_work_order_price(app, client, card_seed, pricing_seed):
    """Свойство целиком: цена строки в темплейте = цена, которую сервер
    поставит в WO кастомера без своей шкалы (suggest_part_price)."""
    from app.blueprints.work_orders.services.mobile_editor import suggest_part_price

    login(client)
    body = client.get(f"/settings/wo_presets/{card_seed['preset_id']}").get_json()
    sdb, shop_id = card_seed["sdb"], card_seed["shop_id"]
    live = {
        "PRESET-MTX-01": pricing_seed["part_matrix"],
        "CARD-OIL": card_seed["parts"]["oil"],
        "CARD-ZERO": card_seed["parts"]["zero"],
        "CARD-PIN": card_seed["parts"]["pin"],
        "CARD-RENUM": card_seed["parts"]["renum"],
    }
    checked = 0
    with app.app_context():
        for row in body["parts"]:
            part_doc = live.get(row["part_number"])
            if part_doc is None:
                continue
            pinned = row["price"] if row["price_overridden"] else None
            expected = suggest_part_price(sdb, shop_id, None, part_doc, pinned_price=pinned)
            assert row["price"] == expected, row
            checked += 1
    assert checked == 5


def test_settings_detail_reports_price_source(client, card_seed):
    login(client)
    body = client.get(f"/settings/wo_presets/{card_seed['preset_id']}").get_json()
    by_pn = {p["part_number"]: p for p in body["parts"]}

    assert by_pn["PRESET-MTX-01"]["auto_source"] == "matrix"
    assert by_pn["PRESET-MTX-01"]["cost"] == 100.0

    oil = by_pn["CARD-OIL"]
    assert (oil["price"], oil["auto_source"], oil["below_cost"]) == (6.0, "fixed", True)

    zero = by_pn["CARD-ZERO"]
    assert (zero["price"], zero["auto_price"], zero["auto_source"]) == (0.0, 0.0, "none")
    assert zero["below_cost"] is False

    pin = by_pn["CARD-PIN"]
    assert (pin["price"], pin["price_source"], pin["price_overridden"]) == (101.29, "pinned", True)
    assert pin["auto_price"] == 101.19  # 67.46 × 1.5 — к ней вернётся очищенное поле

    gone = by_pn["CARD-GONE"]
    assert (gone["price"], gone["price_source"]) == (12.34, "snapshot")
    assert "auto_price" not in gone


def test_templates_use_the_default_scale_like_work_orders(client, card_seed):
    """Вторая (не дефолтная) активная шкала не должна попадать в темплейты.

    Не дефолтная шкала стоит в коллекции ПЕРВОЙ (и по порядку вставки, и по
    имени): выборка «первая активная», как было раньше, дала бы 2% -> $102.
    """
    sdb = card_seed["sdb"]
    shop_id = card_seed["shop_id"]
    default = sdb.parts_pricing_rules.find_one({"shop_id": shop_id, "is_default": True})
    sdb.parts_pricing_rules.delete_one({"_id": default["_id"]})
    other = sdb.parts_pricing_rules.insert_one({
        "shop_id": shop_id, "name": "Aa Our Companies", "mode": "markup",
        "rules": [{"from": 0, "to": None, "value_percent": 2}],
        "is_default": False, "is_active": True, "created_at": _now(),
    }).inserted_id
    # Тот же документ (тот же _id — его удалит фикстура), но теперь после «Aa».
    sdb.parts_pricing_rules.insert_one({**default, "name": "Zz Default"})
    try:
        first_active = sdb.parts_pricing_rules.find_one({"shop_id": shop_id, "is_active": True})
        assert first_active["_id"] == other, "fixture must make the non-default scale come first"

        login(client)
        body = client.get(f"/settings/wo_presets/{card_seed['preset_id']}").get_json()
        by_pn = {p["part_number"]: p for p in body["parts"]}
        assert by_pn["PRESET-MTX-01"]["price"] == 150.0
        html = client.get("/settings/wo_presets").get_data(as_text=True)
        lines, _ = _card_lines(html, "PYTEST-PRICING-CARD")
        assert "@ $150.00" in lines["PRESET-MTX-01"]
    finally:
        sdb.parts_pricing_rules.delete_one({"_id": other})


def test_selling_price_flag_without_a_price_is_not_a_fixed_price(client, card_seed, mongo):
    """Галочка «has selling price» с ценой $0 — «не заполнено»: и темплейт,
    и WO считают такую запчасть по шкале, а не продают за $0."""
    from app.blueprints.work_orders.services.mobile_editor import fixed_selling_price

    blank = card_seed["parts"]["blank_fixed"]
    assert fixed_selling_price(blank) is None
    assert fixed_selling_price({"has_selling_price": True, "selling_price": 6}) == 6.0
    assert fixed_selling_price({"has_selling_price": False, "selling_price": 6}) is None

    login(client)
    _create_preset(client, "PYTEST-PRICING-BLANK", [{
        "part_id": str(blank["_id"]), "part_number": "CARD-BLANK", "description": "x",
        "qty": 1, "cost": 20.0, "price": 0, "price_overridden": False,
    }])
    doc = mongo[SHOP_A_DB].wo_presets.find_one({"name": "PYTEST-PRICING-BLANK"})

    # WO: цены нет -> редактор посчитает по шкале кастомера.
    wo_part = client.get(f"/work_orders/api/presets/{doc['_id']}").get_json()["parts"][0]
    assert wo_part["price"] is None
    # Темплейт: шкала 50% от cost 20.
    settings_part = client.get(f"/settings/wo_presets/{doc['_id']}").get_json()["parts"][0]
    assert (settings_part["price"], settings_part["auto_source"]) == (30.0, "matrix")


def test_pinned_zero_price_survives_and_is_shown(client, pricing_seed, mongo):
    """Закреплённые $0 (бесплатная позиция в пакете) — настоящая цена:
    сервер хранит её, отдаёт редактору как 0.0 с флагом, карточка пишет
    «@ $0.00 custom price», WO получает 0."""
    login(client)
    _create_preset(client, "PYTEST-PRICING-FREE", [{
        "part_id": str(pricing_seed["part_matrix"]["_id"]), "part_number": "PRESET-MTX-01",
        "description": "free", "qty": 1, "cost": 100.0, "price": 0, "price_overridden": True,
    }])
    doc = mongo[SHOP_A_DB].wo_presets.find_one({"name": "PYTEST-PRICING-FREE"})
    assert doc["parts"][0]["price_overridden"] is True

    row = client.get(f"/settings/wo_presets/{doc['_id']}").get_json()["parts"][0]
    assert (row["price"], row["price_overridden"], row["auto_price"]) == (0.0, True, 150.0)

    lines, _ = _card_lines(client.get("/settings/wo_presets").get_data(as_text=True), "PYTEST-PRICING-FREE")
    assert "@ $0.00" in lines["PRESET-MTX-01"] and "custom price" in lines["PRESET-MTX-01"]

    wo_row = client.get(f"/work_orders/api/presets/{doc['_id']}").get_json()["parts"][0]
    assert wo_row["price"] == 0.0


def test_unpinned_rows_from_the_editor_follow_auto_price(client, pricing_seed, mongo):
    """Что редактор шлёт для пустого поля цены: автоцену (или 0, когда её
    нет) без флага фиксации — строка остаётся на автоцене, сохранённое число
    ни на что не влияет."""
    login(client)
    _create_preset(client, "PYTEST-PRICING-UNPINNED", [
        {"part_id": str(pricing_seed["part_matrix"]["_id"]), "part_number": "PRESET-MTX-01",
         "description": "auto", "qty": 1, "cost": 100.0, "price": 150.0, "price_overridden": False},
        {"part_id": str(pricing_seed["part_matrix"]["_id"]), "part_number": "PRESET-MTX-01",
         "description": "stale", "qty": 1, "cost": 1.0, "price": 0, "price_overridden": False},
    ])
    doc = mongo[SHOP_A_DB].wo_presets.find_one({"name": "PYTEST-PRICING-UNPINNED"})
    assert [p["price_overridden"] for p in doc["parts"]] == [False, False]

    body = client.get(f"/settings/wo_presets/{doc['_id']}").get_json()
    assert [p["price"] for p in body["parts"]] == [150.0, 150.0]
    wo = client.get(f"/work_orders/api/presets/{doc['_id']}").get_json()
    assert [p["price"] for p in wo["parts"]] == [None, None]  # WO посчитает по шкале кастомера


def test_null_price_is_never_stored_as_pinned(client, pricing_seed, mongo):
    """Страховка сервера: price null с флагом фиксации (старый или ручной
    клиент) не сохраняется как закреплённая цена."""
    login(client)
    _create_preset(client, "PYTEST-PRICING-EMPTY", [{
        "part_id": str(pricing_seed["part_matrix"]["_id"]), "part_number": "PRESET-MTX-01",
        "description": "x", "qty": 1, "cost": 100.0, "price": None, "price_overridden": True,
    }])
    doc = mongo[SHOP_A_DB].wo_presets.find_one({"name": "PYTEST-PRICING-EMPTY"})
    assert doc["parts"][0]["price_overridden"] is False

    body = client.get(f"/settings/wo_presets/{doc['_id']}").get_json()
    assert body["parts"][0]["price"] == 150.0


def test_cost_between_scale_tiers_uses_the_rounded_tier():
    """Ступени шкалы — в центах, average cost точнее: 5.004 лежит между
    «… – 5.00» и «5.01 – …». Берём ступень по округлённому значению, а не
    продаём по себестоимости."""
    from app.blueprints.work_orders.services.mobile_editor import (
        PRICE_AT_COST, PRICE_MATRIX, PRICE_NONE, match_pricing_rule, price_part,
    )

    rules = [
        {"from": 0.0, "to": 5.0, "value_percent": 100.0},
        {"from": 5.01, "to": 10.0, "value_percent": 50.0},
    ]
    pricing = {"mode": "markup", "rules": rules, "override_part_selling_price": False}

    assert match_pricing_rule(5.004, rules)["value_percent"] == 100.0
    assert match_pricing_rule(5.006, rules)["value_percent"] == 50.0
    # Точное попадание в ступень округление не меняет.
    assert match_pricing_rule(4.999, rules)["value_percent"] == 100.0
    assert match_pricing_rule(7.5, rules)["value_percent"] == 50.0
    assert match_pricing_rule(10.5, rules) is None
    # Ровно полцента — вверх (5.005 -> 5.01), как и в JS-копиях правила.
    assert match_pricing_rule(5.005, rules)["value_percent"] == 50.0

    assert price_part({"average_cost": 5.004}, pricing) == (10.01, PRICE_MATRIX)
    assert price_part({"average_cost": 5.006}, pricing) == (7.51, PRICE_MATRIX)
    assert price_part({"average_cost": 10.5}, pricing) == (10.5, PRICE_AT_COST)
    assert price_part({"average_cost": 0}, pricing) == (0.0, PRICE_NONE)
    assert price_part({"average_cost": 12.0}, None) == (12.0, PRICE_AT_COST)


def test_js_copies_of_the_tier_rule_round_like_the_server():
    """Правило ступени продублировано в JS (редактор WO и редактор
    темплейта). Округление запасного кандидата обязано совпадать с серверным
    round2 = round(cost + 1e-12, 2), иначе карточка темплейта и WO берут
    разные ступени для cost на границе."""
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    expected = "Number((cost + 1e-12).toFixed(2))"
    for rel, fn in (
        ("app/static/js/work_orders/work_order_details.js", "function matchRule("),
        ("app/static/js/settings/wo_presets.js", "function matchPricingRule("),
    ):
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            src = fh.read()
        body = src[src.index(fn): src.index(fn) + 1500]
        assert expected in body, rel

    from app.blueprints.work_orders.services.common import round2
    # Серверная половина того же контракта.
    assert (round2(1.005), round2(5.005), round2(5.004), round2(0.125)) == (1.01, 5.01, 5.0, 0.13)


def test_customer_override_beats_pinned_and_fixed_prices():
    from app.blueprints.work_orders.services.mobile_editor import (
        PRICE_FIXED, PRICE_MATRIX, PRICE_PINNED, price_part,
    )

    part = {"average_cost": 7.10, "has_selling_price": True, "selling_price": 6.0}
    standard = {"mode": "markup", "rules": [{"from": 0.0, "to": None, "value_percent": 100.0}],
                "override_part_selling_price": False}
    own = {"mode": "markup", "rules": [{"from": 0.0, "to": None, "value_percent": 2.0}],
           "override_part_selling_price": True}

    assert price_part(part, standard) == (6.0, PRICE_FIXED)
    assert price_part(part, standard, pinned_price=9.99) == (9.99, PRICE_PINNED)
    # «Override part selling price»: шкала кастомера от текущей cost.
    assert price_part(part, own) == (7.24, PRICE_MATRIX)
    assert price_part(part, own, pinned_price=9.99) == (7.24, PRICE_MATRIX)
