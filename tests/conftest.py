"""
Тестовое окружение Roobico.

КРИТИЧНО: env-переменные выставляются ДО импорта пакета app — app/config.py
читает окружение при импорте (load_dotenv с override=False наши значения не
перетирает). Иначе тесты подключились бы к базе из .env, которая может быть
продовой.
"""
from __future__ import annotations

import os

TEST_MONGO_URI = os.environ.get("TEST_MONGO_URI", "mongodb://127.0.0.1:27017")
os.environ["MONGO_URI"] = TEST_MONGO_URI
os.environ["MASTER_DB_NAME"] = "roobico_test_master"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["SENTRY_DSN"] = ""
os.environ["ENFORCE_HOST_SPLIT"] = "false"

import re
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

TEST_MASTER = "roobico_test_master"
TENANT_A_DB = "roobico_test_tenant_a"
SHOP_A_DB = "roobico_test_shop_a"
SHOP_B_DB = "roobico_test_shop_b"
ALL_TEST_DBS = (TEST_MASTER, TENANT_A_DB, SHOP_A_DB, SHOP_B_DB)

OWNER_EMAIL = "owner@test.local"
OWNER_PASSWORD = "password123"


def _drop_test_dbs():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    for name in ALL_TEST_DBS:
        client.drop_database(name)
    client.close()


@pytest.fixture(scope="session")
def app():
    # Чистый старт: create_app создаёт индексы по магазинам из master,
    # остатки прошлого прогона только мешают.
    _drop_test_dbs()

    from app import create_app
    application = create_app()

    # Предохранитель: тесты пишут и дропают базы — только localhost.
    assert application.config["MONGO_URI"].startswith("mongodb://127.0.0.1"), (
        "Tests must run against local Mongo, got: " + application.config["MONGO_URI"]
    )
    assert application.config["MASTER_DB_NAME"] == TEST_MASTER

    yield application

    _drop_test_dbs()


@pytest.fixture(scope="session")
def seed(app):
    """Тенант A (магазин A + владелец), тенант B (магазин B) — для изоляции."""
    from app.extensions import get_master_db, get_mongo_client

    with app.app_context():
        master = get_master_db()
        client = get_mongo_client()

        now = datetime.now(timezone.utc)
        tenant_a = {
            "_id": ObjectId(), "slug": "test-a", "name": "Test Tenant A",
            "db_name": TENANT_A_DB, "status": "active", "created_at": now,
        }
        tenant_b = {
            "_id": ObjectId(), "slug": "test-b", "name": "Test Tenant B",
            "db_name": "roobico_test_tenant_b", "status": "active", "created_at": now,
        }
        master.tenants.insert_many([tenant_a, tenant_b])

        shop_a = {
            "_id": ObjectId(), "tenant_id": tenant_a["_id"], "name": "Shop A",
            "db_name": SHOP_A_DB, "is_active": True, "created_at": now,
        }
        shop_b = {
            "_id": ObjectId(), "tenant_id": tenant_b["_id"], "name": "Shop B",
            "db_name": SHOP_B_DB, "is_active": True, "created_at": now,
        }
        master.shops.insert_many([shop_a, shop_b])

        owner = {
            "_id": ObjectId(), "email": OWNER_EMAIL,
            "password_hash": generate_password_hash(OWNER_PASSWORD),
            "is_active": True, "tenant_id": tenant_a["_id"],
            "shop_ids": [str(shop_a["_id"])], "role": "owner", "created_at": now,
        }
        master.users.insert_one(owner)

        shop_db_a = client[SHOP_A_DB]
        rate = {
            "_id": ObjectId(), "shop_id": shop_a["_id"], "code": "standard",
            "name": "Standard", "hourly_rate": 100.0, "is_active": True,
        }
        shop_db_a.labor_rates.insert_one(rate)

        # Роль owner в tenant-базе: permission_required вычисляет права через
        # tenant_db.roles, и без документа роли у пользователя нет прав.
        client[TENANT_A_DB].roles.insert_one(
            {"key": "owner", "name": "Owner", "permissions": []}
        )

        return {
            "tenant_a": tenant_a, "tenant_b": tenant_b,
            "shop_a": shop_a, "shop_b": shop_b,
            "owner": owner, "labor_rate": rate,
        }


@pytest.fixture()
def client(app, seed):
    return app.test_client()


@pytest.fixture(autouse=True)
def _clear_rate_limits(app):
    """Login-тесты набивают счётчики; чистим, чтобы тесты не влияли друг на друга."""
    from app.extensions import get_master_db
    with app.app_context():
        get_master_db().rate_limits.delete_many({})
    yield


def get_csrf_token(client) -> str:
    """CSRF-токен из meta-тега базового layout (страница логина или,
    для залогиненного клиента, целевая страница после редиректа)."""
    resp = client.get("/", follow_redirects=True)
    match = re.search(r'name="csrf-token" content="([^"]+)"', resp.get_data(as_text=True))
    assert match, "csrf-token meta not found"
    return match.group(1)


def login(client, email=OWNER_EMAIL, password=OWNER_PASSWORD, remote_addr="127.0.0.1"):
    token = get_csrf_token(client)
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": token},
        environ_base={"REMOTE_ADDR": remote_addr},
    )
