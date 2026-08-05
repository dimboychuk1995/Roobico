"""
Настройки таблиц (flex tables): сохранение per-user per-table, инжекция
в страницы, валидация, изоляция между юзерами.
"""
from __future__ import annotations

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import TEST_MONGO_URI, get_csrf_token, login

MASTER_DB = "roobico_test_master"


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture(autouse=True)
def _cleanup(mongo):
    yield
    mongo[MASTER_DB].user_table_prefs.delete_many({})


def _post_prefs(client, key, prefs):
    token = get_csrf_token(client)
    return client.post("/api/table-prefs", json={"key": key, "prefs": prefs},
                       headers={"X-CSRFToken": token})


def test_save_load_and_injection(client, seed, mongo):
    login(client)
    prefs = {
        "hidden": ["phone", "email"],
        "widths": {"company_name": 240},
        "sort": {"col": "company_name", "dir": "desc"},
    }
    resp = _post_prefs(client, "_vendors|abc123", prefs)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["ok"] is True

    doc = mongo[MASTER_DB].user_table_prefs.find_one(
        {"user_id": seed["owner"]["_id"]})
    assert doc is not None
    assert doc["tables"]["_vendors|abc123"]["hidden"] == ["phone", "email"]
    assert doc["tables"]["_vendors|abc123"]["widths"]["company_name"] == 240

    # Настройки инжектятся на страницы приложения
    page = client.get("/vendors/").get_data(as_text=True)
    assert "window.__tablePrefs" in page
    assert "_vendors|abc123" in page


def test_clear_prefs_with_null(client, seed, mongo):
    login(client)
    _post_prefs(client, "_parts|k1", {"hidden": ["reference"]})
    resp = _post_prefs(client, "_parts|k1", None)
    assert resp.status_code == 200
    doc = mongo[MASTER_DB].user_table_prefs.find_one(
        {"user_id": seed["owner"]["_id"]})
    assert "_parts|k1" not in (doc or {}).get("tables", {})


def test_invalid_key_and_prefs_rejected(client, seed):
    login(client)
    # Точки в ключе (инъекция в путь Mongo) — нельзя
    assert _post_prefs(client, "a.b", {"hidden": []}).status_code == 400
    assert _post_prefs(client, "", {"hidden": []}).status_code == 400
    assert _post_prefs(client, "x" * 300, {}).status_code == 400
    # Точка/доллар в ключе ширины — нельзя
    assert _post_prefs(client, "_ok|1", {"widths": {"a.b": 100}}).status_code == 400
    assert _post_prefs(client, "_ok|1", {"widths": {"$set": 100}}).status_code == 400
    # Кривое направление сортировки
    assert _post_prefs(client, "_ok|1", {"sort": {"col": "x", "dir": "up"}}).status_code == 400
    # Ширина клампится, а не отклоняется
    resp = _post_prefs(client, "_ok|1", {"widths": {"col": 99999}})
    assert resp.status_code == 200


def test_width_clamped(client, seed, mongo):
    login(client)
    _post_prefs(client, "_clamp|1", {"widths": {"col": 99999, "col2": 1}})
    doc = mongo[MASTER_DB].user_table_prefs.find_one(
        {"user_id": seed["owner"]["_id"]})
    widths = doc["tables"]["_clamp|1"]["widths"]
    assert widths["col"] == 2000
    assert widths["col2"] == 40


def test_prefs_isolated_between_users(client, seed, mongo):
    """Чужие настройки не инжектятся: страница содержит только свои ключи."""
    other_user_id = ObjectId()
    mongo[MASTER_DB].user_table_prefs.insert_one({
        "user_id": other_user_id,
        "tables": {"_secret_table|zzz": {"hidden": ["x"]}},
    })
    login(client)
    _post_prefs(client, "_mine|1", {"hidden": ["y"]})
    page = client.get("/vendors/").get_data(as_text=True)
    assert "_mine|1" in page
    assert "_secret_table|zzz" not in page


def test_requires_login(client):
    token = get_csrf_token(client)  # токен доступен и на странице логина
    resp = client.post("/api/table-prefs", json={"key": "_x|1", "prefs": {}},
                       headers={"X-CSRFToken": token})
    assert resp.status_code in (302, 401)
