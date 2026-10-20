"""
Права доступа: роуты проверяют именно тот ключ, который показан в
Settings → Roles; деньги не утекают в механик-режим; менеджер не может
повысить себя до владельца или раздать права, которых у него нет.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import OWNER_EMAIL, SHOP_A_DB, TENANT_A_DB, TEST_MONGO_URI, get_csrf_token, login

PASSWORD = "password123"
USERS = {
    "viewer": "perm-viewer@test.local",
    "mechanic": "perm-mechanic@test.local",
    "manager": "perm-manager@test.local",
    "general_manager": "perm-gm@test.local",
    "locations_only": "perm-locations@test.local",
    "parts_manager": "perm-parts@test.local",
}


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture(scope="module")
def perm_seed(app, seed, mongo):
    """Дефолтные системные роли + кастомная «только локации» + по пользователю на роль + WO."""
    from app.constants.permissions import build_default_roles

    master = mongo["roobico_test_master"]
    tdb = mongo[TENANT_A_DB]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = _now()

    for role in build_default_roles():
        if role["key"] == "owner":
            continue  # owner-роль уже посеяна conftest'ом
        tdb.roles.update_one({"key": role["key"]}, {"$set": role}, upsert=True)
    tdb.roles.update_one(
        {"key": "locations_only"},
        {"$set": {"key": "locations_only", "name": "Locations only", "is_system": False,
                  "permissions": ["settings.view", "settings.manage_locations"]}},
        upsert=True,
    )

    users = {}
    for role, email in USERS.items():
        doc = {
            "_id": ObjectId(), "email": email, "password_hash": generate_password_hash(PASSWORD),
            "first_name": role.title(), "last_name": "Perm", "name": f"{role.title()} Perm",
            "is_active": True, "tenant_id": seed["tenant_a"]["_id"],
            "shop_ids": [str(shop_a["_id"])], "role": role, "created_at": now,
        }
        master.users.insert_one(doc)
        users[role] = doc

    customer = {"_id": ObjectId(), "shop_id": shop_a["_id"], "company_name": "Perm Fleet", "is_active": True, "created_at": now}
    shop_db.customers.insert_one(customer)
    part = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "part_number": "PERM-STOCK-1", "description": "Stock perm part",
        "in_stock": 5, "average_cost": 10.0, "is_active": True, "do_not_track_inventory": False,
        "search_terms": ["perm-stock-1"], "created_at": now, "updated_at": now,
    }
    shop_db.parts.insert_one(part)
    wo = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "wo_number": 95001, "customer_id": customer["_id"],
        "status": "open", "is_active": True, "labors": [], "totals": {"grand_total": 150.0},
        "created_at": now, "updated_at": now,
    }
    shop_db.work_orders.insert_one(wo)

    yield {"users": users, "wo": wo, "customer": customer, "owner": seed["owner"], "part": part}

    master.users.delete_many({"email": {"$in": list(USERS.values())}})
    master.users.delete_many({"email": {"$in": ["perm-created-owner@test.local"]}})
    tdb.roles.delete_one({"key": "locations_only"})
    shop_db.work_orders.delete_one({"_id": wo["_id"]})
    shop_db.customers.delete_one({"_id": customer["_id"]})
    shop_db.parts.delete_one({"_id": part["_id"]})
    shop_db.part_location_stock.delete_many({"part_id": part["_id"]})
    shop_db.inventory_movements.delete_many({"part_id": part["_id"]})


def _login_as(client, role):
    resp = login(client, email=USERS[role], password=PASSWORD)
    assert resp.status_code == 302, resp.get_data(as_text=True)


def _post_json(client, url, payload=None):
    token = get_csrf_token(client)
    return client.post(url, json=payload or {}, headers={"X-CSRFToken": token})


def _put_json(client, url, payload=None):
    token = get_csrf_token(client)
    return client.put(url, json=payload or {}, headers={"X-CSRFToken": token})


def _post_form(client, url, data):
    data = dict(data)
    data["csrf_token"] = get_csrf_token(client)
    return client.post(url, data=data)


# ── work orders: money and status actions need their own keys ───────────

def test_viewer_cannot_pay_change_status_send_or_export(client, perm_seed):
    _login_as(client, "viewer")
    wo_id = str(perm_seed["wo"]["_id"])
    assert _post_json(client, f"/work_orders/api/work_orders/{wo_id}/payment", {"amount": 10}).status_code == 403
    assert _post_json(client, f"/work_orders/api/work_orders/{wo_id}/status", {"status": "paid"}).status_code == 403
    assert _post_json(client, f"/work_orders/api/work_orders/{wo_id}/send-email", {}).status_code == 403
    assert _post_json(client, "/work_orders/api/bulk-payments", {}).status_code == 403
    assert client.get(f"/work_orders/api/work_orders/{wo_id}/download-pdf").status_code == 403
    # Просмотр платежей — есть view_costs, поэтому можно.
    assert client.get(f"/work_orders/api/work_orders/{wo_id}/payments").status_code == 200


def test_test_payment_endpoint_is_gone(client, perm_seed):
    assert login(client).status_code == 302
    resp = _post_json(client, f"/work_orders/api/test/create-sample-payment/{perm_seed['wo']['_id']}")
    assert resp.status_code == 404


# ── stock quantity: direct edits need parts.edit_stock_qty ───────────────

def _part_payload(part, **overrides):
    payload = {
        "part_number": part["part_number"], "description": part["description"], "reference": "",
        "vendor_id": "", "category_id": "", "location_id": "", "in_stock": part["in_stock"],
        "average_cost": part["average_cost"], "do_not_track_inventory": False,
        "has_selling_price": False, "selling_price": 0, "core_has_charge": False, "core_cost": 0,
        "misc_has_charge": False, "misc_charges": [],
    }
    payload.update(overrides)
    return payload


def test_parts_manager_cannot_change_stock_qty_via_edit(client, perm_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    part = perm_seed["part"]
    pid = str(part["_id"])
    _login_as(client, "parts_manager")  # parts.edit есть, parts.edit_stock_qty — нет

    # Обычная правка без изменения остатка — можно.
    resp = _post_json(client, f"/parts/api/{pid}/update", _part_payload(part, description="Renamed by parts manager"))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert shop_db.parts.find_one({"_id": part["_id"]})["description"] == "Renamed by parts manager"

    # Попытка поменять остаток — 403, остаток не тронут.
    resp = _post_json(client, f"/parts/api/{pid}/update", _part_payload(part, in_stock=50))
    assert resp.status_code == 403, resp.get_data(as_text=True)
    assert resp.get_json()["required"] == "parts.edit_stock_qty"
    assert shop_db.parts.find_one({"_id": part["_id"]})["in_stock"] == 5

    # "Set qty" по локации — тоже закрыто.
    assert _post_json(client, f"/parts/api/{pid}/locations/adjust", {"location_id": "", "qty": 7}).status_code == 403

    # Страница партов не отдаёт флаг права.
    body = client.get("/parts/?tab=parts").get_data(as_text=True)
    assert '"edit_stock_qty": false' in body

    # Менеджер — может, и остаток меняется.
    _login_as(client, "manager")
    resp = _post_json(client, f"/parts/api/{pid}/update", _part_payload(part, in_stock=8))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert shop_db.parts.find_one({"_id": part["_id"]})["in_stock"] == 8
    assert '"edit_stock_qty": true' in client.get("/parts/?tab=parts").get_data(as_text=True)


# ── reports are closed without reports.* ────────────────────────────────

def test_reports_require_permissions(client, perm_seed):
    _login_as(client, "mechanic")
    assert client.get("/reports").status_code == 302
    assert client.get("/reports/audit").status_code == 302
    assert client.get("/reports/standard/sales_summary").status_code == 302
    assert client.get("/reports/api/standard/sales_summary").status_code == 403
    assert client.get("/reports/standard/sales_summary/pdf").status_code == 302

    _login_as(client, "viewer")
    assert client.get("/reports").status_code == 200
    assert client.get("/reports/audit").status_code == 200
    # Карточка журнала на странице отчётов видна тем, у кого есть право
    # (user_permissions приходит в шаблон из context processor'а).
    assert "/reports/audit" in client.get("/reports").get_data(as_text=True)
    # viewer: reports.view есть, reports.export — нет
    assert client.get("/reports/standard/sales_summary/pdf").status_code == 302


# ── mechanic mode: no money anywhere ────────────────────────────────────

def test_mechanic_gets_no_costs_from_search_endpoints(client, perm_seed):
    _login_as(client, "mechanic")
    assert client.get("/parts/api/search?q=abc").status_code == 403
    assert _post_json(client, "/dashboard/api/goals", {}).status_code == 403

    resp = client.get("/api/global-search?q=95001")
    assert resp.status_code == 200
    groups = resp.get_json()["results"]
    categories = {g["category"] for g in groups}
    assert "Customers" not in categories and "Parts" not in categories and "Vendors" not in categories
    wo_group = next(g for g in groups if g["category"] == "Work Orders")
    assert "$" not in wo_group["items"][0]["label"]
    assert "Unpaid" not in wo_group["items"][0]["label"]


def test_owner_sees_money_in_global_search(client, perm_seed):
    assert login(client).status_code == 302
    resp = client.get("/api/global-search?q=95001")
    wo_group = next(g for g in resp.get_json()["results"] if g["category"] == "Work Orders")
    assert "$150.00" in wo_group["items"][0]["label"]


# ── settings pages use the keys shown in the role editor ───────────────

def test_settings_routes_use_granular_keys(client, perm_seed):
    _login_as(client, "locations_only")
    assert client.get("/settings/locations").status_code == 200
    assert client.get("/settings/wo_presets").status_code == 302
    assert client.get("/settings/work_orders").status_code == 302
    assert client.get("/settings/parts-settings").status_code == 302
    assert client.get("/settings/pdf-design").status_code == 302
    assert client.get("/settings/users").status_code == 302


def test_portal_send_link_uses_catalog_key(client, perm_seed):
    cid = str(perm_seed["customer"]["_id"])
    _login_as(client, "viewer")
    assert _post_json(client, f"/portal/api/customers/{cid}/send-link", {"emails": ["a@b.co"]}).status_code == 403
    # У владельца ключ customers.manage_portal есть — проверка прав пропускает.
    assert login(client).status_code == 302
    assert _post_json(client, f"/portal/api/customers/{cid}/send-link", {"emails": []}).status_code == 400


# ── escalation: owner accounts, own role, granting beyond yourself ──────

def test_manager_cannot_touch_owner_or_own_role(client, perm_seed, mongo):
    master = mongo["roobico_test_master"]
    owner = perm_seed["owner"]
    me = perm_seed["users"]["manager"]
    _login_as(client, "manager")

    base = {"first_name": "X", "last_name": "Y", "is_active": "on", "pay_type": "salary"}

    # Демотировать владельца — нельзя.
    resp = _post_form(client, f"/settings/users/{owner['_id']}/edit", {**base, "email": OWNER_EMAIL, "role": "manager"})
    assert resp.status_code == 302
    assert master.users.find_one({"_id": owner["_id"]})["role"] == "owner"

    # Сделать себя владельцем — нельзя.
    resp = _post_form(client, f"/settings/users/{me['_id']}/edit", {**base, "email": USERS["manager"], "role": "owner"})
    assert resp.status_code == 302
    assert master.users.find_one({"_id": me["_id"]})["role"] == "manager"

    # Сменить себе роль вообще — нельзя.
    _post_form(client, f"/settings/users/{me['_id']}/edit", {**base, "email": USERS["manager"], "role": "viewer"})
    assert master.users.find_one({"_id": me["_id"]})["role"] == "manager"

    # Создать нового владельца — нельзя.
    _post_form(client, "/settings/users", {**base, "email": "perm-created-owner@test.local", "role": "owner"})
    assert master.users.find_one({"email": "perm-created-owner@test.local"}) is None

    # Деактивировать владельца — нельзя.
    _post_form(client, f"/settings/users/{owner['_id']}/deactivate", {})
    assert master.users.find_one({"_id": owner["_id"]})["is_active"] is True

    # Форма менеджера не предлагает роль Owner; владельцу — предлагает.
    assert 'value="owner"' not in client.get("/settings/users").get_data(as_text=True)
    assert login(client).status_code == 302
    assert 'value="owner"' in client.get("/settings/users").get_data(as_text=True)


def test_overrides_cannot_grant_beyond_actor_or_touch_self_and_owner(client, perm_seed, mongo):
    master = mongo["roobico_test_master"]
    me = perm_seed["users"]["manager"]
    viewer = perm_seed["users"]["viewer"]
    owner = perm_seed["owner"]
    _login_as(client, "manager")

    # Себе — нельзя.
    assert _put_json(client, f"/settings/api/users/{me['_id']}/permissions", {"allow_permissions": ["settings.manage_roles"]}).status_code == 403
    # Владельцу — нельзя.
    assert _put_json(client, f"/settings/api/users/{owner['_id']}/permissions", {"deny_permissions": ["settings.manage_users"]}).status_code == 403
    # Другому — только то, что есть у самого менеджера (billing у manager нет).
    resp = _put_json(client, f"/settings/api/users/{viewer['_id']}/permissions", {"allow_permissions": ["settings.manage_billing"]})
    assert resp.status_code == 403
    assert "settings.manage_billing" in resp.get_json()["error"]
    assert not master.users.find_one({"_id": viewer["_id"]}).get("allow_permissions")
    # Право, которое у менеджера есть — можно.
    resp = _put_json(client, f"/settings/api/users/{viewer['_id']}/permissions", {"allow_permissions": ["parts.edit"]})
    assert resp.status_code == 200
    assert master.users.find_one({"_id": viewer["_id"]})["allow_permissions"] == ["parts.edit"]


def test_roles_cannot_be_given_permissions_actor_lacks(client, perm_seed, mongo):
    tdb = mongo[TENANT_A_DB]
    _login_as(client, "general_manager")  # есть manage_roles, нет manage_billing

    role = tdb.roles.find_one({"key": "locations_only"})
    resp = _put_json(client, f"/settings/api/roles/{role['_id']}", {"permissions": ["settings.view", "settings.manage_billing"]})
    assert resp.status_code == 403
    assert "settings.manage_billing" in resp.get_json()["error"]
    assert tdb.roles.find_one({"_id": role["_id"]})["permissions"] == ["settings.view", "settings.manage_locations"]

    # Убирать права можно всегда, добавлять свои — тоже.
    resp = _put_json(client, f"/settings/api/roles/{role['_id']}", {"permissions": ["settings.view", "settings.manage_locations", "vendors.view"]})
    assert resp.status_code == 200
    resp = _put_json(client, f"/settings/api/roles/{role['_id']}", {"permissions": ["settings.view", "settings.manage_locations"]})
    assert resp.status_code == 200

    resp = _post_json(client, "/settings/api/roles", {"name": "Billing sneak", "permissions": ["settings.manage_billing"]})
    assert resp.status_code == 403
    assert tdb.roles.find_one({"name": "Billing sneak"}) is None


# ── calendar: look vs plan ───────────────────────────────────────────────

def test_calendar_write_routes_need_their_keys(client, perm_seed):
    """Механик видит календарь, но планировать/двигать/удалять не может — и
    страница это знает (флаги для фронта), и API отвечает 403 JSON."""
    _login_as(client, "mechanic")
    assert client.get("/calendar/api/events?start=2030-01-06T00:00:00&end=2030-01-13T00:00:00").status_code == 200
    page = client.get("/calendar")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'data-can-create="0"' in html and 'data-can-edit="0"' in html and 'data-can-delete="0"' in html

    resp = _post_json(client, "/calendar/api/events", {"start_time": "2030-01-07T09:00:00", "end_time": "2030-01-07T10:00:00"})
    assert resp.status_code == 403 and resp.get_json()["error"] == "forbidden"
    assert _put_json(client, f"/calendar/api/events/{ObjectId()}", {"title": "x"}).status_code == 403
    token = get_csrf_token(client)
    assert client.delete(f"/calendar/api/events/{ObjectId()}", headers={"X-CSRFToken": token}).status_code == 403
    assert _put_json(client, "/calendar/api/statuses", {"statuses": []}).status_code == 403

    _login_as(client, "manager")
    html = client.get("/calendar").get_data(as_text=True)
    assert 'data-can-create="1"' in html and 'data-can-edit="1"' in html


def test_every_permission_in_catalog_is_enforced_by_a_route():
    """Каталог прав не должен содержать «декоративных» ключей — каждый
    проверяется хотя бы одним роутом (или является режимным флагом)."""
    import os
    import re

    from app.constants.permissions import PERMISSIONS

    root = os.path.join(os.path.dirname(__file__), "..", "app", "blueprints")
    src = ""
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.endswith(".py"):
                src += open(os.path.join(dirpath, f), encoding="utf-8").read()
    # Режимные ключи проверяются внутри хендлеров через has_permission/is_mechanic_mode.
    mode_keys = {"parts.view_costs", "work_orders.view_costs", "work_orders.manage_email_contacts",
                 "work_orders.edit_time_logs", "attachments.view",
                 # reports.view_financial — выбирается по ключу отчёта внутри
                 # хендлера (_report_permission_for); settings.manage_pay —
                 # поля зарплаты внутри формы пользователя.
                 "reports.view_financial", "settings.manage_pay"}
    missing = []
    for key in PERMISSIONS:
        if key in mode_keys:
            continue
        if not re.search(r'permission_required(?:_any)?\([^)]*"' + re.escape(key) + '"', src):
            missing.append(key)
    assert not missing, f"permissions without a route check: {missing}"
