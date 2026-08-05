"""
Сервис матчинга uAttend↔внутренние юзеры (app/utils/integrations/uattend_match):
кэш по отпечатку состава — хит при неизменном составе, автоматический
пересчёт при любом изменении/добавлении сотрудника.
"""
from __future__ import annotations

import pytest
from pymongo import MongoClient

import app.utils.integrations.uattend_match as uattend_match
from tests.conftest import SHOP_A_DB, TEST_MONGO_URI


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def shop_ctx(seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    shop_id = seed["shop_a"]["_id"]
    yield shop_db, shop_id
    shop_db.uattend_match_cache.delete_many({"shop_id": shop_id})


INTERNAL = [{"internal_id": "i1", "name": "John Doe", "email": "john@test.local"}]
UATTEND = [{"uattend_user_id": 5, "name": "John Doe", "email": ""}]


def _counting_matcher(monkeypatch):
    calls = {"n": 0}

    def fake_match(internal, uattend):
        calls["n"] += 1
        return {5: {"internal_id": "i1", "internal_name": "John Doe",
                    "internal_email": "john@test.local", "confidence": 1.0}}

    monkeypatch.setattr(uattend_match, "match_employees", fake_match)
    return calls


def test_same_roster_hits_cache(shop_ctx, monkeypatch):
    shop_db, shop_id = shop_ctx
    calls = _counting_matcher(monkeypatch)

    first = uattend_match.get_match_map(shop_db, shop_id, INTERNAL, UATTEND)
    assert calls["n"] == 1
    assert first[5]["internal_id"] == "i1"

    # Тот же состав — из кэша, матчер не зовётся; ключи int после Mongo
    second = uattend_match.get_match_map(shop_db, shop_id, INTERNAL, UATTEND)
    assert calls["n"] == 1
    assert second[5]["internal_id"] == "i1"
    assert all(isinstance(k, int) for k in second)


def test_roster_change_triggers_recompute(shop_ctx, monkeypatch):
    shop_db, shop_id = shop_ctx
    calls = _counting_matcher(monkeypatch)

    uattend_match.get_match_map(shop_db, shop_id, INTERNAL, UATTEND)
    assert calls["n"] == 1

    # Добавили uAttend-сотрудника — новый ключ, пересчёт
    grown = UATTEND + [{"uattend_user_id": 6, "name": "New Guy", "email": ""}]
    uattend_match.get_match_map(shop_db, shop_id, INTERNAL, grown)
    assert calls["n"] == 2

    # Переименовали внутреннего юзера — тоже пересчёт
    renamed = [{"internal_id": "i1", "name": "Johnny Doe", "email": "john@test.local"}]
    uattend_match.get_match_map(shop_db, shop_id, renamed, grown)
    assert calls["n"] == 3

    # Возврат к первому составу — его документ ещё в кэше, без пересчёта
    uattend_match.get_match_map(shop_db, shop_id, INTERNAL, UATTEND)
    assert calls["n"] == 3


def test_empty_rosters_do_not_compute_or_write(shop_ctx, monkeypatch):
    shop_db, shop_id = shop_ctx
    calls = _counting_matcher(monkeypatch)

    assert uattend_match.get_match_map(shop_db, shop_id, [], UATTEND) == {}
    assert uattend_match.get_match_map(shop_db, shop_id, INTERNAL, []) == {}
    assert calls["n"] == 0
    assert shop_db.uattend_match_cache.count_documents({"shop_id": shop_id}) == 0


def test_deterministic_match_without_ai(shop_ctx, monkeypatch):
    """Без OPENAI_API_KEY одинаковые имена склеиваются детерминированно —
    ровно случай «Yurii Mykytiuk в обеих системах»."""
    shop_db, shop_id = shop_ctx
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    internal = [
        {"internal_id": "a", "name": "Yurii Mykytiuk", "email": "yuriy@x.com"},
        {"internal_id": "b", "name": "Sergey Mechanic", "email": "sergey@x.com"},
    ]
    uattend = [
        {"uattend_user_id": 545851, "name": "Yurii Mykytiuk", "email": ""},
        {"uattend_user_id": 122532, "name": "Serghei Cosciug", "email": ""},
    ]
    out = uattend_match.get_match_map(shop_db, shop_id, internal, uattend)
    assert out[545851]["internal_id"] == "a"
    # Разные фамилии без email детерминированно не матчатся
    assert 122532 not in out
