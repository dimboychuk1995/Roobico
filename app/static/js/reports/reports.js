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

  function buildSummaryHtml(summary) {
    var html = "";
    for (var key in summary) {
      if (!summary.hasOwnProperty(key)) continue;
      var val = summary[key];
      var label = key.replace(/_/g, " ");
      var display = key.indexOf("count") !== -1 ? String(val) : "$" + fmtMoney(val);
      html += '<div class="col-6 col-md-3"><div class="border rounded p-2 h-100">' +
        '<div class="small text-muted text-capitalize">' + escapeHtml(label) + '</div>' +
        '<div class="fw-semibold">' + escapeHtml(display) + '</div>' +
        '</div></div>';
    }
    return html;
  }

  function buildTheadHtml(tab) {
    if (tab === "sales_summary") {
      return '<tr class="text-muted"><th>Customer</th><th class="text-end">Orders</th><th class="text-end">Labor</th><th class="text-end">Parts</th><th class="text-end">Tax</th><th class="text-end">Revenue</th></tr>';
    }
    if (tab === "payments_summary") {
      return '<tr class="text-muted"><th>Customer</th><th class="text-end">Payments</th><th class="text-end">Amount</th></tr>';
    }
    if (tab === "customer_balances") {
      return '<tr class="text-muted"><th>Customer</th><th class="text-end">Orders</th><th class="text-end">Billed</th><th class="text-end">Paid</th><th class="text-end">Outstanding</th></tr>';
    }
    return '<tr class="text-muted"><th>Vendor</th><th class="text-end">Orders</th><th class="text-end">Total</th><th class="text-end">Paid</th><th class="text-end">Outstanding</th></tr>';
  }

  function buildRowHtml(tab, row) {
    if (tab === "sales_summary") {
      return '<tr><td>' + escapeHtml(row.customer_label) + '</td>' +
        '<td class="text-end">' + (row.orders_count || 0) + '</td>' +
        '<td class="text-end">$' + fmtMoney(row.labor_total) + '</td>' +
        '<td class="text-end">$' + fmtMoney(row.parts_total) + '</td>' +
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
    return '<tr><td>' + escapeHtml(row.vendor_label) + '</td>' +
      '<td class="text-end">' + (row.orders_count || 0) + '</td>' +
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
          summaryEl.innerHTML = buildSummaryHtml(rd.summary);
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

    var tabInput = form.querySelector('input[name="tab"]');
    if (!tabInput) return;

    document.querySelectorAll("[data-report-tab]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var tab = (btn.getAttribute("data-report-tab") || "").trim();
        if (!tab) return;
        tabInput.value = tab;
        form.submit();
      });
    });
  }

  function init() {
    initReportsPage();
    loadReportData();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }

  window.addEventListener("smallshop:content-replaced", init);
})();
