"""Settings -> Users: создание, реактивация и деактивация пользователей."""
from bson import ObjectId

from tests.conftest import TENANT_A_DB, get_csrf_token, login


def _create_user_form(email, **overrides):
    form = {
        "first_name": "New",
        "last_name": "Person",
        "email": email,
        "password": "password123",
        "password_confirm": "password123",
        "role": "owner",
        "is_active": "on",
    }
    form.update(overrides)
    return form


def _insert_user(master, tenant_id, email, **extra):
    doc = {
        "_id": ObjectId(),
        "first_name": "Test",
        "last_name": "Mechanik",
        "name": "Test Mechanik",
        "email": email,
        "role": "mechanic",
        "tenant_id": tenant_id,
        "shop_ids": [],
        "is_active": True,
    }
    doc.update(extra)
    master.users.insert_one(doc)
    return doc


def test_deactivate_user_with_objectid_tenant(app, client, seed):
    """Регрессия: tenant_id в сессии — строка, в документе — ObjectId.

    _id_variants дедуплицировал варианты по str(v), из-за чего ObjectId-вариант
    выпадал из $in и деактивация отвечала "User not found."
    """
    login(client)

    with app.app_context():
        from app.extensions import get_master_db
        master = get_master_db()
        target = _insert_user(master, seed["tenant_a"]["_id"], "deact-me@test.local")

    token = get_csrf_token(client)
    resp = client.post(
        f"/settings/users/{target['_id']}/deactivate",
        data={"csrf_token": token},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "User not found" not in resp.get_data(as_text=True)

    with app.app_context():
        from app.extensions import get_master_db
        doc = get_master_db().users.find_one({"_id": target["_id"]})
    assert doc["is_active"] is False


def test_deactivate_user_of_other_tenant_not_found(app, client, seed):
    """Пользователя чужого тенанта деактивировать нельзя."""
    login(client)

    with app.app_context():
        from app.extensions import get_master_db
        master = get_master_db()
        foreign = _insert_user(master, seed["tenant_b"]["_id"], "foreign@test.local")

    token = get_csrf_token(client)
    resp = client.post(
        f"/settings/users/{foreign['_id']}/deactivate",
        data={"csrf_token": token},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "User not found" in resp.get_data(as_text=True)

    with app.app_context():
        from app.extensions import get_master_db
        doc = get_master_db().users.find_one({"_id": foreign["_id"]})
    assert doc["is_active"] is True


def test_create_with_deactivated_email_reactivates(app, client, seed):
    """Email деактивированного пользователя нашего тенанта: вместо ошибки —
    реактивация с новыми данными из формы."""
    login(client)

    with app.app_context():
        from app.extensions import get_master_db
        master = get_master_db()
        old = _insert_user(
            master, seed["tenant_a"]["_id"], "rehire@test.local",
            is_active=False, name="Old Name", first_name="Old", last_name="Name",
        )

    token = get_csrf_token(client)
    resp = client.post(
        "/settings/users",
        data={"csrf_token": token, **_create_user_form("rehire@test.local")},
        follow_redirects=True,
    )
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "already exists" not in body
    assert "reactivated" in body

    with app.app_context():
        from app.extensions import get_master_db
        master = get_master_db()
        assert master.users.count_documents({"email": "rehire@test.local"}) == 1
        doc = master.users.find_one({"_id": old["_id"]})
    assert doc["is_active"] is True
    assert doc["first_name"] == "New"
    assert doc["last_name"] == "Person"
    assert doc["role"] == "owner"


def test_create_with_active_email_still_errors(app, client, seed):
    login(client)

    with app.app_context():
        from app.extensions import get_master_db
        _insert_user(get_master_db(), seed["tenant_a"]["_id"], "busy@test.local")

    token = get_csrf_token(client)
    resp = client.post(
        "/settings/users",
        data={"csrf_token": token, **_create_user_form("busy@test.local")},
        follow_redirects=True,
    )
    assert "already exists" in resp.get_data(as_text=True)


def test_create_with_foreign_deactivated_email_errors(app, client, seed):
    """Деактивированный пользователь ЧУЖОГО тенанта не реактивируется —
    email просто занят (изоляция тенантов)."""
    login(client)

    with app.app_context():
        from app.extensions import get_master_db
        master = get_master_db()
        foreign = _insert_user(
            master, seed["tenant_b"]["_id"], "foreign-rehire@test.local",
            is_active=False,
        )

    token = get_csrf_token(client)
    resp = client.post(
        "/settings/users",
        data={"csrf_token": token, **_create_user_form("foreign-rehire@test.local")},
        follow_redirects=True,
    )
    assert "already exists" in resp.get_data(as_text=True)

    with app.app_context():
        from app.extensions import get_master_db
        doc = get_master_db().users.find_one({"_id": foreign["_id"]})
    assert doc["is_active"] is False
    assert str(doc["tenant_id"]) == str(seed["tenant_b"]["_id"])


def test_cannot_deactivate_self(app, client, seed):
    login(client)

    token = get_csrf_token(client)
    resp = client.post(
        f"/settings/users/{seed['owner']['_id']}/deactivate",
        data={"csrf_token": token},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "cannot deactivate your own account" in resp.get_data(as_text=True)

    with app.app_context():
        from app.extensions import get_master_db
        doc = get_master_db().users.find_one({"_id": seed["owner"]["_id"]})
    assert doc["is_active"] is True
