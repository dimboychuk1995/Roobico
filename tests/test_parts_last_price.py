"""
Last price и средняя себестоимость партов.

- last_price = цена из последнего по дате парт-ордера с партом; пересчёт при
  создании / изменении / удалении заказа и подтверждении email-заказа;
  возвраты и неподтверждённые email-заказы не считаются.
- average_cost: взвешенная средняя при приёмке; остаток ≤ 0 и нулевая средняя
  не усредняются; unreceive вынимает партию из средней.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from app.blueprints.parts.services.costing import (
    refresh_last_price,
    reverse_weighted_average_cost,
    weighted_average_cost,
)
from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, get_csrf_token, login


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def env(seed, mongo):
    db = mongo[SHOP_A_DB]
    shop_id = seed["shop_a"]["_id"]
    now = _now()

    vendor_id = db.vendors.insert_one({
        "shop_id": shop_id, "name": "Last Price Vendor", "is_active": True, "created_at": now,
    }).inserted_id
    vendor2_id = db.vendors.insert_one({
        "shop_id": shop_id, "name": "Last Price Vendor 2", "is_active": True, "created_at": now,
    }).inserted_id
    manual_vendor_id = db.vendors.insert_one({
        "shop_id": shop_id, "name": "Manual Vendor", "is_active": True, "created_at": now,
    }).inserted_id

    def part(number, **extra):
        doc = {
            "_id": ObjectId(), "shop_id": shop_id, "part_number": number,
            "description": f"Desc {number}", "in_stock": 0, "average_cost": 10.0,
            "is_active": True, "created_at": now, "vendor_id": manual_vendor_id,
        }
        doc.update(extra)
        return doc

    p1 = part("LASTP-1")
    p2 = part("LASTP-2", in_stock=2, average_cost=100.0)
    p3 = part("LASTP-3", in_stock=-3, average_cost=100.0)
    db.parts.insert_many([p1, p2, p3])

    yield {"db": db, "shop_id": shop_id, "vendor_id": vendor_id, "vendor2_id": vendor2_id,
           "manual_vendor_id": manual_vendor_id, "p1": p1, "p2": p2, "p3": p3}

    ids = [p1["_id"], p2["_id"], p3["_id"]]
    db.parts_orders.delete_many({"shop_id": shop_id, "items.part_id": {"$in": ids}})
    db.inventory_movements.delete_many({"part_id": {"$in": ids}})
    db.part_location_stock.delete_many({"part_id": {"$in": ids}})
    db.parts.delete_many({"_id": {"$in": ids}})
    db.vendors.delete_many({"_id": {"$in": [vendor_id, vendor2_id, manual_vendor_id]}})


def _create_order(client, env, items, order_date=None, vendor_id=None):
    token = get_csrf_token(client)
    payload = {"vendor_id": str(vendor_id or env["vendor_id"]), "items": items}
    if order_date:
        payload["order_date"] = order_date
    resp = client.post("/parts/api/orders/create", json=payload, headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["ok"] is True
    return data["order_id"]


def _part(env, key):
    return env["db"].parts.find_one({"_id": env[key]["_id"]})


# ── Чистые формулы ───────────────────────────────────────────────────────

def test_weighted_average_cost_rules():
    # Пример пользователя: 2 по $100 + 2 по $200 → $150.
    assert weighted_average_cost(2, 100, 2, 200) == 150.0
    assert weighted_average_cost(10, 100, 1, 200) == pytest.approx(109.0909, abs=0.001)
    # Нулевой остаток / неизвестная средняя — цена прихода.
    assert weighted_average_cost(0, 100, 3, 200) == 200.0
    assert weighted_average_cost(5, 0, 3, 200) == 200.0
    # Минус от WO не усредняется (раньше давало 350).
    assert weighted_average_cost(-3, 100, 5, 200) == 200.0
    # Нечего приходовать — средняя как была.
    assert weighted_average_cost(4, 150, 0, 999) == 150.0


def test_reverse_weighted_average_cost_rules():
    # Откат партии 2 по $200 из 4 по $150 → 2 по $100.
    assert reverse_weighted_average_cost(4, 150, 2, 200) == 100.0
    # После отката ничего не остаётся — средняя не трогается.
    assert reverse_weighted_average_cost(2, 200, 2, 200) == 200.0
    # Стоимость ушла бы в минус — не трогаем.
    assert reverse_weighted_average_cost(3, 10, 2, 100) == 10.0


# ── last_price через API заказов ─────────────────────────────────────────

def test_last_price_follows_latest_order_by_date(client, env):
    login(client)
    db = env["db"]
    pid = env["p1"]["_id"]
    assert _part(env, "p1").get("last_price") is None

    order_a = _create_order(client, env, [{"part_id": str(pid), "quantity": 1, "price": 10.0}],
                            order_date="2026-10-01")
    part = _part(env, "p1")
    assert part["last_price"] == 10.0
    assert part["last_price_order_id"] == ObjectId(order_a)
    assert part["last_price_order_number"] is not None
    # Вендор парта = вендор последнего заказа (ручной вендор перезаписан).
    assert part["vendor_id"] == env["vendor_id"]

    # Более старый по дате заказ (у другого вендора) не становится «последним».
    order_b = _create_order(client, env, [{"part_id": str(pid), "quantity": 1, "price": 12.0}],
                            order_date="2026-09-01", vendor_id=env["vendor2_id"])
    assert _part(env, "p1")["last_price"] == 10.0
    assert _part(env, "p1")["vendor_id"] == env["vendor_id"]

    # Правка цены в последнем заказе — last_price обновляется.
    token = get_csrf_token(client)
    resp = client.post(f"/parts/api/orders/{order_a}/update", json={
        "vendor_id": str(env["vendor_id"]), "order_date": "2026-10-01",
        "items": [{"part_id": str(pid), "quantity": 1, "price": 11.0}],
    }, headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _part(env, "p1")["last_price"] == 11.0

    # Удалили последний — откат к предыдущему (и его вендору); удалили и его —
    # last_price None, вендор остаётся последним известным (не трогаем).
    token = get_csrf_token(client)
    resp = client.delete(f"/parts/api/orders/{order_a}", headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _part(env, "p1")["last_price"] == 12.0
    assert _part(env, "p1")["vendor_id"] == env["vendor2_id"]
    token = get_csrf_token(client)
    client.delete(f"/parts/api/orders/{order_b}", headers={"X-CSRFToken": token})
    assert _part(env, "p1")["last_price"] is None
    assert _part(env, "p1")["vendor_id"] == env["vendor2_id"]
    assert db.parts_orders.count_documents({"items.part_id": pid, "is_active": {"$ne": False}}) == 0


def test_vendor_switches_to_latest_order_vendor(client, env):
    """Купили у другого — вендор парта переключился; парт без заказов
    сохраняет вендора, выставленного руками."""
    login(client)
    pid = env["p2"]["_id"]
    assert _part(env, "p2")["vendor_id"] == env["manual_vendor_id"]

    _create_order(client, env, [{"part_id": str(pid), "quantity": 1, "price": 5.0}],
                  order_date="2026-10-01", vendor_id=env["vendor_id"])
    assert _part(env, "p2")["vendor_id"] == env["vendor_id"]

    _create_order(client, env, [{"part_id": str(pid), "quantity": 1, "price": 6.0}],
                  order_date="2026-10-05", vendor_id=env["vendor2_id"])
    part = _part(env, "p2")
    assert part["vendor_id"] == env["vendor2_id"] and part["last_price"] == 6.0

    # Парт без заказов (p3) вендора не меняет.
    assert _part(env, "p3")["vendor_id"] == env["manual_vendor_id"]


def test_part_removed_from_order_loses_last_price(client, env):
    login(client)
    p1, p2 = env["p1"]["_id"], env["p2"]["_id"]
    order_id = _create_order(client, env, [
        {"part_id": str(p1), "quantity": 1, "price": 5.0},
        {"part_id": str(p2), "quantity": 1, "price": 6.0},
    ])
    assert _part(env, "p1")["last_price"] == 5.0 and _part(env, "p2")["last_price"] == 6.0
    token = get_csrf_token(client)
    resp = client.post(f"/parts/api/orders/{order_id}/update", json={
        "vendor_id": str(env["vendor_id"]),
        "items": [{"part_id": str(p2), "quantity": 1, "price": 7.0}],
    }, headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _part(env, "p1")["last_price"] is None
    assert _part(env, "p2")["last_price"] == 7.0


def test_unconfirmed_email_order_counts_only_after_confirm(env):
    from app.blueprints.parts.services.email_orders import confirm_email_order

    db = env["db"]
    pid = env["p1"]["_id"]
    order_id = db.parts_orders.insert_one({
        "shop_id": env["shop_id"], "vendor_id": env["vendor_id"], "order_number": 999100,
        "items": [{"part_id": pid, "part_number": "LASTP-1", "quantity": 1, "price": 99.0, "core_charge": 0.0}],
        "non_inventory_amounts": [], "status": "ordered", "is_active": True,
        "needs_confirmation": True, "source": {}, "order_date": _now(), "created_at": _now(),
    }).inserted_id
    refresh_last_price(db, env["shop_id"], [pid])
    assert _part(env, "p1")["last_price"] is None

    order = db.parts_orders.find_one({"_id": order_id})
    ok, _ = confirm_email_order(db, {"_id": env["shop_id"]}, order, actor_user_id=None)
    assert ok
    assert _part(env, "p1")["last_price"] == 99.0


def test_search_and_part_api_expose_last_price(client, env):
    login(client)
    _create_order(client, env, [{"part_id": str(env["p1"]["_id"]), "quantity": 2, "price": 33.5}])

    resp = client.get("/parts/api/search?q=LASTP")
    items = {i["part_number"]: i for i in resp.get_json()["items"]}
    assert items["LASTP-1"]["last_price"] == 33.5
    assert items["LASTP-2"]["last_price"] is None  # заказов не было

    resp = client.get(f"/parts/api/{env['p1']['_id']}")
    assert resp.get_json()["item"]["last_price"] == 33.5

    html = client.get("/parts/?tab=parts&q=LASTP-1").get_data(as_text=True)
    assert "Last price" in html and "33.50" in html


# ── Средняя себестоимость через приёмку ──────────────────────────────────

def test_receive_and_unreceive_keep_average_consistent(client, env):
    login(client)
    pid = env["p2"]["_id"]  # 2 в наличии по $100
    order_id = _create_order(client, env, [{"part_id": str(pid), "quantity": 2, "price": 200.0}])

    token = get_csrf_token(client)
    resp = client.post(f"/parts/api/orders/{order_id}/receive", json={"vendor_bill": "INV-AVG"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    part = _part(env, "p2")
    assert part["in_stock"] == 4 and part["average_cost"] == 150.0

    token = get_csrf_token(client)
    resp = client.post(f"/parts/api/orders/{order_id}/unreceive", headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    part = _part(env, "p2")
    assert part["in_stock"] == 2 and part["average_cost"] == 100.0

    # Повторная приёмка не удваивает партию в средней.
    token = get_csrf_token(client)
    client.post(f"/parts/api/orders/{order_id}/receive", json={"vendor_bill": "INV-AVG"},
                headers={"X-CSRFToken": token})
    part = _part(env, "p2")
    assert part["in_stock"] == 4 and part["average_cost"] == 150.0


def test_receive_on_negative_stock_uses_received_price(client, env):
    login(client)
    pid = env["p3"]["_id"]  # -3 в наличии по $100 (WO списали в минус)
    order_id = _create_order(client, env, [{"part_id": str(pid), "quantity": 5, "price": 200.0}])
    token = get_csrf_token(client)
    resp = client.post(f"/parts/api/orders/{order_id}/receive", json={}, headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    part = _part(env, "p3")
    assert part["in_stock"] == 2
    assert part["average_cost"] == 200.0  # раньше формула давала 350
