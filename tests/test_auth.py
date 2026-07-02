"""Логин, rate limiting, сброс пароля (хэшированные токены)."""
import hashlib
import re
import time

from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, get_csrf_token, login


def test_login_success_sets_session(client, seed):
    resp = login(client)
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert sess.get("user_id") == str(seed["owner"]["_id"])
        assert sess.get("tenant_id") == str(seed["tenant_a"]["_id"])
        assert sess.get("shop_id") == str(seed["shop_a"]["_id"])


def test_login_wrong_password_shows_error(client):
    resp = login(client, password="wrong-password")
    assert resp.status_code == 302
    follow = client.get("/")
    assert "Wrong password" in follow.get_data(as_text=True)
    with client.session_transaction() as sess:
        assert not sess.get("user_id")


def test_login_unknown_user_shows_error(client):
    resp = login(client, email="ghost@test.local")
    assert resp.status_code == 302
    follow = client.get("/")
    assert "not found or inactive" in follow.get_data(as_text=True)


def test_login_rate_limited_after_10_attempts(client):
    ip = "10.9.9.9"
    for _ in range(10):
        login(client, password="wrong", remote_addr=ip)
    login(client, password="wrong", remote_addr=ip)
    follow = client.get("/")
    assert "Too many login attempts" in follow.get_data(as_text=True)


def test_login_rate_limit_is_per_ip(client):
    for _ in range(11):
        login(client, password="wrong", remote_addr="10.1.1.1")
    # Другой IP не задет — обычная ошибка пароля, не лимит.
    login(client, password="wrong", remote_addr="10.2.2.2")
    follow = client.get("/")
    body = follow.get_data(as_text=True)
    assert "Wrong password" in body


def test_rate_limit_window_expires(app):
    from app.utils.rate_limit import hit_rate_limit
    with app.app_context():
        results = [hit_rate_limit("t", "1.1.1.1", max_attempts=2, window_seconds=1) for _ in range(3)]
        assert results == [False, False, True]
        time.sleep(1.1)
        assert hit_rate_limit("t", "1.1.1.1", max_attempts=2, window_seconds=1) is False


def test_forgot_password_stores_hashed_token(client, app, seed, monkeypatch):
    captured = {}

    def fake_send_email(**kwargs):
        captured["html"] = kwargs.get("html_body", "")

    import app.blueprints.auth.routes as auth_routes
    monkeypatch.setattr(auth_routes, "send_email", fake_send_email)

    token = get_csrf_token(client)
    resp = client.post("/forgot-password", data={"email": OWNER_EMAIL, "csrf_token": token})
    assert resp.status_code == 302

    match = re.search(r"/reset-password/([A-Za-z0-9_\-]+)", captured["html"])
    assert match, "reset link not found in email"
    raw_token = match.group(1)

    from app.extensions import get_master_db
    with app.app_context():
        user = get_master_db().users.find_one({"email": OWNER_EMAIL})
    stored = user["reset_token"]
    # В базе — sha256-хэш, не сам токен.
    assert stored != raw_token
    assert stored == hashlib.sha256(raw_token.encode()).hexdigest()

    # Страница сброса открывается по сырому токену (поиск по хэшу).
    page = client.get(f"/reset-password/{raw_token}")
    assert page.status_code == 200

    # Короткий пароль отклоняется.
    csrf = get_csrf_token(client)
    resp = client.post(
        f"/reset-password/{raw_token}",
        data={"password": "short", "confirm_password": "short", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert "at least 8 characters" in resp.get_data(as_text=True)

    # Валидный пароль проходит, токен очищается, новый пароль работает.
    csrf = get_csrf_token(client)
    resp = client.post(
        f"/reset-password/{raw_token}",
        data={"password": "new-password-42", "confirm_password": "new-password-42", "csrf_token": csrf},
    )
    assert resp.status_code == 302
    with app.app_context():
        user = get_master_db().users.find_one({"email": OWNER_EMAIL})
    assert "reset_token" not in user

    assert login(client, password="new-password-42").status_code == 302
    with client.session_transaction() as sess:
        assert sess.get("user_id")

    # Возвращаем старый пароль, чтобы не влиять на другие тесты.
    from werkzeug.security import generate_password_hash
    with app.app_context():
        get_master_db().users.update_one(
            {"email": OWNER_EMAIL},
            {"$set": {"password_hash": generate_password_hash(OWNER_PASSWORD)}},
        )
