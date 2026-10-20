"""
Аудит прав доступа (вторая волна): деньги закрыты ключами *.view_costs на
всех страницах и API (дашборд, балансы, отчёты, запчасти), финансовые и
зарплатный отчёты — своими ключами, мобильные роуты проверяют те же
ключи, что и веб, кадровик не может раздать роль шире своей или поднять
себе оклад, а фронт прячет кнопки, на которые у роли нет прав.
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
    "viewer": "audit-viewer@test.local",
    "mechanic": "audit-mechanic@test.local",
    "senior_mechanic": "audit-senior@test.local",
    "manager": "audit-manager@test.local",
    "parts_editor": "audit-parts-editor@test.local",
    "estimator": "audit-estimator@test.local",
    "hr_only": "audit-hr@test.local",
}
INACTIVE_OWNER_EMAIL = "audit-inactive-owner@test.local"

CUSTOM_ROLES = {
    # parts.edit без parts.view_costs: правка карточки не должна обнулять cost
    "parts_editor": ["parts.view", "parts.edit", "parts_orders.view"],
    # видит деньги и создаёт WO, но не редактирует/не меняет статус
    "estimator": ["work_orders.view", "work_orders.create", "work_orders.view_costs", "customers.view"],
    # кадровик: только пользователи, без ролей/зарплаты
    "hr_only": ["settings.view", "settings.manage_users"],
}


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture(scope="module")
def audit_seed(app, seed, mongo):
    from app.constants.permissions import build_default_roles

    master = mongo["roobico_test_master"]
    tdb = mongo[TENANT_A_DB]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = _now()

    for role in build_default_roles():
        if role["key"] == "owner":
            continue
        tdb.roles.update_one({"key": role["key"]}, {"$set": role}, upsert=True)
    for key, perms in CUSTOM_ROLES.items():
        tdb.roles.update_one(
            {"key": key},
            {"$set": {"key": key, "name": key.replace("_", " ").title(), "is_system": False, "permissions": perms}},
            upsert=True,
        )

    users = {}
    for role, email in USERS.items():
        doc = {
            "_id": ObjectId(), "email": email, "password_hash": generate_password_hash(PASSWORD),
            "first_name": role.title(), "last_name": "Audit", "name": f"{role.title()} Audit",
            "is_active": True, "tenant_id": seed["tenant_a"]["_id"],
            "shop_ids": [str(shop_a["_id"])], "role": role, "created_at": now,
            "pay_type": "salary", "salary_amount": 1000.0,
        }
        master.users.insert_one(doc)
        users[role] = doc

    inactive_owner = {
        "_id": ObjectId(), "email": INACTIVE_OWNER_EMAIL, "password_hash": generate_password_hash(PASSWORD),
        "first_name": "Former", "last_name": "Owner", "name": "Former Owner", "is_active": False,
        "tenant_id": seed["tenant_a"]["_id"], "shop_ids": [str(shop_a["_id"])], "role": "owner", "created_at": now,
    }
    master.users.insert_one(inactive_owner)

    # Вторая локация тенанта A (без собственной базы — только документ в master).
    shop_a2 = {
        "_id": ObjectId(), "tenant_id": seed["tenant_a"]["_id"], "name": "Shop A2",
        "db_name": SHOP_A_DB, "is_active": True, "created_at": now,
    }
    master.shops.insert_one(shop_a2)

    customer = {"_id": ObjectId(), "shop_id": shop_a["_id"], "company_name": "Audit Fleet", "is_active": True,
                "address": "1 Audit St", "created_at": now, "updated_at": now}
    shop_db.customers.insert_one(customer)
    unit = {"_id": ObjectId(), "shop_id": shop_a["_id"], "customer_id": customer["_id"], "unit_number": "AUD-1",
            "vin": "", "is_active": True, "created_at": now, "updated_at": now}
    shop_db.units.insert_one(unit)
    from app.utils.parts_search import build_parts_search_terms

    part = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "part_number": "AUDIT-COST-1", "description": "Audit cost part",
        "in_stock": 3, "average_cost": 42.5, "last_price": 40.0, "is_active": True, "do_not_track_inventory": False,
        "search_terms": build_parts_search_terms("AUDIT-COST-1", "Audit cost part", ""),
        "created_at": now, "updated_at": now,
    }
    shop_db.parts.insert_one(part)
    wo = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "wo_number": 97001, "customer_id": customer["_id"],
        "unit_id": unit["_id"], "status": "open", "is_active": True,
        "labors": [{"labor_id": str(ObjectId()), "labor": {"description": "Audit labor", "hours": 1, "labor_full_total": 100.0},
                    "parts": [{"part_number": "AUDIT-COST-1", "description": "Audit cost part", "qty": 1, "price": 50.0}]}],
        "totals": {"grand_total": 150.0}, "created_at": now, "updated_at": now,
    }
    shop_db.work_orders.insert_one(wo)
    paid_wo = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "wo_number": 97002, "customer_id": customer["_id"],
        "unit_id": unit["_id"], "status": "paid", "is_active": True, "labors": [],
        "totals": {"grand_total": 10.0}, "created_at": now, "updated_at": now,
    }
    shop_db.work_orders.insert_one(paid_wo)
    payment = {"_id": ObjectId(), "shop_id": shop_a["_id"], "work_order_id": paid_wo["_id"], "amount": 10.0,
               "payment_method": "cash", "created_at": now}
    shop_db.work_order_payments.insert_one(payment)

    yield {
        "users": users, "owner": seed["owner"], "inactive_owner": inactive_owner, "shop_a": shop_a, "shop_a2": shop_a2,
        "customer": customer, "unit": unit, "part": part, "wo": wo, "paid_wo": paid_wo,
    }

    master.users.delete_many({"email": {"$in": list(USERS.values()) + [INACTIVE_OWNER_EMAIL, "audit-created@test.local"]}})
    master.shops.delete_one({"_id": shop_a2["_id"]})
    for key in CUSTOM_ROLES:
        tdb.roles.delete_one({"key": key})
    shop_db.work_orders.delete_many({"_id": {"$in": [wo["_id"], paid_wo["_id"]]}})
    shop_db.work_order_payments.delete_many({"work_order_id": paid_wo["_id"]})
    shop_db.customers.delete_one({"_id": customer["_id"]})
    shop_db.units.delete_one({"_id": unit["_id"]})
    shop_db.parts.delete_one({"_id": part["_id"]})


def _login_as(client, role):
    resp = login(client, email=USERS[role], password=PASSWORD)
    assert resp.status_code == 302, resp.get_data(as_text=True)


def _post_json(client, url, payload=None):
    token = get_csrf_token(client)
    return client.post(url, json=payload or {}, headers={"X-CSRFToken": token})


def _post_form(client, url, data):
    data = dict(data)
    data["csrf_token"] = get_csrf_token(client)
    return client.post(url, data=data)


# ── WO details page: view vs create ─────────────────────────────────────

def test_viewer_opens_existing_wo_read_only_but_cannot_create(client, audit_seed):
    _login_as(client, "viewer")
    wo_id = str(audit_seed["wo"]["_id"])
    resp = client.get(f"/work_orders/details?work_order_id={wo_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Work Order #97001" in html
    # Действий, на которые нет прав, в разметке нет вовсе.
    for btn_id in ("editWorkOrderBtn", "createWorkOrderGroup", "saveWorkOrderGroup", "paidWorkOrderBtn",
                   "deleteWorkOrderBtn", "emailWorkOrderBtn", "downloadPdfBtn", "recognizeWoBtn"):
        assert f'id="{btn_id}"' not in html, btn_id
    # Форма нового WO — только с work_orders.create.
    resp = client.get("/work_orders/details")
    assert resp.status_code == 302

    # Список WO: нет Delete/Bulk Payment/Create, ссылка называется Open.
    html = client.get("/work_orders").get_data(as_text=True)
    assert "js-delete-work-order" not in html
    assert 'id="bulkPaymentBtn"' not in html
    assert "Create Work Order" not in html
    assert ">Open<" in html.replace("\n", "").replace(" ", "") or "Open" in html

    assert login(client).status_code == 302
    html = client.get(f"/work_orders/details?work_order_id={wo_id}").get_data(as_text=True)
    for btn_id in ("editWorkOrderBtn", "paidWorkOrderBtn", "deleteWorkOrderBtn", "emailWorkOrderBtn", "downloadPdfBtn"):
        assert f'id="{btn_id}"' in html, btn_id


# ── no money without view_costs (senior mechanic) ───────────────────────

def test_senior_mechanic_gets_no_money_anywhere(client, audit_seed):
    _login_as(client, "senior_mechanic")
    cid = str(audit_seed["customer"]["_id"])
    wo_id = str(audit_seed["wo"]["_id"])

    # Dashboard: только часы, денежные блоки не рендерятся и закрыты по API.
    html = client.get("/dashboard").get_data(as_text=True)
    assert 'data-block="mechanic-hours"' in html
    for block in ("wo-money", "parts-orders", "goal-progress", "outstanding-balance"):
        assert f'data-block="{block}"' not in html, block
    assert 'id="dashGoalsEditBtn"' not in html
    assert client.get("/dashboard/api/metrics/wo-money").status_code == 403
    assert client.get("/dashboard/api/metrics/mechanic-hours").status_code == 200
    full = client.get("/dashboard/api/metrics").get_json()
    assert full["ok"] and "period_money_total" not in full

    # Балансы клиентов — деньги по WO.
    assert client.get(f"/customers/api/balances?ids={cid}").status_code == 403
    html = client.get("/customers").get_data(as_text=True)
    assert "Current Balance" not in html and "js-customer-balance" not in html
    html = client.get(f"/customers/{cid}").get_data(as_text=True)
    assert "Current Balance" not in html and "Grand Total" not in html
    assert "tab=payments" not in html and "tab=estimates" not in html
    # Вкладка платежей недоступна — отдаётся список WO.
    html = client.get(f"/customers/{cid}?tab=payments").get_data(as_text=True)
    assert "Search payments" not in html
    labors = client.get(f"/customers/api/work_orders/{wo_id}/labors").get_json()
    assert labors["ok"] and labors["show_costs"] is False
    assert "labor_total" not in labors["labors"][0] and "price" not in labors["labors"][0]["parts"][0]

    # Отчёты: финансовые закрыты, часы механиков открыты, зарплата закрыта.
    assert client.get("/reports").status_code == 200
    html = client.get("/reports").get_data(as_text=True)
    assert "Mechanic Hours" in html and "Sales Summary" not in html and "Timecard" not in html
    assert client.get("/reports/standard/sales_summary").status_code == 302
    resp = client.get("/reports/api/standard/sales_summary")
    assert resp.status_code == 403 and resp.get_json()["required"] == "reports.view_financial"
    assert client.get("/reports/api/standard/mechanic_hours").status_code == 200
    assert client.get("/reports/timecard-salary").status_code == 302

    # Запчасти: parts.view_costs нет — себестоимость не уходит никуда.
    html = client.get("/parts/?tab=parts").get_data(as_text=True)
    assert "Avg cost" not in html and "Total inventory cost" not in html
    assert '"view_costs": false' in html
    pid = str(audit_seed["part"]["_id"])
    item = client.get(f"/parts/api/{pid}").get_json()["item"]
    assert "average_cost" not in item and "last_price" not in item
    found = client.get("/parts/api/search?q=audit").get_json()["items"]
    assert found and all("average_cost" not in it and "last_price" not in it for it in found)
    history = client.get(f"/parts/api/{pid}/history").get_json()
    assert history["ok"] and all("price" not in o for o in history["orders"])


def test_owner_still_sees_money(client, audit_seed):
    assert login(client).status_code == 302
    cid = str(audit_seed["customer"]["_id"])
    assert client.get(f"/customers/api/balances?ids={cid}").status_code == 200
    html = client.get("/dashboard").get_data(as_text=True)
    assert 'data-block="wo-money"' in html and 'id="dashGoalsEditBtn"' in html
    assert client.get("/reports/api/standard/sales_summary").status_code == 200
    assert client.get("/reports/timecard-salary").status_code == 200
    pid = str(audit_seed["part"]["_id"])
    assert client.get(f"/parts/api/{pid}").get_json()["item"]["average_cost"] == 42.5


def test_vendor_balances_need_parts_orders_view(client, audit_seed):
    _login_as(client, "mechanic")  # vendors.view нет вовсе → 403 по любому ключу
    assert client.get("/vendors/api/balances?ids=" + str(ObjectId())).status_code == 403
    _login_as(client, "senior_mechanic")  # parts_orders.view есть
    assert client.get("/vendors/api/balances?ids=" + str(ObjectId())).status_code == 200
    html = client.get("/vendors/").get_data(as_text=True)
    assert "Add Vendor" not in html  # vendors.create нет


# ── parts.edit without parts.view_costs keeps the cost untouched ────────

def test_parts_update_without_view_costs_preserves_average_cost(client, audit_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    part = audit_seed["part"]
    pid = str(part["_id"])
    _login_as(client, "parts_editor")
    payload = {
        "part_number": part["part_number"], "description": "Renamed without costs", "reference": "",
        "vendor_id": "", "category_id": "", "location_id": "", "in_stock": part["in_stock"],
        "average_cost": 0, "do_not_track_inventory": False, "has_selling_price": False, "selling_price": 0,
        "core_has_charge": False, "core_cost": 0, "misc_has_charge": False, "misc_charges": [],
    }
    resp = _post_json(client, f"/parts/api/{pid}/update", payload)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    fresh = shop_db.parts.find_one({"_id": part["_id"]})
    assert fresh["description"] == "Renamed without costs"
    assert fresh["average_cost"] == 42.5
    # Страница: кнопка Edit есть, Deactivate (parts.delete) — нет, Add Part — нет.
    html = client.get("/parts/?tab=parts").get_data(as_text=True)
    assert "editPartBtn" in html and "Deactivate" not in html and "Add Part" not in html


@pytest.mark.parametrize("role", ["senior_mechanic", "parts_editor", "viewer"])
def test_parts_tabs_render_for_limited_roles(client, audit_seed, role):
    """Все вкладки Parts рендерятся для ролей без части ключей (ветки шаблона
    с {% if %} по правам не ломают страницу)."""
    _login_as(client, role)
    for tab in ("parts", "orders", "payments", "cores", "cores_returns", "stocktakes"):
        resp = client.get(f"/parts/?tab={tab}")
        assert resp.status_code == 200, (role, tab, resp.status_code)
    html = client.get("/parts/?tab=stocktakes").get_data(as_text=True)
    assert "New Stocktake" not in html  # parts.adjust_stock нет у всех трёх


# ── mobile API uses the catalog keys ─────────────────────────────────────

def test_mobile_routes_check_catalog_keys(client, audit_seed):
    uid = str(audit_seed["unit"]["_id"])
    _login_as(client, "viewer")
    resp = _post_json(client, "/api/mobile/customers", {"company_name": "X", "address": "1 Long Street"})
    assert resp.status_code == 403 and resp.get_json()["required"] == "customers.create"
    resp = _post_json(client, "/api/mobile/vendors", {"name": "X"})
    assert resp.status_code == 403 and resp.get_json()["required"] == "vendors.create"
    resp = _post_json(client, f"/api/mobile/units/{uid}/update", {"unit_number": "AUD-2"})
    assert resp.status_code == 403 and resp.get_json()["required"] == "customers.manage_units"
    resp = _post_json(client, "/api/mobile/units", {"customer_id": str(audit_seed["customer"]["_id"]), "unit_number": "Z"})
    assert resp.status_code == 403

    # Механик (work_orders.create) по-прежнему заводит юнит с мобилки.
    _login_as(client, "mechanic")
    resp = _post_json(client, "/api/mobile/units", {"customer_id": str(ObjectId()), "unit_number": "Z"})
    assert resp.status_code == 404  # прошёл проверку прав, клиента нет


# ── status & edit guards for money-seeing roles ─────────────────────────

def test_paid_wo_cannot_leave_paid_without_change_status(client, audit_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    paid_id = str(audit_seed["paid_wo"]["_id"])
    _login_as(client, "estimator")  # create + view_costs, без change_status
    resp = _post_json(client, f"/work_orders/api/work_orders/{paid_id}/status", {"status": "in_progress"})
    assert resp.status_code == 403
    assert shop_db.work_orders.find_one({"_id": audit_seed["paid_wo"]["_id"]})["status"] == "paid"
    assert shop_db.work_order_payments.count_documents({"work_order_id": audit_seed["paid_wo"]["_id"]}) == 1


def test_mechanic_update_endpoints_require_edit_for_managers(client, audit_seed):
    wo_id = str(audit_seed["wo"]["_id"])
    _login_as(client, "estimator")  # видит деньги, create есть, edit нет
    resp = _post_json(client, f"/work_orders/api/mechanic/work_orders/{wo_id}", {"labors": []})
    assert resp.status_code == 403 and resp.get_json()["required"] == "work_orders.edit"
    resp = _post_json(client, f"/api/mobile/work_orders/{wo_id}", {"labors": [{"description": "x"}]})
    assert resp.status_code == 403 and resp.get_json()["required"] == "work_orders.edit"
    resp = _post_json(client, f"/work_orders/api/work_orders/{wo_id}/update", {"labors": []})
    assert resp.status_code == 403


# ── users: role escalation, pay, owner activation ───────────────────────

def test_manage_users_cannot_assign_role_beyond_own_permissions(client, audit_seed, mongo):
    master = mongo["roobico_test_master"]
    viewer = audit_seed["users"]["viewer"]
    base = {"first_name": "Viewer", "last_name": "Audit", "email": USERS["viewer"], "is_active": "on"}

    _login_as(client, "manager")  # нет manage_roles → general_manager шире
    _post_form(client, f"/settings/users/{viewer['_id']}/edit", {**base, "role": "general_manager"})
    assert master.users.find_one({"_id": viewer["_id"]})["role"] == "viewer"
    _post_form(client, f"/settings/users/{viewer['_id']}/edit", {**base, "role": "senior_mechanic"})
    assert master.users.find_one({"_id": viewer["_id"]})["role"] == "senior_mechanic"
    _post_form(client, f"/settings/users/{viewer['_id']}/edit", {**base, "role": "viewer"})
    assert master.users.find_one({"_id": viewer["_id"]})["role"] == "viewer"

    _post_form(client, "/settings/users", {"first_name": "New", "last_name": "GM", "email": "audit-created@test.local",
                                           "role": "general_manager", "is_active": "on"})
    assert master.users.find_one({"email": "audit-created@test.local"}) is None

    # Кадровик без прав на роли вообще не может повысить никого выше себя.
    _login_as(client, "hr_only")
    _post_form(client, f"/settings/users/{viewer['_id']}/edit", {**base, "role": "manager"})
    assert master.users.find_one({"_id": viewer["_id"]})["role"] == "viewer"
    # Правка профиля без смены роли — можно, даже если роль шире (manager).
    manager = audit_seed["users"]["manager"]
    _post_form(client, f"/settings/users/{manager['_id']}/edit",
               {"first_name": "Renamed", "last_name": "Manager", "email": USERS["manager"], "role": "manager", "is_active": "on"})
    assert master.users.find_one({"_id": manager["_id"]})["first_name"] == "Renamed"


def test_pay_fields_require_manage_pay_and_never_for_self(client, audit_seed, mongo):
    master = mongo["roobico_test_master"]
    viewer = audit_seed["users"]["viewer"]
    manager = audit_seed["users"]["manager"]
    base = {"first_name": "Viewer", "last_name": "Audit", "email": USERS["viewer"], "role": "viewer", "is_active": "on"}
    # Предыдущие тесты правили профиль формой без полей оплаты — вернём исходник.
    master.users.update_one({"_id": viewer["_id"]}, {"$set": {"pay_type": "salary", "salary_amount": 1000.0}})

    _login_as(client, "hr_only")  # settings.manage_pay нет
    html = client.get("/settings/users").get_data(as_text=True)
    assert 'id="edit_salary_amount"' not in html and "data-salary-amount" not in html
    _post_form(client, f"/settings/users/{viewer['_id']}/edit", {**base, "pay_type": "hourly", "salary_amount": ""})
    fresh = master.users.find_one({"_id": viewer["_id"]})
    assert fresh["pay_type"] == "salary" and fresh["salary_amount"] == 1000.0

    _login_as(client, "manager")  # manage_pay есть
    html = client.get("/settings/users").get_data(as_text=True)
    assert 'id="edit_salary_amount"' in html
    _post_form(client, f"/settings/users/{viewer['_id']}/edit", {**base, "pay_type": "salary", "salary_amount": "1500"})
    assert master.users.find_one({"_id": viewer["_id"]})["salary_amount"] == 1500.0
    # Себе — никогда.
    _post_form(client, f"/settings/users/{manager['_id']}/edit",
               {"first_name": "Manager", "last_name": "Audit", "email": USERS["manager"], "role": "manager",
                "is_active": "on", "pay_type": "salary", "salary_amount": "99999"})
    assert master.users.find_one({"_id": manager["_id"]})["salary_amount"] == 1000.0


def test_non_owner_cannot_activate_owner_account(client, audit_seed, mongo):
    master = mongo["roobico_test_master"]
    target = audit_seed["inactive_owner"]
    _login_as(client, "manager")
    _post_form(client, f"/settings/users/{target['_id']}/activate", {})
    assert master.users.find_one({"_id": target["_id"]})["is_active"] is False
    assert login(client).status_code == 302
    _post_form(client, f"/settings/users/{target['_id']}/activate", {})
    assert master.users.find_one({"_id": target["_id"]})["is_active"] is True
    master.users.update_one({"_id": target["_id"]}, {"$set": {"is_active": False}})


# ── shop access follows the user document, not the login-time session ──

def test_revoked_shop_access_applies_without_relogin(client, audit_seed, mongo):
    master = mongo["roobico_test_master"]
    viewer = audit_seed["users"]["viewer"]
    shop_a, shop_a2 = audit_seed["shop_a"], audit_seed["shop_a2"]
    _login_as(client, "viewer")
    with client.session_transaction() as sess:
        assert sess["shop_id"] == str(shop_a["_id"])
    try:
        master.users.update_one({"_id": viewer["_id"]}, {"$set": {"shop_ids": [str(shop_a2["_id"])]}})
        assert client.get("/customers").status_code == 200
        with client.session_transaction() as sess:
            assert sess["shop_ids"] == [str(shop_a2["_id"])]
            assert sess["shop_id"] == str(shop_a2["_id"])
        # Старую локацию руками выбрать нельзя.
        resp = _post_form(client, "/session/active-shop", {"shop_id": str(shop_a["_id"])})
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess["shop_id"] == str(shop_a2["_id"])
    finally:
        master.users.update_one({"_id": viewer["_id"]}, {"$set": {"shop_ids": [str(shop_a["_id"])]}})


# ── layout: permissions for JS, attachments buttons ─────────────────────

def test_layout_exposes_permissions_and_hides_attachment_buttons(client, audit_seed):
    cid = str(audit_seed["customer"]["_id"])
    uid = str(audit_seed["unit"]["_id"])
    _login_as(client, "viewer")  # attachments.view есть, upload/delete — нет
    html = client.get(f"/customers/{cid}?tab=details").get_data(as_text=True)
    assert 'id="userPermissionsData"' in html and "js/permissions.js" in html
    assert "Portal Link" not in html  # customers.manage_portal нет
    assert 'type="submit" class="btn btn-primary">Save</button>' not in html  # customers.edit нет
    # Блок вложений на странице юнита: без attachments.upload кнопки Upload нет,
    # без customers.manage_units — нет Save/Deactivate.
    save_btn = '<button type="submit" class="btn btn-primary btn-sm">Save Changes</button>'
    deactivate_btn = 'data-bs-target="#deactivateUnitModal"'
    html = client.get(f"/customers/{cid}/units/{uid}?tab=details").get_data(as_text=True)
    assert "attachments-block" in html and "att-upload-btn" not in html
    assert save_btn not in html and deactivate_btn not in html
    assert login(client).status_code == 302
    html = client.get(f"/customers/{cid}/units/{uid}?tab=details").get_data(as_text=True)
    assert "att-upload-btn" in html and save_btn in html and deactivate_btn in html
    assert "Portal Link" in client.get(f"/customers/{cid}").get_data(as_text=True)
