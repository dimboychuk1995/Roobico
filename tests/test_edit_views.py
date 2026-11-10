"""
edit_views (services/edit_views.py) на локальной тестовой Mongo: ревизия
(labors_rev / rev_filter / with_rev), sid устройства, компактные строки,
record_view и выбор base (find_base). Сценарии адверсариального ревью:
неподхваченный рефетч старого приложения механика, два устройства под
одним логином, push-tap экрана деталей у менеджера (консенсус), отсечка
request_started_at, пустая форма после упавшего GET, чужие view_id.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from app.blueprints.work_orders.services import edit_views as ev
from app.blueprints.work_orders.services.labor_merge import (
    MECHANIC_SPEC,
    MOBILE_MANAGER_SPEC,
    MergeContext,
    merge_labors,
)
from tests.conftest import SHOP_A_DB, TEST_MONGO_URI

T0 = datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def shop(app, seed, mongo):
    """(shop_db, shop_id, new_wo) — вьюхи и тайм-логи созданных WO чистятся после теста."""
    shop_db = mongo[SHOP_A_DB]
    shop_id = seed["shop_a"]["_id"]
    created = []

    def new_wo(labors=None, *, status="in_progress", rev=0):
        wo = {"_id": ObjectId(), "shop_id": shop_id, "status": status, "labors_rev": rev,
              "labors": labors or [], "totals": {"labors": [{"labor": 0} for _ in (labors or [])]}}
        created.append(wo["_id"])
        return wo

    yield shop_db, shop_id, new_wo
    shop_db.wo_edit_views.delete_many({"work_order_id": {"$in": created}})
    shop_db.wo_time_logs.delete_many({"work_order_id": {"$in": created}})


def R(lid="", desc="", parts=None, **kw):
    r = {"labor_id": lid, "description": desc, "issue_description": "", "hours": "", "rate_code": "",
         "labor_total": None, "hours_source": "", "assigned_mechanics": [], "parts": parts or []}
    r.update(kw)
    return r


def M(lid="", desc="", parts=None, **kw):
    r = {"labor_id": lid, "description": desc, "issue_description": "", "hours": "", "rate_code": "",
         "parts": parts or []}
    r.update(kw)
    return r


def ids(res):
    return [r["labor_id"] for r in res.rows]


def _ctx(choice, user_id, sid, now, **kw):
    return MergeContext(user_id=user_id, sid=sid, now=now, **choice.ctx_kwargs(), **kw)


# ───────────────────────── ревизия и CAS ─────────────────────────

def test_labors_rev_reads_missing_and_bad_values():
    assert ev.labors_rev({}) == 0
    assert ev.labors_rev({"labors_rev": 3}) == 3
    assert ev.labors_rev({"labors_rev": "4"}) == 4
    assert ev.labors_rev({"labors_rev": None}) == 0
    assert ev.labors_rev({"labors_rev": "x"}) == 0
    assert ev.labors_rev({"labors_rev": True}) == 0
    assert ev.labors_rev(None) == 0


def test_rev_filter_matches_missing_field_and_zero(shop):
    shop_db, _shop_id, _new = shop
    col = shop_db.c1_rev_filter_probe
    col.drop()
    try:
        missing, zero, two = ObjectId(), ObjectId(), ObjectId()
        col.insert_many([{"_id": missing}, {"_id": zero, "labors_rev": 0}, {"_id": two, "labors_rev": 2}])
        assert {d["_id"] for d in col.find(ev.rev_filter(0))} == {missing, zero}
        assert {d["_id"] for d in col.find(ev.rev_filter(None))} == {missing, zero}
        assert [d["_id"] for d in col.find(ev.rev_filter(2))] == [two]

        # CAS: первая запись по ревизии 0 проходит и двигает ревизию, повтор — нет.
        upd = ev.bump_rev({"$set": {"x": 1}})
        assert col.update_one({"_id": missing, **ev.rev_filter(0)}, upd).modified_count == 1
        assert col.find_one({"_id": missing})["labors_rev"] == 1
        assert col.update_one({"_id": missing, **ev.rev_filter(0)}, upd).modified_count == 0
        assert col.update_one({"_id": missing, **ev.rev_filter(1)}, upd).modified_count == 1
    finally:
        col.drop()


def test_with_rev_always_projects_labors_rev(shop):
    assert ev.with_rev(None) is None
    assert ev.with_rev({"labors": 1}) == {"labors": 1, "labors_rev": 1}
    assert ev.with_rev({"_id": 0, "status": 1})["labors_rev"] == 1
    assert ev.with_rev({"labors": 0}) == {"labors": 0}
    assert ev.with_rev({"labors_rev": 0, "big": 0}) == {"big": 0}
    assert ev.with_rev({"_id": 1}) == {"_id": 1, "labors_rev": 1}
    assert ev.with_rev({"_id": 0}) == {"_id": 0}
    assert ev.with_rev({"_id": 1, "big": 0}) == {"_id": 1, "big": 0}
    assert ev.with_rev(["labors"]) == ["labors", "labors_rev"]

    shop_db, shop_id, new_wo = shop
    col = shop_db.c1_with_rev_probe
    col.drop()
    try:
        col.insert_one({"_id": 1, "status": "open", "labors_rev": 7, "labors": []})
        assert ev.labors_rev(col.find_one({"_id": 1}, ev.with_rev({"status": 1}))) == 7
    finally:
        col.drop()


def test_bump_rev_keeps_other_increments():
    update = {"$set": {"a": 1}, "$inc": {"n": 2}}
    out = ev.bump_rev(update)
    assert out == {"$set": {"a": 1}, "$inc": {"n": 2, "labors_rev": 1}}
    assert update == {"$set": {"a": 1}, "$inc": {"n": 2}}


def test_concurrent_update_response_and_attempts():
    assert ev.CONCURRENT_UPDATE_RESPONSE["error"] == "concurrent_update"
    assert ev.MAX_CAS_ATTEMPTS == 5
    assert issubclass(ev.ConcurrentUpdateError, Exception)


# ───────────────────────── сессия устройства ─────────────────────────

def test_edit_session_id_is_created_once_per_session(app):
    from flask import session

    with app.test_request_context():
        first = ev.edit_session_id()
        assert len(first) == 16
        assert session[ev.SESSION_SID_KEY] == first
        assert ev.edit_session_id() == first
    with app.test_request_context():
        assert ev.edit_session_id() != first  # другое устройство — другая cookie-сессия


# ───────────────────────── компактные строки ─────────────────────────

def test_work_rows_and_compact_rows_from_stored_wo():
    uid, pid = ObjectId(), ObjectId()
    at = datetime(2026, 11, 9, 8, 30)  # naive UTC, как отдаёт pymongo
    misc = '[{"description":"EPA","price":3,"quantity":2,"partIndex":0}]'
    wo = {
        "_id": ObjectId(),
        "labors_rev": 3,
        "labors": [
            {
                "labor_id": "R1", "client_key": "ck-1",
                "minted": {"by": "u1", "sid": "s1", "at": at, "ck": "ck-1"},
                "labor": {"description": "Brakes", "hours": "1.5", "rate_code": "std", "labor_full_total": 175.0,
                          "assigned_mechanics": [{"user_id": uid, "name": "Mike", "role": "mechanic", "percent": 100.0}],
                          "issue_description": "noise", "hours_source": "tracked"},
                "parts": [{"part_id": pid, "part_number": "BP-1", "description": "Pads", "qty": 2, "cost": 20.0,
                           "price": 45.0, "core_charge": 0.0, "misc_charge": 0.0,
                           "misc_charge_description": misc, "one_time_part": False, "unit": "quart"}],
            },
            "garbage",
            {"labor_id": "R2", "labor": {"description": "", "issue_description": "Customer note"}, "parts": []},
        ],
        "totals": {"labors": [{"labor": 150.0}, {}, {"labor": 0}]},
    }
    rows = ev.work_rows_from_wo(wo)
    assert [r["labor_id"] for r in rows] == ["R1", "R2"]
    assert rows[0]["labor_total"] == 150.0
    assert rows[0]["client_key"] == "ck-1" and rows[0]["minted"]["sid"] == "s1"
    assert rows[0]["assigned_mechanics"][0]["user_id"] == uid
    assert rows[1]["labor_total"] is None  # база по индексу самого блока (мусор не сдвигает)

    compact = ev.compact_rows_from_wo(wo)
    json.dumps(compact)  # JSON-safe
    c0 = compact[0]
    assert c0["misc_items"] == [{"description": "EPA", "price": 3, "quantity": 2, "partIndex": 0}]
    assert c0["parts"][0]["part_id"] == str(pid)
    assert c0["parts"][0]["misc_charge_description"] == ""
    assert c0["parts"][0]["unit"] == "quart"
    assert c0["assigned_mechanics"] == [{"user_id": str(uid), "percent": 100.0, "name": "Mike", "role": "mechanic"}]
    assert c0["minted"]["at"].startswith("2026-11-09T08:30:00")
    assert (c0["hours"], c0["hours_source"], c0["labor_total"]) == ("1.5", "tracked", 150.0)
    assert compact[1]["description"] == "" and compact[1]["issue_description"] == "Customer note"
    assert compact[1]["misc_items"] is None


def test_wo_fields_of_office_view():
    cid, uid = ObjectId(), ObjectId()
    date = datetime(2026, 11, 1, tzinfo=timezone.utc)
    wo = {"customer_id": cid, "unit_id": uid, "work_order_date": date, "mileage": 1200,
          "totals": {"is_taxable": True}}
    assert ev.wo_fields_of(wo) == {"customer_id": str(cid), "unit_id": str(uid), "work_order_date": date,
                                   "is_taxable": True, "mileage": 1200}
    assert ev.wo_fields_of({"totals": {}})["is_taxable"] is None


# ───────────────────────── record_view ─────────────────────────

def test_record_view_stores_compact_load_view(shop):
    shop_db, shop_id, new_wo = shop
    user = ObjectId()
    wo = new_wo([{"labor_id": "R1", "labor": {"description": "Brakes"}, "parts": []}], rev=3)
    vid = ev.record_view(shop_db, shop_id, wo, user_id=user, sid="sid-1", channel="mobile", kind="load",
                         protocol=1, wo_fields={"customer_id": "c1"}, now=T0)
    assert vid
    doc = shop_db.wo_edit_views.find_one({"_id": ObjectId(vid)})
    assert doc["shop_id"] == shop_id and doc["work_order_id"] == wo["_id"]
    assert (doc["user_id"], doc["sid"], doc["channel"], doc["kind"], doc["protocol"]) == (
        str(user), "sid-1", "mobile", "load", 1)
    assert doc["labors_rev"] == 3
    assert doc["labors"][0]["labor_id"] == "R1" and doc["labors"][0]["description"] == "Brakes"
    assert doc["wo_fields"] == {"customer_id": "c1"}
    assert "minted" not in doc


def test_record_view_sent_with_belief_rows_and_minted(shop):
    shop_db, shop_id, new_wo = shop
    wo = new_wo([])
    belief = [R("R1", "Brakes"), R("N1", "Oil")]
    vid = ev.record_view(shop_db, shop_id, wo, user_id="u1", sid="s1", channel="mobile", kind="sent",
                         rows=belief, minted=["N1"], labors_rev=5, now=T0)
    doc = shop_db.wo_edit_views.find_one({"_id": ObjectId(vid)})
    assert [r["labor_id"] for r in doc["labors"]] == ["R1", "N1"]
    assert doc["minted"] == ["N1"]
    assert doc["labors_rev"] == 5


def test_record_view_skips_without_permission_paid_and_never_raises(shop):
    shop_db, shop_id, new_wo = shop
    wo = new_wo([])
    assert ev.record_view(shop_db, shop_id, wo, user_id="u1", sid="s1", channel="office", can_edit=False) == ""
    paid = new_wo([], status="paid")
    assert ev.record_view(shop_db, shop_id, paid, user_id="u1", sid="s1", channel="office") == ""
    assert ev.record_view(shop_db, shop_id, wo, user_id="u1", sid="s1", channel="fax") == ""
    assert ev.record_view(shop_db, shop_id, None, user_id="u1", sid="s1", channel="office") == ""
    assert ev.record_view(None, shop_id, wo, user_id="u1", sid="s1", channel="office") == ""
    assert shop_db.wo_edit_views.count_documents({"work_order_id": {"$in": [wo["_id"], paid["_id"]]}}) == 0


def test_running_labor_ids(shop):
    shop_db, shop_id, new_wo = shop
    wo, other = new_wo([]), new_wo([])
    shop_db.wo_time_logs.insert_many([
        {"shop_id": shop_id, "work_order_id": wo["_id"], "labor_id": "L1", "stopped_at": None},
        {"shop_id": shop_id, "work_order_id": wo["_id"], "labor_id": "L2", "stopped_at": T0},
        {"shop_id": shop_id, "work_order_id": other["_id"], "labor_id": "L3", "stopped_at": None},
    ])
    assert ev.running_labor_ids(shop_db, shop_id, wo["_id"]) == {"L1"}
    assert ev.running_labor_ids(shop_db, shop_id, "bad-id") == set()


# ───────────────────────── find_base: новый клиент ─────────────────────────

def test_is_new_client():
    assert ev.is_new_client({"merge_protocol": 1})
    assert ev.is_new_client({"merge_protocol": "1"})
    assert ev.is_new_client({"view_id": ""})
    assert not ev.is_new_client({})
    assert not ev.is_new_client({"merge_protocol": 0})
    assert not ev.is_new_client(None)


def test_find_base_new_client_resolves_own_view(shop):
    shop_db, shop_id, new_wo = shop
    user = str(ObjectId())
    wo = new_wo([{"labor_id": "R1", "labor": {"description": "Brakes"}, "parts": []}])
    vid = ev.record_view(shop_db, shop_id, wo, user_id=user, sid="s1", channel="office", protocol=1,
                         wo_fields={"customer_id": "c1"}, now=T0)
    choice = ev.find_base(shop_db, shop_id, wo["_id"], data={"merge_protocol": 1, "view_id": vid},
                          channel="office", user_id=user, sid="other-sid", request_started_at=T0,
                          mine_rows=[], is_mechanic=False)
    assert choice.reason == "view" and choice.legacy is False
    assert [r["labor_id"] for r in choice.rows] == ["R1"]
    assert choice.view_id == vid and choice.kind == "load"
    assert choice.wo_fields == {"customer_id": "c1"}
    assert choice.note == "" and choice.force_foreign is False


def test_find_base_new_client_sent_view_exposes_minted(shop):
    shop_db, shop_id, new_wo = shop
    wo = new_wo([])
    vid = ev.record_view(shop_db, shop_id, wo, user_id="u1", sid="s1", channel="mobile", kind="sent",
                         protocol=1, rows=[R("N1", "Oil")], minted=["N1"], now=T0)
    choice = ev.find_base(shop_db, shop_id, wo["_id"], data={"view_id": vid}, channel="mobile",
                          user_id="u1", sid="s1", request_started_at=T0, mine_rows=[], is_mechanic=True)
    assert choice.kind == "sent" and choice.minted == frozenset({"N1"})


def test_find_base_new_client_unresolvable_view_is_conservative_never_legacy(shop, seed):
    shop_db, shop_id, new_wo = shop
    user, intruder = str(ObjectId()), str(ObjectId())
    wo, other_wo = new_wo([]), new_wo([])
    vid = ev.record_view(shop_db, shop_id, wo, user_id=user, sid="s1", channel="mobile", protocol=1,
                         rows=[R("R1", "Brakes")], now=T0)
    # Легаси-вьюха того же устройства есть — новому клиенту она не подходит.
    ev.record_view(shop_db, shop_id, wo, user_id=user, sid="s1", channel="mobile", protocol=0,
                   rows=[R("R1", "Brakes")], now=T0)
    later = T0 + timedelta(minutes=1)
    cases = [
        dict(work_order_id=wo["_id"], shop_id=shop_id, user_id=intruder, data={"merge_protocol": 1, "view_id": vid}),
        dict(work_order_id=other_wo["_id"], shop_id=shop_id, user_id=user, data={"merge_protocol": 1, "view_id": vid}),
        dict(work_order_id=wo["_id"], shop_id=seed["shop_b"]["_id"], user_id=user, data={"view_id": vid}),
        dict(work_order_id=wo["_id"], shop_id=shop_id, user_id=user, data={"merge_protocol": 1, "view_id": ""}),
        dict(work_order_id=wo["_id"], shop_id=shop_id, user_id=user, data={"view_id": "not-an-id"}),
        dict(work_order_id=wo["_id"], shop_id=shop_id, user_id=user, data={"merge_protocol": 1}),
    ]
    for case in cases:
        choice = ev.find_base(shop_db, case["shop_id"], case["work_order_id"], data=case["data"],
                              channel="mobile", user_id=case["user_id"], sid="s1",
                              request_started_at=later, mine_rows=[R("R1", "Brakes")], is_mechanic=True)
        assert choice.rows is None, case
        assert choice.legacy is False
        assert choice.reason == "view_unresolved"
        assert choice.note == ev.UNRESOLVED_BASE_NOTE
        assert choice.force_foreign is True
        kwargs = choice.ctx_kwargs()
        assert kwargs["extra_notes"] == (ev.UNRESOLVED_BASE_NOTE,) and kwargs["force_foreign"] is True


# ───────────────────────── find_base: легаси ─────────────────────────

def test_legacy_office_and_mechanic_web_are_conservative(shop):
    shop_db, shop_id, new_wo = shop
    wo = new_wo([])
    for channel in ("office", "mechanic_web"):
        ev.record_view(shop_db, shop_id, wo, user_id="u1", sid="s1", channel=channel, rows=[R("R1", "x")], now=T0)
        choice = ev.find_base(shop_db, shop_id, wo["_id"], data={}, channel=channel, user_id="u1", sid="s1",
                              request_started_at=T0 + timedelta(minutes=1), mine_rows=[R("R1", "x")],
                              is_mechanic=channel == "mechanic_web")
        assert (choice.rows, choice.legacy, choice.reason) == (None, True, "legacy_conservative")


def test_legacy_mobile_without_views_is_conservative(shop):
    shop_db, shop_id, new_wo = shop
    wo = new_wo([])
    choice = ev.find_base(shop_db, shop_id, wo["_id"], data={}, channel="mobile", user_id="u1", sid="s1",
                          request_started_at=T0, mine_rows=[R("R1", "x")], is_mechanic=True)
    assert (choice.rows, choice.legacy, choice.reason) == (None, True, "no_views")


def _legacy_choice(shop_db, shop_id, wo, user, sid, started, mine, is_mechanic=True):
    return ev.find_base(shop_db, shop_id, wo["_id"], data={}, channel="mobile", user_id=user, sid=sid,
                        request_started_at=started, mine_rows=mine, is_mechanic=is_mechanic)


def test_legacy_mobile_non_adopted_refetch_does_not_delete_colleague_job(shop):
    """Ревью legacy-clients #0: механик A печатает во время рефетча старого
    приложения — рефетч выброшен, база остаётся его sent-вьюхой, и работа
    механика B (M) не удаляется, а новая строка A не дублируется."""
    shop_db, shop_id, new_wo = shop
    a, sid_a = str(ObjectId()), "dev-a"
    wo = new_wo([])

    # 1. A открыл WO: [R1].
    ev.record_view(shop_db, shop_id, wo, user_id=a, sid=sid_a, channel="mobile", rows=[R("R1", "Brakes")], now=T0)
    # 2. B добавил M.
    current = [R("R1", "Brakes"), R("M", "Mechanic B job")]
    # 3. Автосейв A с новой строкой без id.
    t1 = T0 + timedelta(minutes=1)
    mine1 = [M("R1", "Brakes"), M("", "Oil chan")]
    c1 = _legacy_choice(shop_db, shop_id, wo, a, sid_a, t1, mine1)
    assert c1.reason == "adopted_load"
    r1 = merge_labors(c1.rows, current, mine1, MECHANIC_SPEC, ctx=_ctx(c1, a, sid_a, t1))
    new_id = r1.labor_ids[1]
    assert ids(r1) == ["R1", new_id, "M"]
    ev.record_view(shop_db, shop_id, wo, user_id=a, sid=sid_a, channel="mobile", kind="sent", rows=r1.belief,
                   minted=r1.minted, now=t1 + timedelta(seconds=1))
    # 4. Рефетч старого приложения (id-less строка) — A печатал, рефетч выброшен.
    ev.record_view(shop_db, shop_id, wo, user_id=a, sid=sid_a, channel="mobile", rows=r1.rows,
                   now=t1 + timedelta(seconds=4))
    # 5. Следующий автосейв A: строка всё ещё без id.
    t3 = t1 + timedelta(seconds=10)
    mine2 = [M("R1", "Brakes"), M("", "Oil change")]
    c2 = _legacy_choice(shop_db, shop_id, wo, a, sid_a, t3, mine2)
    assert c2.reason == "adopted_sent" and c2.kind == "sent"
    assert c2.minted == frozenset({new_id})
    r2 = merge_labors(c2.rows, r1.rows, mine2, MECHANIC_SPEC, ctx=_ctx(c2, a, sid_a, t3))
    assert ids(r2) == ["R1", new_id, "M"]  # M жив, новая строка не задвоилась
    assert r2.rows[1]["description"] == "Oil change"
    assert r2.stats["deleted_ids"] == []


def test_legacy_mobile_adopted_refetch_allows_deleting_seen_row(shop):
    shop_db, shop_id, new_wo = shop
    a, sid_a = str(ObjectId()), "dev-a"
    wo = new_wo([])
    current = [R("R1", "Brakes"), R("L1", "Oil", minted={"by": a, "sid": sid_a, "at": T0, "ck": ""}),
               R("M", "Mechanic B job")]
    ev.record_view(shop_db, shop_id, wo, user_id=a, sid=sid_a, channel="mobile", kind="sent",
                   rows=[R("R1", "Brakes"), R("L1", "Oil")], minted=["L1"], now=T0)
    ev.record_view(shop_db, shop_id, wo, user_id=a, sid=sid_a, channel="mobile", rows=current,
                   now=T0 + timedelta(seconds=3))
    # Приложение подхватило рефетч (знает L1 по id) и A удаляет M.
    started = T0 + timedelta(seconds=20)
    mine = [M("R1", "Brakes"), M("L1", "Oil")]
    choice = _legacy_choice(shop_db, shop_id, wo, a, sid_a, started, mine)
    assert choice.reason == "adopted_load" and choice.kind == "load"
    res = merge_labors(choice.rows, current, mine, MECHANIC_SPEC, ctx=_ctx(choice, a, sid_a, started))
    assert ids(res) == ["R1", "L1"]
    assert res.stats["deleted_ids"] == ["M"]


def test_load_after_sent_is_adopted_only_with_evidence_whatever_the_delay(shop):
    """Ревью D1-01 / D4-03: load после sent (S.minted не пуст) — base, только
    если в mine есть id, которого приложение с состоянием S знать не могло
    (id новой строки S или чужой строки из этой load). Время между S и load
    не решает ничего: медленный рефетч, выброшенный приложением, — не
    «новое открытие формы»."""
    shop_db, shop_id, new_wo = shop
    wo = new_wo([])
    sent = ev.record_view(shop_db, shop_id, wo, user_id="u1", sid="s1", channel="mobile", kind="sent",
                          rows=[R("A", "x"), R("R1", "y")], minted=["R1"], now=T0)
    vid = ev.record_view(shop_db, shop_id, wo, user_id="u1", sid="s1", channel="mobile",
                         rows=[R("A", "x"), R("R1", "y"), R("R2", "colleague")], now=T0 + timedelta(minutes=5))
    later = T0 + timedelta(minutes=6)
    for mine in ([M("A", "x"), M("R1", "y")], [M("A", "x"), M("R2", "colleague")]):
        choice = _legacy_choice(shop_db, shop_id, wo, "u1", "s1", later, mine)
        assert (choice.view_id, choice.reason) == (vid, "adopted_load"), mine
    # Строка S всё ещё без id, чужих id нет — рефетч выброшен: base — S, сколько бы ни прошло.
    choice = _legacy_choice(shop_db, shop_id, wo, "u1", "s1", later, [M("A", "x"), M("", "y 2")])
    assert (choice.view_id, choice.reason, choice.minted) == (sent, "adopted_sent", frozenset({"R1"}))


def test_two_devices_on_one_login_do_not_share_a_base(shop):
    """Один логин механика на телефоне и планшете: база устройства 1 —
    только его вьюхи; работа, добавленная на планшете, не удаляется."""
    shop_db, shop_id, new_wo = shop
    user = str(ObjectId())
    wo = new_wo([])
    v1 = ev.record_view(shop_db, shop_id, wo, user_id=user, sid="phone", channel="mobile",
                        rows=[R("R1", "Brakes")], now=T0)
    ev.record_view(shop_db, shop_id, wo, user_id=user, sid="tablet", channel="mobile",
                   rows=[R("R1", "Brakes"), R("C", "Tablet job")], now=T0 + timedelta(minutes=1))
    current = [R("R1", "Brakes"), R("C", "Tablet job")]
    started = T0 + timedelta(minutes=2)
    mine = [M("R1", "Brakes front")]
    choice = _legacy_choice(shop_db, shop_id, wo, user, "phone", started, mine)
    assert choice.view_id == v1
    res = merge_labors(choice.rows, current, mine, MECHANIC_SPEC, ctx=_ctx(choice, user, "phone", started))
    assert ids(res) == ["R1", "C"]


def test_failed_load_guard(shop):
    shop_db, shop_id, new_wo = shop
    wo = new_wo([])
    ev.record_view(shop_db, shop_id, wo, user_id="u1", sid="s1", channel="mobile",
                   rows=[R("R1", "Brakes"), R("R2", "Oil")], now=T0)
    choice = _legacy_choice(shop_db, shop_id, wo, "u1", "s1", T0 + timedelta(minutes=1), [M("", "Typed after failed load")])
    assert (choice.rows, choice.reason) == (None, "failed_load")
    manager = _legacy_choice(shop_db, shop_id, wo, "u1", "s1", T0 + timedelta(minutes=1),
                             [M("", "Typed after failed load")], is_mechanic=False)
    assert (manager.rows, manager.reason) == (None, "failed_load")


def test_views_recorded_after_request_start_are_ignored(shop):
    shop_db, shop_id, new_wo = shop
    wo = new_wo([])
    early = ev.record_view(shop_db, shop_id, wo, user_id="u1", sid="s1", channel="mobile",
                           rows=[R("R1", "x")], now=T0)
    ev.record_view(shop_db, shop_id, wo, user_id="u1", sid="s1", channel="mobile",
                   rows=[R("R1", "x"), R("R2", "y")], now=T0 + timedelta(minutes=5))
    choice = _legacy_choice(shop_db, shop_id, wo, "u1", "s1", T0 + timedelta(minutes=3), [M("R1", "x")])
    assert choice.view_id == early


def test_legacy_lookup_ignores_new_client_and_merged_views(shop):
    shop_db, shop_id, new_wo = shop
    wo = new_wo([])
    legacy = ev.record_view(shop_db, shop_id, wo, user_id="u1", sid="s1", channel="mobile",
                            rows=[R("R1", "x")], now=T0)
    ev.record_view(shop_db, shop_id, wo, user_id="u1", sid="s1", channel="mobile", protocol=1,
                   rows=[R("R1", "x"), R("R2", "y")], now=T0 + timedelta(minutes=1))
    ev.record_view(shop_db, shop_id, wo, user_id="u1", sid="s1", channel="mobile", kind="merged",
                   rows=[R("R1", "x"), R("R3", "z")], now=T0 + timedelta(minutes=2))
    choice = _legacy_choice(shop_db, shop_id, wo, "u1", "s1", T0 + timedelta(minutes=3), [M("R1", "x")])
    assert choice.view_id == legacy


# ───────────────────────── консенсус менеджера ─────────────────────────

def test_manager_push_tap_details_between_form_load_and_save_does_not_delete(shop):
    """Ревью legacy-clients #2: менеджер открыл форму (F1), механик добавил C
    и нажал Done, менеджер открыл детали из пуша (D2) и вернулся к форме."""
    shop_db, shop_id, new_wo = shop
    manager, sid = str(ObjectId()), "mgr-phone"
    wo = new_wo([])
    form = [R("A", "Brakes", hours="1.0", rate_code="std", labor_total=100.0), R("B", "Oil", hours="0.5", rate_code="std",
                                                                            labor_total=50.0)]
    ev.record_view(shop_db, shop_id, wo, user_id=manager, sid=sid, channel="mobile", rows=form, now=T0)
    current = form + [R("C", "Mechanic job")]
    ev.record_view(shop_db, shop_id, wo, user_id=manager, sid=sid, channel="mobile", rows=current,
                   now=T0 + timedelta(minutes=2))
    started = T0 + timedelta(minutes=3)

    mine = [dict(form[0], description="Brakes and rotors"), dict(form[1])]
    choice = _legacy_choice(shop_db, shop_id, wo, manager, sid, started, mine, is_mechanic=False)
    assert choice.reason == "consensus" and choice.kind == "consensus"
    assert [r["labor_id"] for r in choice.rows] == ["A", "B"]
    res = merge_labors(choice.rows, current, mine, MOBILE_MANAGER_SPEC, ctx=_ctx(choice, manager, sid, started))
    assert ids(res) == ["A", "B", "C"]
    assert res.rows[0]["description"] == "Brakes and rotors"

    # Удалить строку, которую менеджер видел во всех версиях, можно.
    res = merge_labors(choice.rows, current, [mine[0]], MOBILE_MANAGER_SPEC, ctx=_ctx(choice, manager, sid, started))
    assert ids(res) == ["A", "C"]


def test_manager_consensus_window_without_sent_and_cut_by_sent(shop):
    """Без sent-вьюхи кандидаты — load за MANAGER_FORM_WINDOW (24 ч): форма,
    открытая 45 минут назад, — ещё кандидат (ревью D1-02: при окне 30 мин
    она выпадала); старше окна — нет."""
    shop_db, shop_id, new_wo = shop
    wo = new_wo([])
    ev.record_view(shop_db, shop_id, wo, user_id="m1", sid="s1", channel="mobile", rows=[R("A", "x")], now=T0)
    late = T0 + timedelta(minutes=45)
    choice = _legacy_choice(shop_db, shop_id, wo, "m1", "s1", late, [M("A", "x")], is_mechanic=False)
    assert (choice.reason, [r["labor_id"] for r in choice.rows]) == ("consensus", ["A"])
    too_late = T0 + ev.MANAGER_FORM_WINDOW + timedelta(minutes=1)
    choice = _legacy_choice(shop_db, shop_id, wo, "m1", "s1", too_late, [M("A", "x")], is_mechanic=False)
    assert (choice.rows, choice.reason) == (None, "no_candidates")

    ev.record_view(shop_db, shop_id, wo, user_id="m1", sid="s1", channel="mobile", kind="sent",
                   rows=[R("A", "x")], now=T0 + timedelta(minutes=50))
    ev.record_view(shop_db, shop_id, wo, user_id="m1", sid="s1", channel="mobile",
                   rows=[R("A", "x"), R("B", "y")], now=T0 + timedelta(minutes=55))
    choice = _legacy_choice(shop_db, shop_id, wo, "m1", "s1", T0 + timedelta(hours=3),
                            [M("A", "x"), M("B", "y")], is_mechanic=False)
    assert [r["labor_id"] for r in choice.rows] == ["A", "B"]  # только вьюхи после sent, окно не нужно


def test_manager_stale_form_left_under_a_newer_one_keeps_colleague_job():
    """Ревью D1-V1: форма F1 открыта (L1), механик добавил X, пуш → детали
    (L2) → правка → форма F2 (L3) сохранена (S знает X) → назад, детали
    (L4) → назад к устаревшей F1 → Save. F1 держит id только своей L1, а
    форма, сохранившая S, знала X, которого в L1 нет, — L1 другой формы и
    остаётся кандидатом: X не удаляется."""
    a, x = R("A", "Inspect brakes"), R("X", "Replace caliper")
    views = _views_at(
        ("L1", "load", timedelta(0), [a], None),
        ("L2", "load", timedelta(minutes=2), [a, x], None),
        ("L3", "load", timedelta(minutes=3), [a, x], None),
        ("S", "sent", timedelta(minutes=4), [a, dict(x, hours="1.5")], []),
        ("L4", "load", timedelta(minutes=5), [a, dict(x, hours="1.5")], None),
    )
    started = T0 + timedelta(minutes=6)
    mine = [M("A", "Inspect brakes (pads 3mm)")]
    choice = ev.choose_base_from_views(views, channel="mobile", is_mechanic=False, mine_rows=mine,
                                       request_started_at=started)
    assert choice.reason == "consensus" and [r["labor_id"] for r in choice.rows] == ["A"]
    current = [a, dict(x, hours="1.5")]
    res = merge_labors(choice.rows, current, mine, MOBILE_MANAGER_SPEC, ctx=_ctx(choice, "m1", "s1", started))
    assert [r["labor_id"] for r in res.rows] == ["A", "X"]
    assert res.rows[1]["hours"] == "1.5"


def test_manager_stale_form_under_a_newer_one_keeps_a_job_both_loads_could_not_tell_apart():
    """Ревью F1-RR-03: F1 открыта (L1), пуш → детали (L2) → правка → F2 (L3);
    работу N добавили (a) в самой F2 или (b) механик, пока F2 была открыта
    (F2 её не знала — в S её нет). F2 сохранена (S), назад — детали
    перечитаны (L4), назад к устаревшей F1 → Save. Load F1 и F2 держат одни
    и те же id — правило D1-V1 их не различает. После S одна load (экран
    деталей после Save), значит, сохраняет форма, открытая до S: N остаётся.
    Новая форма после S (ещё одна load — её открытие) удалить N может."""
    a, n = R("A", "Inspect brakes"), R("N", "Replace caliper")
    mine = [M("A", "Inspect brakes (pads 3mm)")]
    for s_rows, minted in (([a, n], ["N"]), ([a], [])):
        stacked = _views_at(
            ("L1", "load", timedelta(0), [a], None),
            ("L2", "load", timedelta(minutes=1), [a], None),
            ("L3", "load", timedelta(minutes=2), [a], None),
            ("S", "sent", timedelta(minutes=3), s_rows, minted),
            ("L4", "load", timedelta(minutes=4), [a, n], None),
        )
        started = T0 + timedelta(minutes=6)
        choice = ev.choose_base_from_views(stacked, channel="mobile", is_mechanic=False, mine_rows=mine,
                                           request_started_at=started)
        assert [r["labor_id"] for r in choice.rows] == ["A"], minted
        res = merge_labors(choice.rows, [a, n], mine, MOBILE_MANAGER_SPEC, ctx=_ctx(choice, "m1", "s1", started))
        assert [r["labor_id"] for r in res.rows] == ["A", "N"], minted

        fresh = stacked + _views_at(("L5", "load", timedelta(minutes=5), [a, n], None))
        choice = ev.choose_base_from_views(fresh, channel="mobile", is_mechanic=False, mine_rows=[M("A", "x")],
                                           request_started_at=started)
        assert [r["labor_id"] for r in choice.rows] == ["A", "N"], minted
        res = merge_labors(choice.rows, [a, n], [M("A", "x")], MOBILE_MANAGER_SPEC,
                           ctx=_ctx(choice, "m1", "s1", started))
        assert res.stats["deleted_ids"] == ["N"], minted

    # Цена: экран деталей после Save не перечитался (GET не дошёл), сразу
    # открыта новая форма — первое удаление N не проходит (N остаётся), со
    # следующего Save (детали перечитаны, форма открыта заново) — проходит.
    added = _views_at(
        ("L1", "load", timedelta(0), [a], None),
        ("S", "sent", timedelta(minutes=1), [a, n], ["N"]),
        ("L2", "load", timedelta(minutes=2), [a, n], None),
    )
    started = T0 + timedelta(minutes=3)
    choice = ev.choose_base_from_views(added, channel="mobile", is_mechanic=False, mine_rows=[M("A", "x")],
                                       request_started_at=started)
    assert [r["labor_id"] for r in choice.rows] == ["A"]
    retry = added + _views_at(("S2", "sent", timedelta(minutes=3), [a], []),
                              ("L3", "load", timedelta(minutes=4), [a, n], None),
                              ("L4", "load", timedelta(minutes=5), [a, n], None))
    choice = ev.choose_base_from_views(retry, channel="mobile", is_mechanic=False, mine_rows=[M("A", "x")],
                                       request_started_at=T0 + timedelta(minutes=6))
    assert [r["labor_id"] for r in choice.rows] == ["A", "N"]


def test_manager_consensus_ignores_the_load_of_the_form_that_saved():
    """Обычный путь: форма (L1) сохранена (S), детали (L2), форма снова (L3),
    менеджер удаляет X — load формы, сохранившей S, в кандидаты не
    попадает, удаление работает. Load, где нет какого-то id из mine, тоже
    не кандидат."""
    a, x, y = R("A", "Brakes"), R("X", "Duplicate job"), R("Y", "Wipers")
    flow = [
        ("L1", "load", timedelta(minutes=1), [a, x], None),
        ("S", "sent", timedelta(minutes=2), [a, x], []),
        ("L2", "load", timedelta(minutes=3), [a, x], None),
        ("L3", "load", timedelta(minutes=4), [a, x], None),
    ]
    started = T0 + timedelta(minutes=5)
    mine = [M("A", "Brakes")]
    choice = ev.choose_base_from_views(_views_at(*flow), channel="mobile", is_mechanic=False, mine_rows=mine,
                                       request_started_at=started)
    assert [r["labor_id"] for r in choice.rows] == ["A", "X"]

    # Цена защиты от устаревшей формы (D1-V1): было и более старое открытие
    # без X (L0) — оно могло быть той самой формой, и первое удаление X не
    # проходит (X остаётся, ничего не теряется). Сохранение записывает sent
    # с belief [A] — со второй попытки L0 уже «форма, сохранившая S», и X
    # удаляется. Вторая попытка — из новой формы: после S2 экран деталей
    # перечитан (L5) и форма открыта заново (L6).
    first = _views_at(("L0", "load", timedelta(0), [a], None), *flow)
    choice = ev.choose_base_from_views(first, channel="mobile", is_mechanic=False, mine_rows=mine,
                                       request_started_at=started)
    assert [r["labor_id"] for r in choice.rows] == ["A"]
    second = first + _views_at(("S2", "sent", timedelta(minutes=6), [a], []),
                               ("L5", "load", timedelta(minutes=7), [a, x], None),
                               ("L6", "load", timedelta(minutes=7, seconds=30), [a, x], None))
    choice = ev.choose_base_from_views(second, channel="mobile", is_mechanic=False, mine_rows=mine,
                                       request_started_at=T0 + timedelta(minutes=8))
    assert [r["labor_id"] for r in choice.rows] == ["A", "X"]

    # Без sent: load, в которой нет id из mine (L0 без Y), — не кандидат.
    no_sent = _views_at(("L0", "load", timedelta(0), [a], None), ("L1", "load", timedelta(minutes=1), [a, y], None))
    rows, reason = ev.consensus_base(no_sent, started, mine_rows=[M("Y", "Wipers")])
    assert (reason, [r["labor_id"] for r in rows]) == ("consensus", ["A", "Y"])


def test_consensus_keeps_only_parts_seen_everywhere_and_remaps_misc():
    misc = [{"description": "EPA", "price": 3, "quantity": 1, "partIndex": 1},
            {"description": "Env", "price": 5, "quantity": 1, "manual": True}]
    p = lambda pid: {"part_id": pid, "part_number": "", "description": pid, "qty": 1}  # noqa: E731
    d0 = {"kind": "load", "created_at": T0, "labors": [
        {**R("A", "Job", [p("P1"), p("P2")]), "misc_items": misc}]}
    f1 = {"kind": "load", "created_at": T0 + timedelta(minutes=1), "labors": [
        {**R("A", "Job", [p("P2")]), "misc_items": [dict(misc[0], partIndex=0), misc[1]]}]}
    rows, reason = ev.consensus_base([d0, f1], T0 + timedelta(minutes=2))
    assert reason == "consensus"
    assert [x["part_id"] for x in rows[0]["parts"]] == ["P2"]
    assert rows[0]["misc_items"] == [dict(misc[0], partIndex=0), misc[1]]


def test_consensus_takes_mine_value_seen_in_any_candidate_so_tracked_hours_survive():
    """Самая старая копия видела 1.0 ч, форма — 2.0 ч; менеджер часы не
    трогал (шлёт 2.0), таймер с тех пор дал 2.5 — побеждают tracked 2.5."""
    old = {"kind": "load", "created_at": T0, "labors": [
        R("A", "Old text", hours="1.0", rate_code="std", labor_total=100.0, hours_source="tracked")]}
    form = {"kind": "load", "created_at": T0 + timedelta(minutes=5), "labors": [
        R("A", "New text", hours="2.0", rate_code="std", labor_total=200.0, hours_source="tracked")]}
    mine = [R("A", "New text", hours="2.0", rate_code="std", labor_total=200.0)]
    started = T0 + timedelta(minutes=10)
    choice = ev.choose_base_from_views([old, form], channel="mobile", is_mechanic=False, mine_rows=mine,
                                       request_started_at=started)
    assert choice.rows[0]["hours"] == "2.0" and choice.rows[0]["description"] == "New text"
    current = [R("A", "New text", hours="2.5", rate_code="std", labor_total=250.0, hours_source="tracked")]
    res = merge_labors(choice.rows, current, mine, MOBILE_MANAGER_SPEC,
                       ctx=_ctx(choice, "m1", "s1", started, rates={"std": 100.0}))
    row = res.rows[0]
    assert (row["hours"], row["labor_total"], row["hours_source"]) == ("2.5", 250.0, "tracked")


def test_choose_adopted_view_pure_rules():
    s = {"_id": "S", "kind": "sent", "created_at": T0, "minted": ["N1"], "labors": [R("A", "x"), R("N1", "y")]}
    refetch = {"_id": "L", "kind": "load", "created_at": T0 + timedelta(seconds=5),
               "labors": [R("A", "x"), R("N1", "y")]}
    started = T0 + timedelta(minutes=1)
    assert ev.choose_adopted_view([s, refetch], [M("A", "x"), M("", "y")], started)[0]["_id"] == "S"
    assert ev.choose_adopted_view([s, refetch], [M("A", "x"), M("N1", "y")], started)[0]["_id"] == "L"
    no_minted = dict(s, minted=[])
    assert ev.choose_adopted_view([no_minted, refetch], [M("A", "x")], started)[0]["_id"] == "L"
    assert ev.choose_adopted_view([], [M("A", "x")], started) == (None, "no_views")


def _views_at(*specs):
    """[(id, kind, сдвиг от T0, строки, minted)] → вьюхи для чистых правил."""
    out = []
    for vid, kind, delta, rows, minted in specs:
        v = {"_id": vid, "kind": kind, "created_at": T0 + delta, "labors": rows}
        if kind == "sent":
            v["minted"] = minted
        out.append(v)
    return out


@pytest.mark.parametrize("delay", [timedelta(seconds=5), timedelta(seconds=20), timedelta(minutes=10),
                                   timedelta(hours=3)])
def test_discarded_refetch_is_never_adopted_whatever_its_latency(delay):
    """Ревью D1-01 / D4-03: рефетч после автосейва (в нём новая строка N1 S
    и чужая работа B) приложение выбросило — строка в mine всё ещё без id.
    Base — S при любой задержке рефетча; minted S остаётся для привязки."""
    # (B — работа коллеги, добавленная между S и рефетчем.)
    views = _views_at(
        ("L0", "load", timedelta(0), [R("A", "x")], None),
        ("S", "sent", timedelta(seconds=30), [R("A", "x"), R("N1", "Oil chan")], ["N1"]),
        ("L", "load", timedelta(seconds=30) + delay, [R("A", "x"), R("B", "Replace hose"), R("N1", "Oil chan")],
         None),
    )
    started = T0 + timedelta(seconds=31) + delay
    mine = [M("A", "x"), M("", "Oil change")]
    choice = ev.choose_base_from_views(views, channel="mobile", is_mechanic=True, mine_rows=mine,
                                       request_started_at=started)
    assert (choice.view_id, choice.reason, choice.minted) == ("S", "adopted_sent", frozenset({"N1"}))
    # Приложение подхватило рефетч (знает id N1 или чужой строки B) — base он.
    for adopted in ([M("A", "x"), M("N1", "Oil change")], [M("A", "x"), M("B", "Replace hose"), M("", "Oil")]):
        choice = ev.choose_base_from_views(views, channel="mobile", is_mechanic=True, mine_rows=adopted,
                                           request_started_at=started)
        assert (choice.view_id, choice.reason) == ("L", "adopted_load"), adopted


def test_minted_rows_gone_from_a_later_load_do_not_claim_idless_rows():
    """Форму открыли заново после того, как офис удалил N1 (новую строку S),
    и добавили такую же работу: base — S (доказательства нет), но N1 к
    id-less строке не привязывается — работа создаётся, а не выбрасывается
    как «удалённая другим» (ревью D4-03, проверка на перестраховку)."""
    views = _views_at(
        ("S", "sent", timedelta(0), [R("A", "x"), R("N1", "Oil change")], ["N1"]),
        ("L1", "load", timedelta(seconds=3), [R("A", "x"), R("N1", "Oil change")], None),
        ("L2", "load", timedelta(minutes=2), [R("A", "x")], None),
    )
    mine = [M("A", "x"), M("", "Oil change")]
    started = T0 + timedelta(minutes=3)
    choice = ev.choose_base_from_views(views, channel="mobile", is_mechanic=True, mine_rows=mine,
                                       request_started_at=started)
    assert (choice.view_id, choice.reason, choice.minted) == ("S", "adopted_sent_minted_gone", frozenset())
    res = merge_labors(choice.rows, [R("A", "x")], mine, MECHANIC_SPEC, ctx=_ctx(choice, "u1", "s1", started))
    assert [r["description"] for r in res.rows] == ["x", "Oil change"]
    assert res.rows[1]["labor_id"] != "N1" and res.origins[1]["kind"] == "new"


def test_post_save_refetch_without_the_minted_row_does_not_prove_a_fresh_mount():
    """Ревью F1-RR-02: офис удалил N (новую работу механика), автосейв S2 её
    пропустил («removed by another user»), а рефетч после S2 (уже без N)
    приложение выбросило — механик печатал, строка у него всё ещё без id.
    Первая load после S — рефетч самого S: подхвачен он или выброшен, не
    узнать, поэтому он не доказывает нового открытия формы — N остаётся в
    base_minted, и работа не создаётся заново (раньше создавалась: удаление
    офиса молча откатывалось). Ещё одна load после S (форму открыли заново)
    — доказывает, и такую же работу, добавленную после этого, создаём."""
    a, n = R("A", "Diagnose leak"), R("N", "Oil change")
    a2 = dict(a, description="Diagnose leak - hose")
    views = _views_at(
        ("S1", "sent", timedelta(0), [a, n], ["N"]),
        ("L1", "load", timedelta(seconds=2), [a, n], None),     # рефетч S1, выброшен
        ("S2", "sent", timedelta(seconds=40), [a2, n], ["N"]),  # N пропущена
        ("L2", "load", timedelta(seconds=42), [a2], None),      # рефетч S2 без N, выброшен
    )
    current = [dict(a2)]
    mine = [M("A", "Diagnose leak - hose clamp"), M("", "Oil change")]
    started = T0 + timedelta(seconds=50)
    choice = ev.choose_base_from_views(views, channel="mobile", is_mechanic=True, mine_rows=mine,
                                       request_started_at=started)
    assert (choice.view_id, choice.reason, choice.minted) == ("S2", "adopted_sent", frozenset({"N"}))
    res = merge_labors(choice.rows, current, mine, MECHANIC_SPEC, ctx=_ctx(choice, "u1", "s1", started))
    assert [r["description"] for r in res.rows] == ["Diagnose leak - hose clamp"]
    assert res.stats["skipped_ids"] == ["N"] and res.stats["rows_new"] == 0

    # Форму открыли заново (ещё одна load без N) и добавили такую же работу — она создаётся.
    reopened = views + _views_at(("L3", "load", timedelta(minutes=3), [a2], None))
    mine = [M("A", "Diagnose leak - hose"), M("", "Oil change")]
    started = T0 + timedelta(minutes=4)
    choice = ev.choose_base_from_views(reopened, channel="mobile", is_mechanic=True, mine_rows=mine,
                                       request_started_at=started)
    assert (choice.view_id, choice.reason, choice.minted) == ("S2", "adopted_sent_minted_gone", frozenset())
    res = merge_labors(choice.rows, current, mine, MECHANIC_SPEC, ctx=_ctx(choice, "u1", "s1", started))
    assert [r["description"] for r in res.rows] == ["Diagnose leak - hose", "Oil change"]
    assert res.origins[1]["kind"] == "new"


def test_adopted_load_is_the_newest_one_holding_every_id_of_mine():
    """Приложение держит id только своей load: load, где какого-то id из mine
    нет (строку удалили между двумя открытиями), не может быть основой."""
    views = _views_at(
        ("S", "sent", timedelta(0), [R("A", "x"), R("N1", "y")], ["N1"]),
        ("L1", "load", timedelta(seconds=3), [R("A", "x"), R("N1", "y")], None),
        ("L2", "load", timedelta(minutes=5), [R("N1", "y"), R("C", "colleague job")], None),
    )
    mine = [M("A", "x 2"), M("N1", "y")]
    choice = ev.choose_base_from_views(views, channel="mobile", is_mechanic=True, mine_rows=mine,
                                       request_started_at=T0 + timedelta(minutes=6))
    assert (choice.view_id, choice.reason) == ("L1", "adopted_load")


# ───────────────────────── индексы ─────────────────────────

def _index_by_name(col):
    return {name: info for name, info in col.index_information().items()}


def test_wo_edit_views_indexes_with_ttl(shop):
    from app.extensions import WO_EDIT_VIEWS_TTL_SECONDS, ensure_wo_edit_views_indexes

    shop_db, _shop_id, _new = shop
    ensure_wo_edit_views_indexes(shop_db)
    ensure_wo_edit_views_indexes(shop_db)  # идемпотентно
    info = _index_by_name(shop_db.wo_edit_views)
    assert info["ttl_wo_edit_views_created"]["expireAfterSeconds"] == WO_EDIT_VIEWS_TTL_SECONDS == 14 * 24 * 3600
    assert [k for k, _d in info["idx_wo_edit_views_lookup"]["key"]] == [
        "shop_id", "work_order_id", "user_id", "sid", "channel", "created_at"]


def test_startup_and_new_shop_index_routines_create_edit_views_indexes(app, mongo):
    from app.blueprints.tenant.routes import ensure_shop_collections_indexes as new_shop_indexes
    from app.extensions import ensure_shop_collections_indexes as startup_indexes

    for name, routine in (("roobico_test_c1_idx_startup", startup_indexes),
                          ("roobico_test_c1_idx_new_shop", new_shop_indexes)):
        mongo.drop_database(name)
        try:
            with app.app_context():
                routine(mongo[name])
            assert "ttl_wo_edit_views_created" in mongo[name].wo_edit_views.index_information()
        finally:
            mongo.drop_database(name)


# ───────────────────────── стыковка с адаптерами ─────────────────────────

def test_merged_rows_feed_compute_labors_and_totals(app, shop):
    """Итог merge (в т.ч. строка, восстановленная из компактной base) —
    валидный вход compute_labors_and_totals: id назначений и запчастей —
    ObjectId, закреплённая база и misc JSON сохраняются байт-в-байт."""
    from app.blueprints.work_orders.services.mobile_editor import compute_labors_and_totals

    shop_db, shop_id, new_wo = shop
    uid, pid = ObjectId(), ObjectId()
    misc = '[{"description":"EPA","price":3,"quantity":2,"partIndex":0,"taxable":true}]'
    r1 = {"labor_id": "R1",
          "labor": {"description": "Brakes", "hours": "1.5", "rate_code": "standard", "hours_source": "tracked",
                    "issue_description": "",
                    "assigned_mechanics": [{"user_id": uid, "name": "Mike", "role": "mechanic", "percent": 100.0}]},
          "parts": [{"part_id": pid, "part_number": "BP-1", "description": "Pads", "qty": 2, "cost": 20.0,
                     "price": 45.0, "core_charge": 0.0, "misc_charge": 0.0, "misc_charge_description": misc,
                     "one_time_part": False, "unit": "each"}]}
    r2 = {"labor_id": "R2",
          "labor": {"description": "Wipers", "hours": "0.5", "rate_code": "standard",
                    "assigned_mechanics": [{"user_id": uid, "name": "Mike", "role": "mechanic", "percent": 100.0}]},
          "parts": []}
    wo = new_wo([r1, r2])
    wo["totals"] = {"labors": [{"labor": 150.0}, {"labor": 50.0}]}
    base = ev.compact_rows_from_wo(wo)
    # Офис удалил R2 после того, как механик открыл WO; механик правит обе строки.
    current = ev.work_rows_from_wo({**wo, "labors": [r1], "totals": {"labors": [{"labor": 150.0}]}})
    mine = [M("R1", "Brakes front", [{"part_id": str(pid), "part_number": "BP-1", "description": "Pads", "qty": 2}]),
            M("R2", "Wipers and fluid")]
    res = merge_labors(base, current, mine, MECHANIC_SPEC, ctx=MergeContext(user_id="u1", sid="s1", legacy=True))
    assert [o["kind"] for o in res.origins] == ["merged", "resurrected"]

    with app.app_context():
        labors, totals = compute_labors_and_totals(shop_db, {"_id": shop_id}, res.rows)
    first, second = labors
    assert [x["labor_id"] for x in labors] == ["R1", "R2"]
    assert first["labor"]["description"] == "Brakes front"
    assert first["labor"]["hours_source"] == "tracked"
    assert first["labor"]["assigned_mechanics"][0]["user_id"] == uid
    assert first["parts"][0]["part_id"] == pid
    assert first["parts"][0]["misc_charge_description"] == misc
    assert second["labor"]["hours"] == "0.5"
    assert second["labor"]["assigned_mechanics"][0]["user_id"] == uid  # из компактной base — снова ObjectId
    assert [b["labor"] for b in totals["labors"]] == [150.0, 50.0]
    assert totals["misc_total"] == 6.0
