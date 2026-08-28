"""Цены в сервис-темплейтах (wo_presets).

Правило: цена, введённая вручную в пресете (price_overridden), фиксируется и
применяется в WO вместо динамической (selling price парта / прайс-матрица).
Строки без фиксации продолжают следовать живым ценам каталога.
"""
from __future__ import annotations

import json
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
