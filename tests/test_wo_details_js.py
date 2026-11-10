# -*- coding: utf-8 -*-
"""
Офисная страница WO (work_order_details.js): итог Save, база слияния,
уход из paid, записи в обход Save.

Браузера в тестах нет, поэтому:
- чистые функции модуля (без DOM) исполняются в node и проверяются по
  поведению — тест пропускается, если node не установлен;
- проводка (что уходит в запросы, где вызываются функции) закреплена
  проверкой исходника по вырезанным кускам — регрессия из ревью D5
  (D5-01..D5-05, D5-V1) вернула бы старый текст и уронила бы тест.

Путь к файлу можно подменить переменной WO_DETAILS_JS_PATH (так проверяется,
что тесты падают на версии без исправления).
"""
import json
import os
import re
import shutil
import subprocess

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_PATH = os.environ.get("WO_DETAILS_JS_PATH") or os.path.join(
    _REPO, "app", "static", "js", "work_orders", "work_order_details.js"
)

HELPERS = (
    "afterSaveAction",
    "summaryNeedsDialog",
    "parseStashedSummary",
    "summaryWasAcknowledged",
    "leftPaidWithoutBase",
    "nextEditViewId",
    "hoursFlagSaved",
    "swalDialogOpen",
)


def _src() -> str:
    with open(JS_PATH, encoding="utf-8") as fh:
        return fh.read().replace("\r\n", "\n")


def _module_function(src: str, name: str) -> str:
    """Функция уровня модуля (отступ 2): от `function name(` до строки `  }`."""
    m = re.search(rf"^  function {re.escape(name)}\(.*?^  \}}$", src, re.M | re.S)
    assert m, f"module-level function {name}() not found in work_order_details.js"
    return m.group(0)


def _between(src: str, start: str, end: str) -> str:
    """Кусок исходника от якоря start до ближайшего после него якоря end."""
    i = src.find(start)
    assert i >= 0, f"anchor not found: {start!r}"
    j = src.find(end, i + len(start))
    assert j >= 0, f"end anchor not found after {start!r}: {end!r}"
    return src[i:j]


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)


# ───────────────────────── node: чистые функции ─────────────────────────

_NODE_RUNNER = r"""
const calls = %(calls)s;
const fns = { %(names)s };
// Подделка SweetAlert2 для swalDialogOpen: {"__swal__": {"visible": .., "toast": ..}}.
function revive(arg) {
  if (arg && typeof arg === "object" && arg.__swal__) {
    const s = arg.__swal__;
    return {
      isVisible: () => !!s.visible,
      getPopup: () => (s.popup === false ? null : {
        classList: { contains: (c) => c === "swal2-toast" ? !!s.toast : false },
      }),
    };
  }
  return arg;
}
const out = calls.map(([name, args]) => {
  const r = fns[name](...args.map(revive));
  return r === undefined ? null : r;
});
process.stdout.write(JSON.stringify(out));
"""


def _node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return node


def _call(calls, tmp_path):
    """calls — [(имя функции, [аргументы])] → результаты в том же порядке."""
    node = _node()
    src = _src()
    helpers = "\n\n".join(_module_function(src, n) for n in HELPERS)
    script = '"use strict";\n' + helpers + "\n" + _NODE_RUNNER % {
        "calls": json.dumps(calls),
        "names": ", ".join(HELPERS),
    }
    path = tmp_path / "wo_details_helpers.js"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_after_save_action_never_reloads_over_edits_made_during_save(tmp_path):
    """D5-V1 / D5-04: правки во время Save → остаёмся (без перезагрузки);
    слияние чужих правок или Save as Estimate / Convert → перезагрузка с
    итогом; иначе итог на месте."""
    cases = [
        ({"merged": False, "editedDuringSave": False, "reloadAfter": False}, "done"),
        ({"merged": True, "editedDuringSave": False, "reloadAfter": False}, "reload"),
        ({"merged": False, "editedDuringSave": False, "reloadAfter": True}, "reload"),
        ({"merged": True, "editedDuringSave": True, "reloadAfter": False}, "stay"),
        ({"merged": False, "editedDuringSave": True, "reloadAfter": True}, "stay"),
        ({"merged": False, "editedDuringSave": True, "reloadAfter": False}, "stay"),
    ]
    got = _call([("afterSaveAction", [opts]) for opts, _ in cases], tmp_path)
    assert got == [want for _, want in cases]


