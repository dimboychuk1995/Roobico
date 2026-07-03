"""Инвентарь при редактировании WO: удаление лейбора/запчасти должно
возвращать списанные запчасти на склад (diff old vs new в /update).

Репро бага: «удаляю лейбор с запчастями или запчасть, WO остаётся —
запчасти в инвентори не возвращаются».
"""
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from tests.conftest import SHOP_A_DB, get_csrf_token, login


@pytest.fixture()
def logged_in(client):
    assert login(client).status_code == 302
    return client


@pytest.fixture()
def inv(app, seed):
    from app.extensions import get_mongo_client
    with app.app_context():
        db = get_mongo_client()[SHOP_A_DB]
        customer_id = ObjectId()
        unit_id = ObjectId()
        part = {
            "_id": ObjectId(),
            "shop_id": seed["shop_a"]["_id"],
            "part_number": "INV-TEST-1",
            "description": "Inventory test part",
            "in_stock": 10,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
        }
        db.parts.insert_one(part)
        yield {"db": db, "part": part, "customer_id": customer_id, "unit_id": unit_id}
        db.parts.delete_one({"_id": part["_id"]})
        db.work_orders.delete_many({"customer_id": customer_id})


def _stock(app, inv):
    with app.app_context():
        doc = inv["db"].parts.find_one({"_id": inv["part"]["_id"]}, {"in_stock": 1})
    return int(doc["in_stock"])


def _create_wo(client, inv, with_second_labor=False):
    token = get_csrf_token(client)
    data = {
        "csrf_token": token,
        "customer_id": str(inv["customer_id"]),
        "unit_id": str(inv["unit_id"]),
        "labors[0][labor_description]": "Job with part",
        "labors[0][labor_full_total]": "100",
        "labors[0][parts][0][part_number]": inv["part"]["part_number"],
        "labors[0][parts][0][part_id]": str(inv["part"]["_id"]),
        "labors[0][parts][0][qty]": "2",
        "labors[0][parts][0][price]": "5",
    }
    if with_second_labor:
        data["labors[1][labor_description]"] = "Job without parts"
        data["labors[1][labor_full_total]"] = "50"
    resp = client.post("/work_orders/create", data=data)
    assert resp.status_code in (200, 302)
    return resp


def _find_wo(app, inv):
    with app.app_context():
        return inv["db"].work_orders.find_one({"customer_id": inv["customer_id"], "is_active": True})


def _flat_labor(description, total, parts=None):
    """Форма, которую шлёт фронт (serializeBlocks в work_order_details.js)."""
    return {
        "labor_description": description,
        "labor_hours": 0,
        "labor_rate_code": "",
        "labor_full_total": total,
        "assigned_mechanics": [],
        "issue_description": "",
        "parts": parts or [],
    }


def _update_wo(client, wo_id, labors):
    token = get_csrf_token(client)
    return client.post(
        f"/work_orders/api/work_orders/{wo_id}/update",
        json={"labors": labors, "totals": {}},
        headers={"X-CSRFToken": token},
    )


def test_create_deducts_stock(logged_in, inv, app):
    _create_wo(logged_in, inv)
    assert _stock(app, inv) == 8


def test_removing_part_row_restores_stock(logged_in, inv, app):
    """Удалили запчасть из лейбора, WO сохранили — склад должен вернуться."""
    _create_wo(logged_in, inv)
    assert _stock(app, inv) == 8
    wo = _find_wo(app, inv)

    resp = _update_wo(logged_in, wo["_id"], [_flat_labor("Job with part", 100, parts=[])])
    data = resp.get_json()
    assert data["ok"] is True, data
    assert _stock(app, inv) == 10, f"expected stock restored to 10, got {_stock(app, inv)}; resp={data}"


def test_removing_labor_with_parts_restores_stock(logged_in, inv, app):
    """Удалили целый лейбор с запчастями, WO сохранили — склад должен вернуться."""
    _create_wo(logged_in, inv, with_second_labor=True)
    assert _stock(app, inv) == 8
    wo = _find_wo(app, inv)

    # Остался только лейбор без запчастей
    resp = _update_wo(logged_in, wo["_id"], [_flat_labor("Job without parts", 50)])
    data = resp.get_json()
    assert data["ok"] is True, data
    assert _stock(app, inv) == 10, f"expected stock restored to 10, got {_stock(app, inv)}; resp={data}"


def test_qty_decrease_restores_difference(logged_in, inv, app):
    """Уменьшили qty с 2 до 1 — на склад должна вернуться разница."""
    _create_wo(logged_in, inv)
    assert _stock(app, inv) == 8
    wo = _find_wo(app, inv)

    part_payload = {
        "part_id": str(inv["part"]["_id"]),
        "one_time_part": False,
        "part_number": inv["part"]["part_number"],
        "description": "Inventory test part",
        "qty": 1,
        "cost": 0,
        "price": 5,
        "core_charge": 0,
        "misc_charge": 0,
        "misc_charge_description": "",
    }
    resp = _update_wo(logged_in, wo["_id"], [_flat_labor("Job with part", 100, parts=[part_payload])])
    data = resp.get_json()
    assert data["ok"] is True, data
    assert _stock(app, inv) == 9, f"expected 9, got {_stock(app, inv)}; resp={data}"
