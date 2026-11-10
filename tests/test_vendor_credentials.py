"""
Учётки порталов вендоров, нормализация сайтов и вид «Portal logins».

Сайт: normalize_website / website_label / website_href (таблица) и все пути
записи (форма, JSON create/update, письма, импорт) — https:// дописывается,
чужие схемы и мусор отклоняются. Пароль: в базе только Fernet-токен, открытого
текста нет нигде (документ, ответы деталей/списка, HTML, Activity Journal,
тело отклонённого запроса в журнале); пустой пароль сохраняет, clear удаляет.
Право vendors.view_credentials: без него поля не видны и игнорируются при
сохранении. Показ пароля: 403 без права, 404 для вендора чужого магазина,
no-store, запись «Viewed» в журнал, 429 по лимиту, понятная ошибка без ключа.
AI-инструменты учёток не видят и сослаться на них не могут. Экспорт — без
учёток, импорт нормализует сайт.
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from cryptography.fernet import Fernet
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import SHOP_A_DB, SHOP_B_DB, TENANT_A_DB, TEST_MONGO_URI, get_csrf_token, login

USER_PASSWORD = "password123"
# Пароль портала вендора — не должен оказаться нигде, кроме ответа /credentials.
PLAIN = "Pa55-word!vc"
NAME_RE = {"$regex": "^VC "}

USERS = {
    "creds_only": ("vc-creds@test.local", "vc_creds_only"),
    "editor": ("vc-editor@test.local", "vc_editor"),
    "viewer": ("vc-viewer@test.local", "vc_viewer"),
    "mechanic": ("vc-mechanic@test.local", "vc_mechanic"),
}


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture(scope="module")
def vc_env(app, seed, mongo):
    """Кастомные роли (свои ключи — не трогаем системные роли других модулей)
    и пользователи: только просмотр учёток, редактор без учёток, viewer и
    механик с дефолтными правами своих ролей."""
    from app.constants.permissions import build_default_roles

    defaults = {r["key"]: r["permissions"] for r in build_default_roles()}
    roles = {
        "vc_creds_only": ["vendors.view", "vendors.view_credentials"],
        "vc_editor": ["vendors.view", "vendors.create", "vendors.edit"],
        "vc_viewer": defaults["viewer"],
        "vc_mechanic": defaults["mechanic"],
    }
    master = mongo["roobico_test_master"]
    tdb = mongo[TENANT_A_DB]
    for key, perms in roles.items():
        tdb.roles.update_one(
            {"key": key},
            {"$set": {"key": key, "name": key, "is_system": False, "permissions": perms}},
            upsert=True,
        )
    now = datetime.now(timezone.utc)
    for email, role in USERS.values():
        master.users.delete_many({"email": email})
        master.users.insert_one({
            "_id": ObjectId(), "email": email, "password_hash": generate_password_hash(USER_PASSWORD),
            "name": role, "is_active": True, "tenant_id": seed["tenant_a"]["_id"],
            "shop_ids": [str(seed["shop_a"]["_id"])], "role": role, "created_at": now,
        })

    yield {"shop_a": seed["shop_a"], "shop_b": seed["shop_b"], "owner": seed["owner"]}

    master.users.delete_many({"email": {"$in": [email for email, _ in USERS.values()]}})
    tdb.roles.delete_many({"key": {"$in": list(roles)}})
    for db_name in (SHOP_A_DB, SHOP_B_DB):
        mongo[db_name].vendors.delete_many({"name": NAME_RE})
    master.audit_journal.delete_many({"$or": [
        {"entity_label": NAME_RE}, {"payload.json.name": NAME_RE}, {"payload.form.name": NAME_RE},
    ]})


@pytest.fixture(autouse=True)
def secret_key(app, monkeypatch):
    """Свой ключ шифрования на каждый тест (не зависим от .env)."""
    key = Fernet.generate_key().decode()
    monkeypatch.setitem(app.config, "INTEGRATIONS_SECRET_KEY", key)
    return key


def _login_as(client, who):
    resp = login(client, email=USERS[who][0], password=USER_PASSWORD)
    assert resp.status_code == 302, resp.get_data(as_text=True)


def _post_json(client, url, payload):
    return client.post(url, json=payload, headers={"X-CSRFToken": get_csrf_token(client)})


def _create(client, name, **fields):
    resp = _post_json(client, "/vendors/api/create", {"name": name, **fields})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["vendor_id"]


def _vendor(mongo, vid):
    return mongo[SHOP_A_DB].vendors.find_one({"_id": ObjectId(vid)})


def _decrypt(app, token):
    from app.utils.integrations.crypto import decrypt_secret

    with app.app_context():
        return decrypt_secret(token)


def _journal(mongo):
    return mongo["roobico_test_master"].audit_journal


# ── normalize_website / website_label / website_href ───────────────────


@pytest.mark.parametrize("raw,expected", [
    ("fleetpride.com", "https://fleetpride.com"),
    ("www.x.com/login", "https://www.x.com/login"),
    ("//x.com", "https://x.com"),
    ("http://x.com", "http://x.com"),
    ("https://x.com", "https://x.com"),
    ("  fleetpride.com  ", "https://fleetpride.com"),
    ("HTTPS://FleetPride.COM/Login", "https://fleetpride.com/Login"),
    ("fleetpride.com/", "https://fleetpride.com"),
    ("https://x.com/parts/", "https://x.com/parts/"),
    ("x.com/p?q=1#top", "https://x.com/p?q=1#top"),
    ("localhost", "https://localhost"),
    ("localhost:5000", "https://localhost:5000"),
    ("x.com:8080/login", "https://x.com:8080/login"),
    ("fleetpride.com.", "https://fleetpride.com"),
    ("fleetpride", None),
    ("fleet pride.com", None),
    ("javascript:alert(1)", None),
    ("JavaScript:alert(1)", None),
    ("data:text/html,<b>x</b>", None),
    ("ftp://x.com", None),
    ("mailto:parts@x.com", None),
    ("http:x.com", None),
    ("https://", None),
    ("https://user:pass@x.com", None),
    ("x..com", None),
    # Вставка из письма/предложения: то, что JS-проверка формы тоже отвергает.
    ("fleetpride.com,", None),
    ("<fleetpride.com>", None),
    ('"fleetpride.com"', None),
    ("fleetpride.com!", None),
    ("x-.com", None),
    ("x.com:99999", None),
    ("x.com:0", None),
    ("fleetpride.com​", None),
    ("x.com:٣", None),  # порт — только ASCII-цифры
    ("x.com:00080", "https://x.com:00080"),
    ("", None),
    ("   ", None),
    (None, None),
])
def test_normalize_website(raw, expected):
    from app.utils.urls import normalize_website

    assert normalize_website(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("https://fleetpride.com/login", "fleetpride.com/login"),
    ("https://www.x.com/", "www.x.com"),
    ("x.com/parts/?q=1", "x.com/parts"),
    ("localhost:5000", "localhost:5000"),
    ("N/A", "N/A"),
    (None, ""),
])
def test_website_label(raw, expected):
    from app.utils.urls import website_label

    assert website_label(raw) == expected


def test_website_href_makes_legacy_values_links():
    from app.utils.urls import clean_website_input, website_href

    assert website_href("fleetpride.com") == "https://fleetpride.com"  # сохранено до фичи
    assert website_href("https://x.com/") == "https://x.com"
    assert website_href("javascript:alert(1)") == ""
    assert website_href("N/A") == ""
    assert website_href(None) == ""
    assert clean_website_input("") == (None, None)
    assert clean_website_input("x.com") == ("https://x.com", None)
    assert clean_website_input("x") == (None, "Enter a valid website, e.g. fleetpride.com")


# ── сайт на всех путях записи ──────────────────────────────────────────


def test_create_and_update_store_normalized_website(client, vc_env, mongo):
    assert login(client).status_code == 302
    vid = _create(client, "VC Site Vendor", website="FleetPride.com/")
    assert _vendor(mongo, vid)["website"] == "https://fleetpride.com"

    upd = f"/vendors/api/{vid}/update"
    resp = _post_json(client, upd, {"name": "VC Site Vendor", "website": "www.fleetpride.com/login"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _vendor(mongo, vid)["website"] == "https://www.fleetpride.com/login"

    resp = _post_json(client, upd, {"name": "VC Site Vendor", "website": ""})
    assert resp.status_code == 200
    assert _vendor(mongo, vid)["website"] is None


def test_invalid_website_is_rejected_with_message(client, vc_env, mongo):
    assert login(client).status_code == 302
    expected = {"ok": False, "error": "invalid_website", "message": "Enter a valid website, e.g. fleetpride.com"}

    resp = _post_json(client, "/vendors/api/create", {"name": "VC Bad Site", "website": "javascript:alert(1)"})
    assert resp.status_code == 400 and resp.get_json() == expected
    assert mongo[SHOP_A_DB].vendors.count_documents({"name": "VC Bad Site"}) == 0

    vid = _create(client, "VC Good Site", website="good-site.com")
    resp = _post_json(client, f"/vendors/api/{vid}/update", {"name": "VC Good Site", "website": "no dot here"})
    assert resp.status_code == 400 and resp.get_json() == expected
    assert _vendor(mongo, vid)["website"] == "https://good-site.com"

    # Обычная форма создания: flash с ошибкой, вендор не создан.
    resp = client.post("/vendors/create", data={
        "csrf_token": get_csrf_token(client), "name": "VC Form Bad", "website": "ftp://files.example.com",
    })
    assert resp.status_code == 302
    assert mongo[SHOP_A_DB].vendors.count_documents({"name": "VC Form Bad"}) == 0
    assert "Enter a valid website, e.g. fleetpride.com" in client.get(resp.headers["Location"]).get_data(as_text=True)


def test_form_create_normalizes_website_and_saves_credentials(client, vc_env, mongo, app):
    assert login(client).status_code == 302
    resp = client.post("/vendors/create", data={
        "csrf_token": get_csrf_token(client), "name": "VC Form Vendor", "website": "napaonline.com",
        "portal_login": "shop@fleet.test", "portal_password": PLAIN, "return_view": "logins",
    })
    assert resp.status_code == 302 and "view=logins" in resp.headers["Location"]
    doc = mongo[SHOP_A_DB].vendors.find_one({"name": "VC Form Vendor"})
    assert doc["website"] == "https://napaonline.com"
    assert doc["portal_login"] == "shop@fleet.test"
    assert _decrypt(app, doc["portal_password_enc"]) == PLAIN


def test_email_order_vendor_auto_create_normalizes_website(app, vc_env, mongo):
    from app.blueprints.parts.services.email_orders import create_vendor_from_email

    coll = mongo[SHOP_A_DB].vendors
    with app.app_context():
        doc = create_vendor_from_email(
            coll, vc_env["shop_a"], {"vendor_website": "WWW.Parts-Co.com/"},
            sender_email="orders@parts-co.com", sender_name="", vendor_name="VC Email Vendor",
        )
        junk = create_vendor_from_email(
            coll, vc_env["shop_a"], {"vendor_website": "N/A"},
            sender_email="orders@other.example", sender_name="", vendor_name="VC Email Vendor 2",
        )
    assert coll.find_one({"_id": doc["_id"]})["website"] == "https://www.parts-co.com"
    assert coll.find_one({"_id": junk["_id"]})["website"] is None


def test_legacy_websites_render_as_working_links(client, vc_env, mongo):
    shop_id = vc_env["shop_a"]["_id"]
    mongo[SHOP_A_DB].vendors.insert_many([
        {"shop_id": shop_id, "name": "VC Legacy Site", "website": "legacy-vendor.com", "is_active": True},
        {"shop_id": shop_id, "name": "VC Legacy Junk", "website": "see notes", "is_active": True},
    ])
    assert login(client).status_code == 302
    html = client.get("/vendors/?q=VC+Legacy").get_data(as_text=True)
    assert 'href="https://legacy-vendor.com"' in html and ">legacy-vendor.com</a>" in html
    assert "see notes" in html and 'href="see notes"' not in html


# ── пароль: шифрование и хранение ──────────────────────────────────────


def test_password_is_stored_encrypted(client, vc_env, mongo, app):
    assert login(client).status_code == 302
    vid = _create(client, "VC Crypto Vendor", website="crypto-vendor.com",
                  portal_login="parts@shop.test", portal_password=PLAIN)
    doc = _vendor(mongo, vid)
    token = doc["portal_password_enc"]
    assert token and token != PLAIN and token.startswith("gAAAA")  # Fernet-токен
    assert _decrypt(app, token) == PLAIN
    assert PLAIN not in json.dumps(doc, default=str)
    assert doc["portal_login"] == "parts@shop.test"
    assert doc["portal_password_updated_at"] is not None
    assert doc["portal_password_updated_by"] == vc_env["owner"]["_id"]


def test_update_blank_password_keeps_new_replaces_clear_removes(client, vc_env, mongo, app):
    assert login(client).status_code == 302
    vid = _create(client, "VC Keep Vendor", portal_login="a@shop.test", portal_password=PLAIN)
    upd = f"/vendors/api/{vid}/update"
    token = _vendor(mongo, vid)["portal_password_enc"]

    # Пустой пароль и нет ключа portal_login (старая мобилка) — всё на месте.
    assert _post_json(client, upd, {"name": "VC Keep Vendor", "portal_password": ""}).status_code == 200
    doc = _vendor(mongo, vid)
    assert doc["portal_password_enc"] == token and doc["portal_login"] == "a@shop.test"

    assert _post_json(client, upd, {
        "name": "VC Keep Vendor", "portal_login": "b@shop.test", "portal_password": "N3w-pass",
    }).status_code == 200
    doc = _vendor(mongo, vid)
    assert doc["portal_login"] == "b@shop.test"
    assert doc["portal_password_enc"] != token and _decrypt(app, doc["portal_password_enc"]) == "N3w-pass"

    assert _post_json(client, upd, {
        "name": "VC Keep Vendor", "portal_login": "", "portal_password_clear": True,
    }).status_code == 200
    doc = _vendor(mongo, vid)
    assert doc["portal_password_enc"] is None and doc["portal_login"] is None


def test_too_long_credentials_rejected(client, vc_env, mongo):
    assert login(client).status_code == 302
    resp = _post_json(client, "/vendors/api/create", {"name": "VC Long Login", "portal_login": "x" * 257})
    assert resp.status_code == 400 and resp.get_json()["error"] == "portal_login_too_long"
    resp = _post_json(client, "/vendors/api/create", {"name": "VC Long Password", "portal_password": "p" * 257})
    assert resp.status_code == 400 and resp.get_json()["error"] == "portal_password_too_long"
    assert mongo[SHOP_A_DB].vendors.count_documents({"name": {"$in": ["VC Long Login", "VC Long Password"]}}) == 0


# ── детали / страницы не отдают секретов ───────────────────────────────


def test_details_and_pages_never_expose_password(client, vc_env, mongo):
    assert login(client).status_code == 302
    vid = _create(client, "VC Detail Vendor", website="detail-vendor.com",
                  portal_login="detail@shop.test", portal_password=PLAIN)
    token = _vendor(mongo, vid)["portal_password_enc"]

    resp = client.get(f"/vendors/api/{vid}")
    text = resp.get_data(as_text=True)
    item = resp.get_json()["item"]
    assert item["website"] == "https://detail-vendor.com"
    assert item["website_href"] == "https://detail-vendor.com" and item["website_label"] == "detail-vendor.com"
    assert item["portal_login"] == "detail@shop.test" and item["has_portal_password"] is True
    assert PLAIN not in text and token not in text and "portal_password_enc" not in text

    for url in ("/vendors/?q=VC+Detail", "/vendors/?view=logins&q=VC+Detail"):
        html = client.get(url).get_data(as_text=True)
        assert PLAIN not in html and token not in html, url
    html = client.get("/vendors/?view=logins&q=VC+Detail").get_data(as_text=True)
    assert 'id="vendorLoginsTable"' in html
    assert "detail@shop.test" in html and "js-show-password" in html
    assert 'href="https://detail-vendor.com"' in html and ">detail-vendor.com</a>" in html
    assert 'name="view" value="logins"' in html  # поиск остаётся в этом виде


def test_without_permission_fields_hidden_and_ignored(client, vc_env, mongo):
    assert login(client).status_code == 302
    vid = _create(client, "VC Hidden Vendor", portal_login="hidden@shop.test", portal_password=PLAIN)
    token = _vendor(mongo, vid)["portal_password_enc"]

    _login_as(client, "editor")  # create + edit, без vendors.view_credentials
    item = client.get(f"/vendors/api/{vid}").get_json()["item"]
    assert "portal_login" not in item and "has_portal_password" not in item
    html = client.get("/vendors/?q=VC+Hidden").get_data(as_text=True)
    assert "VC Hidden Vendor" in html
    assert "hidden@shop.test" not in html
    assert 'id="tab-portal-logins"' not in html and 'id="vendorCredentialsGroup"' not in html
    html = client.get("/vendors/?view=logins").get_data(as_text=True)
    assert 'id="vendorLoginsTable"' not in html  # без права — обычный список

    resp = _post_json(client, f"/vendors/api/{vid}/update", {
        "name": "VC Hidden Vendor", "notes": "edited", "portal_login": "evil@x.test",
        "portal_password": "evil", "portal_password_clear": True,
    })
    assert resp.status_code == 200
    doc = _vendor(mongo, vid)
    assert doc["notes"] == "edited"
    assert doc["portal_login"] == "hidden@shop.test" and doc["portal_password_enc"] == token

    vid2 = _create(client, "VC Hidden Created", portal_login="x@x.test", portal_password="pw")
    doc2 = _vendor(mongo, vid2)
    assert "portal_login" not in doc2 and "portal_password_enc" not in doc2

    assert client.get(f"/vendors/api/{vid}/credentials").status_code == 403


# ── показ пароля ───────────────────────────────────────────────────────


def test_reveal_returns_credentials_no_store_and_journals(client, vc_env, mongo):
    assert login(client).status_code == 302
    vid = _create(client, "VC Reveal Vendor", website="reveal.example.com",
                  portal_login="rv@shop.test", portal_password=PLAIN)

    resp = client.get(f"/vendors/api/{vid}/credentials")
    assert resp.status_code == 200
    assert "no-store" in resp.headers["Cache-Control"]
    assert resp.get_json() == {
        "ok": True, "website": "https://reveal.example.com", "website_href": "https://reveal.example.com",
        "login": "rv@shop.test", "password": PLAIN,
    }

    journal = _journal(mongo)
    viewed = journal.find_one({"action": "view", "entity_type": "vendor", "entity_id": vid})
    assert viewed and viewed["summary"] == "Viewed portal password of VC Reveal Vendor"
    created = journal.find_one({"action": "create", "entity_type": "vendor", "entity_id": vid})
    changes = {c["field"]: c for c in created["changes"]}
    assert changes["portal_password"]["new"] == "set"
    assert (changes["portal_login"]["old"], changes["portal_login"]["new"]) == ("", "set")
    for entry in journal.find({"entity_id": vid}):
        dumped = json.dumps(entry, default=str)
        assert PLAIN not in dumped and "rv@shop.test" not in dumped

    # Journal page renders the new action.
    page = client.get(f"/reports/audit?entity_type=vendor&entity_id={vid}").get_data(as_text=True)
    assert "Viewed portal password of VC Reveal Vendor" in page and "Viewed" in page


def test_update_journal_lists_credential_changes_without_values(client, vc_env, mongo):
    assert login(client).status_code == 302
    vid = _create(client, "VC Audit Vendor", portal_login="old@shop.test", portal_password=PLAIN)
    upd = f"/vendors/api/{vid}/update"
    assert _post_json(client, upd, {
        "name": "VC Audit Vendor", "portal_login": "new@shop.test", "portal_password": "Another-1",
    }).status_code == 200
    assert _post_json(client, upd, {"name": "VC Audit Vendor", "portal_password_clear": True}).status_code == 200
    assert _post_json(client, upd, {"name": "VC Audit Vendor", "portal_login": ""}).status_code == 200

    journal = _journal(mongo)
    updates = list(journal.find({"action": "update", "entity_type": "vendor", "entity_id": vid}).sort("_id", 1))
    assert len(updates) == 3
    first = {c["field"]: c for c in updates[0]["changes"]}
    # Логин — как и пароль, без значений: журнал видят и те, кому учётки не показываются.
    assert (first["portal_login"]["old"], first["portal_login"]["new"]) == ("set", "changed")
    assert (first["portal_password"]["old"], first["portal_password"]["new"]) == ("set", "changed")
    second = {c["field"]: c for c in updates[1]["changes"]}
    assert (second["portal_password"]["old"], second["portal_password"]["new"]) == ("set", "removed")
    assert "portal_login" not in second  # логин не трогали
    third = {c["field"]: c for c in updates[2]["changes"]}
    assert (third["portal_login"]["old"], third["portal_login"]["new"]) == ("set", "removed")
    assert "portal_login" not in _vendor(mongo, vid) or _vendor(mongo, vid)["portal_login"] is None
    for entry in journal.find({"entity_id": vid}):
        dumped = json.dumps(entry, default=str)
        for secret in (PLAIN, "Another-1", "old@shop.test", "new@shop.test"):
            assert secret not in dumped


def test_viewer_journal_never_shows_portal_login(client, vc_env, mongo):
    """Viewer (reports.view_audit, но без vendors.view_credentials) не видит
    логин ни в событиях вендора, ни в поиске журнала, ни в теле отклонённого
    запроса."""
    assert login(client).status_code == 302
    vid = _create(client, "VC Journal Vendor", portal_login="journal-login@fleet.test", portal_password=PLAIN)
    assert _post_json(client, f"/vendors/api/{vid}/update", {
        "name": "VC Journal Vendor", "website": "bad site", "portal_login": "journal-login2@fleet.test",
    }).status_code == 400

    rejected = _journal(mongo).find_one({"kind": "request", "path": f"/vendors/api/{vid}/update"})
    assert rejected and rejected["payload"]["json"]["portal_login"] == "***"

    _login_as(client, "viewer")
    pages = {}
    for url in (f"/reports/audit?entity_type=vendor&entity_id={vid}", "/reports/audit?q=fleet.test",
                f"/reports/audit?q={vid}"):
        resp = client.get(url)
        assert resp.status_code == 200, url
        pages[url] = resp.get_data(as_text=True)
        assert "journal-login" not in pages[url] and PLAIN not in pages[url], url
    assert "Portal login" in pages[f"/reports/audit?entity_type=vendor&entity_id={vid}"]  # изменение видно, без значения
    assert "Request data" in pages[f"/reports/audit?q={vid}"]  # тело отклонённого запроса — с маской


def test_reveal_requires_permission(client, vc_env, mongo):
    assert login(client).status_code == 302
    vid = _create(client, "VC Perm Vendor", portal_login="perm@shop.test", portal_password=PLAIN)
    for who in ("viewer", "mechanic"):
        _login_as(client, who)
        resp = client.get(f"/vendors/api/{vid}/credentials")
        assert resp.status_code == 403, who
        assert PLAIN not in resp.get_data(as_text=True)
    _login_as(client, "creds_only")
    resp = client.get(f"/vendors/api/{vid}/credentials")
    assert resp.status_code == 200 and resp.get_json()["password"] == PLAIN


def test_other_shop_vendor_is_404(client, vc_env, mongo, secret_key):
    token = Fernet(secret_key.encode()).encrypt(PLAIN.encode()).decode()
    # Вендор магазина B, оказавшийся в базе A, и вендор в базе B.
    foreign = {"_id": ObjectId(), "shop_id": vc_env["shop_b"]["_id"], "name": "VC Foreign Vendor",
               "is_active": True, "portal_login": "f@x.test", "portal_password_enc": token}
    other_db = {"_id": ObjectId(), "shop_id": vc_env["shop_b"]["_id"], "name": "VC Foreign Vendor B",
                "is_active": True, "portal_login": "f@x.test", "portal_password_enc": token}
    mongo[SHOP_A_DB].vendors.insert_one(foreign)
    mongo[SHOP_B_DB].vendors.insert_one(other_db)

    assert login(client).status_code == 302
    for vid in (foreign["_id"], other_db["_id"]):
        resp = client.get(f"/vendors/api/{vid}/credentials")
        assert resp.status_code == 404 and PLAIN not in resp.get_data(as_text=True)
        assert client.get(f"/vendors/api/{vid}").status_code == 404
        assert _post_json(client, f"/vendors/api/{vid}/update", {"name": "VC Foreign Vendor"}).status_code == 404
    assert client.get("/vendors/api/not-an-id/credentials").status_code == 404
    html = client.get("/vendors/?view=logins&q=VC+Foreign").get_data(as_text=True)
    assert "VC Foreign Vendor" not in html


def test_reveal_is_rate_limited(client, vc_env, monkeypatch):
    from app.blueprints.vendors import services

    monkeypatch.setattr(services, "REVEAL_RATE_LIMIT", 2)
    assert login(client).status_code == 302
    vid = _create(client, "VC Limit Vendor", portal_password=PLAIN)
    assert client.get(f"/vendors/api/{vid}/credentials").status_code == 200
    assert client.get(f"/vendors/api/{vid}/credentials").status_code == 200
    resp = client.get(f"/vendors/api/{vid}/credentials")
    assert resp.status_code == 429
    body = resp.get_json()
    assert body["error"] == "rate_limited" and "wait a few minutes" in body["message"]
    assert PLAIN not in resp.get_data(as_text=True)


def test_missing_encryption_key_gives_clear_error(client, vc_env, mongo, app, monkeypatch):
    message = "Password encryption is not configured on the server — contact support"
    assert login(client).status_code == 302
    vid = _create(client, "VC Key Vendor", portal_password=PLAIN)

    monkeypatch.setitem(app.config, "INTEGRATIONS_SECRET_KEY", "")
    resp = _post_json(client, "/vendors/api/create", {"name": "VC No Key", "portal_password": "pw"})
    assert resp.status_code == 503 and resp.get_json()["message"] == message
    assert mongo[SHOP_A_DB].vendors.count_documents({"name": "VC No Key"}) == 0
    # Всё остальное работает: создание/правка без пароля, логин, показ без пароля.
    vid2 = _create(client, "VC No Key Plain", website="plain.example.com", portal_login="only-login")
    resp = _post_json(client, f"/vendors/api/{vid}/update", {"name": "VC Key Vendor", "notes": "still works"})
    assert resp.status_code == 200
    resp = client.get(f"/vendors/api/{vid2}/credentials")
    assert resp.status_code == 200 and resp.get_json()["login"] == "only-login" and resp.get_json()["password"] == ""

    resp = client.get(f"/vendors/api/{vid}/credentials")
    assert resp.status_code == 503 and resp.get_json()["message"] == message
    monkeypatch.setitem(app.config, "INTEGRATIONS_SECRET_KEY", "not-a-fernet-key")
    resp = client.get(f"/vendors/api/{vid}/credentials")
    assert resp.status_code == 503 and resp.get_json()["message"] == message
    # Ключ сменили без перешифровки — просим ввести пароль заново.
    monkeypatch.setitem(app.config, "INTEGRATIONS_SECRET_KEY", Fernet.generate_key().decode())
    resp = client.get(f"/vendors/api/{vid}/credentials")
    assert resp.status_code == 503 and resp.get_json()["error"] == "password_unreadable"


def test_rejected_request_body_masks_password_in_journal(client, vc_env, mongo):
    assert login(client).status_code == 302
    resp = _post_json(client, "/vendors/api/create", {
        "name": "VC Rejected", "website": "bad site", "portal_login": "rejected-login@shop.test",
        "portal_password": PLAIN,
    })
    assert resp.status_code == 400
    entry = _journal(mongo).find_one({"kind": "request", "path": "/vendors/api/create",
                                      "payload.json.name": "VC Rejected"})
    assert entry and entry["payload"]["json"]["portal_password"] == "***"
    assert entry["payload"]["json"]["portal_login"] == "***"
    dumped = json.dumps(entry, default=str)
    assert PLAIN not in dumped and "rejected-login@shop.test" not in dumped

    # Обычная форма создания (form-encoded) — та же маска.
    resp = client.post("/vendors/create", data={
        "csrf_token": get_csrf_token(client), "name": "VC Rejected Form", "website": "bad site",
        "portal_login": "rejected-form@shop.test", "portal_password": PLAIN,
    })
    assert resp.status_code == 302
    entry = _journal(mongo).find_one({"kind": "request", "path": "/vendors/create",
                                      "payload.form.name": "VC Rejected Form"})
    assert entry and entry["payload"]["form"]["portal_login"] == "***"
    dumped = json.dumps(entry, default=str)
    assert PLAIN not in dumped and "rejected-form@shop.test" not in dumped


# ── права и роли ───────────────────────────────────────────────────────


def test_permission_catalog_and_default_roles():
    from app.constants.permissions import PERMISSION_GROUPS, PERMISSIONS, build_default_roles

    assert PERMISSIONS["vendors.view_credentials"] == "View vendor portal logins & passwords"
    vendors_group = next(g for g in PERMISSION_GROUPS if g["key"] == "vendors")
    assert "vendors.view_credentials" in vendors_group["items"]
    having = {r["key"] for r in build_default_roles() if "vendors.view_credentials" in r["permissions"]}
    assert having == {"owner", "general_manager", "manager", "parts_manager"}


def test_sync_default_roles_grants_system_roles_not_custom(mongo):
    from app.scripts.sync_default_roles import sync_tenant_roles

    tdb = mongo["roobico_test_vc_role_sync"]
    tdb.roles.delete_many({})
    tdb.roles.insert_many([
        {"key": "parts_manager", "is_system": True, "permissions": ["vendors.view", "vendors.edit"]},
        {"key": "viewer", "is_system": True, "permissions": ["vendors.view"]},
        {"key": "custom_vendors", "is_system": False, "permissions": ["vendors.view", "vendors.edit"]},
    ])
    try:
        sync_tenant_roles(tdb)
        perms = {r["key"]: set(r["permissions"]) for r in tdb.roles.find({})}
        assert "vendors.view_credentials" in perms["parts_manager"]
        assert "vendors.view_credentials" not in perms["viewer"]
        # Новая возможность, а не разделение старого ключа: кастомным ролям не выдаётся.
        assert "vendors.view_credentials" not in perms["custom_vendors"]
    finally:
        mongo.drop_database("roobico_test_vc_role_sync")


# ── страницы ───────────────────────────────────────────────────────────


def test_owner_sees_tabs_and_credential_fields(client, vc_env):
    assert login(client).status_code == 302
    html = client.get("/vendors/").get_data(as_text=True)
    assert 'id="tab-portal-logins"' in html and 'id="vendorCredentialsGroup"' in html
    assert 'id="vendorLoginsTable"' not in html  # owner правит вендоров — по умолчанию полный список
    assert 'placeholder="fleetpride.com"' in html and "https:// is added automatically." in html
    assert 'autocomplete="new-password"' in html


def test_credentials_only_user_lands_on_portal_logins(client, vc_env, mongo):
    assert login(client).status_code == 302
    _create(client, "VC Landing Vendor", website="landing.example.com",
            portal_login="land@shop.test", portal_password=PLAIN)

    _login_as(client, "creds_only")
    html = client.get("/vendors/?q=VC+Landing").get_data(as_text=True)
    assert 'id="vendorLoginsTable"' in html and 'id="tab-portal-logins"' in html
    assert "land@shop.test" in html and PLAIN not in html
    assert "Add Vendor" not in html and "editVendorBtn" not in html
    html = client.get("/vendors/?view=vendors&q=VC+Landing").get_data(as_text=True)
    assert 'id="vendorLoginsTable"' not in html and "VC Landing Vendor" in html


def test_vendors_tab_search_filter_and_reset_keep_view(client, vc_env):
    """Поиск, фильтр статуса и Reset на вкладке Vendors отправляют view=vendors:
    без него тот, кто видит учётки, но не правит вендоров, улетал бы на
    Portal logins (там нет фильтра статуса)."""
    hidden_vendors = '<input type="hidden" name="view" value="vendors" />'
    _login_as(client, "creds_only")
    html = client.get("/vendors/?view=vendors").get_data(as_text=True)
    assert hidden_vendors in html  # уходит и с формой поиска, и с select статуса (submit формы)
    html = client.get("/vendors/?view=vendors&status=inactive&q=VC").get_data(as_text=True)
    assert 'id="vendorLoginsTable"' not in html and hidden_vendors in html
    assert 'href="/vendors/?view=vendors">Reset</a>' in html
    html = client.get("/vendors/?view=logins&q=VC").get_data(as_text=True)
    assert '<input type="hidden" name="view" value="logins" />' in html
    assert 'href="/vendors/?view=logins">Reset</a>' in html

    # Без права на учётки вкладок нет — и лишнего параметра тоже.
    _login_as(client, "editor")
    html = client.get("/vendors/?q=VC").get_data(as_text=True)
    assert 'name="view"' not in html and 'id="tab-portal-logins"' not in html


# ── AI-ассистент ───────────────────────────────────────────────────────


def test_ai_data_tools_never_see_credentials(app, vc_env, mongo, monkeypatch):
    from app.blueprints.assistant.services import data_tools
    from app.constants.permissions import ALL_PERMISSIONS

    shop_db = mongo[SHOP_A_DB]
    shop_id = vc_env["shop_a"]["_id"]
    vendor = {"_id": ObjectId(), "shop_id": shop_id, "name": "VC AI Vendor", "is_active": True,
              "website": "https://ai.example.com", "portal_login": "ai-login@shop.test",
              "portal_password_enc": "gAAAAA-fake-token"}
    plain = {"_id": ObjectId(), "shop_id": shop_id, "name": "VC AI Vendor Two", "is_active": True}
    shop_db.vendors.insert_many([vendor, plain])
    both = {"name": {"$in": ["VC AI Vendor", "VC AI Vendor Two"]}}

    def run(name, args):
        return data_tools.run_tool(name, args, shop_db=shop_db, shop_id=shop_id,
                                   permissions=set(ALL_PERMISSIONS))

    # Оракул через $jsonSchema: patternProperties проверяет поле, не называя его.
    def prefix_probe(guess):
        return {"name": "VC AI Vendor",
                "$jsonSchema": {"patternProperties": {"^portal_lo": {"pattern": "^" + guess}}}}

    presence_probe = {"$nor": [{"$jsonSchema": {"patternProperties": {"^portal_lo": {"not": {}}}}}], **both}

    with app.app_context():
        found = run("db_find", {"collection": "vendors", "filter": {"name": "VC AI Vendor"}})
        assert found["returned"] == 1
        doc = found["docs"][0]
        assert doc["website"] == "https://ai.example.com"
        assert "portal_login" not in doc and "portal_password_enc" not in doc

        # find по vendors идёт pipeline'ом — фильтр, сортировка, лимит и проекция как у find.
        found = run("db_find", {"collection": "vendors", "filter": both, "sort": {"name": -1},
                                "projection": {"name": 1, "_id": 0}, "limit": 1})
        assert found["docs"] == [{"name": "VC AI Vendor Two"}]
        assert run("db_count", {"collection": "vendors", "filter": both}) == {"count": 2}
        assert run("db_count", {"collection": "vendors", "filter": {"name": "VC AI Nobody"}}) == {"count": 0}

        agg = run("db_aggregate", {"collection": "vendors", "pipeline": [{"$match": {"name": "VC AI Vendor"}}]})
        assert agg["returned"] == 1
        assert "portal_login" not in agg["docs"][0] and "portal_password_enc" not in agg["docs"][0]

        rejected = [
            ("db_find", {"collection": "vendors", "filter": {"portal_login": {"$regex": "^ai"}}}),
            ("db_find", {"collection": "vendors", "projection": {"portal_password_enc": 1}}),
            ("db_find", {"collection": "vendors", "sort": {"portal_login": 1}}),
            ("db_count", {"collection": "vendors", "filter": {"portal_login": {"$exists": True}}}),
            ("db_count", {"collection": "vendors",
                          "filter": {"$expr": {"$gt": [{"$size": {"$objectToArray": "$$ROOT"}}, 0]}}}),
            ("db_aggregate", {"collection": "vendors", "pipeline": [{"$project": {"x": "$portal_login"}}]}),
            ("db_aggregate", {"collection": "vendors", "pipeline": [{"$group": {"_id": "$portal_password_enc"}}]}),
            ("db_aggregate", {"collection": "vendors", "pipeline": [{"$replaceRoot": {"newRoot": "$$ROOT"}}]}),
            ("db_aggregate", {"collection": "vendors",
                              "pipeline": [{"$project": {"kv": {"$objectToArray": "$$CURRENT"}}}]}),
            ("db_count", {"collection": "vendors", "filter": prefix_probe("a")}),
            ("db_count", {"collection": "vendors", "filter": presence_probe}),
            ("db_find", {"collection": "vendors", "filter": prefix_probe("a")}),
            ("db_aggregate", {"collection": "vendors", "pipeline": [{"$match": presence_probe}]}),
        ]
        for name, args in rejected:
            result = run(name, args)
            assert "error" in result, (name, args, result)
            assert "ai-login@shop.test" not in json.dumps(result)
        assert "vendor portal credential" in run(*rejected[0])["error"]
        assert "$jsonSchema is not allowed" in run("db_count", {"collection": "vendors",
                                                                "filter": presence_probe})["error"]

        # Вторая линия: даже если guard пропустит неизвестный обход (здесь —
        # $jsonSchema), учёток в документах уже нет к моменту фильтра модели.
        monkeypatch.setattr(data_tools, "_FORBIDDEN_OPS", data_tools._FORBIDDEN_OPS - {"$jsonSchema"})
        answers = {
            guess: (run("db_count", {"collection": "vendors", "filter": prefix_probe(guess)}),
                    run("db_find", {"collection": "vendors", "filter": prefix_probe(guess),
                                    "projection": {"name": 1}})["returned"])
            for guess in ("a", "b")
        }
        assert answers["a"] == answers["b"], answers  # ответ не зависит от логина
        assert run("db_count", {"collection": "vendors", "filter": presence_probe}) == {"count": 0}
        agg = run("db_aggregate", {"collection": "vendors", "pipeline": [{"$match": presence_probe}]})
        assert agg["returned"] == 0
    shop_db.vendors.delete_many({"_id": {"$in": [vendor["_id"], plain["_id"]]}})


# ── импорт / экспорт ───────────────────────────────────────────────────


def test_export_never_includes_credentials(client, vc_env, mongo):
    import openpyxl

    assert login(client).status_code == 302
    vid = _create(client, "VC Export Vendor", website="export.example.com",
                  portal_login="export-login@shop.test", portal_password=PLAIN)
    token = _vendor(mongo, vid)["portal_password_enc"]

    text = client.get("/import-export/export/vendors?fmt=csv").get_data(as_text=True)
    assert "VC Export Vendor" in text and "https://export.example.com" in text
    for secret in ("export-login@shop.test", PLAIN, token):
        assert secret not in text

    resp = client.get("/import-export/export/vendors?fmt=xlsx")
    ws = openpyxl.load_workbook(io.BytesIO(resp.data)).active
    cells = " ".join(str(c.value) for row in ws.iter_rows() for c in row if c.value is not None)
    assert "VC Export Vendor" in cells
    for secret in ("export-login@shop.test", PLAIN, token):
        assert secret not in cells


def test_import_normalizes_website_and_ignores_credentials(client, vc_env, mongo):
    assert login(client).status_code == 302
    csv_text = ("Vendor Name,Website,Login,Password\n"
                "VC Import One,fleetpride-import.com/,imp-login,imp-pass\n"
                "VC Import Two,not a site,,\n")
    mapping = {"Vendor Name": "name", "Website": "website", "Login": "portal_login", "Password": "portal_password"}
    resp = client.post("/import-export/import", data={
        "csrf_token": get_csrf_token(client), "entity_type": "vendors", "mapping": json.dumps(mapping),
        "file": (io.BytesIO(csv_text.encode("utf-8")), "vendors.csv"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    result = resp.get_json()
    assert result["imported"] == 2
    assert any("not a site" in w for w in result.get("warnings", []))

    vendors = mongo[SHOP_A_DB].vendors
    one = vendors.find_one({"name": "VC Import One"})
    assert one["website"] == "https://fleetpride-import.com"
    assert "portal_login" not in one and "portal_password_enc" not in one
    assert vendors.find_one({"name": "VC Import Two"})["website"] is None


# ── бэкфилл сайтов ─────────────────────────────────────────────────────


def test_backfill_vendor_websites(app, mongo):
    from app.scripts.backfill_vendor_websites import backfill_vendors

    coll = mongo["roobico_test_vc_backfill"].vendors
    coll.delete_many({})
    coll.insert_many([
        {"name": "Bare", "website": "fleetpride.com"},
        {"name": "Ok", "website": "https://ok.example.com"},
        {"name": "Junk", "website": "see notes"},
        {"name": "Empty", "website": ""},
        {"name": "None", "website": None},
    ])
    try:
        lines: list[str] = []
        with app.app_context():
            dry = backfill_vendors(coll, dry_run=True, verb="would update", log=lines.append)
            assert dry == {"scanned": 3, "changed": 1, "ok": 1, "invalid": 1, "errors": 0}
            assert coll.find_one({"name": "Bare"})["website"] == "fleetpride.com"  # dry-run не пишет
            assert any("see notes" in line for line in lines)

            real = backfill_vendors(coll, dry_run=False, verb="updated", log=lines.append)
            assert real["changed"] == 1
            assert coll.find_one({"name": "Bare"})["website"] == "https://fleetpride.com"
            assert coll.find_one({"name": "Junk"})["website"] == "see notes"  # не трогаем

            again = backfill_vendors(coll, dry_run=False, verb="updated", log=lines.append)
            assert again["changed"] == 0  # идемпотентен
    finally:
        mongo.drop_database("roobico_test_vc_backfill")
