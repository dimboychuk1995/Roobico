# -*- coding: utf-8 -*-
"""
Страница WO механика (app/static/js/mechanic/mechanic_wo.js): живое
обновление, замок, таймеры, пробег — по поведению.

Браузера в тестах нет: страница исполняется в node (vm) с заглушками DOM,
ответы сервера заданы сценарием, часы (Date.now) поддельные и идут на 20 с
за тик опроса. window.appAlert ведёт себя как в layouts/public_base.html:
success/info — тост (не блокирует), остальное — окно SweetAlert. Тест
пропускается, если node не установлен.

Закреплены исправления из ревью D6:
- D6-01: закрытый механику WO (подтверждён / смета) опрос не перечитывает
  каждые 20 с с окном ошибки — один неблокирующий баннер; неудачное
  перечитывание повторяется с растущей паузой и одним тостом;
- D6-02 (контракт C-B): ответ своего Start/Stop с выросшей ревизией на
  странице без правок — перечитывание WO (чужая правка не маскируется), с
  правками — ревизия запоминается;
- D6-03: перечитывание не затирает пробег, который печатают в шапке;
- Start на смете (estimate_cannot_track) — понятный текст и замок.

Путь к файлу можно подменить переменной MECHANIC_WO_JS_PATH (так
проверяется, что тесты падают на версии без исправления).
"""
import json
import os
import shutil
import subprocess

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_PATH = os.environ.get("MECHANIC_WO_JS_PATH") or os.path.join(
    _REPO, "app", "static", "js", "mechanic", "mechanic_wo.js"
)

