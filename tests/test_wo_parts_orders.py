"""
Парт-ордера, привязанные к work order: создание с work_order_id, блок в WO
(статусы + сверка использования позиций), бейдж WO # в общем списке заказов.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, get_csrf_token, login


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def wo_env(seed, mongo):
    db = mongo[SHOP_A_DB]
    shop_id = seed["shop_a"]["_id"]
    now = _now()

    vendor_id = db.vendors.insert_one({
        "shop_id": shop_id, "name": "WO Link Vendor", "is_active": True, "created_at": now,
    }).inserted_id

    part_a = {"_id": ObjectId(), "shop_id": shop_id, "part_number": "WOPO-A",
              "description": "Part A", "in_stock": 0, "average_cost": 10.0,
              "is_active": True, "created_at": now}
    part_b = {"_id": ObjectId(), "shop_id": shop_id, "part_number": "WOPO-B",
              "description": "Part B", "in_stock": 0, "average_cost": 5.0,
              "is_active": True, "created_at": now}
    part_c = {"_id": ObjectId(), "shop_id": shop_id, "part_number": "WOPO-C",
              "description": "Part C", "in_stock": 0, "average_cost": 7.0,
              "is_active": True, "created_at": now}
    db.parts.insert_many([part_a, part_b, part_c])

    # WO использует: A qty 2 (по ObjectId), B qty 1 (легаси — только part_number)
    wo = {
        "_id": ObjectId(), "shop_id": shop_id, "tenant_id": seed["tenant_a"]["_id"],
        "wo_number": 88001, "status": "in_progress", "is_active": True,
        "work_order_date": now, "created_at": now,
        "labors": [{
            "labor_id": "L1", "labor": {"description": "Job"},
            "parts": [
                {"part_id": part_a["_id"], "part_number": "WOPO-A", "qty": 2, "price": 20},
                {"part_number": "WOPO-B", "qty": 1, "price": 8, "one_time_part": True},
            ],
        }],
    }
    db.work_orders.insert_one(wo)

    yield {"db": db, "shop_id": shop_id, "vendor_id": vendor_id,
           "parts": {"a": part_a, "b": part_b, "c": part_c}, "wo": wo}

    db.parts_orders.delete_many({"shop_id": shop_id, "work_order_id": wo["_id"]})
    db.work_orders.delete_one({"_id": wo["_id"]})
    db.parts.delete_many({"_id": {"$in": [part_a["_id"], part_b["_id"], part_c["_id"]]}})
    db.vendors.delete_one({"_id": vendor_id})


def _create_linked_order(client, wo_env, items):
    token = get_csrf_token(client)
    resp = client.post("/parts/api/orders/create", json={
        "vendor_id": str(wo_env["vendor_id"]),
        "work_order_id": str(wo_env["wo"]["_id"]),
        "items": items,
    }, headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["ok"] is True
    return data["order_id"]


def test_create_order_linked_to_wo(client, wo_env):
    login(client)
    order_id = _create_linked_order(client, wo_env, [
        {"part_id": str(wo_env["parts"]["a"]["_id"]), "quantity": 2, "price": 10.0},
    ])
    order = wo_env["db"].parts_orders.find_one({"_id": ObjectId(order_id)})
    assert order["work_order_id"] == wo_env["wo"]["_id"]
    assert order["work_order_number"] == 88001

    # Несуществующий WO — отказ
    token = get_csrf_token(client)
    resp = client.post("/parts/api/orders/create", json={
        "vendor_id": str(wo_env["vendor_id"]),
        "work_order_id": str(ObjectId()),
        "items": [{"part_id": str(wo_env["parts"]["a"]["_id"]), "quantity": 1, "price": 1}],
    }, headers={"X-CSRFToken": token})
    assert resp.status_code == 400
    assert "Work order not found" in resp.get_json()["error"]


def test_linked_orders_endpoint_usage_comparison(client, wo_env):
    login(client)
    _create_linked_order(client, wo_env, [
        {"part_id": str(wo_env["parts"]["a"]["_id"]), "quantity": 2, "price": 10.0},  # used
        {"part_id": str(wo_env["parts"]["b"]["_id"]), "quantity": 3, "price": 5.0},   # partial 1/3
        {"part_id": str(wo_env["parts"]["c"]["_id"]), "quantity": 1, "price": 7.0},   # unused
    ])

    resp = client.get(f"/work_orders/api/work_orders/{wo_env['wo']['_id']}/parts_orders")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["ok"] is True
    assert len(data["orders"]) == 1
    order = data["orders"][0]
    assert order["vendor"] == "WO Link Vendor"
    assert order["status"] == "ordered"
    assert order["payment_status"] == "unpaid"
    assert order["total_amount"] == 42.0  # 2*10 + 3*5 + 1*7

    usage = {it["part_number"]: it for it in order["items"]}
    assert usage["WOPO-A"]["usage"] == "used"
    assert usage["WOPO-B"]["usage"] == "partial"
    assert usage["WOPO-B"]["used_qty"] == 1
    assert usage["WOPO-C"]["usage"] == "unused"

    unused = {u["part_number"] for u in order["unused"]}
    assert unused == {"WOPO-B", "WOPO-C"}


def test_parts_orders_list_shows_wo_badge(client, wo_env):
    login(client)
    _create_linked_order(client, wo_env, [
        {"part_id": str(wo_env["parts"]["a"]["_id"]), "quantity": 1, "price": 10.0},
    ])
    page = client.get("/parts/?tab=orders&date_preset=all_time").get_data(as_text=True)
    assert "WO #88001" in page


def test_vendors_lookup(client, wo_env):
    login(client)
    resp = client.get("/work_orders/api/vendors-lookup")
    assert resp.status_code == 200
    names = [v["name"] for v in resp.get_json()["vendors"]]
    assert "WO Link Vendor" in names


def test_pending_order_links_on_wo_create(client, wo_env, mongo):
    """Заказ, созданный ДО сохранения WO, висит на pending-id и при создании
    WO перепривязывается на настоящий id с номером."""
    login(client)
    db = wo_env["db"]
    pending_id = ObjectId()

    token = get_csrf_token(client)
    resp = client.post("/parts/api/orders/create", json={
        "vendor_id": str(wo_env["vendor_id"]),
        "pending_work_order_id": str(pending_id),
        "items": [{"part_id": str(wo_env["parts"]["a"]["_id"]), "quantity": 1, "price": 10.0}],
    }, headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    order_id = ObjectId(resp.get_json()["order_id"])

    order = db.parts_orders.find_one({"_id": order_id})
    assert order["work_order_id"] == pending_id
    assert order.get("work_order_number") is None

    # Блок на странице создания видит заказ по pending-id (сверка пустая)
    api = client.get(f"/work_orders/api/work_orders/{pending_id}/parts_orders").get_json()
    assert api["ok"] is True
    assert len(api["orders"]) == 1
    assert api["orders"][0]["items"][0]["usage"] == "unused"

    # Создаём WO с этим pending_attachment_id — заказ перепривязывается
    token = get_csrf_token(client)
    resp = client.post("/work_orders/create", data={
        "csrf_token": token,
        "customer_id": str(ObjectId()),
        "unit_id": str(ObjectId()),
        "pending_attachment_id": str(pending_id),
        "labors[0][labor_description]": "Linked job",
        "labors[0][labor_full_total]": "50",
    })
    assert resp.status_code in (200, 302)

    fresh = db.parts_orders.find_one({"_id": order_id})
    new_wo = db.work_orders.find_one({"_id": fresh["work_order_id"]})
    assert new_wo is not None, "заказ перепривязан на настоящий WO"
    assert fresh["work_order_number"] == new_wo["wo_number"]

    db.work_orders.delete_one({"_id": new_wo["_id"]})
    db.parts_orders.delete_one({"_id": order_id})
