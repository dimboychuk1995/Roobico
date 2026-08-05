"""Инвентаризация на живом складе: подсчёт со снапшотом «на момент ввода»,
движения после подсчёта не затираются поправкой, ревью помечает пересчёт."""
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
    loc = {"_id": ObjectId(), "shop_id": shop_id, "name": "ST shelf", "parent_id": None,
           "is_active": True, "created_at": now}
    db.parts_locations.insert_one(loc)

    # Легаси-парт: in_stock без строк по локациям — create_stocktake должен
    # материализовать хвост в primary-локацию.
    part = {
        "_id": ObjectId(),
        "shop_id": shop_id,
        "part_number": "ST-PART-1",
        "description": "Stocktake test part",
        "in_stock": 10,
        "average_cost": 2.5,
        "location_id": loc["_id"],
        "is_active": True,
        "created_at": now,
    }
    db.parts.insert_one(part)

    yield {"db": db, "shop_id": shop_id, "shop": seed["shop_a"], "loc": loc, "part": part}

    st_ids = [s["_id"] for s in db.stocktakes.find({"shop_id": shop_id}, {"_id": 1})]
    db.stocktakes.delete_many({"shop_id": shop_id})
    db.stocktake_items.delete_many({"stocktake_id": {"$in": st_ids}})
    db.parts.delete_one({"_id": part["_id"]})
    db.part_location_stock.delete_many({"part_id": part["_id"]})
    db.inventory_movements.delete_many({"part_id": part["_id"]})
    db.parts_locations.delete_one({"_id": loc["_id"]})


def _create_stocktake(client, env, location_id=""):
    token = get_csrf_token(client)
    resp = client.post(
        "/parts/stocktakes/create",
        data={"csrf_token": token, "name": "Test count", "location_id": location_id, "category_id": ""},
    )
    assert resp.status_code == 302
    st = env["db"].stocktakes.find_one({"shop_id": env["shop_id"]}, sort=[("created_at", -1)])
    assert st is not None
    return st


def _our_item(env, st):
    return env["db"].stocktake_items.find_one(
        {"stocktake_id": st["_id"], "part_id": env["part"]["_id"]}
    )


def test_stocktake_live_warehouse_flow(logged_in, env, app):
    """Ключевой сценарий: посчитали → пришла приёмка → завершили.
    Поправка применяется дельтой и не съедает приёмку."""
    st = _create_stocktake(logged_in, env, location_id=str(env["loc"]["_id"]))

    item = _our_item(env, st)
    assert item is not None, "item for the part must be generated"
    assert item["status"] == "pending"
    assert item["expected_initial"] == 10
    assert item["location_id"] == env["loc"]["_id"]

    # Посчитали фактически 8 (система ждёт 10 → variance = -2)
    token = get_csrf_token(logged_in)
    resp = logged_in.post(
        f"/parts/stocktakes/{st['_id']}/count",
        json={"item_id": str(item["_id"]), "counted_qty": 8},
        headers={"X-CSRFToken": token},
    )
    data = resp.get_json()
    assert data.get("ok"), data
    assert data["item"]["expected_at_count"] == 10
    assert data["item"]["variance"] == -2

    # Пока считали дальше — на склад приняли ещё 5 штук этого парта
    from app.blueprints.parts.services.stock import apply_stock_change
    with app.app_context():
        result = apply_stock_change(
            env["db"], env["shop_id"], env["part"]["_id"], 5, "receive",
            location_id=env["loc"]["_id"],
        )
        assert result["ok"] and result["stock_after"] == 15

    # Ревью: по позиции было движение после подсчёта → рекомендован пересчёт
    from app.blueprints.parts.services.stocktake import build_recount_flags
    with app.app_context():
        items = list(env["db"].stocktake_items.find({"stocktake_id": st["_id"]}))
        flags = build_recount_flags(env["db"], env["shop"], st, items)
    assert str(item["_id"]) in flags

    # Завершение: поправка -2 применяется ДЕЛЬТОЙ, приёмка +5 сохраняется
    resp = logged_in.post(
        f"/parts/stocktakes/{st['_id']}/complete",
        headers={"X-CSRFToken": token},
        json={},
    )
    data = resp.get_json()
    assert data.get("ok"), data

    part_after = env["db"].parts.find_one({"_id": env["part"]["_id"]})
    assert int(part_after["in_stock"]) == 13, "10 - 2 (variance) + 5 (receive) = 13"

    row = env["db"].part_location_stock.find_one(
        {"part_id": env["part"]["_id"], "location_id": env["loc"]["_id"]}
    )
    assert int(row["qty"]) == 13

    mv = env["db"].inventory_movements.find_one({"part_id": env["part"]["_id"], "type": "stocktake"})
    assert mv and mv["qty_delta"] == -2
    assert (mv.get("ref") or {}).get("id") == st["_id"]

    st_after = env["db"].stocktakes.find_one({"_id": st["_id"]})
    assert st_after["status"] == "completed"
    totals = st_after.get("totals") or {}
    assert totals.get("items_adjusted") == 1
    assert totals.get("shortage_value") == 5.0, "-2 шт * $2.50"