# Заглушки DOM + сценарии. Печатает JSON {"<сценарий>": {"<проверка>": {ok, detail}}}.
_NODE_SCRIPT = r"""
"use strict";
const fs = require("fs");
const vm = require("vm");
const FILE = process.argv[2];

function classList(initial) {
  const s = new Set(initial || []);
  return {
    contains: (c) => s.has(c),
    add: (...cs) => cs.forEach((c) => s.add(c)),
    remove: (...cs) => cs.forEach((c) => s.delete(c)),
    toggle: (c, on) => { if (on === undefined ? !s.has(c) : on) s.add(c); else s.delete(c); return s.has(c); },
  };
}

function el(id, initialClasses) {
  const listeners = {};
  return {
    id, tagName: id === "mechWoMileage" ? "INPUT" : "DIV",
    textContent: "", innerHTML: "", value: "", disabled: false,
    style: {}, dataset: {}, classList: classList(initialClasses), options: [], selectedIndex: 0,
    _listeners: listeners,
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    dispatchEvent() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    contains(x) { return !!(x && x._insideLabors); }, closest() { return null; },
    focus() {}, select() {}, appendChild() {}, setAttribute() {}, getAttribute() { return null; },
  };
}

async function flush(n = 40) {
  for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r));
}

async function boot(route, opts = {}) {
  const els = {};
  const docListeners = {};
  const dialogs = [];
  const requests = [];
  const intervals = [];
  // Как в шаблоне: баннер замка скрыт классом d-none.
  const initialClasses = { mechLockNotice: ["alert", "alert-warning", "d-none"] };
  const getEl = (id) => {
    if (id === "mechListPage") return null;
    if (!els[id]) els[id] = el(id, initialClasses[id]);
    return els[id];
  };
  const root = getEl("mechDetailsPage");
  root.dataset.mode = "edit";
  root.dataset.workOrderId = "WO1";
  root.dataset.mechanicMode = opts.mechanicMode === false ? "0" : "1";

  const clock = { now: Date.UTC(2026, 10, 9, 12, 0, 0) };
  class FakeDate extends Date {
    constructor(...args) { if (args.length) super(...args); else super(clock.now); }
    static now() { return clock.now; }
  }
  const document = {
    hidden: false, activeElement: null,
    documentElement: { getAttribute: () => "light", setAttribute() {} },
    getElementById: getEl,
    addEventListener(t, fn) { (docListeners[t] = docListeners[t] || []).push(fn); },
    querySelector(sel) {
      if (sel === ".modal.show") {
        return Object.values(els).find((e) => e.classList.contains("modal") && e.classList.contains("show")) || null;
      }
      return null;
    },
    createElement() { return el(""); },
  };
  function Modal(e) { e.classList.add("modal"); this.show = () => e.classList.add("show"); this.hide = () => e.classList.remove("show"); }
  const window = {
    appAlert(msg, type) {
      type = type || "info";
      dialogs.push({ kind: type, blocking: !(type === "success" || type === "info"), msg: String(msg) });
    },
    bootstrap: { Modal },
    crypto: { randomUUID: () => "uuid-" + Math.random().toString(36).slice(2) },
    location: { search: opts.search || "", href: "" },
    alert(msg) { dialogs.push({ kind: "native-alert", blocking: true, msg: String(msg) }); },
  };
  async function fetch(url, init) {
    const method = (init && init.method) || "GET";
    const body = init && init.body ? JSON.parse(init.body) : undefined;
    requests.push({ method, url, body });
    const r = await route(method, url, body);
    return { ok: r.status >= 200 && r.status < 300, status: r.status, json: async () => r.body };
  }
  const sandbox = {
    window, document, fetch, URLSearchParams, console, Date: FakeDate,
    localStorage: { getItem() { return null; }, setItem() {} },
    setInterval(fn, ms) { intervals.push({ fn, ms }); return intervals.length; },
    clearInterval() {}, setTimeout() { return 0; }, clearTimeout() {},
    Event: function Event(t) { this.type = t; },
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(FILE, "utf8"), sandbox, { filename: FILE });
  (docListeners.DOMContentLoaded || []).forEach((fn) => fn());
  await flush();

  const isDetailsReq = (r) => r.method === "GET" && /\/api\/mechanic\/work_orders\/WO1(\?mp=1)?$/.test(r.url);
  return {
    els, dialogs, requests, document,
    async poll() {
      clock.now += 20000;
      for (const i of intervals.filter((x) => x.ms === 20000)) i.fn();
      await flush();
    },
    async clickLabor(idx, sel) {
      const btn = { disabled: false };
      const target = {
        closest(q) {
          if (q === ".mech-labor-card") return { dataset: { laborIndex: String(idx) } };
          if (q === sel) return btn;
          return null;
        },
      };
      for (const fn of els.mechLaborsContainer._listeners.click || []) await fn({ target });
      await flush();
    },
    async typeInLabors() {
      const target = { classList: classList(["mech-labor-desc"]) };
      for (const fn of els.mechLaborsContainer._listeners.input || []) fn({ target });
      await flush();
    },
    async clickSave() {
      for (const fn of els.mechSaveBtn._listeners.click || []) await fn({});
      await flush();
    },
    async clickBarStop() {
      for (const fn of els.mechTimerBarStopBtn._listeners.click || []) await fn({});
      await flush();
    },
    rows() {
      const html = els.mechLaborsContainer ? els.mechLaborsContainer.innerHTML : "";
      return [...html.matchAll(/<textarea[^>]*>([\s\S]*?)<\/textarea>/g)].map((m) => m[1]);
    },
    detailGets() { return requests.filter(isDetailsReq).length; },
    blocking() { return dialogs.filter((d) => d.blocking); },
    toasts() { return dialogs.filter((d) => !d.blocking); },
    lockNotice() {
      const n = els.mechLockNotice;
      if (!n) return { visible: false, text: "" };
      const t = els.mechLockNoticeText;
      return { visible: !n.classList.contains("d-none"), text: (t && t.textContent) || n.textContent || "" };
    },
    saveDisabled() { return !!(els.mechSaveBtn && els.mechSaveBtn.disabled); },
  };
}

// ---------- ответы сервера ----------
const iso = () => new Date().toISOString();
const REV_NOTICE_RE = /Someone else just updated/;
function bucket(running) {
  const t = iso();
  return { total_seconds: 0, completed_seconds: 0, my_seconds: 0, my_running: !!running,
    my_started_at: running ? t : "", running_users: running ? [{ user_name: "Andy", mine: true, started_at: t }] : [] };
}
function labor(id, desc, running) {
  return { labor_id: id, description: desc, issue_description: "", parts: [], time: bucket(running) };
}
function details(labors, rev, extra) {
  return { status: 200, body: Object.assign({
    ok: true, id: "WO1", wo_number: 7, status: "in_progress", manager_confirmed: false,
    customer: { id: "C1", label: "Fleet" }, unit: { id: "U1", label: "Truck 1", mileage: 50000 },
    date: "11/09/2026", labors, appointments: [], my_running_timer: null,
    labors_rev: rev, view_id: "V" + rev, mileage_required: true, server_now: iso(),
  }, extra || {}) };
}
const CONFIRMED_403 = { status: 403, body: { ok: false, error: "wo_confirmed",
  message: "This work order was confirmed by a manager and is locked." } };
const ESTIMATE_400 = { status: 400, body: { ok: false, error: "estimate_locked_for_mechanics" } };
const OK = { status: 200, body: { ok: true } };
const isRev = (u) => /\/rev$/.test(u);
const isDetails = (m, u) => m === "GET" && /\/api\/mechanic\/work_orders\/WO1(\?mp=1)?$/.test(u);
const isUpdate = (m, u) => m === "POST" && /\/api\/mechanic\/work_orders\/WO1$/.test(u);
const revBody = (rev, locked) => ({ status: 200, body: { ok: true, labors_rev: rev, locked: !!locked, mechanic_done: false } });
function timerBody(rev, running) {
  const t = iso();
  return { status: 200, body: { ok: true,
    timer: { work_order_id: "WO1", wo_number: 7, labor_id: "L1", started_at: t },
    stopped_previous: null, labors_rev: rev, server_now: t, time_summary: { L1: bucket(running) } } };
}

const out = {};
function check(scenario, name, cond, detail) {
  (out[scenario] = out[scenario] || {})[name] = { ok: !!cond, detail: detail === undefined ? null : detail };
}

const SCENARIOS = {
  // D6-01 A: механик открыл подтверждённый WO.
  async confirmed_at_open() {
    const S = "confirmed_at_open";
    let unlocked = false;
    const h = await boot(async (m, u) => {
      if (isRev(u)) return revBody(3, !unlocked);
      if (isDetails(m, u)) return unlocked ? details([labor("L1", "Brakes")], 3) : CONFIRMED_403;
      return OK;
    }, { search: "?mileage=50000" });
    const blockingAtLoad = h.blocking().length;
    for (let i = 0; i < 6; i++) await h.poll();
    check(S, "one dialog at open", blockingAtLoad === 1, h.dialogs.map((d) => d.msg));
    check(S, "no details refetch while locked", h.detailGets() === 1, h.detailGets());
    check(S, "poll adds no dialogs or toasts", h.dialogs.length === blockingAtLoad, h.dialogs.map((d) => d.msg));
    check(S, "notice shows the server reason, Save disabled",
      h.lockNotice().visible && /confirmed by a manager/.test(h.lockNotice().text) && h.saveDisabled(), h.lockNotice());
    unlocked = true;
    await h.poll();
    check(S, "unlock loads the page, hides the notice, enables Save",
      h.detailGets() === 2 && h.rows().join() === "Brakes" && !h.lockNotice().visible && !h.saveDisabled(),
      { gets: h.detailGets(), rows: h.rows(), notice: h.lockNotice(), saveDisabled: h.saveDisabled() });
  },

  // D6-01 B: страница открыта без правок; подтверждение; офис правит; снятие.
  async confirmed_while_open() {
    const S = "confirmed_while_open";
    let phase = 0;
    const h = await boot(async (m, u) => {
      if (isRev(u)) return revBody(phase >= 2 ? 3 : 2, phase === 1 || phase === 2);
      if (isDetails(m, u)) {
        if (phase === 0) return details([labor("L1", "Brakes")], 2);
        if (phase === 3) return details([labor("L1", "Brakes"), labor("L2", "Check lights")], 3);
        return CONFIRMED_403;
      }
      return OK;
    }, { search: "?mileage=50000" });
    await h.poll();
    phase = 1;
    for (let i = 0; i < 3; i++) await h.poll();
    check(S, "confirm: notice, Save disabled, no dialog, no refetch",
      h.lockNotice().visible && h.saveDisabled() && h.blocking().length === 0 && h.detailGets() === 1,
      { notice: h.lockNotice(), dialogs: h.dialogs.map((d) => d.msg), gets: h.detailGets() });
    phase = 2;
    for (let i = 0; i < 6; i++) await h.poll();
    check(S, "office edits while locked: no refetch, no dialogs",
      h.detailGets() === 1 && h.dialogs.length === 0, { gets: h.detailGets(), dialogs: h.dialogs.map((d) => d.msg) });
    phase = 3;
    await h.poll();
    check(S, "unconfirm: refetch shows the office job, Save enabled",
      h.detailGets() === 2 && h.rows().join() === "Brakes,Check lights" && !h.saveDisabled() && !h.lockNotice().visible,
      { gets: h.detailGets(), rows: h.rows(), saveDisabled: h.saveDisabled(), notice: h.lockNotice() });
  },

  // D6-01: смета (ответ одним кодом).
  async estimate_at_open() {
    const S = "estimate_at_open";
    const h = await boot(async (m, u) => {
      if (isRev(u)) return revBody(5, true);
      if (isDetails(m, u)) return ESTIMATE_400;
      return OK;
    }, { search: "?mileage=50000" });
    for (let i = 0; i < 6; i++) await h.poll();
    check(S, "no refetch loop, one dialog", h.detailGets() === 1 && h.blocking().length === 1,
      { gets: h.detailGets(), dialogs: h.dialogs.map((d) => d.msg) });
    check(S, "notice is readable text, not the code",
      h.lockNotice().visible && /estimate/i.test(h.lockNotice().text) && !/estimate_locked/.test(h.lockNotice().text),
      h.lockNotice());
  },

  // D6-01: перечитывание падает (500) — пауза растёт, один тост, потом восстановление.
  async failing_refresh() {
    const S = "failing_refresh";
    let phase = 0;
    const h = await boot(async (m, u) => {
      if (isRev(u)) return revBody(phase ? 3 : 2, false);
      if (isDetails(m, u)) {
        if (phase === 0) return details([labor("L1", "Brakes")], 2);
        if (phase === 1) return { status: 500, body: {} };
        return details([labor("L1", "Brakes"), labor("L2", "Check lights")], 3);
      }
      return OK;
    }, { search: "?mileage=50000" });
    phase = 1;
    for (let i = 0; i < 15; i++) await h.poll();
    const refetches = h.detailGets() - 1;
    check(S, "backs off: 1..5 attempts in 15 polls", refetches >= 1 && refetches <= 5, refetches);
    check(S, "no dialogs, exactly one quiet notice", h.blocking().length === 0 && h.toasts().length === 1,
      h.dialogs.map((d) => d.kind + ":" + d.msg));
    phase = 2;
    for (let i = 0; i < 16 && h.rows().length < 2; i++) await h.poll();
    check(S, "recovers when details work again", h.rows().join() === "Brakes,Check lights", h.rows());
  },

  // WO удалён: одно сообщение, опрос его не повторяет.
  async deleted_wo() {
    const S = "deleted_wo";
    const gone = { status: 404, body: { ok: false, error: "work_order_not_found" } };
    const h = await boot(async (m, u) => (isRev(u) || isDetails(m, u) ? gone : OK), { search: "?mileage=50000" });
    for (let i = 0; i < 3; i++) await h.poll();
    check(S, "a single dialog", h.dialogs.length === 1, h.dialogs.map((d) => d.msg));
  },

  // D6-02 / C-B: Start на странице без правок сразу после чужой правки.
  async start_clean() {
    const S = "start_clean";
    let phase = 0;
    const h = await boot(async (m, u) => {
      if (isRev(u)) return revBody(phase ? 4 : 2, false);
      if (isDetails(m, u)) return phase ? details([labor("L1", "Brakes", true), labor("L2", "Check lights")], 4)
        : details([labor("L1", "Brakes")], 2);
      if (/timers\/start$/.test(u)) return timerBody(4, true);
      return OK;
    }, { search: "?mileage=50000" });
    phase = 1;
    await h.clickLabor(0, "[data-role='timer-btn']");
    check(S, "refetch shows the office job and the running timer",
      h.rows().join() === "Brakes,Check lights" && h.detailGets() === 2 &&
        h.els.mechLaborsContainer.innerHTML.includes("Stop job") && h.els.mechTimerBar.style.display === "flex",
      { rows: h.rows(), gets: h.detailGets() });
    for (let i = 0; i < 3; i++) await h.poll();
    check(S, "no extra refetch or notice afterwards",
      h.detailGets() === 2 && !h.dialogs.some((d) => REV_NOTICE_RE.test(d.msg)), h.dialogs.map((d) => d.msg));
  },

  // D6-02 / C-B: Stop кнопкой работы на странице без правок.
  async stop_clean() {
    const S = "stop_clean";
    let phase = 0;
    const h = await boot(async (m, u) => {
      if (isRev(u)) return revBody(phase ? 4 : 2, false);
      if (isDetails(m, u)) return phase ? details([labor("L1", "Brakes"), labor("L2", "Check lights")], 4)
        : details([labor("L1", "Brakes", true)], 2);
      if (/timers\/stop$/.test(u)) return timerBody(4, false);
      return OK;
    }, { search: "?mileage=50000" });
    phase = 1;
    await h.clickLabor(0, "[data-role='timer-btn']");
    check(S, "refetch shows the office job", h.rows().join() === "Brakes,Check lights", h.rows());
  },

  // C-B: страница с правками — ревизию своего Start запоминает, не перечитывает.
  async start_dirty() {
    const S = "start_dirty";
    let phase = 0;
    const h = await boot(async (m, u) => {
      if (isRev(u)) return revBody(phase ? 3 : 2, false);
      if (isDetails(m, u)) return details([labor("L1", "Brakes")], 2);
      if (/timers\/start$/.test(u)) return timerBody(3, true);
      return OK;
    }, { search: "?mileage=50000" });
    await h.typeInLabors();
    phase = 1;
    await h.clickLabor(0, "[data-role='timer-btn']");
    check(S, "no refetch over unsaved edits", h.detailGets() === 1, h.detailGets());
    for (let i = 0; i < 3; i++) await h.poll();
    check(S, "own Start is not 'someone else'", !h.dialogs.some((d) => REV_NOTICE_RE.test(d.msg)),
      h.dialogs.map((d) => d.msg));
  },

  // C-B: ревизия в ответе не выросла — не перечитываем.
  async start_same_rev() {
    const S = "start_same_rev";
    const h = await boot(async (m, u) => {
      if (isRev(u)) return revBody(2, false);
      if (isDetails(m, u)) return details([labor("L1", "Brakes")], 2);
      if (/timers\/start$/.test(u)) return timerBody(2, true);
      return OK;
    }, { search: "?mileage=50000" });
    await h.clickLabor(0, "[data-role='timer-btn']");
    check(S, "no refetch", h.detailGets() === 1, h.detailGets());
  },

  // Stop в нижней панели: без правок — перечитывание, с правками — правки целы.
  async bar_stop_clean() {
    const S = "bar_stop_clean";
    let phase = 0;
    const h = await boot(async (m, u) => {
      if (isRev(u)) return revBody(phase ? 4 : 2, false);
      if (isDetails(m, u)) return phase ? details([labor("L1", "Brakes"), labor("L2", "Check lights")], 4)
        : details([labor("L1", "Brakes", true)], 2);
      if (/timers\/stop$/.test(u)) return timerBody(4, false);
      return OK;
    }, { search: "?mileage=50000" });
    phase = 1;
    await h.clickBarStop();
    check(S, "refetch", h.detailGets() === 2 && h.rows().join() === "Brakes,Check lights", { gets: h.detailGets(), rows: h.rows() });
  },

  async bar_stop_dirty() {
    const S = "bar_stop_dirty";
    let phase = 0;
    const h = await boot(async (m, u) => {
      if (isRev(u)) return revBody(phase ? 4 : 2, false);
      if (isDetails(m, u)) return details([labor("L1", "Brakes", true)], 2);
      if (/timers\/stop$/.test(u)) return timerBody(4, false);
      return OK;
    }, { search: "?mileage=50000" });
    await h.typeInLabors();
    phase = 1;
    await h.clickBarStop();
    check(S, "no refetch", h.detailGets() === 1, h.detailGets());
    for (let i = 0; i < 2; i++) await h.poll();
    check(S, "own Stop is not 'someone else'", !h.dialogs.some((d) => REV_NOTICE_RE.test(d.msg)),
      h.dialogs.map((d) => d.msg));
  },

  // D6-03: менеджер на странице механика печатает пробег (поле в фокусе).
  async mileage_typing() {
    const S = "mileage_typing";
    let phase = 0;
    const h = await boot(async (m, u) => {
      if (isRev(u)) return { status: 200, body: { ok: true, labors_rev: phase ? 3 : 2, status: "in_progress", manager_confirmed: false } };
      if (isDetails(m, u)) return phase ? details([labor("L1", "Brakes"), labor("L2", "Check lights")], 3, { mileage_required: false })
        : details([labor("L1", "Brakes")], 2, { mileage_required: false });
      if (isUpdate(m, u)) {
        return { status: 200, body: { ok: true, status: "in_progress", labors: [labor("L1", "Brakes"), labor("L2", "Check lights")],
          labors_rev: 4, view_id: "V4", merged_foreign: false, merge_notes: [], warnings: [] } };
      }
      return OK;
    }, { mechanicMode: false });
    const f = h.els.mechWoMileage;
    const baseline = f.value; // значение на момент фокуса
    h.document.activeElement = f;
    f.value = "51234";
    phase = 1;
    await h.poll();
    check(S, "refresh keeps the focused field", f.value === "51234", f.value);
    check(S, "the refresh itself happened", h.rows().join() === "Brakes,Check lights", h.rows());
    // Клик по Save уводит фокус: change срабатывает, только если значение изменилось.
    h.document.activeElement = null;
    if (f.value !== baseline) for (const fn of f._listeners.change || []) fn({ target: f });
    await h.clickSave();
    const post = h.requests.find((r) => isUpdate(r.method, r.url));
    check(S, "Save sends the typed mileage", !!post && post.body.unit_mileage === "51234",
      post ? post.body.unit_mileage : "(no save)");
  },

  // Start на смете (контракт C-B): понятный текст и замок страницы.
  async start_on_estimate_with_message() {
    await startOnEstimate("start_on_estimate_with_message", "This work order is an estimate - time can't be tracked on it.");
  },
  async start_on_estimate_code_only() {
    await startOnEstimate("start_on_estimate_code_only", "");
  },

  // Правки на странице + замок: баннер, не окно; снятие — Save и подсказка.
  async lock_with_unsaved_edits() {
    const S = "lock_with_unsaved_edits";
    let phase = 0;
    const h = await boot(async (m, u) => {
      if (isRev(u)) return phase === 1 ? revBody(2, true) : revBody(phase === 2 ? 3 : 2, false);
      if (isDetails(m, u)) return details([labor("L1", "Brakes")], 2);
      return OK;
    }, { search: "?mileage=50000" });
    await h.typeInLabors();
    phase = 1;
    await h.poll(); await h.poll();
    check(S, "lock: notice about unsaved changes, Save disabled, no dialog, no refetch",
      h.lockNotice().visible && /unsaved changes/.test(h.lockNotice().text) && h.saveDisabled() &&
        h.blocking().length === 0 && h.detailGets() === 1,
      { notice: h.lockNotice(), dialogs: h.dialogs.map((d) => d.msg), gets: h.detailGets() });
    phase = 2;
    await h.poll();
    check(S, "unlock: notice hidden, Save enabled, one 'someone else' notice, edits kept",
      !h.lockNotice().visible && !h.saveDisabled() && h.detailGets() === 1 &&
        h.dialogs.filter((d) => REV_NOTICE_RE.test(d.msg)).length === 1,
      { notice: h.lockNotice(), saveDisabled: h.saveDisabled(), dialogs: h.dialogs.map((d) => d.msg) });
  },

  // Менеджер, страница без правок: WO оплатили — перечитывание и баннер, без окна.
  async paid_on_refresh_manager() {
    const S = "paid_on_refresh_manager";
    let phase = 0;
    const h = await boot(async (m, u) => {
      if (isRev(u)) return { status: 200, body: { ok: true, labors_rev: 2, status: phase ? "paid" : "in_progress", manager_confirmed: false } };
      if (isDetails(m, u)) return details([labor("L1", "Brakes")], 2, { mileage_required: false, status: phase ? "paid" : "in_progress" });
      return OK;
    }, { mechanicMode: false });
    phase = 1;
    await h.poll(); await h.poll();
    check(S, "notice, Save disabled, no dialog, one refetch",
      h.blocking().length === 0 && h.lockNotice().visible && h.saveDisabled() && h.detailGets() === 2,
      { dialogs: h.dialogs.map((d) => d.msg), notice: h.lockNotice(), gets: h.detailGets() });
  },

  // Открыт оплаченный WO: одно предупреждение при открытии (как раньше).
  async paid_at_open() {
    const S = "paid_at_open";
    const h = await boot(async (m, u) => {
      if (isRev(u)) return revBody(2, true);
      if (isDetails(m, u)) return details([labor("L1", "Brakes")], 2, { status: "paid" });
      return OK;
    }, { search: "?mileage=50000" });
    for (let i = 0; i < 3; i++) await h.poll();
    check(S, "one warning, Save disabled, no refetch",
      h.blocking().length === 1 && /Paid work orders cannot be edited/.test(h.blocking()[0].msg) &&
        h.saveDisabled() && h.detailGets() === 1,
      { dialogs: h.dialogs.map((d) => d.msg), gets: h.detailGets() });
  },

  // Открыт оплаченным, правки, Unpaid: Save вернулся, пробег снова обязателен.
  async unpaid_while_dirty() {
    const S = "unpaid_while_dirty";
    let phase = 0;
    const h = await boot(async (m, u) => {
      if (isRev(u)) return revBody(2, phase === 0);
      if (isDetails(m, u)) return details([labor("L1", "Brakes")], 2, { status: "paid" });
      if (isUpdate(m, u)) return { status: 200, body: { ok: true, status: "in_progress", labors: [labor("L1", "Brakes")], labors_rev: 3, view_id: "V3" } };
      return OK;
    });
    await h.typeInLabors();
    phase = 1;
    await h.poll();
    check(S, "Save enabled, notice hidden", !h.saveDisabled() && !h.lockNotice().visible,
      { saveDisabled: h.saveDisabled(), notice: h.lockNotice() });
    await h.clickSave();
    const posted = h.requests.some((r) => isUpdate(r.method, r.url));
    const gate = h.els.mechMileageModal;
    check(S, "Save asks for the mileage first", !posted && !!gate && gate.classList.contains("show"),
      { posted, gateOpen: !!(gate && gate.classList.contains("show")) });
  },

  // Save отклонён замком (подтвердили, пока страница открыта).
  async save_refused_by_lock() {
    const S = "save_refused_by_lock";
    const h = await boot(async (m, u) => {
      if (isRev(u)) return revBody(2, false);
      if (isDetails(m, u)) return details([labor("L1", "Brakes")], 2);
      if (isUpdate(m, u)) return CONFIRMED_403;
      return OK;
    }, { search: "?mileage=50000" });
    await h.typeInLabors();
    await h.clickSave();
    const last = h.dialogs.slice(-1)[0] || {};
    check(S, "message shown, notice + Save disabled",
      /confirmed by a manager/.test(last.msg || "") && h.lockNotice().visible && h.saveDisabled(),
      { last, notice: h.lockNotice(), saveDisabled: h.saveDisabled() });
  },
};

async function startOnEstimate(S, message) {
  const h = await boot(async (m, u) => {
    if (isRev(u)) return revBody(2, false);
    if (isDetails(m, u)) return details([labor("L1", "Brakes")], 2);
    if (/timers\/start$/.test(u)) {
      return { status: 400, body: Object.assign({ ok: false, error: "estimate_cannot_track" }, message ? { message } : {}) };
    }
    return OK;
  }, { search: "?mileage=50000" });
  await h.clickLabor(0, "[data-role='timer-btn']");
  const last = h.dialogs.slice(-1)[0] || {};
  check(S, "readable message", !!last.msg && /estimate/i.test(last.msg) && !/estimate_cannot_track/.test(last.msg) &&
    (!message || last.msg === message), last);
  check(S, "page locked: notice + Save disabled", h.lockNotice().visible && h.saveDisabled(),
    { notice: h.lockNotice(), saveDisabled: h.saveDisabled() });
}

(async () => {
  for (const name of Object.keys(SCENARIOS)) {
    try {
      await SCENARIOS[name]();
    } catch (e) {
      check(name, "scenario ran", false, String((e && e.stack) || e));
    }
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def _node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return node


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    """Все сценарии одним запуском node: {сценарий: {проверка: {ok, detail}}}."""
    node = _node()
    path = tmp_path_factory.mktemp("mechanic_wo_js") / "mechanic_wo_page.js"
    path.write_text(_NODE_SCRIPT, encoding="utf-8")
    proc = subprocess.run([node, str(path), JS_PATH], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _assert_scenarios(page, *names):
    failed = []
    for name in names:
        checks = page.get(name)
        assert checks, f"scenario {name} produced no checks"
        failed += [f"{name}: {check} -> {res['detail']!r}" for check, res in checks.items() if not res["ok"]]
    assert not failed, "\n".join(failed)


def test_locked_wo_is_not_refetched_and_shows_one_quiet_notice(page):
    """D6-01: подтверждённый / сметный WO механику — без GET деталей каждые
    20 с и окна ошибки на каждом тике; баннер с причиной, Save выключен;
    снятие замка перечитывает WO."""
    _assert_scenarios(page, "confirmed_at_open", "confirmed_while_open", "estimate_at_open")


def test_failing_refresh_backs_off_with_one_quiet_notice(page):
    """D6-01: перечитывание, которое не удаётся (500), повторяется с растущей
    паузой и показывает один тост, а не окно каждые 20 с."""
    _assert_scenarios(page, "failing_refresh")


def test_deleted_wo_is_reported_once(page):
    _assert_scenarios(page, "deleted_wo")


def test_own_start_or_stop_refetches_a_clean_page(page):
    """D6-02 / C-B: ответ своего Start/Stop с выросшей ревизией на странице
    без правок — перечитывание (чужая работа, добавленная до клика, видна)."""
    _assert_scenarios(page, "start_clean", "stop_clean", "bar_stop_clean")


def test_own_timer_write_on_a_dirty_page_only_learns_the_rev(page):
    """C-B: с несохранёнными правками ревизия своего таймера запоминается —
    без перечитывания и без «Someone else just updated…»."""
    _assert_scenarios(page, "start_dirty", "start_same_rev", "bar_stop_dirty")


def test_poll_does_not_overwrite_a_mileage_being_typed(page):
    """D6-03: перечитывание по опросу не затирает пробег в поле с фокусом,
    и Save отправляет введённое."""
    _assert_scenarios(page, "mileage_typing")


def test_start_on_estimate_shows_a_readable_message_and_locks(page):
    """C-B: 400 estimate_cannot_track — текст сервера (или понятный запасной),
    не машинный код; страница показывает замок."""
    _assert_scenarios(page, "start_on_estimate_with_message", "start_on_estimate_code_only")


def test_lock_notice_paths(page):
    """Замок при несохранённых правках и его снятие; оплата у менеджера при
    перечитывании (баннер без окна); оплаченный WO при открытии (окно, как
    раньше); Unpaid при правках (Save и обязательный пробег вернулись); Save,
    отклонённый замком."""
    _assert_scenarios(
        page, "lock_with_unsaved_edits", "paid_on_refresh_manager", "paid_at_open",
        "unpaid_while_dirty", "save_refused_by_lock",
    )


def test_mechanic_wo_js_syntax():
    node = _node()
    proc = subprocess.run([node, "--check", JS_PATH], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