def test_summary_dialog_for_notes_without_foreign_merge(tmp_path):
    """D5-03: заметка при merged_foreign:false (юнит сменили оба — оставлен
    наш) открывает окно со списком, а не теряется за тостом «Saved.»."""
    unit_note = "The unit was also changed by another user; your choice was kept."
    got = _call([
        ("summaryNeedsDialog", [{"notes": [unit_note], "warnings": []}]),
        ("summaryNeedsDialog", [{"notes": [], "warnings": ["Saved, but stock levels could not be updated."]}]),
        ("summaryNeedsDialog", [{"notes": [], "warnings": [], "pending": True}]),
        ("summaryNeedsDialog", [{"notes": [], "warnings": []}]),
        ("summaryNeedsDialog", [{"notes": ["  ", None], "warnings": [""]}]),
        ("summaryNeedsDialog", [None]),
    ], tmp_path)
    assert got == [True, True, True, False, False, False]


def test_stashed_summary_survives_reload_until_acknowledged(tmp_path):
    """D5-05: итог из sessionStorage читается, пока свежий (10 минут);
    снимается только когда окно закрыл человек — вытеснение другим
    SweetAlert (результат без dismiss) итог не «читает»."""
    now = 1_800_000_000_000
    fresh = json.dumps({"title": "Saved.", "notes": ["Kept job \"X\" added by another user."], "at": now - 60_000})
    stale = json.dumps({"title": "Saved.", "notes": ["n"], "at": now - 11 * 60_000})
    legacy = json.dumps({"title": "Saved.", "notes": ["n"]})
    got = _call([
        ("parseStashedSummary", [fresh, now]),
        ("parseStashedSummary", [stale, now]),
        ("parseStashedSummary", [legacy, now]),
        ("parseStashedSummary", ["{not json", now]),
        ("parseStashedSummary", ["[1, 2]", now]),
        ("parseStashedSummary", [None, now]),
        ("summaryWasAcknowledged", [{"isConfirmed": True, "isDenied": False, "isDismissed": False}]),
        ("summaryWasAcknowledged", [{"isConfirmed": False, "isDismissed": True, "dismiss": "esc"}]),
        ("summaryWasAcknowledged", [{"isConfirmed": False, "isDismissed": True, "dismiss": "backdrop"}]),
        ("summaryWasAcknowledged", [{"isDismissed": True}]),
        ("summaryWasAcknowledged", [None]),
    ], tmp_path)
    assert got[0]["notes"] == ["Kept job \"X\" added by another user."]
    assert got[1] is None
    assert got[2]["title"] == "Saved."
    assert got[3:6] == [None, None, None]
    assert got[6:] == [True, True, True, False, True]


def test_live_poll_waits_while_a_sweetalert_dialog_is_open(tmp_path):
    """D5-05: открыт диалог SweetAlert (итог Save, подтверждение) — опрос
    ждёт; тост и закрытый SweetAlert опрос не задерживают."""
    got = _call([
        ("swalDialogOpen", [{"__swal__": {"visible": True, "toast": False}}]),
        ("swalDialogOpen", [{"__swal__": {"visible": True, "toast": True}}]),
        ("swalDialogOpen", [{"__swal__": {"visible": False, "toast": False}}]),
        ("swalDialogOpen", [{"__swal__": {"visible": True, "popup": False}}]),
        ("swalDialogOpen", [None]),
    ], tmp_path)
    assert got == [True, False, False, True, False]


def test_leaving_paid_without_a_base_reloads(tmp_path):
    """D5-01: ушли из paid, а view_id пуст (вьюху оплаченного WO не
    записали) и человек может сохранять — страницу надо перечитать."""
    got = _call([
        ("leftPaidWithoutBase", ["paid", "open", "", True]),
        ("leftPaidWithoutBase", ["paid", "in_progress", "", True]),
        ("leftPaidWithoutBase", ["paid", "open", "6a6a55ea0000000000000001", True]),
        ("leftPaidWithoutBase", ["open", "open", "", True]),
        ("leftPaidWithoutBase", ["paid", "paid", "", True]),
        ("leftPaidWithoutBase", ["paid", "open", "", False]),
    ], tmp_path)
    assert got == [True, True, False, False, False, False]


