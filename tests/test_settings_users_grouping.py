"""Settings → Users & Roles: список пользователей сгруппирован по ролям.

Чистые тесты каталога ролей и группировки (app/utils/user_roles.py) и
рендер страницы: порядок групп (системные по иерархии → кастомные A→Z →
«Role not found» → Inactive), Inactive только при статусе all/inactive,
подписи ролей вместо ключей, счётчики в заголовках, сортировка внутри
групп, селекты ролей из каталога (Owner — только владельцу, кастомные
роли назначаемы), поиск по названию роли.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from app.constants.permissions import (
    SYSTEM_ROLE_KEYS,
    SYSTEM_ROLE_LABELS,
    SYSTEM_ROLE_ORDER,
    build_default_roles,
)
from app.utils.user_roles import (
    group_users_by_role,
    load_role_catalog,
    normalize_role_key,
    role_keys_matching,
    role_label_from_key,
)
from tests.conftest import TENANT_A_DB, TEST_MASTER, TEST_MONGO_URI, login

PASSWORD = "password123"
EMAIL_PREFIX = "grpusr-"


# ── чистые тесты ────────────────────────────────────────────────────────

class _FakeRoles:
    def __init__(self, docs):
        self._docs = docs

    def find(self, *args, **kwargs):
        return list(self._docs)


class _FakeTenantDb:
    def __init__(self, docs):
        self.roles = _FakeRoles(docs)


def _u(role, name="", email=None, **extra):
    doc = {"role": role, "name": name, "email": email or f"{(name or str(role)).lower().replace(' ', '.')}@x.test",
           "is_active": True}
    doc.update(extra)
    return doc


def test_system_role_order_matches_default_roles():
    defaults = build_default_roles()
    assert list(SYSTEM_ROLE_ORDER) == [r["key"] for r in defaults]
    assert set(SYSTEM_ROLE_ORDER) == SYSTEM_ROLE_KEYS
    assert SYSTEM_ROLE_LABELS == {r["key"]: r["name"] for r in defaults}


def test_normalize_role_key_defaults_to_viewer():
    assert normalize_role_key({}) == "viewer"
    assert normalize_role_key(None) == "viewer"
    assert normalize_role_key({"role": None}) == "viewer"
    assert normalize_role_key({"role": "   "}) == "viewer"
    assert normalize_role_key({"role": " Senior_Mechanic "}) == "senior_mechanic"


def test_role_label_from_key():
    assert role_label_from_key("senior_mechanic") == "Senior mechanic"
    assert role_label_from_key("old__custom_role") == "Old custom role"
    assert role_label_from_key("") == "—"


def test_catalog_without_tenant_db_is_builtin_order():
    catalog = load_role_catalog(None)
    assert [r["key"] for r in catalog] == list(SYSTEM_ROLE_ORDER)
    assert [r["label"] for r in catalog] == [SYSTEM_ROLE_LABELS[k] for k in SYSTEM_ROLE_ORDER]
    assert {r["kind"] for r in catalog} == {"system"}


def test_catalog_uses_tenant_names_and_sorts_custom_roles():
    catalog = load_role_catalog(_FakeTenantDb([
        {"key": "zz_crew", "name": "Crew lead"},
        {"key": "manager", "name": "Service  advisor"},  # системную роль переименовали
        {"key": "aa_hr", "name": "People admin"},
        {"key": "no_name"},                               # без имени — подпись из ключа
        {"key": "Bad Key", "name": "Broken"},             # неканоничный ключ — проверка прав его не найдёт
        {"key": None, "name": "Nothing"},
    ]))
    keys = [r["key"] for r in catalog]
    # Системные роли — всегда и по иерархии, даже без документов (owner здесь нет).
    assert keys[:7] == list(SYSTEM_ROLE_ORDER)
    # Кастомные — A→Z по названию, а не по ключу: Crew lead, No name, People admin.
    assert keys[7:] == ["zz_crew", "no_name", "aa_hr"]
    by_key = {r["key"]: r for r in catalog}
    assert by_key["manager"]["label"] == "Service advisor"
    assert by_key["manager"]["kind"] == "system"
    assert by_key["owner"]["label"] == "Owner"
    assert by_key["no_name"]["label"] == "No name"
    assert by_key["zz_crew"]["kind"] == "custom"


def test_group_order_inactive_last_and_empty_groups_omitted():
    catalog = load_role_catalog(_FakeTenantDb([
        {"key": "zz_crew", "name": "Crew lead"},
        {"key": "aa_hr", "name": "People admin"},
    ]))
    users = [
        _u("viewer", "Vic"),
        _u("aa_hr", "Hanna"),
        _u("mechanic", "Mike", is_active=False),
        _u("zeta_gone", "Zack"),
        _u("zz_crew", "Carl"),
        _u("MECHANIC ", "Max"),           # ключ нормализуется как в проверке прав
        _u(None, "Nobody"),               # без роли — viewer
        _u("owner", "Olga"),
        _u("alpha_gone", "Al"),
        _u("general_manager", "Gina", is_active=False),
    ]
    groups = group_users_by_role(users, catalog)
    assert [(g["key"], g["kind"]) for g in groups] == [
        ("owner", "system"),
        ("mechanic", "system"),
        ("viewer", "system"),
        ("zz_crew", "custom"),
        ("aa_hr", "custom"),
        ("alpha_gone", "missing"),
        ("zeta_gone", "missing"),
        ("inactive", "inactive"),
    ]
    by_key = {g["key"]: g for g in groups}
    assert [u["name"] for u in by_key["mechanic"]["users"]] == ["Max"]
    assert [u["name"] for u in by_key["viewer"]["users"]] == ["Nobody", "Vic"]
    # Неактивные — одной группой, не по ролям; их роль подписана для колонки Role.
    assert [u["name"] for u in by_key["inactive"]["users"]] == ["Gina", "Mike"]
    assert [u["role_label"] for u in by_key["inactive"]["users"]] == ["General manager", "Mechanic"]
    assert by_key["alpha_gone"]["label"] == "Alpha gone"
    assert by_key["zz_crew"]["label"] == "Crew lead"
    assert all("role_label" in u for u in users)


def test_invited_count_and_name_sort_within_group():
    users = [
        _u("mechanic", "zed Smith", "zed@x.test"),
        _u("mechanic", "Alice Brown", "alice@x.test", invite_pending=True),
        _u("mechanic", "", "b-noname@x.test"),
        _u("mechanic", "", "a-noname@x.test"),
        _u("mechanic", "", "bob@x.test", first_name="Bob", last_name="Stone"),  # name пуст — first + last
        _u("mechanic", "Ivan Invited", "ivan@x.test", invite_pending=True, is_active=False),
    ]
    groups = group_users_by_role(users, load_role_catalog(None))
    mech, inactive = groups
    assert mech["key"] == "mechanic" and inactive["key"] == "inactive"
    # Безымянные — в конце, между собой по email; регистр не важен.
    assert [u["email"] for u in mech["users"]] == [
        "alice@x.test", "bob@x.test", "zed@x.test", "a-noname@x.test", "b-noname@x.test",
    ]
    # Приглашённый остаётся в группе своей роли; неактивное приглашение не считается.
    assert mech["invited_count"] == 1
    assert inactive["invited_count"] == 0


def test_keep_order_preserves_input_order():
    users = [_u("viewer", "Zoe"), _u("viewer", "Adam"), _u("viewer", "Mia")]
    groups = group_users_by_role(users, load_role_catalog(None), keep_order=True)
    assert [u["name"] for u in groups[0]["users"]] == ["Zoe", "Adam", "Mia"]


def test_role_keys_matching_by_label():
    catalog = load_role_catalog(_FakeTenantDb([
        {"key": "manager", "name": "Service advisor"},
        {"key": "zz_crew", "name": "Crew lead"},
    ]))
    assert role_keys_matching(catalog, "parts  MANAGER") == ["parts_manager"]
    assert role_keys_matching(catalog, "advisor") == ["manager"]
    assert role_keys_matching(catalog, "Crew") == ["zz_crew"]
    assert set(role_keys_matching(catalog, "mechanic")) == {"senior_mechanic", "mechanic"}
    assert role_keys_matching(catalog, "   ") == []
    assert role_keys_matching(catalog, "grpusr") == []


# ── рендер страницы ─────────────────────────────────────────────────────

CUSTOM_ROLES = {
    # Ключи «наоборот» к названиям: группы должны идти по названию (Crew < People).
    "grp_z_crew": {"name": "Crew lead", "permissions": ["work_orders.view"]},
    "grp_a_hr": {"name": "People admin", "permissions": ["settings.view", "settings.manage_users"]},
}

TEST_USERS = [
    # (email suffix, role, name, extra)
    ("owner", "owner", "Olivia Grpusr", {}),
    ("manager", "manager", "Mark Grpusr", {}),
    ("senior", "senior_mechanic", "Sam Grpusr", {}),
    ("mech-zed", "mechanic", "Zed Grpusr", {}),
    ("mech-alice", "mechanic", "alice Grpusr", {}),
    ("mech-invited", "mechanic", "Ivan Grpusr", {"invite_pending": True}),
    ("crew", "grp_z_crew", "Carl Grpusr", {}),
    ("hr", "grp_a_hr", "Hanna Grpusr", {}),
    ("gone", "grp_gone", "Gus Grpusr", {}),  # роли нет в tenant.roles
    ("inactive", "senior_mechanic", "Ina Grpusr", {"is_active": False}),
]

ACTIVE_ORDER = ["owner", "manager", "senior_mechanic", "mechanic", "grp_z_crew", "grp_a_hr", "grp_gone"]


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def grouping_env(app, seed, mongo):
    master = mongo[TEST_MASTER]
    tdb = mongo[TENANT_A_DB]
    now = datetime.now(timezone.utc)
    for key, spec in CUSTOM_ROLES.items():
        tdb.roles.update_one(
            {"key": key},
            {"$set": {"key": key, "name": spec["name"], "permissions": spec["permissions"],
                      "is_system": False, "is_protected": False}},
            upsert=True,
        )
    docs = []
    for suffix, role, name, extra in TEST_USERS:
        first, last = name.split(" ", 1)
        doc = {
            "_id": ObjectId(), "email": f"{EMAIL_PREFIX}{suffix}@test.local",
            "password_hash": generate_password_hash(PASSWORD),
            "first_name": first, "last_name": last, "name": name, "role": role,
            "is_active": True, "tenant_id": seed["tenant_a"]["_id"],
            "shop_ids": [str(seed["shop_a"]["_id"])], "created_at": now,
        }
        doc.update(extra)
        docs.append(doc)
    master.users.insert_many(docs)

    yield {"users": {d["email"]: d for d in docs}}

    master.users.delete_many({"email": {"$regex": f"^{EMAIL_PREFIX}"}})
    tdb.roles.delete_many({"key": {"$in": list(CUSTOM_ROLES)}})


def _page(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, url
    return resp.get_data(as_text=True)


def _groups(html):
    return re.findall(r'<tbody data-role-group="([^"]+)"', html)


def _group_html(html, key):
    start = html.index(f'<tbody data-role-group="{key}"')
    return html[start:html.index("</tbody>", start)]


def _flat(html):
    return re.sub(r"\s+", " ", html)


def test_groups_follow_role_hierarchy_and_status_filter(client, grouping_env):
    assert login(client).status_code == 302

    html = _page(client, "/settings/users?q=grpusr&status=all")
    assert _groups(html) == ACTIVE_ORDER + ["inactive"]
    assert 'data-role-group="inactive" data-group-kind="inactive"' in html
    assert 'data-role-group="grp_gone" data-group-kind="missing"' in html
    assert 'data-role-group="grp_z_crew" data-group-kind="custom"' in html

    # По умолчанию (active) неактивных нет — ни группы, ни строки.
    html = _page(client, "/settings/users?q=grpusr")
    assert _groups(html) == ACTIVE_ORDER
    assert f"{EMAIL_PREFIX}inactive@test.local" not in html

    # Только неактивные — одна группа; роль человека видна в колонке Role.
    html = _page(client, "/settings/users?q=grpusr&status=inactive")
    assert _groups(html) == ["inactive"]
    inactive = _group_html(html, "inactive")
    assert f"{EMAIL_PREFIX}inactive@test.local" in inactive
    assert '<span class="badge text-bg-secondary">Senior mechanic</span>' in inactive
    assert "/activate" in inactive


def test_group_headers_counts_and_role_labels(client, grouping_env):
    assert login(client).status_code == 302
    html = _page(client, "/settings/users?q=grpusr&status=all")
    flat = _flat(html)

    assert '<tr class="table-section-row"> <td colspan="5">' in flat
    assert '<span class="fw-semibold">Mechanic</span> · 3 · 1 invited </td>' in flat
    assert '<span class="fw-semibold">Senior mechanic</span> · 1 </td>' in flat
    assert '<span class="fw-semibold">Crew lead</span> · 1 · custom role </td>' in flat
    assert '<span class="fw-semibold">Grp gone</span> · 1 <span class="badge text-bg-warning ms-1"' in flat
    assert "Role not found" in flat
    assert '<span class="fw-semibold">Inactive</span> · 1 </td>' in flat

    # Колонка Role — названия ролей, не ключи; Edit по-прежнему несёт ключ.
    assert '<span class="badge text-bg-secondary">People admin</span>' in html
    assert ">senior_mechanic<" not in html and ">grp_a_hr<" not in html
    assert 'data-role="grp_gone"' in html

    # Приглашённый — в группе своей роли, с бэджем и Resend invite.
    mech = _group_html(html, "mechanic")
    assert "Invited" in mech and "Resend invite" in mech

    # Старый «Tip»-баннер и заглушка «Later we will load roles…» убраны.
    assert "Now you can choose which shops" not in html
    assert "Later we will load roles" not in html


def test_users_sorted_by_name_inside_group_unless_explicit_sort(client, grouping_env):
    assert login(client).status_code == 302

    mech = _group_html(_page(client, "/settings/users?q=grpusr"), "mechanic")
    assert mech.index("grpusr-mech-alice@") < mech.index("grpusr-mech-invited@") < mech.index("grpusr-mech-zed@")

    html = _page(client, "/settings/users?q=grpusr&sort_by=email&sort_dir=desc")
    assert _groups(html) == ACTIVE_ORDER  # группы остаются на местах
    mech = _group_html(html, "mechanic")
    assert mech.index("grpusr-mech-zed@") < mech.index("grpusr-mech-invited@") < mech.index("grpusr-mech-alice@")

    # Сортировка по роли/статусу больше не поддерживается — порядок по имени.
    mech = _group_html(_page(client, "/settings/users?q=grpusr&sort_by=role&sort_dir=desc"), "mechanic")
    assert mech.index("grpusr-mech-alice@") < mech.index("grpusr-mech-zed@")


def test_single_page_ignores_small_per_page(client, grouping_env):
    """Группы не рвутся между страницами: per_page из старых ссылок не действует."""
    assert login(client).status_code == 302
    html = _page(client, "/settings/users?q=grpusr&per_page=1&page=1")
    assert _groups(html) == ACTIVE_ORDER
    for email, doc in grouping_env["users"].items():
        assert (email in html) == doc.get("is_active", True)
    assert "Page 1 of" not in html


def test_owner_role_select_lists_catalog_with_custom_roles(client, grouping_env):
    assert login(client).status_code == 302
    html = _page(client, "/settings/users?q=grpusr")
    assert 'value="owner"' in html
    assert '<option value="" selected disabled>Select a role…</option>' in html
    assert '<optgroup label="Custom roles">' in html
    assert html.index('value="grp_z_crew"') < html.index('value="grp_a_hr"')  # Crew lead < People admin
    assert 'value="grp_gone"' not in html  # несуществующая роль не предлагается
    # Системные — по иерархии.
    positions = [html.index(f'<option value="{key}">') for key in SYSTEM_ROLE_ORDER]
    assert positions == sorted(positions)
    assert "Manage roles" in html


def test_non_owner_gets_no_owner_option_but_custom_roles(client, grouping_env):
    assert login(client, email=f"{EMAIL_PREFIX}hr@test.local", password=PASSWORD).status_code == 302
    html = _page(client, "/settings/users?q=grpusr&status=all")
    assert 'value="owner"' not in html
    assert 'value="grp_z_crew"' in html and 'value="manager"' in html
    # Группа владельцев видна, но строка владельца — только для чтения.
    assert _groups(html) == ACTIVE_ORDER + ["inactive"]
    owner_group = _group_html(html, "owner")
    assert "Owner account" in owner_group and "edit-user-btn" not in owner_group
    # Без settings.manage_roles ссылки на страницу ролей нет.
    assert "Manage roles" not in html


def test_search_matches_role_name(client, grouping_env):
    assert login(client).status_code == 302
    html = _page(client, "/settings/users?q=crew%20lead")
    assert _groups(html) == ["grp_z_crew"]
    assert f"{EMAIL_PREFIX}crew@test.local" in html
    assert f"{EMAIL_PREFIX}hr@test.local" not in html


def test_empty_result_uses_shared_empty_state(client, grouping_env):
    assert login(client).status_code == 302
    html = _page(client, "/settings/users?q=zz-no-such-user-zz")
    assert "data-role-group" not in html
    assert 'class="empty-state"' in html and "No users found" in html
