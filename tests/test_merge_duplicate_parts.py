"""
Скрипт слияния дублей запчастей (app/scripts/merge_duplicate_parts.py):
перенос остатков, локаций и всех ссылок на выжившего, идемпотентность.
"""
from __future__ import annotations

import pytest
from bson import ObjectId
from pymongo import ASCENDING, MongoClient

from app.scripts.merge_duplicate_parts import (
    collect_groups, merge_dupe_into, pick_survivor,
)
from tests.conftest import TEST_MONGO_URI

SHOP_ID = ObjectId()
LOC_A = ObjectId()
LOC_B = ObjectId()


@pytest.fixture()
def sdb():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    db = client["roobico_test_merge_parts"]
    client.drop_database(db.name)
    # Боевой уникальный индекс — merge обязан его не нарушать
    db.part_location_stock.create_index(
        [("shop_id", ASCENDING), ("part_id", ASCENDING), ("location_id", ASCENDING)],
        unique=True,
    )
    yield db
    client.drop_database(db.name)
    client.close()


def _seed(sdb):
    survivor_id, dupe_id = ObjectId(), ObjectId()
    sdb.parts.insert_many([
        {"_id": survivor_id, "shop_id": SHOP_ID, "part_number": "AB-1",
         "description": "Original", "in_stock": 4, "is_active": True,
         "created_at": 1},
        {"_id": dupe_id, "shop_id": SHOP_ID, "part_number": "ab-1 ",
         "description": "Dup", "in_stock": 3, "is_active": True,
         "created_at": 2, "interchange_group": ObjectId()},
    ])
    sdb.part_location_stock.insert_many([
        {"shop_id": SHOP_ID, "part_id": survivor_id, "location_id": LOC_A,
         "part_number": "AB-1", "qty": 4},
        # Коллизия: у дубля строка в ТОЙ ЖЕ локации + своя локация
        {"shop_id": SHOP_ID, "part_id": dupe_id, "location_id": LOC_A,
         "part_number": "ab-1", "qty": 2},
        {"shop_id": SHOP_ID, "part_id": dupe_id, "location_id": LOC_B,
         "part_number": "ab-1", "qty": 1},
    ])
    wo_id = ObjectId()
    sdb.work_orders.insert_one({
        "_id": wo_id, "shop_id": SHOP_ID,
        "labors": [
            {"labor": {}, "parts": [
                {"part_id": dupe_id, "part_number": "ab-1", "quantity": 1},
                {"part_id": str(dupe_id), "part_number": "ab-1", "quantity": 2},
            ]},
        ],
        "inventory_deductions": [
            {"part_id": str(survivor_id), "part_number": "AB-1", "qty_used": 1},
            {"part_id": str(dupe_id), "part_number": "ab-1", "qty_used": 2},
        ],
    })
    po_id = ObjectId()
    sdb.parts_orders.insert_one({
        "_id": po_id, "shop_id": SHOP_ID,
        "items": [{"part_id": dupe_id, "part_number": "ab-1", "quantity": 5}],
        "received_item_locations": {str(dupe_id): str(LOC_B)},
    })
    sdb.wo_presets.insert_one({
        "shop_id": SHOP_ID,
        "parts": [{"part_id": str(dupe_id), "part_number": "ab-1"}],
    })
    sdb.cores.insert_many([
        {"shop_id": SHOP_ID, "part_id": survivor_id, "is_active": True, "quantity": 1},
        {"shop_id": SHOP_ID, "part_id": dupe_id, "is_active": True, "quantity": 2},
    ])
    sdb.inventory_movements.insert_many([
        {"shop_id": SHOP_ID, "part_id": dupe_id, "type": "initial", "qty_delta": 3},
    ])
    sdb.attachments.insert_one(
        {"shop_id": SHOP_ID, "entity_type": "part", "entity_id": dupe_id})
    return survivor_id, dupe_id, wo_id, po_id