def test_side_write_view_id_is_adopted_only_when_present(tmp_path):
    """D5-02: Describe issue / правка часов вернули выведенную вьюху — она
    становится базой; пустой или отсутствующий view_id (старый сервер,
    вьюха не нашлась) базу не трогает."""
    got = _call([
        ("nextEditViewId", ["view-a", "view-b"]),
        ("nextEditViewId", ["view-a", ""]),
        ("nextEditViewId", ["view-a", None]),
        ("nextEditViewId", ["view-a", 5]),
        ("nextEditViewId", ["", "view-b"]),
    ], tmp_path)
    assert got == ["view-b", "view-a", "view-a", "view-a", "view-b"]


def test_hours_flag_is_kept_when_hours_were_edited_again_during_save(tmp_path):
    """D5-V1: флаг ручных часов снимается после Save, только если номер
    правки не сменился, пока Save был в полёте."""
    got = _call([
        ("hoursFlagSaved", ["3", "3"]),
        ("hoursFlagSaved", ["", ""]),
        ("hoursFlagSaved", ["", None]),
        ("hoursFlagSaved", ["3", "4"]),
        ("hoursFlagSaved", ["", "5"]),
    ], tmp_path)
    assert got == [True, True, True, False, False]


# ───────────────────────── проводка (исходник) ─────────────────────────


def _save_fn(src):
    return _between(src, "async function saveWorkOrderEdits(", "// Обычный Save сметы оставляет её сметой")


def test_save_shows_notes_reloads_with_summary_and_guards_in_flight_edits():
    """D5-03 / D5-04 / D5-V1: исход Save решает afterSaveAction; итог «на
    месте» показывается всегда через showMergeSummary (заметки без
    merged_foreign не теряются); перед перезагрузкой итог сохраняется;
    форма заперта на время запроса, правки за это время ловит снимок."""
    body = _squash(_save_fn(_src()))
    assert "const editedDuringSave = saveFormSnapshot(serializeBlocks(blocksContainer), " \
           "serializeTotals(blocksContainer)) !== sentForm;" in body
    assert "afterSaveAction({ merged: summary.merged, editedDuringSave, " \
           "reloadAfter: !!(reloadIf && reloadIf(workOrderStatus)), })" in body
    assert 'if (action === "reload") { ' in body and "stashMergeSummary(summary); pageReloading = true; liveReload();" in body
    assert "showMergeSummary({ ...summary, pending: true });" in body
    # итог на месте: без старого условия «только при warnings»
    assert "if (warnings.length) {" not in body
    assert body.rstrip().count("showMergeSummary(summary);") == 1
    # запрос уходит при запертой форме; замок снимается, если страница не перезагружается
    assert body.index("setFormBusy(true);") < body.index("await apiPostJson(")
    assert "if (!pageReloading) { setSaveButtonsBusy(false); setFormBusy(false); }" in body
    # флаги ручных часов: снимаются только неизменённые с отправки
    assert "if (hoursFlagSaved(sentHoursFlags[i], bEl.dataset.hoursEdited)) delete bEl.dataset.hoursEdited;" in body
    # база для «остаёмся со своими строками» — отправленная вьюха
    assert "editViewId = (resp.merged_foreign && !editedDuringSave) ? String(resp.merged_view_id" in body


def test_hours_flag_carries_an_edit_number():
    """D5-V1: правка часов пишет во флаг новый номер правки, сериализация
    шлёт hours_edited по наличию флага."""
    src = _src()
    assert src.count("blockEl.dataset.hoursEdited = String(++hoursEditSeq);") == 2
    assert 'blockEl.dataset.hoursEdited = "1"' not in src
    assert "hours_edited: !!bEl.dataset.hoursEdited," in src


def test_estimate_and_convert_reload_through_save_summary():
    """D5-04: Save as Estimate / Convert не перезагружают страницу сами
    (это стирало окно с предупреждениями) — Save перечитывает её, сохранив
    итог."""
    src = _src()
    est = _squash(_between(src, 'document.getElementById("saveAsEstimateBtn")?.addEventListener',
                           'document.getElementById("convertEstimateBtn")?.addEventListener'))
    conv = _squash(_between(src, 'document.getElementById("convertEstimateBtn")?.addEventListener',
                            "// ---------- live updates"))
    assert 'saveWorkOrderEdits("estimate", { reloadIf: (status) => status === "estimate" })' in est
    assert 'saveWorkOrderEdits("open", { reloadIf: (status) => status !== "estimate" })' in conv
    assert "window.location.reload()" not in est
    assert "window.location.reload()" not in conv


