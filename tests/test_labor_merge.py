"""
Ядро трёхстороннего merge строк WO (services/labor_merge.py): чистые
тесты без БД — правила §1.5 дизайна и сценарии адверсариального ревью
(пинг-понг двух механиков, устаревший офис, tracked-часы, легаси-привязка
id-less строк, misc JSON, belief).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.blueprints.work_orders.services.labor_merge import (
    MECHANIC_SPEC,
    MOBILE_MANAGER_SPEC,
    OFFICE_SPEC,
    MergeContext,
    is_blank_row,
    lift_misc,
    match_parts,
    merge_labors,
    norm_qty,
    split_misc_items,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
RATES = {"std": 100.0, "premium": 130.0}


# ───────────────────────── фабрики строк ─────────────────────────

def P(pid="", pn="", desc="", qty=1, price=0.0, **kw):
    """Сохранённая запчасть (форма документа WO)."""
    p = {
        "part_id": pid, "part_number": pn, "description": desc, "qty": qty,
        "cost": 0.0, "price": price, "core_charge": 0.0, "misc_charge": 0.0,
        "misc_charge_description": "", "one_time_part": not pid, "unit": "each",
    }
    p.update(kw)
    return p


def R(lid="", desc="", parts=None, *, hours="", rate="", total=None, source="", assigned=None, issue="", **kw):
    """Рабочая строка (форма _stored_block_to_payload)."""
    r = {
        "labor_id": lid, "description": desc, "issue_description": issue,
        "hours": hours, "rate_code": rate, "labor_total": total, "hours_source": source,
        "assigned_mechanics": assigned or [], "parts": parts or [],
    }
    r.update(kw)
    return r


def MP(pid="", pn="", desc="", qty=1):
    """Запчасть от механика (без денег — как шлют приложение и веб-страница)."""
    return {"part_id": pid, "part_number": pn, "description": desc, "qty": qty, "one_time_part": not pid}


def M(lid="", desc="", parts=None, issue="", **kw):
    """Строка механика после stripMoney: hours/rate пустые, денег нет."""
    r = {"labor_id": lid, "description": desc, "issue_description": issue, "hours": "", "rate_code": "",
         "parts": parts or []}
    r.update(kw)
    return r


def ctx(**kw):
    seq = iter(f"N{i}" for i in range(1, 1000))
    base = {"user_id": "u1", "sid": "s1", "now": NOW, "mint_id": lambda: next(seq), "rates": RATES}
    base.update(kw)
    return MergeContext(**base)


def merge(base, current, mine, spec=MECHANIC_SPEC, **kw):
    return merge_labors(base, current, mine, spec, ctx=ctx(**kw))


def ids(res):
    return [r["labor_id"] for r in res.rows]


def descs(res):
    return [r["description"] for r in res.rows]


def misc_of(row):
    return json.loads(row["parts"][0]["misc_charge_description"])


# ───────────────────────── хелперы ─────────────────────────

def test_norm_qty_lenient_for_integral_values():
    assert norm_qty(2) == 2
    assert norm_qty("3") == 3
    assert norm_qty(2.0) == 2
    assert norm_qty("2.0") == 2
    assert norm_qty(1.5) == 0  # как normalize_parts_payload: нецелое не хранится
    assert norm_qty(None) == 0
    assert norm_qty("") == 0


def test_is_blank_row():
    assert is_blank_row(R("A", ""))
    assert is_blank_row(R("A", "", issue="Customer complaint"))
    assert not is_blank_row(R("A", "Brakes"))
    assert not is_blank_row(R("A", "", [P(desc="x")]))


def test_split_misc_items_lifts_first_part_json_only():
    raw = '[{"description":"EPA","price":3,"quantity":1,"partIndex":0}]'
    parts = [P("P0", misc_charge_description=raw), P("P1", misc_charge_description="Legacy text")]
    items, out, original = split_misc_items(parts)
    assert items == [{"description": "EPA", "price": 3, "quantity": 1, "partIndex": 0}]
    assert original == raw
    assert out[0]["misc_charge_description"] == ""
    assert out[1]["misc_charge_description"] == "Legacy text"
    assert parts[0]["misc_charge_description"] == raw  # исходник не тронут

    items, out, original = split_misc_items([P("P0", misc_charge_description="Disposal")])
    assert items is None and original is None
    assert out[0]["misc_charge_description"] == "Disposal"


def test_lift_misc_prefers_compact_misc_items():
    row = {"misc_items": [{"description": "Env", "price": 5, "manual": True}], "parts": [P("P0")]}
    items, parts = lift_misc(row)
    assert items == [{"description": "Env", "price": 5, "manual": True}]
    assert len(parts) == 1


def test_match_parts_exact_alias_and_anon_fallback():
    a = [P("X"), P("X"), P(pn="ABC"), P(desc="Rags"), P(desc="Gloves")]
    b = [P("X"), P(pid="CAT", pn="abc"), P("X"), P(desc="Shop gloves"), P(desc="Rags")]
    m = match_parts(a, b)
    assert m[0] == 0 and m[1] == 2          # key#n
    assert m[2] == 1                         # алиас pn → id
    assert m[3] == 4                         # anon по описанию
    assert m[4] == 3                         # anon по позиции среди оставшихся


def test_match_parts_alias_needs_one_side_without_id():
    assert match_parts([P("A", pn="X1")], [P("B", pn="X1")]) == {}


def test_ctx_may_be_a_dict():
    res = merge_labors([R("A", "Job")], [R("A", "Job")], [M("A", "Job 2")], MECHANIC_SPEC,
                       ctx={"user_id": "u1", "sid": "s1", "now": NOW})
    assert descs(res) == ["Job 2"]


# ───────────────────────── текстовые поля 3-way ─────────────────────────

def test_text_mine_changed_wins():
    res = merge([R("A", "Brakes")], [R("A", "Brakes (office)")], [M("A", "Brakes front")])
    assert descs(res) == ["Brakes front"]


def test_text_untouched_takes_current_and_is_foreign():
    res = merge([R("A", "Brakes")], [R("A", "Brakes front pads")], [M("A", "Brakes")])
    assert descs(res) == ["Brakes front pads"]
    assert res.foreign_any is True
    assert res.foreign_structure is False
    assert res.notes == []


def test_text_without_base_mine_wins():
    res = merge(None, [R("A", "Brakes (office)")], [M("A", "Brakes")], legacy=True)
    assert descs(res) == ["Brakes"]


def test_issue_description_three_way():
    res = merge([R("A", "Job", issue="noise")], [R("A", "Job", issue="noise at 60 mph")],
                [M("A", "Job 2", issue="noise")])
    assert res.rows[0]["issue_description"] == "noise at 60 mph"
    assert res.rows[0]["description"] == "Job 2"


# ───────────────────────── строки: id, новые, привязка ─────────────────────────

def test_new_row_minted_with_marker_and_aligned_outputs():
    res = merge([R("A", "Brakes")], [R("A", "Brakes")],
                [M("A", "Brakes"), M("", "Oil change", client_key="k-1")])
    assert ids(res) == ["A", "N1"]
    assert res.labor_ids == ["A", "N1"]
    assert res.minted == ["N1"]
    assert res.client_keys == {"k-1": "N1"}
    new = res.rows[1]
    assert new["minted"] == {"by": "u1", "sid": "s1", "at": NOW, "ck": "k-1"}
    assert new["client_key"] == "k-1"
    assert res.origins[1]["kind"] == "new"


def test_new_row_uses_new_block_callback_with_final_id():
    seen = []

    def new_block(raw):
        seen.append(raw["labor_id"])
        return {**raw, "rate_code": "std", "hours_source": "", "assigned_mechanics": []}

    res = merge([], [], [M("", "Diag")], new_block=new_block)
    assert seen == ["N1"]
    assert res.rows[0]["rate_code"] == "std"
    assert res.rows[0]["labor_id"] == "N1"


def test_client_markers_from_payload_are_ignored():
    forged = M("", "Job", minted={"by": "someone", "sid": "x"})
    res = merge([], [], [forged])
    assert res.rows[0]["minted"]["by"] == "u1"


def test_client_key_retry_after_lost_response_no_duplicate():
    """Новый клиент: ответ потерялся, повтор с тем же client_key и старой
    вьюхой — это та же строка, не дубль и не «добавлена другим»."""
    base = [R("A", "Brakes")]
    first = merge(base, [R("A", "Brakes")], [M("A", "Brakes"), M("", "Oil", [MP("P1", qty=2)], client_key="k")])
    retry = merge(base, first.rows, [M("A", "Brakes"), M("", "Oil", [MP("P1", qty=2)], client_key="k")])
    assert ids(retry) == ids(first)
    assert retry.client_keys == {"k": first.client_keys["k"]}
    assert retry.minted == [first.client_keys["k"]]
    assert retry.notes == []
    assert retry.foreign_any is False
    assert len(retry.rows[1]["parts"]) == 1


def test_client_keys_map_each_new_row():
    res = merge([], [], [M("", "One", client_key="a"), M("", "Two", client_key="b")])
    assert res.client_keys == {"a": "N1", "b": "N2"}
    assert res.labor_ids == ["N1", "N2"]


def test_legacy_three_consecutive_idless_saves_keep_one_id():
    """Старое приложение не подхватывает рефетч (печатает дальше): строка
    без id приходит снова и снова — id один, без churn и дублей."""
    cur = [R("A", "Brakes")]
    base = [R("A", "Brakes")]
    r1 = merge(base, cur, [M("A", "Brakes"), M("", "Oil chan")], legacy=True)
    lid = r1.labor_ids[1]
    assert r1.minted == [lid]

    r2 = merge(r1.belief, r1.rows, [M("A", "Brakes"), M("", "Oil change")], legacy=True, base_minted=r1.minted)
    assert r2.labor_ids[1] == lid
    assert ids(r2) == ["A", lid]
    assert r2.rows[1]["description"] == "Oil change"
    assert r2.minted == [lid]
    assert r2.notes == []

    r3 = merge(r2.belief, r2.rows, [M("A", "Brakes"), M("", "Oil change 5W30")], legacy=True, base_minted=r2.minted)
    assert ids(r3) == ["A", lid]
    assert r3.rows[1]["description"] == "Oil change 5W30"
    assert r3.rows[1]["minted"]["by"] == "u1"  # метка сохраняется для следующего раза


def test_legacy_overlapping_requests_same_idless_row_no_duplicate():
    """Done нажат во время автосейва: два запроса с одной id-less строкой,
    второй читает WO уже после записи первого, base у обоих одна."""
    base = [R("A", "Brakes")]
    mine = [M("A", "Brakes"), M("", "Preset job", [MP("P1", qty=1)])]
    r1 = merge(base, [R("A", "Brakes")], mine, legacy=True)
    r2 = merge(base, r1.rows, mine, legacy=True)
    assert ids(r2) == ids(r1)
    assert len(r2.rows) == 2
    assert r2.notes == []
    assert r2.foreign_any is False
    assert r2.origins[1]["kind"] == "mapped"


def test_legacy_marker_of_other_device_is_not_mapped():
    """Один логин на двух устройствах: строка устройства 1 не «поглощает»
    новую строку устройства 2 и не удаляется им."""
    base = [R("A", "Brakes")]
    r1 = merge(base, [R("A", "Brakes")], [M("A", "Brakes"), M("", "Job from phone")], legacy=True, sid="s1")
    r2 = merge(base, r1.rows, [M("A", "Brakes"), M("", "Job from tablet")], legacy=True, sid="s2")
    assert len(r2.rows) == 3
    assert "Job from phone" in descs(r2)
    assert "Job from tablet" in descs(r2)
    assert 'Kept job "Job from phone" added by another user.' in r2.notes


def test_legacy_marker_row_known_by_id_is_not_mapped_to_new_row():
    """Строку с меткой клиент уже получил с id (она в его load-base) и
    удалил; новая id-less строка — новая, а не «переименование» старой."""
    marker = {"by": "u1", "sid": "s1", "at": NOW, "ck": ""}
    cur = [R("A", "Brakes"), R("L", "Oil", minted=marker)]
    base = [R("A", "Brakes"), R("L", "Oil")]
    res = merge(base, cur, [M("A", "Brakes"), M("", "Wipers")], legacy=True)
    assert "L" not in ids(res)
    assert res.labor_ids[1] == "N1"
    assert res.stats["deleted_ids"] == ["L"]


def test_legacy_marker_without_base_needs_compatible_description_and_fresh_marker():
    fresh = {"by": "u1", "sid": "s1", "at": NOW - timedelta(minutes=1), "ck": ""}
    cur = [R("A", "Brakes"), R("L", "Brake pads", minted=fresh)]
    other = merge(None, cur, [M("A", "Brakes"), M("", "Oil")], legacy=True)
    assert len(other.rows) == 3  # описание не совпало — новая строка, L сохранена

    same = merge(None, cur, [M("A", "Brakes"), M("", "Brake pads front")], legacy=True)
    assert ids(same) == ["A", "L"]
    assert same.rows[1]["description"] == "Brake pads front"

    stale = {"by": "u1", "sid": "s1", "at": NOW - timedelta(hours=1), "ck": ""}
    cur_stale = [R("A", "Brakes"), R("L", "Brake pads", minted=stale)]
    res = merge(None, cur_stale, [M("A", "Brakes"), M("", "Brake pads")], legacy=True)
    assert len(res.rows) == 3


def _two_minted_rows(**cur_kw):
    mk = {"by": "u1", "sid": "s1", "at": NOW, "ck": ""}
    base = [R("A", "Inspect"), R("x1", "Oil change", minted=mk), R("y1", "Wipers", minted=mk)]
    cur = [R("A", "Inspect"), R("x1", "Oil change", minted=mk), R("y1", "Wipers", minted=mk, **cur_kw)]
    return base, cur


def test_legacy_idless_rows_never_swap_ids_by_position():
    """Ревью D1-07: старое приложение сохранило «Oil change» (x1) и «Wipers»
    (y1), рефетч не подхвачен; пользователь удалил Oil change и добавил
    Brakes. Число строк совпадает, но по позиции пары не строятся поперёк
    описаний: Wipers остаётся y1, Brakes — новая, x1 удаляется."""
    base, cur = _two_minted_rows()
    mine = [M("A", "Inspect"), M("", "Wipers"), M("", "Brakes")]
    res = merge(base, cur, mine, legacy=True, base_minted={"x1", "y1"})
    got = [(r["labor_id"], r["description"], o["kind"]) for r, o in zip(res.rows, res.origins)]
    assert got == [("A", "Inspect", "merged"), ("y1", "Wipers", "mapped"), ("N1", "Brakes", "new")]
    assert res.stats["deleted_ids"] == ["x1"]
    assert res.minted == ["y1", "N1"]

    # Коллега ведёт таймер на x1 — x1 не удаляется; часы на y1 остаются у Wipers.
    base, cur = _two_minted_rows(hours="0.5", hours_source="tracked")
    res = merge(base, cur, mine, legacy=True, base_minted={"x1", "y1"}, running_labor_ids={"x1"})
    rows = {r["labor_id"]: r for r in res.rows}
    assert (rows["y1"]["description"], rows["y1"]["hours"]) == ("Wipers", "0.5")
    assert rows["x1"]["description"] == "Oil change"
    assert 'Kept job "Oil change": someone is working on it.' in res.notes


def test_legacy_idless_row_renamed_completely_still_maps_by_position():
    """Позиционная привязка (без общих описаний) по-прежнему работает внутри
    промежутка: строку переименовали целиком, и ещё добавили новую."""
    mk = {"by": "u1", "sid": "s1", "at": NOW, "ck": ""}
    base = [R("A", "Brakes"), R("x1", "Oil chnge", minted=mk)]
    cur = [R("A", "Brakes"), R("x1", "Oil chnge", minted=mk)]
    res = merge(base, cur, [M("A", "Brakes"), M("", "Synthetic oil service"), M("", "Wipers")],
                legacy=True, base_minted={"x1"})
    assert [(r["labor_id"], r["description"]) for r in res.rows] == [
        ("A", "Brakes"), ("x1", "Synthetic oil service"), ("N1", "Wipers")]


def test_new_client_does_not_use_markers():
    marker = {"by": "u1", "sid": "s1", "at": NOW, "ck": "other-key"}
    cur = [R("A", "Brakes"), R("L", "Oil", minted=marker)]
    res = merge([R("A", "Brakes")], cur, [M("A", "Brakes"), M("", "Oil", client_key="k2")])
    assert len(res.rows) == 3


def test_unknown_id_recreated_keeping_id_without_marker():
    res = merge(None, [R("A", "Brakes")], [M("A", "Brakes"), M("GONE", "Old job")], legacy=True)
    assert ids(res) == ["A", "GONE"]
    assert "minted" not in res.rows[1]
    assert res.origins[1]["kind"] == "unknown"


def test_legacy_wo_without_labor_ids_office_save_does_not_duplicate():
    """Легаси-WO без labor_id, вкладка офиса отрендерена до сохранения id:
    эфемерные id формы сопоставляются со строками без id, не дублируются."""
    cur = [R("", "Brakes", hours="1.0", rate="std", total=100.0), R("", "Oil", hours="0.5", rate="std", total=50.0)]
    mine = [R("E1", "Brakes", hours="1.0", rate="std", total=100.0),
            R("E2", "Oil and filter", hours="0.5", rate="std", total=50.0)]
    res = merge(None, cur, mine, OFFICE_SPEC, legacy=True)
    assert ids(res) == ["E1", "E2"]
    assert descs(res) == ["Brakes", "Oil and filter"]
    assert res.origins[0]["kind"] == "adopted"


def _legacy_rows(ids=("L1", "L2")):
    return [R(ids[0], "Legacy A", hours="1", rate="std", total=100.0),
            R(ids[1], "Legacy B", [P("PADS", pn="CM-PADS", qty=1, price=99.5)], hours="2", rate="std", total=200.0)]


def _pre_deploy_form(ids=("", "")):
    return [R(ids[0], "Legacy A edited", hours="1", rate="std", total=100.0),
            R(ids[1], "Legacy B", [P("PADS", pn="CM-PADS", qty=1, price=99.5)], hours="2", rate="std", total=200.0)]


def test_pre_deploy_manager_form_on_a_legacy_wo_does_not_duplicate_jobs():
    """Ревью D4-02: форма менеджера, открытая до деплоя, держит строки
    легаси-WO без id (labor_id ""). Вьюх нет — консервативный merge; id в
    WO уже сохранены (или ещё нет) — строки формы сопоставляются со строками
    WO, как прежняя запись массива, без дублей."""
    for ids in (("L1", "L2"), ("", "")):
        res = merge(None, _legacy_rows(ids), _pre_deploy_form(), MOBILE_MANAGER_SPEC, legacy=True)
        assert descs(res) == ["Legacy A edited", "Legacy B"], ids
        assert [o["kind"] for o in res.origins] == ["adopted", "adopted"]
        assert len(res.rows[1]["parts"]) == 1 and res.notes == []
        if ids[0]:
            assert res.labor_ids == ["L1", "L2"]


def test_pre_deploy_office_tab_with_render_ids_does_not_duplicate_jobs():
    """Ревью D4-X1: вкладка офиса, отрендеренная до деплоя, держит временные
    id; после деплоя другой GET сохранил настоящие — ни один id вкладки не
    узнаётся. Строки сопоставляются по описанию/порядку, id — из WO."""
    res = merge(None, _legacy_rows(), _pre_deploy_form(("E1", "E2")), OFFICE_SPEC, legacy=True)
    assert descs(res) == ["Legacy A edited", "Legacy B"]
    assert res.labor_ids == ["L1", "L2"] and ids(res) == ["L1", "L2"]
    assert res.stats["kept_ids"] == [] and res.stats["parts_kept"] == 0


def test_legacy_array_fallback_stays_off_when_the_form_is_not_that_form():
    """Без совпавшего описания (форма после упавшей загрузки, набранная
    заново), с метками minted в WO (строки, созданные после деплоя), для
    канала механика (его формы id получали всегда), с base — сопоставления
    нет: строки WO сохраняются, ничего не перезаписано."""
    typed = [R("", "Typed after failed load", hours="1", rate="std"), R("", "Another job", hours="1", rate="std")]
    res = merge(None, _legacy_rows(), typed, MOBILE_MANAGER_SPEC, legacy=True)
    assert descs(res) == ["Typed after failed load", "Another job", "Legacy A", "Legacy B"]

    marked = _legacy_rows()
    marked[1]["minted"] = {"by": "u9", "sid": "x", "at": NOW, "ck": ""}
    res = merge(None, marked, _pre_deploy_form(), MOBILE_MANAGER_SPEC, legacy=True)
    assert len(res.rows) == 4

    res = merge(None, _legacy_rows(), _pre_deploy_form(), MECHANIC_SPEC, legacy=True)
    assert len(res.rows) == 4

    res = merge(None, _legacy_rows(), _pre_deploy_form(), MOBILE_MANAGER_SPEC, legacy=False)
    assert len(res.rows) == 4


def test_legacy_array_fallback_pairs_by_description_only_and_one_to_one():
    """Ревью F1-RR-01: страховка D4-02 сопоставляла строки по описанию, а
    остальные — по позиции. Форма менеджера после упавшей загрузки (набрана
    заново: «Oil change» + «Wipers») приклеивала «Wipers» к чужой работе
    «Brakes» — та молча теряла описание, жалобу и часы. Теперь — только по
    описанию и только один к одному со строками WO; нет такого соответствия —
    консервативный merge, строки WO не тронуты."""
    current = [R("L1", "Oil change", hours="1", rate="std", total=100.0, issue="Leaks at drain plug"),
               R("L2", "Brakes", hours="3", rate="std", total=300.0, issue="Squeal at low speed")]
    typed = [R("", "Oil change", hours="1.5", rate="std"), R("", "Wipers", hours="0.5", rate="std")]
    res = merge(None, current, typed, MOBILE_MANAGER_SPEC, legacy=True)
    by_id = {r["labor_id"]: r for r in res.rows}
    assert (by_id["L2"]["description"], by_id["L2"]["issue_description"], by_id["L2"]["hours"]) == (
        "Brakes", "Squeal at low speed", "3")
    assert (by_id["L1"]["issue_description"], by_id["L1"]["hours"]) == ("Leaks at drain plug", "1")
    assert descs(res) == ["Oil change", "Wipers", "Oil change", "Brakes"]
    assert res.stats["rows_legacy_array"] == 0 and sorted(res.stats["kept_ids"]) == ["L1", "L2"]

    # Вкладка офиса до деплоя (id рендера) + новая строка; X добавили после её
    # рендера — не склеивается с «Wipers», остаётся как есть (адаптер офиса
    # такой Save отклоняет: page_outdated).
    cur = [R("a", "Brakes", hours="1", rate="std"), R("b", "Oil change", hours="1", rate="std"),
           R("x", "Replace hose", hours="2", rate="std", issue="Leaks")]
    tab = [R("e1", "Brakes", hours="1", rate="std"), R("e2", "Oil change", hours="1", rate="std"),
           R("", "Wipers", hours="0.5", rate="std")]
    res = merge(None, cur, tab, OFFICE_SPEC, legacy=True)
    x = next(r for r in res.rows if r["labor_id"] == "x")
    assert (x["description"], x["hours"], x["issue_description"]) == ("Replace hose", "2", "Leaks")
    assert "x" in res.stats["kept_ids"] and res.stats["rows_legacy_array"] == 0

    # Число строк совпало, но одна строка формы ни с одной строкой WO не сходится.
    res = merge(None, _legacy_rows(), [_pre_deploy_form()[0], R("", "Wipers", hours="2", rate="std")],
                MOBILE_MANAGER_SPEC, legacy=True)
    assert len(res.rows) == 4 and res.stats["rows_legacy_array"] == 0
    legacy_b = next(r for r in res.rows if r["labor_id"] == "L2")
    assert (legacy_b["description"], len(legacy_b["parts"])) == ("Legacy B", 1)


def test_duplicate_id_in_one_request_gets_new_id():
    res = merge([R("A", "x")], [R("A", "x")], [M("A", "x"), M("A", "y")])
    assert res.labor_ids == ["A", "N1"]
    assert ids(res) == ["A", "N1"]


# ───────────────────────── строки, удалённые другими ─────────────────────────

def test_row_removed_by_other_and_unchanged_is_skipped_with_note():
    res = merge([R("A", "Brakes"), R("B", "Wipers")], [R("A", "Brakes")],
                [M("A", "Brakes 2"), M("B", "Wipers")])
    assert ids(res) == ["A"]
    assert res.labor_ids == ["A", "B"]
    assert 'Job "Wipers" was removed by another user.' in res.notes
    assert res.foreign_any and res.foreign_structure
    assert [b["labor_id"] for b in res.belief] == ["A", "B"]
    assert res.stats["skipped_ids"] == ["B"]


def test_row_removed_by_other_but_changed_by_me_is_restored_with_base_money():
    uid = ObjectId()
    job_b = R("B", "Job B", [P("P1", qty=1, price=45.0)], hours="2.0", rate="std", total=200.0,
              assigned=[{"user_id": str(uid), "percent": 100.0, "name": "Mike", "role": "mechanic"}])
    base = [R("A", "Job A", hours="1.0", rate="std", total=100.0), job_b]
    cur = [R("A", "Job A", hours="1.0", rate="std", total=100.0)]
    mine = [R("A", "Job A", hours="1.0", rate="std", total=100.0),
            {**job_b, "description": "Job B fixed"}]
    res = merge(base, cur, mine, OFFICE_SPEC)
    restored = res.rows[1]
    assert restored["description"] == "Job B fixed"
    assert restored["hours"] == "2.0" and restored["labor_total"] == 200.0
    assert restored["parts"][0]["price"] == 45.0
    assert restored["assigned_mechanics"][0]["user_id"] == uid  # обратно в ObjectId
    assert res.origins[1]["kind"] == "resurrected"
    assert any(n.startswith('Restored job "Job B fixed"') for n in res.notes)
    assert res.foreign_structure


def test_mechanic_restore_takes_money_from_base_not_from_mine():
    base = [R("A", "Job A"), R("B", "Job B", [P("P1", qty=1, price=45.0)], hours="2.0", rate="std", total=200.0)]
    res = merge(base, [R("A", "Job A")], [M("A", "Job A"), M("B", "Job B", [MP("P1", qty=3)])])
    restored = res.rows[1]
    assert restored["hours"] == "2.0"
    assert restored["rate_code"] == "std"
    assert restored["labor_total"] == 200.0
    assert restored["parts"][0]["price"] == 45.0
    assert restored["parts"][0]["qty"] == 3


def test_skipped_row_belief_keeps_base_money_so_later_restore_is_not_zero():
    """Ревью merge-semantics #4: belief пропущенной строки — из base, а не
    из mine механика (у него hours '' и нет цен), иначе следующее
    «воскрешение» дало бы $0."""
    job_b = R("B", "Job B", [P("P1", qty=1, price=45.0)], hours="2.0", rate="std", total=200.0, source="preset")
    base = [R("A", "Job A"), job_b]
    s1 = merge(base, [R("A", "Job A")], [M("A", "Job A 2"), M("B", "Job B", [MP("P1", qty=1)])], legacy=True)
    belief_b = s1.belief[1]
    assert belief_b["hours"] == "2.0" and belief_b["labor_total"] == 200.0
    assert belief_b["parts"][0]["price"] == 45.0

    s2 = merge(s1.belief, s1.rows, [M("A", "Job A 2"), M("B", "Job B", [MP("P1", qty=1), MP("P9", qty=1)])],
               legacy=True)
    restored = next(r for r in s2.rows if r["labor_id"] == "B")
    assert restored["labor_total"] == 200.0
    assert restored["hours_source"] == "preset"
    assert restored["parts"][0]["price"] == 45.0
    assert len(restored["parts"]) == 2


# ───────────────────────── строки current, которых нет в mine ─────────────────────────

def test_ping_pong_two_mechanics_never_lose_jobs():
    """Пинг-понг автосейвов двух телефонов (WO #7636 / #7808 у LTS)."""
    # A открыл WO, затем B добавил Filter.
    base_a = [R("J1", "Brakes")]
    cur = [R("J1", "Brakes"), R("F", "Filter")]
    ra = merge(base_a, cur, [M("J1", "Brakes front")], legacy=True)
    assert descs(ra) == ["Brakes front", "Filter"]
    assert ra.notes == ['Kept job "Filter" added by another user.']

    # Автосейв B со своей устаревшей копией.
    base_b = [R("J1", "Brakes"), R("F", "Filter")]
    rb = merge(base_b, ra.rows, [M("J1", "Brakes"), M("F", "Filter 2")], legacy=True, user_id="u2", sid="s2")
    assert descs(rb) == ["Brakes front", "Filter 2"]

    # A снова (base — его belief).
    ra2 = merge(ra.belief, rb.rows, [M("J1", "Brakes front pads")], legacy=True)
    assert descs(ra2) == ["Brakes front pads", "Filter 2"]


def test_stale_office_save_keeps_mechanic_rows():
    base = [R("A", "Job", [P("P1", qty=1, price=10.0)], hours="1.0", rate="std", total=100.0)]
    cur = base + [R("M", "Mechanic job", [P("P2", qty=1, price=20.0)])]
    mine = [R("A", "Job", [P("P1", qty=1, price=12.0)], hours="1.0", rate="std", total=100.0)]
    res = merge(base, cur, mine, OFFICE_SPEC)
    assert ids(res) == ["A", "M"]
    assert res.rows[0]["parts"][0]["price"] == 12.0
    assert res.notes == ['Kept job "Mechanic job" added by another user.']
    assert res.foreign_structure


def test_row_removed_by_client_and_unchanged_is_deleted():
    base = [R("A", "x"), R("B", "y")]
    res = merge(base, [R("A", "x"), R("B", "y")], [M("A", "x")])
    assert ids(res) == ["A"]
    assert res.stats["deleted_ids"] == ["B"]
    assert res.foreign_any is False and res.notes == []


def test_delete_vs_modify_keeps_modified_row():
    base = [R("A", "x"), R("B", "Wipers")]
    res = merge(base, [R("A", "x"), R("B", "Wipers front")], [M("A", "x")])
    assert ids(res) == ["A", "B"]
    assert res.notes == ['Kept job "Wipers front": it was changed by another user.']


def test_running_timer_row_is_not_deleted():
    base = [R("A", "x"), R("B", "Wipers")]
    res = merge(base, [R("A", "x"), R("B", "Wipers")], [M("A", "x")], running_labor_ids={"B"})
    assert ids(res) == ["A", "B"]
    assert res.notes == ['Kept job "Wipers": someone is working on it.']


def test_tracked_hours_since_base_protect_row_for_every_spec():
    base = [R("A", "x"), R("B", "Wipers", hours="1.0", source="tracked")]
    cur = [R("A", "x"), R("B", "Wipers", hours="1.5", source="tracked")]
    res = merge(base, cur, [M("A", "x")])
    assert ids(res) == ["A", "B"]


def test_money_change_by_office_does_not_protect_row_from_mechanic_delete():
    base = [R("A", "x"), R("B", "Wipers", hours="1.0", rate="std", total=100.0)]
    cur = [R("A", "x"), R("B", "Wipers", hours="1.0", rate="std", total=150.0)]
    res = merge(base, cur, [M("A", "x")])
    assert ids(res) == ["A"]  # механик удалил строку явно; деньги — не его поле


def test_legacy_blank_row_kept_silently_in_its_place():
    base = [R("A", "Brakes"), R("BL", "", issue="Customer says noise"), R("C", "Oil")]
    res = merge(base, [dict(r) for r in base], [M("A", "Brakes 2"), M("C", "Oil")], legacy=True)
    assert ids(res) == ["A", "BL", "C"]
    assert res.notes == []
    assert res.foreign_any is False
    assert [o["kind"] for o in res.origins] == ["merged", "kept_silent", "merged"]
    assert "BL" in [b["labor_id"] for b in res.belief]


def test_blank_row_rule_is_legacy_only():
    base = [R("A", "Brakes"), R("BL", "", issue="note")]
    res = merge(base, [dict(r) for r in base], [M("A", "Brakes")], legacy=False)
    assert ids(res) == ["A"]


def test_legacy_blank_row_filled_by_other_is_kept_silently():
    base = [R("A", "Brakes"), R("BL", "")]
    cur = [R("A", "Brakes"), R("BL", "Diagnose noise")]
    res = merge(base, cur, [M("A", "Brakes")], legacy=True)
    assert ids(res) == ["A", "BL"]
    assert res.rows[1]["description"] == "Diagnose noise"
    assert res.notes == [] and res.foreign_any is False
    # belief — пустая строка, какой её видел клиент: следующее сохранение
    # снова не сочтёт её отсутствие удалением.
    belief_bl = next(b for b in res.belief if b["labor_id"] == "BL")
    assert belief_bl["description"] == ""
    again = merge(res.belief, res.rows, [M("A", "Brakes 2")], legacy=True)
    assert ids(again) == ["A", "BL"] and again.notes == []


def test_legacy_blank_row_added_after_base_is_silent():
    res = merge([R("A", "Brakes")], [R("A", "Brakes"), R("BL2", "", issue="call customer")],
                [M("A", "Brakes")], legacy=True)
    assert ids(res) == ["A", "BL2"]
    assert res.notes == [] and res.foreign_any is False
    assert "BL2" in [b["labor_id"] for b in res.belief]


def test_conservative_keeps_rows_not_in_mine():
    cur = [R("A", "x"), R("X", "Diag"), R("BL", "")]
    res = merge(None, cur, [M("A", "x")], legacy=True)
    # Пустую строку легаси-клиент знает (не шлёт) — она у своей соседки;
    # строки, которых клиент мог не знать, — в конце.
    assert ids(res) == ["A", "BL", "X"]
    assert res.notes == ['Kept job "Diag": it was not in your copy of the work order.']
    assert res.foreign_structure
    office = merge(None, cur, [R("A", "x")], OFFICE_SPEC, legacy=True)
    assert ids(office) == ["A", "X", "BL"]  # офис пустые строки шлёт — правило не для него


def test_legacy_idless_row_whose_minted_row_was_removed_by_other_is_skipped():
    """Ревью legacy-clients #11: id, выданный id-less строке (в base_minted),
    удалил кто-то другой — строка не создаётся заново, а «удалена другим»;
    если клиент её поменял — восстанавливается с тем же id."""
    base = [R("A", "Brakes"), R("L1", "Oil", hours="1.0", rate="std", total=100.0)]
    cur = [R("A", "Brakes")]
    same = merge(base, cur, [M("A", "Brakes"), M("", "Oil")], legacy=True, base_minted={"L1"})
    assert ids(same) == ["A"]
    assert same.labor_ids == ["A", "L1"] and same.minted == ["L1"]
    assert 'Job "Oil" was removed by another user.' in same.notes

    changed = merge(base, cur, [M("A", "Brakes"), M("", "Oil and filter")], legacy=True, base_minted={"L1"})
    assert ids(changed) == ["A", "L1"]
    assert changed.rows[1]["labor_total"] == 100.0  # деньги — из base, не из mine механика

    other = merge(base, cur, [M("A", "Brakes"), M("", "Wipers")], legacy=True, base_minted={"L1"})
    assert len(other.rows) == 2 and other.labor_ids[1] != "L1"  # другая строка — новая


def test_unknown_rows_are_appended_after_mine_rows():
    cur = [R("X", "From office"), R("A", "Brakes"), R("Y", "From mechanic B")]
    res = merge([R("A", "Brakes")], cur, [M("A", "Brakes"), M("", "New")])
    assert ids(res) == ["A", "N1", "X", "Y"]
    assert [o["kind"] for o in res.origins] == ["merged", "new", "kept", "kept"]


# ───────────────────────── денежная группа ─────────────────────────

def _money(lid="A", hours="1.0", rate="std", total=100.0, source="", **kw):
    return R(lid, "Job", hours=hours, rate=rate, total=total, source=source, **kw)


def test_office_untouched_and_unchanged_takes_mine_group_repricing():
    """Как раньше: офис пересчитывает labor по текущей ставке при Save."""
    res = merge([_money()], [_money()], [_money(total=120.0)], OFFICE_SPEC)
    assert res.rows[0]["labor_total"] == 120.0
    assert res.origins[0]["money_from_mine"] is True
    assert res.foreign_any is False and res.foreign_structure is False


def test_office_untouched_but_changed_by_other_takes_current_group():
    res = merge([_money()], [_money(total=150.0)], [_money()], OFFICE_SPEC)
    assert res.rows[0]["labor_total"] == 150.0
    assert res.origins[0]["money_from_mine"] is False
    assert res.foreign_structure is True


def test_office_touched_takes_mine_group_and_clears_source():
    res = merge([_money(source="tracked")], [_money(hours="1.5", total=150.0, source="tracked")],
                [_money(hours="3.0", total=300.0, hours_edited=True)], OFFICE_SPEC)
    row = res.rows[0]
    assert (row["hours"], row["labor_total"], row["hours_source"]) == ("3.0", 300.0, "")
    assert res.tracked_adjusted == []


def test_tracked_hours_preserved_with_base():
    res = merge([_money(source="tracked")], [_money(hours="1.5", total=150.0, source="tracked")],
                [_money(total=110.0)], OFFICE_SPEC)
    row = res.rows[0]
    assert (row["hours"], row["labor_total"], row["hours_source"]) == ("1.5", 150.0, "tracked")


def test_tracked_hours_preserved_without_base_and_reported_as_adjusted():
    """Старая вкладка офиса (нет base): устаревшие 1.0 ч не перетирают
    tracked 2.0 ч и метка tracked не снимается (как test_time_tracking_deep)."""
    res = merge(None, [_money(hours="2.0", total=200.0, source="tracked")], [_money()], OFFICE_SPEC, legacy=True)
    row = res.rows[0]
    assert (row["hours"], row["labor_total"], row["hours_source"]) == ("2.0", 200.0, "tracked")
    assert res.tracked_adjusted == ["A"]
    assert res.foreign_any is True
    assert res.foreign_structure is False  # быстрый путь офиса подставит базу строки


def test_tracked_hours_with_rate_change_recompute_base():
    res = merge([_money(source="tracked")], [_money(hours="1.5", total=150.0, source="tracked")],
                [_money(rate="premium", total=130.0)], OFFICE_SPEC)
    row = res.rows[0]
    assert (row["hours"], row["rate_code"]) == ("1.5", "premium")
    assert row["labor_total"] == 195.0  # 1.5 × 130
    assert res.tracked_adjusted == ["A"]


def test_explicit_hours_edit_beats_tracked_hours():
    res = merge(None, [_money(hours="2.0", total=200.0, source="tracked")],
                [_money(hours="1.0", hours_edited=True)], OFFICE_SPEC, legacy=True)
    row = res.rows[0]
    assert (row["hours"], row["hours_source"]) == ("1.0", "")


def test_stale_hours_edited_flag_does_not_beat_tracked_hours():
    res = merge([_money(source="tracked")], [_money(hours="1.5", total=150.0, source="tracked")],
                [_money(hours_edited=True)], OFFICE_SPEC)
    assert res.rows[0]["hours"] == "1.5"
    assert res.rows[0]["hours_source"] == "tracked"


# Правка тайм-лога в режиме Edit: база вкладки (производная вьюха) уже знает
# свежие tracked-часы 1.5, форма показывает прежние 1.33 без флага
# hours_edited («Save сам подставит свежие часы»).
_TL_BASE = _money(hours="1.5", total=150.0, source="tracked")
_TL_FORM = _money(hours="1.33", total=133.0)


def test_office_untyped_hours_of_a_tracked_row_never_revert_a_colleagues_change():
    """Ревью RR-F2-01: часы tracked-строки, которых вкладка руками не вводила,
    — не её правка. Раньше они «отличались от base» и брались из формы:
    ручные 3 ч другого менеджера молча становились 1.33 (недобор в счёте),
    его ставка и пресет на этой работе откатывались."""
    base = [dict(_TL_BASE)]
    res = merge(base, [_money(hours="3", total=300.0)], [dict(_TL_FORM)], OFFICE_SPEC)
    row = res.rows[0]
    assert (row["hours"], row["labor_total"], row["hours_source"]) == ("3", 300.0, "")
    assert res.origins[0]["money_from_mine"] is False and res.foreign_structure is True

    # Вкладка поменяла только ставку: часы — коллеги, ставка — вкладки.
    res = merge(base, [_money(hours="3", total=300.0)], [dict(_TL_FORM, rate_code="premium", labor_total=172.9)],
                OFFICE_SPEC)
    row = res.rows[0]
    assert (row["hours"], row["rate_code"], row["labor_total"], row["hours_source"]) == ("3", "premium", 390.0, "")
    assert res.origins[0]["money_from_mine"] is False and res.foreign_structure is True

    res = merge(base, [_money(hours="1.5", rate="premium", total=195.0, source="tracked")], [dict(_TL_FORM)],
                OFFICE_SPEC)
    assert (res.rows[0]["hours"], res.rows[0]["rate_code"], res.rows[0]["labor_total"]) == ("1.5", "premium", 195.0)
    res = merge(base, [_money(hours="2.5", total=250.0, source="preset")], [dict(_TL_FORM)], OFFICE_SPEC)
    assert (res.rows[0]["hours"], res.rows[0]["labor_total"], res.rows[0]["hours_source"]) == ("2.5", 250.0, "preset")

    # Никто не менял — свежие tracked-часы (как и раньше); введённые руками — вкладки.
    res = merge(base, [dict(_TL_BASE)], [dict(_TL_FORM)], OFFICE_SPEC)
    row = res.rows[0]
    assert (row["hours"], row["labor_total"], row["hours_source"]) == ("1.5", 150.0, "tracked")
    res = merge(base, [_money(hours="3", total=300.0)], [dict(_TL_FORM, hours="2", hours_edited=True)], OFFICE_SPEC)
    assert (res.rows[0]["hours"], res.rows[0]["hours_source"]) == ("2", "")


def test_office_untyped_hours_do_not_restore_a_tracked_job_removed_by_another_user():
    """Ревью RW-01 / RR-F2-01: работу удалил другой пользователь — Save
    вкладки с теми же (не введёнными руками) часами её не воскрешает
    («you changed it» было неправдой, работа снова шла в счёт, запчасти
    списывались второй раз). Введённые часы — правка вкладки, работа
    возвращается."""
    other = R("B", "Other job", hours="1", rate="std", total=100.0)
    base = [dict(_TL_BASE), dict(other)]
    current = [dict(other)]
    res = merge(base, current, [dict(_TL_FORM), dict(other, labor_total=110.0)], OFFICE_SPEC)
    assert ids(res) == ["B"]
    assert res.stats["skipped_ids"] == ["A"] and res.stats["resurrected_ids"] == []
    assert 'Job "Job" was removed by another user.' in res.notes

    res = merge(base, current, [dict(_TL_FORM, hours="2", hours_edited=True), dict(other)], OFFICE_SPEC)
    assert ids(res) == ["A", "B"] and res.stats["resurrected_ids"] == ["A"]
    assert (res.rows[0]["hours"], res.rows[0]["hours_source"]) == ("2", "")


def test_apply_preset_on_preset_row_without_hours_flag_keeps_the_new_hours():
    """Ревью D1-04 / D2-01: «Apply preset» / «Add Preset» пишет часы в форму
    программно (без флага hours_edited). Пресетная строка механика (1 ч) →
    офис применил пресет на 2.5 ч: часы и база — из формы, как в прежнем
    коде (откатывались только tracked-строки), метка пресета остаётся."""
    stored = _money(hours="1", total=100.0, source="preset")
    applied = dict(_money(hours="2.5", total=250.0), description="Brake service")
    for base in ([dict(stored)], None):   # новая вкладка (base) и старая (без base)
        res = merge(base, [dict(stored)], [dict(applied)], OFFICE_SPEC, legacy=base is None)
        row = res.rows[0]
        assert (row["description"], row["hours"], row["labor_total"], row["hours_source"]) == (
            "Brake service", "2.5", 250.0, "preset"), base
        assert res.tracked_adjusted == [] and res.origins[0]["money_from_mine"] is True
        assert res.foreign_any is False and res.foreign_structure is False


def test_typed_hours_on_preset_row_clear_the_preset_marker():
    res = merge([_money(source="preset")], [_money(source="preset")],
                [_money(hours="3.0", total=300.0, hours_edited=True)], OFFICE_SPEC)
    row = res.rows[0]
    assert (row["hours"], row["labor_total"], row["hours_source"]) == ("3.0", 300.0, "")


def test_preset_hours_changed_by_someone_else_survive_an_untouched_stale_tab():
    """Часы пресетной строки поменял другой (метка осталась preset), вкладка
    их не трогала — остаются чужие часы; и при правке одной только ставки
    часы — чужие, база пересчитана по новой ставке."""
    base = [_money(hours="1.0", total=100.0, source="preset")]
    cur = [_money(hours="3.0", total=300.0, source="preset")]
    res = merge(base, cur, [_money(hours="1.0", total=100.0)], OFFICE_SPEC)
    assert (res.rows[0]["hours"], res.rows[0]["labor_total"]) == ("3.0", 300.0)
    res = merge(base, cur, [_money(hours="1.0", rate="premium", total=130.0)], OFFICE_SPEC)
    row = res.rows[0]
    assert (row["hours"], row["rate_code"], row["labor_total"], row["hours_source"]) == (
        "3.0", "premium", 390.0, "preset")
    assert res.tracked_adjusted == ["A"]


def test_mobile_manager_preset_hours_edit_applies_with_and_without_base():
    """Ревью D1-04: старое приложение менеджера без base (консенсусу не из
    чего собраться) — правка часов пресетной строки применяется, как раньше;
    с base — тоже. Нетронутые часы при чужой правке — чужие."""
    stored = _money(hours="1", total=100.0, source="preset")
    edited = _money(hours="2.5", total=None)
    for base in ([dict(stored)], None):
        res = merge(base, [dict(stored)], [dict(edited)], MOBILE_MANAGER_SPEC, legacy=base is None)
        row = res.rows[0]
        assert (row["hours"], row["labor_total"]) == ("2.5", None), base
        assert res.tracked_adjusted == []
    res = merge([dict(stored)], [_money(hours="3", total=300.0, source="preset")], [dict(stored)],
                MOBILE_MANAGER_SPEC)
    assert (res.rows[0]["hours"], res.rows[0]["labor_total"]) == ("3", 300.0)


def test_mobile_manager_labor_total_edit_clears_tracked_source():
    res = merge([_money(source="tracked")], [_money(source="tracked")], [_money(total=250.0)], MOBILE_MANAGER_SPEC)
    row = res.rows[0]
    assert (row["labor_total"], row["hours_source"]) == (250.0, "")


def test_mobile_manager_untouched_takes_current_group():
    res = merge([_money()], [_money(total=175.0)], [_money()], MOBILE_MANAGER_SPEC)
    assert res.rows[0]["labor_total"] == 175.0


def test_mobile_manager_hours_edit_takes_mine_and_clears_source():
    res = merge([_money(source="preset")], [_money(source="preset")], [_money(hours="3", total=None)],
                MOBILE_MANAGER_SPEC)
    row = res.rows[0]
    assert (row["hours"], row["labor_total"], row["hours_source"]) == ("3", None, "")


def test_mobile_manager_without_base_keeps_tracked_hours():
    res = merge(None, [_money(hours="2.0", total=200.0, source="tracked")], [_money()], MOBILE_MANAGER_SPEC,
                legacy=True)
    assert res.rows[0]["hours"] == "2.0"
    assert res.rows[0]["labor_total"] == 200.0


def test_mechanic_spec_never_takes_money_or_assignments_from_mine():
    uid = ObjectId()
    assigned = [{"user_id": uid, "percent": 100.0, "name": "Mike", "role": "mechanic"}]
    cur = [_money(hours="2.0", total=200.0, source="tracked", assigned=assigned)]
    res = merge([_money(hours="2.0", total=200.0, source="tracked", assigned=assigned)], cur,
                [M("A", "Job", assigned_mechanics=[])])
    row = res.rows[0]
    assert (row["hours"], row["rate_code"], row["labor_total"]) == ("2.0", "std", 200.0)
    assert row["assigned_mechanics"] == assigned
    assert res.foreign_any is False


def test_office_assignments_three_way():
    b = [{"user_id": "u1", "percent": 100.0}]
    c = [{"user_id": "u1", "percent": 50.0}, {"user_id": "u2", "percent": 50.0}]
    res = merge([_money(assigned=b)], [_money(assigned=c)], [_money(assigned=b)], OFFICE_SPEC)
    assert res.rows[0]["assigned_mechanics"] == c
    res = merge([_money(assigned=b)], [_money(assigned=c)], [_money(assigned=[{"user_id": "u3", "percent": 100}])],
                OFFICE_SPEC)
    assert res.rows[0]["assigned_mechanics"] == [{"user_id": "u3", "percent": 100}]


# ───────────────────────── запчасти ─────────────────────────

def test_part_alias_pn_to_catalog_id_no_duplicate():
    base = [R("A", "Job", [P(pn="ABC", desc="Filter")])]
    cur = [R("A", "Job", [P("CAT1", pn="ABC", desc="Filter", price=12.5)])]
    res = merge(base, cur, [M("A", "Job", [MP(pn="ABC", desc="Filter", qty=2)])])
    parts = res.rows[0]["parts"]
    assert len(parts) == 1
    assert (parts[0]["part_id"], parts[0]["qty"], parts[0]["price"]) == ("CAT1", 2, 12.5)
    assert res.notes == []


def test_racing_request_already_added_same_part_no_duplicate():
    res = merge([R("A", "Job")], [R("A", "Job", [P("P1", qty=2, price=9.0)])], [M("A", "Job", [MP("P1", qty=2)])])
    parts = res.rows[0]["parts"]
    assert len(parts) == 1 and parts[0]["price"] == 9.0


def test_anon_lines_matched_by_description_not_position():
    """Ревью merge-semantics #8: офис удалил Rags, механик поднял Gloves до 2."""
    base = [R("A", "Job", [P(desc="Rags", qty=1), P(desc="Gloves", qty=1)])]
    cur = [R("A", "Job", [P(desc="Gloves", qty=1)])]
    res = merge(base, cur, [M("A", "Job", [MP(desc="Rags", qty=1), MP(desc="Gloves", qty=2)])])
    parts = res.rows[0]["parts"]
    assert [(p["description"], p["qty"]) for p in parts] == [("Gloves", 2)]
    assert 'Part "Rags" was removed by another user.' in res.notes


def test_anon_line_description_edit_matched_by_position():
    base = [R("A", "Job", [P(desc="Rags", price=5.0)])]
    res = merge(base, [dict(base[0])], [M("A", "Job", [MP(desc="Shop rags")])])
    parts = res.rows[0]["parts"]
    assert len(parts) == 1
    assert (parts[0]["description"], parts[0]["price"]) == ("Shop rags", 5.0)


def test_duplicate_catalog_lines_matched_by_occurrence():
    base = [R("A", "Job", [P("X", qty=1, price=10.0), P("X", qty=2, price=11.0)])]
    res = merge(base, [dict(base[0])], [M("A", "Job", [MP("X", qty=1), MP("X", qty=5)])])
    parts = res.rows[0]["parts"]
    assert [(p["qty"], p["price"]) for p in parts] == [(1, 10.0), (5, 11.0)]


def test_concurrent_removal_and_rename_of_anonymous_lines_does_not_duplicate():
    """Ревью D1-05: B удалил Rags, A (устаревшая копия) переименовал Gloves.
    Раньше нетронутая Rags A по позиции склеивалась с Gloves current, а
    «Blue gloves» считалась удалённой другим и восстанавливалась — строка в
    счёте задваивалась. Теперь запчасть с родословной в base идёт через base."""
    base = [R("A", "Brake job", [P(desc="Rags", qty=1, price=5.0), P(desc="Gloves", qty=2, price=7.0)])]
    cur = [R("A", "Brake job", [P(desc="Gloves", qty=2, price=7.0)])]
    res = merge(base, cur, [M("A", "Brake job", [MP(desc="Rags", qty=1), MP(desc="Blue gloves", qty=2)])])
    assert [(p["description"], p["qty"], p["price"]) for p in res.rows[0]["parts"]] == [("Blue gloves", 2, 7.0)]
    assert 'Part "Rags" was removed by another user.' in res.notes
    assert not any(n.startswith("Restored") for n in res.notes)

    office_mine = [R("A", "Brake job", [P(desc="Rags", qty=1, price=5.0), P(desc="Blue gloves", qty=2, price=7.0)])]
    res = merge(base, cur, office_mine, OFFICE_SPEC)
    assert [(p["description"], p["qty"]) for p in res.rows[0]["parts"]] == [("Blue gloves", 2)]


def test_concurrent_removal_of_a_duplicate_catalog_line_and_qty_change_keeps_one_line():
    """Ревью D1-05 (вариант key#n): в работе две строки одной запчасти (qty 1
    и 2). Офис удалил первую, механик выставил второй 5 — итог одна строка
    qty 5, а не [2, 5]."""
    base = [R("A", "Job", [P("X", qty=1, price=10.0), P("X", qty=2, price=11.0)])]
    cur = [R("A", "Job", [P("X", qty=2, price=11.0)])]
    res = merge(base, cur, [M("A", "Job", [MP("X", qty=1), MP("X", qty=5)])])
    assert [(p["qty"], p["price"]) for p in res.rows[0]["parts"]] == [(5, 11.0)]
    assert not any(n.startswith("Restored") for n in res.notes)


def test_new_lines_without_base_partner_are_not_glued_by_position():
    """Строки без пары в base (новая у клиента и добавленная другим) — одна
    и та же, только если совпал ключ: разные анонимные строки не склеиваются."""
    base = [R("A", "Job", [P("P1")])]
    cur = [R("A", "Job", [P("P1"), P(desc="Rags", qty=1, price=5.0)])]
    res = merge(base, cur, [M("A", "Job", [MP("P1"), MP(desc="Shop towels", qty=1)])])
    assert [p["description"] for p in res.rows[0]["parts"]] == ["", "Shop towels", "Rags"]
    assert 'Kept part "Rags" on "Job" added by another user.' in res.notes


def test_match_parts_prefers_equal_content_when_duplicate_counts_differ():
    a = [P("X", qty=1, price=10.0), P("X", qty=2, price=11.0)]
    assert match_parts(a, [P("X", qty=2, price=11.0)]) == {1: 0}
    assert match_parts(a, [P("X", qty=7, price=99.0)]) == {0: 0}          # нет равных — по вхождению
    assert match_parts(a, list(a)) == {0: 0, 1: 1}
    assert match_parts([P(desc="Rags")], [P(desc="Towels")], anon_fallback=False) == {}


def test_part_added_by_other_is_kept_after_mine_parts():
    base = [R("A", "Job", [P("P1")])]
    cur = [R("A", "Job", [P("P1"), P("P2", desc="Belt")])]
    res = merge(base, cur, [M("A", "Job", [MP("P1"), MP("P3", desc="Hose")])])
    assert [p["part_id"] for p in res.rows[0]["parts"]] == ["P1", "P3", "P2"]
    assert 'Kept part "Belt" on "Job" added by another user.' in res.notes
    assert res.foreign_structure


def test_part_removed_by_client_is_dropped():
    base = [R("A", "Job", [P("P1"), P("P2")])]
    res = merge(base, [dict(base[0])], [M("A", "Job", [MP("P1")])])
    assert [p["part_id"] for p in res.rows[0]["parts"]] == ["P1"]
    assert res.stats["parts_deleted"] == 1
    assert res.foreign_any is False


def test_part_removed_by_client_but_changed_by_other_is_kept():
    base = [R("A", "Job", [P("P1"), P("P2", desc="Belt", qty=1)])]
    cur = [R("A", "Job", [P("P1"), P("P2", desc="Belt", qty=4)])]
    res = merge(base, cur, [M("A", "Job", [MP("P1")])])
    assert [p["part_id"] for p in res.rows[0]["parts"]] == ["P1", "P2"]
    assert 'Kept part "Belt" on "Job": it was changed by another user.' in res.notes


def test_part_removed_by_other_unchanged_is_skipped():
    base = [R("A", "Job", [P("P1"), P("P2", desc="Belt")])]
    res = merge(base, [R("A", "Job", [P("P1")])], [M("A", "Job", [MP("P1"), MP("P2", desc="Belt")])])
    assert [p["part_id"] for p in res.rows[0]["parts"]] == ["P1"]
    assert 'Part "Belt" was removed by another user.' in res.notes
    assert res.foreign_structure


def test_part_removed_by_other_but_changed_by_mechanic_is_restored_with_base_price():
    base = [R("A", "Job", [P("P1"), P("P2", desc="Belt", qty=1, price=30.0)])]
    res = merge(base, [R("A", "Job", [P("P1")])], [M("A", "Job", [MP("P1"), MP("P2", desc="Belt", qty=3)])])
    p2 = res.rows[0]["parts"][1]
    assert (p2["part_id"], p2["qty"], p2["price"]) == ("P2", 3, 30.0)
    assert any(n.startswith('Restored part "Belt"') for n in res.notes)


def test_new_part_goes_through_callback():
    def autofill(raw):
        return {**raw, "cost": 40.0, "price": 99.5, "unit": "each"}

    res = merge([R("A", "Job")], [R("A", "Job")], [M("A", "Job", [MP("P1", qty=2)])], new_part=autofill)
    assert res.rows[0]["parts"][0]["price"] == 99.5


def test_mechanic_web_legacy_qty_quirk():
    base = [R("A", "Job", [P("P1", qty=0)])]
    cur = [R("A", "Job", [P("P1", qty=3)])]
    legacy = merge(base, cur, [M("A", "Job", [MP("P1", qty=1)])], legacy=True, is_mechanic_web=True)
    assert legacy.rows[0]["parts"][0]["qty"] == 3
    assert legacy.foreign_any is True  # qty 3 взят у офиса (отличается от mine)
    unchanged = merge(base, [dict(base[0])], [M("A", "Job", [MP("P1", qty=1)])], legacy=True, is_mechanic_web=True)
    assert unchanged.rows[0]["parts"][0]["qty"] == 0
    assert unchanged.foreign_any is False
    # Новый JS шлёт qty как есть: 0 → 1 — правка механика.
    new_js = merge(base, cur, [M("A", "Job", [MP("P1", qty=1)])], legacy=False, is_mechanic_web=True)
    assert new_js.rows[0]["parts"][0]["qty"] == 1


def test_mechanic_web_quirk_does_not_resurrect_row():
    base = [R("A", "x"), R("B", "Job", [P("P1", qty=0)])]
    res = merge(base, [R("A", "x")], [M("A", "x"), M("B", "Job", [MP("P1", qty=1)])], legacy=True,
                is_mechanic_web=True)
    assert ids(res) == ["A"]


def test_mechanic_empty_part_description_keeps_stored_one():
    base = [R("A", "Job", [P("P1", desc="Brake pad")])]
    res = merge(base, [dict(base[0])], [M("A", "Job", [MP("P1", desc="")])])
    assert res.rows[0]["parts"][0]["description"] == "Brake pad"
    assert res.foreign_any is False


def test_office_part_money_from_current_forces_merged_totals_path():
    base = [R("A", "Job", [P("P1", qty=1, price=10.0)], hours="1.0", rate="std", total=100.0)]
    cur = [R("A", "Job", [P("P1", qty=1, price=14.0)], hours="1.0", rate="std", total=100.0)]
    mine = [R("A", "Job 2", [P("P1", qty=1, price=10.0)], hours="1.0", rate="std", total=100.0)]
    res = merge(base, cur, mine, OFFICE_SPEC)
    assert res.rows[0]["parts"][0]["price"] == 14.0
    assert res.foreign_structure is True


# ───────────────────────── misc-позиции ─────────────────────────

ENV = {"description": "Env fee", "price": 5, "quantity": 1, "manual": True, "taxable": True}
SHOP = {"description": "Shop fee", "price": 7, "quantity": 1, "manual": True, "taxable": True}


def _with_misc(parts, items, raw=None):
    parts = [dict(p) for p in parts]
    parts[0]["misc_charge_description"] = raw if raw is not None else json.dumps(items)
    return parts


def test_misc_mechanic_auto_and_office_manual_both_survive():
    epa = {"description": "EPA", "price": 3, "quantity": 2, "partIndex": 1, "taxable": True}
    base = [R("A", "Job", _with_misc([P("P0")], [ENV]))]
    cur = [R("A", "Job", _with_misc([P("P0"), P("P1", desc="Oil")], [ENV, epa]))]
    mine = [R("A", "Job", _with_misc([P("P0")], [ENV, SHOP]))]
    res = merge(base, cur, mine, OFFICE_SPEC)
    row = res.rows[0]
    assert [p["part_id"] for p in row["parts"]] == ["P0", "P1"]
    items = misc_of(row)
    assert sorted(i["description"] for i in items) == ["EPA", "Env fee", "Shop fee"]
    assert next(i for i in items if i["description"] == "EPA")["partIndex"] == 1
    assert row["parts"][1]["misc_charge_description"] == ""
    assert res.foreign_structure


def test_misc_partindex_follows_owner_when_earlier_part_removed():
    core = {"description": "Core fee", "price": 4, "quantity": 1, "partIndex": 1}
    base = [R("A", "Job", _with_misc([P("PA"), P("PB")], [core]))]
    cur_raw = '[{"description":"Core fee","price":4,"quantity":1,"partIndex":0}]'
    cur = [R("A", "Job", _with_misc([P("PB")], None, raw=cur_raw))]
    res = merge(base, cur, [M("A", "Job", [MP("PA"), MP("PB")])])
    row = res.rows[0]
    assert [p["part_id"] for p in row["parts"]] == ["PB"]
    assert row["parts"][0]["misc_charge_description"] == cur_raw  # байт-в-байт


def test_misc_partindex_remapped_for_kept_part():
    epa = {"description": "EPA", "price": 3, "quantity": 1, "partIndex": 2}
    base = [R("A", "Job", [P("PA"), P("PB")])]
    cur = [R("A", "Job", _with_misc([P("PA"), P("PB"), P("PX", desc="Battery")], [epa]))]
    res = merge(base, cur, [M("A", "Job", [MP("PA"), MP("PB"), MP("PN", desc="Hose")])])
    row = res.rows[0]
    assert [p["part_id"] for p in row["parts"]] == ["PA", "PB", "PN", "PX"]
    assert misc_of(row)[0]["partIndex"] == 3


def test_misc_byte_identical_when_unchanged():
    raw = '[{"description":"Disposal","price":5,"quantity":1,"partIndex":0,"taxable":true}]'
    stored = R("A", "Job", _with_misc([P("P1", qty=1)], None, raw=raw))
    res = merge([dict(stored)], [dict(stored)], [M("A", "Job 2", [MP("P1", qty=1)])])
    assert res.rows[0]["parts"][0]["misc_charge_description"] == raw


def test_misc_office_unchanged_keeps_client_string():
    raw_js = '[{"description":"Env fee","price":5,"quantity":1,"manual":true,"taxable":true}]'
    base = [R("A", "Job", _with_misc([P("P0")], [ENV]))]
    mine = [R("A", "Job 2", _with_misc([P("P0")], None, raw=raw_js))]
    res = merge(base, [dict(base[0])], mine, OFFICE_SPEC)
    assert res.rows[0]["parts"][0]["misc_charge_description"] == raw_js
    assert res.foreign_structure is False


def test_misc_catalog_items_of_new_part():
    def autofill(raw):
        return {**raw, "price": 20.0, "_catalog_misc_items": [{"description": "EPA fee", "price": 2.5, "taxable": True}]}

    res = merge([R("A", "Job", [P("P0")])], [R("A", "Job", [P("P0")])],
                [M("A", "Job", [MP("P0"), MP("P1", qty=2)])], new_part=autofill)
    row = res.rows[0]
    items = misc_of(row)
    assert items == [{"description": "EPA fee", "price": 2.5, "taxable": True, "quantity": 2, "partIndex": 1}]
    assert all("_catalog_misc_items" not in p for p in row["parts"])
    assert all("_catalog_misc_items" not in p for b in res.belief for p in b["parts"])


def test_misc_auto_items_of_removed_part_dropped_manual_kept():
    auto_b = {"description": "Core fee", "price": 4, "quantity": 1, "partIndex": 1}
    stored = R("A", "Job", _with_misc([P("PA"), P("PB")], [auto_b, ENV]))
    res = merge([dict(stored)], [dict(stored)], [M("A", "Job", [MP("PA")])])
    row = res.rows[0]
    assert [p["part_id"] for p in row["parts"]] == ["PA"]
    assert misc_of(row) == [ENV]


def test_misc_items_without_parts_are_dropped_and_counted():
    stored = R("A", "Job", _with_misc([P("PA")], [ENV]))
    res = merge([dict(stored)], [dict(stored)], [M("A", "Job", [])])
    assert res.rows[0]["parts"] == []
    assert res.stats["misc_dropped"] == 1


EPA = {"description": "EPA fee", "price": 3.35, "taxable": True, "quantity": 1}


def test_mechanic_belief_carries_misc_reindexed_onto_its_parts():
    """Ревью D4-01: belief механика (его sent-вьюха) хранит misc строки —
    partIndex по запчастям belief; сбор сохранённой чужой запчасти (её у
    клиента нет) в belief не попадает, сбор запчасти, удалённой другим (у
    клиента она есть), — берётся из base."""
    stored = R("A", "Job", _with_misc([P("PA"), P("PB", desc="Battery")], [dict(EPA, partIndex=1), ENV]))
    res = merge([dict(stored)], [dict(stored)], [M("A", "Job 2", [MP("PA"), MP("PB", desc="Battery")])])
    assert res.belief[0]["misc_items"] == [dict(EPA, partIndex=1), ENV]
    assert all(not p["misc_charge_description"] for p in res.belief[0]["parts"])

    cur = [R("A", "Job", _with_misc([P("PA"), P("PX", desc="Battery")], [dict(EPA, partIndex=1)]))]
    res = merge([R("A", "Job", [P("PA")])], cur, [M("A", "Job 2", [MP("PA")])])
    assert misc_of(res.rows[0]) == [dict(EPA, partIndex=1)]      # в WO сбор остался
    assert res.belief[0]["misc_items"] == []                      # у клиента PX нет

    base = [R("A", "Job", _with_misc([P("PA"), P("PB", desc="Battery")], [dict(EPA, partIndex=1)]))]
    res = merge(base, [R("A", "Job", [P("PA")])], [M("A", "Job 2", [MP("PA"), MP("PB", desc="Battery")])])
    assert [p["part_id"] for p in res.rows[0]["parts"]] == ["PA"]   # PB удалил другой — пропущена
    assert res.belief[0]["misc_items"] == [dict(EPA, partIndex=1)]


def test_mechanic_restore_from_its_sent_view_keeps_catalog_misc():
    """Ревью D4-01: base механика — его belief. Другой удалил работу (или
    одну запчасть с EPA), механик её поменял — восстановленная работа /
    запчасть возвращается вместе со сбором (раньше сбор терялся: недобор)."""
    battery = R("B", "Battery swap", _with_misc([P("BAT", desc="Battery", qty=1, price=120.0)], [dict(EPA, partIndex=0)]))
    cur0 = [R("A", "Main job"), battery]
    s1 = merge(_compact(cur0), cur0, [M("A", "Main job 2"), M("B", "Battery swap", [MP("BAT", desc="Battery")])],
               legacy=True)
    assert s1.belief[1]["misc_items"] == [dict(EPA, partIndex=0)]

    # Работу удалил другой; механик (base — его belief) её правит — возвращается с EPA.
    row = merge(s1.belief, [R("A", "Main job 2")],
                [M("A", "Main job 2"), M("B", "Battery swap - tested", [MP("BAT", desc="Battery")])], legacy=True)
    restored = next(r for r in row.rows if r["labor_id"] == "B")
    assert misc_of(restored) == [dict(EPA, partIndex=0)]

    # Другой убрал аккумулятор (единственную запчасть); механик поменял его qty — запчасть со сбором.
    part = merge(s1.belief, [R("A", "Main job 2"), R("B", "Battery swap", [])],
                 [M("A", "Main job 2"), M("B", "Battery swap", [MP("BAT", desc="Battery", qty=2)])], legacy=True)
    blk = next(r for r in part.rows if r["labor_id"] == "B")
    assert [(p["part_id"], p["qty"]) for p in blk["parts"]] == [("BAT", 2)]
    assert misc_of(blk) == [dict(EPA, partIndex=0)]
    assert any(n.startswith('Restored part "Battery"') for n in part.notes)


def _compact(rows):
    """Строки как в load-вьюхе: misc поднят в misc_items."""
    out = []
    for r in rows:
        items, parts = lift_misc(r)
        out.append({**r, "parts": parts, "misc_items": items})
    return out


def test_misc_conservative_office_keeps_items_of_kept_parts():
    epa = {"description": "EPA", "price": 3, "quantity": 1, "partIndex": 1}
    cur = [R("A", "Job", _with_misc([P("P0"), P("P1", desc="Oil")], [ENV, epa]))]
    mine = [R("A", "Job", _with_misc([P("P0")], [ENV]))]
    res = merge(None, cur, mine, OFFICE_SPEC, legacy=True)
    items = misc_of(res.rows[0])
    assert sorted(i["description"] for i in items) == ["EPA", "Env fee"]


# ───────────────────────── belief и флаги ─────────────────────────

def test_belief_overlays_mine_editable_values_only():
    stored = R("A", "Job", [P("P1", qty=1, price=50.0)], hours="2.0", rate="std", total=200.0, source="tracked")
    res = merge([dict(stored)], [dict(stored, description="Job (office)")], [M("A", "Job", [MP("P1", qty=1)])])
    assert res.rows[0]["description"] == "Job (office)"
    belief = res.belief[0]
    assert belief["description"] == "Job"  # так думает клиент
    assert (belief["hours"], belief["rate_code"], belief["labor_total"]) == ("2.0", "std", 200.0)
    assert belief["parts"][0]["price"] == 50.0


def test_belief_of_office_takes_client_money_group():
    res = merge([_money()], [_money()], [_money(hours="2.0", total=200.0, hours_edited=True)], OFFICE_SPEC)
    belief = res.belief[0]
    assert (belief["hours"], belief["labor_total"]) == ("2.0", 200.0)
    assert "hours_edited" not in belief


def test_conflict_free_office_save_is_not_foreign():
    stored = R("A", "Job", [P("P1", qty=1, price=10.0)], hours="1.0", rate="std", total=100.0)
    mine = [R("A", "Job 2", [P("P1", qty=2, price=11.0), P("P2", qty=1, price=3.0)], hours="1.5", rate="std",
              total=150.0, hours_edited=True)]
    res = merge([dict(stored)], [dict(stored)], mine, OFFICE_SPEC)
    assert res.foreign_any is False
    assert res.foreign_structure is False
    assert res.notes == [] and res.tracked_adjusted == []
    assert res.origins[0]["money_from_mine"] is True


def test_notes_are_capped():
    base = [R("A", "keep")] + [R(f"B{i}", f"Job {i}") for i in range(8)]
    mine = [M("A", "keep")] + [M(f"B{i}", f"Job {i}") for i in range(8)]
    res = merge(base, [R("A", "keep")], mine)
    assert len(res.notes) == 6
    assert res.notes[-1] == "And 3 more changes from other users."


def test_extra_notes_and_forced_foreign():
    res = merge(None, [R("A", "x")], [M("A", "x")], extra_notes=("Couldn't confirm.",), force_foreign=True)
    assert res.notes[0] == "Couldn't confirm."
    assert res.foreign_any is True


def test_labor_ids_aligned_with_mine_including_garbage_and_skipped_rows():
    base = [R("A", "x"), R("B", "y")]
    res = merge(base, [R("A", "x")], [M("A", "x"), "garbage", M("B", "y"), M("", "new", client_key="k")])
    assert res.labor_ids == ["A", "", "B", "N1"]
    assert res.minted == ["N1"]
    assert res.client_keys == {"k": "N1"}
    assert ids(res) == ["A", "N1"]


def test_origins_are_aligned_with_rows():
    base = [R("A", "x"), R("B", "y")]
    cur = [R("A", "x"), R("B", "y changed"), R("C", "other")]
    res = merge(base, cur, [M("A", "x 2"), M("", "new")])
    assert ids(res) == ["A", "N1", "B", "C"]
    assert [(o["kind"], o["mine_index"], o["current_index"]) for o in res.origins] == [
        ("merged", 0, 0), ("new", 1, None), ("kept", None, 1), ("kept", None, 2),
    ]


# ───────────────────────── устойчивость и прочие сценарии ревью ─────────────────────────

def test_stale_office_save_after_transfer_keeps_repriced_labor():
    """Ревью merge-semantics #23 (уровень строк): transfer переоценил строку,
    устаревшая вкладка офиса её не трогала — переоценка остаётся."""
    before = R("A", "Job", [P("P1", qty=1, price=10.0)], hours="1.0", rate="std", total=100.0)
    after = R("A", "Job", [P("P1", qty=1, price=8.0)], hours="1.0", rate="fleet", total=90.0)
    res = merge([dict(before)], [after], [dict(before)], OFFICE_SPEC)
    row = res.rows[0]
    assert (row["rate_code"], row["labor_total"], row["parts"][0]["price"]) == ("fleet", 90.0, 8.0)
    assert res.foreign_structure is True


def test_mechanic_notes_never_mention_money():
    base = [R("A", "x", [P("P1", desc="Belt", price=30.0)]), R("B", "Job B", hours="2.0", total=200.0)]
    cur = [R("A", "x", [P("P1", desc="Belt", price=30.0), P("P2", desc="Hose", price=12.0)]),
           R("C", "Job C", total=50.0)]
    res = merge(base, cur, [M("A", "x", []), M("B", "Job B")])
    assert res.notes
    text = " ".join(res.notes).lower()
    for word in ("$", "price", "rate", "hour", "total", "cost"):
        assert word not in text


def test_new_part_callback_may_reject_a_line():
    res = merge([R("A", "Job")], [R("A", "Job")], [M("A", "Job", [MP(desc="")])], new_part=lambda raw: None)
    assert res.rows[0]["parts"] == []
    assert res.stats["parts_rejected"] == 1


def test_garbage_shapes_do_not_crash():
    cur = [R("A", "x", parts="oops", assigned_mechanics="bad", hours="abc", labor_total="n/a", minted="bad"),
           "junk", None]
    mine = [R("A", "x", parts={"no": "list"}, misc_items="bad"), 42]
    res = merge([R("A", "x", parts=[P(misc_charge_description="[1, 2, {\"partIndex\": \"z\"}]")])], cur, mine,
                OFFICE_SPEC)
    assert ids(res) == ["A"]
    assert res.labor_ids == ["A", ""]


@pytest.mark.parametrize("spec", [MECHANIC_SPEC, MOBILE_MANAGER_SPEC, OFFICE_SPEC], ids=lambda s: s.name)
@pytest.mark.parametrize("bad", [5, True, 2.5, "oops", {"no": "list"}])
def test_non_list_parts_never_crash_the_core(spec, bad):
    """Ревью D1-06: parts не списком (кривой клиент) — не 500, а «нет
    запчастей», как в прежнем коде (normalize_parts_payload → []); колбэк
    new_block получает уже нормальный список."""
    seen = []

    def new_block(raw):
        seen.append(raw["parts"])
        return {**raw, "parts": [p for p in raw["parts"] if isinstance(p, dict)]}

    for base in ([R("A", "Brakes")], None):
        res = merge(base, [R("A", "Brakes", [P("P1")])],
                    [{"labor_id": "A", "description": "x", "parts": bad},
                     {"labor_id": "", "description": "New", "parts": bad}],
                    spec, legacy=base is None, new_block=new_block)
        assert [r["description"] for r in res.rows][:2] == ["x", "New"]
        assert res.rows[1]["parts"] == []
    assert seen and all(p == [] for p in seen)
    assert is_blank_row({"description": "", "parts": bad})
    assert split_misc_items(bad) == (None, [], None)
    assert lift_misc({"parts": bad, "misc_items": None}) == (None, [])
    assert match_parts(bad, [P("P1")]) == {}


def test_current_duplicate_ids_are_kept_not_lost():
    cur = [R("A", "first"), R("A", "second copy")]
    res = merge([R("A", "first")], cur, [M("A", "first edited")])
    assert descs(res) == ["first edited", "second copy"]
    assert res.foreign_any is False
