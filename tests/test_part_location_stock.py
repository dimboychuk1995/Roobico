"""Мульти-локационные остатки: строки part_location_stock, журнал движений,
перенос между локациями, ручные поправки, списание WO из primary-локации."""
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
    loc1 = {"_id": ObjectId(), "shop_id": shop_id, "name": "Main shelf", "parent_id": None,
            "is_active": True, "created_at": now}
    loc2 = {"_id": ObjectId(), "shop_id": shop_id, "name": "Back room", "parent_id": None,
            "is_active": True, "created_at": now}
    db.parts_locations.insert_many([loc1, loc2])

    yield {"db": db, "shop_id": shop_id, "loc1": loc1, "loc2": loc2}

    db.parts_locations.delete_many({"_id": {"$in": [loc1["_id"], loc2["_id"]]}})
    parts = list(db.parts.find({"part_number": {"$regex": "^MLS-"}}, {"_id": 1}))
    part_ids = [p["_id"] for p in parts]
    db.parts.delete_many({"_id": {"$in": part_ids}})
    db.part_location_stock.delete_many({"part_id": {"$in": part_ids}})
    db.inventory_movements.delete_many({"part_id": {"$in": part_ids}})
    db.work_orders.delete_many({"shop_id": shop_id, "labors.parts.part_id": {"$in": [str(x) for x in part_ids]}})


def _create_part(client, env, part_number, in_stock, location_id):
    token = get_csrf_token(client)
    resp = client.post(
        "/parts/api/create",
        json={
            "part_number": part_number,
            "description": "multi-location test part",
            "in_stock": in_stock,
            "location_id": str(location_id) if location_id else "",
        },
        headers={"X-CSRFToken": token},
    )
    data = resp.get_json()
    assert data and data.get("ok"), data
    return ObjectId(data["part_id"])


def _part(env, part_id):
    return env["db"].parts.find_one({"_id": part_id})


def _row_qty(env, part_id, location_id):
    row = env["db"].part_location_stock.find_one(
        {"shop_id": env["shop_id"], "part_id": part_id, "location_id": location_id}
    )
    return int(row["qty"]) if row else None


def test_initial_stock_creates_row_and_movement(logged_in, env):
    part_id = _create_part(logged_in, env, "MLS-INIT-1", 10, env["loc1"]["_id"])

    assert int(_part(env, part_id)["in_stock"]) == 10
    assert _row_qty(env, part_id, env["loc1"]["_id"]) == 10

    mv = env["db"].inventory_movements.find_one({"part_id": part_id, "type": "initial"})
    assert mv and mv["qty_delta"] == 10 and mv["stock_after"] == 10
    assert mv["location_id"] == env["loc1"]["_id"]


