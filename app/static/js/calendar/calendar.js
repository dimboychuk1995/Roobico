/*
 * Calendar — appointments dispatch board (page: /calendar).
 *
 * Plain JS, no build step. Sections:
 *   1. utilities (dates, colours, http)
 *   2. state + data layer (/calendar/api/*)
 *   3. rendering (toolbar, legend, grid, blocks, status board)
 *   4. interactions (navigation, grid click, drag & drop, resize, live refresh)
 *   5. appointment modal
 *   6. settings modal
 *   7. boot
 *
 * All times are the shop's wall clock without a zone ("YYYY-MM-DDTHH:MM:SS"),
 * exactly as the API sends and expects them; inside the page an appointment
 * is {date: "YYYY-MM-DD", startMin, endMin} (minutes since midnight, end may
 * be 1440 = next-day midnight).
 *
 * Three views: "week" (a column per day), "day" (one day, a column per
 * mechanic) and "board" (one day, a column per stage — Scheduled / In
 * progress / Completed; dragging a card to another column changes its status).
 *
 * Live: the page asks /calendar/api/changes for a cheap change token every
 * few seconds and re-reads the appointments only when it differs, so edits
 * made by other people (or by work orders) show up without a reload.
 */
(function () {
  "use strict";

  const toolbar = document.getElementById("calToolbar");
  const gridEl = document.getElementById("calGrid");
  if (!toolbar || !gridEl) return;

  // Re-init safety: if the script runs twice on one document, drop the old timers/listeners.
  if (window.__roobicoCalendar && typeof window.__roobicoCalendar.destroy === "function") {
    window.__roobicoCalendar.destroy();
  }

  const SLOT = 15;
  const MIN_HOUR_H = 44;
  const LIVE_POLL_MS = 3000;      // change-token check while the tab is visible
  const LIVE_RETRY_MS = 15000;    // pace after repeated failures (offline, server restart)
  const LIVE_FAILS_BEFORE_SLOW = 3;
  const FULL_REFRESH_MS = 60000;  // safety net: labels and WO state do not move the token
  const DRAG_THRESHOLD = 4;
  const VIEWS = ["week", "day", "board"];
  // Board columns = the system statuses of the work flow; their labels and colours come from settings.
  const BOARD_COLUMNS = ["scheduled", "in_progress", "completed"];
  const UNASSIGNED = "unassigned";
  const LS_VIEW = "roobico.calendar.view";
  const LS_MECH = "roobico.calendar.mechanic";
  const MECH_PALETTE = [
    "#d81b60", "#e65100", "#00838f", "#6a1b9a", "#2e7d32", "#1565c0",
    "#8d6e63", "#c2185b", "#f9a825", "#00695c", "#5e35b1", "#ef6c00",
  ];
  const UNASSIGNED_COLOR = "#8a8f98";
  const DEFAULT_STATUSES = [
    { key: "scheduled", label: "Scheduled", color: "#1a73e8" },
    { key: "confirmed", label: "Confirmed", color: "#0d904f" },
    { key: "in_progress", label: "In Progress", color: "#e8710a" },
    { key: "completed", label: "Completed", color: "#5f6368" },
    { key: "cancelled", label: "Cancelled", color: "#d93025" },
  ];
  const SYSTEM_STATUS_KEYS = ["scheduled", "in_progress", "completed", "cancelled"];

  const can = {
    create: toolbar.dataset.canCreate === "1",
    edit: toolbar.dataset.canEdit === "1",
    delete: toolbar.dataset.canDelete === "1",
    manageSettings: toolbar.dataset.canManageSettings === "1",
    createWo: toolbar.dataset.canCreateWo === "1",
  };

  const $ = (id) => document.getElementById(id);
  const ui = {
    prev: $("calPrev"), today: $("calToday"), next: $("calNext"), date: $("calDate"), title: $("calTitle"),
    viewWeek: $("calViewWeek"), viewDay: $("calViewDay"), viewBoard: $("calViewBoard"), filter: $("calMechanicFilter"),
    settingsBtn: $("calSettingsBtn"), legend: $("calLegend"), mechLegend: $("calMechLegend"), live: $("calLive"),
  };

  // ── 1. Utilities ─────────────────────────────────────────────────────────

  const pad2 = (n) => (n < 10 ? "0" : "") + n;
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const snapDown = (min) => Math.floor(min / SLOT) * SLOT;
  const snapRound = (min) => Math.round(min / SLOT) * SLOT;

  function isValidIso(s) {
    return /^\d{4}-\d{2}-\d{2}$/.test(String(s || "")) && !Number.isNaN(dateFromIso(s).getTime());
  }
  function dateFromIso(iso) {
    const p = String(iso).split("-").map(Number);
    return new Date(p[0], p[1] - 1, p[2]);
  }
  function isoFromDate(d) {
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }
  function addDays(iso, n) {
    const d = dateFromIso(iso);
    d.setDate(d.getDate() + n);
    return isoFromDate(d);
  }
  function weekStartOf(iso) {
    const d = dateFromIso(iso);
    const dow = (d.getDay() + 6) % 7; // Monday = 0
    return addDays(iso, -dow);
  }
  function minutesOf(dt) {
    const m = /T(\d{2}):(\d{2})/.exec(String(dt || ""));
    return m ? Number(m[1]) * 60 + Number(m[2]) : 0;
  }
  function fmtMin(min) {
    return pad2(Math.floor(min / 60)) + ":" + pad2(min % 60);
  }
  function fmtRange(s, e) {
    return fmtMin(s) + "–" + fmtMin(e);
  }
  /* First line of a block: the end time sits in its own span so a very narrow block can drop it (CSS). */
  function timeHtml(s, e) {
    return fmtMin(s) + '<span class="cal-event-time-end">–' + fmtMin(e) + "</span>";
  }
  function toApiDatetime(date, min) {
    if (min >= 1440) return addDays(date, 1) + "T00:00:00";
    return date + "T" + fmtMin(min) + ":00";
  }
  function monthName(d) {
    return d.toLocaleDateString("en-US", { month: "long" });
  }
  function longDate(iso) {
    return dateFromIso(iso).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" });
  }
  function weekTitle(ws) {
    const a = dateFromIso(ws);
    const b = dateFromIso(addDays(ws, 6));
    const ma = monthName(a);
    const mb = monthName(b);
    if (a.getFullYear() !== b.getFullYear()) {
      return ma + " " + a.getDate() + ", " + a.getFullYear() + " – " + mb + " " + b.getDate() + ", " + b.getFullYear();
    }
    if (ma !== mb) return ma + " " + a.getDate() + " – " + mb + " " + b.getDate() + ", " + a.getFullYear();
    return ma + " " + a.getDate() + " – " + b.getDate() + ", " + a.getFullYear();
  }

  /* Current wall clock of the shop (body[data-app-timezone]); falls back to the browser clock. */
  function shopNow() {
    const now = new Date();
    const tz = document.body.getAttribute("data-app-timezone") || "";
    if (tz) {
      try {
        const parts = new Intl.DateTimeFormat("en-US", {
          timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
        }).formatToParts(now);
        const get = (t) => { const p = parts.find((x) => x.type === t); return p ? p.value : ""; };
        let hour = Number(get("hour"));
        if (hour === 24) hour = 0;
        const date = get("year") + "-" + get("month") + "-" + get("day");
        if (isValidIso(date)) return { date, minutes: hour * 60 + Number(get("minute")) };
      } catch (e) {
        // unknown zone name — browser clock below
      }
    }
    const fallback = toolbar.dataset.today;
    return { date: isValidIso(fallback) ? fallback : isoFromDate(now), minutes: now.getHours() * 60 + now.getMinutes() };
  }

  function hexToRgb(hex) {
    let h = String(hex || "").trim().replace(/^#/, "");
    if (h.length === 3) h = h.split("").map((c) => c + c).join("");
    if (!/^[0-9a-f]{6}$/i.test(h)) return null;
    const n = parseInt(h, 16);
    return [n >> 16, (n >> 8) & 255, n & 255];
  }
  /* Black or white text for a given background, by perceived brightness. */
  function textOn(bg) {
    const rgb = hexToRgb(bg);
    if (!rgb) return "#ffffff";
    const yiq = (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000;
    return yiq >= 150 ? "#1b1b1b" : "#ffffff";
  }
  function initialsOf(name) {
    const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "?";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  /* Date inputs may be enhanced by flatpickr (public.js): keep the picker in sync with the value. */
  function setDateInput(el, ymd) {
    el.value = ymd || "";
    if (el._flatpickr) el._flatpickr.setDate(ymd || null, false, "Y-m-d");
  }
  function setDateDisabled(el, flag) {
    el.disabled = flag;
    if (el._flatpickr && el._flatpickr.altInput) el._flatpickr.altInput.disabled = flag;
  }

  function lsGet(key) { try { return window.localStorage.getItem(key); } catch (e) { return null; } }
  function lsSet(key, value) { try { window.localStorage.setItem(key, value); } catch (e) { /* private mode */ } }

  function notify(message, type) {
    if (typeof window.appAlert === "function") return window.appAlert(message, type || "info");
    window.alert(message);
    return Promise.resolve();
  }
  function confirmDialog(message) {
    if (typeof window.appConfirm === "function") return window.appConfirm(message);
    return Promise.resolve(window.confirm(message));
  }

  /* JSON fetch; CSRF header is added by static/js/csrf.js. Rejects with Error(message) on !ok. */
  function api(method, url, body) {
    const opts = { method, credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then((res) => res.text().then((text) => {
      let data = null;
      if (text) { try { data = JSON.parse(text); } catch (e) { data = null; } }
      if (!res.ok) {
        const err = new Error((data && data.error) || ("Request failed (" + res.status + ")"));
        err.status = res.status;
        throw err;
      }
      return data;
    }));
  }

  // ── 2. State & data layer ────────────────────────────────────────────────

  const state = {
    view: "week",
    date: shopNow().date,
    filter: "",
    settings: { statuses: DEFAULT_STATUSES.slice(), start_hour: 6, end_hour: 20, slot_minutes: SLOT },
    mechanics: [],
    woStates: [],
    events: [],
    eventsSig: "",
    loadedWeek: "",
    loadError: "",
    customers: null,
    presets: null,
    unitsCache: {},
    columns: [],
    visStart: 0,
    visEnd: 1440,
    hourH: 48,
  };
  let drag = null;
  let boardDrag = null;
  let suppressClickUntil = 0;
  let nowTimer = null;
  let resizeTimer = null;
  // Live refresh: tokens last seen for the loaded week (null = not known yet).
  const live = {
    timer: null, busy: false, stopped: false, failures: 0,
    week: "", eventsToken: null, settingsToken: null, fullAt: 0, renderPending: false,
  };

  const statusByKey = (key) => state.settings.statuses.find((s) => s.key === key) || null;
  const statusColor = (key) => { const s = statusByKey(key); return s ? s.color : "#888888"; };
  const statusLabel = (key) => { const s = statusByKey(key); return s ? s.label : String(key || "").replace(/_/g, " "); };
  const mechanicById = (id) => state.mechanics.find((m) => m.id === id) || null;
  function mechColor(id) {
    const idx = state.mechanics.findIndex((m) => m.id === id);
    return idx >= 0 ? MECH_PALETTE[idx % MECH_PALETTE.length] : UNASSIGNED_COLOR;
  }
  const eventById = (id) => state.events.find((e) => e.id === id) || null;

  function normalizeEvent(raw) {
    const start = String(raw.start_time || "");
    const end = String(raw.end_time || "");
    const date = start.slice(0, 10);
    const startMin = minutesOf(start);
    let endMin = end.slice(0, 10) > date ? 1440 : minutesOf(end);
    if (endMin <= startMin) endMin = Math.min(1440, startMin + SLOT);
    let mechanics = (Array.isArray(raw.mechanics) ? raw.mechanics : [])
      .filter((m) => m && m.id)
      .map((m) => ({ id: String(m.id), name: String(m.name || "") }));
    if (!mechanics.length && raw.mechanic_id) {
      mechanics = [{ id: String(raw.mechanic_id), name: String(raw.mechanic_name || "") }];
    }
    return {
      id: String(raw.id || ""),
      date, startMin, endMin,
      title: String(raw.title || ""),
      status: String(raw.status || "scheduled"),
      customerId: String(raw.customer_id || ""),
      customerLabel: String(raw.customer_label || ""),
      unitId: String(raw.unit_id || ""),
      unitLabel: String(raw.unit_label || ""),
      mechanics,
      presets: (Array.isArray(raw.presets) ? raw.presets : [])
        .filter((p) => p && p.id)
        .map((p) => ({ id: String(p.id), name: String(p.name || "") })),
      workOrderId: String(raw.work_order_id || ""),
      woNumber: raw.wo_number == null ? null : raw.wo_number,
      woState: String(raw.wo_state || ""),
      laborSummary: Array.isArray(raw.labor_summary) ? raw.labor_summary.map(String) : [],
    };
  }

  function applySettings(data) {
    const s = data && typeof data === "object" ? data : {};
    const statuses = (Array.isArray(s.statuses) ? s.statuses : [])
      .filter((x) => x && x.key)
      .map((x) => ({ key: String(x.key), label: String(x.label || x.key), color: hexToRgb(x.color) ? String(x.color) : "#888888" }));
    state.settings = {
      statuses: statuses.length ? statuses : DEFAULT_STATUSES.slice(),
      start_hour: Number.isInteger(s.start_hour) ? clamp(s.start_hour, 0, 23) : 6,
      end_hour: Number.isInteger(s.end_hour) ? clamp(s.end_hour, 1, 24) : 20,
      slot_minutes: SLOT,
    };
    if (state.settings.end_hour <= state.settings.start_hour) {
      state.settings.start_hour = 6;
      state.settings.end_hour = 20;
    }
  }

  function loadSettings() {
    return api("GET", "/calendar/api/settings")
      .then(applySettings)
      .catch((err) => {
        console.error("calendar settings", err);
        applySettings(null);
        notify("Could not load calendar settings — using defaults.", "warning");
      });
  }

  function loadMechanics() {
    return api("GET", "/calendar/api/mechanics")
      .then((rows) => {
        state.mechanics = (Array.isArray(rows) ? rows : [])
          .filter((m) => m && m.id)
          .map((m) => ({ id: String(m.id), name: String(m.name || ""), role: String(m.role || "") }));
      })
      .catch((err) => {
        console.error("calendar mechanics", err);
        state.mechanics = [];
        notify("Could not load the mechanics list.", "error");
      });
  }

  function loadWoStates() {
    return api("GET", "/calendar/api/wo_states")
      .then((rows) => { state.woStates = Array.isArray(rows) ? rows : []; })
      .catch((err) => { console.error("calendar wo_states", err); state.woStates = []; });
  }

  /* Live refresh of the settings another user saved: silent, keeps the current ones on failure.
     Resolves true when new settings were applied. */
  function refreshSettings() {
    return api("GET", "/calendar/api/settings")
      .then((data) => { applySettings(data); return true; })
      .catch((err) => { console.error("calendar settings refresh", err); return false; });
  }

  /* Loads the week that contains state.date. silent = live refresh (no toasts, re-render only on change).
     Resolves true/false = the appointments changed or not, null = the request failed. */
  function loadEvents(silent) {
    const ws = weekStartOf(state.date);
    const url = "/calendar/api/events?start=" + encodeURIComponent(ws + "T00:00:00") +
      "&end=" + encodeURIComponent(addDays(ws, 7) + "T00:00:00");
    return api("GET", url).then((rows) => {
      const list = Array.isArray(rows) ? rows : [];
      const sig = JSON.stringify(list);
      const changed = sig !== state.eventsSig || state.loadedWeek !== ws;
      state.eventsSig = sig;
      state.loadedWeek = ws;
      state.loadError = "";
      if (changed) state.events = list.map(normalizeEvent).filter((e) => e.id && e.date);
      return changed;
    }).catch((err) => {
      console.error("calendar events", err);
      if (!silent) {
        state.loadError = err.message || "Could not load appointments";
        notify(state.loadError, "error");
      }
      return null;
    });
  }

  function upsertEvent(raw) {
    const ev = normalizeEvent(raw);
    const idx = state.events.findIndex((e) => e.id === ev.id);
    const inRange = state.loadedWeek && ev.date >= state.loadedWeek && ev.date < addDays(state.loadedWeek, 7);
    if (idx >= 0) {
      if (inRange) state.events[idx] = ev; else state.events.splice(idx, 1);
    } else if (inRange) {
      state.events.push(ev);
    }
    state.eventsSig = "";
    return ev;
  }

  function removeEvent(id) {
    state.events = state.events.filter((e) => e.id !== id);
    state.eventsSig = "";
  }

  const loadCustomers = () => state.customers
    ? Promise.resolve(state.customers)
    : api("GET", "/calendar/api/customers").then((rows) => { state.customers = Array.isArray(rows) ? rows : []; return state.customers; });
  const loadPresets = () => state.presets
    ? Promise.resolve(state.presets)
    : api("GET", "/calendar/api/presets").then((rows) => { state.presets = Array.isArray(rows) ? rows : []; return state.presets; });
  const loadUnits = (customerId) => {
    if (!customerId) return Promise.resolve([]);
    if (state.unitsCache[customerId]) return Promise.resolve(state.unitsCache[customerId]);
    return api("GET", "/calendar/api/units/" + encodeURIComponent(customerId))
      .then((rows) => { state.unitsCache[customerId] = Array.isArray(rows) ? rows : []; return state.unitsCache[customerId]; });
  };

  // ── 3. Rendering ─────────────────────────────────────────────────────────

  /* A mechanic who is no longer assignable (deactivated, lost the role) is missing from
     state.mechanics; an appointment whose whole crew is like that counts as unassigned, so
     the By-mechanic view and the Unassigned filter never lose sight of it. */
  const hasKnownMechanic = (ev) => ev.mechanics.some((m) => !!mechanicById(m.id));

  function passesFilter(ev) {
    if (!state.filter) return true;
    if (state.filter === UNASSIGNED) return !hasKnownMechanic(ev);
    return ev.mechanics.some((m) => m.id === state.filter);
  }

  /* Columns of the current view: week → 7 dates; day → mechanics (+ Unassigned) for state.date. */
  function buildColumns() {
    const today = shopNow().date;
    if (state.view === "week") {
      const ws = weekStartOf(state.date);
      return Array.from({ length: 7 }, (_, i) => {
        const date = addDays(ws, i);
        const d = dateFromIso(date);
        return {
          key: date, date, mechId: null, isToday: date === today,
          dow: d.toLocaleDateString("en-US", { weekday: "short" }), dom: d.getDate(),
          events: state.events.filter((e) => e.date === date && passesFilter(e)),
        };
      });
    }
    const dayEvents = state.events.filter((e) => e.date === state.date);
    const cols = state.mechanics
      .filter((m) => !state.filter || state.filter === m.id)
      .map((m) => ({
        key: "m:" + m.id, date: state.date, mechId: m.id, name: m.name, color: mechColor(m.id), isToday: state.date === today,
        events: dayEvents.filter((e) => e.mechanics.some((x) => x.id === m.id)),
      }));
    if (!state.filter || state.filter === UNASSIGNED) {
      cols.push({
        key: "m:", date: state.date, mechId: "", name: "Unassigned", color: UNASSIGNED_COLOR, isToday: state.date === today,
        events: dayEvents.filter((e) => !hasKnownMechanic(e)),
      });
    }
    return cols;
  }

  /* Hour range shown: working hours from settings, widened so every block of the view is visible. */
  function computeVisibleRange(cols) {
    let s = state.settings.start_hour * 60;
    let e = state.settings.end_hour * 60;
    cols.forEach((c) => c.events.forEach((ev) => {
      s = Math.min(s, Math.floor(ev.startMin / 60) * 60);
      e = Math.max(e, Math.ceil(ev.endMin / 60) * 60);
    }));
    s = clamp(s, 0, 1380);
    e = clamp(Math.max(e, s + 60), 60, 1440);
    return { start: s, end: e };
  }

  /* Overlapping blocks: cluster → columns → equal widths. Returns [{ev, col, n}]. */
  function layoutOverlaps(events) {
    const sorted = events.slice().sort((a, b) => a.startMin - b.startMin || (b.endMin - b.startMin) - (a.endMin - a.startMin));
    const out = [];
    let cluster = null;
    let clusterEnd = -1;
    const flush = () => {
      if (!cluster) return;
      const n = cluster.cols.length;
      cluster.items.forEach((it) => out.push({ ev: it.ev, col: it.col, n }));
    };
    sorted.forEach((ev) => {
      if (!cluster || ev.startMin >= clusterEnd) {
        flush();
        cluster = { items: [], cols: [] };
        clusterEnd = ev.endMin;
      } else {
        clusterEnd = Math.max(clusterEnd, ev.endMin);
      }
      let col = cluster.cols.findIndex((end) => end <= ev.startMin);
      if (col < 0) { col = cluster.cols.length; cluster.cols.push(ev.endMin); } else { cluster.cols[col] = ev.endMin; }
      cluster.items.push({ ev, col });
    });
    flush();
    return out;
  }

  const yOf = (min) => ((min - state.visStart) / 60) * state.hourH;

  function eventTooltip(ev) {
    const lines = [fmtRange(ev.startMin, ev.endMin) + " · " + statusLabel(ev.status)];
    if (ev.title) lines.push(ev.title);
    if (ev.customerLabel) lines.push("Customer: " + ev.customerLabel);
    if (ev.unitLabel) lines.push("Unit: " + ev.unitLabel);
    lines.push(ev.mechanics.length
      ? "Mechanics: " + ev.mechanics.map((m) => m.name + (mechanicById(m.id) ? "" : " (former mechanic)")).join(", ")
      : "No mechanic assigned");
    if (ev.presets.length) lines.push("Presets: " + ev.presets.map((p) => p.name).join(", "));
    if (ev.workOrderId) lines.push("WO #" + (ev.woNumber || "?") + (ev.woState ? " · " + woStateLabel(ev.woState) : ""));
    return lines.join("\n");
  }

  function woStateInfo(key) {
    return state.woStates.find((s) => s && s.key === key) || null;
  }
  function woStateLabel(key) {
    const s = woStateInfo(key);
    return s ? s.label : String(key || "").replace(/_/g, " ");
  }

  function mechChipHtml(mech, large) {
    const color = mechColor(mech.id);
    return '<span class="cal-mech-chip' + (large ? " is-lg" : "") + '" style="background:' + esc(color) + ";color:" + esc(textOn(color)) +
      '" title="' + esc(mech.name) + '">' + esc(initialsOf(mech.name)) + "</span>";
  }

  function buildEventEl(place, col) {
    const ev = place.ev;
    const bg = statusColor(ev.status);
    const el = document.createElement("div");
    const cancelled = ev.status === "cancelled";
    const showChips = state.view === "week" && ev.mechanics.length > 0;
    el.className = "cal-event" + (cancelled ? " is-cancelled" : "") + (showChips ? " has-mechs" : "");
    el.dataset.id = ev.id;
    el.dataset.colKey = col.key;
    el.style.top = yOf(ev.startMin) + "px";
    el.style.height = Math.max(18, ((ev.endMin - ev.startMin) / 60) * state.hourH - 2) + "px";
    el.style.left = "calc(" + ((place.col / place.n) * 100) + "% + 1px)";
    el.style.width = "calc(" + (100 / place.n) + "% - 3px)";
    el.style.background = bg;
    el.style.color = textOn(bg);
    el.title = eventTooltip(ev);

    const title = ev.title || ev.customerLabel || "(no title)";
    // First row: the time (shrinks with an ellipsis) and, in week view, the crew chips beside it.
    // Narrow blocks drop chips, then the end time (@container rules in calendar.css); the tooltip has it all.
    let html = '<div class="cal-event-head"><div class="cal-event-time">' + timeHtml(ev.startMin, ev.endMin) + "</div>";
    if (showChips) html += '<div class="cal-event-mechs">' + ev.mechanics.map((m) => mechChipHtml(m, false)).join("") + "</div>";
    html += "</div>";
    html += '<div class="cal-event-title">' + esc(title) + "</div>";
    if (ev.customerLabel && ev.customerLabel !== title) html += '<div class="cal-event-sub">' + esc(ev.customerLabel) + "</div>";
    if (ev.unitLabel) html += '<div class="cal-event-sub">' + esc(ev.unitLabel) + "</div>";
    if (state.view === "day" && !col.mechId && ev.mechanics.length) {
      // Unassigned column, day view: the stored crew is nobody we can assign to any more — say who it was.
      html += '<div class="cal-event-sub cal-event-former">Former mechanic: ' + esc(ev.mechanics.map((m) => m.name).join(", ")) + "</div>";
    }
    if (ev.workOrderId) html += '<span class="cal-event-wo">WO #' + esc(ev.woNumber || "?") + "</span>";
    if (can.edit) html += '<div class="cal-event-resize" title="Drag to change the end time"></div>';
    el.innerHTML = html;
    return el;
  }

  function buildHeadCell(col) {
    const head = document.createElement("div");
    head.className = "cal-col-head" + (col.isToday ? " is-today" : "");
    head.dataset.colKey = col.key;
    if (state.view === "week") {
      head.classList.add("is-clickable");
      head.title = "Open " + longDate(col.date) + " by mechanic";
      head.innerHTML = '<div class="cal-dow">' + esc(col.dow) + '</div><div class="cal-dom">' + col.dom + "</div>";
    } else {
      const n = col.events.filter((e) => e.status !== "cancelled").length;
      head.innerHTML =
        '<div class="cal-mech-head' + (col.mechId ? "" : " is-unassigned") + '">' +
          (col.mechId
            ? mechChipHtml({ id: col.mechId, name: col.name }, true)
            : '<span class="cal-legend-dot" style="background:' + UNASSIGNED_COLOR + '"></span>') +
          '<span class="cal-mech-head-name" title="' + esc(col.name) + '">' + esc(col.name) + "</span>" +
        "</div>" +
        '<div class="cal-mech-count">' + n + (n === 1 ? " appointment" : " appointments") + "</div>";
    }
    return head;
  }

  function gridHeight() {
    const top = gridEl.getBoundingClientRect().top;
    return Math.max(320, Math.floor(window.innerHeight - top - 16));
  }

  function render() {
    gridEl.classList.toggle("is-board", state.view === "board");
    gridEl.classList.toggle("can-edit", can.edit);
    if (state.view === "board") { renderBoard(); return; }
    const cols = buildColumns();
    state.columns = cols;
    const vr = computeVisibleRange(cols);
    state.visStart = vr.start;
    state.visEnd = vr.end;
    const hours = (vr.end - vr.start) / 60;

    const oldScroll = gridEl.querySelector(".cal-scroll");
    const keepTop = oldScroll ? oldScroll.scrollTop : 0;
    const keepLeft = oldScroll ? oldScroll.scrollLeft : 0;

    gridEl.style.height = gridHeight() + "px";
    gridEl.classList.toggle("can-create", can.create);

    const scroll = document.createElement("div");
    scroll.className = "cal-scroll";
    const sheet = document.createElement("div");
    sheet.className = "cal-sheet";
    sheet.style.gridTemplateColumns = "var(--cal-gutter-w) repeat(" + cols.length + ", minmax(var(--cal-col-min), 1fr))";
    const gutterHead = document.createElement("div");
    gutterHead.className = "cal-gutter-head";
    sheet.appendChild(gutterHead);
    cols.forEach((col) => sheet.appendChild(buildHeadCell(col)));
    scroll.appendChild(sheet);
    gridEl.replaceChildren(scroll);

    const headH = gutterHead.nextElementSibling ? gutterHead.nextElementSibling.offsetHeight : 48;
    state.hourH = Math.max(MIN_HOUR_H, Math.floor((gridEl.clientHeight - headH - 2) / hours));
    gridEl.style.setProperty("--cal-hour-h", state.hourH + "px");
    gridEl.style.setProperty("--cal-hours", String(hours));

    const gutter = document.createElement("div");
    gutter.className = "cal-gutter";
    for (let m = vr.start; m < vr.end; m += 60) {
      const lbl = document.createElement("div");
      lbl.className = "cal-gutter-label";
      lbl.style.top = yOf(m) + "px";
      lbl.textContent = fmtMin(m);
      gutter.appendChild(lbl);
    }
    sheet.appendChild(gutter);

    const now = shopNow();
    cols.forEach((col) => {
      const colEl = document.createElement("div");
      colEl.className = "cal-col" + (col.isToday ? " is-today" : "");
      colEl.dataset.colKey = col.key;
      layoutOverlaps(col.events).forEach((place) => colEl.appendChild(buildEventEl(place, col)));
      if (col.isToday && now.minutes >= vr.start && now.minutes <= vr.end) {
        const line = document.createElement("div");
        line.className = "cal-now";
        line.style.top = yOf(now.minutes) + "px";
        colEl.appendChild(line);
      }
      sheet.appendChild(colEl);
    });

    const total = cols.reduce((n, c) => n + c.events.length, 0);
    if (state.loadError) {
      showGridMessage(state.loadError);
    } else if (!total && state.loadedWeek) {
      showGridMessage(state.view === "week" ? "No appointments this week" : "No appointments on this day" +
        (can.create ? " — click a time slot to add one" : ""));
    }

    scroll.scrollTop = keepTop;
    scroll.scrollLeft = keepLeft;
    updateToolbar();
  }

  function showGridMessage(text) {
    const msg = document.createElement("div");
    msg.className = "cal-grid-msg small";
    msg.textContent = text;
    gridEl.appendChild(msg);
  }

  // Status board: one day, a column per stage of the work.

  /* Column of an appointment on the board. In Progress and Completed have their own column; every
     other live status (Scheduled, Confirmed, the shop's own ones) is still ahead and waits in the
     first one; a Cancelled appointment is not on the board at all. */
  function boardColumnOf(ev) {
    if (ev.status === "cancelled") return "";
    return ev.status === "in_progress" || ev.status === "completed" ? ev.status : "scheduled";
  }

  function buildCardEl(ev, colKey) {
    const el = document.createElement("div");
    el.className = "cal-card";
    el.dataset.id = ev.id;
    el.style.setProperty("--cal-card-accent", statusColor(ev.status));
    el.title = eventTooltip(ev);

    const title = ev.title || ev.customerLabel || "(no title)";
    let html = '<div class="cal-card-head"><span class="cal-card-time">' + fmtRange(ev.startMin, ev.endMin) + "</span>";
    if (ev.status !== colKey) {
      // Confirmed / a custom status sharing the first column: say which one it is.
      const color = statusColor(ev.status);
      html += '<span class="badge" style="background:' + esc(color) + ";color:" + esc(textOn(color)) + '">' + esc(statusLabel(ev.status)) + "</span>";
    }
    if (ev.workOrderId) html += '<span class="badge text-bg-secondary cal-card-wo">WO #' + esc(ev.woNumber || "?") + "</span>";
    html += "</div>";
    html += '<div class="cal-card-title">' + esc(title) + "</div>";
    if (ev.customerLabel && ev.customerLabel !== title) html += '<div class="cal-card-sub">' + esc(ev.customerLabel) + "</div>";
    if (ev.unitLabel) html += '<div class="cal-card-sub">' + esc(ev.unitLabel) + "</div>";
    html += '<div class="cal-card-mechs">';
    if (ev.mechanics.length) {
      html += ev.mechanics.map((m) => mechChipHtml(m, false)).join("") +
        '<span class="cal-card-mech-names">' + esc(ev.mechanics.map((m) => m.name).join(", ")) + "</span>";
    } else {
      html += '<span class="cal-card-mech-names is-unassigned">Unassigned</span>';
    }
    html += "</div>";
    el.innerHTML = html;
    return el;
  }

  function renderBoard() {
    state.columns = [];
    const dayEvents = state.events
      .filter((e) => e.date === state.date && passesFilter(e))
      .sort((a, b) => a.startMin - b.startMin || a.endMin - b.endMin || (a.id < b.id ? -1 : 1));

    // Keep each column where the user scrolled it (the board is rebuilt on every live update).
    const keepTop = {};
    gridEl.querySelectorAll(".cal-board-col").forEach((col) => {
      const list = col.querySelector(".cal-board-list");
      if (list) keepTop[col.dataset.status] = list.scrollTop;
    });
    const oldBoard = gridEl.querySelector(".cal-board");
    const keepLeft = oldBoard ? oldBoard.scrollLeft : 0;

    gridEl.style.height = gridHeight() + "px";
    gridEl.classList.remove("can-create");

    const board = document.createElement("div");
    board.className = "cal-board";
    const lists = {};
    BOARD_COLUMNS.forEach((key) => {
      const items = dayEvents.filter((e) => boardColumnOf(e) === key);
      const label = statusLabel(key);
      const col = document.createElement("section");
      col.className = "cal-board-col";
      col.dataset.status = key;
      col.style.setProperty("--cal-board-accent", statusColor(key));

      const head = document.createElement("header");
      head.className = "cal-board-head";
      head.innerHTML =
        '<span class="cal-legend-dot" style="background:' + esc(statusColor(key)) + '"></span>' +
        '<span class="cal-board-title" title="' + esc(label) + '">' + esc(label) + "</span>" +
        '<span class="cal-board-count" title="Appointments in this column">' + items.length + "</span>" +
        (can.create
          ? '<button type="button" class="btn btn-outline-secondary btn-sm cal-board-add" data-status="' + esc(key) +
            '" title="New appointment: ' + esc(label) + '" aria-label="New appointment: ' + esc(label) + '">' +
            '<i class="bi bi-plus-lg" aria-hidden="true"></i></button>'
          : "");

      const list = document.createElement("div");
      list.className = "cal-board-list";
      items.forEach((ev) => list.appendChild(buildCardEl(ev, key)));
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "cal-board-empty";
        empty.textContent = state.loadedWeek ? "No appointments" + (can.edit ? " — drag one here" : "") : "Loading…";
        list.appendChild(empty);
      }
      lists[key] = list;
      col.appendChild(head);
      col.appendChild(list);
      board.appendChild(col);
    });

    const cancelled = dayEvents.filter((e) => e.status === "cancelled").length;
    if (cancelled) {
      const note = document.createElement("div");
      note.className = "cal-board-note";
      note.textContent = cancelled + (cancelled === 1 ? " cancelled appointment is" : " cancelled appointments are") +
        " not on the board — see By mechanic or Week.";
      board.appendChild(note);
    }

    gridEl.replaceChildren(board);
    BOARD_COLUMNS.forEach((key) => { if (keepTop[key]) lists[key].scrollTop = keepTop[key]; });
    board.scrollLeft = keepLeft;
    if (state.loadError) showGridMessage(state.loadError);
    updateToolbar();
  }

  function updateNowLine() {
    const now = shopNow();
    const todayCols = state.columns.filter((c) => c.isToday);
    if (todayCols.length && todayCols[0].date !== now.date) { render(); return; }
    gridEl.querySelectorAll(".cal-col.is-today").forEach((colEl) => {
      let line = colEl.querySelector(".cal-now");
      const inRange = now.minutes >= state.visStart && now.minutes <= state.visEnd;
      if (!inRange) { if (line) line.remove(); return; }
      if (!line) { line = document.createElement("div"); line.className = "cal-now"; colEl.appendChild(line); }
      line.style.top = yOf(now.minutes) + "px";
    });
  }

  function updateToolbar() {
    ui.title.textContent = state.view === "week" ? weekTitle(weekStartOf(state.date)) : longDate(state.date);
    if (ui.date.value !== state.date) setDateInput(ui.date, state.date);
    [["week", ui.viewWeek], ["day", ui.viewDay], ["board", ui.viewBoard]].forEach((pair) => {
      const btn = pair[1];
      if (!btn) return;
      btn.classList.toggle("active", state.view === pair[0]);
      btn.setAttribute("aria-pressed", String(state.view === pair[0]));
    });
    if (ui.filter.value !== state.filter) ui.filter.value = state.filter;
    ui.mechLegend.querySelectorAll(".cal-mech-legend-btn").forEach((b) => {
      b.classList.toggle("is-active", b.dataset.mech === state.filter && state.filter !== "");
    });
  }

  function renderFilterOptions() {
    const opts = ['<option value="">All mechanics</option>'];
    state.mechanics.forEach((m) => opts.push('<option value="' + esc(m.id) + '">' + esc(m.name) + "</option>"));
    opts.push('<option value="' + UNASSIGNED + '">Unassigned</option>');
    ui.filter.innerHTML = opts.join("");
    if (state.filter && state.filter !== UNASSIGNED && !mechanicById(state.filter)) state.filter = "";
    ui.filter.value = state.filter;
  }

  function renderLegend() {
    ui.legend.innerHTML = state.settings.statuses.map((s) =>
      '<span class="cal-legend-item"><span class="cal-legend-dot" style="background:' + esc(s.color) + '"></span>' + esc(s.label) + "</span>"
    ).join("");
    ui.mechLegend.innerHTML = state.mechanics.map((m) =>
      '<button type="button" class="cal-mech-legend-btn" data-mech="' + esc(m.id) + '" title="Show only ' + esc(m.name) + '">' +
        mechChipHtml(m, false) + "<span>" + esc(m.name) + "</span></button>"
    ).join("");
  }

  // ── 4. Interactions ──────────────────────────────────────────────────────

  function syncUrl() {
    const params = new URLSearchParams();
    params.set("date", state.date);
    params.set("view", state.view);
    try { window.history.replaceState(null, "", window.location.pathname + "?" + params.toString()); } catch (e) { /* ignore */ }
  }

  /* Go to a date: refetch when the week changes, otherwise just redraw. */
  function navigate(dateIso) {
    if (!isValidIso(dateIso)) return;
    state.date = dateIso;
    updateToolbar();
    syncUrl();
    if (weekStartOf(dateIso) !== state.loadedWeek) {
      loadEvents(false).then(render);
    } else {
      render();
    }
  }

  function setView(view) {
    state.view = VIEWS.indexOf(view) >= 0 ? view : "week";
    lsSet(LS_VIEW, state.view);
    syncUrl();
    render();
  }

  function setFilter(value) {
    state.filter = value === UNASSIGNED || mechanicById(value) ? value : "";
    lsSet(LS_MECH, state.filter);
    render();
  }

  function columnByKey(key) {
    return state.columns.find((c) => c.key === key) || null;
  }

  /* "+" of a board column: a new appointment for the shown day, already in that column's status. */
  function openCreateFromBoard(status) {
    if (!can.create) return;
    // No slot was clicked, so no time is known: the dialog opens with the start time unselected
    // (needsTime) and the hour buttons on :00 — the user presses the one they mean.
    const first = state.settings.start_hour * 60;
    const start = clamp(8 * 60, first, Math.max(first, state.settings.end_hour * 60 - 60));
    const mech = state.filter && state.filter !== UNASSIGNED ? mechanicById(state.filter) : null;
    openCreateModal({
      date: state.date, startMin: start, endMin: Math.min(1440, start + 60), mechanics: mech ? [mech] : [], status, needsTime: true,
    });
  }

  /* Click on an empty slot → new appointment; on a block or a board card → edit; on a week header → day view. */
  function onGridClick(e) {
    if (Date.now() < suppressClickUntil) return;
    const addBtn = e.target.closest(".cal-board-add");
    if (addBtn) { openCreateFromBoard(addBtn.dataset.status); return; }
    const card = e.target.closest(".cal-card");
    if (card) {
      const cardEv = eventById(card.dataset.id);
      if (cardEv) openEditModal(cardEv);
      return;
    }
    const head = e.target.closest(".cal-col-head.is-clickable");
    if (head) {
      const col = columnByKey(head.dataset.colKey);
      if (col) { state.date = col.date; setView("day"); }
      return;
    }
    const evEl = e.target.closest(".cal-event");
    if (evEl) {
      const ev = eventById(evEl.dataset.id);
      if (ev) openEditModal(ev);
      return;
    }
    const colEl = e.target.closest(".cal-col");
    if (!colEl || !can.create) return;
    const col = columnByKey(colEl.dataset.colKey);
    if (!col) return;
    const y = e.clientY - colEl.getBoundingClientRect().top;
    const start = clamp(snapDown(state.visStart + (y / state.hourH) * 60), 0, 1440 - SLOT);
    const mechanics = col.mechId ? [mechanicById(col.mechId)].filter(Boolean) : [];
    openCreateModal({ date: col.date, startMin: start, endMin: Math.min(1440, start + 60), mechanics });
  }

  /* Mechanics after dropping a block into another mechanic column of the day view. */
  function mechanicsAfterMove(ev, fromMechId, toMechId) {
    const ids = ev.mechanics.map((m) => m.id);
    if (!toMechId) return [];
    if (!fromMechId || ids.length <= 1) return [toMechId];
    const out = ids.filter((id) => id !== fromMechId);
    if (out.indexOf(toMechId) < 0) out.push(toMechId);
    return out;
  }

  // Board drag & drop: a card dragged into another column takes that column's status.

  function boardPointerDown(e) {
    if (boardDrag) releaseBoardDrag();
    const card = e.target.closest(".cal-card");
    const colEl = card ? card.closest(".cal-board-col") : null;
    const ev = card ? eventById(card.dataset.id) : null;
    if (!ev || !colEl) return;
    const rect = card.getBoundingClientRect();
    boardDrag = {
      ev, el: card, pointerId: e.pointerId, active: false, ghost: null,
      startX: e.clientX, startY: e.clientY,
      offsetX: e.clientX - rect.left, offsetY: e.clientY - rect.top, width: rect.width,
      fromKey: colEl.dataset.status, toKey: colEl.dataset.status,
    };
  }

  function boardPointerMove(e) {
    const d = boardDrag;
    if (!d.active) {
      if (Math.abs(e.clientX - d.startX) < DRAG_THRESHOLD && Math.abs(e.clientY - d.startY) < DRAG_THRESHOLD) return;
      d.active = true;
      try { gridEl.setPointerCapture(e.pointerId); } catch (err) { /* capture unsupported */ }
      // The card itself stays in its column (dimmed); a copy follows the pointer.
      d.ghost = d.el.cloneNode(true);
      d.ghost.classList.add("is-ghost");
      d.ghost.style.width = d.width + "px";
      gridEl.appendChild(d.ghost);
      d.el.classList.add("is-drag-source");
      gridEl.classList.add("is-dragging");
    }
    d.ghost.style.left = (e.clientX - d.offsetX) + "px";
    d.ghost.style.top = (e.clientY - d.offsetY) + "px";
    let target = "";
    gridEl.querySelectorAll(".cal-board-col").forEach((col) => {
      const r = col.getBoundingClientRect();
      const inside = e.clientX >= r.left && e.clientX < r.right && e.clientY >= r.top && e.clientY < r.bottom;
      if (inside) target = col.dataset.status;
      col.classList.toggle("is-drop-target", inside && col.dataset.status !== d.fromKey);
    });
    // Outside every column = "release to cancel", like leaving the grid in the other views.
    d.toKey = target;
    d.ghost.classList.toggle("is-outside", !target);
  }

  function releaseBoardDrag() {
    const d = boardDrag;
    boardDrag = null;
    if (!d) return null;
    try { gridEl.releasePointerCapture(d.pointerId); } catch (err) { /* already released */ }
    if (d.ghost) d.ghost.remove();
    d.el.classList.remove("is-drag-source");
    gridEl.classList.remove("is-dragging");
    gridEl.querySelectorAll(".cal-board-col.is-drop-target").forEach((col) => col.classList.remove("is-drop-target"));
    return d;
  }

  function boardPointerUp() {
    const d = releaseBoardDrag();
    if (!d || !d.active) return; // a plain click → handled by onGridClick
    suppressClickUntil = Date.now() + 400;
    if (!d.toKey || d.toKey === d.fromKey) return;
    setStatusFromBoard(d.ev, d.toKey);
  }

  /* The card jumps to its new column at once; the server answer (or its refusal) follows. */
  function setStatusFromBoard(ev, status) {
    const before = ev.status;
    ev.status = status;
    render();
    api("PUT", "/calendar/api/events/" + encodeURIComponent(ev.id), { status })
      .then((saved) => { upsertEvent(saved); render(); })
      .catch((err) => {
        ev.status = before;
        notify(err.message || "Could not change the status.", "error");
        loadEvents(false).then(render);
      });
  }

  function onPointerDown(e) {
    if (e.button !== 0 || !can.edit) return;
    if (state.view === "board") { boardPointerDown(e); return; }
    if (drag) { const stale = releaseDrag(); if (stale && stale.active) render(); }
    const evEl = e.target.closest(".cal-event");
    if (!evEl) return;
    const ev = eventById(evEl.dataset.id);
    const colEl = evEl.closest(".cal-col");
    if (!ev || !colEl) return;
    const fromCol = columnByKey(colEl.dataset.colKey);
    if (!fromCol) return;
    const colRect = colEl.getBoundingClientRect();
    const pointerMin = state.visStart + ((e.clientY - colRect.top) / state.hourH) * 60;
    drag = {
      ev, el: evEl, pointerId: e.pointerId,
      mode: e.target.closest(".cal-event-resize") ? "resize" : "move",
      startX: e.clientX, startY: e.clientY, active: false,
      grabOffset: pointerMin - ev.startMin,
      fromCol, curColKey: fromCol.key,
      startMin: ev.startMin, endMin: ev.endMin,
      colRects: Array.from(gridEl.querySelectorAll(".cal-col")).map((c) => ({ el: c, key: c.dataset.colKey, rect: c.getBoundingClientRect() })),
      gridRect: gridEl.getBoundingClientRect(), inside: true,
    };
  }

  function onPointerMove(e) {
    if (boardDrag) { boardPointerMove(e); return; }
    if (!drag) return;
    if (!drag.active) {
      if (Math.abs(e.clientX - drag.startX) < DRAG_THRESHOLD && Math.abs(e.clientY - drag.startY) < DRAG_THRESHOLD) return;
      drag.active = true;
      // Capture only once a real drag starts (a plain click must keep its target),
      // and on the grid rather than the block: re-parenting the block into another
      // column would silently release a capture held by the block itself.
      try { gridEl.setPointerCapture(e.pointerId); } catch (err) { /* capture unsupported */ }
      drag.el.classList.add(drag.mode === "move" ? "is-dragging" : "is-resizing");
      gridEl.classList.add(drag.mode === "move" ? "is-dragging" : "is-resizing");
      if (drag.mode === "move") { drag.el.style.left = "1px"; drag.el.style.width = "calc(100% - 3px)"; }
    }
    // Outside the grid = "release to cancel": the block dims and dropping there changes nothing
    // (the position below is only clamped so the block stays visible while the pointer is away).
    const g = drag.gridRect;
    drag.inside = e.clientX >= g.left && e.clientX < g.right && e.clientY >= g.top && e.clientY < g.bottom;
    drag.el.classList.toggle("is-outside", !drag.inside);
    const limit = Math.min(1440, state.visEnd);
    if (drag.mode === "move") {
      const target = drag.colRects.find((c) => e.clientX >= c.rect.left && e.clientX < c.rect.right) ||
        drag.colRects.find((c) => c.key === drag.curColKey);
      if (!target) return;
      const dur = drag.ev.endMin - drag.ev.startMin;
      const pointerMin = state.visStart + ((e.clientY - target.rect.top) / state.hourH) * 60;
      const start = clamp(snapRound(pointerMin - drag.grabOffset), state.visStart, Math.max(state.visStart, limit - dur));
      if (target.key !== drag.curColKey) { target.el.appendChild(drag.el); drag.curColKey = target.key; }
      drag.startMin = start;
      drag.endMin = start + dur;
      drag.el.style.top = yOf(start) + "px";
    } else {
      const col = drag.colRects.find((c) => c.key === drag.fromCol.key);
      if (!col) return;
      const pointerMin = state.visStart + ((e.clientY - col.rect.top) / state.hourH) * 60;
      drag.endMin = clamp(snapRound(pointerMin), drag.ev.startMin + SLOT, limit);
      drag.el.style.height = Math.max(18, ((drag.endMin - drag.ev.startMin) / 60) * state.hourH - 2) + "px";
    }
    const timeEl = drag.el.querySelector(".cal-event-time");
    if (timeEl) timeEl.innerHTML = timeHtml(drag.startMin, drag.endMin);
  }

  function releaseDrag() {
    const d = drag;
    drag = null;
    if (!d) return null;
    try { gridEl.releasePointerCapture(d.pointerId); } catch (err) { /* already released */ }
    gridEl.classList.remove("is-dragging", "is-resizing");
    return d;
  }

  function onPointerCancel() {
    if (boardDrag) { releaseBoardDrag(); return; }
    const d = releaseDrag();
    if (d && d.active) render();
  }

  function onPointerUp() {
    if (boardDrag) { boardPointerUp(); return; }
    const d = releaseDrag();
    if (!d || !d.active) return; // a plain click → handled by onGridClick
    suppressClickUntil = Date.now() + 400;
    if (!d.inside) { render(); return; } // released outside the grid → cancelled, like Esc
    const toCol = columnByKey(d.curColKey) || d.fromCol;
    const payload = {};
    let changed = d.startMin !== d.ev.startMin || d.endMin !== d.ev.endMin;
    const date = d.mode === "move" ? toCol.date : d.ev.date;
    if (date !== d.ev.date) changed = true;
    payload.start_time = toApiDatetime(date, d.startMin);
    payload.end_time = toApiDatetime(date, d.endMin);
    if (d.mode === "move" && state.view === "day" && toCol.key !== d.fromCol.key) {
      payload.mechanic_ids = mechanicsAfterMove(d.ev, d.fromCol.mechId, toCol.mechId);
      changed = true;
    }
    if (!changed) { render(); return; }
    api("PUT", "/calendar/api/events/" + encodeURIComponent(d.ev.id), payload)
      .then((saved) => { upsertEvent(saved); render(); })
      .catch((err) => {
        notify(err.message || "Could not move the appointment.", "error");
        loadEvents(false).then(render);
      });
  }

  function onKeyDown(e) {
    if (e.key !== "Escape") return;
    if (boardDrag) {
      const b = releaseBoardDrag();
      if (b && b.active) suppressClickUntil = Date.now() + 400;
      return;
    }
    if (drag) {
      const d = releaseDrag();
      if (d && d.active) { suppressClickUntil = Date.now() + 400; render(); }
    }
  }

  function onResize() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (!drag && !boardDrag) render(); }, 120);
  }

  // Live refresh. Every LIVE_POLL_MS the page asks for the change tokens of the loaded week (one
  // cheap request); the appointments are re-read only when the token moved, and once in
  // FULL_REFRESH_MS regardless — a renamed customer or a work order's state does not move it.

  function setLiveState(ok) {
    if (!ui.live) return;
    ui.live.classList.toggle("is-live", ok);
    ui.live.classList.toggle("is-offline", !ok);
    const label = ui.live.querySelector(".cal-live-label");
    if (label) label.textContent = ok ? "Live" : "Reconnecting…";
  }

  function scheduleLive(delay) {
    clearTimeout(live.timer);
    if (!live.stopped) live.timer = setTimeout(liveTick, delay);
  }

  function liveTick() {
    if (live.stopped) return;
    const ws = state.loadedWeek;
    // Never redraw under a drag: the next tick picks the change up.
    if (live.busy || !ws || document.visibilityState !== "visible" || drag || boardDrag) {
      scheduleLive(LIVE_POLL_MS);
      return;
    }
    if (live.renderPending) { live.renderPending = false; render(); }
    if (live.week !== ws) { live.week = ws; live.eventsToken = null; } // another week → its own token
    live.busy = true;
    const url = "/calendar/api/changes?start=" + encodeURIComponent(ws + "T00:00:00") +
      "&end=" + encodeURIComponent(addDays(ws, 7) + "T00:00:00");
    api("GET", url).then((tok) => {
      if (!tok || typeof tok.events !== "string") throw new Error("Unexpected answer");
      live.failures = 0;
      setLiveState(true);
      const jobs = [];
      const settingsToken = String(tok.settings == null ? "" : tok.settings);
      if (live.settingsToken === null) {
        live.settingsToken = settingsToken; // the settings were loaded moments ago, at start
      } else if (settingsToken !== live.settingsToken) {
        jobs.push(refreshSettings().then((applied) => {
          if (applied) { live.settingsToken = settingsToken; renderLegend(); }
          return applied;
        }));
      }
      if (tok.events !== live.eventsToken || Date.now() - live.fullAt > FULL_REFRESH_MS) {
        jobs.push(loadEvents(true).then((changed) => {
          // Remember the token only for a successful read of the week it describes.
          if (changed !== null && state.loadedWeek === ws) { live.eventsToken = tok.events; live.fullAt = Date.now(); }
          return !!changed;
        }));
      }
      return Promise.all(jobs).then((results) => {
        if (!results.some(Boolean)) return;
        if (drag || boardDrag) live.renderPending = true; else render();
      });
    }).catch((err) => {
      live.failures += 1;
      if (live.failures >= LIVE_FAILS_BEFORE_SLOW) setLiveState(false);
      console.error("calendar live refresh", err);
    }).finally(() => {
      live.busy = false;
      scheduleLive(live.failures >= LIVE_FAILS_BEFORE_SLOW ? LIVE_RETRY_MS : LIVE_POLL_MS);
    });
  }

  function onVisibilityChange() {
    if (document.visibilityState === "visible") scheduleLive(0); // back on the tab → check right away
  }

  // ── 5. Appointment modal ─────────────────────────────────────────────────

  const modalEl = $("appointmentModal");
  const f = {
    label: $("appointmentModalLabel"), eventId: $("apptEventId"), workOrderId: $("apptWorkOrderId"),
    linked: $("apptLinkedWO"), linkedNumber: $("apptLinkedWONumber"), linkedState: $("apptLinkedWOState"),
    linkedSummary: $("apptLinkedWOSummary"), linkedOpen: $("apptLinkedWOOpen"), unlinkBtn: $("apptUnlinkWOBtn"),
    customer: $("apptCustomer"), customerSearch: $("apptCustomerSearch"), customerDrop: $("apptCustomerDrop"), customerClear: $("apptCustomerClear"),
    unit: $("apptUnit"), unitSearch: $("apptUnitSearch"), unitDrop: $("apptUnitDrop"), unitClear: $("apptUnitClear"),
    linkedHint: $("apptLinkedFieldsHint"),
    presetSearch: $("apptPresetSearch"), presetDrop: $("apptPresetDrop"), presetTags: $("apptPresetTags"),
    date: $("apptDate"), start: $("apptStart"), end: $("apptEnd"),
    when: $("apptWhen"), whenSummary: $("apptWhenSummary"), dateStrip: $("apptDateStrip"), hourGrid: $("apptHourGrid"),
    minuteGroup: $("apptMinuteGroup"), moreHours: $("apptMoreHours"), durationGroup: $("apptDurationGroup"), endRow: $("apptEndRow"),
    mechSearch: $("apptMechanicSearch"), mechDrop: $("apptMechanicDrop"), mechTags: $("apptMechanicTags"),
    statusGroup: $("apptStatusGroup"), title: $("apptTitle"), overlap: $("apptOverlapHint"),
    deleteBtn: $("apptDeleteBtn"), createWoBtn: $("apptCreateWOBtn"), saveBtn: $("apptSaveBtn"),
  };
  const modal = {
    prepared: false,
    mode: "create",
    readOnly: false,
    status: "scheduled",
    mechanics: [],
    presets: [],
    linkWoId: "",          // work_order_id to send on save ("" = unlink, null = leave untouched)
    linkInfo: null,        // {woNumber, woState, laborSummary, workOrderId} shown in the linked panel
    saving: false,
    focusTitle: false,     // focus the title once the modal is shown (create from an empty slot)
    timePicked: true,      // false = nobody chose the start time yet (opened by "+", not by a slot) → Save asks for it
    durationMin: 60,       // length of the appointment; the end follows the start by this much
    allHours: false,       // hour buttons for the whole day instead of the working hours
    customEnd: false,      // "Other…" pressed → the exact "Ends at" list is shown
  };
  const bsModal = () => window.bootstrap && window.bootstrap.Modal ? window.bootstrap.Modal.getOrCreateInstance(modalEl) : null;

  // Lengths offered as buttons; anything else goes through "Other…" and the exact end time.
  const DURATIONS = [30, 60, 90, 120, 180, 240, 360, 480];

  function fmtDuration(min) {
    if (min < 60) return min + " min";
    const h = Math.floor(min / 60);
    const m = min % 60;
    if (!m) return h + " h";
    return m === 30 ? h + ".5 h" : h + " h " + m + " min";
  }

  function fillTimeSelects() {
    const start = [];
    for (let m = 0; m < 1440; m += SLOT) start.push('<option value="' + m + '">' + fmtMin(m) + "</option>");
    f.start.innerHTML = start.join("");
    fillEndOptions();
  }

  /* "Ends at" offers only times after the start, each with its length: 09:00 (1 h). */
  function fillEndOptions() {
    const s = Number(f.start.value) || 0;
    const keep = clamp(s + modal.durationMin, s + SLOT, 1440);
    const opts = [];
    for (let m = s + SLOT; m <= 1440; m += SLOT) {
      opts.push('<option value="' + m + '">' + (m === 1440 ? "24:00 (midnight)" : fmtMin(m)) + " · " + fmtDuration(m - s) + "</option>");
    }
    f.end.innerHTML = opts.join("");
    f.end.value = String(keep);
  }

  // ── When: day strip, start-time buttons, length ──

  /* Seven days starting today when the chosen day is among them, otherwise the chosen day's week. */
  function dateStripDays(dateIso) {
    const today = shopNow().date;
    const first = dateIso >= today && dateIso <= addDays(today, 6) ? today : weekStartOf(dateIso);
    return Array.from({ length: 7 }, (_, i) => addDays(first, i));
  }

  /* Hours in which a chosen mechanic already has an appointment that day: {hour: ["Name 08:00–10:00"]}. */
  function busyHours(dateIso) {
    const ids = modal.mechanics.map((m) => m.id);
    const out = {};
    if (!ids.length || !isValidIso(dateIso)) return out;
    state.events.forEach((ev) => {
      if (ev.id === f.eventId.value || ev.date !== dateIso || ev.status === "cancelled") return;
      const names = ev.mechanics.filter((m) => ids.indexOf(m.id) >= 0).map((m) => m.name);
      if (!names.length) return;
      const label = names.join(", ") + " " + fmtRange(ev.startMin, ev.endMin);
      for (let h = Math.floor(ev.startMin / 60); h * 60 < ev.endMin; h += 1) (out[h] = out[h] || []).push(label);
    });
    return out;
  }

  function chipHtml(attrs, label, opts) {
    const o = opts || {};
    return '<button type="button" class="btn btn-sm btn-outline-secondary cal-chip' + (o.selected ? " active" : "") +
      (o.extraClass ? " " + o.extraClass : "") + '" ' + attrs + ' aria-pressed="' + (o.selected ? "true" : "false") + '"' +
      (o.title ? ' title="' + esc(o.title) + '"' : "") + (o.disabled || modal.readOnly ? " disabled" : "") + ">" + label + "</button>";
  }

  function renderWhen() {
    const date = isValidIso(f.date.value) ? f.date.value : "";
    const s = Number(f.start.value) || 0;
    const now = shopNow();
    fillEndOptions();
    const e = Number(f.end.value);

    // Day strip
    f.dateStrip.innerHTML = (date ? dateStripDays(date) : dateStripDays(now.date)).map((iso) => {
      const d = dateFromIso(iso);
      const weekday = d.toLocaleDateString("en-US", { weekday: "short" });
      const top = iso === now.date ? "Today" : iso === addDays(now.date, 1) ? "Tomorrow" : weekday;
      // "Tomorrow" is too long for the button on a phone — calendar.css swaps in data-short there
      return chipHtml('data-date="' + iso + '"',
        '<span class="cal-chip-top"' + (top === "Tomorrow" ? ' data-short="' + weekday + '"' : "") + ">" + top +
        '</span><span class="cal-chip-main">' + d.getDate() + "</span>",
        { selected: iso === date, title: longDate(iso), extraClass: "cal-chip-day" });
    }).join("");

    // Start time: one button per hour, carrying the chosen minutes (08:30, 09:30 …)
    const minute = s % 60;
    const hour = Math.floor(s / 60);
    let from = state.settings.start_hour;
    let to = state.settings.end_hour;
    if (modal.allHours || (modal.timePicked && (hour < from || hour >= to))) { from = 0; to = 24; }
    const busy = busyHours(date);
    const cells = [];
    for (let h = from; h < to; h += 1) {
      const value = h * 60 + minute;
      const isBusy = !!busy[h];
      cells.push(chipHtml('data-start="' + value + '"', fmtMin(value), {
        selected: modal.timePicked && h === hour,
        extraClass: (isBusy ? "is-busy " : "") + (date === now.date && value + SLOT <= now.minutes ? "is-past" : ""),
        title: isBusy ? "Already booked: " + busy[h].join("; ") : "Start at " + fmtMin(value),
      }));
    }
    f.hourGrid.innerHTML = cells.join("");
    f.minuteGroup.innerHTML = [0, 15, 30, 45].map((m) =>
      chipHtml('data-minute="' + m + '"', ":" + pad2(m), { selected: m === minute, title: "Start at " + pad2(m) + " minutes past the hour" })
    ).join("");
    const wholeDay = from === 0 && to === 24;
    const workingIsWholeDay = state.settings.start_hour === 0 && state.settings.end_hour === 24;
    f.moreHours.textContent = wholeDay ? "Working hours only" : "Earlier / later hours";
    f.moreHours.classList.toggle("d-none", workingIsWholeDay || modal.readOnly ||
      (wholeDay && !modal.allHours)); // widened by an off-hours time → nothing to switch back to

    // Length
    const preset = DURATIONS.indexOf(modal.durationMin) >= 0 && e - s === modal.durationMin;
    const showEnd = modal.customEnd || !preset;
    f.durationGroup.innerHTML = DURATIONS.map((d) =>
      chipHtml('data-duration="' + d + '"', fmtDuration(d), { selected: !showEnd && d === e - s, disabled: s + d > 1440 })
    ).join("") + chipHtml('data-duration="other"', "Other…", { selected: showEnd, title: "Choose the exact end time" });
    f.endRow.classList.toggle("d-none", !showEnd);
    f.end.disabled = modal.readOnly;

    // Summary
    const day = date ? dateFromIso(date).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" }) : "No date";
    f.whenSummary.textContent = modal.timePicked
      ? day + " · " + fmtMin(s) + " – " + (e >= 1440 ? "24:00" : fmtMin(e)) + " · " + fmtDuration(e - s)
      : day + " · choose a start time";
    f.whenSummary.classList.toggle("is-missing", !modal.timePicked);
    if (modal.timePicked) f.when.classList.remove("is-missing");
    updateOverlapHint();
  }

  /* Start time chosen with a button: the end keeps its distance (the length). */
  function setStart(min, picked) {
    f.start.value = String(clamp(snapDown(min), 0, 1440 - SLOT));
    if (picked) modal.timePicked = true;
    renderWhen();
  }

  function onWhenClick(e) {
    if (modal.readOnly) return;
    const btn = e.target.closest("button");
    if (!btn || btn.disabled || !f.when.contains(btn)) return;
    // The buttons are redrawn on every change — put the focus back on the one just pressed.
    let refocus = "";
    if (btn.dataset.date) {
      setDateInput(f.date, btn.dataset.date);
      refocus = '[data-date="' + btn.dataset.date + '"]';
      renderWhen();
    } else if (btn.dataset.start !== undefined) {
      refocus = '[data-start="' + btn.dataset.start + '"]';
      setStart(Number(btn.dataset.start), true);
    } else if (btn.dataset.minute !== undefined) {
      // Minutes alone are not "a chosen time": the hour buttons just show them (08:30, 09:30 …).
      refocus = '[data-minute="' + btn.dataset.minute + '"]';
      setStart(Math.floor((Number(f.start.value) || 0) / 60) * 60 + Number(btn.dataset.minute), false);
    } else if (btn.dataset.duration === "other") {
      modal.customEnd = true;
      renderWhen();
      f.end.focus();
    } else if (btn.dataset.duration) {
      modal.durationMin = Number(btn.dataset.duration);
      modal.customEnd = false;
      refocus = '[data-duration="' + btn.dataset.duration + '"]';
      renderWhen();
    } else if (btn === f.moreHours) {
      modal.allHours = !modal.allHours;
      renderWhen();
    }
    const again = refocus ? f.when.querySelector(refocus) : null;
    if (again) again.focus({ preventScroll: true });
  }

  /* Save without a start time: show where it is missing. */
  function flagMissingTime() {
    f.when.classList.add("is-missing");
    if (typeof f.when.scrollIntoView === "function") f.when.scrollIntoView({ block: "center", behavior: "smooth" });
    const first = f.hourGrid.querySelector("button:not(:disabled)");
    if (first) first.focus({ preventScroll: true });
  }

  function setSearchValue(wrapInput, text) {
    wrapInput.value = text || "";
    const wrap = wrapInput.closest(".cal-search");
    if (wrap) wrap.classList.toggle("has-value", !!text);
  }

  /* Searchable dropdown on a text input: list-group under the input, arrows/Enter/Esc, click to pick. */
  function makeSearch(input, drop, opts) {
    let visible = [];
    let active = -1;
    const hide = () => { drop.classList.add("d-none"); drop.innerHTML = ""; visible = []; active = -1; };
    const setActive = (i) => {
      active = i;
      drop.querySelectorAll(".list-group-item").forEach((el, idx) => el.classList.toggle("active", idx === i));
      const el = drop.querySelector(".list-group-item.active");
      if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "nearest" });
    };
    const show = () => {
      if (input.disabled || input.readOnly) return;
      const q = input.value.trim().toLowerCase();
      Promise.resolve(opts.items()).then((list) => {
        if (document.activeElement !== input) return;
        // exclude() applies regardless of the query (the list also opens on focus with an empty
        // query), so an already chosen item never shows up again.
        visible = (list || []).filter((it) => (!q || opts.match(it, q)) && !(opts.exclude && opts.exclude(it))).slice(0, 80);
        drop.innerHTML = visible.length
          ? visible.map((it, i) => {
              const sub = opts.sub ? opts.sub(it) : "";
              return '<button type="button" class="list-group-item list-group-item-action" data-idx="' + i + '">' +
                esc(opts.text(it)) + (sub ? '<span class="cal-dd-sub">' + esc(sub) + "</span>" : "") + "</button>";
            }).join("")
          : '<div class="cal-dd-empty">' + esc(opts.empty || "No matches") + "</div>";
        drop.classList.remove("d-none");
        setActive(visible.length ? 0 : -1);
      }).catch((err) => {
        drop.innerHTML = '<div class="cal-dd-empty">' + esc(err.message || "Could not load the list") + "</div>";
        drop.classList.remove("d-none");
      });
    };
    const pick = (it) => { hide(); opts.onPick(it); };
    input.addEventListener("focus", show);
    input.addEventListener("input", () => { if (opts.onType) opts.onType(); show(); });
    input.addEventListener("blur", () => setTimeout(hide, 150));
    input.addEventListener("keydown", (e) => {
      if (drop.classList.contains("d-none")) { if (e.key === "ArrowDown") show(); return; }
      if (e.key === "ArrowDown") { e.preventDefault(); if (visible.length) setActive((active + 1) % visible.length); }
      else if (e.key === "ArrowUp") { e.preventDefault(); if (visible.length) setActive((active - 1 + visible.length) % visible.length); }
      else if (e.key === "Enter") { e.preventDefault(); if (active >= 0 && visible[active]) pick(visible[active]); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); hide(); }
    });
    drop.addEventListener("mousedown", (e) => e.preventDefault()); // keep the input focused
    drop.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-idx]");
      if (btn && visible[Number(btn.dataset.idx)]) pick(visible[Number(btn.dataset.idx)]);
    });
    return { hide };
  }

  function setCustomer(id, label) {
    f.customer.value = id || "";
    setSearchValue(f.customerSearch, label || "");
    setUnit("", "");
    f.unitSearch.disabled = !id || modal.readOnly;
    f.unitSearch.placeholder = id ? "Search unit or VIN…" : "Choose a customer first";
    if (id) loadUnits(id).catch((err) => console.error("calendar units", err));
  }
  function setUnit(id, label) {
    f.unit.value = id || "";
    setSearchValue(f.unitSearch, label || "");
  }

  const isLinkedNow = () => !!(modal.linkInfo && modal.linkWoId !== "");

  function renderTags() {
    f.mechTags.innerHTML = modal.mechanics.map((m) =>
      '<span class="cal-tag"><span class="cal-tag-dot" style="background:' + esc(mechColor(m.id)) + '"></span>' + esc(m.name) +
      '<button type="button" class="cal-tag-remove" data-kind="mech" data-id="' + esc(m.id) + '" title="Remove" aria-label="Remove ' + esc(m.name) + '"' +
      (modal.readOnly ? " disabled" : "") + ">&times;</button></span>"
    ).join("");
    f.presetTags.innerHTML = modal.presets.map((p) =>
      '<span class="cal-tag">' + esc(p.name) +
      '<button type="button" class="cal-tag-remove" data-kind="preset" data-id="' + esc(p.id) + '" title="Remove" aria-label="Remove ' + esc(p.name) + '"' +
      (modal.readOnly ? " disabled" : "") + ">&times;</button></span>"
    ).join("");
    renderWhen(); // the "already booked" marks on the hour buttons follow the chosen mechanics
  }

  function renderStatusButtons() {
    const statuses = state.settings.statuses.slice();
    if (modal.status && !statuses.some((s) => s.key === modal.status)) {
      statuses.push({ key: modal.status, label: statusLabel(modal.status), color: "#888888" });
    }
    // Unselected: the status colour only on the border and the dot, the label in the theme's text
    // colour — dark statuses on the dark modal (and light custom colours on the light one) stay readable.
    f.statusGroup.innerHTML = statuses.map((s) => {
      const selected = s.key === modal.status;
      const style = selected
        ? "background:" + esc(s.color) + ";border-color:" + esc(s.color) + ";color:" + esc(textOn(s.color))
        : "border-color:" + esc(s.color);
      const dot = '<span class="cal-legend-dot" style="background:' + esc(selected ? textOn(s.color) : s.color) + '"></span>';
      return '<button type="button" class="btn btn-sm cal-status-btn' + (selected ? " is-selected" : "") + '" data-status="' + esc(s.key) +
        '" style="' + style + '" aria-pressed="' + (selected ? "true" : "false") + '"' + (modal.readOnly ? " disabled" : "") + ">" +
        dot + esc(s.label) + "</button>";
    }).join("");
  }

  function renderLinkedPanel() {
    const info = modal.linkInfo;
    const linked = isLinkedNow();
    f.linked.classList.toggle("d-none", !linked);
    f.workOrderId.value = linked ? info.workOrderId : "";
    if (linked) {
      f.linkedNumber.textContent = "WO #" + (info.woNumber || "?");
      const st = woStateInfo(info.woState);
      const color = st ? st.color : "#888888";
      f.linkedState.textContent = info.woState ? woStateLabel(info.woState) : "";
      f.linkedState.classList.toggle("d-none", !info.woState);
      f.linkedState.style.background = color;
      f.linkedState.style.color = textOn(color);
      f.linkedSummary.textContent = (info.laborSummary || []).join(", ");
      f.linkedOpen.href = "/work_orders/details?work_order_id=" + encodeURIComponent(info.workOrderId);
      f.unlinkBtn.classList.toggle("d-none", modal.readOnly);
      f.linkedHint.textContent = "Customer and unit come from WO #" + (info.woNumber || "?") + " — change them there or unlink.";
      f.linkedHint.classList.remove("d-none");
    } else if (modal.linkInfo && modal.linkWoId === "") {
      f.linkedHint.textContent = "The link to WO #" + (modal.linkInfo.woNumber || "?") + " will be removed when you save.";
      f.linkedHint.classList.remove("d-none");
    } else {
      f.linkedHint.classList.add("d-none");
    }
    // linked → customer/unit are read-only (they follow the WO); view-only mode disables everything
    const lockIdentity = modal.readOnly || linked;
    f.customerSearch.readOnly = lockIdentity;
    f.customerSearch.disabled = modal.readOnly;
    f.unitSearch.readOnly = lockIdentity;
    f.unitSearch.disabled = modal.readOnly || !f.customer.value;
    f.customerClear.classList.toggle("d-none", lockIdentity);
    f.unitClear.classList.toggle("d-none", lockIdentity);
    f.createWoBtn.classList.toggle("d-none", !(can.createWo && !modal.readOnly && !linked));
  }

  function applyReadOnly() {
    const ro = modal.readOnly;
    [f.presetSearch, f.start, f.end, f.mechSearch, f.title].forEach((el) => { el.disabled = ro; });
    setDateDisabled(f.date, ro);
    f.saveBtn.classList.toggle("d-none", ro);
    f.deleteBtn.classList.toggle("d-none", !(modal.mode === "edit" && can.delete));
  }

  function updateOverlapHint() {
    const date = f.date.value;
    const s = Number(f.start.value);
    const e = Number(f.end.value);
    const ids = modal.mechanics.map((m) => m.id);
    const hits = [];
    if (ids.length && isValidIso(date) && e > s) {
      state.events.forEach((ev) => {
        if (ev.id === f.eventId.value || ev.date !== date || ev.status === "cancelled") return;
        if (!(ev.startMin < e && ev.endMin > s)) return;
        ev.mechanics.filter((m) => ids.indexOf(m.id) >= 0).forEach((m) => {
          hits.push(m.name + " " + fmtRange(ev.startMin, ev.endMin) + (ev.title ? " (" + ev.title.slice(0, 40) + ")" : ""));
        });
      });
    }
    f.overlap.classList.toggle("d-none", !hits.length);
    f.overlap.textContent = hits.length ? "Already booked: " + hits.slice(0, 3).join("; ") + (hits.length > 3 ? " …" : "") : "";
  }

  function resetModal() {
    modal.mode = "create";
    modal.readOnly = false;
    modal.status = state.settings.statuses[0] ? state.settings.statuses[0].key : "scheduled";
    if (statusByKey("scheduled")) modal.status = "scheduled";
    modal.mechanics = [];
    modal.presets = [];
    modal.linkWoId = null;
    modal.linkInfo = null;
    modal.saving = false;
    modal.focusTitle = false;
    modal.timePicked = true;
    modal.durationMin = 60;
    modal.allHours = false;
    modal.customEnd = false;
    f.when.classList.remove("is-missing");
    f.eventId.value = "";
    f.workOrderId.value = "";
    f.title.value = "";
    f.saveBtn.disabled = false;
    f.createWoBtn.disabled = false;
    f.deleteBtn.disabled = false;
    setCustomer("", "");
  }

  function fillTimes(date, startMin, endMin) {
    setDateInput(f.date, date);
    const start = clamp(snapDown(startMin), 0, 1440 - SLOT);
    f.start.value = String(start);
    modal.durationMin = clamp(snapRound(endMin), start + SLOT, 1440) - start;
    modal.customEnd = false;
    fillEndOptions(); // the end = start + length
  }

  function finishOpen() {
    renderTags();
    renderStatusButtons();
    renderLinkedPanel();
    applyReadOnly();
    updateOverlapHint();
    modal.prepared = true;
    const m = bsModal();
    if (m) m.show();
  }

  /* opts: {date, startMin, endMin, mechanics?, status?, snapshot?, needsTime?} — snapshot = WO prefill
     from ?wo=; status = the board column the "+" was pressed in; needsTime = the start time was not
     chosen by the user (no slot was clicked), so it is left unselected and Save asks for it. */
  function openCreateModal(opts) {
    resetModal();
    if (opts.status && statusByKey(opts.status)) modal.status = opts.status;
    f.label.textContent = "New appointment";
    fillTimes(opts.date, opts.startMin, opts.endMin);
    modal.timePicked = !opts.needsTime;
    modal.mechanics = (opts.mechanics || []).map((m) => ({ id: m.id, name: m.name }));
    const snap = opts.snapshot;
    if (snap) {
      setCustomer(snap.customer_id || "", snap.customer_label || "");
      if (snap.unit_id) setUnit(snap.unit_id, snap.unit_label || "");
      modal.mechanics = (Array.isArray(snap.mechanics) ? snap.mechanics : [])
        .filter((m) => m && m.id).map((m) => ({ id: String(m.id), name: String(m.name || "") }));
      f.title.value = String(snap.title || "");
      modal.linkWoId = String(snap.work_order_id || "");
      modal.linkInfo = {
        workOrderId: modal.linkWoId, woNumber: snap.wo_number, woState: String(snap.wo_state || ""), laborSummary: [],
      };
    }
    modal.focusTitle = !snap;
    finishOpen();
  }

  function openEditModal(ev) {
    resetModal();
    modal.mode = "edit";
    modal.readOnly = !can.edit;
    f.label.textContent = modal.readOnly ? "Appointment" : "Edit appointment";
    f.eventId.value = ev.id;
    fillTimes(ev.date, ev.startMin, ev.endMin);
    setCustomer(ev.customerId, ev.customerLabel);
    if (ev.unitId) setUnit(ev.unitId, ev.unitLabel);
    modal.mechanics = ev.mechanics.map((m) => ({ id: m.id, name: m.name }));
    modal.presets = ev.presets.map((p) => ({ id: p.id, name: p.name }));
    modal.status = ev.status;
    f.title.value = ev.title;
    if (ev.workOrderId) {
      modal.linkInfo = { workOrderId: ev.workOrderId, woNumber: ev.woNumber, woState: ev.woState, laborSummary: ev.laborSummary };
      modal.linkWoId = null; // untouched unless the user unlinks
    }
    finishOpen();
  }

  function collectPayload() {
    const date = f.date.value;
    const s = Number(f.start.value);
    const e = Number(f.end.value);
    if (!isValidIso(date)) throw new Error("Please choose a date.");
    if (!modal.timePicked) {
      flagMissingTime();
      throw new Error("Choose the start time — press one of the time buttons.");
    }
    if (!(e > s)) throw new Error("End time must be after start time.");
    const payload = {
      title: f.title.value.trim().slice(0, 500),
      status: modal.status,
      start_time: toApiDatetime(date, s),
      end_time: toApiDatetime(date, e),
      mechanics: modal.mechanics.map((m) => ({ id: m.id, name: m.name })),
      presets: modal.presets.map((p) => ({ id: p.id, name: p.name })),
    };
    // Customer and unit are read-only while linked (they follow the WO) — not sent, so a stored
    // identity that drifted from the WO can never block a title/status/time edit with a 400.
    if (!isLinkedNow()) {
      payload.customer_id = f.customer.value || "";
      payload.unit_id = f.unit.value || "";
    }
    if (modal.linkWoId !== null) payload.work_order_id = modal.linkWoId;
    return payload;
  }

  /* POST or PUT the modal content; resolves with the saved (normalized) appointment. */
  function saveAppointment() {
    let payload;
    try { payload = collectPayload(); } catch (err) { notify(err.message, "warning"); return Promise.reject(new Error("invalid")); }
    const id = f.eventId.value;
    const req = id
      ? api("PUT", "/calendar/api/events/" + encodeURIComponent(id), payload)
      : api("POST", "/calendar/api/events", payload);
    modal.saving = true;
    f.saveBtn.disabled = true;
    f.createWoBtn.disabled = true;
    return req.then((saved) => upsertEvent(saved)).finally(() => {
      modal.saving = false;
      f.saveBtn.disabled = false;
      f.createWoBtn.disabled = false;
    });
  }

  function onSaveClick() {
    if (modal.saving || modal.readOnly) return;
    saveAppointment().then(() => {
      const m = bsModal();
      if (m) m.hide();
      render();
      notify("Appointment saved", "success");
    }).catch((err) => {
      if (err && err.message !== "invalid") notify(err.message || "Could not save the appointment.", "error");
    });
  }

  function onDeleteClick() {
    const id = f.eventId.value;
    if (!id || !can.delete) return;
    confirmDialog("Delete this appointment?").then((ok) => {
      if (!ok) return;
      f.deleteBtn.disabled = true;
      api("DELETE", "/calendar/api/events/" + encodeURIComponent(id))
        .then(() => {
          removeEvent(id);
          const m = bsModal();
          if (m) m.hide();
          render();
          notify("Appointment deleted", "success");
        })
        .catch((err) => notify(err.message || "Could not delete the appointment.", "error"))
        .finally(() => { f.deleteBtn.disabled = false; });
    });
  }

  /* Saves first, then opens a prefilled WO page (same URL contract as before). */
  function onCreateWoClick() {
    if (modal.saving) return;
    const customerId = f.customer.value;
    const unitId = f.unit.value;
    if (!customerId || !unitId) { notify("Please select a customer and unit first.", "warning"); return; }
    saveAppointment().then((saved) => {
      let url = "/work_orders/details?customer_id=" + encodeURIComponent(customerId) + "&unit_id=" + encodeURIComponent(unitId);
      if (modal.presets.length) url += "&presets=" + encodeURIComponent(modal.presets.map((p) => p.id).join(","));
      if (modal.mechanics.length) {
        url += "&mechanic_id=" + encodeURIComponent(modal.mechanics[0].id);
        url += "&mechanic_ids=" + encodeURIComponent(modal.mechanics.map((m) => m.id).join(","));
      }
      const note = f.title.value.trim();
      if (note && note !== f.customerSearch.value.trim()) url += "&note=" + encodeURIComponent(note);
      url += "&calendar_event_id=" + encodeURIComponent(saved.id);
      window.open(url, "_blank");
      const m = bsModal();
      if (m) m.hide();
      render();
    }).catch((err) => {
      if (err && err.message !== "invalid") notify(err.message || "Could not save the appointment.", "error");
    });
  }

  function bindModal() {
    fillTimeSelects();

    makeSearch(f.customerSearch, f.customerDrop, {
      items: loadCustomers,
      match: (c, q) => String(c.label || "").toLowerCase().indexOf(q) >= 0,
      text: (c) => c.label || "(no name)",
      empty: "No customers found",
      onType: () => { if (f.customer.value) setCustomer("", f.customerSearch.value); },
      onPick: (c) => { setCustomer(String(c.id), c.label || ""); renderLinkedPanel(); f.unitSearch.focus(); },
    });
    f.customerClear.addEventListener("click", () => { setCustomer("", ""); renderLinkedPanel(); f.customerSearch.focus(); });

    makeSearch(f.unitSearch, f.unitDrop, {
      items: () => loadUnits(f.customer.value),
      match: (u, q) => (String(u.label || "") + " " + String(u.vin || "")).toLowerCase().indexOf(q) >= 0,
      text: (u) => u.label || "(unit)",
      sub: (u) => (u.vin ? "VIN " + u.vin : ""),
      empty: "This customer has no active units",
      onType: () => { if (f.unit.value) setUnit("", f.unitSearch.value); },
      onPick: (u) => setUnit(String(u.id), u.label || ""),
    });
    f.unitClear.addEventListener("click", () => { setUnit("", ""); f.unitSearch.focus(); });

    const presetChosen = (p) => modal.presets.some((x) => x.id === String(p.id));
    const mechanicChosen = (m) => modal.mechanics.some((x) => x.id === String(m.id));
    makeSearch(f.presetSearch, f.presetDrop, {
      items: loadPresets,
      match: (p, q) => String(p.name || "").toLowerCase().indexOf(q) >= 0,
      exclude: presetChosen,
      text: (p) => p.name || "(preset)",
      empty: "No presets found",
      onPick: (p) => {
        if (!presetChosen(p)) modal.presets.push({ id: String(p.id), name: String(p.name || "") });
        f.presetSearch.value = "";
        renderTags();
      },
    });

    makeSearch(f.mechSearch, f.mechDrop, {
      items: () => state.mechanics,
      match: (m, q) => m.name.toLowerCase().indexOf(q) >= 0,
      exclude: mechanicChosen,
      text: (m) => m.name,
      sub: (m) => m.role.replace(/_/g, " "),
      empty: "No more mechanics to add",
      onPick: (m) => {
        if (!mechanicChosen(m)) modal.mechanics.push({ id: m.id, name: m.name });
        f.mechSearch.value = "";
        renderTags();
      },
    });

    modalEl.addEventListener("click", (e) => {
      const rm = e.target.closest(".cal-tag-remove");
      if (rm && !modal.readOnly) {
        if (rm.dataset.kind === "mech") modal.mechanics = modal.mechanics.filter((m) => m.id !== rm.dataset.id);
        else modal.presets = modal.presets.filter((p) => p.id !== rm.dataset.id);
        renderTags();
        return;
      }
      const st = e.target.closest(".cal-status-btn");
      if (st && !modal.readOnly) { modal.status = st.dataset.status; renderStatusButtons(); }
    });

    f.when.addEventListener("click", onWhenClick);
    f.end.addEventListener("change", () => {
      // An exact end time → that distance becomes the length.
      modal.durationMin = Math.max(SLOT, Number(f.end.value) - Number(f.start.value));
      modal.customEnd = true;
      renderWhen();
    });
    f.date.addEventListener("change", renderWhen);
    f.title.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") onSaveClick(); });

    f.unlinkBtn.addEventListener("click", () => { modal.linkWoId = ""; renderLinkedPanel(); });
    f.saveBtn.addEventListener("click", onSaveClick);
    f.deleteBtn.addEventListener("click", onDeleteClick);
    f.createWoBtn.addEventListener("click", onCreateWoClick);

    // Opened from outside (e.g. a screenshot script) without our prep → a blank "new appointment" for today.
    modalEl.addEventListener("show.bs.modal", () => {
      if (modal.prepared) return;
      resetModal();
      f.label.textContent = "New appointment";
      fillTimes(shopNow().date, 8 * 60, 9 * 60);
      modal.timePicked = false;
      renderTags(); renderStatusButtons(); renderLinkedPanel(); applyReadOnly();
      modal.prepared = true;
    });
    modalEl.addEventListener("shown.bs.modal", () => {
      // Focus the title only when the whole form is visible: in a scrollable body (small screens)
      // focusing it would scroll the Customer field — the first thing to fill — out of view.
      const body = modalEl.querySelector(".modal-body");
      if (modal.focusTitle && body && body.scrollHeight <= body.clientHeight + 1) f.title.focus({ preventScroll: true });
      modal.focusTitle = false;
      if (body) body.scrollTop = 0;
    });
    modalEl.addEventListener("hidden.bs.modal", () => { modal.prepared = false; });
  }

  /* ?wo=<id> → prefill a new appointment from the work order snapshot. */
  function openFromWorkOrder(woId, dateIso) {
    if (!can.create) { notify("You do not have permission to create appointments.", "warning"); return; }
    api("GET", "/calendar/api/work_orders/" + encodeURIComponent(woId) + "/snapshot")
      .then((snap) => {
        const hours = Number(snap && snap.hours) || 0;
        const dur = clamp(Math.ceil(hours * 4) / 4, 1, 8);
        // The length comes from the work order's hours; the start time is for the user to choose.
        openCreateModal({ date: dateIso, startMin: 8 * 60, endMin: Math.min(1440, 8 * 60 + Math.round(dur * 60)), snapshot: snap, needsTime: true });
      })
      .catch((err) => notify(err.message || "Work order not found.", "error"));
  }

  // ── 6. Settings modal ────────────────────────────────────────────────────

  const settingsEl = $("calSettingsModal");
  const sf = settingsEl ? {
    list: $("settingsStatusList"), add: $("settingsAddStatus"), start: $("settingsStartHour"), end: $("settingsEndHour"), save: $("settingsSaveBtn"),
  } : null;
  let settingsRows = [];

  function slugify(label) {
    return String(label || "").trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
  }

  function renderSettingsRows() {
    sf.list.innerHTML = settingsRows.map((r, i) => {
      const system = SYSTEM_STATUS_KEYS.indexOf(r.key) >= 0;
      return '<div class="cal-settings-row" data-idx="' + i + '">' +
        '<input type="color" class="form-control form-control-color" data-field="color" value="' + esc(r.color) + '" title="Colour">' +
        '<input type="text" class="form-control form-control-sm cal-settings-label" data-field="label" value="' + esc(r.label) + '" placeholder="Status name" maxlength="40">' +
        (system
          ? '<span class="cal-settings-lock" title="System status — cannot be removed"><i class="bi bi-lock-fill" aria-hidden="true"></i></span>'
          : '<button type="button" class="btn btn-outline-danger btn-sm cal-settings-remove" data-remove="' + i + '" title="Remove status" aria-label="Remove status"><i class="bi bi-x-lg" aria-hidden="true"></i></button>') +
        "</div>";
    }).join("");
  }

  function openSettingsModal() {
    settingsRows = state.settings.statuses.map((s) => ({ key: s.key, label: s.label, color: s.color }));
    renderSettingsRows();
    const startOpts = [];
    const endOpts = [];
    for (let h = 0; h <= 24; h += 1) {
      if (h < 24) startOpts.push('<option value="' + h + '">' + fmtMin(h * 60) + "</option>");
      if (h > 0) endOpts.push('<option value="' + h + '">' + fmtMin(h * 60) + "</option>");
    }
    sf.start.innerHTML = startOpts.join("");
    sf.end.innerHTML = endOpts.join("");
    sf.start.value = String(state.settings.start_hour);
    sf.end.value = String(state.settings.end_hour);
    const m = window.bootstrap && window.bootstrap.Modal ? window.bootstrap.Modal.getOrCreateInstance(settingsEl) : null;
    if (m) m.show();
  }

  function saveSettings() {
    const statuses = [];
    for (let i = 0; i < settingsRows.length; i += 1) {
      const r = settingsRows[i];
      const label = String(r.label || "").trim();
      if (!label) { notify("Every status needs a name.", "warning"); return; }
      const key = r.key || slugify(label);
      if (!key) { notify('"' + label + '" needs at least one letter or digit.', "warning"); return; }
      if (statuses.some((s) => s.key === key)) { notify('Status "' + label + '" duplicates another one.', "warning"); return; }
      statuses.push({ key, label, color: hexToRgb(r.color) ? r.color : "#888888" });
    }
    const startHour = Number(sf.start.value);
    const endHour = Number(sf.end.value);
    if (!(startHour < endHour)) { notify("Working hours: the end must be after the start.", "warning"); return; }
    sf.save.disabled = true;
    api("PUT", "/calendar/api/settings", { statuses, start_hour: startHour, end_hour: endHour })
      .then((data) => {
        applySettings(data);
        renderLegend();
        render();
        const m = window.bootstrap && window.bootstrap.Modal ? window.bootstrap.Modal.getOrCreateInstance(settingsEl) : null;
        if (m) m.hide();
        notify("Calendar settings saved", "success");
      })
      .catch((err) => notify(err.message || "Could not save the settings.", "error"))
      .finally(() => { sf.save.disabled = false; });
  }

  function bindSettings() {
    if (!settingsEl || !sf) return;
    if (ui.settingsBtn) ui.settingsBtn.addEventListener("click", openSettingsModal);
    sf.add.addEventListener("click", () => {
      settingsRows.push({ key: "", label: "", color: "#888888" });
      renderSettingsRows();
      const inputs = sf.list.querySelectorAll('[data-field="label"]');
      if (inputs.length) inputs[inputs.length - 1].focus();
    });
    sf.list.addEventListener("input", (e) => {
      const row = e.target.closest(".cal-settings-row");
      const field = e.target.dataset.field;
      if (!row || !field) return;
      const r = settingsRows[Number(row.dataset.idx)];
      if (r) r[field] = e.target.value;
    });
    sf.list.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-remove]");
      if (!btn) return;
      settingsRows.splice(Number(btn.dataset.remove), 1);
      renderSettingsRows();
    });
    sf.save.addEventListener("click", saveSettings);
  }

  // ── 7. Boot ──────────────────────────────────────────────────────────────

  function bindToolbar() {
    ui.prev.addEventListener("click", () => navigate(addDays(state.date, state.view === "week" ? -7 : -1)));
    ui.next.addEventListener("click", () => navigate(addDays(state.date, state.view === "week" ? 7 : 1)));
    ui.today.addEventListener("click", () => navigate(shopNow().date));
    ui.date.addEventListener("change", () => { if (isValidIso(ui.date.value)) navigate(ui.date.value); });
    ui.viewWeek.addEventListener("click", () => setView("week"));
    ui.viewDay.addEventListener("click", () => setView("day"));
    if (ui.viewBoard) ui.viewBoard.addEventListener("click", () => setView("board"));
    ui.filter.addEventListener("change", () => setFilter(ui.filter.value));
    ui.mechLegend.addEventListener("click", (e) => {
      const btn = e.target.closest(".cal-mech-legend-btn");
      if (!btn) return;
      setFilter(state.filter === btn.dataset.mech ? "" : btn.dataset.mech);
    });
    gridEl.addEventListener("click", onGridClick);
    gridEl.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerCancel);
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("resize", onResize);
  }

  function init() {
    const params = new URLSearchParams(window.location.search);
    const savedView = lsGet(LS_VIEW);
    const urlView = params.get("view");
    state.view = VIEWS.indexOf(urlView) >= 0 ? urlView : (VIEWS.indexOf(savedView) >= 0 ? savedView : "week");
    const savedFilter = lsGet(LS_MECH);
    state.filter = savedFilter || "";
    if (isValidIso(params.get("date"))) state.date = params.get("date");
    const woParam = params.get("wo");

    bindToolbar();
    bindModal();
    bindSettings();
    updateToolbar();

    Promise.all([loadSettings(), loadMechanics(), loadWoStates()])
      .then(() => {
        renderFilterOptions();
        renderLegend();
        return loadEvents(false);
      })
      .then(() => {
        render();
        syncUrl();
        if (woParam) openFromWorkOrder(woParam, state.date);
        live.fullAt = Date.now();
        scheduleLive(LIVE_POLL_MS);
        nowTimer = setInterval(updateNowLine, 60000);
      });

    window.__roobicoCalendar = {
      destroy() {
        live.stopped = true;
        clearTimeout(live.timer);
        clearInterval(nowTimer);
        clearTimeout(resizeTimer);
        if (boardDrag) releaseBoardDrag();
        document.removeEventListener("keydown", onKeyDown);
        document.removeEventListener("visibilitychange", onVisibilityChange);
        window.removeEventListener("pointermove", onPointerMove);
        window.removeEventListener("pointerup", onPointerUp);
        window.removeEventListener("pointercancel", onPointerCancel);
        window.removeEventListener("resize", onResize);
      },
    };
  }

  init();
})();
