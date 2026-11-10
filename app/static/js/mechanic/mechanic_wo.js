/* Механик-режим: список WO и карточка WO без цен.
   CSRF добавляет csrf.js автоматически; тосты — window.appAlert. */
(function () {
  "use strict";

  var API = {
    list: "/work_orders/api/mechanic/work_orders",
    // mp=1 — страница шлёт view_id сама (протокол слияния): сервер сводит её
    // Save с тем, что она видела, и с текущим WO.
    details: function (id) { return "/work_orders/api/mechanic/work_orders/" + encodeURIComponent(id) + "?mp=1"; },
    // Дешёвый опрос «WO изменился?» (labors_rev), вьюх не пишет.
    rev: function (id) { return "/work_orders/api/mechanic/work_orders/" + encodeURIComponent(id) + "/rev"; },
    create: "/work_orders/api/mechanic/work_orders",
    update: function (id) { return "/work_orders/api/mechanic/work_orders/" + encodeURIComponent(id); },
    timerStart: "/work_orders/api/mechanic/timers/start",
    timerStop: "/work_orders/api/mechanic/timers/stop",
    timerCurrent: "/work_orders/api/mechanic/timers/current",
    timersActive: "/work_orders/api/mechanic/timers/active",
    customers: "/work_orders/api/customers",
    units: "/work_orders/api/units",
    // WO, над которым уже работают по юниту (второй механик попадает в него).
    unitActiveWo: function (id) { return "/work_orders/api/mechanic/units/" + encodeURIComponent(id) + "/active_work_order"; },
    unitCreate: "/api/mobile/units",
    unitHistory: function (id) { return "/work_orders/api/mechanic/units/" + encodeURIComponent(id) + "/history"; },
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

  // Ошибка запроса несёт status и тело ответа (data): по ним обработчики
  // различают, например, 409 unit_has_active_work_order.
  function requestError(res, data) {
    var err = new Error((data && (data.message || data.error)) || ("Request failed (" + res.status + ")"));
    err.status = res.status;
    err.data = data || {};
    return err;
  }

  // Запрос не дошёл / ответ потерян: status 0, error "network".
  function networkError() {
    return requestError({ status: 0 }, { error: "network", message: "Network error — check the connection." });
  }

  async function getJson(url) {
    var res;
    try {
      res = await fetch(url, { headers: { "Accept": "application/json" } });
    } catch (e) {
      throw networkError();
    }
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok || data.ok === false) throw requestError(res, data);
    return data;
  }

  async function postJson(url, body) {
    var res;
    try {
      res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify(body || {}),
      });
    } catch (e) {
      throw networkError();
    }
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok || data.ok === false) throw requestError(res, data);
    return data;
  }

  function toast(msg, kind) {
    if (window.appAlert) window.appAlert(msg, kind || "info");
  }

  // Ответы с одним машинным кодом (без message) — понятным текстом.
  var ERROR_TEXTS = {
    estimate_locked_for_mechanics: "This work order is an estimate — only the office can change it.",
    estimate_cannot_track: "Time can't be tracked on an estimate — ask the office to turn it into a work order.",
    paid_cannot_edit: "Paid work orders cannot be edited.",
    paid_cannot_track: "This work order is locked — time can't be tracked on it.",
    labors_required: "Add at least one job description or part.",
    work_order_not_found: "This work order was deleted or is no longer available.",
    labor_not_found: "This job is no longer on the work order. Reload the page.",
    no_running_timer: "Your timer is already stopped.",
  };

  function errorText(e, fallback) {
    var data = (e && e.data) || {};
    if (data.message) return String(data.message);
    if (data.error && ERROR_TEXTS[data.error]) return ERROR_TEXTS[data.error];
    return (e && e.message) || fallback;
  }

  // Отказ «WO закрыт для правки»: подтверждён менеджером / смета (механику)
  // или оплачен (всем).
  var LOCK_ERRORS = {
    wo_confirmed: true,
    estimate_locked_for_mechanics: true,
    estimate_cannot_track: true,
    paid_cannot_edit: true,
    paid_cannot_track: true,
  };

  function isLockError(e) {
    var code = e && e.data && e.data.error;
    return !!(code && LOCK_ERRORS[code] === true);
  }

  // Ключ новой работы (client_key): живёт на строке до получения labor_id.
  // Повтор Save после потерянного ответа сервер узнаёт по нему и не
  // создаёт вторую такую же работу.
  function newClientKey() {
    try {
      if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
    } catch (e) { /* небезопасный контекст — ниже запасной вариант */ }
    return "ck-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12) +
      Math.random().toString(36).slice(2, 12);
  }

  function cleanList(arr) {
    return (Array.isArray(arr) ? arr : [])
      .map(function (x) { return String(x || "").trim(); })
      .filter(Boolean);
  }

  // Итог сохранения: заметки сервера (какие чужие работы/запчасти сохранены,
  // что удалил другой человек) и предупреждения — списком в SweetAlert;
  // без них — короткий тост.
  function showSaveOutcome(data, okMessage) {
    var notes = cleanList(data && data.merge_notes);
    var warnings = cleanList(data && data.warnings);
    if (!notes.length && !warnings.length) {
      toast(okMessage, "success");
      return;
    }
    if (typeof window.Swal === "undefined") {
      window.alert([okMessage].concat(notes, warnings).join("\n"));
      return;
    }
    function list(items) {
      return '<ul class="mb-2 ps-3">' + items.map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("") + "</ul>";
    }
    var intro = data && data.merged_foreign
      ? "Other people changed this work order at the same time. Both sets of changes were kept:"
      : "";
    var noAnim = { popup: "", backdrop: "" };
    window.Swal.fire({
      icon: warnings.length ? "warning" : "info",
      title: esc(okMessage),
      html: '<div class="text-start small">' +
        (notes.length ? (intro ? '<p class="mb-2">' + esc(intro) + "</p>" : "") + list(notes) : "") +
        (warnings.length ? list(warnings) : "") +
        "</div>",
      confirmButtonText: "OK",
      confirmButtonColor: "#1a7a42",
      showClass: noAnim,
      hideClass: noAnim,
    });
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
    // Статус не выбирается: сервер отдаёт механику только in_progress.
    var state = { q: "", page: 1 };
    var listEl = $("mechWoList");
    var emptyEl = $("mechListEmpty");
    var searchInput = $("mechSearchInput");

    function cardHtml(item) {
      var workingNow = item.working_now || [];
      var badge;
      if (item.status === "paid") {
        badge = '<span class="badge text-bg-success">Paid</span>';
      } else if (item.mechanic_done) {
        badge = '<span class="badge text-bg-success">Done</span>';
      } else if (item.is_in_progress) {
        // Единая логика с менеджером: зелёный — работают прямо сейчас,
        // серый — взят в работу, но в данный момент простаивает.
        badge = workingNow.length
          ? '<span class="badge text-bg-success">In Progress</span>'
          : '<span class="badge text-bg-secondary">In Progress</span>';
      } else {
        badge = '<span class="badge text-bg-warning">Open</span>';
      }
      var running = item.my_timer_running
        ? '<span class="mech-wo-running"><span class="mech-timer-dot"></span>My timer running</span>'
        : "";
      // ✓ — механики, уже поставившие Done (ждут остальных).
      var doneMechanics = item.mechanic_done ? [] : (item.mechanics_done || []);
      var idleMechanics = (item.mechanics || []).filter(function (m) {
        return workingNow.indexOf(m) === -1 && doneMechanics.indexOf(m) === -1;
      });
      var mechanicsLine = "";
      if (workingNow.length || idleMechanics.length || doneMechanics.length) {
        var parts = [];
        if (workingNow.length) parts.push('<span class="text-success">&#9679; ' + esc(workingNow.join(", ")) + "</span>");
        if (idleMechanics.length) parts.push('<span class="text-muted">' + esc(idleMechanics.join(", ")) + "</span>");
        if (doneMechanics.length) parts.push('<span class="text-success" title="Marked done — waiting for the other mechanics">&#10003; ' + esc(doneMechanics.join(", ")) + "</span>");
        mechanicsLine = '<div class="mech-wo-meta"><span>' + parts.join(", ") + "</span></div>";
      }
      return (
        '<a class="mech-wo-card" href="/mechanic/work_orders/' + encodeURIComponent(item.id) + '">' +
          '<div class="mech-wo-card-top">' +
            '<span class="mech-wo-number">WO #' + esc(item.wo_number) + "</span>" + badge +
          "</div>" +
          '<div class="mech-wo-meta">' +
            "<span>" + esc(item.customer || "-") +
              (item.customer_inactive ? ' <span class="badge text-bg-secondary">Inactive</span>' : "") +
            "</span>" +
            "<span>" + esc(item.unit || "-") + (item.date ? " · " + esc(item.date) : "") + "</span>" +
          "</div>" + mechanicsLine + running +
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
    // Пробег обязателен только для механика (менеджер на этой странице не спрашивается).
    var mechanicMode = root.dataset.mechanicMode === "1";
    // После создания WO страница перезагружается с ?mileage=<подтверждённый>:
    // второй раз не спрашиваем.
    var presetMileage = (new URLSearchParams(window.location.search).get("mileage") || "").trim();

    // labors: [{labor_id, client_key, description, issue_description, parts: [{part_id, part_number, description, qty, loadedQty}], time}]
    // mileage — текущий пробег юнита; mileageConfirmed — механик подтвердил
    // его в окне (обязательный шаг перед работой с WO, см. showMileageGate).
    //
    // Несколько человек на одном WO: Save сливается на сервере трёхсторонне
    // (base — edit view страницы viewId, current — WO в базе, mine — форма),
    // чужие работы не удаляются. rev — известная странице ревизия строк WO
    // (labors_rev); dirty/editSeq — несохранённые правки на странице: поверх
    // них страница никогда не перечитывает WO.
    var state = {
      labors: [], saving: false, mileage: "", mileageConfirmed: false, mileageRequired: mechanicMode,
      mileageRequiredOpen: mechanicMode,
      viewId: "", rev: 0, dirty: false, editSeq: 0, loading: false,
      revPollOff: false, revPollBusy: false, noticeRev: 0, locked: false, paid: false,
      // Неудачное перечитывание по опросу: {key, count, retryAt} — та же
      // ревизия повторяется с растущей паузой; refreshErrorShown — последняя
      // показанная ошибка (одна и та же не повторяется).
      refreshFail: null, refreshErrorShown: "",
    };
    var mileageModal = null;

    // Любая правка формы (текст, qty, состав работ/запчастей).
    function markDirty() {
      state.dirty = true;
      state.editSeq += 1;
    }

    function learnRev(value) {
      var n = Number(value);
      if (Number.isFinite(n) && n > state.rev) state.rev = n;
    }

    // ---------- замок ----------
    // WO закрыт для правки: оплачен, а механику — ещё смета или подтверждён
    // менеджером. Save выключен, баннер над работами объясняет почему (не
    // окно: замок приходит и сам по себе, из опроса). Сняли — всё обратно.
    var LOCKED_TEXT = "A manager locked this work order — changes can't be saved right now.";
    var LOCK_NOTICE = "A manager locked this work order — your unsaved changes can't be saved.";
    var CONFIRMED_TEXT = "This work order was confirmed by a manager and is locked.";

    function syncSaveButton() {
      var btn = $("mechSaveBtn");
      if (btn) btn.disabled = state.saving || state.locked;
    }

    // message — причина для баннера; без неё остаётся уже показанная.
    function applyLock(locked, message) {
      state.locked = !!locked;
      // Не закрыт — значит и не оплачен (locked всегда включает paid): WO
      // снова в работе — и пробег снова обязателен, как для открытого WO.
      if (!state.locked && state.paid) {
        state.paid = false;
        state.mileageRequired = state.mileageRequiredOpen;
      }
      var box = $("mechLockNotice");
      var textEl = $("mechLockNoticeText") || box;
      if (box) {
        if (state.locked) {
          if (message || !textEl.textContent) textEl.textContent = message || LOCKED_TEXT;
          box.classList.remove("d-none");
        } else {
          box.classList.add("d-none");
          textEl.textContent = "";
        }
      }
      syncSaveButton();
    }

    // Причина замка по деталям WO.
    function detailsLockText(data) {
      if (data.status === "paid") return ERROR_TEXTS.paid_cannot_edit;
      if (data.status === "estimate") return ERROR_TEXTS.estimate_locked_for_mechanics;
      return CONFIRMED_TEXT;
    }

    // Нижняя граница qty: загруженная строка с qty 0 (отложенная/справочная
    // позиция) остаётся 0, пока механик сам её не поменяет; новые — от 1.
    function minQty(part) {
      return part && part.loadedQty === 0 ? 0 : 1;
    }

    function parsedMileage(value) {
      var s = String(value || "").trim().replace(/,/g, "");
      if (!/^\d+$/.test(s)) return null;
      return parseInt(s, 10);
    }

    function validateMileageInput() {
      var btn = $("mechMileageConfirmBtn");
      var input = $("mechMileageInput");
      if (btn && input) btn.disabled = parsedMileage(input.value) === null;
    }

    function showMileageGate(unitLabel, lastKnown) {
      var modalEl = $("mechMileageModal");
      if (!modalEl || !window.bootstrap) return;
      var unitEl = $("mechMileageUnit");
      var lastEl = $("mechMileageLast");
      var input = $("mechMileageInput");
      if (unitEl) unitEl.textContent = unitLabel || "—";
      if (lastEl) lastEl.textContent = lastKnown ? Number(lastKnown).toLocaleString() : "—";
      if (input) input.value = lastKnown || "";
      validateMileageInput();
      if (!mileageModal) mileageModal = new window.bootstrap.Modal(modalEl, { backdrop: "static", keyboard: false });
      mileageModal.show();
      setTimeout(function () { if (input) { input.focus(); input.select(); } }, 250);
    }

    function setMileage(value) {
      state.mileage = String(value);
      state.mileageConfirmed = true;
      var el = $("mechWoMileage");
      if (el) el.value = state.mileage;
    }
    var laborsContainer = $("mechLaborsContainer");
    var pickerTargetIndex = null;
    var tickHandle = null;

    function newLabor() {
      return { labor_id: "", client_key: newClientKey(), description: "", issue_description: "", parts: [], time: null };
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
              // qty строки — как в WO (без подмены 0 на 1): иначе Save выдал
              // бы нетронутую строку за правку.
              '<input type="number" class="form-control mech-qty-input" inputmode="numeric" min="' + minQty(p) + '" value="' + esc(p.qty) + '">' +
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
            // описание проблемы (офис / заметка записи календаря) — механику только для чтения
            (labor.issue_description
              ? '<div class="text-muted small mt-1"><i class="bi bi-info-circle me-1"></i>' + esc(labor.issue_description) + "</div>"
              : "") +
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

    // План из календаря (payload.appointments: [{start_time, end_time,
    // mechanics: [{name}], presets: [{name}], note}]): когда запланировано,
    // кто в бригаде, заметка менеджера. Записей нет (или сервер без этого
    // поля) — карточка скрыта.
    function renderScheduled(items) {
      var card = $("mechScheduledCard");
      var body = $("mechScheduledBody");
      if (!card || !body) return;
      var rows = (Array.isArray(items) ? items : []).filter(function (a) { return a && a.start_time; });
      if (!rows.length) { card.style.display = "none"; return; }
      function pad2(v) { return String(v).padStart(2, "0"); }
      function fmtTime(d) { return pad2(d.getHours()) + ":" + pad2(d.getMinutes()); }
      function names(arr) {
        return (Array.isArray(arr) ? arr : [])
          .map(function (x) { return String((x && x.name) || "").trim(); })
          .filter(Boolean);
      }
      body.innerHTML = '<div class="mech-parts-title">Scheduled</div>' + rows.map(function (a) {
        // времена записи — настенные часы магазина без зоны, как в календаре
        var st = new Date(a.start_time);
        var en = a.end_time ? new Date(a.end_time) : null;
        var when = Number.isFinite(st.getTime())
          ? st.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) + " · " + fmtTime(st) +
            (en && Number.isFinite(en.getTime()) ? "–" + fmtTime(en) : "")
          : "—";
        var crew = names(a.mechanics);
        var presets = names(a.presets);
        var note = String(a.note || "").trim();
        return (
          '<div class="mech-info-row"><i class="bi bi-calendar-check"></i><span>' + esc(when) + "</span></div>" +
          '<div class="mech-info-row"><i class="bi bi-people"></i>' +
            (crew.length ? "<span>" + esc(crew.join(", ")) + "</span>" : '<span class="text-muted">No mechanic assigned</span>') +
          "</div>" +
          (presets.length ? '<div class="mech-info-row"><i class="bi bi-lightning-charge"></i><span>' + esc(presets.join(", ")) + "</span></div>" : "") +
          (note ? '<div class="mech-info-row"><i class="bi bi-chat-left-text"></i><span class="text-muted">' + esc(note) + "</span></div>" : "")
        );
      }).join('<hr class="my-2">');
      card.style.display = "";
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
          if (!qtyInput) return;
          // Пустое/мусорное поле — qty строки не меняем.
          var v = parseInt(qtyInput.value, 10);
          if (Number.isFinite(v)) part.qty = Math.max(minQty(part), v);
        });
      });
    }

    function renderStatusBadge(status) {
      var statusEl = $("mechWoStatus");
      if (!statusEl) return;
      var s = status || "open";
      var cls = s === "paid" ? "text-bg-success" : (s === "in_progress" ? "text-bg-info" : "text-bg-warning");
      var label = s === "in_progress" ? "In Progress" : s.charAt(0).toUpperCase() + s.slice(1);
      statusEl.innerHTML = '<span class="badge ' + cls + '">' + esc(label) + "</span>";
    }

    // Строка работы из ответа сервера (детали / Save): qty — как в WO.
    function laborFromServer(l) {
      return {
        labor_id: l.labor_id || "",
        description: l.description || "",
        issue_description: l.issue_description || "",
        parts: (l.parts || []).map(function (p) {
          var q = Number(p.qty);
          if (!Number.isFinite(q)) q = 0;
          return {
            part_id: p.part_id || "",
            part_number: p.part_number || "",
            description: p.description || "",
            qty: q,
            loadedQty: q,
          };
        }),
        time: l.time ? normalizeTimeBucket(l.time) : null,
      };
    }

    // Только что добавленные пустые работы (на сервер не уходили) при
    // перечитывании WO не теряем.
    function pendingBlankLabors() {
      return state.labors.filter(function (l) {
        return !l.labor_id && !String(l.description || "").trim() && !l.parts.length;
      });
    }

    function typingInLabors() {
      var el = document.activeElement;
      return !!(el && laborsContainer.contains(el) && (el.tagName === "TEXTAREA" || el.tagName === "INPUT"));
    }

    // Ответ Save. Правок за время запроса не было — берём строки из ответа
    // (там чужие изменения, слитые сервером, и id новых работ); были —
    // оставляем свои, новым работам проставляем id по client_key, base
    // следующего Save — sent view (то, что мы отправили). true — строки
    // взяты из ответа.
    function applySaveResponse(data, seq) {
      data = data || {};
      learnRev(data.labors_rev);
      if (data.status) renderStatusBadge(data.status);
      if (state.editSeq === seq && Array.isArray(data.labors)) {
        state.labors = data.labors.map(laborFromServer).concat(pendingBlankLabors());
        if (!state.labors.length) state.labors.push(newLabor());
        if (typeof data.view_id === "string") {
          state.viewId = data.merged_foreign ? String(data.merged_view_id || data.view_id || "") : data.view_id;
        }
        state.dirty = false;
        render();
        return true;
      }
      collectFromDom();
      var keyed = (data.client_keys && typeof data.client_keys === "object") ? data.client_keys : {};
      state.labors.forEach(function (l) {
        if (!l.labor_id && l.client_key && keyed[l.client_key]) l.labor_id = String(keyed[l.client_key]);
      });
      if (typeof data.view_id === "string") state.viewId = data.view_id;
      // Механик печатает — не перерисовываем (фокус и курсор остались бы
      // без поля); Start/Stop новой работы появится при следующей отрисовке.
      if (!typingInLabors()) render();
      return false;
    }

    // refresh — перечитывание уже открытой страницы (опрос ревизии, таймер,
    // Save): правки, сделанные пока шёл запрос, не затираем, а ошибку
    // показываем тостом, не окном (перечитывание идёт и само по себе).
    // true — детали применены, false — запрос не удался, null — пропущены
    // (пока шёл запрос, на странице начали править).
    async function loadDetails(opts) {
      var refresh = !!(opts && opts.refresh);
      var seq = state.editSeq;
      state.loading = true;
      try {
        var data = await getJson(API.details(woId));
        if (refresh && state.editSeq !== seq) return null;
        syncServerNow(data.server_now);
        // Base следующего Save и ревизия — из этих же деталей.
        if (typeof data.view_id === "string") state.viewId = data.view_id;
        if (data.labors_rev !== undefined && Number.isFinite(Number(data.labors_rev))) {
          state.rev = Number(data.labors_rev);
        }
        state.paid = data.status === "paid";
        // Закрыт для правки: оплачен (механику — ещё смета/подтверждён).
        var locked = state.paid ||
          (mechanicMode && (data.status === "estimate" || !!data.manager_confirmed));

        $("mechWoTitle").textContent = "WO #" + (data.wo_number || "");
        renderStatusBadge(data.status);

        $("mechWoInfoCard").style.display = "";
        $("mechInfoCustomer").textContent = (data.customer && data.customer.label) || "—";
        $("mechInfoUnit").textContent = (data.unit && data.unit.label) || "—";
        $("mechInfoDate").textContent = data.date || "—";
        renderScheduled(data.appointments);

        // mileageRequiredOpen — то же для неоплаченного WO (вернётся при Unpaid).
        state.mileageRequiredOpen = !!data.mileage_required;
        state.mileageRequired = state.mileageRequiredOpen && data.status !== "paid";
        var unitMileage = (data.unit && data.unit.mileage != null && data.unit.mileage !== "") ? String(data.unit.mileage) : "";
        if (!state.mileageConfirmed && presetMileage && parsedMileage(presetMileage) !== null) {
          setMileage(parsedMileage(presetMileage)); // подтверждён при создании WO
        }
        // Пробег не подтверждён — показываем пробег юнита, но не поверх поля,
        // в котором сейчас печатают: введённое запишет change на blur
        // (перезапись значения скриптом съела бы и его, и сам change).
        var mileageEl = $("mechWoMileage");
        var typingMileage = !!mileageEl && document.activeElement === mileageEl;
        if (!state.mileageConfirmed && !typingMileage) {
          state.mileage = unitMileage;
          if (mileageEl) mileageEl.value = unitMileage;
        }

        var historyBtn = $("mechUnitHistoryBtn");
        if (historyBtn) {
          var historyUnitId = (data.unit && data.unit.id) || "";
          historyBtn.style.display = historyUnitId ? "" : "none";
          historyBtn.dataset.unitId = historyUnitId;
        }

        state.labors = (data.labors || []).map(laborFromServer).concat(refresh ? pendingBlankLabors() : []);
        if (!state.labors.length) state.labors.push(newLabor());
        state.dirty = false;

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

        applyLock(locked, locked ? detailsLockText(data) : "");
        state.refreshErrorShown = "";
        if (data.status === "paid") {
          // Окно — только при открытии; оплату, пришедшую при перечитывании,
          // показывает баннер.
          if (!refresh) toast("Paid work orders cannot be edited", "warning");
        } else {
          var gateEl = $("mechMileageModal");
          var gateOpen = !!(gateEl && gateEl.classList.contains("show"));
          if (state.mileageRequired && !state.mileageConfirmed && !gateOpen) {
            showMileageGate(data.unit && data.unit.label, unitMileage);
          }
        }
        return true;
      } catch (e) {
        // WO удалён — опрос то же сообщение не повторит.
        if (e && e.data && e.data.error === "work_order_not_found") state.revPollOff = true;
        if (isLockError(e)) {
          // Механику WO закрыт (подтверждён / смета): причина — в баннере,
          // Save выключен; опрос закрытый WO больше не перечитывает. Окно —
          // только при открытии страницы.
          applyLock(true, errorText(e, LOCKED_TEXT));
          if (!refresh) toast(errorText(e, LOCKED_TEXT), "warning");
          return false;
        }
        var msg = errorText(e, refresh ? "Couldn't refresh this work order." : "Failed to load work order");
        if (!refresh) {
          toast(msg, "error");
        } else if (msg !== state.refreshErrorShown) {
          toast(msg, "info");
        }
        state.refreshErrorShown = msg;
        return false;
      } finally {
        state.loading = false;
      }
    }

    // ---------- customer / unit pickers (create mode) ----------
    function goToExistingWorkOrder(wo, message) {
      toast((message || ("Work order #" + wo.wo_number + " is already in progress for this unit.")) + " Opening it\u2026", "info");
      window.location.href = "/mechanic/work_orders/" + encodeURIComponent(wo.id);
    }

    // true — по юниту уже есть WO в работе и мы уходим в него.
    async function openActiveUnitWorkOrder(unitId) {
      try {
        var data = await getJson(API.unitActiveWo(unitId));
        if (data && data.work_order && data.work_order.id) {
          goToExistingWorkOrder(data.work_order, data.message);
          return true;
        }
      } catch (e) {
        // Проверка не удалась — создание всё равно защищено сервером (409).
      }
      return false;
    }

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
      var historyCreateBtn = $("mechUnitHistoryCreateBtn");
      unitSel.addEventListener("change", async function () {
        if (historyCreateBtn) historyCreateBtn.disabled = !unitSel.value;
        // Новый WO: пробег подтверждается сразу при выборе юнита.
        state.mileageConfirmed = false;
        state.mileage = "";
        if (!unitSel.value) return;
        // Один юнит в цеху — один WO: по юниту уже идёт работа (другой
        // механик или просто in progress) → открываем тот WO, а не заводим
        // новый. Сервер дублирует проверку на создании (409).
        if (mechanicMode && await openActiveUnitWorkOrder(unitSel.value)) return;
        if (state.mileageRequired) {
          var opt = unitSel.options[unitSel.selectedIndex];
          showMileageGate(opt ? opt.textContent : "", opt ? (opt.dataset.mileage || "") : "");
        }
      });

      custSel.addEventListener("change", async function () {
        unitSel.innerHTML = '<option value="">Select unit...</option>';
        unitSel.disabled = !custSel.value;
        if (newUnitBtn) newUnitBtn.disabled = !custSel.value;
        if (historyCreateBtn) historyCreateBtn.disabled = true;
        if (!custSel.value) return;
        try {
          var data = await getJson(API.units + "?customer_id=" + encodeURIComponent(custSel.value));
          (data.items || []).forEach(function (u) {
            var opt = document.createElement("option");
            opt.value = u.id;
            opt.textContent = u.label;
            if (u.mileage != null && u.mileage !== "") opt.dataset.mileage = String(u.mileage);
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
          // Пробег, введённый в форме нового юнита, — подсказка для окна пробега.
          var typedMileage = ($("mechUnitMileage").value || "").trim();
          if (typedMileage) opt.dataset.mileage = typedMileage;
          unitSel.appendChild(opt);
          unitSel.value = data.id;
          // Окно пробега открываем после закрытия модалки юнита (две модалки
          // друг на друге ломают скролл страницы).
          if (modal) {
            modalEl.addEventListener("hidden.bs.modal", function () { unitSel.dispatchEvent(new Event("change")); }, { once: true });
            modal.hide();
          } else {
            unitSel.dispatchEvent(new Event("change"));
          }
          toast("Unit created", "success");
        } catch (e) {
          toast(e.message || "Failed to create unit", "error");
        } finally {
          btn.disabled = false;
        }
      });
    }

    // ---------- unit history (без цен) ----------
    var unitHistoryModalEl = $("mechUnitHistoryModal");
    var unitHistoryModal = null;
    var unitHistoryCache = {};

    function historyStatusBadge(status) {
      var s = String(status || "open");
      var cls = s === "completed" ? "text-bg-success" : (s === "in_progress" ? "text-bg-info" : "text-bg-warning");
      var label = s === "in_progress" ? "In Progress" : s.charAt(0).toUpperCase() + s.slice(1);
      return '<span class="badge ' + cls + '">' + esc(label) + "</span>";
    }

    function renderUnitHistory(data) {
      var box = $("mechUnitHistoryBody");
      var labelEl = $("mechUnitHistoryUnitLabel");
      if (labelEl) labelEl.textContent = data.unit_label || "";
      var items = data.items || [];
      if (!items.length) {
        box.innerHTML = '<div class="text-muted small">No previous work orders for this unit.</div>';
        return;
      }
      box.innerHTML = items.map(function (wo) {
        var jobs = (wo.labors || []).map(function (l) {
          var parts = (l.parts || []).map(function (p) {
            var label = [p.part_number, p.description].filter(Boolean).join(" — ");
            return '<div class="mech-history-part">' + esc(label || "Part") +
              ' <span class="mech-history-qty">×' + (p.qty || 0) + "</span></div>";
          }).join("");
          return '<div class="mech-history-job">' +
            (l.description ? '<div class="mech-history-job-desc">' + esc(l.description) + "</div>" : "") +
            parts +
          "</div>";
        }).join("");
        return '<div class="mech-history-item">' +
          '<div class="mech-history-head">' +
            '<span class="mech-history-wo">WO #' + esc(wo.wo_number == null ? "" : wo.wo_number) + "</span>" +
            historyStatusBadge(wo.status) +
            '<span class="mech-history-date">' + esc(wo.date || "") + "</span>" +
          "</div>" +
          (jobs || '<div class="text-muted small">No jobs recorded.</div>') +
        "</div>";
      }).join("");
    }

    async function openUnitHistory(unitId) {
      if (!unitId || !unitHistoryModalEl) return;
      if (!unitHistoryModal && window.bootstrap) unitHistoryModal = new window.bootstrap.Modal(unitHistoryModalEl);
      if (unitHistoryModal) unitHistoryModal.show();

      var box = $("mechUnitHistoryBody");
      var cacheKey = unitId + "|" + (woId || "");
      if (unitHistoryCache[cacheKey]) {
        renderUnitHistory(unitHistoryCache[cacheKey]);
        return;
      }
      box.innerHTML = '<div class="text-muted small">Loading...</div>';
      try {
        var url = API.unitHistory(unitId) + (woId ? "?exclude=" + encodeURIComponent(woId) : "");
        var data = await getJson(url);
        unitHistoryCache[cacheKey] = data;
        renderUnitHistory(data);
      } catch (e) {
        box.innerHTML = '<div class="text-danger small">' + esc(e.message || "Failed to load history") + "</div>";
      }
    }

    function bindUnitHistory() {
      var infoBtn = $("mechUnitHistoryBtn");
      if (infoBtn) {
        infoBtn.addEventListener("click", function () {
          openUnitHistory(infoBtn.dataset.unitId || "");
        });
      }
      var createBtn = $("mechUnitHistoryCreateBtn");
      if (createBtn) {
        createBtn.addEventListener("click", function () {
          var unitSel = $("mechUnitSelect");
          openUnitHistory(unitSel ? unitSel.value : "");
        });
      }
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
            client_key: newClientKey(),
            description: preset.name || preset.description || "",
            issue_description: "",
            // Сервер по preset_id берёт часы/ставку пресета для биллинга.
            preset_id: btn.dataset.presetId || "",
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
          markDirty();
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
        markDirty();
        if (partModal) partModal.hide();
        render();
      });
    }

    // ---------- timers ----------
    // Ответ таймера несёт ревизию WO после всех его записей: Start двигает
    // её всегда, Stop — когда пересчитал часы. Молча принять её нельзя: в
    // неё может войти чужая правка, сделанная до клика, и чистая страница
    // так её и не показала бы. Поэтому ревизия выросла и правок нет —
    // перечитываем WO (как Stop в нижней панели); есть правки — запоминаем
    // ревизию (своё действие не выдаём за чужое, чужое сольёт Save); идёт
    // сохранение/загрузка — ничего: ревизию сверит их ответ и опрос.
    function isThisWo(timer) {
      return !timer || !timer.work_order_id || String(timer.work_order_id) === String(woId);
    }

    function afterOwnTimerWrite(respRev, revBefore) {
      var n = Number(respRev);
      if (!Number.isFinite(n) || n <= Math.max(revBefore, state.rev)) return;
      if (state.dirty) {
        learnRev(n);
        return;
      }
      if (state.saving || state.loading) return;
      loadDetails({ refresh: true });
    }

    async function startTimer(laborId) {
      var revBefore = state.rev;
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
      // Напечатанное, пока шёл запрос, — в state до перерисовки.
      collectFromDom();
      render();
      afterOwnTimerWrite(data.labors_rev, revBefore);
    }

    async function stopTimer() {
      var revBefore = state.rev;
      var data = await postJson(API.timerStop, {});
      syncServerNow(data.server_now);
      // Ревизия в ответе — того WO, где шёл таймер; чужую не сравниваем.
      var thisWo = isThisWo(data.timer);
      if (thisWo) applyTimeSummary(data.time_summary);
      setBarTimer(null);
      collectFromDom();
      render();
      if (thisWo) afterOwnTimerWrite(data.labors_rev, revBefore);
    }

    // ---------- save ----------
    // Сохранённые работы (с labor_id) уходят всегда, даже опустевшие: сервер
    // удаляет работу, только когда её убрали из списка. Новые — когда в них
    // что-то есть, со своим client_key. qty — как есть (новые строки
    // начинаются с 1, загруженные — со значения из WO).
    function buildLaborsPayload() {
      return state.labors
        .filter(function (l) { return l.labor_id || l.description || l.parts.length; })
        .map(function (l) {
          var row = {
            labor_id: l.labor_id,
            description: l.description,
            issue_description: l.issue_description,
            preset_id: l.preset_id || "",
            parts: l.parts.map(function (p) {
              var q = Number(p.qty);
              return {
                part_id: p.part_id,
                part_number: p.part_number,
                description: p.description,
                qty: Number.isFinite(q) ? q : minQty(p),
              };
            }),
          };
          if (!l.labor_id && l.client_key) row.client_key = l.client_key;
          return row;
        });
    }

    // Тело Save/подтверждения пробега для существующего WO: протокол
    // слияния (view_id — с чем страница начинала правку).
    function updatePayload(labors) {
      return { labors: labors, unit_mileage: state.mileage, merge_protocol: 1, view_id: state.viewId };
    }

    async function save() {
      if (state.saving) return;
      collectFromDom();

      var labors = buildLaborsPayload();

      if (!labors.length) {
        toast("Add at least one job description or part", "warning");
        return;
      }

      // Пробег — обязательный шаг для механика: без подтверждения сервер отклонит.
      if (state.mileageRequired && (!state.mileageConfirmed || parsedMileage(state.mileage) === null)) {
        var selUnit = mode === "create" ? $("mechUnitSelect") : null;
        var selOpt = selUnit && selUnit.value ? selUnit.options[selUnit.selectedIndex] : null;
        showMileageGate(
          mode === "create" ? (selOpt ? selOpt.textContent : "") : $("mechInfoUnit").textContent,
          mode === "create" ? (selOpt ? (selOpt.dataset.mileage || "") : "") : state.mileage
        );
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
            unit_mileage: state.mileage,
          });
          toast("Work order #" + data.wo_number + " saved (In Progress)", "success");
          window.location.href = "/mechanic/work_orders/" + encodeURIComponent(data.id) +
            (state.mileageConfirmed ? "?mileage=" + encodeURIComponent(state.mileage) : "");
        } else {
          var seq = state.editSeq;
          var resp = await postJson(API.update(woId), updatePayload(labors));
          var adopted = applySaveResponse(resp, seq);
          if (!adopted && state.editSeq === seq) {
            // Ответ без строк (старый сервер) — перечитываем WO, как раньше.
            state.saving = false;
            await loadDetails({ refresh: true });
          }
          showSaveOutcome(resp, "Saved (In Progress)");
        }
      } catch (e) {
        // Пока механик заполнял форму, коллега уже завёл WO по этому юниту —
        // уходим в него (сервер отказал создавать дубль).
        if (e && e.data && e.data.error === "unit_has_active_work_order" && e.data.work_order) {
          goToExistingWorkOrder(e.data.work_order, e.data.message);
          return;
        }
        if (e && e.status === 0) {
          // Правки остались на странице; повторный Save безопасен (новые
          // работы сервер узнает по client_key).
          toast("Network error — your changes are still on the page. Check the connection and click Save again.", "error");
        } else {
          // WO закрыли, пока страница открыта, — баннер и выключенный Save
          // до снятия замка (его заметит опрос).
          var lockErr = isLockError(e);
          if (lockErr) applyLock(true, errorText(e, LOCKED_TEXT));
          toast(errorText(e, "Failed to save"), lockErr || (e && e.status === 409) ? "warning" : "error");
        }
      } finally {
        state.saving = false;
        // WO закрыт (оплачен / замок менеджера) — Save не включаем.
        syncSaveButton();
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
        markDirty();
        render();
        return;
      }

      var removeBtn = e.target.closest(".mech-part-remove");
      if (removeBtn) {
        collectFromDom();
        var row = removeBtn.closest(".mech-part-row");
        labor.parts.splice(Number(row.dataset.partIndex), 1);
        markDirty();
        render();
        return;
      }

      var minus = e.target.closest(".mech-qty-minus");
      var plus = e.target.closest(".mech-qty-plus");
      if (minus || plus) {
        var qRow = (minus || plus).closest(".mech-part-row");
        var input = qRow.querySelector(".mech-qty-input");
        var part = labor.parts[Number(qRow.dataset.partIndex)];
        var lo = minQty(part);
        var val = parseInt(input.value, 10);
        if (!Number.isFinite(val)) val = part && Number.isFinite(Number(part.qty)) ? Number(part.qty) : lo;
        input.value = minus ? Math.max(lo, val - 1) : Math.max(lo, val + 1);
        markDirty();
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
          // Смета / подтверждён / оплачен: причина — в окне (ответ на клик)
          // и в баннере, Save выключен до снятия замка.
          var timerLock = isLockError(err);
          if (timerLock) applyLock(true, errorText(err, LOCKED_TEXT));
          toast(errorText(err, "Timer error"), timerLock ? "warning" : "error");
        } finally {
          timerBtn.disabled = false;
        }
      }
    });

    // Набор текста / qty в карточках — несохранённая правка.
    laborsContainer.addEventListener("input", function (e) {
      if (e.target && (e.target.classList.contains("mech-labor-desc") || e.target.classList.contains("mech-qty-input"))) {
        markDirty();
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

    // ---------- mileage gate ----------
    var mileageInputEl = $("mechMileageInput");
    if (mileageInputEl) mileageInputEl.addEventListener("input", validateMileageInput);
    var mileageConfirmBtn = $("mechMileageConfirmBtn");
    if (mileageConfirmBtn) {
      mileageConfirmBtn.addEventListener("click", async function () {
        var value = parsedMileage(mileageInputEl ? mileageInputEl.value : "");
        if (value === null) return;
        setMileage(value);
        if (mileageModal) mileageModal.hide();
        // Существующий WO: записываем пробег сразу, не дожидаясь Save.
        if (mode === "edit" && woId) {
          if (state.saving) return;
          collectFromDom();
          var labors = buildLaborsPayload();
          if (!labors.length) return;
          var seq = state.editSeq;
          var saveBtnEl = $("mechSaveBtn");
          state.saving = true;
          if (saveBtnEl) saveBtnEl.disabled = true;
          try {
            var resp = await postJson(API.update(woId), updatePayload(labors));
            // Ответ несёт строки WO (с id и чужими правками) — берём их, иначе
            // новые работы остались бы без id и пересоздавались бы при Save.
            var adopted = applySaveResponse(resp, seq);
            if (!adopted && state.editSeq === seq) {
              state.saving = false;
              await loadDetails({ refresh: true });
            }
            showSaveOutcome(resp, "Mileage saved");
          } catch (e) {
            var mileageLock = isLockError(e);
            if (mileageLock) applyLock(true, errorText(e, LOCKED_TEXT));
            toast(errorText(e, "Failed to save mileage"), mileageLock ? "warning" : "error");
          } finally {
            state.saving = false;
            syncSaveButton();
          }
        }
      });
    }
    var woMileageEl = $("mechWoMileage");
    if (woMileageEl) {
      woMileageEl.addEventListener("change", function () {
        var value = parsedMileage(woMileageEl.value);
        if (value !== null) setMileage(value);
      });
    }

    bindPartPicker();
    bindPresetPicker();
    bindUnitHistory();
    // Stop в нижней панели: несохранённые правки на странице не трогаем —
    // обновляем только время; без правок — перечитываем WO целиком.
    // Ревизию ответа запоминаем только при правках (своё действие не
    // «чужое»); во время сохранения/загрузки её сверит опрос — иначе чужая
    // правка, вошедшая в ту же ревизию, осталась бы незамеченной.
    bindTimerBarStop(function (data) {
      if (mode !== "edit") return;
      if (!isThisWo(data && data.timer)) return;
      if (!state.dirty && !state.saving && !state.loading) {
        loadDetails({ refresh: true });
        return;
      }
      if (state.dirty) learnRev(data && data.labors_rev);
      collectFromDom();
      applyTimeSummary(data && data.time_summary);
      render();
    });

    // ---------- живое обновление (другие механики / офис) ----------
    // Раз в 20 с (вкладка на экране) спрашиваем ревизию WO. Сдвинулась не
    // нами: без несохранённых правок — перечитываем WO; с правками — один
    // раз подсказываем, что при Save их сольют с чужими. Закрытый механику
    // WO (смета / подтверждён / оплачен) не перечитываем вовсе: детали ему
    // отказывают (403/400) или только для чтения — баннер о замке один раз,
    // дальше опрос только спрашивает ревизию, пока замок не снимут. Старый
    // сервер без эндпоинта (404 not_found) — опрос молча выключается.
    var REV_POLL_MS = 20000;
    // Перечитать ту же ревизию не удалось — следующая попытка через 40 с,
    // 80 с, 160 с… но не реже, чем раз в 5 минут.
    var REFRESH_RETRY_MAX_MS = 5 * 60 * 1000;
    var REV_NOTICE = "Someone else just updated this work order. When you save, their changes will be merged with yours.";
    var PAID_DIRTY_NOTICE = "This work order was paid — your unsaved changes can't be saved.";

    async function pollRev() {
      if (mode !== "edit" || !woId || state.revPollOff || document.hidden) return;
      if (state.saving || state.loading || state.revPollBusy) return;
      // Открытое окно (пробег, запчасти, пресеты) перечитыванием не сбиваем.
      if (document.querySelector(".modal.show")) return;
      state.revPollBusy = true;
      try {
        var data = await getJson(API.rev(woId));
        if (state.saving || state.loading) return;
        var rev = Number(data.labors_rev);
        var revMoved = Number.isFinite(rev) && rev > state.rev;
        // Механику статус не отдают: locked = оплачен / смета / подтверждён.
        var lockedNow = data.locked === true || data.status === "paid";
        var lockChanged = lockedNow !== state.locked;
        if (!revMoved && !lockChanged) return;

        if (lockedNow && (mechanicMode || state.dirty)) {
          // Ревизию не запоминаем: когда замок снимут, страница сверит её
          // и перечитает WO (или скажет о чужих правках).
          if (lockChanged) {
            applyLock(true, !state.dirty ? LOCKED_TEXT : (mechanicMode ? LOCK_NOTICE : PAID_DIRTY_NOTICE));
          }
          return;
        }
        if (!state.dirty) {
          var key = String(rev) + "|" + lockedNow;
          var fail = state.refreshFail;
          if (fail && fail.key === key && Date.now() < fail.retryAt) return;
          var ok = await loadDetails({ refresh: true });
          if (ok === false) {
            var count = fail && fail.key === key ? fail.count + 1 : 1;
            state.refreshFail = {
              key: key,
              count: count,
              retryAt: Date.now() + Math.min(REV_POLL_MS * Math.pow(2, count), REFRESH_RETRY_MAX_MS),
            };
          } else if (ok) {
            state.refreshFail = null;
          }
          return;
        }
        // Правки на странице. Замок сняли — Save снова доступен (правки на
        // месте); чужие изменения — подсказка один раз на ревизию.
        if (lockChanged) applyLock(false);
        if (revMoved && rev > state.noticeRev) {
          state.noticeRev = rev;
          toast(REV_NOTICE, "info");
        }
      } catch (e) {
        if (e && e.status === 404) {
          // not_found — сервер без эндпоинта; work_order_not_found — WO удалён.
          state.revPollOff = true;
          if (e.data && e.data.error === "work_order_not_found") toast(ERROR_TEXTS.work_order_not_found, "warning");
        }
        // Сеть и прочее — молча, до следующего тика.
      } finally {
        state.revPollBusy = false;
      }
    }

    if (mode === "edit") {
      loadDetails();
      setInterval(pollRev, REV_POLL_MS);
      document.addEventListener("visibilitychange", function () { if (!document.hidden) pollRev(); });
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