def test_stocktake_count_and_cancel_changes_nothing(logged_in, env):
    st = _create_stocktake(logged_in, env)
    item = _our_item(env, st)

    token = get_csrf_token(logged_in)
    resp = logged_in.post(
        f"/parts/stocktakes/{st['_id']}/count",
        json={"item_id": str(item["_id"]), "counted_qty": 3},
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json().get("ok")

    resp = logged_in.post(
        f"/parts/stocktakes/{st['_id']}/cancel",
        headers={"X-CSRFToken": token},
        json={},
    )
    assert resp.get_json().get("ok")

    part_after = env["db"].parts.find_one({"_id": env["part"]["_id"]})
    assert int(part_after["in_stock"]) == 10, "cancel must not touch stock"

    # Повторный count по отменённой сессии — отказ
    resp = logged_in.post(
        f"/parts/stocktakes/{st['_id']}/count",
        json={"item_id": str(item["_id"]), "counted_qty": 4},
        headers={"X-CSRFToken": token},
    )
    assert resp.status_code == 400


def test_complete_with_zero_uncounted(logged_in, env, app):
    """Полная инвентаризация: zero_uncounted=True обнуляет непосчитанные позиции
    дельтой от остатка на момент завершения (параллельная приёмка не съедается)."""
    st = _create_stocktake(logged_in, env)
    item = _our_item(env, st)
    assert item["status"] == "pending"

    # Пока «считали» — на склад пришло ещё 3 штуки
    from app.blueprints.parts.services.stock import apply_stock_change
    with app.app_context():
        apply_stock_change(
            env["db"], env["shop_id"], env["part"]["_id"], 3, "receive",
            location_id=env["loc"]["_id"],
        )

    token = get_csrf_token(logged_in)
    resp = logged_in.post(
        f"/parts/stocktakes/{st['_id']}/complete",
        json={"zero_uncounted": True},
        headers={"X-CSRFToken": token},
    )
    data = resp.get_json()
    assert data.get("ok"), data
    assert data["totals"]["items_zeroed"] >= 1
    assert data["totals"]["items_uncounted"] == 0

    part_after = env["db"].parts.find_one({"_id": env["part"]["_id"]})
    assert int(part_after["in_stock"]) == 0, "не посчитали = не нашли → 0 (13 на момент завершения списаны)"

    item_after = env["db"].stocktake_items.find_one({"_id": item["_id"]})
    assert item_after["status"] == "counted"
    assert item_after.get("auto_zeroed") is True
    assert item_after["counted_qty"] == 0
    assert item_after["expected_at_count"] == 13, "остаток на момент завершения: 10 + 3"
    assert item_after["variance"] == -13

    mv = env["db"].inventory_movements.find_one({"part_id": env["part"]["_id"], "type": "stocktake"})
    assert mv and mv["qty_delta"] == -13


def test_complete_without_flag_keeps_uncounted(logged_in, env):
    """Без флага (циклический пересчёт) непосчитанное не трогается."""
    st = _create_stocktake(logged_in, env)

    token = get_csrf_token(logged_in)
    resp = logged_in.post(
        f"/parts/stocktakes/{st['_id']}/complete",
        json={},
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json().get("ok")

    part_after = env["db"].parts.find_one({"_id": env["part"]["_id"]})
    assert int(part_after["in_stock"]) == 10

    item_after = env["db"].stocktake_items.find_one(
        {"stocktake_id": st["_id"], "part_id": env["part"]["_id"]}
    )
    assert item_after["status"] == "pending"
    assert not item_after.get("auto_zeroed")


def test_stocktake_pages_render(logged_in, env):
    """Смоук рендера: вкладка Stocktakes, экран подсчёта, настройки локаций."""
    st = _create_stocktake(logged_in, env)

    resp = logged_in.get("/parts?tab=stocktakes", follow_redirects=True)
    assert resp.status_code == 200
    assert b"ST-" in resp.data

    resp = logged_in.get(f"/parts/stocktakes/{st['_id']}")
    assert resp.status_code == 200
    assert b"ST-PART-1" in resp.data

    resp = logged_in.get("/settings/parts-settings")
    assert resp.status_code == 200
    assert b"Parts Locations" in resp.data


def test_stocktake_add_found_item(logged_in, env):
    st = _create_stocktake(logged_in, env)
    token = get_csrf_token(logged_in)

    # «Нашли на полке» парт, созданный уже после старта сессии
    now = datetime.now(timezone.utc)
    found = {
        "_id": ObjectId(),
        "shop_id": env["shop_id"],
        "part_number": "ST-FOUND-1",
        "in_stock": 0,
        "location_id": None,
        "is_active": True,
        "created_at": now,
    }
    env["db"].parts.insert_one(found)
    try:
        resp = logged_in.post(
            f"/parts/stocktakes/{st['_id']}/items/add",
            json={"part_id": str(found["_id"]), "location_id": str(env["loc"]["_id"])},
            headers={"X-CSRFToken": token},
        )
        data = resp.get_json()
        assert data.get("ok"), data

        item = env["db"].stocktake_items.find_one(
            {"stocktake_id": st["_id"], "part_id": found["_id"]}
        )
        assert item is not None
        assert item["location_id"] == env["loc"]["_id"]

        # Посчитали 4 найденных → после завершения остаток должен стать 4
        resp = logged_in.post(
            f"/parts/stocktakes/{st['_id']}/count",
            json={"item_id": str(item["_id"]), "counted_qty": 4},
            headers={"X-CSRFToken": token},
        )
        assert resp.get_json().get("ok")

        resp = logged_in.post(
            f"/parts/stocktakes/{st['_id']}/complete",
            headers={"X-CSRFToken": token},
            json={},
        )
        assert resp.get_json().get("ok")

        found_after = env["db"].parts.find_one({"_id": found["_id"]})
        assert int(found_after["in_stock"]) == 4
    finally:
        env["db"].parts.delete_one({"_id": found["_id"]})
        env["db"].part_location_stock.delete_many({"part_id": found["_id"]})
        env["db"].inventory_movements.delete_many({"part_id": found["_id"]})


def test_completed_stocktake_changes_view(logged_in, env):
    """Карточка «What changed»: было→стало тремя группами — изменившиеся,
    обнулённые (непосчитанные при zero_uncounted), найденные (было 0)."""
    from datetime import datetime, timezone

    db, shop_id, loc = env["db"], env["shop_id"], env["loc"]
    now = datetime.now(timezone.utc)
    part_zero = {
        "_id": ObjectId(), "shop_id": shop_id, "part_number": "ST-PART-2",
        "description": "Will be zeroed", "in_stock": 5, "average_cost": 1.0,
        "location_id": loc["_id"], "is_active": True, "created_at": now,
    }
    part_found = {
        "_id": ObjectId(), "shop_id": shop_id, "part_number": "ST-PART-3",
        "description": "Found on shelf", "in_stock": 0, "average_cost": 4.0,
        "location_id": loc["_id"], "is_active": True, "created_at": now,
    }
    db.parts.insert_many([part_zero, part_found])
    try:
        st = _create_stocktake(logged_in, env, location_id=str(loc["_id"]))
        items = {it["part_number"]: it for it in
                 db.stocktake_items.find({"stocktake_id": st["_id"]})}
        token = get_csrf_token(logged_in)

        # ST-PART-1: 10 → 6 (changed); ST-PART-3: 0 → 4 (found); ST-PART-2 не считаем
        for pn, qty in (("ST-PART-1", 6), ("ST-PART-3", 4)):
            resp = logged_in.post(
                f"/parts/stocktakes/{st['_id']}/count",
                json={"item_id": str(items[pn]["_id"]), "counted_qty": qty},
                headers={"X-CSRFToken": token},
            )
            assert resp.get_json().get("ok"), resp.get_data(as_text=True)

        resp = logged_in.post(
            f"/parts/stocktakes/{st['_id']}/complete",
            json={"zero_uncounted": True},
            headers={"X-CSRFToken": token},
        )
        assert resp.get_json().get("ok")

        # Остатки: 6 / 0 / 4
        assert db.parts.find_one({"_id": env["part"]["_id"]})["in_stock"] == 6
        assert db.parts.find_one({"_id": part_zero["_id"]})["in_stock"] == 0
        assert db.parts.find_one({"_id": part_found["_id"]})["in_stock"] == 4

        page = logged_in.get(f"/parts/stocktakes/{st['_id']}").get_data(as_text=True)
        assert "What changed" in page
        assert "Quantity changed" in page
        assert "Went to zero" in page
        assert "Found during count" in page
        # обнулённая строка несёт было=5, стало=0
        zero_item = db.stocktake_items.find_one(
            {"stocktake_id": st["_id"], "part_id": part_zero["_id"]})
        assert zero_item["expected_at_count"] == 5
        assert zero_item["counted_qty"] == 0
        assert zero_item["auto_zeroed"] is True
    finally:
        db.parts.delete_many({"_id": {"$in": [part_zero["_id"], part_found["_id"]]}})
        db.part_location_stock.delete_many(
            {"part_id": {"$in": [part_zero["_id"], part_found["_id"]]}})
        db.inventory_movements.delete_many(
            {"part_id": {"$in": [part_zero["_id"], part_found["_id"]]}})
