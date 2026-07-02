"""CSRF: формы, JSON API, исключения (webhook, публичная авторизация по токену)."""
from tests.conftest import get_csrf_token


def test_post_without_token_blocked(client):
    resp = client.post("/login", data={"email": "a@b.c", "password": "x"}, follow_redirects=True)
    assert "session has expired" in resp.get_data(as_text=True)


def test_post_with_token_passes(client):
    token = get_csrf_token(client)
    resp = client.post(
        "/login",
        data={"email": "ghost@test.local", "password": "x", "csrf_token": token},
        follow_redirects=True,
    )
    body = resp.get_data(as_text=True)
    assert "session has expired" not in body
    assert "not found or inactive" in body


def test_json_api_without_header_rejected_as_json(client):
    resp = client.post("/work_orders/api/ai/polish-issue", json={"text": "hi"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "csrf_failed"


def test_json_api_with_header_passes_csrf(client):
    token = get_csrf_token(client)
    resp = client.post(
        "/work_orders/api/ai/polish-issue",
        json={"text": "hi"},
        headers={"X-CSRFToken": token},
    )
    data = resp.get_json(silent=True) or {}
    assert data.get("error") != "csrf_failed"


def test_stripe_webhook_exempt(client):
    resp = client.post("/billing/stripe/webhook", data="{}", content_type="application/json")
    data = resp.get_json(silent=True) or {}
    # Падает на проверке подписи/конфига, но не на CSRF.
    assert data.get("error") != "csrf_failed"


def test_public_authorization_exempt(client):
    resp = client.post("/authorize/nonexistent-token", data={"action": "approve"})
    assert resp.status_code == 404
    body = resp.get_data(as_text=True)
    assert "csrf_failed" not in body
    assert "session has expired" not in body
