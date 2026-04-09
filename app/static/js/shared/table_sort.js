/**
 * Universal table sorting — server-side + client-side fallback.
 *
 * Server-side mode (paginated tables):
 *   Add class "sortable" to the <table>.
 *   Add data-sort-field="mongo_field" to each sortable <th>.
 *   Clicking a <th> navigates to the current URL with sort_by & sort_dir params.
 *   The active sort column is highlighted from URL params on page load.
 *
 * Client-side mode (non-paginated / JS-rendered tables):
 *   Add class "sortable" to the <table>.
 *   Do NOT add data-sort-field to <th> elements.
 *   Works the same as before — sorts DOM rows in place.
 *
 * Columns with class "no-sort" are excluded in both modes.
 * Override cell sort key with data-sort-value="..." on <td>.
 */
(function () {
  "use strict";

  /* ── helpers ── */

  var CURRENCY_RE = /^\s*\$?\s*-?[\d,]+\.?\d*\s*$/;
  var NUMBER_RE   = /^\s*-?[\d,]+\.?\d*\s*$/;
  var DATE_RE     = /^\s*(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})\s*$/;
  var ISO_DATE_RE = /^\s*(\d{4})[\/\-](\d{1,2})[\/\-](\d{1,2})/;

  function stripCurrency(s) { return s.replace(/[$,\s]/g, ""); }

  function parseNum(s) {
    var v = parseFloat(stripCurrency(s));
    return isNaN(v) ? null : v;
  }

  function parseDate(s) {
    if (!s || !s.trim()) return null;
    var m;
    m = ISO_DATE_RE.exec(s);
    if (m) return new Date(+m[1], +m[2] - 1, +m[3]).getTime();
    m = DATE_RE.exec(s);
    if (m) {
      var yr = +m[3];
      if (yr < 100) yr += 2000;
      return new Date(yr, +m[1] - 1, +m[2]).getTime();
    }
    return null;
  }

  function cellValue(td) {
    if (td.hasAttribute("data-sort-value")) return td.getAttribute("data-sort-value");
    return (td.textContent || "").trim();
  }

  function getUrlParam(name) {
    var params = new URLSearchParams(window.location.search);
    return params.get(name) || "";
  }

  /* ── detect if table uses server-side sorting ── */

  function hasServerSort(headerRow) {
    var ths = headerRow.cells;
    for (var i = 0; i < ths.length; i++) {
      if (ths[i].hasAttribute("data-sort-field") && !ths[i].classList.contains("no-sort")) {
        return true;
      }
    }
    return false;
  }

  /* ── server-side: navigate to URL with sort params ── */

  function navigateWithSort(field, dir) {
    var params = new URLSearchParams(window.location.search);
    params.set("sort_by", field);
    params.set("sort_dir", dir);
    // Reset to page 1 when sorting changes
    params.delete("page");
    // Also reset tab-prefixed pages
    var keysToDelete = [];
    params.forEach(function (_, k) {
      if (/_page$/.test(k)) keysToDelete.push(k);
    });
    for (var i = 0; i < keysToDelete.length; i++) {
      params.delete(keysToDelete[i]);
    }
    window.location.search = params.toString();
  }

  /* ── client-side: sort DOM rows ── */

  function sortTable(table, colIdx, asc) {
    var tbody = table.tBodies[0];
    if (!tbody) return;

    var rows = Array.prototype.slice.call(tbody.rows);
    if (rows.length === 0) return;

    var type = "text";
    for (var i = 0; i < rows.length && i < 20; i++) {
      var cell = rows[i].cells[colIdx];
      if (!cell) continue;
      var val = cellValue(cell);
      if (!val || val === "-" || val === "—" || val === "N/A") continue;
      if (CURRENCY_RE.test(val) || NUMBER_RE.test(val)) { type = "number"; break; }
      if (parseDate(val) !== null) { type = "date"; break; }
      break;
    }

    rows.sort(function (a, b) {
      var va = cellValue(a.cells[colIdx] || { textContent: "" });
      var vb = cellValue(b.cells[colIdx] || { textContent: "" });
      var cmp = 0;
      if (type === "number") {
        var na = parseNum(va), nb = parseNum(vb);
        if (na === null && nb === null) cmp = 0;
        else if (na === null) cmp = 1;
        else if (nb === null) cmp = -1;
        else cmp = na - nb;
      } else if (type === "date") {
        var da = parseDate(va), db = parseDate(vb);
        if (da === null && db === null) cmp = 0;
        else if (da === null) cmp = 1;
        else if (db === null) cmp = -1;
        else cmp = da - db;
      } else {
        va = va.toLowerCase();
        vb = vb.toLowerCase();
        cmp = va < vb ? -1 : va > vb ? 1 : 0;
      }
      return asc ? cmp : -cmp;
    });

    for (var j = 0; j < rows.length; j++) {
      tbody.appendChild(rows[j]);
    }
  }

  /* ── init a single table ── */

  function initTable(table) {
    if (table._tableSortInit) return;
    table._tableSortInit = true;

    var thead = table.tHead;
    if (!thead) return;
    var headerRow = thead.rows[0];
    if (!headerRow) return;

    var ths = headerRow.cells;
    var serverMode = hasServerSort(headerRow);

    // Read current sort from URL for server-side tables
    var activeSortBy = serverMode ? getUrlParam("sort_by") : "";
    var activeSortDir = serverMode ? getUrlParam("sort_dir") : "";

    for (var i = 0; i < ths.length; i++) {
      (function (th, idx) {
        if (th.classList.contains("no-sort")) return;

        var field = th.getAttribute("data-sort-field") || "";

        // In server mode, skip columns without data-sort-field
        if (serverMode && !field) return;

        th.classList.add("sortable-th");
        th.style.cursor = "pointer";
        th.style.userSelect = "none";
        th.setAttribute("title", "Click to sort");

        var indicator = document.createElement("span");
        indicator.className = "sort-indicator";
        indicator.textContent = "";
        th.appendChild(indicator);

        // Highlight active sort on page load (server-side)
        if (serverMode && field === activeSortBy && activeSortDir) {
          var isAsc = activeSortDir === "asc";
          th.setAttribute("data-sort-dir", activeSortDir);
          th.classList.add(isAsc ? "sort-asc" : "sort-desc");
          indicator.textContent = isAsc ? " ▲" : " ▼";
        }

        th.addEventListener("click", function () {
          var curDir = th.getAttribute("data-sort-dir");
          var asc = curDir !== "asc";

          if (serverMode) {
            navigateWithSort(field, asc ? "asc" : "desc");
            return;
          }

          // Client-side mode
          for (var k = 0; k < ths.length; k++) {
            ths[k].removeAttribute("data-sort-dir");
            ths[k].classList.remove("sort-asc", "sort-desc");
            var ind = ths[k].querySelector(".sort-indicator");
            if (ind) ind.textContent = "";
          }
          th.setAttribute("data-sort-dir", asc ? "asc" : "desc");
          th.classList.add(asc ? "sort-asc" : "sort-desc");
          indicator.textContent = asc ? " ▲" : " ▼";
          sortTable(table, idx, asc);
        });
      })(ths[i], i);
    }
  }

  /* ── public API ── */

  window.TableSort = {
    init: function (tableOrSelector) {
      if (typeof tableOrSelector === "string") {
        var tables = document.querySelectorAll(tableOrSelector);
        for (var i = 0; i < tables.length; i++) initTable(tables[i]);
      } else if (tableOrSelector && tableOrSelector.tagName === "TABLE") {
        initTable(tableOrSelector);
      }
    },
    refresh: function (table) {
      if (table) {
        table._tableSortInit = false;
        initTable(table);
      }
    }
  };

  /* ── auto-init ── */

  function autoInit() {
    var tables = document.querySelectorAll("table.sortable");
    for (var i = 0; i < tables.length; i++) initTable(tables[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoInit);
  } else {
    autoInit();
  }

  if (typeof MutationObserver !== "undefined") {
    new MutationObserver(function (mutations) {
      for (var i = 0; i < mutations.length; i++) {
        var added = mutations[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          var node = added[j];
          if (node.nodeType !== 1) continue;
          if (node.tagName === "TABLE" && node.classList.contains("sortable")) {
            initTable(node);
          }
          var nested = node.querySelectorAll ? node.querySelectorAll("table.sortable") : [];
          for (var k = 0; k < nested.length; k++) initTable(nested[k]);
        }
      }
    }).observe(document.body, { childList: true, subtree: true });
  }
})();
