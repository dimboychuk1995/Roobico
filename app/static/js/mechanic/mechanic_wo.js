/* Механик-режим: список WO и карточка WO без цен.
   CSRF добавляет csrf.js автоматически; тосты — window.appAlert. */
(function () {
  "use strict";

  var API = {
    list: "/work_orders/api/mechanic/work_orders",
    details: function (id) { return "/work_orders/api/mechanic/work_orders/" + encodeURIComponent(id); },
    create: "/work_orders/api/mechanic/work_orders",
    update: function (id) { return "/work_orders/api/mechanic/work_orders/" + encodeURIComponent(id); },
    timerStart: "/work_orders/api/mechanic/timers/start",
    timerStop: "/work_orders/api/mechanic/timers/stop",
    timerCurrent: "/work_orders/api/mechanic/timers/current",
    timersActive: "/work_orders/api/mechanic/timers/active",
    customers: "/work_orders/api/customers",
    units: "/work_orders/api/units",
    unitCreate: "/api/mobile/units",
    partsSearch: "/work_orders/api/parts/search",
    presets: "/work_orders/api/presets",
    presetDetail: function (id) { return "/work_orders/api/presets/" + encodeURIComponent(id); },
  };

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  async function getJson(url) {
    var res = await fetch(url, { headers: { "Accept": "application/json" } });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok || data.ok === false) {
      throw new Error((data && data.error) || ("Request failed (" + res.status + ")"));
    }
    return data;
  }

  async function postJson(url, body) {
    var res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(body || {}),
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok || data.ok === false) {
      throw new Error((data && data.error) || ("Request failed (" + res.status + ")"));
    }
    return data;
  }

  function toast(msg, kind) {
    if (window.appAlert) window.appAlert(msg, kind || "info");
  }

  // Смещение часов сервера: elapsed считаем от server_now, не от локальных часов.
  var serverOffsetMs = 0;
  function syncServerNow(iso) {
    if (!iso) return;
    var t = Date.parse(iso);
    if (Number.isFinite(t)) serverOffsetMs = t - Date.now();
  }
  function serverNowMs() { return Date.now() + serverOffsetMs; }

  function fmtDuration(totalSeconds) {
    var s = Math.max(0, Math.floor(totalSeconds));
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    return h + ":" + String(m).padStart(2, "0") + ":" + String(sec).padStart(2, "0");
  }

  // ---------------- theme toggle ----------------
  function initThemeToggle() {
    var btn = $("mechThemeToggle");
    if (!btn) return;
    function apply(theme) {
      document.documentElement.setAttribute("data-theme", theme);
      try { localStorage.setItem("roobico.theme", theme); } catch (e) { /* ignore */ }
      var icon = btn.querySelector("i");
      if (icon) icon.className = theme === "dark" ? "bi bi-sun" : "bi bi-moon";
    }
    var current = document.documentElement.getAttribute("data-theme") || "light";
    var icon = btn.querySelector("i");
    if (icon) icon.className = current === "dark" ? "bi bi-sun" : "bi bi-moon";
    btn.addEventListener("click", function () {
      var now = document.documentElement.getAttribute("data-theme") || "light";
      apply(now === "dark" ? "light" : "dark");
    });
  }

  // ---------------- sticky timer bar ----------------
  var barTimer = null;      // {work_order_id, wo_number, labor_id, started_at, labor_description}
  var barTickHandle = null;

  function renderTimerBar() {
    var bar = $("mechTimerBar");
    if (!bar) return;
    if (!barTimer) {
      bar.style.display = "none";
      if (barTickHandle) { clearInterval(barTickHandle); barTickHandle = null; }
      return;
    }
    bar.style.display = "flex";
    var link = $("mechTimerBarLink");
    if (link) {
      var label = "WO #" + (barTimer.wo_number || "");
      if (barTimer.labor_description) label += " — " + barTimer.labor_description;
      link.textContent = label;
      link.href = "/mechanic/work_orders/" + encodeURIComponent(barTimer.work_order_id);
    }
    function tick() {
      var el = $("mechTimerBarElapsed");
      if (!el) return;
      var started = Date.parse(barTimer.started_at);
      el.textContent = fmtDuration((serverNowMs() - started) / 1000);
    }
    tick();
    if (barTickHandle) clearInterval(barTickHandle);
    barTickHandle = setInterval(tick, 1000);
  }

  function setBarTimer(timer) {
    barTimer = timer || null;
    renderTimerBar();
  }

  async function loadCurrentTimer() {
    try {
      var data = await getJson(API.timerCurrent);
      syncServerNow(data.server_now);
      setBarTimer(data.timer);
    } catch (e) { /* не блокируем страницу */ }
  }

  function bindTimerBarStop(onStopped) {
    var btn = $("mechTimerBarStopBtn");
    if (!btn) return;
    btn.addEventListener("click", async function () {
      btn.disabled = true;
      try {
        var data = await postJson(API.timerStop, {});
        syncServerNow(data.server_now);
        setBarTimer(null);
        toast("Timer stopped", "success");
        if (onStopped) onStopped(data);
      } catch (e) {
        toast(e.message || "Failed to stop timer", "error");
      } finally {
        btn.disabled = false;
      }
    });
  }

  // ================= LIST PAGE =================
  function initListPage(root) {
    var state = { q: "", status: "all", page: 1 };
    var listEl = $("mechWoList");
    var emptyEl = $("mechListEmpty");
    var searchInput = $("mechSearchInput");
    var chips = $("mechStatusChips");

    function cardHtml(item) {
      var badge;
      if (item.status === "paid") {
        badge = '<span class="badge text-bg-success">Paid</span>';
      } else if (item.is_in_progress) {
        badge = '<span class="badge text-bg-info">In Progress</span>';
      } else {
        badge = '<span class="badge text-bg-warning">Open</span>';
      }
      var running = item.my_timer_running
        ? '<span class="mech-wo-running"><span class="mech-timer-dot"></span>My timer running</span>'
        : "";
      return (
        '<a class="mech-wo-card" href="/mechanic/work_orders/' + encodeURIComponent(item.id) + '">' +
          '<div class="mech-wo-card-top">' +
            '<span class="mech-wo-number">WO #' + esc(item.wo_number) + "</span>" + badge +
          "</div>" +
          '<div class="mech-wo-meta">' +
            "<span>" + esc(item.customer || "-") + "</span>" +
            "<span>" + esc(item.unit || "-") + (item.date ? " · " + esc(item.date) : "") + "</span>" +
          "</div>" + running +
        "</a>"
      );
    }

    // «Сейчас в работе»: активные таймеры всех механиков магазина.
    var activeTickHandle = null;

    function renderActiveNow(items) {
      var wrap = $("mechActiveNow");
      var list = $("mechActiveNowList");
      var allTitle = $("mechAllTitle");
      if (!wrap || !list) return;
      if (activeTickHandle) { clearInterval(activeTickHandle); activeTickHandle = null; }

      if (!items || !items.length) {
        wrap.style.display = "none";
        if (allTitle) allTitle.style.display = "none";
        return;
      }
      wrap.style.display = "";
      if (allTitle) allTitle.style.display = "";

      list.innerHTML = items.map(function (t) {
        var who = esc(t.user_name || "—") + (t.mine ? " (you)" : "");
        return (
          '<a class="mech-wo-card" href="/mechanic/work_orders/' + encodeURIComponent(t.work_order_id) + '">' +
            '<div class="mech-wo-card-top">' +
              '<span class="mech-wo-number">WO #' + esc(t.wo_number) + "</span>" +
              '<span class="badge text-bg-info">In Progress</span>' +
            "</div>" +
            '<div class="mech-wo-meta">' +
              "<span>" + esc(t.customer || "-") + (t.labor_description ? " · " + esc(t.labor_description) : "") + "</span>" +
            "</div>" +
            '<div class="mech-active-meta">' +
              '<span><span class="mech-timer-dot"></span> ' + who + "</span>" +
              '<span class="mech-active-elapsed" data-started-at="' + esc(t.started_at) + '">0:00:00</span>' +
            "</div>" +
          "</a>"
        );
      }).join("");

      function tick() {
        list.querySelectorAll(".mech-active-elapsed").forEach(function (el) {
          var started = Date.parse(el.dataset.startedAt);
          if (Number.isFinite(started)) {
            el.textContent = fmtDuration((serverNowMs() - started) / 1000);
          }
        });
      }
      tick();
      activeTickHandle = setInterval(tick, 1000);
    }

    async function loadActiveNow() {
      try {
        var data = await getJson(API.timersActive);
        syncServerNow(data.server_now);
        renderActiveNow(data.items || []);
      } catch (e) { /* блок не критичен для страницы */ }
    }

    async function load() {
      try {
        var url = API.list + "?page=" + state.page +
          "&status=" + encodeURIComponent(state.status) +
          (state.q ? "&q=" + encodeURIComponent(state.q) : "");
        var data = await getJson(url);
        syncServerNow(data.server_now);

        var items = data.items || [];
        listEl.innerHTML = items.map(cardHtml).join("");
        emptyEl.style.display = items.length ? "none" : "";

        var p = data.pagination || {};
        var pag = $("mechPagination");
        if (p.pages > 1) {
          pag.style.display = "";
          $("mechPageLabel").textContent = "Page " + p.page + " of " + p.pages;
          $("mechPrevBtn").disabled = !p.has_prev;
          $("mechNextBtn").disabled = !p.has_next;
        } else {
          pag.style.display = "none";
        }
      } catch (e) {
        toast(e.message || "Failed to load work orders", "error");
      }
    }

    var searchDebounce = null;
    searchInput.addEventListener("input", function () {
      clearTimeout(searchDebounce);
      searchDebounce = setTimeout(function () {
        state.q = searchInput.value.trim();
        state.page = 1;
        load();
      }, 300);
    });

    chips.addEventListener("click", function (e) {
      var btn = e.target.closest(".mech-chip");
      if (!btn) return;
      chips.querySelectorAll(".mech-chip").forEach(function (c) { c.classList.remove("active"); });
      btn.classList.add("active");
      state.status = btn.dataset.status || "all";
      state.page = 1;
      load();
    });

    $("mechPrevBtn").addEventListener("click", function () { state.page = Math.max(1, state.page - 1); load(); });
    $("mechNextBtn").addEventListener("click", function () { state.page += 1; load(); });

    bindTimerBarStop(function () { load(); loadActiveNow(); });
    loadCurrentTimer();
    loadActiveNow();
    load();
    // Блок «сейчас в работе» обновляем раз в минуту — другие механики
    // стартуют/стопают таймеры со своих устройств.
    setInterval(loadActiveNow, 60000);
  }

  // ================= DETAILS PAGE =================
  function initDetailsPage(root) {
    var mode = root.dataset.mode || "create";
    var woId = root.dataset.workOrderId || "";

    // labors: [{labor_id, description, issue_description, parts: [{part_id, part_number, description, qty}], time}]
    var state = { labors: [], saving: false };
    var laborsContainer = $("mechLaborsContainer");
    var pickerTargetIndex = null;
    var tickHandle = null;

    function newLabor() {
      return { labor_id: "", description: "", issue_description: "", parts: [], time: null };
    }

    function laborHtml(labor, idx) {
      var partsRows = labor.parts.map(function (p, pIdx) {
        return (
          '<div class="mech-part-row" data-part-index="' + pIdx + '">' +
            '<div class="mech-part-main">' +
              '<div class="mech-part-number">' + esc(p.part_number || p.description || "Part") + "</div>" +
              (p.part_number && p.description ? '<div class="mech-part-desc">' + esc(p.description) + "</div>" : "") +
            "</div>" +
            '<div class="mech-qty">' +
              '<button type="button" class="mech-qty-minus" aria-label="Decrease">−</button>' +
              '<input type="number" class="form-control mech-qty-input" inputmode="numeric" min="1" value="' + esc(p.qty || 1) + '">' +
              '<button type="button" class="mech-qty-plus" aria-label="Increase">+</button>' +
            "</div>" +
            '<button type="button" class="mech-part-remove" aria-label="Remove part"><i class="bi bi-x-lg"></i></button>' +
          "</div>"
        );
      }).join("");

      var timerSection = "";
      if (mode === "edit" && labor.labor_id) {
        var t = labor.time || { total_seconds: 0, completed_seconds: 0, my_running: false, running_users: [] };
        var isMine = !!t.my_running;
        // Над одной работой могут работать несколько механиков одновременно.
        var others = (t.running_users || []).filter(function (u) { return !u.mine; });
        var othersLabel = others.map(function (u) { return esc(u.user_name || "other"); }).join(", ");
        var trackedLine = '<span class="mech-labor-tracked" data-role="tracked">' +
          "Tracked: <span data-role='tracked-total'>" + fmtDuration(liveLaborSeconds(t)) + "</span>" +
          (othersLabel ? ' · <span class="running">' + othersLabel + " working</span>" : "") +
          "</span>";
        timerSection =
          '<div class="mech-timer-row">' +
            trackedLine +
            '<button type="button" class="btn mech-timer-btn ' + (isMine ? "btn-danger" : "btn-success") + '" data-role="timer-btn">' +
              (isMine ? '<i class="bi bi-stop-fill"></i> Stop job' : '<i class="bi bi-play-fill"></i> Start job') +
            "</button>" +
          "</div>";
      }

      var removeBtn = labor.labor_id
        ? ""
        : '<button type="button" class="btn btn-outline-danger btn-sm" data-role="remove-labor"><i class="bi bi-trash"></i></button>';

      return (
        '<div class="mech-labor-card" data-labor-index="' + idx + '">' +
          '<div class="mech-labor-head">' +
            '<span class="mech-labor-title">Job ' + (idx + 1) + "</span>" + removeBtn +
          "</div>" +
          '<div class="mech-labor-body">' +
            '<textarea class="form-control mech-labor-desc" placeholder="What was done..." rows="2">' + esc(labor.description) + "</textarea>" +
            timerSection +
            '<div class="mech-parts">' +
              '<div class="mech-parts-title">Parts</div>' +
              '<div class="mech-parts-rows">' + partsRows + "</div>" +
              '<button type="button" class="btn btn-outline-secondary mech-add-part" data-role="add-part">' +
                '<i class="bi bi-plus-lg"></i> Add part' +
              "</button>" +
            "</div>" +
          "</div>" +
        "</div>"
      );
    }

    function render() {
      laborsContainer.innerHTML = state.labors.map(laborHtml).join("");
      startTicker();
    }

    // Живое время работы: завершённые сессии + все идущие таймеры
    // (своих и чужих) от их started_at.
    function liveLaborSeconds(t) {
      if (!t) return 0;
      var total = t.completed_seconds || 0;
      (t.running_users || []).forEach(function (u) {
        var started = Date.parse(u.started_at);
        if (Number.isFinite(started)) {
          total += Math.max(0, (serverNowMs() - started) / 1000);
        }
      });
      return total;
    }

    function startTicker() {
      if (tickHandle) clearInterval(tickHandle);
      tickHandle = setInterval(function () {
        state.labors.forEach(function (labor, idx) {
          var t = labor.time;
          if (!t || !(t.running_users || []).length) return;
          var card = laborsContainer.querySelector('[data-labor-index="' + idx + '"]');
          if (!card) return;
          var el = card.querySelector("[data-role='tracked-total']");
          if (el) el.textContent = fmtDuration(liveLaborSeconds(t));
        });
      }, 1000);
    }

    function normalizeTimeBucket(bucket) {
      bucket = bucket || {};
      return {
        total_seconds: bucket.total_seconds || 0,
        completed_seconds: bucket.completed_seconds || 0,
        my_seconds: bucket.my_seconds || 0,
        my_running: !!bucket.my_running,
        my_started_at: bucket.my_started_at || "",
        running_users: bucket.running_users || [],
      };
    }

    function applyTimeSummary(summary) {
      state.labors.forEach(function (labor) {
        if (!labor.labor_id) return;
        labor.time = normalizeTimeBucket((summary || {})[labor.labor_id]);
      });
    }

    function collectFromDom() {
      // Синхронизировать description/qty из DOM в state перед действиями.
      laborsContainer.querySelectorAll(".mech-labor-card").forEach(function (card) {
        var idx = Number(card.dataset.laborIndex);
        var labor = state.labors[idx];
        if (!labor) return;
        var desc = card.querySelector(".mech-labor-desc");
        if (desc) labor.description = desc.value.trim();
        card.querySelectorAll(".mech-part-row").forEach(function (row) {
          var pIdx = Number(row.dataset.partIndex);
          var part = labor.parts[pIdx];
          if (!part) return;
          var qtyInput = row.querySelector(".mech-qty-input");
          if (qtyInput) part.qty = Math.max(1, parseInt(qtyInput.value, 10) || 1);
        });
      });
    }

    async function loadDetails() {
      try {
        var data = await getJson(API.details(woId));
        syncServerNow(data.server_now);

        $("mechWoTitle").textContent = "WO #" + (data.wo_number || "");
        var statusEl = $("mechWoStatus");
        if (statusEl) {
          var s = data.status || "open";
          var cls = s === "paid" ? "text-bg-success" : (s === "in_progress" ? "text-bg-info" : "text-bg-warning");
          var label = s === "in_progress" ? "In Progress" : s.charAt(0).toUpperCase() + s.slice(1);
          statusEl.innerHTML = '<span class="badge ' + cls + '">' + esc(label) + "</span>";
        }

        $("mechWoInfoCard").style.display = "";
        $("mechInfoCustomer").textContent = (data.customer && data.customer.label) || "—";
        $("mechInfoUnit").textContent = (data.unit && data.unit.label) || "—";
        $("mechInfoDate").textContent = data.date || "—";

        state.labors = (data.labors || []).map(function (l) {
          return {
            labor_id: l.labor_id || "",
            description: l.description || "",
            issue_description: l.issue_description || "",
            parts: (l.parts || []).map(function (p) {
              return {
                part_id: p.part_id || "",
                part_number: p.part_number || "",
                description: p.description || "",
                qty: p.qty || 1,
              };
            }),
            time: l.time ? normalizeTimeBucket(l.time) : null,
          };
        });
        if (!state.labors.length) state.labors.push(newLabor());

        if (data.my_running_timer) {
          var running = data.my_running_timer;
          var laborDesc = "";
          state.labors.forEach(function (l) {
            if (l.labor_id === running.labor_id) laborDesc = l.description;
          });
          setBarTimer({
            work_order_id: running.work_order_id,
            wo_number: running.wo_number,
            labor_id: running.labor_id,
            started_at: running.started_at,
            labor_description: laborDesc,
          });
        }

        render();

        if (data.status === "paid") {
          $("mechSaveBtn").disabled = true;
          toast("Paid work orders cannot be edited", "warning");
        }
      } catch (e) {
        toast(e.message || "Failed to load work order", "error");
      }
    }

    // ---------- customer / unit pickers (create mode) ----------
    async function initCreatePickers() {
      var custSel = $("mechCustomerSelect");
      var unitSel = $("mechUnitSelect");
      var newUnitBtn = $("mechNewUnitBtn");
      try {
        var data = await getJson(API.customers);
        (data.customers || []).forEach(function (c) {
          var opt = document.createElement("option");
          opt.value = c.id;
          opt.textContent = c.label;
          custSel.appendChild(opt);
        });
      } catch (e) {
        toast(e.message || "Failed to load customers", "error");
      }
      custSel.addEventListener("change", async function () {
        unitSel.innerHTML = '<option value="">Select unit...</option>';
        unitSel.disabled = !custSel.value;
        if (newUnitBtn) newUnitBtn.disabled = !custSel.value;
        if (!custSel.value) return;
        try {
          var data = await getJson(API.units + "?customer_id=" + encodeURIComponent(custSel.value));
          (data.items || []).forEach(function (u) {
            var opt = document.createElement("option");
            opt.value = u.id;
            opt.textContent = u.label;
            unitSel.appendChild(opt);
          });
        } catch (e) {
          toast(e.message || "Failed to load units", "error");
        }
      });

      initUnitModal(custSel, unitSel, newUnitBtn);
    }

    // ---------- new unit modal (create mode) ----------
    function initUnitModal(custSel, unitSel, newUnitBtn) {
      var modalEl = $("mechUnitModal");
      if (!modalEl || !newUnitBtn) return;
      var modal = null;

      newUnitBtn.addEventListener("click", function () {
        if (!custSel.value) return;
        ["mechUnitNumber", "mechUnitVin", "mechUnitYear", "mechUnitMake",
         "mechUnitModel", "mechUnitType", "mechUnitMileage"].forEach(function (id) {
          var el = $(id);
          if (el) el.value = "";
        });
        if (!modal && window.bootstrap) modal = new window.bootstrap.Modal(modalEl);
        if (modal) modal.show();
      });

      $("mechUnitSaveBtn").addEventListener("click", async function () {
        var btn = this;
        var unitNumber = ($("mechUnitNumber").value || "").trim();
        var vin = ($("mechUnitVin").value || "").trim().toUpperCase();
        if (!unitNumber && !vin) {
          toast("Unit number or VIN is required", "warning");
          return;
        }
        btn.disabled = true;
        try {
          var data = await postJson(API.unitCreate, {
            customer_id: custSel.value,
            unit_number: unitNumber,
            vin: vin,
            year: ($("mechUnitYear").value || "").trim(),
            make: ($("mechUnitMake").value || "").trim(),
            model: ($("mechUnitModel").value || "").trim(),
            type: ($("mechUnitType").value || "").trim(),
            mileage: ($("mechUnitMileage").value || "").trim(),
          });
          var opt = document.createElement("option");
          opt.value = data.id;
          opt.textContent = data.label;
          unitSel.appendChild(opt);
          unitSel.value = data.id;
          if (modal) modal.hide();
          toast("Unit created", "success");
        } catch (e) {
          toast(e.message || "Failed to create unit", "error");
        } finally {
          btn.disabled = false;
        }
      });
    }

    // ---------- preset picker ----------
    var presetModalEl = $("mechPresetModal");
    var presetModal = null;
    var presetsCache = null;

    function renderPresetResults(filter) {
      var box = $("mechPresetResults");
      var q = String(filter || "").trim().toLowerCase();
      var items = (presetsCache || []).filter(function (p) {
        return !q || (p.name || "").toLowerCase().indexOf(q) !== -1;
      });
      box.innerHTML = items.map(function (p) {
        return '<button type="button" class="mech-part-result" data-preset-id="' + esc(p.id) + '">' +
          '<div class="mech-part-number">' + esc(p.name || "Preset") + "</div>" +
        "</button>";
      }).join("") || '<div class="text-muted small p-2">No presets found.</div>';
    }

    function bindPresetPicker() {
      var addBtn = $("mechAddPresetBtn");
      if (!addBtn || !presetModalEl) return;

      addBtn.addEventListener("click", async function () {
        if (!presetModal && window.bootstrap) presetModal = new window.bootstrap.Modal(presetModalEl);
        $("mechPresetFilterInput").value = "";
        if (presetsCache === null) {
          try {
            presetsCache = await getJson(API.presets);
            if (!Array.isArray(presetsCache)) presetsCache = [];
          } catch (e) {
            presetsCache = null;
            toast(e.message || "Failed to load presets", "error");
            return;
          }
        }
        renderPresetResults("");
        if (presetModal) presetModal.show();
      });

      $("mechPresetFilterInput").addEventListener("input", function () {
        renderPresetResults(this.value);
      });

      $("mechPresetResults").addEventListener("click", async function (e) {
        var btn = e.target.closest("[data-preset-id]");
        if (!btn) return;
        btn.disabled = true;
        try {
          var preset = await getJson(API.presetDetail(btn.dataset.presetId));
          collectFromDom();
          var labor = {
            labor_id: "",
            description: preset.name || preset.description || "",
            issue_description: "",
            parts: (preset.parts || []).map(function (p) {
              return {
                part_id: p.part_id || "",
                part_number: p.part_number || "",
                description: p.description || "",
                qty: p.qty || 1,
              };
            }),
            time: null,
          };
          // Пустой единственный блок заменяем, иначе добавляем новый.
          var only = state.labors.length === 1
            && !state.labors[0].description
            && !state.labors[0].parts.length
            && !state.labors[0].labor_id;
          if (only) state.labors = [labor]; else state.labors.push(labor);
          if (presetModal) presetModal.hide();
          render();
          toast('Preset "' + (preset.name || "") + '" applied', "success");
        } catch (err) {
          toast(err.message || "Failed to load preset", "error");
        } finally {
          btn.disabled = false;
        }
      });
    }

    // ---------- part picker modal ----------
    var partModalEl = $("mechPartModal");
    var partModal = null;
    var partSearchDebounce = null;

    function openPartPicker(laborIndex) {
      pickerTargetIndex = laborIndex;
      if (!partModal && window.bootstrap) partModal = new window.bootstrap.Modal(partModalEl);
      $("mechPartSearchInput").value = "";
      $("mechPartResults").innerHTML =
        '<div class="text-muted small p-2">Type at least 3 characters to search.</div>';
      if (partModal) partModal.show();
      setTimeout(function () { $("mechPartSearchInput").focus(); }, 250);
    }

    function renderPartResults(items, query) {
      var box = $("mechPartResults");
      var html = "";
      if (query.length >= 2) {
        html += '<button type="button" class="mech-part-result" data-one-time="1">' +
          '<div class="mech-part-number"><i class="bi bi-plus-circle"></i> Add "' + esc(query) + '" as one-time part</div>' +
          '<div class="meta">Not from catalog — manager fills the price</div>' +
        "</button>";
      }
      function partResultBtn(p, altFor) {
        return '<button type="button" class="mech-part-result' + (altFor ? " mech-part-result-alt" : "") + '" data-part-id="' + esc(p.id) + '"' +
          ' data-part-number="' + esc(p.part_number) + '" data-description="' + esc(p.description) + '">' +
          '<div class="mech-part-number">' + (altFor ? "&#8646; " : "") + esc(p.part_number || p.description) + "</div>" +
          '<div class="meta">' + (altFor ? "Cross ref for " + esc(altFor) + " · " : "") + esc(p.description || "") +
            (p.in_stock != null ? " · Stock: " + esc(p.in_stock) : "") + "</div>" +
        "</button>";
      }
      (items || []).forEach(function (p) {
        html += partResultBtn(p, "");
        // Взаимозаменяемые парты — строками под основным результатом.
        (Array.isArray(p.alternates) ? p.alternates : []).forEach(function (alt) {
          html += partResultBtn(alt, p.part_number || "");
        });
      });
      box.innerHTML = html || '<div class="text-muted small p-2">Nothing found.</div>';
    }

    function bindPartPicker() {
      var input = $("mechPartSearchInput");
      input.addEventListener("input", function () {
        clearTimeout(partSearchDebounce);
        var q = input.value.trim();
        if (q.length < 3) {
          renderPartResults([], q);
          return;
        }
        partSearchDebounce = setTimeout(async function () {
          try {
            var data = await getJson(API.partsSearch + "?q=" + encodeURIComponent(q));
            renderPartResults(data.items || [], q);
          } catch (e) {
            renderPartResults([], q);
          }
        }, 250);
      });

      $("mechPartResults").addEventListener("click", function (e) {
        var btn = e.target.closest(".mech-part-result");
        if (!btn || pickerTargetIndex == null) return;
        collectFromDom();
        var labor = state.labors[pickerTargetIndex];
        if (!labor) return;
        if (btn.dataset.oneTime === "1") {
          var q = $("mechPartSearchInput").value.trim();
          labor.parts.push({ part_id: "", part_number: q, description: "", qty: 1 });
        } else {
          labor.parts.push({
            part_id: btn.dataset.partId || "",
            part_number: btn.dataset.partNumber || "",
            description: btn.dataset.description || "",
            qty: 1,
          });
        }
        if (partModal) partModal.hide();
        render();
      });
    }

    // ---------- timers ----------
    async function startTimer(laborId) {
      var data = await postJson(API.timerStart, { work_order_id: woId, labor_id: laborId });
      syncServerNow(data.server_now);
      applyTimeSummary(data.time_summary);
      var laborDesc = "";
      state.labors.forEach(function (l) { if (l.labor_id === laborId) laborDesc = l.description; });
      setBarTimer({
        work_order_id: woId,
        wo_number: (data.timer && data.timer.wo_number) || ($("mechWoTitle").textContent || "").replace("WO #", ""),
        labor_id: laborId,
        started_at: data.timer.started_at,
        labor_description: laborDesc,
      });
      if (data.stopped_previous && data.stopped_previous.work_order_id !== woId) {
        toast("Timer on WO #" + (data.stopped_previous.wo_number || "") + " stopped", "info");
      }
      render();
    }

    async function stopTimer() {
      var data = await postJson(API.timerStop, {});
      syncServerNow(data.server_now);
      applyTimeSummary(data.time_summary);
      setBarTimer(null);
      render();
    }

    // ---------- save ----------
    async function save() {
      if (state.saving) return;
      collectFromDom();

      var labors = state.labors
        .filter(function (l) { return l.description || l.parts.length; })
        .map(function (l) {
          return {
            labor_id: l.labor_id,
            description: l.description,
            issue_description: l.issue_description,
            parts: l.parts.map(function (p) {
              return {
                part_id: p.part_id,
                part_number: p.part_number,
                description: p.description,
                qty: p.qty || 1,
              };
            }),
          };
        });

      if (!labors.length) {
        toast("Add at least one job description or part", "warning");
        return;
      }

      var saveBtn = $("mechSaveBtn");
      state.saving = true;
      saveBtn.disabled = true;
      try {
        if (mode === "create") {
          var custSel = $("mechCustomerSelect");
          var unitSel = $("mechUnitSelect");
          if (!custSel.value || !unitSel.value) {
            toast("Select customer and unit first", "warning");
            return;
          }
          var data = await postJson(API.create, {
            customer_id: custSel.value,
            unit_id: unitSel.value,
            labors: labors,
          });
          toast("Work order #" + data.wo_number + " saved (In Progress)", "success");
          window.location.href = "/mechanic/work_orders/" + encodeURIComponent(data.id);
        } else {
          await postJson(API.update(woId), { labors: labors });
          toast("Saved (In Progress)", "success");
          await loadDetails();
        }
      } catch (e) {
        toast(e.message || "Failed to save", "error");
      } finally {
        state.saving = false;
        saveBtn.disabled = false;
      }
    }

    // ---------- events ----------
    laborsContainer.addEventListener("click", async function (e) {
      var card = e.target.closest(".mech-labor-card");
      if (!card) return;
      var idx = Number(card.dataset.laborIndex);
      var labor = state.labors[idx];
      if (!labor) return;

      if (e.target.closest("[data-role='add-part']")) {
        openPartPicker(idx);
        return;
      }

      if (e.target.closest("[data-role='remove-labor']")) {
        collectFromDom();
        state.labors.splice(idx, 1);
        if (!state.labors.length) state.labors.push(newLabor());
        render();
        return;
      }

      var removeBtn = e.target.closest(".mech-part-remove");
      if (removeBtn) {
        collectFromDom();
        var row = removeBtn.closest(".mech-part-row");
        labor.parts.splice(Number(row.dataset.partIndex), 1);
        render();
        return;
      }

      var minus = e.target.closest(".mech-qty-minus");
      var plus = e.target.closest(".mech-qty-plus");
      if (minus || plus) {
        var qRow = (minus || plus).closest(".mech-part-row");
        var input = qRow.querySelector(".mech-qty-input");
        var val = Math.max(1, parseInt(input.value, 10) || 1);
        input.value = minus ? Math.max(1, val - 1) : val + 1;
        return;
      }

      var timerBtn = e.target.closest("[data-role='timer-btn']");
      if (timerBtn) {
        timerBtn.disabled = true;
        try {
          collectFromDom();
          if (labor.time && labor.time.my_running) {
            await stopTimer();
          } else {
            await startTimer(labor.labor_id);
          }
        } catch (err) {
          toast(err.message || "Timer error", "error");
        } finally {
          timerBtn.disabled = false;
        }
      }
    });

    $("mechAddLaborBtn").addEventListener("click", function () {
      collectFromDom();
      state.labors.push(newLabor());
      render();
      if (mode === "edit") {
        toast("Start/Stop appears after saving the new job", "info");
      }
    });

    $("mechSaveBtn").addEventListener("click", save);

    bindPartPicker();
    bindPresetPicker();
    bindTimerBarStop(function () {
      if (mode === "edit") loadDetails();
    });

    if (mode === "edit") {
      loadDetails();
    } else {
      state.labors.push(newLabor());
      render();
      initCreatePickers();
      loadCurrentTimer();
    }
  }

  // ---------------- boot ----------------
  document.addEventListener("DOMContentLoaded", function () {
    initThemeToggle();
    var listPage = $("mechListPage");
    var detailsPage = $("mechDetailsPage");
    if (listPage) initListPage(listPage);
    if (detailsPage) initDetailsPage(detailsPage);
  });
})();
