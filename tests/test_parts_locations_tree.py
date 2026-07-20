"""Дерево локаций запчастей: подлокации, лимит глубины, защита от циклов,
guard'ы удаления (подлокации / привязанные парты / остатки)."""
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
    created = {"db": db, "shop_id": seed["shop_a"]["_id"]}
    yield created
    db.parts_locations.delete_many({"shop_id": seed["shop_a"]["_id"]})
    db.part_location_stock.delete_many({"shop_id": seed["shop_a"]["_id"]})
    db.parts.delete_many({"part_number": {"$regex": "^LOCTREE-"}})


def _create_location(client, name, parent_id=""):
    token = get_csrf_token(client)
    return client.post(
        "/settings/parts-settings/locations/create",
        data={"csrf_token": token, "name": name, "parent_id": parent_id},
    )


def _find_loc(env, name):
    return env["db"].parts_locations.find_one({"shop_id": env["shop_id"], "name": name})


def test_create_sub_location(logged_in, env):
    assert _create_location(logged_in, "Warehouse").status_code == 302
    warehouse = _find_loc(env, "Warehouse")
    assert warehouse and warehouse.get("parent_id") is None

    assert _create_location(logged_in, "Rack 1", str(warehouse["_id"])).status_code == 302
    rack = _find_loc(env, "Rack 1")
    assert rack and rack["parent_id"] == warehouse["_id"]


def test_depth_limit(logged_in, env):
    parent_id = ""
    for name in ("L1", "L2", "L3", "L4"):
        assert _create_location(logged_in, name, parent_id).status_code == 302
        node = _find_loc(env, name)
        assert node is not None, f"{name} not created"
        parent_id = str(node["_id"])

    # Пятый уровень запрещён (MAX_LOCATION_DEPTH = 4)
    _create_location(logged_in, "L5", parent_id)
    assert _find_loc(env, "L5") is None


def test_move_under_own_descendant_rejected(logged_in, env):
    _create_location(logged_in, "Root")
    root = _find_loc(env, "Root")
    _create_location(logged_in, "Child", str(root["_id"]))
    child = _find_loc(env, "Child")

    token = get_csrf_token(logged_in)
    logged_in.post(
        f"/settings/parts-settings/locations/{root['_id']}/update",
        data={"csrf_token": token, "name": "Root", "parent_id": str(child["_id"])},
    )
    root_after = _find_loc(env, "Root")
    assert root_after.get("parent_id") is None, "cycle must be rejected"


def test_delete_guards(logged_in, env):
    _create_location(logged_in, "DelRoot")
    root = _find_loc(env, "DelRoot")
    _create_location(logged_in, "DelChild", str(root["_id"]))
    child = _find_loc(env, "DelChild")

    token = get_csrf_token(logged_in)

    # С подлокацией удалять нельзя
    logged_in.post(
        f"/settings/parts-settings/locations/{root['_id']}/delete",
        data={"csrf_token": token},
    )
    assert _find_loc(env, "DelRoot") is not None

    # Локация с остатком не удаляется
    env["db"].part_location_stock.insert_one({
        "shop_id": env["shop_id"],
        "part_id": ObjectId(),
        "location_id": child["_id"],
        "qty": 3,
        "created_at": datetime.now(timezone.utc),
    })
    logged_in.post(
        f"/settings/parts-settings/locations/{child['_id']}/delete",
        data={"csrf_token": token},
    )
    assert _find_loc(env, "DelChild") is not None

    # Обнулили остаток — теперь можно
    env["db"].part_location_stock.update_many(
        {"location_id": child["_id"]}, {"$set": {"qty": 0}}
    )
    logged_in.post(
        f"/settings/parts-settings/locations/{child['_id']}/delete",
        data={"csrf_token": token},
    )
    assert _find_loc(env, "DelChild") is None

    # Ребёнка больше нет — корень удаляется
    logged_in.post(
        f"/settings/parts-settings/locations/{root['_id']}/delete",
        data={"csrf_token": token},
    )
    assert _find_loc(env, "DelRoot") is None
