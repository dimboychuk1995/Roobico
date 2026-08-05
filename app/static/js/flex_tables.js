// Flex tables: скрытие/показ колонок, ресайз ширины, сортировка (через
// TableSort из public.js) для ВСЕХ таблиц с <thead>, с сохранением настроек
// per-user per-table.
//
// Хранение: если сервер заинжектил window.__tablePrefs (залогиненный юзер) —
// настройки читаются оттуда и сохраняются POST'ом на /api/table-prefs
// (debounce). Иначе (нет идентичности — напр. клиентский портал) — fallback
// на localStorage.
//
// Идентичность таблицы: путь страницы (ObjectId-сегменты нормализуются в
// ":id") + сигнатура заголовков. Смена набора колонок в новой версии — новый
// ключ, настройки просто сбрасываются на дефолт.
//
// Исключения: data-flex-table="off" на <table> или любом предке; таблицы без
// thead. Сортировка не навешивается на таблицы с формами в tbody (редакторы
// строк — там порядок DOM-строк несёт смысл), скрытие/ресайз работают везде.
(function () {
  "use strict";
  if (window.__flexTablesBound) return;
  window.__flexTablesBound = true;

  var SERVER_PREFS = (typeof window.__tablePrefs === "object" && window.__tablePrefs) || null;
  var LS_KEY = "roobico_table_prefs";
  var saveTimers = {};
  var seq = 0;

  // ── ключи и настройки ────────────────────────────────────────────

  function pageKey() {
    return (window.location.pathname || "/")
      .replace(/[0-9a-f]{24}/gi, ":id")
      .replace(/\/+$/, "") || "/";
  }

  function normLabel(s) {
    return String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  }

  function headerLabels(table) {
    var out = [];
    var row = table.tHead && table.tHead.rows[0];
    if (!row) return out;
    for (var i = 0; i < row.cells.length; i++) {
      var th = row.cells[i].cloneNode(true);
      var junk = th.querySelectorAll(".sort-indicator,.ft-resize-handle");
      for (var j = 0; j < junk.length; j++) junk[j].remove();
      out.push(normLabel(th.textContent) || ("col" + i));
    }
    return out;
  }

  function hashStr(s) {
    var h = 5381;
    for (var i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
    return h.toString(36);
  }

  function tableKey(table) {
    if (!table._ftKey) {
      var sig = headerLabels(table).join(",");
      var base = pageKey().replace(/[^A-Za-z0-9:_-]+/g, "_") + "|" + hashStr(sig);
      // Две одинаковые таблицы на странице — нумеруем по позиции в DOM.
      // Счётчик по живому DOM (а не глобальный), чтобы SPA-подмена контента
      // не сдвигала ключи.
      var twins = document.querySelectorAll('table[data-ft-base]');
      var n = 0;
      for (var i = 0; i < twins.length; i++) {
        if (twins[i] !== table && twins[i].getAttribute("data-ft-base") === base &&
            twins[i].isConnected) n++;
      }
      table.setAttribute("data-ft-base", base);
      table._ftKey = n > 0 ? base + "|" + (n + 1) : base;
    }
    return table._ftKey;
  }

  function loadLocal() {
    try { return JSON.parse(localStorage.getItem(LS_KEY) || "{}") || {}; }
    catch (e) { return {}; }
  }

  function getPrefs(key) {
    if (SERVER_PREFS) return SERVER_PREFS[key] || null;
    return loadLocal()[key] || null;
  }

  function setPrefs(key, prefs, flush) {
    if (SERVER_PREFS) {
      if (prefs) SERVER_PREFS[key] = prefs; else delete SERVER_PREFS[key];
      clearTimeout(saveTimers[key]);
      var doSave = function () {
        return fetch("/api/table-prefs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key: key, prefs: prefs }),
        }).catch(function () { /* сеть моргнула — настройки останутся в памяти */ });
      };
      if (flush) return doSave();          // немедленно (перед reload)
      saveTimers[key] = setTimeout(doSave, 600);
      return null;
    }
    var all = loadLocal();
    if (prefs) all[key] = prefs; else delete all[key];
    try { localStorage.setItem(LS_KEY, JSON.stringify(all)); } catch (e) { /* quota */ }
    return null;
  }

  function readPrefs(table) {
    return getPrefs(tableKey(table)) || {};
  }

  function writePrefs(table, patch) {
    var cur = readPrefs(table);
    Object.keys(patch).forEach(function (k) {
      if (patch[k] === null) delete cur[k]; else cur[k] = patch[k];
    });
    setPrefs(tableKey(table), Object.keys(cur).length ? cur : null);
  }

  // ── скрытие колонок (через <style>, чтобы покрывать будущие строки) ──

  function ensureStyleEl(table) {
    if (!table._ftStyle) {
      table._ftStyle = document.createElement("style");
      document.head.appendChild(table._ftStyle);
      table.setAttribute("data-ft-id", "ft" + (++seq));
    }
    return table._ftStyle;
  }

  function applyHidden(table) {
    var hidden = readPrefs(table).hidden || [];
    var labels = headerLabels(table);
    var rules = [];
    hidden.forEach(function (name) {
      var idx = labels.indexOf(name);
      if (idx < 0) return;
      rules.push('table[data-ft-id="' + table.getAttribute("data-ft-id") +
        '"] tr > *:nth-child(' + (idx + 1) + '):not([colspan])');
    });
    ensureStyleEl(table).textContent = rules.length
      ? rules.join(",") + "{display:none;}" : "";
  }

  // ── ресайз колонок ───────────────────────────────────────────────

  function ensureColgroup(table) {
    if (table._ftCols) return table._ftCols;
    var count = (table.tHead.rows[0] || {}).cells ? table.tHead.rows[0].cells.length : 0;
    var cg = table.querySelector("colgroup[data-ft]");
    if (!cg) {
      cg = document.createElement("colgroup");
      cg.setAttribute("data-ft", "1");
      for (var i = 0; i < count; i++) cg.appendChild(document.createElement("col"));
      table.insertBefore(cg, table.firstChild);
    }
    table._ftCols = cg.children;
    return table._ftCols;
  }

  function applyWidths(table) {
    var widths = readPrefs(table).widths;
    if (!widths || !Object.keys(widths).length) return;
    var labels = headerLabels(table);
    var cols = ensureColgroup(table);
    table.style.tableLayout = "fixed";
    if (!table.style.minWidth) table.style.minWidth = "0";
    labels.forEach(function (name, idx) {
      if (widths[name] && cols[idx]) cols[idx].style.width = widths[name] + "px";
    });
  }

  function snapshotWidths(table) {
    // Перед первым ресайзом фиксируем текущие авто-ширины, чтобы переход
    // на table-layout:fixed не перекосил остальные колонки.
    var cols = ensureColgroup(table);
    var row = table.tHead.rows[0];
    for (var i = 0; i < row.cells.length; i++) {
      if (!cols[i].style.width) cols[i].style.width = row.cells[i].getBoundingClientRect().width + "px";
    }
    table.style.tableLayout = "fixed";
  }

  function attachResizers(table) {
    var row = table.tHead.rows[0];
    for (var i = 0; i < row.cells.length; i++) {
      (function (th, idx) {
        if (th.querySelector(".ft-resize-handle")) return;
        var handle = document.createElement("span");
        handle.className = "ft-resize-handle";
        th.style.position = "relative";
        th.appendChild(handle);

        handle.addEventListener("mousedown", function (e) {
          e.preventDefault();
          e.stopPropagation();
          snapshotWidths(table);
          var cols = ensureColgroup(table);
          var startX = e.clientX;
          var startW = parseFloat(cols[idx].style.width) || th.getBoundingClientRect().width;
          document.body.classList.add("ft-resizing");

          function onMove(ev) {
            var w = Math.max(40, startW + (ev.clientX - startX));
            cols[idx].style.width = w + "px";
          }
          function onUp() {
            document.removeEventListener("mousemove", onMove);
            document.removeEventListener("mouseup", onUp);
            document.body.classList.remove("ft-resizing");
            var labels = headerLabels(table);
            var widths = readPrefs(table).widths || {};
            widths[labels[idx]] = Math.round(parseFloat(cols[idx].style.width));
            writePrefs(table, { widths: widths });
          }
          document.addEventListener("mousemove", onMove);
          document.addEventListener("mouseup", onUp);
        });

        // Клик по ручке не должен сортировать колонку
        handle.addEventListener("click", function (e) { e.stopPropagation(); });
      })(row.cells[i], i);
    }
  }

  // ── сортировка (через TableSort) + запоминание ───────────────────

  function tbodyHasControls(table) {
    for (var b = 0; b < table.tBodies.length; b++) {
      if (table.tBodies[b].querySelector("input,select,textarea")) return true;
    }
    return false;
  }

  function isServerSorted(table) {
    var row = table.tHead.rows[0];
    for (var i = 0; i < row.cells.length; i++) {
      if (row.cells[i].hasAttribute("data-sort-field")) return true;
    }
    return false;
  }

  function attachSorting(table) {
    if (tbodyHasControls(table)) return;           // редакторы строк не сортируем
    if (window.TableSort) window.TableSort.init(table);

    if (isServerSorted(table)) return;             // серверная сортировка живёт в URL

    var row = table.tHead.rows[0];
    for (var i = 0; i < row.cells.length; i++) {
      (function (th, idx) {
        th.addEventListener("click", function (e) {
          if (e.target.classList && e.target.classList.contains("ft-resize-handle")) return;
          // TableSort уже отработал (его листенер навешан раньше) — читаем итог
          var dir = th.getAttribute("data-sort-dir");
          if (dir) writePrefs(table, { sort: { col: headerLabels(table)[idx], dir: dir } });
        });
      })(row.cells[i], i);
    }

    var saved = readPrefs(table).sort;
    if (saved && saved.col) {
      var labels = headerLabels(table);
      var idx = labels.indexOf(saved.col);
      var th = idx >= 0 ? row.cells[idx] : null;
      if (th && !th.classList.contains("no-sort")) {
        th.click();                                 // asc
        if (saved.dir === "desc") th.click();       // второй клик — desc
      }
    }
  }

  // ── меню колонок ─────────────────────────────────────────────────

  function displayLabel(table, idx) {
    var th = table.tHead.rows[0].cells[idx].cloneNode(true);
    var junk = th.querySelectorAll(".sort-indicator,.ft-resize-handle");
    for (var j = 0; j < junk.length; j++) junk[j].remove();
    return (th.textContent || "").trim() || ("Column " + (idx + 1));
  }

  function closeMenus() {
    document.querySelectorAll(".ft-menu.show").forEach(function (m) { m.classList.remove("show"); });
  }
  document.addEventListener("click", function (e) {
    if (!e.target.closest || !e.target.closest(".ft-menu, .ft-gear")) closeMenus();
  });

  function buildMenu(table, wrapper, toolbar) {
    var gear = document.createElement("button");
    gear.type = "button";
    gear.className = "ft-gear";
    gear.title = "Table columns";
    // Текстовый символ вместо иконочного шрифта — работает и там, где
    // bootstrap-icons не подключены (admin panel).
    gear.textContent = "⚙";

    var menu = document.createElement("div");
    menu.className = "ft-menu";

    gear.addEventListener("click", function (e) {
      e.stopPropagation();
      var wasOpen = menu.classList.contains("show");
      closeMenus();
      if (wasOpen) return;
      renderMenu();
      menu.classList.add("show");
    });

    function renderMenu() {
      menu.textContent = "";
      var title = document.createElement("div");
      title.className = "ft-menu__title";
      title.textContent = "Columns";
      menu.appendChild(title);

      var labels = headerLabels(table);
      var hidden = readPrefs(table).hidden || [];
      labels.forEach(function (name, idx) {
        var item = document.createElement("label");
        item.className = "ft-menu__item";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.className = "form-check-input";
        cb.checked = hidden.indexOf(name) < 0;
        cb.addEventListener("change", function () {
          var cur = readPrefs(table).hidden || [];
          if (cb.checked) cur = cur.filter(function (n) { return n !== name; });
          else if (cur.indexOf(name) < 0) cur.push(name);
          // Последнюю видимую колонку скрыть нельзя
          if (cur.length >= labels.length) { cb.checked = true; return; }
          writePrefs(table, { hidden: cur.length ? cur : null });
          applyHidden(table);
        });
        var span = document.createElement("span");
        span.textContent = displayLabel(table, idx);
        item.appendChild(cb);
        item.appendChild(span);
        menu.appendChild(item);
      });

      var reset = document.createElement("button");
      reset.type = "button";
      reset.className = "ft-menu__reset";
      reset.textContent = "Reset table";
      reset.addEventListener("click", function () {
        closeMenus();
        var done = setPrefs(tableKey(table), null, true);
        if (done && done.finally) done.finally(function () { window.location.reload(); });
        else window.location.reload();
      });
      menu.appendChild(reset);
    }

    toolbar.appendChild(gear);
    wrapper.appendChild(menu);
  }

  // ── инициализация одной таблицы ──────────────────────────────────

  function enhance(table) {
    if (table._ftInit) return;
    if (!table.tHead || !table.tHead.rows.length || !table.tHead.rows[0].cells.length) return;
    if (table.closest('[data-flex-table="off"]')) return;
    table._ftInit = true;

    // Оборачиваем .table-responsive (или саму таблицу, если его нет) в
    // ft-host — позиционный контейнер для шестерёнки и меню.
    var target = table.closest(".table-responsive") || table;
    var wrapper = (target.parentElement &&
                   target.parentElement.classList.contains("ft-host"))
      ? target.parentElement : null;
    if (!wrapper) {
      wrapper = document.createElement("div");
      wrapper.className = "ft-host";
      target.parentNode.insertBefore(wrapper, target);
      wrapper.appendChild(target);
    }

    // Шестерёнка живёт в тонком тулбаре НАД таблицей — поверх заголовков
    // ничего не висит (при горизонтальном скролле оверлей налезал бы).
    var toolbar = document.createElement("div");
    toolbar.className = "ft-toolbar";
    wrapper.insertBefore(toolbar, wrapper.firstChild);

    ensureStyleEl(table);
    buildMenu(table, wrapper, toolbar);
    attachResizers(table);
    applyWidths(table);
    applyHidden(table);
    attachSorting(table);
  }

  function enhanceAll(root) {
    var tables = (root || document).querySelectorAll("table");
    for (var i = 0; i < tables.length; i++) enhance(tables[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { enhanceAll(); });
  } else {
    enhanceAll();
  }

  window.addEventListener("roobico:content-replaced", function () { enhanceAll(); });

  if (typeof MutationObserver !== "undefined") {
    new MutationObserver(function (mutations) {
      for (var i = 0; i < mutations.length; i++) {
        var added = mutations[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          var node = added[j];
          if (node.nodeType !== 1) continue;
          if (node.tagName === "TABLE") enhance(node);
          else if (node.querySelectorAll) enhanceAll(node);
        }
      }
    }).observe(document.body, { childList: true, subtree: true });
  }

  window.FlexTables = { enhanceAll: enhanceAll };
})();
