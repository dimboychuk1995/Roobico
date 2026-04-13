(function () {
  "use strict";

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtMoney(v) {
    var n = parseFloat(v);
    return isNaN(n) ? "0.00" : n.toFixed(2);
  }

  function buildSummaryHtml(summary, tab) {
    var layouts = {
      "sales_summary": [
        ["revenue_total", "labor_total", "parts_total", "misc_charges_total", "sales_tax_total"],
        ["orders_count", "avg_ticket"]
      ],
      "payments_summary": [
        ["payments_total", "payments_count", "avg_payment"]
      ],
      "customer_balances": [
        ["billed_total", "paid_total", "outstanding_total"],
        ["customers_count"]
      ],
      "vendor_balances": [
        ["total_amount", "paid_amount", "remaining_balance"],
        ["vendors_count", "orders_count"]
      ],
      "parts_orders_summary": [
        ["parts_total", "cores_total", "non_inventory_total", "total_amount"],
        ["paid_amount", "remaining_balance"],
        ["vendors_count", "orders_count"]
      ],
      "general_revenue": [
        ["sales_revenue", "parts_cost", "parts_profit"],
        ["po_total_spent", "net_revenue"],
        ["wo_count", "po_count"]
      ]
    };

    var rowDefs = layouts[tab];

    function cardHtml(key, val) {
      var label = key.replace(/_/g, " ");
      var display = key.indexOf("count") !== -1 ? String(val) : "$" + fmtMoney(val);
      return '<div class="col"><div class="border rounded p-2 h-100">' +
        '<div class="small text-muted text-capitalize">' + escapeHtml(label) + '</div>' +
        '<div class="fw-semibold">' + escapeHtml(display) + '</div>' +
        '</div></div>';
    }

    if (!rowDefs) {
      // Fallback: all keys in one row
      var html = "";
      for (var key in summary) {
        if (summary.hasOwnProperty(key)) html += cardHtml(key, summary[key]);
      }
      return html ? '<div class="row g-2">' + html + '</div>' : "";
    }

    var usedKeys = {};
    var html = "";
    for (var r = 0; r < rowDefs.length; r++) {
      var rowHtml = "";
      for (var i = 0; i < rowDefs[r].length; i++) {
        var k = rowDefs[r][i];
        if (summary.hasOwnProperty(k)) {
          rowHtml += cardHtml(k, summary[k]);
          usedKeys[k] = true;
        }
      }
      if (rowHtml) html += '<div class="row g-2 mb-2">' + rowHtml + '</div>';
    }
    // Any remaining keys
    var extraHtml = "";
    for (var key in summary) {
      if (summary.hasOwnProperty(key) && !usedKeys[key]) extraHtml += cardHtml(key, summary[key]);
    }
    if (extraHtml) html += '<div class="row g-2">' + extraHtml + '</div>';
    return html;
  }

  function buildTheadHtml(tab) {
    if (tab === "sales_summary") {
      return '<tr class="text-muted"><th>Customer</th><th class="text-end">Orders</th><th class="text-end">Labor</th><th class="text-end">Parts</th><th class="text-end">Misc Charges</th><th class="text-end">Tax</th><th class="text-end">Revenue</th></tr>';
    }
    if (tab === "payments_summary") {
      return '<tr class="text-muted"><th>Customer</th><th class="text-end">Payments</th><th class="text-end">Amount</th></tr>';
    }
    if (tab === "customer_balances") {
      return '<tr class="text-muted"><th>Customer</th><th class="text-end">Orders</th><th class="text-end">Billed</th><th class="text-end">Paid</th><th class="text-end">Outstanding</th></tr>';
    }
    if (tab === "vendor_balances") {
      return '<tr class="text-muted"><th>Vendor</th><th class="text-end">Orders</th><th class="text-end">Total</th><th class="text-end">Paid</th><th class="text-end">Outstanding</th></tr>';
    }
    if (tab === "general_revenue") {
      return '<tr class="text-muted"><th>Category</th><th class="text-end">Amount</th></tr>';
    }
    return '<tr class="text-muted"><th>Vendor</th><th class="text-end">Orders</th><th class="text-end">Parts</th><th class="text-end">Cores</th><th class="text-end">Shop Supply</th><th class="text-end">Tools</th><th class="text-end">Utilities</th><th class="text-end">Pmt to Svc</th><th class="text-end">Non‑Inv Total</th><th class="text-end">Total</th><th class="text-end">Paid</th><th class="text-end">Balance</th></tr>';
  }

  function buildRowHtml(tab, row) {
    if (tab === "sales_summary") {
      return '<tr><td>' + escapeHtml(row.customer_label) + '</td>' +
        '<td class="text-end">' + (row.orders_count || 0) + '</td>' +
        '<td class="text-end">$' + fmtMoney(row.labor_total) + '</td>' +
        '<td class="text-end">$' + fmtMoney(row.parts_total) + '</td>' +
        '<td class="text-end">$' + fmtMoney(row.misc_charges_total) + '</td>' +
        '<td class="text-end">$' + fmtMoney(row.sales_tax_total) + '</td>' +
        '<td class="text-end fw-semibold">$' + fmtMoney(row.grand_total) + '</td></tr>';
    }
    if (tab === "payments_summary") {
      return '<tr><td>' + escapeHtml(row.customer_label) + '</td>' +
        '<td class="text-end">' + (row.payments_count || 0) + '</td>' +
        '<td class="text-end fw-semibold">$' + fmtMoney(row.amount_total) + '</td></tr>';
    }
    if (tab === "customer_balances") {
      return '<tr><td>' + escapeHtml(row.customer_label) + '</td>' +
        '<td class="text-end">' + (row.orders_count || 0) + '</td>' +
        '<td class="text-end">$' + fmtMoney(row.billed_total) + '</td>' +
        '<td class="text-end">$' + fmtMoney(row.paid_total) + '</td>' +
        '<td class="text-end fw-semibold">$' + fmtMoney(row.outstanding_total) + '</td></tr>';
    }
    if (tab === "vendor_balances") {
      return '<tr><td>' + escapeHtml(row.vendor_label) + '</td>' +
        '<td class="text-end">' + (row.orders_count || 0) + '</td>' +
        '<td class="text-end">$' + fmtMoney(row.total_amount) + '</td>' +
        '<td class="text-end">$' + fmtMoney(row.paid_amount) + '</td>' +
        '<td class="text-end fw-semibold">$' + fmtMoney(row.remaining_balance) + '</td></tr>';
    }
    if (tab === "general_revenue") {
      var cat = row.category || "";
      var isBold = cat.indexOf("Total") !== -1 || cat.indexOf("Profit") !== -1 || cat.indexOf("Net") !== -1;
      var cls = isBold ? ' class="fw-semibold"' : '';
      var isSep = cat.indexOf("Parts Orders") === 0 && cat.indexOf("Parts Orders \u2014 Parts") === 0;
      var sep = isSep ? '<tr><td colspan="2" class="border-0 py-1"></td></tr>' : '';
      return sep + '<tr><td' + cls + '>' + escapeHtml(cat) + '</td>' +
        '<td class="text-end' + (isBold ? ' fw-semibold' : '') + '">$' + fmtMoney(row.amount) + '</td></tr>';
    }
    return '<tr><td>' + escapeHtml(row.vendor_label) + '</td>' +
      '<td class="text-end">' + (row.orders_count || 0) + '</td>' +
      '<td class="text-end">$' + fmtMoney(row.parts_total) + '</td>' +
      '<td class="text-end">$' + fmtMoney(row.cores_total) + '</td>' +
      '<td class="text-end">$' + fmtMoney(row.shop_supply_total) + '</td>' +
      '<td class="text-end">$' + fmtMoney(row.tools_total) + '</td>' +
      '<td class="text-end">$' + fmtMoney(row.utilities_total) + '</td>' +
      '<td class="text-end">$' + fmtMoney(row.payment_to_another_service_total) + '</td>' +
      '<td class="text-end">$' + fmtMoney(row.non_inventory_total) + '</td>' +
      '<td class="text-end">$' + fmtMoney(row.total_amount) + '</td>' +
      '<td class="text-end">$' + fmtMoney(row.paid_amount) + '</td>' +
      '<td class="text-end fw-semibold">$' + fmtMoney(row.remaining_balance) + '</td></tr>';
  }

  function loadReportData() {
    var card = document.getElementById("reportCard");
    if (!card) return;
    var apiUrl = card.getAttribute("data-report-api");
    var tab = card.getAttribute("data-selected-tab") || "sales_summary";
    if (!apiUrl) return;

    // Only load if the page was submitted with query params (Generate was clicked)
    var qs = window.location.search;
    if (!qs || qs === "?") {
      // No filters applied yet — show prompt instead of loading
      var loading = document.getElementById("reportLoading");
      var emptyEl = document.getElementById("reportEmpty");
      if (loading) loading.classList.add("d-none");
      if (emptyEl) { emptyEl.textContent = "Select filters and click Generate to view the report."; emptyEl.classList.remove("d-none"); }
      return;
    }

    var loading = document.getElementById("reportLoading");
    var summaryEl = document.getElementById("reportSummary");
    var tableWrap = document.getElementById("reportTableWrap");
    var thead = document.getElementById("reportThead");
    var tbody = document.getElementById("reportTbody");
    var emptyEl = document.getElementById("reportEmpty");
    var titleEl = document.getElementById("reportTitle");
    var shopEl = document.getElementById("reportShopName");
    var footerEl = document.getElementById("reportFooter");

    // pass current query string to API
    var qs = window.location.search;
    var url = apiUrl + (qs || "");

    if (loading) loading.classList.remove("d-none");
    if (summaryEl) summaryEl.classList.add("d-none");
    if (tableWrap) tableWrap.classList.add("d-none");
    if (emptyEl) emptyEl.classList.add("d-none");

    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (loading) loading.classList.add("d-none");

        if (!data.ok) {
          if (emptyEl) { emptyEl.textContent = data.error || "Error loading report."; emptyEl.classList.remove("d-none"); }
          return;
        }

        var rd = data.report_data || {};
        if (titleEl) titleEl.textContent = rd.title || "Report";
        if (shopEl) shopEl.textContent = "Shop: " + (data.shop_name || "-");
        if (footerEl) footerEl.textContent = "Generated: " + new Date().toLocaleString();

        if (rd.summary && Object.keys(rd.summary).length) {
          summaryEl.innerHTML = buildSummaryHtml(rd.summary, tab);
          summaryEl.classList.remove("d-none");
        }

        var rows = rd.rows || [];
        if (rows.length) {
          thead.innerHTML = buildTheadHtml(tab);
          var rowsHtml = "";
          for (var i = 0; i < rows.length; i++) {
            rowsHtml += buildRowHtml(tab, rows[i]);
          }
          tbody.innerHTML = rowsHtml;
          tableWrap.classList.remove("d-none");
        } else {
          emptyEl.classList.remove("d-none");
        }
      })
      .catch(function () {
        if (loading) loading.classList.add("d-none");
        if (emptyEl) { emptyEl.textContent = "Failed to load report data."; emptyEl.classList.remove("d-none"); }
      });
  }

  function initReportsPage() {
    var form = document.getElementById("standardReportsFilterForm");
    if (!form) return;

    // Date preset live-updates the date inputs
    var presetSelect = form.querySelector('select[name="date_preset"]');
    if (presetSelect) {
      presetSelect.addEventListener("change", function () {
        if (typeof window.applyDatePresetToForm === "function") {
          window.applyDatePresetToForm(form, presetSelect.value);
        }
      });
    }

    var tabInput = form.querySelector('input[name="tab"]');

    if (tabInput) {
      document.querySelectorAll("[data-report-tab]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var tab = (btn.getAttribute("data-report-tab") || "").trim();
          if (!tab) return;
          tabInput.value = tab;
          form.submit();
        });
      });
    }

    // Before form submit, collect checked customer IDs into a single hidden input
    form.addEventListener("submit", function () {
      var hiddenInput = document.getElementById("customerIdsHidden");
      if (!hiddenInput) return;
      var checkboxList = document.getElementById("customerCheckboxList");
      if (!checkboxList) return;

      var boxes = checkboxList.querySelectorAll(".customer-checkbox");
      var total = boxes.length;
      var checkedIds = [];
      boxes.forEach(function (cb) { if (cb.checked) checkedIds.push(cb.value); });

      // If all selected send empty (= all on backend), otherwise comma-separated
      hiddenInput.value = (checkedIds.length === total) ? "" : checkedIds.join(",");
    });

    // Vendor IDs collection
    form.addEventListener("submit", function () {
      var vendorHidden = document.getElementById("vendorIdsHidden");
      if (!vendorHidden) return;
      var vendorList = document.getElementById("vendorCheckboxList");
      if (!vendorList) return;

      var vendorBoxes = vendorList.querySelectorAll(".vendor-checkbox");
      var vendorTotal = vendorBoxes.length;
      var vendorChecked = [];
      vendorBoxes.forEach(function (cb) { if (cb.checked) vendorChecked.push(cb.value); });
      vendorHidden.value = (vendorChecked.length === vendorTotal) ? "" : vendorChecked.join(",");
    });
  }

  function initCustomerMultiSelect() {
    var container = document.getElementById("customerMultiSelect");
    if (!container) return;

    var btn = document.getElementById("customerDropdownBtn");
    var searchInput = document.getElementById("customerSearchInput");
    var checkboxList = document.getElementById("customerCheckboxList");
    var selectAllBtn = document.getElementById("customerSelectAll");
    var deselectAllBtn = document.getElementById("customerDeselectAll");

    function getCheckboxes() {
      return checkboxList ? checkboxList.querySelectorAll(".customer-checkbox") : [];
    }

    function getVisibleItems() {
      return checkboxList ? checkboxList.querySelectorAll(".customer-check-item:not([style*='display: none'])") : [];
    }

    function updateLabel() {
      var boxes = getCheckboxes();
      var total = boxes.length;
      var checked = 0;
      boxes.forEach(function (cb) { if (cb.checked) checked++; });

      if (checked === 0) {
        btn.textContent = "No Customers";
      } else if (checked === total) {
        btn.textContent = "All Customers";
      } else {
        btn.textContent = checked + " of " + total + " selected";
      }
    }

    // Search filter
    if (searchInput) {
      searchInput.addEventListener("input", function () {
        var q = searchInput.value.toLowerCase().trim();
        var items = checkboxList.querySelectorAll(".customer-check-item");
        items.forEach(function (item) {
          var label = item.querySelector(".form-check-label");
          var text = label ? label.textContent.toLowerCase() : "";
          item.style.display = (!q || text.indexOf(q) !== -1) ? "" : "none";
        });
      });
    }

    // Select All (visible only)
    if (selectAllBtn) {
      selectAllBtn.addEventListener("click", function () {
        var visibleItems = getVisibleItems();
        visibleItems.forEach(function (item) {
          var cb = item.querySelector(".customer-checkbox");
          if (cb) cb.checked = true;
        });
        updateLabel();
      });
    }

    // Deselect All (visible only)
    if (deselectAllBtn) {
      deselectAllBtn.addEventListener("click", function () {
        var visibleItems = getVisibleItems();
        visibleItems.forEach(function (item) {
          var cb = item.querySelector(".customer-checkbox");
          if (cb) cb.checked = false;
        });
        updateLabel();
      });
    }

    // Update label on any checkbox change
    if (checkboxList) {
      checkboxList.addEventListener("change", function (e) {
        if (e.target.classList.contains("customer-checkbox")) {
          updateLabel();
        }
      });
    }

    // Prevent dropdown from closing when clicking inside
    var menu = document.getElementById("customerDropdownMenu");
    if (menu) {
      menu.addEventListener("click", function (e) {
        e.stopPropagation();
      });
    }

    updateLabel();
  }

  function initVendorMultiSelect() {
    var container = document.getElementById("vendorMultiSelect");
    if (!container) return;

    var btn = document.getElementById("vendorDropdownBtn");
    var searchInput = document.getElementById("vendorSearchInput");
    var checkboxList = document.getElementById("vendorCheckboxList");
    var selectAllBtn = document.getElementById("vendorSelectAll");
    var deselectAllBtn = document.getElementById("vendorDeselectAll");

    function getCheckboxes() {
      return checkboxList ? checkboxList.querySelectorAll(".vendor-checkbox") : [];
    }

    function getVisibleItems() {
      return checkboxList ? checkboxList.querySelectorAll(".vendor-check-item:not([style*='display: none'])") : [];
    }

    function updateLabel() {
      var boxes = getCheckboxes();
      var total = boxes.length;
      var checked = 0;
      boxes.forEach(function (cb) { if (cb.checked) checked++; });

      if (checked === 0) {
        btn.textContent = "No Vendors";
      } else if (checked === total) {
        btn.textContent = "All Vendors";
      } else {
        btn.textContent = checked + " of " + total + " selected";
      }
    }

    if (searchInput) {
      searchInput.addEventListener("input", function () {
        var q = searchInput.value.toLowerCase().trim();
        var items = checkboxList.querySelectorAll(".vendor-check-item");
        items.forEach(function (item) {
          var label = item.querySelector(".form-check-label");
          var text = label ? label.textContent.toLowerCase() : "";
          item.style.display = (!q || text.indexOf(q) !== -1) ? "" : "none";
        });
      });
    }

    if (selectAllBtn) {
      selectAllBtn.addEventListener("click", function () {
        var visibleItems = getVisibleItems();
        visibleItems.forEach(function (item) {
          var cb = item.querySelector(".vendor-checkbox");
          if (cb) cb.checked = true;
        });
        updateLabel();
      });
    }

    if (deselectAllBtn) {
      deselectAllBtn.addEventListener("click", function () {
        var visibleItems = getVisibleItems();
        visibleItems.forEach(function (item) {
          var cb = item.querySelector(".vendor-checkbox");
          if (cb) cb.checked = false;
        });
        updateLabel();
      });
    }

    if (checkboxList) {
      checkboxList.addEventListener("change", function (e) {
        if (e.target.classList.contains("vendor-checkbox")) {
          updateLabel();
        }
      });
    }

    var menu = document.getElementById("vendorDropdownMenu");
    if (menu) {
      menu.addEventListener("click", function (e) {
        e.stopPropagation();
      });
    }

    updateLabel();
  }

  function init() {
    initReportsPage();
    initCustomerMultiSelect();
    initVendorMultiSelect();
    loadReportData();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }

  window.addEventListener("smallshop:content-replaced", init);
})();
