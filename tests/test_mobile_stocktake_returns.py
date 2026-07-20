"""Mobile API: инвентаризация (list/create/detail + count/complete через
веб-JSON роуты) и возвраты вендору (пометка в списке заказов)."""
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, SHOP_A_DB


def _mobile_login(client, email=OWNER_EMAIL, password=OWNER_PASSWORD):
    resp = client.post(
        "/api/mobile/login",
        json={"email": email, "password": password},
        environ_base={"REMOTE_ADDR": "127.0.0.7"},
    )
    assert resp.status_code == 200
    return resp.get_json()


@pytest.fixture()
def env(app, seed):
    from app.extensions import get_mongo_client

    with app.app_context():
        db = get_mongo_client()[SHOP_A_DB]

    shop_id = seed["shop_a"]["_id"]
    now = datetime.now(timezone.utc)
    loc = {"_id": ObjectId(), "shop_id": shop_id, "name": "Mob shelf", "parent_id": None,
           "is_active": True, "created_at": now}
    db.parts_locations.insert_one(loc)
    part = {
        "_id": ObjectId(),
        "shop_id": shop_id,
        "part_number": "MOB-ST-1",
        "description": "Mobile stocktake part",
        "in_stock": 6,
        "average_cost": 3.0,
        "location_id": loc["_id"],
        "is_active": True,
        "created_at": now,
    }
    db.parts.insert_one(part)

    yield {"db": db, "shop_id": shop_id, "loc": loc, "part": part}

    st_ids = [s["_id"] for s in db.stocktakes.find({"shop_id": shop_id}, {"_id": 1})]
    db.stocktakes.delete_many({"shop_id": shop_id})
    db.stocktake_items.delete_many({"stocktake_id": {"$in": st_ids}})
    db.parts_orders.delete_many({"shop_id": shop_id, "items.part_id": part["_id"]})
    db.parts.delete_one({"_id": part["_id"]})
    db.part_location_stock.delete_many({"part_id": part["_id"]})
    db.inventory_movements.delete_many({"part_id": part["_id"]})
    db.parts_locations.delete_one({"_id": loc["_id"]})


def test_mobile_stocktake_full_flow(client, env):
    login = _mobile_login(client)
    headers = {"X-CSRFToken": login["csrf_token"]}

    # Справочники для формы создания
    resp = client.get("/api/mobile/stocktake_options")
    data = resp.get_json()
    assert data["ok"]
    assert any(l["path"] == "Mob shelf" for l in data["locations"])

    # Создание со scope по локации
    resp = client.post(
        "/api/mobile/stocktakes",
        json={"name": "Mobile count", "location_id": str(env["loc"]["_id"]), "category_id": ""},
        headers=headers,
    )
    data = resp.get_json()
    assert data["ok"], data
    st_id = data["id"]

    # Список
    resp = client.get("/api/mobile/stocktakes")
    rows = resp.get_json()["items"]
    assert any(r["id"] == st_id and r["status"] == "open" for r in rows)

    # Детали: позиция нашего парта с expected 6
    resp = client.get(f"/api/mobile/stocktakes/{st_id}")
    detail = resp.get_json()
    assert detail["ok"]
    item = next(it for it in detail["items"] if it["part_number"] == "MOB-ST-1")
    assert item["expected"] == 6
    assert item["status"] == "pending"

    # Подсчёт — через тот же веб-JSON роут, которым пользуется приложение
    resp = client.post(
        f"/parts/stocktakes/{st_id}/count",
        json={"item_id": item["id"], "counted_qty": 4},
        headers=headers,
    )
    assert resp.get_json()["ok"]

    resp = client.get(f"/api/mobile/stocktakes/{st_id}")
    detail = resp.get_json()
    item = next(it for it in detail["items"] if it["part_number"] == "MOB-ST-1")
    assert item["counted_qty"] == 4
    assert item["variance"] == -2
    assert detail["items_counted"] >= 1

    # Завершение
    resp = client.post(
        f"/parts/stocktakes/{st_id}/complete",
        json={"zero_uncounted": False},
        headers=headers,
    )
    assert resp.get_json()["ok"]

    part_after = env["db"].parts.find_one({"_id": env["part"]["_id"]})
    assert int(part_after["in_stock"]) == 4

    resp = client.get(f"/api/mobile/stocktakes/{st_id}")
    detail = resp.get_json()
    assert detail["status"] == "completed"
    assert detail["totals"]["items_adjusted"] == 1


def test_mobile_parts_orders_marks_returns(client, env):
    login = _mobile_login(client)
    headers = {"X-CSRFToken": login["csrf_token"]}

    now = datetime.now(timezone.utc)
    order = {
        "_id": ObjectId(),
        "shop_id": env["shop_id"],
        "order_number": 97001,
        "status": "ordered",
        "items": [{
            "part_id": env["part"]["_id"],
            "part_number": env["part"]["part_number"],
            "quantity": 5,
            "price": 2.0,
        }],
        "non_inventory_amounts": [],
        "is_active": True,
        "created_at": now,
        "order_date": now,
    }
    env["db"].parts_orders.insert_one(order)

    resp = client.post(
        f"/parts/api/orders/{order['_id']}/receive",
        json={"vendor_bill": "MB-1"},
        headers=headers,
    )
    assert resp.get_json()["ok"]

    # Возврат — через тот же веб-JSON роут, которым пользуется приложение
    resp = client.post(
        f"/parts/api/orders/{order['_id']}/returns",
        json={"items": [{"part_id": str(env["part"]["_id"]), "quantity": 2}], "notes": "mob"},
        headers=headers,
    )
    data = resp.get_json()
    assert data["ok"], data

    # Мобильный список заказов помечает возврат и отдаёт кредит минусом
    resp = client.get("/api/mobile/parts_orders")
    rows = resp.get_json()["items"]
    ret_row = next(r for r in rows if r.get("is_return"))
    assert ret_row["return_for_order_number"] == 97001
    assert ret_row["payment_status"] == "credit"
    assert ret_row["total_amount"] == -4.0

    # Деталка возврата (экран parts-order) — с полями возврата
    resp = client.get(f"/parts/api/orders/{data['return_id']}")
    order_detail = resp.get_json()["order"]
    assert order_detail["is_return"] is True
    assert order_detail["return_for_order_number"] == 97001
    assert order_detail["credit_total"] == 4.0
    assert order_detail["payment_summary"]["payment_status"] == "credit"
