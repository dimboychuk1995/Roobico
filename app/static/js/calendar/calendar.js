(function () {
  "use strict";

  /* SweetAlert-уведомление с fallback на нативный alert. */
  function notify(message, type) {
    if (window.appAlert) window.appAlert(message, type || "error");
    else window.alert(message);
  }

  /* ── constants ── */
  var DAY_NAMES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
  var DAY_NAMES_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  var MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];
  var HOURS_START = 6;
  var HOURS_END = 21;
  var SLOT_HEIGHT = 48;
  var STEP_MINUTES = 15;
  var PX_PER_MINUTE = SLOT_HEIGHT / 60;
  var UNASSIGNED_KEY = "unassigned";
  var UNSCHEDULED_ROW_H = 66; // strip with not-yet-scheduled work orders (see calendar.css)
  var LS_PREFIX = "roobico.calendar.";

  function lsGet(key, fallback) {
    try {
      var v = window.localStorage.getItem(LS_PREFIX + key);
      return v === null ? fallback : v;
    } catch (e) { return fallback; }
  }
  function lsSet(key, value) {
    try { window.localStorage.setItem(LS_PREFIX + key, String(value)); } catch (e) { /* private mode */ }
  }

  function calcSlotHeight() {
    var toolbar = document.querySelector('.cal-toolbar');
    var topbar = document.querySelector('.app-topbar');
    var toolbarH = toolbar ? toolbar.offsetHeight : 0;
    var topbarH = topbar ? topbar.offsetHeight : 0;
    var headerRowH = 70; // approximate header row height
    // account for .app-main-pad padding (~31px), toolbar mb-2 (~8px), borders (~2px)
    var chrome = topbarH + toolbarH + headerRowH + UNSCHEDULED_ROW_H + 42;
    var available = window.innerHeight - chrome;
    var numSlots = HOURS_END - HOURS_START;
    var h = Math.floor(available / numSlots);
    return Math.max(h, 30);
  }

  /* ── state ── */
  var weekStart = null;
  var expandedDayIndex = -1;
  var expandLevel = 0;               // 0 - none, 1 - wide day, 2 - full-width day
  var selectedDayIndex = 0;          // mechanics view: which day of the week
  var viewMode = lsGet("view", "week") === "mechanics" ? "mechanics" : "week";
  var mechanicFilter = "";
  var showWorkOrders = lsGet("showWO", "1") !== "0";
  var nowLineTimer = null;
  var cachedEvents = [];
  var cachedCustomers = [];
  var cachedMechanics = [];
  var cachedStatuses = [];
  var cachedWoStates = [];
  var cachedPresets = [];
  var drag = null; // active drag state
  var dragJustEnded = false;

  /* ── DOM refs ── */
  var container = document.getElementById("calWeekContainer");
  var toolbarEl = document.querySelector(".cal-toolbar");
  var canCreateWO = !!(toolbarEl && toolbarEl.getAttribute("data-can-create-wo") === "1");
  var canCreate = !!(toolbarEl && toolbarEl.getAttribute("data-can-create") === "1");
  var canEdit = !!(toolbarEl && toolbarEl.getAttribute("data-can-edit") === "1");
  var canDelete = !!(toolbarEl && toolbarEl.getAttribute("data-can-delete") === "1");
  var titleEl   = document.getElementById("calTitle");
  var btnPrev   = document.getElementById("calPrev");
  var btnNext   = document.getElementById("calNext");
  var btnToday  = document.getElementById("calToday");
  var btnViewWeek = document.getElementById("calViewWeek");
  var btnViewMech = document.getElementById("calViewMechanics");
  var mechFilterEl = document.getElementById("calMechanicFilter");
  var showWOEl = document.getElementById("calShowWO");
  var legendEl = document.getElementById("calLegend");
  var modalEl   = document.getElementById("appointmentModal");
  var modalTitle = document.getElementById("appointmentModalLabel");
  var elEventId         = document.getElementById("apptEventId");
  var elWorkOrderId     = document.getElementById("apptWorkOrderId");
  var elLinkedWO        = document.getElementById("apptLinkedWO");
  var elLinkedWONumber  = document.getElementById("apptLinkedWONumber");
  var elLinkedWOState   = document.getElementById("apptLinkedWOState");
  var elLinkedWOSummary = document.getElementById("apptLinkedWOSummary");
  var elLinkedWOOpen    = document.getElementById("apptLinkedWOOpen");
  var elUnlinkWOBtn     = document.getElementById("apptUnlinkWOBtn");
  var elCustomer        = document.getElementById("apptCustomer");
  var elCustomerSearch  = document.getElementById("apptCustomerSearch");
  var elCustomerDrop    = document.getElementById("apptCustomerDrop");
  var elUnit            = document.getElementById("apptUnit");
  var elUnitSearch      = document.getElementById("apptUnitSearch");
  var elUnitDrop        = document.getElementById("apptUnitDrop");
  var elDate            = document.getElementById("apptDate");
  var elStart           = document.getElementById("apptStart");
  var elEnd             = document.getElementById("apptEnd");
  var elMechanic        = document.getElementById("apptMechanic");
  var elMechanicSearch  = document.getElementById("apptMechanicSearch");
  var elMechanicDrop    = document.getElementById("apptMechanicDrop");
  var elStatusGrp       = document.getElementById("apptStatusGroup");
  var elTitleInp        = document.getElementById("apptTitle");
  var elPresetSearch    = document.getElementById("apptPresetSearch");
  var elPresetDrop      = document.getElementById("apptPresetDrop");
  var elPresetPendingId = document.getElementById("apptPresetPendingId");
  var elPresetAddBtn    = document.getElementById("apptPresetAddBtn");
  var elPresetTags      = document.getElementById("apptPresetTags");
  var elSaveBtn         = document.getElementById("apptSaveBtn");
  var elDelBtn          = document.getElementById("apptDeleteBtn");
  var elCreateWOBtn     = document.getElementById("apptCreateWOBtn");
  var selectedPresets   = []; // [{id, name}]
  var unlinkRequested   = false;

  // WO modal refs
  var woModalEl   = document.getElementById("woEventModal");
  var woModalTitle = document.getElementById("woEventModalLabel");
  var woStateEl   = document.getElementById("woEventState");
  var woWhenEl    = document.getElementById("woEventWhen");
  var woCustomerEl = document.getElementById("woEventCustomer");
  var woUnitEl    = document.getElementById("woEventUnit");
  var woMechanicsEl = document.getElementById("woEventMechanics");
  var woLaborsEl  = document.getElementById("woEventLabors");
  var woHoursEl   = document.getElementById("woEventHours");
  var woHintEl    = document.getElementById("woEventHint");
  var woScheduleBtn = document.getElementById("woEventScheduleBtn");
  var woOpenBtn   = document.getElementById("woEventOpenBtn");
  var currentWoEvent = null;

  if (!container) return;

  var bsModal = null;
  function getModal() {
    if (!bsModal && modalEl) {
      try { bsModal = new bootstrap.Modal(modalEl); } catch (e) {}
    }
    return bsModal;
  }
  var bsWoModal = null;
  function getWoModal() {
    if (!bsWoModal && woModalEl) {
      try { bsWoModal = new bootstrap.Modal(woModalEl); } catch (e) {}
    }
    return bsWoModal;
  }

  /* ── date helpers ── */

  function cloneDate(d) {
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  }

  function mondayOfWeek(d) {
    var c = cloneDate(d);
    var diff = (c.getDay() + 6) % 7;
    c.setDate(c.getDate() - diff);
    return c;
  }

  function addDays(d, n) {
    var c = cloneDate(d);
    c.setDate(c.getDate() + n);
    return c;
  }

  function isSameDay(a, b) {
    return a.getFullYear() === b.getFullYear() &&
           a.getMonth() === b.getMonth() &&
           a.getDate() === b.getDate();
  }

  function pad2(v) { return String(v).padStart(2, "0"); }

  function formatDateShort(d) {
    return MONTH_NAMES[d.getMonth()].slice(0, 3) + " " + d.getDate();
  }

  function toISODate(d) {
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  function toLocalISO(dateStr, timeStr) {
    return dateStr + "T" + timeStr + ":00";
  }

  function parseISODate(str) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(str || ""));
    if (!m) return null;
    return new Date(parseInt(m[1], 10), parseInt(m[2], 10) - 1, parseInt(m[3], 10));
  }

  function dayIndexOf(date) {
    for (var d = 0; d < 7; d++) {
      if (isSameDay(date, addDays(weekStart, d))) return d;
    }
    return -1;
  }

  function fmtTime(d) {
    return pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }

  /* Time selects know only 15-minute steps between HOURS_START and HOURS_END:
     snap any (start, end) in minutes of the day into that grid, end > start. */
  function clampGridMinutes(min) {
    return Math.max(HOURS_START * 60, Math.min(HOURS_END * 60, min));
  }

  function snapRange(startMin, endMin) {
    var st = clampGridMinutes(Math.floor(startMin / STEP_MINUTES) * STEP_MINUTES);
    var en = clampGridMinutes(Math.ceil(endMin / STEP_MINUTES) * STEP_MINUTES);
    if (en <= st) en = Math.min(st + STEP_MINUTES, HOURS_END * 60);
    if (en <= st) st = en - STEP_MINUTES;
    return [st, en];
  }

  function minutesToValue(min) {
    return pad2(Math.floor(min / 60)) + ":" + pad2(min % 60);
  }

  function fmtDuration(seconds) {
    var s = Math.max(0, Math.floor(Number(seconds) || 0));
    var h = Math.floor(s / 3600);
    var m = Math.round((s % 3600) / 60);
    return h > 0 ? h + "h " + m + "m" : m + "m";
  }

  /* ── time select options (15-min step) ── */

  function buildTimeOptions(selectEl) {
    selectEl.innerHTML = "";
    for (var h = HOURS_START; h <= HOURS_END; h++) {
      for (var m = 0; m < 60; m += STEP_MINUTES) {
        if (h === HOURS_END && m > 0) break;
        var opt = document.createElement("option");
        var val = pad2(h) + ":" + pad2(m);
        opt.value = val;
        opt.textContent = val;
        selectEl.appendChild(opt);
      }
    }
  }

  // pre-fill both selects
  buildTimeOptions(elStart);
  buildTimeOptions(elEnd);

  /* ── event classification ── */

  function isWorkOrderEvent(ev) {
    return !!(ev && (ev.kind === "work_order" || ev.work_order_id));
  }

  function isVirtual(ev) {
    return !!(ev && ev.is_virtual);
  }

  function eventMechanicIds(ev) {
    if (Array.isArray(ev.mechanic_ids) && ev.mechanic_ids.length) return ev.mechanic_ids.map(String);
    if (ev.mechanic_id) return [String(ev.mechanic_id)];
    return [];
  }

  function eventMechanicNames(ev) {
    if (Array.isArray(ev.mechanics) && ev.mechanics.length) {
      return ev.mechanics.map(function (m) { return m.name || ""; }).filter(Boolean);
    }
    return ev.mechanic_name ? [ev.mechanic_name] : [];
  }

  /* Each mechanic gets a stable colour (by position in the shop's mechanic
     list) and initials; both appear in the legend on top and on every block. */
  // Deliberately away from the status hues (amber / blue / violet / teal /
  // green), so "who" never looks like "what state".
  var MECHANIC_COLORS = ["#d81b60", "#f4511e", "#8d6e63", "#00acc1", "#c0ca33", "#6d4c41", "#ad1457", "#ff8f00", "#607d8b", "#9e9d24", "#e91e63", "#546e7a"];

  function mechanicIndex(id) {
    for (var i = 0; i < cachedMechanics.length; i++) {
      if (String(cachedMechanics[i].id) === String(id)) return i;
    }
    return -1;
  }

  function mechanicColor(id) {
    var idx = mechanicIndex(id);
    if (idx < 0) {
      // not in the current list (deactivated) — hash the id
      var h = 0, str = String(id || "");
      for (var c = 0; c < str.length; c++) h = (h * 31 + str.charCodeAt(c)) & 0xffff;
      idx = h;
    }
    return MECHANIC_COLORS[idx % MECHANIC_COLORS.length];
  }

  function mechanicInitials(name) {
    var parts = String(name || "").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "?";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  function eventMechanicList(ev) {
    if (Array.isArray(ev.mechanics) && ev.mechanics.length) return ev.mechanics;
    if (ev.mechanic_id) return [{ id: ev.mechanic_id, name: ev.mechanic_name || "" }];
    return [];
  }

  function woStateInfo(key) {
    for (var i = 0; i < cachedWoStates.length; i++) {
      if (cachedWoStates[i].key === key) return cachedWoStates[i];
    }
    return { key: key || "open", label: key ? key.replace(/_/g, " ") : "Work order", color: "#1a73e8" };
  }

  function eventColor(ev, statusColors) {
    if (isWorkOrderEvent(ev) && ev.wo_state) return woStateInfo(ev.wo_state).color;
    return statusColors[ev.status] || "#1a73e8";
  }

  function woPageUrl(workOrderId) {
    return "/work_orders/details?work_order_id=" + encodeURIComponent(workOrderId);
  }

  function passesFilters(ev) {
    if (!showWorkOrders && isVirtual(ev)) return false;
    if (mechanicFilter) {
      var ids = eventMechanicIds(ev);
      if (mechanicFilter === UNASSIGNED_KEY) return ids.length === 0;
      return ids.indexOf(mechanicFilter) !== -1;
    }
    return true;
  }

  /* ── title ── */

  function updateTitle() {
    var txt;
    if (viewMode === "mechanics") {
      var day = addDays(weekStart, selectedDayIndex);
      txt = DAY_NAMES_LONG[selectedDayIndex] + ", " + MONTH_NAMES[day.getMonth()] + " " + day.getDate() + ", " + day.getFullYear();
      titleEl.textContent = txt;
      return;
    }
    var end = addDays(weekStart, 6);
    if (weekStart.getMonth() === end.getMonth()) {
      txt = MONTH_NAMES[weekStart.getMonth()] + " " + weekStart.getDate() + " – " + end.getDate() + ", " + weekStart.getFullYear();
    } else if (weekStart.getFullYear() === end.getFullYear()) {
      txt = formatDateShort(weekStart) + " – " + formatDateShort(end) + ", " + weekStart.getFullYear();
    } else {
      txt = formatDateShort(weekStart) + ", " + weekStart.getFullYear() + " – " + formatDateShort(end) + ", " + end.getFullYear();
    }
    titleEl.textContent = txt;
  }

  /* ── API helpers ── */

  function fetchJSON(url, opts) {
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) {
          var msg = (data && data.error) ? data.error : ("HTTP " + r.status);
          throw new Error(msg);
        }
        return data;
      });
    });
  }

  function sendJSON(url, method, payload) {
    return fetchJSON(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
  }

  function loadCustomers() {
    return fetchJSON("/calendar/api/customers").then(function (data) {
      cachedCustomers = Array.isArray(data) ? data : [];
    });
  }

  function loadMechanics() {
    return fetchJSON("/calendar/api/mechanics").then(function (data) {
      cachedMechanics = Array.isArray(data) ? data : [];
      renderMechanicFilter();
      renderLegend();
    });
  }

  function loadStatuses() {
    return fetchJSON("/calendar/api/statuses").then(function (data) {
      cachedStatuses = Array.isArray(data) ? data : [];
    });
  }

  function loadWoStates() {
    return fetchJSON("/calendar/api/wo_states").then(function (data) {
      cachedWoStates = Array.isArray(data) ? data : [];
      renderLegend();
    });
  }

  function loadPresets() {
    return fetchJSON("/calendar/api/presets").then(function (data) {
      cachedPresets = Array.isArray(data) ? data : [];
    });
  }

  function loadUnits(customerId) {
    if (!customerId) return Promise.resolve([]);
    return fetchJSON("/calendar/api/units/" + customerId);
  }

  var loadSeq = 0;

  function loadEvents() {
    var ws = toISODate(weekStart);
    var we = toISODate(addDays(weekStart, 7));
    var url = "/calendar/api/events?start=" + ws + "T00:00:00&end=" + we + "T00:00:00&include_work_orders=1";
    var seq = ++loadSeq;
    return fetchJSON(url)
      .then(function (data) {
        if (seq !== loadSeq) return; // a newer week was requested meanwhile
        cachedEvents = Array.isArray(data) ? data : [];
        renderEvents();
        updateMechanicHeaderCounts();
      })
      .catch(function (e) {
        notify("Could not load calendar events: " + (e.message || e), "error");
      });
  }

  /* ── toolbar: filters, legend ── */

  function renderMechanicFilter() {
    if (!mechFilterEl) return;
    var current = mechFilterEl.value || mechanicFilter;
    mechFilterEl.innerHTML = "";
    var optAll = document.createElement("option");
    optAll.value = "";
    optAll.textContent = "All mechanics";
    mechFilterEl.appendChild(optAll);
    for (var i = 0; i < cachedMechanics.length; i++) {
      var opt = document.createElement("option");
      opt.value = cachedMechanics[i].id;
      opt.textContent = cachedMechanics[i].name;
      mechFilterEl.appendChild(opt);
    }
    var optNone = document.createElement("option");
    optNone.value = UNASSIGNED_KEY;
    optNone.textContent = "Unassigned only";
    mechFilterEl.appendChild(optNone);
    mechFilterEl.value = current;
    if (mechFilterEl.value !== current) { mechFilterEl.value = ""; mechanicFilter = ""; }
  }

  function renderLegend() {
    if (!legendEl) return;
    legendEl.innerHTML = "";
    var showStates = showWorkOrders && cachedWoStates.length > 0;
    if (!showStates && !cachedMechanics.length) { legendEl.classList.add("d-none"); return; }
    legendEl.classList.remove("d-none");
    if (showStates) {
      var label = document.createElement("span");
      label.className = "cal-legend-label";
      label.textContent = "Work orders:";
      legendEl.appendChild(label);
      for (var i = 0; i < cachedWoStates.length; i++) {
        var item = document.createElement("span");
        item.className = "cal-legend-item";
        var dot = document.createElement("span");
        dot.className = "cal-legend-dot";
        dot.style.backgroundColor = cachedWoStates[i].color;
        item.appendChild(dot);
        item.appendChild(document.createTextNode(cachedWoStates[i].label));
        legendEl.appendChild(item);
      }
      var hint = document.createElement("span");
      hint.className = "cal-legend-hint";
      hint.textContent = "Blocks = when mechanics actually worked (from their timers). Work orders with no time yet sit in the \"Not scheduled\" strip.";
      legendEl.appendChild(hint);
    }
    renderMechanicMarkers();
  }

  /* Who is who: one marker per mechanic (colour + initials + name). Click a
     marker to show only that mechanic; click again to show everyone. */
  function renderMechanicMarkers() {
    if (!legendEl || !cachedMechanics.length) return;
    if (legendEl.childNodes.length) {
      var br = document.createElement("span");
      br.className = "cal-legend-break";
      legendEl.appendChild(br);
    }
    var label = document.createElement("span");
    label.className = "cal-legend-label";
    label.textContent = "Mechanics:";
    legendEl.appendChild(label);
    for (var i = 0; i < cachedMechanics.length; i++) {
      (function (m) {
        var marker = document.createElement("button");
        marker.type = "button";
        marker.className = "cal-mech-marker" + (mechanicFilter === String(m.id) ? " active" : "");
        marker.title = mechanicFilter === String(m.id) ? "Show everyone" : "Show only " + m.name;
        var dot = document.createElement("span");
        dot.className = "cal-mech-badge";
        dot.style.backgroundColor = mechanicColor(m.id);
        dot.textContent = mechanicInitials(m.name);
        marker.appendChild(dot);
        marker.appendChild(document.createTextNode(m.name));
        marker.addEventListener("click", function () {
          setMechanicFilter(mechanicFilter === String(m.id) ? "" : String(m.id));
        });
        legendEl.appendChild(marker);
      })(cachedMechanics[i]);
    }
  }

  function setMechanicFilter(value) {
    mechanicFilter = value || "";
    if (mechFilterEl) mechFilterEl.value = mechanicFilter;
    renderLegend();
    if (viewMode === "mechanics") render();
    else { renderEvents(); }
  }

  function setViewMode(mode) {
    viewMode = mode === "mechanics" ? "mechanics" : "week";
    lsSet("view", viewMode);
    if (btnViewWeek) btnViewWeek.classList.toggle("active", viewMode === "week");
    if (btnViewMech) btnViewMech.classList.toggle("active", viewMode === "mechanics");
    if (viewMode === "mechanics") {
      var todayIdx = dayIndexOf(new Date());
      if (selectedDayIndex < 0 || selectedDayIndex > 6) selectedDayIndex = todayIdx >= 0 ? todayIdx : 0;
    }
    expandedDayIndex = -1;
    expandLevel = 0;
    render();
  }

  /* ── columns (days in week view, mechanics in mechanics view) ── */

  function getColumns() {
    var cols = [];
    if (viewMode === "mechanics") {
      var day = addDays(weekStart, selectedDayIndex);
      for (var i = 0; i < cachedMechanics.length; i++) {
        var m = cachedMechanics[i];
        if (mechanicFilter === UNASSIGNED_KEY || (mechanicFilter && mechanicFilter !== String(m.id))) continue;
        cols.push({ key: "mech:" + m.id, mechanicId: m.id, mechanicName: m.name, role: m.role || "", date: day, dayIndex: selectedDayIndex });
      }
      if (!mechanicFilter || mechanicFilter === UNASSIGNED_KEY) {
        cols.push({ key: "mech:" + UNASSIGNED_KEY, mechanicId: "", mechanicName: "Unassigned", role: "", date: day, dayIndex: selectedDayIndex, unassigned: true });
      }
      return cols;
    }
    for (var d = 0; d < 7; d++) {
      cols.push({ key: "day:" + d, date: addDays(weekStart, d), dayIndex: d });
    }
    return cols;
  }

  function eventInColumn(col, ev) {
    if (!ev.start_time) return false;
    if (!passesFilters(ev)) return false;
    var st = new Date(ev.start_time);
    if (!isSameDay(st, col.date)) return false;
    if (viewMode === "mechanics") {
      var ids = eventMechanicIds(ev);
      if (col.unassigned) {
        if (ids.length) return false;
      } else if (ids.indexOf(String(col.mechanicId)) === -1) {
        return false;
      }
    }
    return true;
  }

  // Events drawn on the time grid (appointments + actual mechanic sessions).
  function columnEvents(col, events) {
    var out = [];
    for (var i = 0; i < events.length; i++) {
      if (events[i].unscheduled) continue;
      // a plan replaced by actual work is hidden only while that work is shown
      if (events[i].superseded && showWorkOrders) continue;
      if (eventInColumn(col, events[i])) out.push(events[i]);
    }
    return out;
  }

  // Work orders without any scheduled or tracked time: chips in the strip.
  function columnChips(col, events) {
    var out = [];
    for (var i = 0; i < events.length; i++) {
      if (!events[i].unscheduled) continue;
      if (eventInColumn(col, events[i])) out.push(events[i]);
    }
    out.sort(function (a, b) { return (a.wo_number || 0) - (b.wo_number || 0); });
    return out;
  }

  /* ── build grid ── */

  function buildHeaderRow(columns) {
    var row = document.createElement("div");
    row.className = "cal-header-row";

    var gutter = document.createElement("div");
    gutter.className = "cal-header-gutter";
    row.appendChild(gutter);

    var cols = document.createElement("div");
    cols.className = "cal-header-cols";

    var today = new Date();
    for (var i = 0; i < columns.length; i++) {
      var col = columns[i];
      var cell = document.createElement("div");
      cell.className = "cal-header-cell";
      cell.setAttribute("data-col-key", col.key);

      if (viewMode === "mechanics") {
        cell.classList.add("cal-header-cell--mech");
        if (col.unassigned) cell.classList.add("cal-header-cell--unassigned");
        var name = document.createElement("span");
        name.className = "cal-mech-name";
        if (!col.unassigned) {
          var hb = document.createElement("span");
          hb.className = "cal-mech-badge";
          hb.style.backgroundColor = mechanicColor(col.mechanicId);
          hb.textContent = mechanicInitials(col.mechanicName);
          name.appendChild(hb);
        }
        name.appendChild(document.createTextNode(col.mechanicName));
        name.title = col.mechanicName;
        cell.appendChild(name);
        var sub = document.createElement("span");
        sub.className = "cal-mech-sub";
        sub.setAttribute("data-count-for", col.key);
        sub.textContent = col.role ? col.role.replace(/_/g, " ") : "";
        cell.appendChild(sub);
      } else {
        var isToday = isSameDay(col.date, today);
        if (isToday) cell.classList.add("cal-today");
        cell.setAttribute("data-day-index", col.dayIndex);

        var dayName = document.createElement("span");
        dayName.className = "cal-day-name";
        dayName.textContent = DAY_NAMES[col.dayIndex];
        cell.appendChild(dayName);

        var dayNum = document.createElement("span");
        dayNum.className = "cal-day-number" + (isToday ? " cal-day-number-today" : "");
        dayNum.textContent = col.date.getDate();
        cell.appendChild(dayNum);

        // markers on top of the lanes - filled when the day is expanded
        var laneNames = document.createElement("div");
        laneNames.className = "cal-lane-names";
        cell.appendChild(laneNames);

        cell.title = "Click to expand this day, click again for full width, once more to collapse";
        (function (idx) {
          cell.addEventListener("click", function () { toggleExpandDay(idx); });
        })(col.dayIndex);
      }

      cols.appendChild(cell);
    }

    row.appendChild(cols);
    return row;
  }

  function buildUnscheduledRow(columns) {
    var row = document.createElement("div");
    row.className = "cal-unscheduled-row";

    var gutter = document.createElement("div");
    gutter.className = "cal-unscheduled-gutter";
    gutter.title = "Work orders without a scheduled time or tracked work yet. Drag a chip onto the grid to schedule it.";
    gutter.innerHTML = '<span>Not<br>scheduled</span>';
    row.appendChild(gutter);

    var cells = document.createElement("div");
    cells.className = "cal-unscheduled-cols";
    for (var i = 0; i < columns.length; i++) {
      var cell = document.createElement("div");
      cell.className = "cal-unscheduled-cell";
      cell.setAttribute("data-col-key", columns[i].key);
      cells.appendChild(cell);
    }
    row.appendChild(cells);
    return row;
  }

  function renderUnscheduled() {
    var columns = getColumns();
    var cells = container.querySelectorAll(".cal-unscheduled-cell");
    for (var ci = 0; ci < columns.length; ci++) {
      var cell = cells[ci];
      if (!cell) continue;
      cell.innerHTML = "";
      var chips = columnChips(columns[ci], cachedEvents);
      for (var k = 0; k < chips.length; k++) {
        cell.appendChild(buildChip(chips[k]));
      }
    }
  }

  function buildChip(ev) {
    var info = woStateInfo(ev.wo_state);
    var chip = document.createElement("div");
    chip.className = "cal-chip";
    chip.style.borderColor = info.color;
    chip.style.color = info.color;
    var label = (ev.wo_number ? "WO #" + ev.wo_number : "WO") + (ev.customer_label ? " · " + ev.customer_label : "");
    chip.innerHTML = '<span class="cal-chip-dot" style="background:' + escapeHtml(info.color) + '"></span>' +
      '<span class="cal-chip-text">' + escapeHtml(label) + '</span>';
    var tip = [label];
    if (ev.unit_label) tip.push(ev.unit_label);
    if (Array.isArray(ev.labor_summary) && ev.labor_summary.length) tip.push(ev.labor_summary.join(", "));
    var names = eventMechanicNames(ev);
    if (names.length) tip.push("Assigned: " + names.join(", "));
    tip.push("Status: " + info.label);
    tip.push(canCreate ? "Drag onto the grid to schedule, click for details" : "Click for details");
    chip.title = tip.join("\n");

    chip.addEventListener("click", function (e) {
      e.stopPropagation();
      if (dragJustEnded) return;
      openWoModal(ev);
    });
    if (canCreate) {
      chip.addEventListener("mousedown", function (e) {
        if (e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        startChipDrag(ev, e);
      });
    } else {
      chip.classList.add("cal-chip--static");
    }
    return chip;
  }

  function buildTimeGutter() {
    var gutter = document.createElement("div");
    gutter.className = "cal-time-gutter";

    for (var h = HOURS_START; h <= HOURS_END; h++) {
      var label = document.createElement("div");
      label.className = "cal-time-label";
      label.style.height = SLOT_HEIGHT + "px";
      label.textContent = pad2(h) + ":00";
      gutter.appendChild(label);
    }
    return gutter;
  }

  function buildColumn(col) {
    var colEl = document.createElement("div");
    colEl.className = "cal-day-col";
    colEl.setAttribute("data-col-key", col.key);
    colEl.setAttribute("data-day-index", col.dayIndex);
    if (col.mechanicId !== undefined) colEl.setAttribute("data-mechanic-id", col.mechanicId || "");

    for (var h = HOURS_START; h < HOURS_END; h++) {
      var slot = document.createElement("div");
      slot.className = "cal-hour-slot";
      slot.style.height = SLOT_HEIGHT + "px";
      slot.setAttribute("data-hour", h);

      (function (hour, column) {
        slot.addEventListener("click", function (e) {
          if (dragJustEnded || !canCreate) return;
          if (e.target.closest(".cal-event")) return;
          openModal(null, column.dayIndex, hour, column.unassigned ? null : column.mechanicId || null);
        });
      })(h, col);

      colEl.appendChild(slot);
    }
    return colEl;
  }

  function render() {
    container.innerHTML = "";
    updateTitle();

    SLOT_HEIGHT = calcSlotHeight();
    PX_PER_MINUTE = SLOT_HEIGHT / 60;

    var columns = getColumns();

    var grid = document.createElement("div");
    grid.className = "cal-grid";
    grid.classList.toggle("cal-grid--mechanics", viewMode === "mechanics");

    grid.appendChild(buildHeaderRow(columns));
    grid.appendChild(buildUnscheduledRow(columns));

    var body = document.createElement("div");
    body.className = "cal-body-scroll";

    body.appendChild(buildTimeGutter());

    var bodyCols = document.createElement("div");
    bodyCols.className = "cal-body-cols";
    for (var i = 0; i < columns.length; i++) {
      bodyCols.appendChild(buildColumn(columns[i]));
    }
    body.appendChild(bodyCols);

    grid.appendChild(body);
    container.appendChild(grid);

    applyLayout();
    updateNowLine();
    startNowLineTimer();
    loadEvents();
  }

  /* ── render events on grid ── */

  function renderEvents() {
    var columns = getColumns();
    var colEls = container.querySelectorAll(".cal-day-col");
    for (var c = 0; c < colEls.length; c++) {
      var old = colEls[c].querySelectorAll(".cal-event");
      for (var x = 0; x < old.length; x++) old[x].remove();
    }
    renderUnscheduled();

    var statusColors = {};
    for (var s = 0; s < cachedStatuses.length; s++) {
      statusColors[cachedStatuses[s].key] = cachedStatuses[s].color;
    }

    for (var ci = 0; ci < columns.length; ci++) {
      var col = columns[ci];
      var colEl = colEls[ci];
      if (!colEl) continue;
      var oldSeps = colEl.querySelectorAll(".cal-lane-sep");
      for (var os = 0; os < oldSeps.length; os++) oldSeps[os].remove();

      var colEvents = columnEvents(col, cachedEvents);
      markConflicts(colEvents);

      // Week view: every day is laid out in mechanic lanes, so one mechanic
      // always has one place in the column. Collapsed day - only mechanics
      // who have blocks that day, no markers; expanded day - every mechanic
      // of the shop (same order as "By mechanic"), markers in the header.
      var expanded = viewMode === "week" && col.dayIndex === expandedDayIndex;
      var lanes = null;
      if (viewMode === "week") lanes = expanded ? dayLanes(colEvents) : usedLanes(colEvents);
      updateLaneHeader(col, expanded ? lanes : null);

      if (lanes && lanes.length) {
        var laneCount = lanes.length;
        for (var li = 0; li < laneCount; li++) {
          if (li > 0 && expanded) {
            var sep = document.createElement("div");
            sep.className = "cal-lane-sep";
            sep.style.left = (li / laneCount * 100) + "%";
            colEl.appendChild(sep);
          }
          var laneEvents = colEvents.filter(function (ev) { return eventLaneKeys(ev).indexOf(lanes[li].key) !== -1; });
          var laneLayout = layoutItems(laneEvents);
          for (var la = 0; la < laneLayout.length; la++) {
            var item = laneLayout[la];
            renderSingleEvent(item.ev, colEl, statusColors,
              (li + item.colIndex / item.totalCols) / laneCount * 100,
              (1 / item.totalCols) / laneCount * 100);
          }
        }
      } else {
        var layout = layoutItems(colEvents);
        for (var a = 0; a < layout.length; a++) {
          renderSingleEvent(layout[a].ev, colEl, statusColors,
            layout[a].colIndex / layout[a].totalCols * 100,
            1 / layout[a].totalCols * 100);
        }
      }
    }
  }

  /* Which lane(s) an event belongs to: its mechanics that exist in the shop's
     list; anything else (no mechanic, deactivated user) goes to "Unassigned". */
  function eventLaneKeys(ev) {
    var keys = [];
    var ids = eventMechanicIds(ev);
    for (var i = 0; i < ids.length; i++) {
      if (mechanicIndex(ids[i]) >= 0 && keys.indexOf(ids[i]) === -1) keys.push(ids[i]);
    }
    return keys.length ? keys : [UNASSIGNED_KEY];
  }

  /* Lanes of an expanded day: every mechanic of the shop (respecting the
     mechanic filter) + "Unassigned" only when something has no mechanic. */
  function dayLanes(colEvents) {
    var lanes = [];
    for (var i = 0; i < cachedMechanics.length; i++) {
      var m = cachedMechanics[i];
      if (mechanicFilter === UNASSIGNED_KEY || (mechanicFilter && mechanicFilter !== String(m.id))) continue;
      lanes.push({ key: String(m.id), name: m.name });
    }
    var hasUnassigned = false;
    for (var k = 0; k < colEvents.length; k++) {
      if (eventLaneKeys(colEvents[k]).indexOf(UNASSIGNED_KEY) !== -1) { hasUnassigned = true; break; }
    }
    if (hasUnassigned && (!mechanicFilter || mechanicFilter === UNASSIGNED_KEY)) {
      lanes.push({ key: UNASSIGNED_KEY, name: "Unassigned" });
    }
    return lanes;
  }

  /* Lanes of a collapsed day: only mechanics who have blocks that day (in
     the shop's order) + "Unassigned" when something has no mechanic. */
  function usedLanes(colEvents) {
    var used = {};
    for (var k = 0; k < colEvents.length; k++) {
      var keys = eventLaneKeys(colEvents[k]);
      for (var j = 0; j < keys.length; j++) used[keys[j]] = true;
    }
    var lanes = [];
    for (var i = 0; i < cachedMechanics.length; i++) {
      var id = String(cachedMechanics[i].id);
      if (used[id]) lanes.push({ key: id, name: cachedMechanics[i].name });
    }
    if (used[UNASSIGNED_KEY]) lanes.push({ key: UNASSIGNED_KEY, name: "Unassigned" });
    return lanes;
  }

  /* Markers on top of the expanded day: whose lane is which. They live in
     the header cell, which has the same width as the column below. */
  function updateLaneHeader(col, lanes) {
    if (viewMode !== "week") return;
    var cell = container.querySelector('.cal-header-cell[data-day-index="' + col.dayIndex + '"]');
    if (!cell) return;
    var holder = cell.querySelector(".cal-lane-names");
    if (!holder) return;
    holder.innerHTML = "";
    var show = !!(lanes && lanes.length);
    holder.classList.toggle("has-lanes", show);
    if (!show) return;
    for (var i = 0; i < lanes.length; i++) {
      var name = document.createElement("span");
      name.className = "cal-lane-name" + (lanes[i].key === UNASSIGNED_KEY ? " cal-lane-name--unassigned" : "");
      if (lanes[i].key !== UNASSIGNED_KEY) {
        var badge = document.createElement("span");
        badge.className = "cal-mech-badge";
        badge.style.backgroundColor = mechanicColor(lanes[i].key);
        badge.textContent = mechanicInitials(lanes[i].name);
        name.appendChild(badge);
      }
      name.appendChild(document.createTextNode(lanes[i].name));
      name.title = lanes[i].name;
      holder.appendChild(name);
    }
  }

  /* Side-by-side layout for overlapping events: returns [{ev, colIndex, totalCols}]. */
  function layoutItems(events) {
    var sorted = events.slice().sort(function (a, b) {
      var d = new Date(a.start_time) - new Date(b.start_time);
      if (d !== 0) return d;
      return new Date(b.end_time) - new Date(a.end_time);
    });
    var items = [];
    for (var j = 0; j < sorted.length; j++) {
      var stD = new Date(sorted[j].start_time);
      var enD = new Date(sorted[j].end_time);
      items.push({
        ev: sorted[j],
        startMin: stD.getHours() * 60 + stD.getMinutes(),
        endMin: enD.getHours() * 60 + enD.getMinutes()
      });
    }

    // split into independent clusters (connected overlap groups)
    var clusters = [];
    var idx = 0;
    while (idx < items.length) {
      var cluster = [items[idx]];
      var clusterEnd = items[idx].endMin;
      idx++;
      while (idx < items.length && items[idx].startMin < clusterEnd) {
        cluster.push(items[idx]);
        if (items[idx].endMin > clusterEnd) clusterEnd = items[idx].endMin;
        idx++;
      }
      clusters.push(cluster);
    }

    var out = [];
    for (var gc = 0; gc < clusters.length; gc++) {
      var clItems = clusters[gc];
      var subCols = [];
      var assignments = [];
      for (var k = 0; k < clItems.length; k++) {
        var it = clItems[k];
        var placed = false;
        for (var sc = 0; sc < subCols.length; sc++) {
          var fits = true;
          for (var p = 0; p < subCols[sc].length; p++) {
            if (it.startMin < subCols[sc][p].endMin && it.endMin > subCols[sc][p].startMin) {
              fits = false;
              break;
            }
          }
          if (fits) {
            subCols[sc].push(it);
            assignments.push({ ev: it.ev, colIndex: sc });
            placed = true;
            break;
          }
        }
        if (!placed) {
          subCols.push([it]);
          assignments.push({ ev: it.ev, colIndex: subCols.length - 1 });
        }
      }
      for (var a = 0; a < assignments.length; a++) {
        out.push({ ev: assignments[a].ev, colIndex: assignments[a].colIndex, totalCols: subCols.length });
      }
    }
    return out;
  }

  /* An appointment that overlaps the same mechanic's actual timer session on
     another work order is a plan that did not happen that way - flag it. */
  function markConflicts(colEvents) {
    for (var i = 0; i < colEvents.length; i++) {
      var ev = colEvents[i];
      ev._conflict = null;
      if (isVirtual(ev) || !ev.mechanic_id) continue;
      var st = new Date(ev.start_time), en = new Date(ev.end_time);
      for (var j = 0; j < colEvents.length; j++) {
        var other = colEvents[j];
        if (!isVirtual(other) || other.source !== "time_logs") continue;
        if (other.work_order_id && ev.work_order_id && other.work_order_id === ev.work_order_id) continue;
        if (eventMechanicIds(other).indexOf(String(ev.mechanic_id)) === -1) continue;
        var ost = new Date(other.start_time), oen = new Date(other.end_time);
        if (ost < en && oen > st) { ev._conflict = other; break; }
      }
    }
  }

  function updateMechanicHeaderCounts() {
    if (viewMode !== "mechanics") return;
    var columns = getColumns();
    for (var i = 0; i < columns.length; i++) {
      var sub = container.querySelector('[data-count-for="' + columns[i].key + '"]');
      if (!sub) continue;
      var seen = {};
      columnEvents(columns[i], cachedEvents).forEach(function (e) { seen[e.work_order_id || e.id] = true; });
      var n = Object.keys(seen).length;
      var pending = columnChips(columns[i], cachedEvents).length;
      var role = columns[i].role ? columns[i].role.replace(/_/g, " ") : "";
      sub.textContent = (role ? role + " · " : "") + n + (n === 1 ? " job" : " jobs") +
        (pending ? " · " + pending + " not scheduled" : "");
    }
  }

  function renderSingleEvent(ev, col, statusColors, leftPct, widthPct) {
    var st = new Date(ev.start_time);
    var en = new Date(ev.end_time);
    // a fact = one mechanic's timer session; a running one keeps growing
    // with the clock (the server's end is "now" at load time)
    var isFact = isVirtual(ev) && ev.source === "time_logs";
    var nowDt = new Date();
    if (ev.running && isSameDay(st, nowDt) && nowDt > en) en = nowDt;

    var startH = st.getHours() + st.getMinutes() / 60;
    var endHr  = en.getHours() + en.getMinutes() / 60;
    if (endHr <= HOURS_START || startH >= HOURS_END) return;

    var topClamped = Math.max(startH, HOURS_START);
    var botClamped = Math.min(endHr, HOURS_END);
    var topPx  = (topClamped - HOURS_START) * SLOT_HEIGHT;
    var height = (botClamped - topClamped) * SLOT_HEIGHT;
    if (height < 12) height = 12;

    var isWO = isWorkOrderEvent(ev);
    var color = eventColor(ev, statusColors);

    var block = document.createElement("div");
    block.className = "cal-event";
    if (isWO) block.classList.add("cal-event--wo");
    if (isWO && !isVirtual(ev)) block.classList.add("cal-event--linked");
    if (ev.running) block.classList.add("cal-event--running");
    if (isFact) block.classList.add("cal-event--fact");
    if (ev.running) {
      block.setAttribute("data-running", "1");
      block.setAttribute("data-start-h", String(startH));
      block.setAttribute("data-day", toISODate(st));
    }
    block.style.top = topPx + "px";
    block.style.height = height + "px";
    block.style.backgroundColor = color;

    // side-by-side layout for overlaps
    block.style.left = leftPct + "%";
    block.style.width = widthPct + "%";
    if (ev._conflict) block.classList.add("cal-event--conflict");

    // whose block: coloured left edge + initials of the mechanic(s)
    var mechList = eventMechanicList(ev);
    if (mechList.length) {
      block.style.boxShadow = "inset 5px 0 0 " + mechanicColor(mechList[0].id) + ", 0 1px 2px rgba(0,0,0,.2)";
    }

    var line1, line2Parts = [], line3 = "";
    var mechanicNames = eventMechanicNames(ev);
    if (isWO) {
      var woLabel = ev.wo_number ? "WO #" + ev.wo_number : "Work order";
      line1 = woLabel + (ev.customer_label ? " · " + ev.customer_label : "");
      if (ev.unit_label) line2Parts.push(ev.unit_label);
      if (viewMode !== "mechanics" && mechanicNames.length) line2Parts.push(mechanicNames.join(", "));
      if (Array.isArray(ev.labor_summary) && ev.labor_summary.length) line3 = ev.labor_summary.join(", ");
      else if (ev.title && ev.title !== woLabel) line3 = ev.title;
    } else {
      line1 = ev.customer_label || ev.title || "Appointment";
      if (ev.unit_label) line2Parts.push(ev.unit_label);
      if (viewMode !== "mechanics" && mechanicNames.length) line2Parts.push(mechanicNames.join(", "));
      if (ev.title && ev.title !== line1) line3 = ev.title;
    }

    var html = '<div class="cal-event-title">' + escapeHtml(line1) + '</div>';
    if (line2Parts.length) html += '<div class="cal-event-sub">' + escapeHtml(line2Parts.join(" · ")) + '</div>';
    if (line3) html += '<div class="cal-event-note">' + escapeHtml(line3) + '</div>';
    if (isWO && ev.wo_state) {
      var info = woStateInfo(ev.wo_state);
      html += '<span class="cal-event-chip">' + escapeHtml(info.label) + (ev.running ? ' ●' : '') + '</span>';
    }
    if (mechList.length) {
      html += '<span class="cal-event-mechs">';
      for (var mi = 0; mi < Math.min(mechList.length, 3); mi++) {
        html += '<span class="cal-mech-badge" style="background:' + escapeHtml(mechanicColor(mechList[mi].id)) + '" title="' +
          escapeHtml(mechList[mi].name || "") + '">' + escapeHtml(mechanicInitials(mechList[mi].name)) + '</span>';
      }
      html += '</span>';
    }
    if (ev._conflict) {
      html += '<span class="cal-event-warn" title="Overlaps this mechanic&#39;s actual work on ' +
        escapeHtml(ev._conflict.wo_number ? "WO #" + ev._conflict.wo_number : "another work order") + '">!</span>';
    }
    block.innerHTML = html;
    block.title = buildTooltip(ev, line1);

    (function (evt, blk) {
      blk.addEventListener("click", function (e) {
        e.stopPropagation();
        if (dragJustEnded) return;
        if (isVirtual(evt)) openWoModal(evt);
        else openModalForEdit(evt);
      });
      // Facts (timer sessions) cannot be moved. Plans need the edit right;
      // putting a work order onto the grid needs the create right.
      var draggable = !isFact && (isVirtual(evt) ? canCreate : canEdit);
      if (draggable) {
        blk.addEventListener("mousedown", function (e) {
          if (e.button !== 0) return;
          e.preventDefault();
          e.stopPropagation();
          startDrag(evt, blk, e);
        });
      } else {
        blk.classList.add("cal-event--static");
      }
    })(ev, block);

    col.appendChild(block);
  }

  function buildTooltip(ev, line1) {
    var parts = [line1];
    var st = new Date(ev.start_time), en = new Date(ev.end_time);
    parts.push(fmtTime(st) + "–" + fmtTime(en));
    var names = eventMechanicNames(ev);
    if (names.length) parts.push("Mechanics: " + names.join(", "));
    if (isWorkOrderEvent(ev) && ev.wo_state) parts.push("Status: " + woStateInfo(ev.wo_state).label);
    if (Array.isArray(ev.worked) && ev.worked.length) {
      parts.push("Worked: " + ev.worked.map(function (u) {
        return u.name + " " + fmtDuration(u.seconds) + (u.running ? " (now)" : "");
      }).join(", "));
    }
    if (ev._conflict) {
      var c = ev._conflict;
      parts.push("! Planned here, but the mechanic was actually working on " +
        (c.wo_number ? "WO #" + c.wo_number : "another work order") + " " +
        fmtTime(new Date(c.start_time)) + "-" + fmtTime(new Date(c.end_time)));
    }
    return parts.join("\n");
  }

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.appendChild(document.createTextNode(str == null ? "" : String(str)));
    return div.innerHTML;
  }

  /* ── drag & drop ── */

  function snapTo15(minutes) {
    return Math.round(minutes / STEP_MINUTES) * STEP_MINUTES;
  }

  function startDrag(evt, blockEl, mouseEvt) {
    var bodyCols = container.querySelector(".cal-body-cols");
    if (!bodyCols) return;
    var cols = bodyCols.querySelectorAll(".cal-day-col");

    var st = new Date(evt.start_time);
    var en = new Date(evt.end_time);
    var durationMin = Math.max(1, Math.round((en - st) / 60000));

    var origColIdx = 0;
    for (var i = 0; i < cols.length; i++) {
      if (cols[i] === blockEl.parentNode) { origColIdx = i; break; }
    }

    drag = {
      evt: evt,
      el: blockEl,
      durationMin: durationMin,
      startX: mouseEvt.clientX,
      startY: mouseEvt.clientY,
      origTop: parseFloat(blockEl.style.top),
      origColIdx: origColIdx,
      currentColIdx: origColIdx,
      cols: cols,
      inGrid: true,
      moved: false
    };

    blockEl.classList.add("cal-dragging");
    document.addEventListener("mousemove", onDragMove);
    document.addEventListener("mouseup", onDragEnd);
  }

  /* Drag a not-yet-scheduled work order from the strip onto the grid: a
     temporary block appears under the cursor and follows the normal drag
     flow; dropping it creates a linked appointment. */
  function startChipDrag(evt, mouseEvt) {
    var bodyCols = container.querySelector(".cal-body-cols");
    if (!bodyCols) return;
    var cols = bodyCols.querySelectorAll(".cal-day-col");
    if (!cols.length) return;

    var st = new Date(evt.start_time);
    var en = new Date(evt.end_time);
    var durationMin = Math.max(STEP_MINUTES, Math.round((en - st) / 60000) || 60);

    // start in the column under the cursor (or the first one)
    var colIdx = 0;
    for (var i = 0; i < cols.length; i++) {
      var cr = cols[i].getBoundingClientRect();
      if (mouseEvt.clientX >= cr.left && mouseEvt.clientX < cr.right) { colIdx = i; break; }
    }
    var colEl = cols[colIdx];
    var rect = colEl.getBoundingClientRect();
    // the chip sits above the grid, so its own offset starts negative; the
    // unclamped value is what lets the block follow the cursor 1:1
    var rawTop = snapTo15((mouseEvt.clientY - rect.top) / PX_PER_MINUTE) * PX_PER_MINUTE;
    var maxTop = (HOURS_END - HOURS_START) * SLOT_HEIGHT - durationMin * PX_PER_MINUTE;
    var top = Math.max(0, Math.min(rawTop, maxTop));

    var info = woStateInfo(evt.wo_state);
    var block = document.createElement("div");
    block.className = "cal-event cal-event--wo cal-event--ghost";
    block.style.top = top + "px";
    block.style.height = Math.max(12, durationMin * PX_PER_MINUTE) + "px";
    block.style.left = "0";
    block.style.width = "100%";
    block.style.backgroundColor = info.color;
    block.innerHTML = '<div class="cal-event-title">' + escapeHtml((evt.wo_number ? "WO #" + evt.wo_number : "Work order") +
      (evt.customer_label ? " · " + evt.customer_label : "")) + '</div>' +
      '<div class="cal-event-sub">Drop to schedule</div>';
    colEl.appendChild(block);

    startDrag(evt, block, mouseEvt);
    if (drag) {
      drag.fromChip = true;
      drag.origTop = rawTop;
      drag.startY = mouseEvt.clientY;
      drag.inGrid = false;
    }
  }

  function onDragMove(e) {
    if (!drag) return;
    var dx = e.clientX - drag.startX;
    var dy = e.clientY - drag.startY;
    if (Math.abs(dy) > 3 || Math.abs(dx) > 3) drag.moved = true;

    // vertical: snap to 15 min
    var newTop = drag.origTop + dy;
    var maxTop = (HOURS_END - HOURS_START) * SLOT_HEIGHT - parseFloat(drag.el.style.height);
    newTop = Math.max(0, Math.min(newTop, maxTop));
    var snappedMin = snapTo15(newTop / PX_PER_MINUTE);
    newTop = snappedMin * PX_PER_MINUTE;
    drag.el.style.top = newTop + "px";

    // horizontal: detect column under cursor & move block
    var colIdx = -1;
    for (var i = 0; i < drag.cols.length; i++) {
      var cr = drag.cols[i].getBoundingClientRect();
      if (e.clientX >= cr.left && e.clientX < cr.right) { colIdx = i; break; }
    }
    if (colIdx >= 0 && colIdx !== drag.currentColIdx) {
      drag.cols[colIdx].appendChild(drag.el);
      drag.currentColIdx = colIdx;
    }
    // Is the cursor over the time grid at all? A chip dragged around the
    // strip and released there must not be scheduled at 06:00.
    var gridRect = drag.cols[drag.currentColIdx].getBoundingClientRect();
    drag.inGrid = colIdx >= 0 && e.clientY >= gridRect.top && e.clientY <= gridRect.bottom;
  }

  function onDragEnd(e) {
    document.removeEventListener("mousemove", onDragMove);
    document.removeEventListener("mouseup", onDragEnd);
    if (!drag) return;

    drag.el.classList.remove("cal-dragging");

    if (!drag.moved || (drag.fromChip && !drag.inGrid)) {
      if (drag.fromChip) {
        // a plain click on a chip (or a drag that never reached the grid):
        // nothing is scheduled; a click shows the work order instead
        var chipEvt = drag.evt;
        var wasClick = !drag.moved;
        drag.el.remove();
        drag = null;
        if (wasClick) openWoModal(chipEvt);
        return;
      }
      drag = null;
      return;
    }

    // suppress clicks briefly after drag
    dragJustEnded = true;
    setTimeout(function () { dragJustEnded = false; }, 300);

    var topPx = parseFloat(drag.el.style.top);
    var totalMin = snapTo15(topPx / PX_PER_MINUTE);
    var newHour = HOURS_START + Math.floor(totalMin / 60);
    var newMin = totalMin % 60;

    var columns = getColumns();
    var targetCol = columns[drag.currentColIdx] || columns[drag.origColIdx];
    var newDate = targetCol ? targetCol.date : addDays(weekStart, 0);
    var startISO = toISODate(newDate) + "T" + pad2(newHour) + ":" + pad2(newMin) + ":00";
    // whole minutes, never past the end of the grid
    var gridMinutes = (HOURS_END - HOURS_START) * 60;
    var endTotalMin = Math.min(Math.round(totalMin + drag.durationMin), gridMinutes);
    if (endTotalMin <= totalMin) endTotalMin = Math.min(totalMin + STEP_MINUTES, gridMinutes);
    var endH = HOURS_START + Math.floor(endTotalMin / 60);
    var endM = endTotalMin % 60;
    var endISO = toISODate(newDate) + "T" + pad2(endH) + ":" + pad2(endM) + ":00";

    var evt = drag.evt;
    var dropLane = null;
    if (viewMode === "week" && targetCol && targetCol.dayIndex === expandedDayIndex && e && typeof e.clientX === "number") {
      // expanded day: the lane under the cursor says who the plan is for
      var lanes = dayLanes(columnEvents(targetCol, cachedEvents));
      var colRect = drag.cols[drag.currentColIdx].getBoundingClientRect();
      if (lanes.length && colRect.width > 0) {
        var laneIdx = Math.floor((e.clientX - colRect.left) / colRect.width * lanes.length);
        dropLane = lanes[Math.max(0, Math.min(laneIdx, lanes.length - 1))];
      }
    }
    drag = null;

    var payload = { start_time: startISO, end_time: endISO };
    var target = null;
    if (viewMode === "mechanics" && targetCol) {
      target = targetCol.unassigned ? { key: UNASSIGNED_KEY } : { key: String(targetCol.mechanicId), name: targetCol.mechanicName };
    } else if (dropLane) {
      target = dropLane;
    }
    if (target) {
      // Dropped into a mechanic column / lane - that mechanic gets the plan.
      if (target.key === UNASSIGNED_KEY) {
        payload.mechanic_id = "";
        payload.mechanic_name = "";
        payload.keep_unassigned = true;
      } else {
        payload.mechanic_id = target.key;
        payload.mechanic_name = target.name;
      }
    }

    var req;
    if (isVirtual(evt)) {
      // A work order dragged onto the grid becomes a scheduled, linked appointment.
      payload.work_order_id = evt.work_order_id;
      req = sendJSON("/calendar/api/events", "POST", payload);
    } else {
      req = sendJSON("/calendar/api/events/" + evt.id, "PUT", payload);
    }
    req
      .then(function () { loadEvents(); })
      .catch(function (e) { notify(e.message || "Could not move the event.", "error"); loadEvents(); });
  }

  /* ── now line ── */

  function updateNowLine() {
    var grid = container.querySelector(".cal-body-cols");
    if (!grid) return;

    var old = grid.querySelectorAll(".cal-now-line");
    for (var i = 0; i < old.length; i++) old[i].remove();

    var now = new Date();
    var hours = now.getHours();
    var mins  = now.getMinutes();
    if (hours < HOURS_START || hours >= HOURS_END) return;
    var pxFromTop = (hours - HOURS_START) * SLOT_HEIGHT + (mins / 60) * SLOT_HEIGHT;

    var columns = getColumns();
    var colEls = grid.querySelectorAll(".cal-day-col");
    for (var c = 0; c < columns.length; c++) {
      if (!isSameDay(columns[c].date, now)) continue;
      var col = colEls[c];
      if (!col) continue;
      var line = document.createElement("div");
      line.className = "cal-now-line";
      line.style.top = pxFromTop + "px";
      col.appendChild(line);
      if (viewMode !== "mechanics") break;
    }
  }

  /* Running timers: the block's bottom edge follows the clock until the
     next reload (otherwise it would freeze at the load time). */
  function stretchRunningBlocks() {
    var now = new Date();
    var nowH = now.getHours() + now.getMinutes() / 60;
    var blocks = container.querySelectorAll(".cal-event[data-running]");
    for (var i = 0; i < blocks.length; i++) {
      var b = blocks[i];
      if (b.getAttribute("data-day") !== toISODate(now)) continue;
      var startH = Math.max(parseFloat(b.getAttribute("data-start-h")) || HOURS_START, HOURS_START);
      var bot = Math.min(Math.max(nowH, startH), HOURS_END);
      var h = (bot - startH) * SLOT_HEIGHT;
      if (h > (parseFloat(b.style.height) || 0)) b.style.height = Math.max(12, h) + "px";
    }
  }

  function startNowLineTimer() {
    if (nowLineTimer) clearInterval(nowLineTimer);
    nowLineTimer = setInterval(function () { updateNowLine(); stretchRunningBlocks(); }, 60000);
  }

  /* ── layout / expand ── */

  /* Header cell, time column and the "Not scheduled" cell of a day always
     share the same width classes: normal / wide (level 1) / full (level 2)
     for the expanded day, collapsed for the others. */
  function applyLayout() {
    if (viewMode === "mechanics") return;
    var headerCells = container.querySelectorAll(".cal-header-cell");
    var bodyCols    = container.querySelectorAll(".cal-day-col");
    var chipCells   = container.querySelectorAll(".cal-unscheduled-cell");

    var isExpanded = expandedDayIndex >= 0 && expandedDayIndex < 7;

    for (var i = 0; i < 7; i++) {
      var els = [headerCells[i], bodyCols[i], chipCells[i]];
      for (var k = 0; k < els.length; k++) {
        var el = els[k];
        if (!el) continue;
        el.classList.remove("cal-expanded", "cal-expanded--full", "cal-collapsed");
        if (!isExpanded) continue;
        if (i === expandedDayIndex) {
          el.classList.add("cal-expanded");
          if (expandLevel >= 2) el.classList.add("cal-expanded--full");
        } else {
          el.classList.add("cal-collapsed");
        }
      }
    }
  }

  /* Click on a day header: 1st - wide day with mechanic lanes, 2nd - the
     day takes almost the whole grid (other days become narrow strips),
     3rd - back to the normal week. */
  function toggleExpandDay(idx) {
    if (expandedDayIndex !== idx) {
      expandedDayIndex = idx;
      expandLevel = 1;
    } else if (expandLevel === 1) {
      expandLevel = 2;
    } else {
      expandedDayIndex = -1;
      expandLevel = 0;
    }
    applyLayout();
    renderEvents();
  }

  /* ── searchable dropdown helper ── */
  function bindSearchable(searchInput, dropDiv, hiddenInput, items, valueFn, labelFn, onSelect) {
    var list = items.map(function (item) {
      return { value: valueFn(item), label: labelFn(item) };
    });

    // Remove old listeners by cloning the search input
    var newInput = searchInput.cloneNode(true);
    searchInput.parentNode.replaceChild(newInput, searchInput);

    // Update DOM references
    if (searchInput === elCustomerSearch) elCustomerSearch = newInput;
    else if (searchInput === elUnitSearch) elUnitSearch = newInput;
    else if (searchInput === elMechanicSearch) elMechanicSearch = newInput;
    else if (searchInput === elPresetSearch) elPresetSearch = newInput;

    function renderList(filtered) {
      dropDiv.innerHTML = "";
      for (var i = 0; i < filtered.length; i++) {
        (function (entry) {
          var a = document.createElement("a");
          a.href = "#";
          a.className = "list-group-item list-group-item-action py-1 px-2";
          a.style.fontSize = "0.9em";
          a.textContent = entry.label;
          a.addEventListener("mousedown", function (e) {
            e.preventDefault();
            hiddenInput.value = entry.value;
            newInput.value = entry.label;
            dropDiv.style.display = "none";
            if (onSelect) onSelect(entry.value, entry.label);
          });
          dropDiv.appendChild(a);
        })(filtered[i]);
      }
      dropDiv.style.display = filtered.length ? "block" : "none";
    }

    newInput.addEventListener("input", function () {
      var q = this.value.toLowerCase();
      if (!q) {
        hiddenInput.value = "";
        renderList(list);
        return;
      }
      var filtered = list.filter(function (entry) {
        return entry.label.toLowerCase().indexOf(q) !== -1;
      });
      renderList(filtered);
    });

    newInput.addEventListener("focus", function () {
      var q = this.value.toLowerCase();
      var filtered = q ? list.filter(function (e) { return e.label.toLowerCase().indexOf(q) !== -1; }) : list;
      renderList(filtered);
    });

    newInput.addEventListener("blur", function () {
      setTimeout(function () { dropDiv.style.display = "none"; }, 150);
    });

    return newInput;
  }

  function clearSearchable(searchInput, hiddenInput, dropDiv) {
    searchInput.value = "";
    hiddenInput.value = "";
    dropDiv.innerHTML = "";
    dropDiv.style.display = "none";
  }

  function buildStatusButtons(selectedKey) {
    elStatusGrp.innerHTML = "";
    for (var i = 0; i < cachedStatuses.length; i++) {
      var st = cachedStatuses[i];
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-sm cal-status-btn";
      btn.setAttribute("data-status", st.key);
      btn.style.borderColor = st.color;
      btn.style.color = st.color;
      btn.textContent = st.label;
      if (st.key === selectedKey) {
        btn.style.backgroundColor = st.color;
        btn.style.color = "#fff";
        btn.classList.add("active");
      }
      btn.addEventListener("click", function () {
        var allBtns = elStatusGrp.querySelectorAll(".cal-status-btn");
        for (var j = 0; j < allBtns.length; j++) {
          var sColor = allBtns[j].style.borderColor;
          allBtns[j].classList.remove("active");
          allBtns[j].style.backgroundColor = "transparent";
          allBtns[j].style.color = sColor;
        }
        this.classList.add("active");
        this.style.backgroundColor = this.style.borderColor;
        this.style.color = "#fff";
      });
      elStatusGrp.appendChild(btn);
    }
  }

  function getSelectedStatus() {
    var active = elStatusGrp.querySelector(".cal-status-btn.active");
    return active ? active.getAttribute("data-status") : "scheduled";
  }

  function loadUnitsForCustomer(customerId) {
    clearSearchable(elUnitSearch, elUnit, elUnitDrop);
    if (!customerId) {
      elUnitSearch.disabled = true;
      return;
    }
    elUnitSearch.disabled = false;
    loadUnits(customerId).then(function (units) {
      bindSearchable(elUnitSearch, elUnitDrop, elUnit, units,
        function (u) { return u.id; },
        function (u) { return u.label; },
        function () { updateCreateWOBtn(); }
      );
    }).catch(function () { notify("Could not load units.", "error"); });
  }

  function mechanicById(id) {
    for (var i = 0; i < cachedMechanics.length; i++) {
      if (String(cachedMechanics[i].id) === String(id)) return cachedMechanics[i];
    }
    return null;
  }

  function renderLinkedPanel(ev) {
    if (!elLinkedWO) return;
    var linked = !!(ev && ev.work_order_id && !unlinkRequested);
    elLinkedWO.classList.toggle("d-none", !linked);
    if (!linked) return;
    var info = woStateInfo(ev.wo_state);
    elLinkedWONumber.textContent = ev.wo_number ? "WO #" + ev.wo_number : "work order";
    elLinkedWOState.textContent = info.label;
    elLinkedWOState.style.backgroundColor = info.color;
    elLinkedWOState.style.color = "#fff";
    var summaryParts = [];
    if (Array.isArray(ev.labor_summary) && ev.labor_summary.length) summaryParts.push(ev.labor_summary.join(", "));
    var names = eventMechanicNames(ev);
    if (names.length) summaryParts.push("Mechanics: " + names.join(", "));
    elLinkedWOSummary.textContent = summaryParts.join(" · ");
    elLinkedWOOpen.href = woPageUrl(ev.work_order_id);
  }

  function openModal(existingEvent, dayIndex, hour, presetMechanicId) {
    var isEdit = !!existingEvent;
    unlinkRequested = false;
    modalTitle.textContent = isEdit ? "Edit Appointment" : "New Appointment";
    elEventId.value = isEdit ? existingEvent.id : "";
    elWorkOrderId.value = isEdit && existingEvent.work_order_id ? existingEvent.work_order_id : "";
    elDelBtn.classList.toggle("d-none", !isEdit);
    renderLinkedPanel(isEdit ? existingEvent : null);

    // bind searchable dropdowns
    bindSearchable(elCustomerSearch, elCustomerDrop, elCustomer, cachedCustomers,
      function (c) { return c.id; },
      function (c) { return c.label; },
      function (val) { loadUnitsForCustomer(val); updateCreateWOBtn(); }
    );
    // clear units when customer text is manually edited
    elCustomerSearch.addEventListener("input", function () {
      if (!elCustomer.value) {
        clearSearchable(elUnitSearch, elUnit, elUnitDrop);
        elUnitSearch.disabled = true;
        updateCreateWOBtn();
      }
    });

    bindSearchable(elMechanicSearch, elMechanicDrop, elMechanic, cachedMechanics,
      function (m) { return m.id; },
      function (m) { return m.name + (m.role ? " (" + m.role.replace(/_/g, " ") + ")" : ""); },
      null
    );

    // presets searchable
    bindSearchable(elPresetSearch, elPresetDrop, elPresetPendingId, cachedPresets,
      function (p) { return p.id; },
      function (p) { return p.name; },
      function () { elPresetAddBtn.disabled = false; }
    );

    if (isEdit) {
      // customer
      elCustomer.value = existingEvent.customer_id || "";
      elCustomerSearch.value = existingEvent.customer_label || "";
      if (existingEvent.customer_id) {
        loadUnits(existingEvent.customer_id).then(function (units) {
          bindSearchable(elUnitSearch, elUnitDrop, elUnit, units,
            function (u) { return u.id; },
            function (u) { return u.label; },
            function () { updateCreateWOBtn(); }
          );
          elUnitSearch.disabled = false;
          elUnit.value = existingEvent.unit_id || "";
          elUnitSearch.value = existingEvent.unit_label || "";
          updateCreateWOBtn();
        }).catch(function () { notify("Could not load units.", "error"); });
      } else {
        elUnitSearch.disabled = true;
      }

      var stDt = new Date(existingEvent.start_time);
      var enDt = new Date(existingEvent.end_time);
      elDate.value = toISODate(stDt);
      // the selects only know 15-minute steps inside the grid hours
      var snapped = snapRange(stDt.getHours() * 60 + stDt.getMinutes(), enDt.getHours() * 60 + enDt.getMinutes());
      elStart.value = minutesToValue(snapped[0]);
      elEnd.value = minutesToValue(snapped[1]);

      elMechanic.value = existingEvent.mechanic_id || "";
      var mech = existingEvent.mechanic_id ? mechanicById(existingEvent.mechanic_id) : null;
      elMechanicSearch.value = mech ? mech.name : (existingEvent.mechanic_name || "");

      elTitleInp.value = existingEvent.title || "";
      selectedPresets = (existingEvent.presets || []).slice();
      renderPresetTags();
      buildStatusButtons(existingEvent.status || "scheduled");
    } else {
      clearSearchable(elCustomerSearch, elCustomer, elCustomerDrop);
      clearSearchable(elUnitSearch, elUnit, elUnitDrop);
      clearSearchable(elMechanicSearch, elMechanic, elMechanicDrop);
      clearSearchable(elPresetSearch, elPresetPendingId, elPresetDrop);
      elPresetAddBtn.disabled = true;
      elUnitSearch.disabled = true;

      var dayDate = addDays(weekStart, dayIndex);
      elDate.value = toISODate(dayDate);
      elStart.value = pad2(hour) + ":00";
      var endHour = Math.min(hour + 1, HOURS_END);
      elEnd.value = pad2(endHour) + ":00";
      elTitleInp.value = "";
      selectedPresets = [];
      renderPresetTags();
      buildStatusButtons("scheduled");

      var preMech = presetMechanicId ? mechanicById(presetMechanicId) : null;
      if (preMech) {
        elMechanic.value = preMech.id;
        elMechanicSearch.value = preMech.name;
      }
    }

    updateCreateWOBtn();
    getModal().show();
  }

  function updateCreateWOBtn() {
    if (!elCreateWOBtn) return;
    var hasCustomer = !!elCustomer.value;
    var hasUnit = !!elUnit.value;
    var alreadyLinked = !!elWorkOrderId.value && !unlinkRequested;
    elCreateWOBtn.classList.toggle("d-none", !(canCreateWO && hasCustomer && hasUnit && !alreadyLinked));
  }

  function openModalForEdit(ev) {
    openModal(ev, 0, 0, null);
  }

  /* New appointment prefilled from a work order block or chip: linked to
     the WO, customer/unit carried over, startMin + planned hours as the
     time, the given mechanic. The user picks the final time and saves. */
  function openModalForWorkOrder(ev, dayDate, startMin, mechanicId) {
    var dayIdx = dayIndexOf(dayDate);
    openModal(null, dayIdx < 0 ? 0 : dayIdx, HOURS_START, mechanicId || null);
    elDate.value = toISODate(dayDate);
    elWorkOrderId.value = ev.work_order_id || "";
    renderLinkedPanel(ev);
    elCustomer.value = ev.customer_id || "";
    elCustomerSearch.value = ev.customer_label || "";
    if (ev.customer_id) {
      elUnitSearch.disabled = false;
      elUnit.value = ev.unit_id || "";
      elUnitSearch.value = ev.unit_label || "";
      loadUnits(ev.customer_id).then(function (units) {
        bindSearchable(elUnitSearch, elUnitDrop, elUnit, units,
          function (u) { return u.id; },
          function (u) { return u.label; },
          function () { updateCreateWOBtn(); }
        );
        elUnit.value = ev.unit_id || "";
        elUnitSearch.value = ev.unit_label || "";
        updateCreateWOBtn();
      }).catch(function () { notify("Could not load units.", "error"); });
    }
    var hours = Number(ev.labor_hours) > 0 ? Math.min(Number(ev.labor_hours), 8) : 1;
    var planned = Math.max(STEP_MINUTES, Math.ceil(hours * 60 / STEP_MINUTES) * STEP_MINUTES);
    var range = snapRange(startMin, startMin + planned);
    elStart.value = minutesToValue(range[0]);
    elEnd.value = minutesToValue(range[1]);
    if (Array.isArray(ev.labor_summary) && ev.labor_summary.length) elTitleInp.value = ev.labor_summary.join(", ");
    updateCreateWOBtn();
  }

  if (elUnlinkWOBtn) {
    elUnlinkWOBtn.addEventListener("click", function () {
      unlinkRequested = true;
      renderLinkedPanel(null);
      updateCreateWOBtn();
      notify("The link will be removed when you save.", "info");
    });
  }

  /* ── WO modal ── */

  function openWoModal(ev) {
    currentWoEvent = ev;
    var info = woStateInfo(ev.wo_state);
    woModalTitle.textContent = (ev.wo_number ? "WO #" + ev.wo_number : "Work order") + (ev.customer_label ? " · " + ev.customer_label : "");
    woStateEl.textContent = info.label;
    woStateEl.style.backgroundColor = info.color;
    woStateEl.style.color = "#fff";

    var st = new Date(ev.start_time), en = new Date(ev.end_time);
    var when = DAY_NAMES_LONG[(st.getDay() + 6) % 7] + ", " + formatDateShort(st);
    if (ev.unscheduled) when += " · not scheduled";
    else when += " · " + fmtTime(st) + "–" + fmtTime(en) + (ev.source === "time_logs" ? " (from the mechanic's timer)" : "");
    woWhenEl.textContent = when;

    woCustomerEl.textContent = ev.customer_label || "—";
    woUnitEl.textContent = ev.unit_label || "—";

    woMechanicsEl.innerHTML = "";
    if (Array.isArray(ev.worked) && ev.worked.length) {
      ev.worked.forEach(function (u) {
        var row = document.createElement("div");
        row.textContent = u.name + " — " + fmtDuration(u.seconds) + (u.running ? " (working now)" : "");
        woMechanicsEl.appendChild(row);
      });
    } else {
      var names = eventMechanicNames(ev);
      woMechanicsEl.textContent = names.length ? names.join(", ") : "Not assigned";
    }

    woLaborsEl.innerHTML = "";
    if (Array.isArray(ev.labor_summary) && ev.labor_summary.length) {
      ev.labor_summary.forEach(function (text) {
        var row = document.createElement("div");
        row.textContent = text;
        woLaborsEl.appendChild(row);
      });
    } else {
      woLaborsEl.textContent = "—";
    }
    woHoursEl.textContent = ev.labor_hours ? ev.labor_hours + " h" : "—";
    renderWoPlans(ev);
    woHintEl.textContent = ev.source === "time_logs"
      ? "This block is one mechanic's actual work session on the job (from the timer). It cannot be moved; \"Add to schedule\" plans a follow-up slot for this mechanic."
      : "This work order has no scheduled time and no tracked work yet - it sits in the \"Not scheduled\" strip on its work order date. Drag it onto the grid or press Add to schedule to plan it.";
    woOpenBtn.href = woPageUrl(ev.work_order_id);
    getWoModal().show();
  }

  /* Appointments linked to this WO on the same day — including plans that
     were superseded by actual work (hidden from the grid, editable here). */
  function renderWoPlans(ev) {
    var holder = document.getElementById("woEventPlans");
    if (!holder) return;
    holder.innerHTML = "";
    var day = new Date(ev.start_time);
    var plans = cachedEvents.filter(function (e) {
      return !isVirtual(e) && e.work_order_id && e.work_order_id === ev.work_order_id &&
        e.start_time && isSameDay(new Date(e.start_time), day);
    });
    if (!plans.length) { holder.classList.add("d-none"); return; }
    holder.classList.remove("d-none");
    var title = document.createElement("div");
    title.className = "small text-muted mb-1";
    title.textContent = "Planned for this day:";
    holder.appendChild(title);
    plans.forEach(function (p) {
      var row = document.createElement("div");
      row.className = "d-flex align-items-center justify-content-between gap-2 cal-wo-plan";
      var st = new Date(p.start_time), en = new Date(p.end_time);
      var text = document.createElement("span");
      text.className = "small";
      text.textContent = fmtTime(st) + "–" + fmtTime(en) + (p.mechanic_name ? " · " + p.mechanic_name : "") +
        (p.superseded ? " · hidden: replaced by actual work" : "");
      row.appendChild(text);
      var btns = document.createElement("span");
      btns.className = "d-flex gap-1";
      if (canEdit) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-sm btn-outline-secondary";
        btn.textContent = "Edit";
        btn.addEventListener("click", function () {
          getWoModal().hide();
          openModalForEdit(p);
        });
        btns.appendChild(btn);
      }
      if (canDelete) {
        var del = document.createElement("button");
        del.type = "button";
        del.className = "btn btn-sm btn-outline-danger";
        del.textContent = "Delete";
        del.addEventListener("click", function () {
          var proceed = window.appConfirm
            ? window.appConfirm("Delete this appointment?")
            : Promise.resolve(window.confirm("Delete this appointment?"));
          proceed.then(function (ok) {
            if (!ok) return;
            fetchJSON("/calendar/api/events/" + p.id, { method: "DELETE" })
              .then(function () { return loadEvents(); })
              .then(function () { if (currentWoEvent) renderWoPlans(currentWoEvent); })
              .catch(function (err) { notify(err.message || "Could not delete the appointment.", "error"); });
          });
        });
        btns.appendChild(del);
      }
      row.appendChild(btns);
      holder.appendChild(row);
    });
  }

  if (woScheduleBtn) {
    woScheduleBtn.classList.toggle("d-none", !canCreate);
    woScheduleBtn.addEventListener("click", function () {
      if (!currentWoEvent || !canCreate) return;
      var ev = currentWoEvent;
      var mechanicIds = eventMechanicIds(ev);
      var mech = mechanicIds.length ? mechanicById(mechanicIds[0]) : null;
      getWoModal().hide();
      if (ev.source === "time_logs") {
        // A fact already happened - plan the follow-up: the next quarter
        // after this session, for the same mechanic; the user picks the time.
        var en = new Date(ev.end_time);
        openModalForWorkOrder(ev, en, Math.ceil((en.getHours() * 60 + en.getMinutes()) / STEP_MINUTES) * STEP_MINUTES, mech ? mech.id : null);
        return;
      }
      // Not scheduled yet: plan it on its work order date from the default
      // start the server suggested (nothing is saved until the user does).
      var st = new Date(ev.start_time);
      openModalForWorkOrder(ev, st, st.getHours() * 60 + st.getMinutes(), mech ? mech.id : null);
    });
  }

  /* ── preset add / remove ── */
  function renderPresetTags() {
    elPresetTags.innerHTML = "";
    for (var i = 0; i < selectedPresets.length; i++) {
      (function (idx) {
        var p = selectedPresets[idx];
        var tag = document.createElement("span");
        tag.className = "badge bg-secondary d-inline-flex align-items-center gap-1";
        tag.style.fontSize = "0.85em";
        tag.innerHTML = escapeHtml(p.name) + ' <button type="button" class="btn-close btn-close-white" style="font-size:0.6em;" aria-label="Remove"></button>';
        tag.querySelector(".btn-close").addEventListener("click", function () {
          selectedPresets.splice(idx, 1);
          renderPresetTags();
        });
        elPresetTags.appendChild(tag);
      })(i);
    }
  }

  elPresetAddBtn.addEventListener("click", function () {
    var val = elPresetPendingId.value;
    if (!val) return;
    // don't add duplicates
    for (var i = 0; i < selectedPresets.length; i++) {
      if (selectedPresets[i].id === val) return;
    }
    selectedPresets.push({ id: val, name: elPresetSearch.value });
    renderPresetTags();
    clearSearchable(elPresetSearch, elPresetPendingId, elPresetDrop);
    elPresetAddBtn.disabled = true;
    // auto-fill title with preset names
    elTitleInp.value = selectedPresets.map(function (p) { return p.name; }).join(", ");
  });

  /* ── save ── */
  function collectPayload() {
    var dateVal = elDate.value;
    var startVal = elStart.value;
    var endVal = elEnd.value;
    if (!dateVal || !startVal || !endVal || !parseISODate(dateVal)) {
      notify("Date and time are required.", "warning");
      return null;
    }
    if (endVal <= startVal) {
      notify("End time must be after start time.", "warning");
      return null;
    }
    var mech = elMechanic.value ? mechanicById(elMechanic.value) : null;
    var payload = {
      start_time: toLocalISO(dateVal, startVal),
      end_time: toLocalISO(dateVal, endVal),
      customer_id: elCustomer.value || "",
      customer_label: elCustomer.value ? elCustomerSearch.value : "New Customer",
      unit_id: elUnit.value || "",
      unit_label: elUnit.value ? elUnitSearch.value : (elUnitSearch.disabled ? "" : "New Unit"),
      mechanic_id: elMechanic.value || "",
      mechanic_name: mech ? mech.name : (elMechanic.value ? elMechanicSearch.value : ""),
      presets: selectedPresets.slice(),
      status: getSelectedStatus(),
      title: elTitleInp.value.trim(),
    };
    if (!elEventId.value && elWorkOrderId.value) payload.work_order_id = elWorkOrderId.value;
    if (unlinkRequested) payload.work_order_id = "";
    return payload;
  }

  function saveAppointment() {
    var payload = collectPayload();
    if (!payload) return Promise.reject(new Error("invalid"));
    var eventId = elEventId.value;
    var url = eventId ? "/calendar/api/events/" + eventId : "/calendar/api/events";
    var method = eventId ? "PUT" : "POST";
    return sendJSON(url, method, payload);
  }

  elSaveBtn.addEventListener("click", function () {
    elSaveBtn.disabled = true;
    saveAppointment()
      .then(function () {
        getModal().hide();
        loadEvents();
      })
      .catch(function (e) {
        if (e && e.message !== "invalid") notify(e.message || "Could not save the appointment.", "error");
      })
      .then(function () { elSaveBtn.disabled = false; });
  });

  /* ── create work order ──
     Saves the appointment first (so the WO links back to it), then opens the
     WO page prefilled with customer, unit, presets, mechanic and note. */
  elCreateWOBtn.addEventListener("click", function () {
    var customerId = elCustomer.value;
    var unitId = elUnit.value;
    if (!customerId || !unitId) {
      notify("Please select a customer and unit first.", "warning");
      return;
    }
    elCreateWOBtn.disabled = true;
    saveAppointment()
      .then(function (saved) {
        var eventId = (saved && saved.id) || elEventId.value;
        var url = "/work_orders/details?customer_id=" + encodeURIComponent(customerId) +
                  "&unit_id=" + encodeURIComponent(unitId);
        if (selectedPresets.length) {
          var ids = selectedPresets.map(function (p) { return p.id; }).join(",");
          url += "&presets=" + encodeURIComponent(ids);
        }
        if (elMechanic.value) url += "&mechanic_id=" + encodeURIComponent(elMechanic.value);
        var note = elTitleInp.value.trim();
        if (note) url += "&note=" + encodeURIComponent(note);
        if (eventId) url += "&calendar_event_id=" + encodeURIComponent(eventId);
        window.open(url, "_blank");
        getModal().hide();
        loadEvents();
      })
      .catch(function (e) {
        if (e && e.message !== "invalid") notify(e.message || "Could not save the appointment.", "error");
      })
      .then(function () { elCreateWOBtn.disabled = false; });
  });

  /* ── delete ── */
  elDelBtn.addEventListener("click", function () {
    var eventId = elEventId.value;
    if (!eventId) return;
    var proceed = window.appConfirm
      ? window.appConfirm("Delete this appointment?")
      : Promise.resolve(window.confirm("Delete this appointment?"));
    proceed.then(function (ok) {
      if (!ok) return;
      fetchJSON("/calendar/api/events/" + eventId, { method: "DELETE" })
        .then(function () {
          getModal().hide();
          loadEvents();
        })
        .catch(function (e) { notify(e.message || "Could not delete the appointment.", "error"); });
    });
  });

  /* ── settings modal ── */

  var settingsModalEl = document.getElementById("calSettingsModal");
  var settingsListEl  = document.getElementById("settingsStatusList");
  var settingsAddBtn  = document.getElementById("settingsAddStatus");
  var settingsSaveBtn = document.getElementById("settingsSaveBtn");
  var settingsBtn     = document.getElementById("calSettingsBtn");
  var bsSettingsModal = null;

  function getSettingsModal() {
    if (!bsSettingsModal && settingsModalEl) {
      bsSettingsModal = new bootstrap.Modal(settingsModalEl);
    }
    return bsSettingsModal;
  }

  function renderSettingsRows() {
    settingsListEl.innerHTML = "";
    cachedStatuses.forEach(function (s, idx) {
      var row = document.createElement("div");
      row.className = "d-flex align-items-center gap-2 mb-2 settings-status-row";
      row.dataset.index = idx;

      var colorInput = document.createElement("input");
      colorInput.type = "color";
      colorInput.className = "form-control form-control-color";
      colorInput.value = s.color;
      colorInput.title = "Pick color";
      colorInput.style.width = "40px";
      colorInput.style.minWidth = "40px";
      colorInput.style.padding = "2px";

      var keyInput = document.createElement("input");
      keyInput.type = "text";
      keyInput.className = "form-control form-control-sm";
      keyInput.value = s.key;
      keyInput.placeholder = "key";
      keyInput.style.width = "120px";

      var labelInput = document.createElement("input");
      labelInput.type = "text";
      labelInput.className = "form-control form-control-sm";
      labelInput.value = s.label;
      labelInput.placeholder = "Label";
      labelInput.style.flex = "1";

      var removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "btn btn-outline-danger btn-sm";
      removeBtn.innerHTML = "&times;";
      removeBtn.title = "Remove";
      removeBtn.addEventListener("click", function () {
        cachedStatuses.splice(idx, 1);
        renderSettingsRows();
      });

      row.appendChild(colorInput);
      row.appendChild(keyInput);
      row.appendChild(labelInput);
      row.appendChild(removeBtn);
      settingsListEl.appendChild(row);
    });
  }

  function collectStatusesFromRows() {
    var rows = settingsListEl.querySelectorAll(".settings-status-row");
    var result = [];
    rows.forEach(function (row) {
      var inputs = row.querySelectorAll("input");
      var color = inputs[0].value;
      var key = inputs[1].value.trim();
      var label = inputs[2].value.trim();
      if (key && label) {
        result.push({ key: key, label: label, color: color });
      }
    });
    return result;
  }

  if (settingsBtn) {
    settingsBtn.addEventListener("click", function () {
      renderSettingsRows();
      getSettingsModal().show();
    });
  }

  if (settingsAddBtn) {
    settingsAddBtn.addEventListener("click", function () {
      var fromRows = collectStatusesFromRows();
      cachedStatuses = fromRows;
      cachedStatuses.push({ key: "", label: "", color: "#888888" });
      renderSettingsRows();
    });
  }

  if (settingsSaveBtn) {
    settingsSaveBtn.addEventListener("click", function () {
      var statuses = collectStatusesFromRows();
      if (!statuses.length) { notify("Add at least one status.", "warning"); return; }

      sendJSON("/calendar/api/statuses", "PUT", { statuses: statuses })
        .then(function (data) {
          cachedStatuses = data;
          getSettingsModal().hide();
          renderEvents();
        })
        .catch(function (e) { notify(e.message || "Could not save statuses.", "error"); });
    });
  }

  /* ── navigation ── */

  function goToWeek(date) {
    weekStart = mondayOfWeek(date);
    expandedDayIndex = -1;
    if (viewMode === "mechanics") {
      selectedDayIndex = dayIndexOf(date);
      if (selectedDayIndex < 0) selectedDayIndex = 0;
    }
    render();
  }

  function step(direction) {
    if (viewMode === "mechanics") {
      goToWeek(addDays(addDays(weekStart, selectedDayIndex), direction));
    } else {
      goToWeek(addDays(weekStart, 7 * direction));
    }
  }

  btnPrev.addEventListener("click", function () { step(-1); });
  btnNext.addEventListener("click", function () { step(1); });
  btnToday.addEventListener("click", function () { goToWeek(new Date()); });

  if (btnViewWeek) btnViewWeek.addEventListener("click", function () { setViewMode("week"); });
  if (btnViewMech) btnViewMech.addEventListener("click", function () {
    if (viewMode !== "mechanics") {
      // Switching from the week: open today if it is in this week, else Monday.
      var todayIdx = dayIndexOf(new Date());
      selectedDayIndex = todayIdx >= 0 ? todayIdx : 0;
    }
    setViewMode("mechanics");
  });
  if (mechFilterEl) mechFilterEl.addEventListener("change", function () {
    setMechanicFilter(mechFilterEl.value || "");
  });
  if (showWOEl) {
    showWOEl.checked = showWorkOrders;
    showWOEl.addEventListener("change", function () {
      showWorkOrders = !!showWOEl.checked;
      lsSet("showWO", showWorkOrders ? "1" : "0");
      renderLegend();
      renderEvents();
      updateMechanicHeaderCounts();
      // toolbar height may change with the legend — recompute grid
      setTimeout(function () { render(); }, 0);
    });
  }

  /* ── keyboard ── */

  document.addEventListener("keydown", function (e) {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;
    if (document.querySelector(".modal.show")) return;
    if (e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); step(1); }
    else if (e.key === "t" || e.key === "T") { goToWeek(new Date()); }
  });

  /* ── init ── */
  if (btnViewWeek) btnViewWeek.classList.toggle("active", viewMode === "week");
  if (btnViewMech) btnViewMech.classList.toggle("active", viewMode === "mechanics");
  weekStart = mondayOfWeek(new Date());
  selectedDayIndex = Math.max(0, dayIndexOf(new Date()));
  Promise.all([loadCustomers(), loadMechanics(), loadStatuses(), loadWoStates(), loadPresets()])
    .catch(function (e) { notify("Calendar data load error: " + (e.message || e), "error"); })
    .then(function () { goToWeek(new Date()); });

  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { render(); }, 150);
  });
})();
