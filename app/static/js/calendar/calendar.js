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
  var STEP_MINUTES = 15;
  var PX_PER_MINUTE = SLOT_HEIGHT / 60;

  /* ── state ── */
  var weekStart = null;
  var expandedDayIndex = -1;
  var nowLineTimer = null;
  var cachedEvents = [];
  var cachedCustomers = [];
  var cachedMechanics = [];
  var cachedStatuses = [];
  var drag = null; // active drag state
  var dragJustEnded = false;

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
          if (dragJustEnded) return;
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

    // group events by day
    var dayBuckets = {};
    for (var i = 0; i < cachedEvents.length; i++) {
      var ev = cachedEvents[i];
      var st = new Date(ev.start_time);
      var dayIdx = -1;
      for (var d = 0; d < 7; d++) {
        if (isSameDay(st, addDays(weekStart, d))) { dayIdx = d; break; }
      }
      if (dayIdx < 0) continue;
      if (!dayBuckets[dayIdx]) dayBuckets[dayIdx] = [];
      dayBuckets[dayIdx].push(ev);
    }

    for (var di in dayBuckets) {
      if (!dayBuckets.hasOwnProperty(di)) continue;
      var dayEvents = dayBuckets[di];
      var col = cols[parseInt(di)];
      if (!col) continue;

      // sort by start time, then longer events first
      dayEvents.sort(function (a, b) {
        var d = new Date(a.start_time) - new Date(b.start_time);
        if (d !== 0) return d;
        return new Date(b.end_time) - new Date(a.end_time);
      });

      // build time ranges
      var items = [];
      for (var j = 0; j < dayEvents.length; j++) {
        var stD = new Date(dayEvents[j].start_time);
        var enD = new Date(dayEvents[j].end_time);
        items.push({
          ev: dayEvents[j],
          startMin: stD.getHours() * 60 + stD.getMinutes(),
          endMin: enD.getHours() * 60 + enD.getMinutes()
        });
      }

      // split into independent clusters (connected overlap groups)
      var clusters = [];
      var ci = 0;
      while (ci < items.length) {
        var cluster = [items[ci]];
        var clusterEnd = items[ci].endMin;
        ci++;
        while (ci < items.length && items[ci].startMin < clusterEnd) {
          cluster.push(items[ci]);
          if (items[ci].endMin > clusterEnd) clusterEnd = items[ci].endMin;
          ci++;
        }
        clusters.push(cluster);
      }

      // for each cluster, assign columns independently
      for (var gc = 0; gc < clusters.length; gc++) {
        var clItems = clusters[gc];
        var columns = [];
        var assignments = [];

        for (var j = 0; j < clItems.length; j++) {
          var it = clItems[j];
          var placed = false;
          for (var c = 0; c < columns.length; c++) {
            var fits = true;
            for (var p = 0; p < columns[c].length; p++) {
              if (it.startMin < columns[c][p].endMin && it.endMin > columns[c][p].startMin) {
                fits = false;
                break;
              }
            }
            if (fits) {
              columns[c].push(it);
              assignments.push({ ev: it.ev, colIndex: c });
              placed = true;
              break;
            }
          }
          if (!placed) {
            columns.push([it]);
            assignments.push({ ev: it.ev, colIndex: columns.length - 1 });
          }
        }

        var totalCols = columns.length;
        for (var k = 0; k < assignments.length; k++) {
          renderSingleEvent(assignments[k].ev, col, statusColors, assignments[k].colIndex, totalCols);
        }
      }
    }
  }

  function renderSingleEvent(ev, col, statusColors, colIndex, colTotal) {
    var st = new Date(ev.start_time);
    var en = new Date(ev.end_time);

    var startH = st.getHours() + st.getMinutes() / 60;
    var endHr  = en.getHours() + en.getMinutes() / 60;
    if (endHr <= HOURS_START || startH >= HOURS_END) return;

    var topClamped = Math.max(startH, HOURS_START);
    var botClamped = Math.min(endHr, HOURS_END);
    var topPx  = (topClamped - HOURS_START) * SLOT_HEIGHT;
    var height = (botClamped - topClamped) * SLOT_HEIGHT;
    if (height < 12) height = 12;

    var color = statusColors[ev.status] || "#1a73e8";

    var block = document.createElement("div");
    block.className = "cal-event";
    block.style.top = topPx + "px";
    block.style.height = height + "px";
    block.style.backgroundColor = color;

    // side-by-side layout for overlaps
    var w = 100 / colTotal;
    block.style.left = (w * colIndex) + "%";
    block.style.width = w + "%";

    var line1 = ev.customer_label || ev.title || "Appointment";
    var line2Parts = [];
    if (ev.unit_label) line2Parts.push(ev.unit_label);
    if (ev.mechanic_name) line2Parts.push(ev.mechanic_name);

    var html = '<div class="cal-event-title">' + escapeHtml(line1) + '</div>';
    if (line2Parts.length) html += '<div class="cal-event-sub">' + escapeHtml(line2Parts.join(" \u00b7 ")) + '</div>';
    if (ev.title && ev.title !== line1) html += '<div class="cal-event-note">' + escapeHtml(ev.title) + '</div>';
    block.innerHTML = html;

    (function (evt, blk) {
      blk.addEventListener("click", function (e) {
        e.stopPropagation();
        if (dragJustEnded) return;
        openModalForEdit(evt);
      });
      blk.addEventListener("mousedown", function (e) {
        if (e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        startDrag(evt, blk, e);
      });
    })(ev, block);

    col.appendChild(block);
  }

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.appendChild(document.createTextNode(str));
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
    var durationMin = (en - st) / 60000;

    // find original day index
    var origDayIdx = 0;
    for (var d = 0; d < 7; d++) {
      if (isSameDay(st, addDays(weekStart, d))) { origDayIdx = d; break; }
    }

    drag = {
      evt: evt,
      el: blockEl,
      durationMin: durationMin,
      startX: mouseEvt.clientX,
      startY: mouseEvt.clientY,
      origTop: parseFloat(blockEl.style.top),
      origDayIdx: origDayIdx,
      currentDayIdx: origDayIdx,
      cols: cols,
      moved: false
    };

    blockEl.classList.add("cal-dragging");
    document.addEventListener("mousemove", onDragMove);
    document.addEventListener("mouseup", onDragEnd);
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

    // horizontal: detect day column under cursor & move block
    var colIdx = -1;
    for (var i = 0; i < drag.cols.length; i++) {
      var cr = drag.cols[i].getBoundingClientRect();
      if (e.clientX >= cr.left && e.clientX < cr.right) { colIdx = i; break; }
    }
    if (colIdx >= 0 && colIdx !== drag.currentDayIdx) {
      drag.cols[colIdx].appendChild(drag.el);
      drag.currentDayIdx = colIdx;
    }
  }

  function onDragEnd(e) {
    document.removeEventListener("mousemove", onDragMove);
    document.removeEventListener("mouseup", onDragEnd);
    if (!drag) return;

    drag.el.classList.remove("cal-dragging");

    if (!drag.moved) { drag = null; return; }

    // suppress clicks briefly after drag
    dragJustEnded = true;
    setTimeout(function () { dragJustEnded = false; }, 300);

    var topPx = parseFloat(drag.el.style.top);
    var totalMin = snapTo15(topPx / PX_PER_MINUTE);
    var newHour = HOURS_START + Math.floor(totalMin / 60);
    var newMin = totalMin % 60;

    var dayIdx = drag.currentDayIdx;

    var newDate = addDays(weekStart, dayIdx);
    var startISO = toISODate(newDate) + "T" + pad2(newHour) + ":" + pad2(newMin) + ":00";
    var endTotalMin = totalMin + drag.durationMin;
    var endH = HOURS_START + Math.floor(endTotalMin / 60);
    var endM = endTotalMin % 60;
    var endISO = toISODate(newDate) + "T" + pad2(endH) + ":" + pad2(endM) + ":00";

    var evtId = drag.evt.id;
    drag = null;

    fetch("/calendar/api/events/" + evtId, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start_time: startISO, end_time: endISO }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { alert(data.error); }
        loadEvents();
      })
      .catch(function () { loadEvents(); });
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
      if (!statuses.length) { alert("Add at least one status."); return; }

      fetch("/calendar/api/statuses", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ statuses: statuses }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { alert(data.error); return; }
          cachedStatuses = data;
          getSettingsModal().hide();
          renderEvents();
        })
        .catch(function (e) { console.error("Save statuses error:", e); });
    });
  }

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
