(function () {
  "use strict";

  /* ── constants ── */
  var DAY_NAMES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
  var MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];
  var HOURS_START = 6;
  var HOURS_END = 21;
  var SLOT_HEIGHT = 48;

  /* ── state ── */
  var weekStart = null;
  var expandedDayIndex = -1;
  var nowLineTimer = null;
  var cachedEvents = [];
  var cachedCustomers = [];
  var cachedMechanics = [];
  var cachedStatuses = [];

  /* ── DOM refs ── */
  var container = document.getElementById("calWeekContainer");
  var titleEl   = document.getElementById("calTitle");
  var btnPrev   = document.getElementById("calPrev");
  var btnNext   = document.getElementById("calNext");
  var btnToday  = document.getElementById("calToday");

  var modalEl     = document.getElementById("appointmentModal");
  var modalTitle  = document.getElementById("appointmentModalLabel");
  var elEventId   = document.getElementById("apptEventId");
  var elCustomer  = document.getElementById("apptCustomer");
  var elUnit      = document.getElementById("apptUnit");
  var elDate      = document.getElementById("apptDate");
  var elStart     = document.getElementById("apptStart");
  var elEnd       = document.getElementById("apptEnd");
  var elMechanic  = document.getElementById("apptMechanic");
  var elStatusGrp = document.getElementById("apptStatusGroup");
  var elTitleInp  = document.getElementById("apptTitle");
  var elSaveBtn   = document.getElementById("apptSaveBtn");
  var elDelBtn    = document.getElementById("apptDeleteBtn");

  if (!container) return;

  function getModal() {
    if (!bsModal && modalEl) {
      try { bsModal = new bootstrap.Modal(modalEl); } catch (e) {}
    }
    return bsModal;
  }
  var bsModal = null;

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

  /* ── title ── */

  function updateTitle() {
    var end = addDays(weekStart, 6);
    var txt;
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
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function loadCustomers() {
    return fetchJSON("/calendar/api/customers").then(function (data) {
      cachedCustomers = data || [];
    });
  }

  function loadMechanics() {
    return fetchJSON("/calendar/api/mechanics").then(function (data) {
      cachedMechanics = data || [];
    });
  }

  function loadStatuses() {
    return fetchJSON("/calendar/api/statuses").then(function (data) {
      cachedStatuses = data || [];
    });
  }

  function loadUnits(customerId) {
    if (!customerId) return Promise.resolve([]);
    return fetchJSON("/calendar/api/units/" + customerId);
  }

  function loadEvents() {
    var ws = toISODate(weekStart);
    var we = toISODate(addDays(weekStart, 7));
    return fetchJSON("/calendar/api/events?start=" + ws + "T00:00:00&end=" + we + "T00:00:00")
      .then(function (data) {
        cachedEvents = data || [];
        renderEvents();
      });
  }

  /* ── build grid ── */

  function buildHeaderRow() {
    var row = document.createElement("div");
    row.className = "cal-header-row";

    var gutter = document.createElement("div");
    gutter.className = "cal-header-gutter";
    row.appendChild(gutter);

    var cols = document.createElement("div");
    cols.className = "cal-header-cols";

    var today = new Date();
    for (var i = 0; i < 7; i++) {
      var dayDate = addDays(weekStart, i);
      var isToday = isSameDay(dayDate, today);

      var cell = document.createElement("div");
      cell.className = "cal-header-cell";
      if (isToday) cell.classList.add("cal-today");
      cell.setAttribute("data-day-index", i);

      var dayName = document.createElement("span");
      dayName.className = "cal-day-name";
      dayName.textContent = DAY_NAMES[i];
      cell.appendChild(dayName);

      var dayNum = document.createElement("span");
      dayNum.className = "cal-day-number" + (isToday ? " cal-day-number-today" : "");
      dayNum.textContent = dayDate.getDate();
      cell.appendChild(dayNum);

      (function (idx) {
        cell.addEventListener("click", function () { toggleExpandDay(idx); });
      })(i);

      cols.appendChild(cell);
    }

    row.appendChild(cols);
    return row;
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

  function buildDayColumn(dayIdx) {
    var col = document.createElement("div");
    col.className = "cal-day-col";
    col.setAttribute("data-day-index", dayIdx);

    for (var h = HOURS_START; h < HOURS_END; h++) {
      var slot = document.createElement("div");
      slot.className = "cal-hour-slot";
      slot.style.height = SLOT_HEIGHT + "px";
      slot.setAttribute("data-hour", h);
      slot.setAttribute("data-day-index", dayIdx);

      (function (hour, di) {
        slot.addEventListener("click", function (e) {
          if (e.target.closest(".cal-event")) return;
          openModal(null, di, hour);
        });
      })(h, dayIdx);

      col.appendChild(slot);
    }
    return col;
  }

  function render() {
    container.innerHTML = "";
    updateTitle();

    var grid = document.createElement("div");
    grid.className = "cal-grid";

    grid.appendChild(buildHeaderRow());

    var body = document.createElement("div");
    body.className = "cal-body-scroll";

    body.appendChild(buildTimeGutter());

    var bodyCols = document.createElement("div");
    bodyCols.className = "cal-body-cols";
    for (var i = 0; i < 7; i++) {
      bodyCols.appendChild(buildDayColumn(i));
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
    var cols = container.querySelectorAll(".cal-day-col");
    for (var c = 0; c < cols.length; c++) {
      var old = cols[c].querySelectorAll(".cal-event");
      for (var x = 0; x < old.length; x++) old[x].remove();
    }

    var statusColors = {};
    for (var s = 0; s < cachedStatuses.length; s++) {
      statusColors[cachedStatuses[s].key] = cachedStatuses[s].color;
    }

    for (var i = 0; i < cachedEvents.length; i++) {
      var ev = cachedEvents[i];
      var st = new Date(ev.start_time);
      var en = new Date(ev.end_time);

      var dayIdx = -1;
      for (var d = 0; d < 7; d++) {
        if (isSameDay(st, addDays(weekStart, d))) { dayIdx = d; break; }
      }
      if (dayIdx < 0) continue;

      var col = cols[dayIdx];
      if (!col) continue;

      var startH = st.getHours() + st.getMinutes() / 60;
      var endH   = en.getHours() + en.getMinutes() / 60;
      if (endH <= HOURS_START || startH >= HOURS_END) continue;

      var topClamped = Math.max(startH, HOURS_START);
      var botClamped = Math.min(endH, HOURS_END);

      var topPx  = (topClamped - HOURS_START) * SLOT_HEIGHT;
      var height = (botClamped - topClamped) * SLOT_HEIGHT;
      if (height < 12) height = 12;

      var color = statusColors[ev.status] || "#1a73e8";

      var block = document.createElement("div");
      block.className = "cal-event";
      block.style.top = topPx + "px";
      block.style.height = height + "px";
      block.style.backgroundColor = color;

      var line1 = ev.customer_label || ev.title || "Appointment";
      var line2Parts = [];
      if (ev.unit_label) line2Parts.push(ev.unit_label);
      if (ev.mechanic_name) line2Parts.push(ev.mechanic_name);
      var timeStr = pad2(st.getHours()) + ":" + pad2(st.getMinutes()) + " – " +
                    pad2(en.getHours()) + ":" + pad2(en.getMinutes());

      block.innerHTML =
        '<div class="cal-event-title">' + escapeHtml(line1) + '</div>' +
        '<div class="cal-event-time">' + timeStr + '</div>' +
        (line2Parts.length ? '<div class="cal-event-sub">' + escapeHtml(line2Parts.join(" · ")) + '</div>' : '');

      (function (evt) {
        block.addEventListener("click", function (e) {
          e.stopPropagation();
          openModalForEdit(evt);
        });
      })(ev);

      col.appendChild(block);
    }
  }

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
  }

  /* ── now line ── */

  function updateNowLine() {
    var grid = container.querySelector(".cal-body-cols");
    if (!grid) return;

    var old = grid.querySelectorAll(".cal-now-line");
    for (var i = 0; i < old.length; i++) old[i].remove();

    var now = new Date();
    var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());

    for (var d = 0; d < 7; d++) {
      var dayDate = addDays(weekStart, d);
      if (!isSameDay(dayDate, today)) continue;

      var col = grid.querySelectorAll(".cal-day-col")[d];
      if (!col) continue;

      var hours = now.getHours();
      var mins  = now.getMinutes();

      if (hours < HOURS_START || hours >= HOURS_END) break;

      var pxFromTop = (hours - HOURS_START) * SLOT_HEIGHT + (mins / 60) * SLOT_HEIGHT;

      var line = document.createElement("div");
      line.className = "cal-now-line";
      line.style.top = pxFromTop + "px";
      col.appendChild(line);
      break;
    }
  }

  function startNowLineTimer() {
    if (nowLineTimer) clearInterval(nowLineTimer);
    nowLineTimer = setInterval(updateNowLine, 60000);
  }

  /* ── layout / expand ── */

  function applyLayout() {
    var headerCells = container.querySelectorAll(".cal-header-cell");
    var bodyCols    = container.querySelectorAll(".cal-day-col");

    var isExpanded = expandedDayIndex >= 0 && expandedDayIndex < 7;

    for (var i = 0; i < 7; i++) {
      var hc = headerCells[i];
      var bc = bodyCols[i];
      if (!hc || !bc) continue;

      if (isExpanded) {
        if (i === expandedDayIndex) {
          hc.classList.add("cal-expanded");
          hc.classList.remove("cal-collapsed");
          bc.classList.add("cal-expanded");
          bc.classList.remove("cal-collapsed");
        } else {
          hc.classList.remove("cal-expanded");
          hc.classList.add("cal-collapsed");
          bc.classList.remove("cal-expanded");
          bc.classList.add("cal-collapsed");
        }
      } else {
        hc.classList.remove("cal-expanded", "cal-collapsed");
        bc.classList.remove("cal-expanded", "cal-collapsed");
      }
    }
  }

  function toggleExpandDay(idx) {
    expandedDayIndex = expandedDayIndex === idx ? -1 : idx;
    applyLayout();
  }

  /* ── modal ── */

  function populateSelect(el, items, valueFn, labelFn, placeholder) {
    el.innerHTML = "";
    if (placeholder) {
      var ph = document.createElement("option");
      ph.value = "";
      ph.textContent = placeholder;
      el.appendChild(ph);
    }
    for (var i = 0; i < items.length; i++) {
      var opt = document.createElement("option");
      opt.value = valueFn(items[i]);
      opt.textContent = labelFn(items[i]);
      el.appendChild(opt);
    }
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
          var sKey = allBtns[j].getAttribute("data-status");
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
    elUnit.innerHTML = '<option value="">— New Unit —</option>';
    if (!customerId) {
      elUnit.disabled = true;
      return;
    }
    elUnit.disabled = false;
    loadUnits(customerId).then(function (units) {
      populateSelect(elUnit, units,
        function (u) { return u.id; },
        function (u) { return u.label; },
        "— New Unit —"
      );
    });
  }

  function openModal(existingEvent, dayIndex, hour) {
    var isEdit = !!existingEvent;
    modalTitle.textContent = isEdit ? "Edit Appointment" : "New Appointment";
    elEventId.value = isEdit ? existingEvent.id : "";
    elDelBtn.classList.toggle("d-none", !isEdit);

    // populate customers
    populateSelect(elCustomer, cachedCustomers,
      function (c) { return c.id; },
      function (c) { return c.label; },
      "— New Customer —"
    );

    // populate mechanics
    populateSelect(elMechanic, cachedMechanics,
      function (m) { return m.id; },
      function (m) { return m.name + (m.role ? " (" + m.role + ")" : ""); },
      "— None —"
    );

    if (isEdit) {
      elCustomer.value = existingEvent.customer_id || "";
      if (existingEvent.customer_id) {
        loadUnitsForCustomer(existingEvent.customer_id);
        // set unit after load
        loadUnits(existingEvent.customer_id).then(function () {
          elUnit.value = existingEvent.unit_id || "";
        });
      } else {
        elUnit.innerHTML = '<option value="">— New Unit —</option>';
        elUnit.disabled = true;
      }
      var stDt = new Date(existingEvent.start_time);
      var enDt = new Date(existingEvent.end_time);
      elDate.value = toISODate(stDt);
      elStart.value = pad2(stDt.getHours()) + ":" + pad2(stDt.getMinutes());
      elEnd.value = pad2(enDt.getHours()) + ":" + pad2(enDt.getMinutes());
      elMechanic.value = existingEvent.mechanic_id || "";
      elTitleInp.value = existingEvent.title || "";
      buildStatusButtons(existingEvent.status || "scheduled");
    } else {
      var dayDate = addDays(weekStart, dayIndex);
      elDate.value = toISODate(dayDate);
      elStart.value = pad2(hour) + ":00";
      var endHour = Math.min(hour + 1, HOURS_END);
      elEnd.value = pad2(endHour) + ":00";
      elMechanic.value = "";
      elTitleInp.value = "";
      elUnit.innerHTML = '<option value="">— New Unit —</option>';
      elUnit.disabled = true;
      buildStatusButtons("scheduled");
    }

    getModal().show();
  }

  function openModalForEdit(ev) {
    openModal(ev, 0, 0);
  }

  /* ── customer change → reload units ── */
  elCustomer.addEventListener("change", function () {
    loadUnitsForCustomer(this.value);
  });

  /* ── save ── */
  elSaveBtn.addEventListener("click", function () {
    var dateVal = elDate.value;
    var startVal = elStart.value;
    var endVal = elEnd.value;
    if (!dateVal || !startVal || !endVal) {
      alert("Date and time are required.");
      return;
    }

    var startISO = toLocalISO(dateVal, startVal);
    var endISO   = toLocalISO(dateVal, endVal);

    var custSel = elCustomer.options[elCustomer.selectedIndex];
    var unitSel = elUnit.options[elUnit.selectedIndex];
    var mechSel = elMechanic.options[elMechanic.selectedIndex];

    var payload = {
      start_time: startISO,
      end_time: endISO,
      customer_id: elCustomer.value || "",
      customer_label: elCustomer.value ? custSel.textContent : "New Customer",
      unit_id: elUnit.value || "",
      unit_label: elUnit.value ? unitSel.textContent : (elUnit.disabled ? "" : "New Unit"),
      mechanic_id: elMechanic.value || "",
      mechanic_name: elMechanic.value ? mechSel.textContent : "",
      status: getSelectedStatus(),
      title: elTitleInp.value.trim(),
    };

    var eventId = elEventId.value;
    var url, method;
    if (eventId) {
      url = "/calendar/api/events/" + eventId;
      method = "PUT";
    } else {
      url = "/calendar/api/events";
      method = "POST";
    }

    fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) {
          alert(data.error);
          return;
        }
        getModal().hide();
        loadEvents();
      });
  });

  /* ── delete ── */
  elDelBtn.addEventListener("click", function () {
    var eventId = elEventId.value;
    if (!eventId) return;
    if (!confirm("Delete this appointment?")) return;

    fetch("/calendar/api/events/" + eventId, { method: "DELETE" })
      .then(function (r) { return r.json(); })
      .then(function () {
        getModal().hide();
        loadEvents();
      });
  });

  /* ── navigation ── */

  function goToWeek(date) {
    weekStart = mondayOfWeek(date);
    expandedDayIndex = -1;
    render();
  }

  btnPrev.addEventListener("click", function () { goToWeek(addDays(weekStart, -7)); });
  btnNext.addEventListener("click", function () { goToWeek(addDays(weekStart, 7)); });
  btnToday.addEventListener("click", function () { goToWeek(new Date()); });

  /* ── keyboard ── */

  document.addEventListener("keydown", function (e) {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;
    if (e.key === "ArrowLeft") { e.preventDefault(); goToWeek(addDays(weekStart, -7)); }
    else if (e.key === "ArrowRight") { e.preventDefault(); goToWeek(addDays(weekStart, 7)); }
    else if (e.key === "t" || e.key === "T") { goToWeek(new Date()); }
  });

  /* ── init ── */
  goToWeek(new Date());
  Promise.all([loadCustomers(), loadMechanics(), loadStatuses()])
    .then(function () { loadEvents(); })
    .catch(function (e) { console.warn("Calendar data load error:", e); });
})();
