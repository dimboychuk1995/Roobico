"""
Живые имена пользователей (app/utils/user_names.py): формат имени, резолвер
(неактивные, id строкой и ObjectId, изоляция тенантов, тенант сессии, кэш на
запрос), подписи и relabel, переписывание снимков после переименования (все
формы, без updated_at / labors_rev / журнала, идемпотентность, чужой
тенант и чужой магазин не трогаются, вне запроса) и скрипт бэкфилла
(dry-run, боевой прогон, --tenant по id и slug).

Тенанты X и Y — свои, со своими shop-базами: прогоны рефреша и скрипта по
--tenant не задевают данные других модулей.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import TEST_MASTER, TEST_MONGO_URI

X1_DB = "roobico_test_user_names_x1"
X2_DB = "roobico_test_user_names_x2"
Y_DB = "roobico_test_user_names_y"
OLD = "Vitalii Mechanick"
NEW = "Vitaliy Pryyemskyy"
# Метка времени «до рефреша»: pymongo отдаёт наивный UTC — сравниваем так же.
STAMP = datetime(2026, 1, 2, 3, 4, 5)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def world(app, seed, mongo):
    """Тенант X: магазин X1 и неактивный X2 (tenant_id строкой); механик,
    переименованный из OLD в NEW, коллега, уволенный (tenant_id строкой) и
    пользователь без имени и email. Тенант Y: свой магазин и свой механик."""
    master = mongo[TEST_MASTER]
    now = datetime.now(timezone.utc)
    # db_name обязателен: у tenants уникальный (не sparse) индекс по нему.
    tenant_x = {"_id": ObjectId(), "slug": "user-names-x", "name": "User Names X", "status": "active",
                "db_name": "roobico_test_user_names_tenant_x", "created_at": now}
    tenant_y = {"_id": ObjectId(), "slug": "user-names-y", "name": "User Names Y", "status": "active",
                "db_name": "roobico_test_user_names_tenant_y", "created_at": now}
    shop_x1 = {"_id": ObjectId(), "tenant_id": tenant_x["_id"], "name": "X1", "db_name": X1_DB, "is_active": True}
    shop_x2 = {"_id": ObjectId(), "tenant_id": str(tenant_x["_id"]), "name": "X2", "db_name": X2_DB, "is_active": False}
    shop_y = {"_id": ObjectId(), "tenant_id": tenant_y["_id"], "name": "Y", "db_name": Y_DB, "is_active": True}
    vitaliy = {
        "_id": ObjectId(), "tenant_id": tenant_x["_id"], "email": "vitaliy.names@test.local",
        "first_name": "Vitaliy", "last_name": "Pryyemskyy", "name": NEW, "role": "mechanic", "is_active": True,
    }
    colleague = {
        "_id": ObjectId(), "tenant_id": tenant_x["_id"], "email": "colleague.names@test.local",
        "first_name": "Other", "last_name": "Mech", "name": "Other Mech", "role": "mechanic", "is_active": True,
    }
    retired = {
        "_id": ObjectId(), "tenant_id": str(tenant_x["_id"]), "email": "retired.names@test.local",
        "first_name": "Old", "last_name": "Timer", "role": "senior_mechanic", "is_active": False,
    }
    nameless = {"_id": ObjectId(), "tenant_id": tenant_x["_id"], "email": "", "role": "viewer", "is_active": True}
    stranger = {
        "_id": ObjectId(), "tenant_id": tenant_y["_id"], "email": "stranger.names@test.local",
        "first_name": "Yuri", "last_name": "Foreign", "role": "mechanic", "is_active": True,
    }
    users = [vitaliy, colleague, retired, nameless, stranger]
    # try/finally: упавшая на середине подготовка не оставляет данных,
    # которые уронили бы следующие тесты модуля.
    try:
        master.tenants.insert_many([tenant_x, tenant_y])
        master.shops.insert_many([shop_x1, shop_x2, shop_y])
        master.users.insert_many(users)

        yield {
            "tenant_x": tenant_x, "tenant_y": tenant_y,
            "shop_x1": shop_x1, "shop_x2": shop_x2, "shop_y": shop_y,
            "vitaliy": vitaliy, "colleague": colleague, "retired": retired,
            "nameless": nameless, "stranger": stranger,
        }
    finally:
        master.users.delete_many({"_id": {"$in": [u["_id"] for u in users]}})
        master.shops.delete_many({"_id": {"$in": [shop_x1["_id"], shop_x2["_id"], shop_y["_id"]]}})
        master.tenants.delete_many({"_id": {"$in": [tenant_x["_id"], tenant_y["_id"]]}})
        for name in (X1_DB, X2_DB, Y_DB):
            mongo.drop_database(name)


# ── формат имени ─────────────────────────────────────────────

def test_user_display_name_precedence():
    from app.utils.user_names import user_display_name

    assert user_display_name({"first_name": " Ann ", "last_name": "Lee ", "name": "Old", "email": "a@x"}) == "Ann Lee"
    assert user_display_name({"first_name": "Ann", "name": "Old"}) == "Ann"
    assert user_display_name({"first_name": "", "last_name": None, "name": " Legacy ", "email": "a@x"}) == "Legacy"
    assert user_display_name({"email": "only@x"}) == "only@x"
    assert user_display_name({}) == "" and user_display_name(None) == ""


def test_tenant_id_values_forms():
    """tenant_id бывает и ObjectId, и строкой — для $in нужны обе формы."""
    from app.utils.user_names import tenant_id_values, tenant_values_for_shop

    tid = ObjectId()
    assert tenant_id_values(tid) == [tid, str(tid)]
    assert tenant_id_values(str(tid)) == [str(tid), tid]
    assert tenant_id_values("legacy-slug") == ["legacy-slug"]
    assert tenant_id_values(None) == [] and tenant_id_values("") == []
    # Мусор не роняет вызывающего (рефреш снимков «никогда не бросает»).
    assert tenant_id_values({"weird": 1}) == [{"weird": 1}, "{'weird': 1}"]
    assert tenant_values_for_shop({"tenant_id": tid}) == [tid, str(tid)]
    assert tenant_values_for_shop({"name": "no tenant"}) is None and tenant_values_for_shop(None) is None


# ── резолвер ─────────────────────────────────────────────────

def test_load_user_names_inactive_mixed_ids_and_tenant_isolation(app, world):
    from app.utils.user_names import load_user_names, tenant_id_values

    vid, rid, sid = world["vitaliy"]["_id"], world["retired"]["_id"], world["stranger"]["_id"]
    x_values = tenant_id_values(world["tenant_x"]["_id"])
    with app.app_context():
        names = load_user_names(
            [vid, str(rid), sid, "u1", "", None, "uattend:5", str(vid)], tenant_values=x_values,
        )
        # Без фильтра is_active/role: уволенный резолвится (tenant_id у него строкой).
        assert names == {
            str(vid): {"name": NEW, "is_active": True, "role": "mechanic"},
            str(rid): {"name": "Old Timer", "is_active": False, "role": "senior_mechanic"},
        }
        # Изоляция: механик тенанта Y по тенанту X не резолвится и наоборот.
        assert str(sid) not in names
        y_names = load_user_names([vid, sid], tenant_values=tenant_id_values(world["tenant_y"]["_id"]))
        assert set(y_names) == {str(sid)} and y_names[str(sid)]["name"] == "Yuri Foreign"
        # Вне запроса тенанта сессии нет — без явного тенанта ничего не резолвится.
        assert load_user_names([vid]) == {}
        assert load_user_names([vid], tenant_values=[]) == {}
        assert load_user_names([], tenant_values=x_values) == {}


def test_load_user_names_session_tenant_and_request_memo(app, world, mongo):
    from flask import session

    from app.utils.auth import SESSION_TENANT_ID
    from app.utils.user_names import load_user_names, tenant_id_values

    master = mongo[TEST_MASTER]
    vid, sid = world["vitaliy"]["_id"], world["stranger"]["_id"]
    with app.test_request_context("/"):
        session[SESSION_TENANT_ID] = str(world["tenant_x"]["_id"])
        assert set(load_user_names([vid, sid])) == {str(vid)}  # тенант — из сессии
        master.users.update_one({"_id": vid}, {"$set": {"first_name": "Renamed"}})
        try:
            # Тот же запрос — кэш flask.g, база повторно не читается.
            assert load_user_names([str(vid)])[str(vid)]["name"] == NEW
            # Кэш раздельный по тенанту: промах под X не мешает найти под Y.
            assert load_user_names([sid], tenant_values=tenant_id_values(world["tenant_y"]["_id"]))
            # Новый запрос (тот же app context, значит тот же g) — кэш новый.
            with app.test_request_context("/"):
                session[SESSION_TENANT_ID] = str(world["tenant_x"]["_id"])
                assert load_user_names([vid])[str(vid)]["name"] == "Renamed Pryyemskyy"
        finally:
            master.users.update_one({"_id": vid}, {"$set": {"first_name": "Vitaliy"}})


def test_display_name_for_and_relabel_people():
    from app.utils.user_names import FORMER_USER_LABEL, display_name_for, relabel_people

    uid, off, gone = ObjectId(), ObjectId(), ObjectId()
    names = {
        str(uid): {"name": "Live Name", "is_active": True, "role": "mechanic"},
        str(off): {"name": "Off Duty", "is_active": False, "role": "mechanic"},
    }
    assert display_name_for(names, uid, "Old") == "Live Name"
    assert display_name_for(names, str(uid), "Old") == "Live Name"
    assert display_name_for(names, off, "x") == "Off Duty"
    assert display_name_for(names, off, "x", mark_inactive=True) == "Off Duty (inactive)"
    assert display_name_for(names, gone, " Snap ") == "Snap"
    assert display_name_for(names, gone, "") == FORMER_USER_LABEL == "Former user"
    assert display_name_for(names, gone, "", default="") == ""
    assert display_name_for(names, None, "Legacy") == "Legacy"

    items = [
        {"user_id": uid, "name": "Old"},
        {"id": str(off), "name": "Off"},
        {"user_id": gone, "name": "Kept Snapshot"},
        {"user_id": gone, "name": ""},
        {"name": "No id"},
        "junk",
    ]
    assert relabel_people(items, names) == 2
    assert [i["name"] for i in items[:5]] == ["Live Name", "Off Duty", "Kept Snapshot", "", "No id"]
    assert relabel_people(items, names) == 0
    # default — только там, где снимок пуст.
    assert relabel_people(items, names, default=FORMER_USER_LABEL) == 1
    assert items[3]["name"] == FORMER_USER_LABEL and items[2]["name"] == "Kept Snapshot"
    crew = [{"id": off, "label": "x"}]
    assert relabel_people(crew, names, id_keys=("id",), name_key="label", mark_inactive=True) == 1
    assert crew[0]["label"] == "Off Duty (inactive)"


# ── переименование: снимки в shop-базах ─────────────────────

def _seed_snapshots(mongo, world) -> dict:
    """Все формы снимка со старым именем OLD (id ObjectId и строкой) плюс
    соседи, которых трогать нельзя."""
    vid, other = world["vitaliy"]["_id"], world["colleague"]["_id"]
    x1, x2, y = mongo[X1_DB], mongo[X2_DB], mongo[Y_DB]
    s1, s2, sy = world["shop_x1"]["_id"], world["shop_x2"]["_id"], world["shop_y"]["_id"]

    crew = [{"user_id": vid, "name": OLD, "percent": 60.0}, {"user_id": other, "name": "Other Mech", "percent": 40.0}]
    wo_full = {
        "_id": ObjectId(), "shop_id": s1, "wo_number": 1, "updated_at": STAMP, "labors_rev": 7,
        "labors": [
            {"labor_id": "L1", "labor": {"description": "Brakes", "assigned_mechanics": [dict(m) for m in crew]},
             "assigned_mechanics": [dict(m) for m in crew]},
            {"labor_id": "L2", "labor": {"description": "No crew"}},
            {"labor_id": "L3", "labor": {"assigned_mechanics": None}, "assigned_mechanics": None},
            "junk",
        ],
        "mechanic_done_marks": [{"user_id": vid, "name": OLD, "at": STAMP}, {"user_id": other, "name": "Other Mech", "at": STAMP}],
    }
    # Легаси: id строкой и только блочное зеркало.
    wo_str = {
        "_id": ObjectId(), "shop_id": s1, "wo_number": 2, "updated_at": STAMP,
        "labors": [{"labor_id": "L9", "assigned_mechanics": [{"user_id": str(vid), "name": OLD, "percent": 100.0}]}],
    }
    wo_odd = {"_id": ObjectId(), "shop_id": s1, "wo_number": 3, "labors": "not-a-list", "updated_at": STAMP}
    x1.work_orders.insert_many([wo_full, wo_str, wo_odd])
    x1.wo_time_logs.insert_many([
        {"shop_id": s1, "user_id": vid, "user_name": OLD, "updated_at": STAMP},
        {"shop_id": s1, "user_id": str(vid), "user_name": OLD, "updated_at": STAMP},
        {"shop_id": s1, "user_id": other, "user_name": "Other Mech", "updated_at": STAMP},
        # Документ чужого магазина в той же базе — фильтр shop_id его не берёт.
        {"shop_id": ObjectId(), "user_id": vid, "user_name": OLD, "updated_at": STAMP},
    ])
    ev_crew = {
        "_id": ObjectId(), "shop_id": s1, "updated_at": STAMP,
        "mechanics": [{"id": vid, "name": OLD}, {"id": other, "name": "Other Mech"}],
        "mechanic_ids": [vid, other], "mechanic_id": vid, "mechanic_name": OLD,
    }
    ev_legacy = {"_id": ObjectId(), "shop_id": s1, "updated_at": STAMP, "mechanic_id": vid, "mechanic_name": OLD}
    ev_second = {
        "_id": ObjectId(), "shop_id": s1, "updated_at": STAMP,
        "mechanics": [{"id": other, "name": "Other Mech"}, {"id": vid, "name": OLD}],
        "mechanic_ids": [other, vid], "mechanic_id": other, "mechanic_name": "Other Mech",
    }
    x1.calendar_events.insert_many([ev_crew, ev_legacy, ev_second])
    # Второй (неактивный) магазин тенанта — тоже его история.
    x2.wo_time_logs.insert_one({"shop_id": s2, "user_id": vid, "user_name": OLD, "updated_at": STAMP})
    # Чужой тенант с тем же id (мусор) — не трогается.
    y.wo_time_logs.insert_one({"shop_id": sy, "user_id": vid, "user_name": OLD})
    y.work_orders.insert_one({"shop_id": sy, "labors": [{"labor": {"assigned_mechanics": [{"user_id": vid, "name": OLD}]}}]})
    return {"wo_full": wo_full, "wo_str": wo_str, "wo_odd": wo_odd,
            "ev_crew": ev_crew, "ev_legacy": ev_legacy, "ev_second": ev_second}


EXPECTED = {
    "shops": 2, "errors": 0,
    "work_orders": 1, "work_order_mirrors": 2, "done_marks": 1,
    "time_logs": 3, "calendar_mechanics": 2, "calendar_mechanic_name": 2,
}


def _old_name_count(mongo, world) -> int:
    """Документы магазинов тенанта X со старым именем (тайм-лог чужого
    магазина в базе X1 — не в счёт: его трогать нельзя)."""
    x1 = mongo[X1_DB]
    s1 = world["shop_x1"]["_id"]
    return (
        x1.work_orders.count_documents({"shop_id": s1, "$or": [
            {"labors.labor.assigned_mechanics.name": OLD}, {"labors.assigned_mechanics.name": OLD},
            {"mechanic_done_marks.name": OLD},
        ]})
        + x1.wo_time_logs.count_documents({"shop_id": s1, "user_name": OLD})
        + x1.calendar_events.count_documents({"shop_id": s1, "$or": [{"mechanics.name": OLD}, {"mechanic_name": OLD}]})
        + mongo[X2_DB].wo_time_logs.count_documents({"shop_id": world["shop_x2"]["_id"], "user_name": OLD})
    )


def test_refresh_snapshots_all_shapes_without_touching_versions(world, mongo):
    """Вне app/request context (как из скрипта): все формы переписаны, соседи
    и чужие не тронуты, updated_at / labors_rev / журнал — как были;
    повторный прогон — ноль изменений."""
    from app.utils.user_names import refresh_user_name_snapshots

    docs = _seed_snapshots(mongo, world)
    master = mongo[TEST_MASTER]
    journal_before = master.audit_journal.count_documents({})
    vid, tenant_id = world["vitaliy"]["_id"], world["tenant_x"]["_id"]

    assert refresh_user_name_snapshots(master, tenant_id, vid, f"  {NEW} ") == EXPECTED

    x1 = mongo[X1_DB]
    wo = x1.work_orders.find_one({"_id": docs["wo_full"]["_id"]})
    assert wo["updated_at"] == STAMP and wo["labors_rev"] == 7
    block = wo["labors"][0]
    assert [m["name"] for m in block["labor"]["assigned_mechanics"]] == [NEW, "Other Mech"]
    assert [m["name"] for m in block["assigned_mechanics"]] == [NEW, "Other Mech"]
    assert [m["percent"] for m in block["labor"]["assigned_mechanics"]] == [60.0, 40.0]
    assert wo["labors"][1:] == docs["wo_full"]["labors"][1:]
    assert [m["name"] for m in wo["mechanic_done_marks"]] == [NEW, "Other Mech"]
    wo_str = x1.work_orders.find_one({"_id": docs["wo_str"]["_id"]})
    assert wo_str["labors"][0]["assigned_mechanics"][0]["name"] == NEW and wo_str["updated_at"] == STAMP
    assert x1.work_orders.find_one({"_id": docs["wo_odd"]["_id"]})["labors"] == "not-a-list"

    shop_logs = list(x1.wo_time_logs.find({"shop_id": world["shop_x1"]["_id"]}))
    assert sorted(log["user_name"] for log in shop_logs) == sorted([NEW, NEW, "Other Mech"])
    assert all(log["updated_at"] == STAMP for log in shop_logs)
    assert x1.wo_time_logs.count_documents({"shop_id": {"$ne": world["shop_x1"]["_id"]}, "user_name": OLD}) == 1
    assert mongo[X2_DB].wo_time_logs.find_one({})["user_name"] == NEW

    ev = x1.calendar_events.find_one({"_id": docs["ev_crew"]["_id"]})
    assert ev["mechanics"] == [{"id": vid, "name": NEW}, {"id": world["colleague"]["_id"], "name": "Other Mech"}]
    assert ev["mechanic_name"] == NEW and ev["updated_at"] == STAMP
    assert x1.calendar_events.find_one({"_id": docs["ev_legacy"]["_id"]})["mechanic_name"] == NEW
    second = x1.calendar_events.find_one({"_id": docs["ev_second"]["_id"]})
    assert second["mechanics"][1]["name"] == NEW and second["mechanic_name"] == "Other Mech"

    # Чужой тенант — как был.
    assert mongo[Y_DB].wo_time_logs.find_one({})["user_name"] == OLD
    assert mongo[Y_DB].work_orders.find_one({})["labors"][0]["labor"]["assigned_mechanics"][0]["name"] == OLD
    assert master.audit_journal.count_documents({}) == journal_before

    # Идемпотентность.
    again = refresh_user_name_snapshots(master, tenant_id, vid, NEW)
    assert again == {**{key: 0 for key in EXPECTED}, "shops": 2}


def test_refresh_snapshots_dry_run_counts_and_guards(world, mongo):
    from app.utils.user_names import refresh_user_name_snapshots

    _seed_snapshots(mongo, world)
    master = mongo[TEST_MASTER]
    vid, tenant_id = world["vitaliy"]["_id"], world["tenant_x"]["_id"]
    before = _old_name_count(mongo, world)
    assert before > 0

    assert refresh_user_name_snapshots(master, tenant_id, str(vid), NEW, dry_run=True) == EXPECTED
    assert _old_name_count(mongo, world) == before

    zero = {key: 0 for key in EXPECTED}
    # Пользователь чужого тенанта, пустое имя, мусорные id — ничего.
    assert refresh_user_name_snapshots(master, tenant_id, world["stranger"]["_id"], "Hacker") == zero
    assert refresh_user_name_snapshots(master, tenant_id, vid, "   ") == zero
    assert refresh_user_name_snapshots(master, tenant_id, "garbage", NEW) == zero
    assert refresh_user_name_snapshots(master, None, vid, NEW) == zero
    assert refresh_user_name_snapshots(master, {"weird": 1}, vid, NEW) == zero
    assert refresh_user_name_snapshots(None, tenant_id, vid, NEW) == zero
    assert _old_name_count(mongo, world) == before


def test_refresh_snapshots_never_raises_on_bad_shop(world, mongo):
    """Магазин с битым db_name: ошибка в лог и счётчик, остальные магазины
    обработаны, наружу не бросает."""
    from app.utils.user_names import refresh_user_name_snapshots

    _seed_snapshots(mongo, world)
    master = mongo[TEST_MASTER]
    bad = {"_id": ObjectId(), "tenant_id": world["tenant_x"]["_id"], "name": "Bad", "db_name": "bad name/with slash"}
    master.shops.insert_one(bad)
    try:
        counters = refresh_user_name_snapshots(master, world["tenant_x"]["_id"], world["vitaliy"]["_id"], NEW)
    finally:
        master.shops.delete_one({"_id": bad["_id"]})
    assert counters["errors"] == 1 and counters["shops"] == 2 and counters["time_logs"] == 3


# ── скрипт бэкфилла ──────────────────────────────────────────

@pytest.fixture()
def script(app, monkeypatch):
    """Скрипт с create_app, который обязан смотреть в тестовую Mongo
    (conftest выставляет MONGO_URI до импорта app; load_dotenv его не перетирает)."""
    import app.scripts.backfill_user_name_snapshots as module

    real_create_app = module.create_app

    def _guarded_create_app():
        application = real_create_app()
        assert application.config["MONGO_URI"].startswith("mongodb://127.0.0.1"), \
            "script must run against the local test Mongo"
        assert application.config["MASTER_DB_NAME"] == app.config["MASTER_DB_NAME"]
        return application

    monkeypatch.setattr(module, "create_app", _guarded_create_app)
    return module


def test_backfill_script_dry_run_then_real_run(world, mongo, script, monkeypatch, capsys):
    _seed_snapshots(mongo, world)
    vid = world["vitaliy"]["_id"]
    before = _old_name_count(mongo, world)

    monkeypatch.setattr(sys, "argv", ["x", "--dry-run", "--tenant", str(world["tenant_x"]["_id"])])
    assert script.main() == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and f"would update {vid} -> {NEW!r}: work_orders=1 work_order_mirrors=2" in out
    assert "User Names X" in out and "User Names Y" not in out
    # 4 пользователя: переименованный меняется, коллега уже с живым именем,
    # у уволенного снимков нет, безымянный пропущен.
    assert "users=4 changed=1 skipped=1 errors=0" in out
    assert _old_name_count(mongo, world) == before

    # Боевой прогон — по slug.
    monkeypatch.setattr(sys, "argv", ["x", "--tenant", "user-names-x"])
    assert script.main() == 0
    out = capsys.readouterr().out
    assert "DRY RUN" not in out and f"updated {vid} -> {NEW!r}: " in out and "would update" not in out
    assert _old_name_count(mongo, world) == 0
    assert mongo[Y_DB].wo_time_logs.find_one({})["user_name"] == OLD

    # Повторный прогон ничего не меняет; неизвестный тенант — код 2.
    assert script.main() == 0
    assert "users=4 changed=0 skipped=1 errors=0" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["x", "--tenant", "no-such-tenant"])
    assert script.main() == 2