def test_stashed_summary_is_kept_until_closed_and_poll_waits_for_dialogs():
    """D5-05: итог снимается из sessionStorage только после закрытия окна;
    live-опрос ничего не решает, пока окно итога / диалог SweetAlert открыт."""
    src = _src()
    onload = _squash(_between(src, "if (isCreated && workOrderId) {\n      let raw = null;", "async function pollLiveState()"))
    assert "parseStashedSummary(raw, Date.now())" in onload
    assert "if (summaryWasAcknowledged(result)) dropMergeSummary();" in onload
    assert "sessionStorage.removeItem(MERGE_SUMMARY_KEY)" not in onload
    poll = _squash(_between(src, "async function pollLiveState()", "setInterval(pollLiveState"))
    assert 'if (mergeSummaryOpen || swalDialogOpen(typeof Swal === "undefined" ? null : Swal)) return;' in poll
    assert poll.index("swalDialogOpen(") < poll.index("liveReload()")
    show = _squash(_between(src, "function showMergeSummary(info, opts) {", "// Итог Save, сохранённый перед перезагрузкой"))
    assert "mergeSummaryOpen = token;" in show and "}).then(closed, closed);" in show


def test_leaving_paid_without_base_reloads_the_page():
    """D5-01: Unpaid и удаление последнего платежа на странице без базы
    слияния перечитывают страницу (с коротким итогом после перезагрузки)."""
    src = _src()
    unpaid = _squash(_between(src, 'unpaidBtn?.addEventListener("click"', "// delete work order"))
    assert "if (leftPaidWithoutBase(prevStatus, workOrderStatus, editViewId, canSaveWo)) {" in unpaid
    assert 'stashMergeSummary({ title: "Marked as unpaid." }); pageReloading = true; liveReload(); return;' in unpaid
    assert unpaid.index("leftPaidWithoutBase(") < unpaid.index("applyStateFromStatus();")
    pay = _squash(_between(src, 'event.target.closest(".js-delete-wo-payment-inline")', "// ── Email contacts modal logic"))
    assert "if (leftPaidWithoutBase(prevStatus, workOrderStatus, editViewId, canSaveWo)) {" in pay
    assert 'stashMergeSummary({ title: "Payment deleted." }); pageReloading = true; liveReload(); return;' in pay
    assert pay.index("leftPaidWithoutBase(") < pay.index("applyStateFromStatus();")


def test_describe_and_time_log_writes_send_and_adopt_view_id():
    """D5-02 (контракт C-A): Describe issue, правка и удаление сессии часов
    шлют view_id страницы и берут выведенный сервером view_id из ответа.
    Удаление сессии шлёт JSON-тело."""
    src = _src()
    describe = _squash(_between(src, "describeSaveBtn.addEventListener(\"click\"", "if (describeModal) describeModal.hide();\n      });"))
    assert "view_id: editViewId," in describe
    assert describe.index("learnLaborsRev(data.labors_rev); adoptEditViewId(data.view_id);") > describe.index("view_id: editViewId,")
    delete = _squash(_between(src, "/work_orders/api/time_logs/${encodeURIComponent(logId)}/delete", "toast(\"Session deleted\""))
    assert '"Content-Type": "application/json"' in delete
    assert "body: JSON.stringify({ view_id: editViewId })," in delete
    assert "adoptEditViewId(data.view_id);" in delete
    update = _squash(_between(src, "/work_orders/api/time_logs/${encodeURIComponent(change.id)}/update", "timeLogsChanged = true;"))
    assert "body: JSON.stringify({ seconds: change.seconds, view_id: editViewId })," in update
    assert "adoptEditViewId(data.view_id);" in update
    adopt = _squash(_between(src, "function adoptEditViewId(id) {", "}"))
    assert "editViewId = nextEditViewId(editViewId, id);" in adopt


def test_helpers_are_module_level_and_syntax_is_valid():
    """Файл разбирается node (--check), чистые функции — уровня модуля."""
    node = _node()
    proc = subprocess.run([node, "--check", JS_PATH], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    src = _src()
    for name in HELPERS:
        _module_function(src, name)
