"""Mobile API /api/mobile/parts: единица измерения, минимальный остаток,
фильтр stock_level=below|near и счётчики для чипов фильтра."""
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, SHOP_A_DB

PREFIX = "MSTK-"


def _mobile_login(client, email=OWNER_EMAIL, password=OWNER_PASSWORD):
    return client.post(
        "/api/mobile/login",
        json={"email": email, "password": password},
        environ_base={"REMOTE_ADDR": "127.0.0.6"},
    )


def _by_number(data):
    assert data["ok"] is True
    return {x["part_number"]: x for x in data["items"]}


@pytest.fixture()
def min_stock_parts(app, seed):
    """Набор партов магазина A во всех состояниях относительно минимума."""
    from app.extensions import get_mongo_client

    with app.app_context():
        db = get_mongo_client()[SHOP_A_DB]
        now = datetime.now(timezone.utc)
        shop_id = seed["shop_a"]["_id"]

        def part(suffix, **extra):
            doc = {
                "_id": ObjectId(), "shop_id": shop_id, "part_number": PREFIX + suffix,
                "description": f"Min stock test {suffix}", "average_cost": 10.0,
                "selling_price": 15.0, "is_active": True, "created_at": now,
            }
            doc.update(extra)
            return doc

        docs = [
            # ниже минимума при нулевом остатке — в обычном списке (in_stock > 0) не виден
            part("BELOW0", in_stock=0, min_stock_enabled=True, min_stock=5),
            part("BELOW2", in_stock=2, min_stock_enabled=True, min_stock=5),
            # ровно на минимуме и на верхней границе near (min 5 → near до 6)
            part("NEAR", in_stock=5, min_stock_enabled=True, min_stock=5),
            part("NEAR6", in_stock=6, min_stock_enabled=True, min_stock=5),
            part("OK", in_stock=20, min_stock_enabled=True, min_stock=5),
            # склад не ведётся — минимум не действует, состояния нет
            part("NOTRACK", in_stock=1, do_not_track_inventory=True,
                 min_stock_enabled=True, min_stock=5),
            part("GAL", in_stock=12, unit_of_measure="gallon"),
            # документ без новых полей: each, минимум не задан
            part("LEGACY", in_stock=3),
        ]
        db.parts.insert_many(docs)
        yield {"db": db, "parts": {d["part_number"]: d for d in docs}}
        db.parts.delete_many({"_id": {"$in": [d["_id"] for d in docs]}})


def test_mobile_parts_items_carry_unit_and_min_stock_fields(client, min_stock_parts):
    _mobile_login(client)
    rows = _by_number(client.get(f"/api/mobile/parts?q={PREFIX}").get_json())

    gal = rows[PREFIX + "GAL"]
    assert (gal["unit"], gal["unit_short"], gal["unit_label"]) == ("gallon", "gal", "Gallon")
    assert gal["min_stock_enabled"] is False
    assert gal["min_stock"] == 0
    assert gal["stock_state"] is None

    legacy = rows[PREFIX + "LEGACY"]
    assert (legacy["unit"], legacy["unit_short"], legacy["unit_label"]) == ("each", "ea", "Each")
    assert legacy["min_stock_enabled"] is False
    assert legacy["min_stock"] == 0
    assert legacy["stock_state"] is None

    below = rows[PREFIX + "BELOW2"]
    assert below["min_stock_enabled"] is True
    assert below["min_stock"] == 5
    assert below["stock_state"] == "below"
    assert rows[PREFIX + "NEAR"]["stock_state"] == "near"
    assert rows[PREFIX + "NEAR6"]["stock_state"] == "near"
    assert rows[PREFIX + "OK"]["stock_state"] is None
    # без фильтра — как вкладка Stock: нулевой остаток не показываем
    assert PREFIX + "BELOW0" not in rows


def test_mobile_parts_stock_level_below(client, min_stock_parts):
    _mobile_login(client)
    data = client.get(f"/api/mobile/parts?q={PREFIX}&stock_level=below").get_json()
    rows = _by_number(data)
    assert set(rows) == {PREFIX + "BELOW0", PREFIX + "BELOW2"}
    assert all(x["stock_state"] == "below" for x in rows.values())
    assert rows[PREFIX + "BELOW0"]["in_stock"] == 0


