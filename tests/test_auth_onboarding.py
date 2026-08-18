"""
Онбординг: подтверждение email при регистрации тенанта и приглашения
пользователей (invite → страница «задай пароль» → логин).
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import TEST_MASTER, TEST_MONGO_URI, get_csrf_token, login

PASSWORD = "password123"


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── Email verification ──────────────────────────────────────────────


def test_register_creates_unverified_owner_with_token(client, app, mongo):
    """Регистрация: owner создаётся с email_verified=False и verify-токеном.

    Письмо в тестах не уходит (нет RESEND_API_KEY) — регистрация от этого
    не падает (best-effort), а токен уже записан в документ.
    """
    form = {
        "csrf_token": get_csrf_token(client),
        "company_name": "Verify Garage Test",
        "company_address": "12 Main St, Austin, TX 78701",
        "company_phone": "+1 555 222 3344",
        "first_name": "Vera",
        "last_name": "Fied",
        "email": "owner@verify-garage-test.local",
        "password": PASSWORD,
        "password_confirm": PASSWORD,
        "website": "",
        "form_ts": str(int(time.time()) - 30),
    }
    resp = client.post("/tenant/register", data=form, environ_base={"REMOTE_ADDR": "10.7.7.7"})
    try:
        assert resp.status_code == 201, resp.get_data(as_text=True)

        master = mongo[TEST_MASTER]
        user = master.users.find_one({"email": "owner@verify-garage-test.local"})
        assert user is not None
        assert user["email_verified"] is False
        assert user.get("email_verify_token")

        # Подтверждение по ссылке.
        token = "test-verify-token"
        master.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"email_verify_token": _sha(token), "email_verify_token_created": time.time()}},
        )
        resp = client.get(f"/verify-email/{token}", follow_redirects=False)
        assert resp.status_code == 302

        user = master.users.find_one({"_id": user["_id"]})
        assert user["email_verified"] is True
        assert "email_verify_token" not in user

        # Повторный клик по той же ссылке — токен уже удалён.
        assert client.get(f"/verify-email/{token}").status_code == 302
    finally:
        master = mongo[TEST_MASTER]
        tenant = master.tenants.find_one({"slug": "verify-garage-test"})
        if tenant:
            master.users.delete_many({"tenant_id": tenant["_id"]})
            for shop in master.shops.find({"tenant_id": tenant["_id"]}):
                if shop.get("db_name"):
                    mongo.drop_database(shop["db_name"])
            master.shops.delete_many({"tenant_id": tenant["_id"]})
            if tenant.get("db_name"):
                mongo.drop_database(tenant["db_name"])
            master.tenants.delete_one({"_id": tenant["_id"]})


def test_expired_verify_token_rejected(client, seed, mongo):
    master = mongo[TEST_MASTER]
    token = "expired-verify-token"
    user_id = master.users.find_one({"email": "owner@test.local"})["_id"]
    master.users.update_one(
        {"_id": user_id},
        {"$set": {
            "email_verified": False,
            "email_verify_token": _sha(token),
            "email_verify_token_created": time.time() - 8 * 24 * 3600,
        }},
    )
    try:
        client.get(f"/verify-email/{token}")
        user = master.users.find_one({"_id": user_id})
        assert user["email_verified"] is False  # просроченная ссылка не подтверждает
    finally:
        master.users.update_one(
            {"_id": user_id},
            {"$unset": {"email_verified": "", "email_verify_token": "", "email_verify_token_created": ""}},
        )


def test_verify_banner_shown_until_confirmed(client, seed, mongo):
    """Полоска в layout: есть при email_verified=False, исчезает после подтверждения."""
    master = mongo[TEST_MASTER]
    user_id = master.users.find_one({"email": "owner@test.local"})["_id"]

    master.users.update_one({"_id": user_id}, {"$set": {"email_verified": False}})
    try:
        login(client)
        page = client.get("/dashboard").get_data(as_text=True)
        assert "Please confirm your email" in page
        assert "Resend email" in page

        master.users.update_one({"_id": user_id}, {"$set": {"email_verified": True}})
        page = client.get("/dashboard").get_data(as_text=True)
        assert "Please confirm your email" not in page
    finally:
        master.users.update_one({"_id": user_id}, {"$unset": {"email_verified": ""}})


def test_legacy_users_without_flag_see_no_banner(client, seed):
    """Старые пользователи (без поля email_verified) полоску не видят."""
    login(client)
    page = client.get("/dashboard").get_data(as_text=True)
    assert "Please confirm your email" not in page


# ── User invitations ────────────────────────────────────────────────

INVITED_EMAIL = "invited-user@test.local"


@pytest.fixture()
def cleanup_invited(mongo):
    yield
    mongo[TEST_MASTER].users.delete_many({"email": INVITED_EMAIL})


def _create_invited_user(client, seed):
    login(client)
    token = get_csrf_token(client)
    return client.post("/settings/users", data={
        "csrf_token": token,
        "first_name": "Ivy",
        "last_name": "Invited",
        "email": INVITED_EMAIL,
        "role": "manager",
        "is_active": "1",
        "send_invite": "1",
        "shop_ids": str(seed["shop_a"]["_id"]),
    }, follow_redirects=False)


def test_invite_flow_end_to_end(client, seed, mongo, cleanup_invited):
    # Роль manager нужна в tenant DB (conftest сеет только owner).
    tdb = mongo["roobico_test_tenant_a"]
    tdb.roles.update_one(
        {"key": "manager"}, {"$set": {"key": "manager", "name": "Manager", "permissions": []}}, upsert=True
    )

    resp = _create_invited_user(client, seed)
    assert resp.status_code == 302

    master = mongo[TEST_MASTER]
    user = master.users.find_one({"email": INVITED_EMAIL})
    assert user is not None
    assert user["invite_pending"] is True
    assert user["password_hash"] == ""
    assert user.get("invite_token")  # токен записан даже если письмо не ушло

    # Логин до установки пароля заблокирован с понятным сообщением.
    client.get("/logout")
    resp = login(client, email=INVITED_EMAIL, password="whatever123")
    page = client.get("/").get_data(as_text=True)
    assert "waiting for setup" in page

    # Страница приглашения по токену.
    token = "test-invite-token"
    master.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"invite_token": _sha(token), "invite_token_created": time.time()}},
    )
    page = client.get(f"/invite/{token}").get_data(as_text=True)
    assert INVITED_EMAIL in page

    # Установка пароля активирует аккаунт.
    csrf = get_csrf_token(client)
    resp = client.post(f"/invite/{token}", data={
        "csrf_token": csrf,
        "password": PASSWORD,
        "confirm_password": PASSWORD,
    })
    assert resp.status_code == 302

    user = master.users.find_one({"_id": user["_id"]})
    assert user["invite_pending"] is False
    assert user["email_verified"] is True  # пароль задан по ссылке из письма
    assert user["password_hash"]
    assert "invite_token" not in user

    # Теперь логин проходит.
    resp = login(client, email=INVITED_EMAIL, password=PASSWORD)
    assert resp.status_code == 302
    assert "/login" not in (resp.headers.get("Location") or "")
    client.get("/logout")


def test_invite_token_single_use_and_expiry(client, seed, mongo, cleanup_invited):
    master = mongo[TEST_MASTER]
    now = datetime.now(timezone.utc)
    user = {
        "_id": ObjectId(),
        "email": INVITED_EMAIL,
        "password_hash": "",
        "invite_pending": True,
        "is_active": True,
        "tenant_id": seed["tenant_a"]["_id"],
        "shop_ids": [str(seed["shop_a"]["_id"])],
        "role": "manager",
        "created_at": now,
    }
    master.users.insert_one(user)

    # Просроченный токен.
    token = "expired-invite-token"
    master.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"invite_token": _sha(token), "invite_token_created": time.time() - 8 * 24 * 3600}},
    )
    resp = client.get(f"/invite/{token}", follow_redirects=False)
    assert resp.status_code == 302  # редирект на логин с ошибкой
    assert master.users.find_one({"_id": user["_id"]})["invite_pending"] is True

    # Свежий токен, но приглашение уже принято — использовать нельзя.
    master.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"invite_pending": False, "invite_token_created": time.time()}},
    )
    csrf = get_csrf_token(client)
    client.post(f"/invite/{token}", data={
        "csrf_token": csrf, "password": PASSWORD, "confirm_password": PASSWORD,
    })
    assert master.users.find_one({"_id": user["_id"]})["password_hash"] == ""


def test_resend_invite_regenerates_token(client, seed, mongo, cleanup_invited):
    tdb = mongo["roobico_test_tenant_a"]
    tdb.roles.update_one(
        {"key": "manager"}, {"$set": {"key": "manager", "name": "Manager", "permissions": []}}, upsert=True
    )
    _create_invited_user(client, seed)

    master = mongo[TEST_MASTER]
    user = master.users.find_one({"email": INVITED_EMAIL})
    first_token = user["invite_token"]

    csrf = get_csrf_token(client)
    resp = client.post(f"/settings/users/{user['_id']}/resend-invite", data={"csrf_token": csrf})
    assert resp.status_code == 302

    user = master.users.find_one({"_id": user["_id"]})
    assert user["invite_token"] != first_token  # токен перевыпущен
    client.get("/logout")


def test_users_list_shows_invited_badge(client, seed, mongo, cleanup_invited):
    tdb = mongo["roobico_test_tenant_a"]
    tdb.roles.update_one(
        {"key": "manager"}, {"$set": {"key": "manager", "name": "Manager", "permissions": []}}, upsert=True
    )
    _create_invited_user(client, seed)

    page = client.get("/settings/users").get_data(as_text=True)
    assert "Invited" in page
    assert "Resend invite" in page
    client.get("/logout")
