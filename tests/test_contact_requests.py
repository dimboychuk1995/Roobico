"""Форма «Contact us» на странице логина + её обработка в админке.

Публичный POST /contact: валидация, антибот (honeypot / form_ts), rate
limit, запись в master.contact_requests. Админка: список, смена статуса,
бейдж новых заявок, запись в admin_audit.
"""
from __future__ import annotations

import time

import pytest
from bson import ObjectId
from werkzeug.security import generate_password_hash

from tests.conftest import get_csrf_token


def _form(client, **overrides):
    data = {
        "csrf_token": get_csrf_token(client),
        "name": "Jane Fleet",
        "email": "jane@fleet.example",
        "phone": "+1 555 010 0000",
        "company": "Fleet Co",
        "topic": "demo",
        "message": "We run 40 trucks and would like a demo of Roobico.",
        "website": "",  # honeypot — человек его не заполняет
        "form_ts": str(int(time.time()) - 30),
    }
    data.update(overrides)
    return data


def _post(client, data, ip="203.0.113.10"):
    return client.post("/contact", data=data, environ_base={"REMOTE_ADDR": ip})


@pytest.fixture(autouse=True)
def _clean(app):
    from app.extensions import get_master_db
    with app.app_context():
        get_master_db().contact_requests.delete_many({})
    yield
    with app.app_context():
        get_master_db().contact_requests.delete_many({})


def test_login_page_has_contact_tab_and_phone(client):
    body = client.get("/").get_data(as_text=True)
    assert "Contact us" in body
    assert "(773) 331-2795" in body
    assert 'href="tel:+17733312795"' in body
    assert 'id="contactForm"' in body
    assert 'action="/contact"' in body


def test_submit_stores_request(client, app):
    resp = _post(client, _form(client))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["ok"] is True

    from app.extensions import get_master_db
    with app.app_context():
        doc = get_master_db().contact_requests.find_one({"email": "jane@fleet.example"})
    assert doc is not None
    assert doc["status"] == "new"
    assert doc["name"] == "Jane Fleet"
    assert doc["company"] == "Fleet Co"
    assert doc["topic"] == "demo"
    assert doc["source"] == "login_page"
    assert doc["ip"] == "203.0.113.10"
    assert doc["created_at"] is not None


def test_submit_validation_errors(client):
    resp = _post(client, _form(client, name="J", email="not-an-email", message="short"))
    assert resp.status_code == 400
    errors = " ".join(resp.get_json()["errors"])
    assert "name" in errors.lower()
    assert "email" in errors.lower()
    assert "10 characters" in errors


def test_submit_rejects_honeypot(client, app):
    resp = _post(client, _form(client, website="http://spam.example"))
    assert resp.status_code == 400
    from app.extensions import get_master_db
    with app.app_context():
        assert get_master_db().contact_requests.count_documents({}) == 0


def test_submit_rejects_instant_or_missing_form_ts(client):
    assert _post(client, _form(client, form_ts=str(int(time.time())))).status_code == 400
    form = _form(client)
    form.pop("form_ts")
    assert _post(client, form).status_code == 400


def test_submit_rate_limited_per_ip(client):
    ip = "198.51.100.7"
    for _ in range(5):
        assert _post(client, _form(client), ip=ip).status_code == 200
    resp = _post(client, _form(client), ip=ip)
    assert resp.status_code == 429
    # Другой IP не задет.
    assert _post(client, _form(client), ip="198.51.100.8").status_code == 200


def test_submit_requires_csrf(client):
    form = _form(client)
    form.pop("csrf_token")
    resp = _post(client, form)
    # CSRFError → глобальный обработчик: JSON 400 для /api/, иначе redirect.
    assert resp.status_code in (400, 302)


# ── Admin panel ──────────────────────────────────────────────────────


@pytest.fixture()
def admin_client(app, client):
    from app.extensions import get_master_db
    with app.app_context():
        master = get_master_db()
        master.admin_users.delete_many({"email": "root@roobico-test.local"})
        master.admin_users.insert_one({
            "_id": ObjectId(), "email": "root@roobico-test.local",
            "password_hash": generate_password_hash("admin-pass-123"), "is_active": True,
        })
    token = get_csrf_token(client)
    resp = client.post("/admin/login", data={
        "email": "root@roobico-test.local", "password": "admin-pass-123", "csrf_token": token,
    })
    assert resp.status_code == 302
    return client


def _seed_request(app, **overrides):
    from app.blueprints.contact import services
    from app.extensions import get_master_db
    doc = {
        "name": "Bob Shop", "email": "bob@shop.example", "phone": "", "company": "",
        "topic": "support", "message": "Cannot log in after changing my email.",
        "status": "new", "source": "login_page", "admin_note": "", "ip": "1.2.3.4",
        "user_agent": "pytest", "created_at": services.utcnow(), "updated_at": services.utcnow(),
    }
    doc.update(overrides)
    with app.app_context():
        return get_master_db().contact_requests.insert_one(doc).inserted_id


def test_admin_list_requires_login(client):
    resp = client.get("/admin/contact-requests")
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_admin_list_shows_requests_and_badge(admin_client, app):
    _seed_request(app)
    _seed_request(app, name="Closed One", status="closed")

    body = admin_client.get("/admin/contact-requests").get_data(as_text=True)
    assert "Bob Shop" in body
    assert "Cannot log in after changing my email." in body
    assert "Closed One" not in body  # фильтр по умолчанию — open

    body_all = admin_client.get("/admin/contact-requests?status=all").get_data(as_text=True)
    assert "Closed One" in body_all

    body_search = admin_client.get("/admin/contact-requests?status=all&q=closed").get_data(as_text=True)
    assert "Closed One" in body_search and "Bob Shop" not in body_search

    # Бейдж «1 new» в сайдбаре админки и карточка на дашборде.
    dash = admin_client.get("/admin/dashboard").get_data(as_text=True)
    assert "New contact requests" in dash
    assert "Contact requests" in dash


def test_admin_set_status_and_note_logged(admin_client, app):
    rid = _seed_request(app)
    token = get_csrf_token(admin_client)
    resp = admin_client.post(
        f"/admin/contact-requests/{rid}/status",
        data={"status": "in_progress", "admin_note": "Replied by email", "csrf_token": token},
    )
    assert resp.status_code == 302

    from app.extensions import get_master_db
    with app.app_context():
        master = get_master_db()
        doc = master.contact_requests.find_one({"_id": rid})
        assert doc["status"] == "in_progress"
        assert doc["admin_note"] == "Replied by email"
        assert doc["handled_by"] == "root@roobico-test.local"
        audit = master.admin_audit.find_one({"action": "contact_request.set_status", "target_id": rid})
        assert audit is not None
        assert audit["after"]["status"] == "in_progress"

    # Закрытие проставляет closed_at; невалидный статус отклоняется.
    token = get_csrf_token(admin_client)
    admin_client.post(f"/admin/contact-requests/{rid}/status", data={"status": "closed", "csrf_token": token})
    with app.app_context():
        doc = get_master_db().contact_requests.find_one({"_id": rid})
        assert doc["status"] == "closed" and doc.get("closed_at") is not None

    token = get_csrf_token(admin_client)
    resp = admin_client.post(f"/admin/contact-requests/{rid}/status", data={"status": "bogus", "csrf_token": token})
    assert resp.status_code == 302
    with app.app_context():
        assert get_master_db().contact_requests.find_one({"_id": rid})["status"] == "closed"