def test_transfer_between_locations(logged_in, env):
    part_id = _create_part(logged_in, env, "MLS-TRANS-1", 10, env["loc1"]["_id"])
    token = get_csrf_token(logged_in)

    resp = logged_in.post(
        f"/parts/api/{part_id}/locations/transfer",
        json={
            "from_location_id": str(env["loc1"]["_id"]),
            "to_location_id": str(env["loc2"]["_id"]),
            "qty": 4,
        },
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json().get("ok"), resp.get_json()

    assert int(_part(env, part_id)["in_stock"]) == 10, "transfer must not change the total"
    assert _row_qty(env, part_id, env["loc1"]["_id"]) == 6
    assert _row_qty(env, part_id, env["loc2"]["_id"]) == 4
    assert env["db"].inventory_movements.count_documents({"part_id": part_id, "type": "transfer"}) == 2

    # Больше, чем есть в источнике — отказ
    resp = logged_in.post(
        f"/parts/api/{part_id}/locations/transfer",
        json={
            "from_location_id": str(env["loc1"]["_id"]),
            "to_location_id": str(env["loc2"]["_id"]),
            "qty": 100,
        },
        headers={"X-CSRFToken": token},
    )
    assert resp.status_code == 400
    assert _row_qty(env, part_id, env["loc1"]["_id"]) == 6


def test_adjust_location_qty(logged_in, env):
    part_id = _create_part(logged_in, env, "MLS-ADJ-1", 10, env["loc1"]["_id"])
    token = get_csrf_token(logged_in)

    resp = logged_in.post(
        f"/parts/api/{part_id}/locations/adjust",
        json={"location_id": str(env["loc1"]["_id"]), "qty": 7},
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json().get("ok"), resp.get_json()

    assert int(_part(env, part_id)["in_stock"]) == 7
    assert _row_qty(env, part_id, env["loc1"]["_id"]) == 7
    mv = env["db"].inventory_movements.find_one({"part_id": part_id, "type": "manual_edit"})
    assert mv and mv["qty_delta"] == -3


def test_wo_deducts_from_primary_location(logged_in, env, app):
    part_id = _create_part(logged_in, env, "MLS-WO-1", 10, env["loc1"]["_id"])

    token = get_csrf_token(logged_in)
    resp = logged_in.post(
        "/work_orders/create",
        data={
            "csrf_token": token,
            "customer_id": str(ObjectId()),
            "unit_id": str(ObjectId()),
            "labors[0][labor_description]": "Job",
            "labors[0][labor_full_total]": "50",
            "labors[0][parts][0][part_number]": "MLS-WO-1",
            "labors[0][parts][0][part_id]": str(part_id),
            "labors[0][parts][0][qty]": "2",
            "labors[0][parts][0][price]": "5",
        },
    )
    assert resp.status_code in (200, 302)

    assert int(_part(env, part_id)["in_stock"]) == 8
    assert _row_qty(env, part_id, env["loc1"]["_id"]) == 8

    mv = env["db"].inventory_movements.find_one({"part_id": part_id, "type": "wo_deduct"})
    assert mv and mv["qty_delta"] == -2 and mv["stock_after"] == 8
    assert (mv.get("ref") or {}).get("kind") == "work_order"


def _create_wo_with_part(client, part_id, part_number, qty):
    token = get_csrf_token(client)
    return client.post(
        "/work_orders/create",
        data={
            "csrf_token": token,
            "customer_id": str(ObjectId()),
            "unit_id": str(ObjectId()),
            "labors[0][labor_description]": "Job",
            "labors[0][labor_full_total]": "50",
            "labors[0][parts][0][part_number]": part_number,
            "labors[0][parts][0][part_id]": str(part_id),
            "labors[0][parts][0][qty]": str(qty),
            "labors[0][parts][0][price]": "5",
        },
    )


def test_wo_deduct_waterfall_across_locations(logged_in, env):
    """Списание в WO: сначала дефолтная локация, потом остальные с остатком;
    нехватка уводит дефолтную в минус."""
    part_id = _create_part(logged_in, env, "MLS-WF-1", 8, env["loc1"]["_id"])
    token = get_csrf_token(logged_in)

    # Раскладываем: L1 (дефолт) = 3, L2 = 5
    resp = logged_in.post(
        f"/parts/api/{part_id}/locations/transfer",
        json={
            "from_location_id": str(env["loc1"]["_id"]),
            "to_location_id": str(env["loc2"]["_id"]),
            "qty": 5,
        },
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json().get("ok")

    # WO на 6 шт: 3 из дефолтной, 3 из второй
    assert _create_wo_with_part(logged_in, part_id, "MLS-WF-1", 6).status_code in (200, 302)
    assert int(_part(env, part_id)["in_stock"]) == 2
    assert _row_qty(env, part_id, env["loc1"]["_id"]) == 0
    assert _row_qty(env, part_id, env["loc2"]["_id"]) == 2
    assert env["db"].inventory_movements.count_documents(
        {"part_id": part_id, "type": "wo_deduct"}
    ) == 2, "по движению на каждую задействованную локацию"

    # Ещё WO на 4 шт: 2 из L2, нехватка 2 уходит минусом в дефолтную L1
    assert _create_wo_with_part(logged_in, part_id, "MLS-WF-1", 4).status_code in (200, 302)
    assert int(_part(env, part_id)["in_stock"]) == -2
    assert _row_qty(env, part_id, env["loc1"]["_id"]) == -2
    assert _row_qty(env, part_id, env["loc2"]["_id"]) == 0


def _insert_order(env, part_id, qty, price=2.0):
    now = datetime.now(timezone.utc)
    order = {
        "_id": ObjectId(),
        "shop_id": env["shop_id"],
        "order_number": 99001,
        "status": "ordered",
        "items": [{"part_id": part_id, "part_number": "x", "quantity": qty, "price": price}],
        "non_inventory_amounts": [],
        "is_active": True,
        "created_at": now,
    }
    env["db"].parts_orders.insert_one(order)
    return order


def test_receive_into_chosen_location_sets_default(logged_in, env):
    """Приёмка в выбранную локацию; у парта без дефолтной локации выбранная
    становится дефолтной. Unreceive снимает из той же локации."""
    part_id = _create_part(logged_in, env, "MLS-RCV-1", 0, None)
    order = _insert_order(env, part_id, 6)
    token = get_csrf_token(logged_in)

    resp = logged_in.post(
        f"/parts/api/orders/{order['_id']}/receive",
        json={"vendor_bill": "INV-1", "item_locations": {str(part_id): str(env["loc2"]["_id"])}},
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json().get("ok"), resp.get_json()

    part = _part(env, part_id)
    assert int(part["in_stock"]) == 6
    assert part.get("location_id") == env["loc2"]["_id"], "выбранная при приёмке локация стала дефолтной"
    assert _row_qty(env, part_id, env["loc2"]["_id"]) == 6

    order_after = env["db"].parts_orders.find_one({"_id": order["_id"]})
    assert order_after["received_item_locations"] == {str(part_id): str(env["loc2"]["_id"])}

    # Отмена приёмки — из той же локации
    resp = logged_in.post(
        f"/parts/api/orders/{order['_id']}/unreceive",
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json().get("ok"), resp.get_json()
    assert int(_part(env, part_id)["in_stock"]) == 0
    assert _row_qty(env, part_id, env["loc2"]["_id"]) == 0

    env["db"].parts_orders.delete_one({"_id": order["_id"]})


def test_receive_defaults_to_part_location(logged_in, env):
    """Без выбора локации приёмка идёт в дефолтную локацию парта."""
    part_id = _create_part(logged_in, env, "MLS-RCV-2", 0, env["loc1"]["_id"])
    order = _insert_order(env, part_id, 4)
    token = get_csrf_token(logged_in)

    resp = logged_in.post(
        f"/parts/api/orders/{order['_id']}/receive",
        json={"vendor_bill": ""},
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json().get("ok"), resp.get_json()
    assert _row_qty(env, part_id, env["loc1"]["_id"]) == 4

    # receive-context отдаёт дефолтную локацию для предвыбора в диалоге
    resp = logged_in.get(f"/parts/api/orders/{order['_id']}/receive-context")
    data = resp.get_json()
    assert data.get("ok")

    env["db"].parts_orders.delete_one({"_id": order["_id"]})


def test_legacy_part_materializes_remainder(logged_in, env, app):
    """Парт с in_stock без строк (легаси): перенос материализует хвост в primary."""
    now = datetime.now(timezone.utc)
    part = {
        "_id": ObjectId(),
        "shop_id": env["shop_id"],
        "part_number": "MLS-LEGACY-1",
        "in_stock": 5,
        "location_id": env["loc1"]["_id"],
        "is_active": True,
        "created_at": now,
    }
    env["db"].parts.insert_one(part)

    token = get_csrf_token(logged_in)
    resp = logged_in.post(
        f"/parts/api/{part['_id']}/locations/transfer",
        json={
            "from_location_id": str(env["loc1"]["_id"]),
            "to_location_id": str(env["loc2"]["_id"]),
            "qty": 2,
        },
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json().get("ok"), resp.get_json()

    assert _row_qty(env, part["_id"], env["loc1"]["_id"]) == 3
    assert _row_qty(env, part["_id"], env["loc2"]["_id"]) == 2
    assert int(_part(env, part["_id"])["in_stock"]) == 5
    assert env["db"].inventory_movements.count_documents(
        {"part_id": part["_id"], "type": "backfill"}
    ) == 1
