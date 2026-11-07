"""
Ночная уборка системного клиента "NEW Customer"
(customers/services/system_customer.py + app/scripts/cleanup_system_customer.py):
удаляются только «висячие» юниты системного клиента — без WO / записи
календаря / инспекции / вложения и старше grace-периода; сам системный
клиент, обычные клиенты (в том числе названный "New Customer") и другие
магазины не трогаются; событие уходит в Activity Journal.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest
from bson import Binary, ObjectId

from app.blueprints.customers.services import system_customer
from app.blueprints.customers.services.system_customer import (
    REFERENCING_COLLECTIONS,
    cleanup_system_customer_units,
    find_unit_references,
)
from tests.conftest import SHOP_A_DB, SHOP_B_DB

ACTOR_NAME = "Nightly cleanup"


def _unit(shop_id, customer_id, vin, *, created_at, updated_at=None, is_active=True, number=None):
    doc = {
        "_id": ObjectId(), "shop_id": shop_id, "customer_id": customer_id,
        "vin": vin, "unit_number": number, "make": "FORD", "model": "F-350", "year": 2019,
        "type": "TRUCK", "mileage": None, "is_active": is_active,
    }
    if created_at is not None:
        doc["created_at"] = created_at
    if updated_at is not None:
        doc["updated_at"] = updated_at
    return doc


def _recall_row(shop_id, customer_id, unit_id, campaign, now):
    return {
        "_id": ObjectId(), "shop_id": shop_id, "customer_id": customer_id, "unit_id": unit_id,
        "campaign_number": campaign, "channel": "email", "status": "sent",
        "to": ["fleet@example.com"], "created_at": now,
    }


@pytest.fixture()
def env(app, seed):
    """Системный клиент магазина A с полным набором юнитов-кейсов, обычные
    клиенты, системный клиент магазина B; после теста — подчистка всего."""
    from app.extensions import get_master_db, get_mongo_client

    with app.app_context():
        client = get_mongo_client()
        master = get_master_db()
        db_a, db_b = client[SHOP_A_DB], client[SHOP_B_DB]
        shop_a, shop_b = seed["shop_a"], seed["shop_b"]
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=3)
        recent = now - timedelta(minutes=10)

        sys_a = {
            "_id": ObjectId(), "shop_id": shop_a["_id"], "tenant_id": shop_a["tenant_id"],
            "company_name": "NEW Customer", "is_system": True, "contacts": [],
            "taxable": False, "is_active": True, "created_at": old, "updated_at": old,
        }
        fleet = {
            "_id": ObjectId(), "shop_id": shop_a["_id"], "tenant_id": shop_a["tenant_id"],
            "company_name": "Cleanup Fleet LLC", "contacts": [], "is_active": True, "created_at": old,
        }
        # Обычный клиент, которого просто назвали "New Customer" — не системный.
        lookalike = {
            "_id": ObjectId(), "shop_id": shop_a["_id"], "tenant_id": shop_a["tenant_id"],
            "company_name": "New Customer", "contacts": [], "is_active": True, "created_at": old,
        }
        sys_b = {
            "_id": ObjectId(), "shop_id": shop_b["_id"], "tenant_id": shop_b["tenant_id"],
            "company_name": "NEW Customer", "is_system": True, "contacts": [],
            "taxable": False, "is_active": True, "created_at": old,
        }
        db_a.customers.insert_many([sys_a, fleet, lookalike])
        db_b.customers.insert_one(sys_b)

        sid, cid = shop_a["_id"], sys_a["_id"]
        units = {
            # кандидаты на удаление
            "old_active": _unit(sid, cid, "1CLNUP00000000001", created_at=old, updated_at=old),
            "old_inactive": _unit(sid, cid, "1CLNUP00000000002", created_at=old, updated_at=old, is_active=False),
            "no_created_at": _unit(sid, cid, "1CLNUP00000000003", created_at=None),
            # остаются: есть ссылки
            "with_wo": _unit(sid, cid, "1CLNUP00000000004", created_at=old, number="WO-1"),
            "with_event": _unit(sid, cid, "1CLNUP00000000005", created_at=old),
            "with_inspection": _unit(sid, cid, "1CLNUP00000000006", created_at=old, is_active=False),
            "with_attachment": _unit(sid, cid, "1CLNUP00000000007", created_at=old),
            # остаются: grace-период
            "recent_created": _unit(sid, cid, "1CLNUP00000000008", created_at=recent),
            "recent_updated": _unit(sid, cid, "1CLNUP00000000009", created_at=old, updated_at=recent),
            # чужие: обычный клиент, «двойник» по имени, чужой shop_id в базе A, магазин B
            "fleet_unit": _unit(sid, fleet["_id"], "1CLNUP00000000010", created_at=old),
            "lookalike_unit": _unit(sid, lookalike["_id"], "1CLNUP00000000011", created_at=old),
            "foreign_shop_id": _unit(shop_b["_id"], cid, "1CLNUP00000000012", created_at=old),
            "shop_b_unit": _unit(shop_b["_id"], sys_b["_id"], "1CLNUP00000000013", created_at=old),
        }
        db_a.units.insert_many([u for k, u in units.items() if k != "shop_b_unit"])
        db_b.units.insert_one(units["shop_b_unit"])

        wo = {
            "_id": ObjectId(), "shop_id": sid, "tenant_id": shop_a["tenant_id"], "customer_id": cid,
            "unit_id": units["with_wo"]["_id"], "wo_number": 99801, "work_order_date": old,
            "status": "open", "labors": [], "totals": {}, "is_active": True, "created_at": old,
        }
        db_a.work_orders.insert_one(wo)
        event = {
            "_id": ObjectId(), "shop_id": sid, "tenant_id": shop_a["tenant_id"],
            "title": "Cleanup appt", "customer_id": cid, "unit_id": units["with_event"]["_id"],
            "start_time": now + timedelta(days=1), "end_time": now + timedelta(days=1, hours=1),
            "status": "scheduled", "created_at": old,
        }
        db_a.calendar_events.insert_one(event)
        inspection = {
            "_id": ObjectId(), "shop_id": sid, "tenant_id": shop_a["tenant_id"],
            "unit_id": units["with_inspection"]["_id"], "customer_id": cid,
            "vin": units["with_inspection"]["vin"], "inspection_date": old, "components": {},
            "created_at": old,
        }
        db_a.annual_inspections.insert_one(inspection)
        attachment = {
            "_id": ObjectId(), "entity_type": "unit", "entity_id": units["with_attachment"]["_id"],
            "filename": "door.jpg", "content_type": "image/jpeg", "size": 1,
            "data": Binary(b"x"), "uploaded_by": None, "uploaded_at": old, "shop_id": sid,
        }
        db_a.attachments.insert_one(attachment)
        recalls = [
            _recall_row(sid, cid, units["old_active"]["_id"], "24V-001", old),
            _recall_row(sid, cid, units["old_active"]["_id"], "24V-002", old),
            _recall_row(sid, cid, units["with_wo"]["_id"], "24V-003", old),
        ]
        db_a.recall_notifications.insert_many(recalls)

        yield {
            "db_a": db_a, "db_b": db_b, "master": master, "shop_a": shop_a, "shop_b": shop_b,
            "sys_a": sys_a, "sys_b": sys_b, "units": units, "now": now,
        }

        unit_ids = [u["_id"] for u in units.values()]
        db_a.units.delete_many({"_id": {"$in": unit_ids}})
        db_b.units.delete_many({"_id": {"$in": unit_ids}})
        db_a.recall_notifications.delete_many({"unit_id": {"$in": unit_ids}})
        db_a.work_orders.delete_one({"_id": wo["_id"]})
        db_a.calendar_events.delete_one({"_id": event["_id"]})
        db_a.annual_inspections.delete_one({"_id": inspection["_id"]})
        db_a.attachments.delete_one({"_id": attachment["_id"]})
        db_a.customers.delete_many({"_id": {"$in": [sys_a["_id"], fleet["_id"], lookalike["_id"]]}})
        db_b.customers.delete_one({"_id": sys_b["_id"]})
        master.audit_journal.delete_many({
            "user_name": ACTOR_NAME,
            "shop_id": {"$in": [str(shop_a["_id"]), str(shop_b["_id"])]},
        })


def _run(env, **kwargs):
    return cleanup_system_customer_units(env["db_a"], env["shop_a"], **kwargs)


def _exists(db, unit):
    return db.units.find_one({"_id": unit["_id"]}) is not None


DELETED_KEYS = ("old_active", "old_inactive", "no_created_at")
KEPT_KEYS = ("with_wo", "with_event", "with_inspection", "with_attachment",
             "recent_created", "recent_updated", "fleet_unit", "lookalike_unit", "foreign_shop_id")


# ── что удаляется ───────────────────────────────────────────────────────


def test_old_active_unreferenced_unit_deleted_with_recall_rows(env):
    unit = env["units"]["old_active"]
    assert env["db_a"].recall_notifications.count_documents({"unit_id": unit["_id"]}) == 2

    summary = _run(env)

    assert not _exists(env["db_a"], unit)
    assert env["db_a"].recall_notifications.count_documents({"unit_id": unit["_id"]}) == 0
    assert summary["recall_notifications_deleted"] == 2
    assert summary["dry_run"] is False
    row = next(u for u in summary["deleted_units"] if u["id"] == str(unit["_id"]))
    assert row["vin"] == unit["vin"] and row["label"]


def test_old_inactive_unreferenced_unit_deleted(env):
    unit = env["units"]["old_inactive"]
    _run(env)
    assert not _exists(env["db_a"], unit), "деактивированный висячий юнит тоже уходит"


def test_missing_created_at_treated_as_old(env):
    _run(env)
    assert not _exists(env["db_a"], env["units"]["no_created_at"])


def test_summary_counts(env):
    summary = _run(env)
    assert summary["system_customers"] == 1
    assert summary["candidates"] == 9, "юнит с чужим shop_id — не кандидат"
    assert summary["deleted"] == 3
    assert summary["kept_referenced"] == 4
    assert summary["kept_referenced_by"] == {
        "work_orders": 1, "calendar_events": 1, "annual_inspections": 1, "attachments": 1,
    }
    assert summary["skipped_recent"] == 2
    assert summary["kept_late_referenced"] == 0
    assert {u["id"] for u in summary["deleted_units"]} == {
        str(env["units"][k]["_id"]) for k in DELETED_KEYS
    }
    for key in KEPT_KEYS:
        assert _exists(env["db_a"], env["units"][key]), key


# ── что остаётся ────────────────────────────────────────────────────────


def test_unit_with_work_order_kept_with_its_recall_rows(env):
    unit = env["units"]["with_wo"]
    _run(env)
    assert _exists(env["db_a"], unit)
    assert env["db_a"].recall_notifications.count_documents({"unit_id": unit["_id"]}) == 1


def test_unit_with_calendar_event_kept(env):
    _run(env)
    assert _exists(env["db_a"], env["units"]["with_event"])


def test_unit_with_annual_inspection_kept(env):
    _run(env)
    assert _exists(env["db_a"], env["units"]["with_inspection"])


def test_unit_with_attachment_kept(env):
    _run(env)
    assert _exists(env["db_a"], env["units"]["with_attachment"])


def test_recent_units_kept_until_grace_passes(env):
    summary = _run(env)
    assert _exists(env["db_a"], env["units"]["recent_created"])
    assert _exists(env["db_a"], env["units"]["recent_updated"]), "свежий updated_at тоже держит юнит"
    assert summary["skipped_recent"] == 2

    # Через 3 часа они всё ещё внутри grace-периода (6 ч).
    still = _run(env, now=env["now"] + timedelta(hours=3))
    assert still["deleted"] == 0 and still["skipped_recent"] == 2

    # Прошло 7 часов (grace 6 ч) — те же юниты уходят, остальное не трогается.
    later = _run(env, now=env["now"] + timedelta(hours=7))
    assert later["deleted"] == 2 and later["skipped_recent"] == 0
    assert not _exists(env["db_a"], env["units"]["recent_created"])
    assert not _exists(env["db_a"], env["units"]["recent_updated"])
    assert later["kept_referenced"] == 4


def test_ordinary_customer_unit_kept(env):
    _run(env)
    assert _exists(env["db_a"], env["units"]["fleet_unit"])


def test_non_system_customer_named_new_customer_kept(env):
    _run(env)
    assert _exists(env["db_a"], env["units"]["lookalike_unit"])
    assert env["db_a"].customers.find_one({"company_name": "New Customer", "is_system": {"$ne": True}})


def test_system_customer_document_untouched(env):
    before = env["db_a"].customers.find_one({"_id": env["sys_a"]["_id"]})
    _run(env)
    after = env["db_a"].customers.find_one({"_id": env["sys_a"]["_id"]})
    assert after == before
    assert after["is_system"] is True and after["is_active"] is True


# ── режимы ──────────────────────────────────────────────────────────────


def test_dry_run_deletes_nothing_but_reports_counts(env):
    summary = _run(env, dry_run=True)
    assert summary["dry_run"] is True
    assert summary["deleted"] == 3 and summary["kept_referenced"] == 4 and summary["skipped_recent"] == 2
    assert summary["recall_notifications_deleted"] == 2
    assert {u["id"] for u in summary["deleted_units"]} == {str(env["units"][k]["_id"]) for k in DELETED_KEYS}
    for unit in env["units"].values():
        db = env["db_b"] if unit is env["units"]["shop_b_unit"] else env["db_a"]
        assert _exists(db, unit)
    assert env["db_a"].recall_notifications.count_documents({"unit_id": env["units"]["old_active"]["_id"]}) == 2
    assert env["master"].audit_journal.find_one({"user_name": ACTOR_NAME}) is None


def test_second_run_is_noop(env):
    first = _run(env)
    assert first["deleted"] == 3
    second = _run(env)
    assert second["deleted"] == 0 and second["deleted_units"] == []
    assert second["candidates"] == 6
    assert second["kept_referenced"] == 4 and second["skipped_recent"] == 2
    assert env["master"].audit_journal.count_documents({"user_name": ACTOR_NAME}) == 1, \
        "без удалений событие в журнал не пишется"


def test_shop_isolation(env):
    _run(env)
    # Магазин B: свой системный клиент и его висячий юнит целы.
    assert _exists(env["db_b"], env["units"]["shop_b_unit"])
    assert env["db_b"].customers.find_one({"_id": env["sys_b"]["_id"]})
    # База A: юнит системного клиента A, но с shop_id магазина B — не трогаем.
    assert _exists(env["db_a"], env["units"]["foreign_shop_id"])

    # Прогон для B чистит только B.
    summary_b = cleanup_system_customer_units(env["db_b"], env["shop_b"])
    assert summary_b["deleted"] == 1
    assert not _exists(env["db_b"], env["units"]["shop_b_unit"])
    assert _exists(env["db_a"], env["units"]["foreign_shop_id"])


def test_no_system_customer_is_a_noop(env):
    env["db_a"].customers.delete_one({"_id": env["sys_a"]["_id"]})
    summary = _run(env)
    assert summary["system_customers"] == 0 and summary["candidates"] == 0 and summary["deleted"] == 0
    for key in DELETED_KEYS:
        assert _exists(env["db_a"], env["units"][key])


def test_audit_journal_entry(env):
    summary = _run(env)
    entry = env["master"].audit_journal.find_one(
        {"kind": "event", "source": "system", "user_name": ACTOR_NAME,
         "entity_type": "customer", "entity_id": str(env["sys_a"]["_id"]), "action": "delete"},
    )
    assert entry is not None
    assert entry["shop_id"] == str(env["shop_a"]["_id"])
    assert entry["tenant_id"] == str(env["shop_a"]["tenant_id"])
    assert entry["entity_label"] == "NEW Customer"
    assert "Nightly cleanup: removed 3 stray unit(s)" in entry["summary"]
    details = entry["details"]
    deleted_ids = {u["id"] for u in summary["deleted_units"]}
    # Полный список — строками (audit_log._plain режет списки до 40, строки нет).
    assert isinstance(details["deleted_unit_ids"], str)
    assert set(details["deleted_unit_ids"].split(", ")) == deleted_ids
    assert isinstance(details["deleted_units"], str)
    lines = details["deleted_units"].split("; ")
    assert len(lines) == 3
    for line in lines:
        assert any(f"(id {uid})" in line for uid in deleted_ids) and "VIN 1CLNUP" in line
    assert details["kept_referenced"] == 4 and details["skipped_recent"] == 2
    assert details["kept_late_referenced"] == 0
    assert details["recall_notifications_deleted"] == 2
    assert details["grace_hours"] == 6.0


def test_referencing_collections_shape_and_shop_scoping():
    """Каждая запись — (коллекция, поле, доп. фильтр, scoped_by_shop): WO,
    календарь и инспекции запрашиваются с shop_id (их индексы начинаются с
    него), attachments — по (entity_type, entity_id) без shop_id."""
    entries = {coll: (field, extra, scoped) for coll, field, extra, scoped in REFERENCING_COLLECTIONS}
    assert entries == {
        "work_orders": ("unit_id", None, True),
        "calendar_events": ("unit_id", None, True),
        "annual_inspections": ("unit_id", None, True),
        "attachments": ("entity_id", {"entity_type": "unit"}, False),
    }


# ── форма ссылок: документированные гарантии ────────────────────────────


@pytest.fixture()
def extra(env):
    """Документы поверх env (ссылки в нестандартной форме) в базе A —
    удаляются после теста."""
    added: list[tuple[str, ObjectId]] = []

    def add(coll_name, doc):
        doc.setdefault("_id", ObjectId())
        env["db_a"][coll_name].insert_one(doc)
        added.append((coll_name, doc["_id"]))
        return doc

    yield add
    for coll_name, doc_id in added:
        env["db_a"][coll_name].delete_one({"_id": doc_id})


def _stray(env, vin):
    """Старый юнит системного клиента A без ссылок — кандидат на удаление."""
    old = env["now"] - timedelta(days=3)
    return _unit(env["shop_a"]["_id"], env["sys_a"]["_id"], vin, created_at=old, updated_at=old)


def _wo(env, unit_id, *, number, shop_id=None, is_active=True):
    return {
        "shop_id": shop_id or env["shop_a"]["_id"], "tenant_id": env["shop_a"]["tenant_id"],
        "customer_id": env["sys_a"]["_id"], "unit_id": unit_id, "wo_number": number,
        "status": "open", "labors": [], "totals": {}, "is_active": is_active,
        "created_at": env["now"],
    }


def _attachment(env, entity_id, *, shop_id=None):
    doc = {
        "entity_type": "unit", "entity_id": entity_id, "filename": "vin.jpg",
        "content_type": "image/jpeg", "size": 1, "data": Binary(b"x"),
        "uploaded_by": None, "uploaded_at": env["now"],
    }
    if shop_id is not None:
        doc["shop_id"] = shop_id
    return doc


def test_work_order_with_string_unit_id_keeps_unit(env, extra):
    unit = extra("units", _stray(env, "1CLNUP00000000021"))
    extra("work_orders", _wo(env, str(unit["_id"]), number=99802))
    summary = _run(env)
    assert _exists(env["db_a"], unit), "unit_id строкой — тоже ссылка"
    assert summary["kept_referenced_by"]["work_orders"] == 2


def test_soft_deleted_work_order_keeps_unit(env, extra):
    unit = extra("units", _stray(env, "1CLNUP00000000022"))
    extra("work_orders", _wo(env, unit["_id"], number=99803, is_active=False))
    summary = _run(env)
    assert _exists(env["db_a"], unit), "удалённый (is_active: false) WO тоже держит юнит"
    assert summary["kept_referenced_by"]["work_orders"] == 2


def test_attachment_with_string_entity_id_keeps_unit(env, extra):
    unit = extra("units", _stray(env, "1CLNUP00000000023"))
    extra("attachments", _attachment(env, str(unit["_id"]), shop_id=env["shop_a"]["_id"]))
    summary = _run(env)
    assert _exists(env["db_a"], unit), "entity_id строкой — тоже ссылка"
    assert summary["kept_referenced_by"]["attachments"] == 2


def test_find_unit_references_scopes_by_shop_except_attachments(env, extra):
    unit_wo = extra("units", _stray(env, "1CLNUP00000000024"))
    unit_att = extra("units", _stray(env, "1CLNUP00000000025"))
    # WO с shop_id другого магазина: shop-scoped запрос (по индексу
    # shop_id+unit_id) его не видит — в shop-базе таких документов не бывает.
    extra("work_orders", _wo(env, unit_wo["_id"], number=99804, shop_id=env["shop_b"]["_id"]))
    # Вложение вовсе без shop_id: запрос идёт по (entity_type, entity_id).
    extra("attachments", _attachment(env, unit_att["_id"]))

    refs = find_unit_references(
        env["db_a"], env["shop_a"]["_id"],
        [unit_wo["_id"], unit_att["_id"], env["units"]["with_wo"]["_id"]],
    )
    assert unit_wo["_id"] not in refs
    assert refs[unit_att["_id"]] == ["attachments"]
    assert refs[env["units"]["with_wo"]["_id"]] == ["work_orders"]
    assert find_unit_references(env["db_a"], env["shop_a"]["_id"], []) == {}


# ── гонка: ссылка появилась после первого прохода ───────────────────────


def test_late_reference_found_in_transaction_keeps_unit(env, monkeypatch):
    """Первый проход устарел (вернул «ссылок нет») — повторная проверка внутри
    транзакции всё равно оставляет юниты с WO / записью / инспекцией /
    вложением; счётчики и журнал отражают реально удалённое."""
    monkeypatch.setattr(system_customer, "find_unit_references", lambda *_a, **_k: {})

    summary = _run(env)

    assert summary["kept_referenced"] == 0 and summary["kept_referenced_by"] == {}
    assert summary["kept_late_referenced"] == 4
    assert summary["deleted"] == 3 and summary["skipped_recent"] == 2
    assert {u["id"] for u in summary["deleted_units"]} == {str(env["units"][k]["_id"]) for k in DELETED_KEYS}
    for key in KEPT_KEYS:
        assert _exists(env["db_a"], env["units"][key]), key
    for key in DELETED_KEYS:
        assert not _exists(env["db_a"], env["units"][key]), key
    assert env["db_a"].recall_notifications.count_documents({"unit_id": env["units"]["with_wo"]["_id"]}) == 1
    assert summary["recall_notifications_deleted"] == 2

    entry = env["master"].audit_journal.find_one({"user_name": ACTOR_NAME, "entity_id": str(env["sys_a"]["_id"])})
    assert "removed 3 stray unit(s)" in entry["summary"]
    details = entry["details"]
    assert details["kept_late_referenced"] == 4
    assert set(details["deleted_unit_ids"].split(", ")) == {u["id"] for u in summary["deleted_units"]}
    assert len(details["deleted_units"].split("; ")) == 3


# ── CLI ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def script(app, monkeypatch):
    """Скрипт с create_app, который обязан смотреть в тестовую Mongo
    (conftest выставляет MONGO_URI до импорта app; load_dotenv его не перетирает)."""
    import app.scripts.cleanup_system_customer as module

    real_create_app = module.create_app

    def _guarded_create_app():
        application = real_create_app()
        assert application.config["MONGO_URI"].startswith("mongodb://127.0.0.1"), \
            "script must run against the local test Mongo"
        assert application.config["MASTER_DB_NAME"] == app.config["MASTER_DB_NAME"]
        return application

    monkeypatch.setattr(module, "create_app", _guarded_create_app)
    return module


def test_cli_dry_run_single_shop(env, script, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["x", "--dry-run", "--shop-db", SHOP_A_DB])
    assert script.main() == 0
    out = capsys.readouterr().out
    assert f"({SHOP_A_DB})" in out and f"({SHOP_B_DB})" not in out
    assert "deleted=3" in out and "kept_referenced=4" in out and "skipped_recent=2" in out
    assert "DRY RUN" in out
    for key in DELETED_KEYS:
        unit = env["units"][key]
        assert _exists(env["db_a"], unit)
        assert f"  would delete {unit['_id']} VIN {unit['vin']} unit # " in out
    assert "  deleted " not in out


def test_cli_real_run_all_shops(env, script, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["x", "--grace-hours", "6"])
    assert script.main() == 0
    out = capsys.readouterr().out
    assert f"({SHOP_A_DB})" in out and f"({SHOP_B_DB})" in out
    assert "Totals:" in out and "DRY RUN" not in out and "would delete" not in out
    for key in DELETED_KEYS:
        unit = env["units"][key]
        assert not _exists(env["db_a"], unit)
        assert f"  deleted {unit['_id']} VIN {unit['vin']} unit # " in out
    assert not _exists(env["db_b"], env["units"]["shop_b_unit"])
    assert f"  deleted {env['units']['shop_b_unit']['_id']} VIN " in out
    for key in KEPT_KEYS:
        assert _exists(env["db_a"], env["units"][key]), key


def test_cli_reports_shop_error_with_exit_code(env, script, monkeypatch, capsys):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(script, "cleanup_system_customer_units", _boom)
    monkeypatch.setattr(sys, "argv", ["x", "--shop-db", SHOP_A_DB])
    assert script.main() == 1
    out = capsys.readouterr().out
    assert "ERROR" in out and "'errors': 1" in out
    for key in DELETED_KEYS:
        assert _exists(env["db_a"], env["units"][key])


def test_full_list_kept_in_journal_and_cron_log_beyond_40_units(env, script, monkeypatch, capsys):
    """Больше 40 юнитов за ночь: audit_log._plain режет списки до 40 элементов,
    строки — нет; в cron-лог (stdout) скрипт печатает по строке на юнит.
    Событие при этом одно на магазин."""
    sid, cid = env["shop_a"]["_id"], env["sys_a"]["_id"]
    old = env["now"] - timedelta(days=5)
    many = [
        _unit(sid, cid, f"1CLNUP0000000{n:04d}", created_at=old, updated_at=old, number=f"T-{n}")
        for n in range(100, 145)
    ]
    env["db_a"].units.insert_many(many)
    try:
        monkeypatch.setattr(sys, "argv", ["x", "--shop-db", SHOP_A_DB])
        assert script.main() == 0
        out = capsys.readouterr().out
    finally:
        env["db_a"].units.delete_many({"_id": {"$in": [u["_id"] for u in many]}})

    expected = {str(u["_id"]) for u in many} | {str(env["units"][k]["_id"]) for k in DELETED_KEYS}
    assert len(expected) == 48

    entries = list(env["master"].audit_journal.find({"user_name": ACTOR_NAME, "entity_id": str(cid)}))
    assert len(entries) == 1, "ровно одно событие на магазин"
    details = entries[0]["details"]
    assert set(details["deleted_unit_ids"].split(", ")) == expected
    assert len(details["deleted_units"].split("; ")) == 48
    for uid in expected:
        assert f"(id {uid})" in details["deleted_units"]
    assert "removed 48 stray unit(s)" in entries[0]["summary"]

    assert "deleted=48" in out
    for uid in expected:
        assert f"  deleted {uid} VIN " in out
    for unit in many:
        assert f"  deleted {unit['_id']} VIN {unit['vin']} unit # {unit['unit_number']} " in out