def test_mobile_parts_stock_level_near_includes_below_and_at_minimum(client, min_stock_parts):
    _mobile_login(client)
    data = client.get(f"/api/mobile/parts?q={PREFIX}&stock_level=near").get_json()
    assert set(_by_number(data)) == {
        PREFIX + "BELOW0", PREFIX + "BELOW2", PREFIX + "NEAR", PREFIX + "NEAR6",
    }


def test_mobile_parts_unknown_stock_level_is_ignored(client, min_stock_parts):
    _mobile_login(client)
    rows = _by_number(client.get(f"/api/mobile/parts?q={PREFIX}&stock_level=bogus").get_json())
    assert PREFIX + "BELOW0" not in rows
    assert PREFIX + "OK" in rows


def test_mobile_parts_filter_respects_search_and_pagination(client, min_stock_parts):
    _mobile_login(client)
    url = f"/api/mobile/parts?q={PREFIX}BELOW&stock_level=near&per_page=1"
    data = client.get(url + "&page=1").get_json()
    assert data["pagination"]["total"] == 2
    assert data["pagination"]["pages"] == 2
    assert data["pagination"]["has_next"] is True
    assert len(data["items"]) == 1
    page2 = client.get(url + "&page=2").get_json()
    assert {data["items"][0]["part_number"], page2["items"][0]["part_number"]} == {
        PREFIX + "BELOW0", PREFIX + "BELOW2",
    }


def test_mobile_parts_counts(app, client, seed, min_stock_parts):
    from app.blueprints.parts.services.min_stock import stock_level_filter

    _mobile_login(client)
    data = client.get(f"/api/mobile/parts?q={PREFIX}OK").get_json()
    counts = data["counts"]
    assert set(counts) == {"below", "near"}
    # Счётчики — по всем активным партам магазина, а не по поисковой выдаче;
    # near включает below (как фильтр), поэтому не меньше.
    assert counts["below"] >= 2
    assert counts["near"] >= 4
    assert counts["near"] >= counts["below"]
    with app.app_context():
        active = {"shop_id": seed["shop_a"]["_id"], "is_active": True}
        for level in ("below", "near"):
            expected = min_stock_parts["db"].parts.count_documents(
                {"$and": [active, stock_level_filter(level)]}
            )
            assert counts[level] == expected


def test_mobile_parts_do_not_track_never_has_state(client, min_stock_parts):
    _mobile_login(client)
    row = _by_number(client.get(f"/api/mobile/parts?q={PREFIX}NOTRACK").get_json())[PREFIX + "NOTRACK"]
    assert row["stock_state"] is None
    assert row["min_stock_enabled"] is False
    assert row["min_stock"] == 0
    for level in ("below", "near"):
        data = client.get(f"/api/mobile/parts?q={PREFIX}&stock_level={level}").get_json()
        assert PREFIX + "NOTRACK" not in _by_number(data)


def test_mobile_parts_costs_hidden_without_permission_but_stock_state_kept(app, client, seed, min_stock_parts):
    """parts.view без parts.view_costs: цен в ответе нет, состояние запаса и единица — есть."""
    from werkzeug.security import generate_password_hash

    with app.app_context():
        from app.extensions import get_master_db, get_mongo_client

        get_mongo_client()[seed["tenant_a"]["db_name"]].roles.update_one(
            {"key": "parts_viewer_test"},
            {"$set": {"name": "Parts Viewer", "permissions": ["parts.view"]}},
            upsert=True,
        )
        user_id = get_master_db().users.insert_one({
            "_id": ObjectId(), "email": "parts-viewer@test.local",
            "password_hash": generate_password_hash("password123"),
            "is_active": True, "tenant_id": seed["tenant_a"]["_id"],
            "shop_ids": [str(seed["shop_a"]["_id"])], "role": "parts_viewer_test",
        }).inserted_id
    try:
        resp = _mobile_login(client, email="parts-viewer@test.local", password="password123")
        assert resp.status_code == 200
        row = _by_number(client.get(f"/api/mobile/parts?q={PREFIX}BELOW2").get_json())[PREFIX + "BELOW2"]
        assert "average_cost" not in row
        assert "selling_price" not in row
        assert row["stock_state"] == "below"
        assert row["unit_short"] == "ea"
    finally:
        with app.app_context():
            from app.extensions import get_master_db, get_mongo_client

            get_master_db().users.delete_one({"_id": user_id})
            get_mongo_client()[seed["tenant_a"]["db_name"]].roles.delete_one({"key": "parts_viewer_test"})