def test_merge_moves_stock_and_all_references(sdb):
    survivor_id, dupe_id, wo_id, po_id = _seed(sdb)

    groups = collect_groups(sdb, SHOP_ID)
    assert list(groups.keys()) == ["ab-1"]
    survivor = pick_survivor(groups["ab-1"])
    assert survivor["_id"] == survivor_id

    dupe = next(d for d in groups["ab-1"] if d["_id"] == dupe_id)
    merge_dupe_into(sdb, SHOP_ID, survivor, dupe)

    # Остаток: 4 + 3
    assert sdb.parts.find_one({"_id": survivor_id})["in_stock"] == 7
    merged_doc = sdb.parts.find_one({"_id": dupe_id})
    assert merged_doc["in_stock"] == 0
    assert merged_doc["is_active"] is False
    assert merged_doc["merged_into"] == survivor_id
    assert "interchange_group" not in merged_doc

    # Локации: A = 4+2, B = 1; строк дубля не осталось
    rows = {r["location_id"]: r["qty"] for r in
            sdb.part_location_stock.find({"shop_id": SHOP_ID, "part_id": survivor_id})}
    assert rows == {LOC_A: 6, LOC_B: 1}
    assert sdb.part_location_stock.count_documents({"part_id": dupe_id}) == 0
    # Инвариант multi-location: in_stock == сумме по локациям
    assert sum(rows.values()) == 7

    # WO: обе формы ссылок (ObjectId и строка) указывают на выжившего
    wo = sdb.work_orders.find_one({"_id": wo_id})
    part_ids = [p["part_id"] for p in wo["labors"][0]["parts"]]
    assert part_ids == [survivor_id, survivor_id]
    # deductions схлопнуты в одну строку с суммой qty_used
    ded = wo["inventory_deductions"]
    assert len(ded) == 1
    assert ded[0]["part_id"] == str(survivor_id)
    assert ded[0]["qty_used"] == 3

    # parts_orders: items + перекладка ключа received_item_locations
    po = sdb.parts_orders.find_one({"_id": po_id})
    assert po["items"][0]["part_id"] == survivor_id
    assert po["received_item_locations"] == {str(survivor_id): str(LOC_B)}

    # Пресет (строковый id)
    preset = sdb.wo_presets.find_one({"shop_id": SHOP_ID})
    assert preset["parts"][0]["part_id"] == str(survivor_id)

    # Cores: количество слито в core выжившего, дубликат-core погашен
    s_core = sdb.cores.find_one({"part_id": survivor_id, "is_active": True})
    assert s_core["quantity"] == 3
    d_core = sdb.cores.find_one({"part_id": dupe_id})
    assert d_core["is_active"] is False and d_core["quantity"] == 0

    # Движения и вложения перевешаны
    assert sdb.inventory_movements.count_documents({"part_id": dupe_id}) == 0
    assert sdb.inventory_movements.count_documents({"part_id": survivor_id}) == 1
    assert sdb.attachments.find_one({"entity_type": "part"})["entity_id"] == survivor_id


def test_merge_is_idempotent(sdb):
    survivor_id, dupe_id, _, _ = _seed(sdb)
    groups = collect_groups(sdb, SHOP_ID)
    survivor = pick_survivor(groups["ab-1"])
    dupe = next(d for d in groups["ab-1"] if d["_id"] == dupe_id)
    merge_dupe_into(sdb, SHOP_ID, survivor, dupe)

    # После слияния группа исчезает (merged-док исключён из группировки)
    assert collect_groups(sdb, SHOP_ID) == {}
    assert sdb.parts.find_one({"_id": survivor_id})["in_stock"] == 7


def test_group_without_active_parts_is_skipped(sdb):
    sdb.parts.insert_many([
        {"_id": ObjectId(), "shop_id": SHOP_ID, "part_number": "X1",
         "is_active": False, "created_at": 1},
        {"_id": ObjectId(), "shop_id": SHOP_ID, "part_number": "x1",
         "is_active": False, "created_at": 2},
    ])
    groups = collect_groups(sdb, SHOP_ID)
    assert pick_survivor(groups["x1"]) is None
