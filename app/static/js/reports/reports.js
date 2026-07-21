(function () {
  "use strict";

  var _chartInstance = null;
  var _lastChartData = null;

  // Цвета осей/сетки/легенды Chart.js зависят от темы: дефолтные тёмно-серые
  // подписи нечитаемы на тёмном фоне. При переключении темы график
  // перерисовывается (MutationObserver в конце файла).
  function chartThemeColors() {
    var dark = document.documentElement.getAttribute("data-theme") === "dark";
    return {
      text: dark ? "#c9c9c9" : "#666",
      grid: dark ? "rgba(255,255,255,0.09)" : "rgba(0,0,0,0.1)"
    };
  }

  var CHART_COLORS = [
    "rgba(54,162,235,0.7)",
    "rgba(255,99,132,0.7)",
    "rgba(75,192,192,0.7)",
    "rgba(255,206,86,0.7)",
    "rgba(153,102,255,0.7)",
    "rgba(255,159,64,0.7)",
    "rgba(99,255,132,0.7)",
    "rgba(201,203,207,0.7)",
    "rgba(255,99,255,0.7)",
    "rgba(54,235,162,0.7)"
  ];

  var CHART_BORDERS = [
    "rgba(54,162,235,1)",
    "rgba(255,99,132,1)",
    "rgba(75,192,192,1)",
    "rgba(255,206,86,1)",
    "rgba(153,102,255,1)",
    "rgba(255,159,64,1)",
    "rgba(99,255,132,1)",
    "rgba(201,203,207,1)",
    "rgba(255,99,255,1)",
    "rgba(54,235,162,1)"
  ];

  function renderChart(chartData) {
    var wrap = document.getElementById("reportChartWrap");
    var canvas = document.getElementById("reportChart");
    if (!wrap || !canvas) return;
    _lastChartData = chartData;
    var themeColors = chartThemeColors();

    if (_chartInstance) {
      _chartInstance.destroy();
      _chartInstance = null;
    }

    if (!chartData || !chartData.labels || !chartData.labels.length) {
      wrap.classList.add("d-none");
      return;
    }

    var datasets = [];
    var hasSecondAxis = false;
    for (var i = 0; i < chartData.datasets.length; i++) {
      var ds = chartData.datasets[i];
      var ci = i % CHART_COLORS.length;
      var entry = {
        label: ds.label,
        data: ds.data,
        backgroundColor: CHART_COLORS[ci],
        borderColor: CHART_BORDERS[ci],
        borderWidth: 1
      };
      if (ds.yAxisID) {
        entry.yAxisID = ds.yAxisID;
        entry.type = "line";
        entry.fill = false;
        entry.borderWidth = 2;
        entry.pointRadius = 3;
        entry.backgroundColor = CHART_BORDERS[ci];
        hasSecondAxis = true;
      }
      datasets.push(entry);
    }

    var isHours = chartData.is_hours === true;

    var scales = {
      x: {
        ticks: { color: themeColors.text },
        grid: { color: themeColors.grid }
      },
      y: {
        beginAtZero: true,
        ticks: {
          color: themeColors.text,
          callback: function (v) {
            return isHours ? v + " hrs" : "$" + v.toLocaleString();
          }
        },
        grid: { color: themeColors.grid }
      }
    };
    if (hasSecondAxis) {
      scales.y1 = {
        beginAtZero: true,
        position: "right",
        grid: { drawOnChartArea: false },
        ticks: {
          color: themeColors.text,
          callback: function (v) { return v + " hrs"; }
        }
      };
    }

    _chartInstance = new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: { labels: chartData.labels, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: scales,
        plugins: {
          legend: {
            labels: { color: themeColors.text }
          },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                var val = ctx.parsed.y;
                var axisId = ctx.dataset.yAxisID;
                var fmt = (isHours || axisId === "y1") ? val.toFixed(2) + " hrs" : "$" + val.toFixed(2);
                return ctx.dataset.label + ": " + fmt;
              }
            }
          }
        }
      }
    });

    wrap.classList.remove("d-none");
  }

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

  function fmtHours(v) {
    var n = parseFloat(v);
    return isNaN(n) ? "0.00" : n.toFixed(2);
  }

  function buildSummaryHtml(summary, tab) {
    var layouts = {
      "sales_summary": [
        ["revenue_total", "labor_total", "parts_total", "parts_cost_total", "sales_tax_total"],
        ["orders_count", "avg_ticket", "invoiced_hours"]
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
        ["sales_revenue", "sales_labor", "parts_sale", "parts_cost", "parts_profit", "core_charges"],
        ["po_total_spent", "net_revenue"],
        ["wo_count", "po_count", "invoiced_hours", "total_mech_hours"]
      ],
      "mechanic_hours": [
        ["total_hours", "total_tracked_hours", "mechanics_count"],
        ["total_wo", "total_entries"]
      ]
    };

    var rowDefs = layouts[tab];

    function cardHtml(key, val, opts) {
      opts = opts || {};
      var label = opts.label || key.replace(/_/g, " ");
      var isCount = opts.isCount || key.indexOf("count") !== -1;
      var isHours = opts.isHours || key.indexOf("hours") !== -1 || key.indexOf("entries") !== -1;
      var isWeeks = opts.isWeeks || key.indexOf("weeks") !== -1;
      var display;
      if (isCount) {
        display = String(val);
      } else if (isWeeks) {
        var n = Number(val) || 0;
        display = (Math.round(n * 100) / 100) + " weeks";
      } else if (isHours) {
        display = fmtHours(val) + " hrs";
      } else {
        display = "$" + fmtMoney(val);
      }
      var accent = opts.accent ? ' style="border-left:3px solid ' + opts.accent + '"' : '';
      return '<div class="col"><div class="border rounded p-2 h-100"' + accent + '>' +
        '<div class="small text-muted text-capitalize">' + escapeHtml(label) + '</div>' +
        '<div class="fw-semibold">' + escapeHtml(display) + '</div>' +
        '</div></div>';
    }

    function sectionHtml(title, subtitle, cardsHtml) {
      var sub = subtitle ? '<div class="small text-muted">' + escapeHtml(subtitle) + '</div>' : '';
      return '<div class="mb-3">' +
        '<div class="d-flex align-items-baseline justify-content-between mb-1">' +
          '<div class="fw-semibold text-uppercase small text-secondary" style="letter-spacing:.04em">' + escapeHtml(title) + '</div>' +
          sub +
        '</div>' +
        '<div class="row g-2">' + cardsHtml + '</div>' +
      '</div>';
    }

    // ── Custom layout for General Revenue: два хиро-блока → формулы →
    //    вся детализация в таблице ниже ─────────────────────────────────
    if (tab === "general_revenue") {
      var s = summary;
      var html = "";

      var hasPayroll = s.hasOwnProperty("labor_cost") && (s.labor_cost || 0) > 0;
      var revenue = Number(s.sales_revenue || 0);
      var partsOrders = Number(s.po_total_spent || 0);
      var partsCost = Number(s.parts_cost || 0);
      var salaries = Number(s.labor_cost || 0);
      var netPO = Number(s.net_revenue_parts_orders || 0);
      var grossCost = s.hasOwnProperty("revenue_minus_parts_cost")
        ? Number(s.revenue_minus_parts_cost || 0)
        : Math.round((revenue - partsCost) * 100) / 100;
      var weeks = s.payroll_weeks ? (Math.round(Number(s.payroll_weeks) * 100) / 100) : null;

      function fmt(v) { return "$" + fmtMoney(v); }
      function signColor(v) { return v >= 0 ? "#198754" : "#dc3545"; }
      function rgba(v, a) { return "rgba(" + (v >= 0 ? "25,135,84" : "220,53,69") + "," + a + ")"; }

      // ── Two headline blocks ───────────────────────────────────────────
      function heroBlock(title, sub, value, accent) {
        return '<div class="col-md-6">' +
          '<div class="rounded p-3 h-100 text-center" style="background:' + rgba(value, 0.08) + '; border:1px solid ' + rgba(value, 0.35) + '; border-top:4px solid ' + accent + '">' +
            '<div class="fw-semibold text-uppercase" style="letter-spacing:.05em">' + escapeHtml(title) + '</div>' +
            '<div class="small text-muted">' + escapeHtml(sub) + '</div>' +
            '<div class="fw-bold" style="font-size:2rem; color:' + signColor(value) + '">' + fmt(value) + '</div>' +
          '</div>' +
        '</div>';
      }

      html += '<div class="row g-2 mb-3">' +
        heroBlock(
          "Revenue − Parts Cost",
          "All income minus the shop's cost of parts used on Work Orders",
          grossCost, "#0d6efd"
        ) +
        heroBlock(
          "Revenue − Parts Orders − Salaries",
          "All income minus vendor parts orders minus payroll",
          netPO, "#6f42c1"
        ) +
      '</div>';

      // ── Formulas with real numbers ────────────────────────────────────
      function term(v, label, color) {
        return '<span style="color:' + color + '">' + fmt(v) + '</span>' +
          ' <span class="text-muted small">' + escapeHtml(label) + '</span>';
      }

      function formulaRow(title, termsHtml, result) {
        return '<div class="d-flex flex-wrap justify-content-between align-items-baseline gap-2 py-1">' +
          '<span class="small fw-semibold text-uppercase text-secondary" style="letter-spacing:.04em">' + escapeHtml(title) + '</span>' +
          '<span>' + termsHtml + ' = <strong style="color:' + signColor(result) + '">' + fmt(result) + '</strong></span>' +
        '</div>';
      }

      var salaryLabel = "salaries" + (hasPayroll && weeks ? " (" + weeks + " wks)" : "");
      html += '<div class="border rounded p-3 mb-3">' +
        formulaRow(
          "Revenue − Parts Cost",
          term(revenue, "revenue", "#198754") + ' − ' + term(partsCost, "parts cost", "#dc3545"),
          grossCost
        ) +
        formulaRow(
          "Revenue − Parts Orders − Salaries",
          term(revenue, "revenue", "#198754") + ' − ' + term(partsOrders, "parts orders", "#dc3545") +
            ' − ' + term(salaries, salaryLabel, "#dc3545"),
          netPO
        ) +
      '</div>';

      // ── Activity strip (counts for context) ───────────────────────────
      var actParts = [];
      if (s.hasOwnProperty("wo_count")) actParts.push('<span><strong>' + (s.wo_count || 0) + '</strong> Work Orders</span>');
      if (s.hasOwnProperty("po_count")) actParts.push('<span><strong>' + (s.po_count || 0) + '</strong> Parts Orders</span>');
      if (s.hasOwnProperty("invoiced_hours")) actParts.push('<span><strong>' + fmtHours(s.invoiced_hours) + '</strong> hrs billed to customers</span>');
      if (s.hasOwnProperty("total_mech_hours")) actParts.push('<span><strong>' + fmtHours(s.total_mech_hours) + '</strong> mechanic hrs</span>');
      if (weeks) actParts.push('<span><strong>' + weeks + '</strong> weeks in period</span>');
      if (actParts.length) {
        html += '<div class="border rounded p-2 mb-3 d-flex flex-wrap gap-3 small text-muted align-items-center">' +
          '<span class="text-uppercase fw-semibold text-secondary" style="letter-spacing:.04em">Activity</span>' +
          actParts.join('<span class="text-muted">·</span>') +
        '</div>';
      }

      // ── Warnings only when a number is silently $0 ────────────────────
      if (s.customer_filter_active) {
        html += '<div class="alert alert-secondary py-2 small mb-3" style="border:1px solid var(--bs-border-color)">' +
          '<strong>Customer filter is active</strong> — payroll and parts orders cannot be tied to specific customers and are counted as $0 here.' +
        '</div>';
      } else if (!hasPayroll) {
        html += '<div class="alert alert-secondary py-2 small mb-3" style="border:1px solid var(--bs-border-color)">' +
          'Payroll unavailable — configure the uAttend integration and per-user pay rates; salaries are counted as $0 here.' +
        '</div>';
      }

      return html;
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
      return '<tr class="text-muted"><th>Customer</th><th class="text-end">Orders</th><th class="text-end">Labor</th><th class="text-end">Parts</th><th class="text-end">Parts Cost</th><th class="text-end">Tax</th><th class="text-end">Hours</th><th class="text-end">Revenue</th></tr>';
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
      return '<tr class="text-muted"><th style="width:30%">Category</th><th>What is this</th><th class="text-end" style="width:16%">Amount</th></tr>';
    }
    if (tab === "mechanic_hours") {
      return '<tr class="text-muted"><th>Mechanic</th><th class="text-end">Billed Hours</th><th class="text-end">Tracked Hours</th><th class="text-end">Work Orders</th><th class="text-end">Labor Entries</th></tr>';
    }
    return '<tr class="text-muted"><th>Vendor</th><th class="text-end">Orders</th><th class="text-end">Parts</th><th class="text-end">Cores</th><th class="text-end">Shop Supply</th><th class="text-end">Tools</th><th class="text-end">Utilities</th><th class="text-end">Pmt to Svc</th><th class="text-end">Non‑Inv Total</th><th class="text-end">Total</th><th class="text-end">Paid</th><th class="text-end">Balance</th></tr>';
  }

  function buildRowHtml(tab, row) {
    if (tab === "sales_summary") {
      return '<tr><td>' + escapeHtml(row.customer_label) + '</td>' +
        '<td class="text-end">' + (row.orders_count || 0) + '</td>' +
        '<td class="text-end">$' + fmtMoney(row.labor_total) + '</td>' +
        '<td class="text-end">$' + fmtMoney(row.parts_total) + '</td>' +
        '<td class="text-end">$' + fmtMoney(row.parts_cost_total) + '</td>' +
        '<td class="text-end">$' + fmtMoney(row.sales_tax_total) + '</td>' +
        '<td class="text-end">' + fmtHours(row.invoiced_hours) + ' hrs</td>' +
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
      // Section header row: group title + plain-language explanation.
      if (row.row_type === "section") {
        return '<tr class="table-section-row"><td colspan="3" class="py-2">' +
          '<div class="fw-semibold small text-uppercase" style="letter-spacing:.04em">' + escapeHtml(cat) + '</div>' +
          (row.desc ? '<div class="small" style="opacity:.75">' + escapeHtml(row.desc) + '</div>' : '') +
          '</td></tr>';
      }
      // Empty separator row (legacy safety)
      if (!cat && row.amount == null) {
        return '<tr><td colspan="3" class="border-0 py-2"></td></tr>';
      }
      var kind = row.kind || "";
      var isResult = kind === "result";
      var isEmph = row.emphasis === true;
      var catClasses = [];
      if (isEmph) catClasses.push("fw-bold");
      else if (isResult) catClasses.push("fw-semibold");
      else if (kind === "info") catClasses.push("text-muted");
      var catCls = catClasses.length ? ' class="' + catClasses.join(" ") + '"' : '';
      var indentStyle = row.indent ? ' style="padding-left:1.75rem"' : '';

      var amountHtml;
      if (row.is_hours) {
        amountHtml = fmtHours(row.amount) + " hrs";
      } else {
        var v = Number(row.amount) || 0;
        if (kind === "in") {
          amountHtml = '<span style="color:#198754">+$' + fmtMoney(v) + '</span>';
        } else if (kind === "out") {
          amountHtml = '<span style="color:#dc3545">−$' + fmtMoney(v) + '</span>';
        } else if (isEmph) {
          amountHtml = '<span style="color:' + (v >= 0 ? "#198754" : "#dc3545") + '">$' + fmtMoney(v) + '</span>';
        } else {
          amountHtml = '$' + fmtMoney(v);
        }
      }
      var amtCls = 'text-end' + (isEmph ? ' fw-bold' : (isResult ? ' fw-semibold' : ''));
      var rowStyle = isEmph ? ' style="border-top:2px solid var(--border, #dee2e6)"' : '';
      return '<tr' + rowStyle + '>' +
        '<td' + catCls + indentStyle + '>' + escapeHtml(cat) + '</td>' +
        '<td class="small text-muted">' + escapeHtml(row.desc || "") + '</td>' +
        '<td class="' + amtCls + '">' + amountHtml + '</td></tr>';
    }
    if (tab === "mechanic_hours") {
      return '<tr><td>' + escapeHtml(row.mechanic_name) + '</td>' +
        '<td class="text-end fw-semibold">' + fmtHours(row.total_hours) + '</td>' +
        '<td class="text-end">' + fmtHours(row.tracked_hours) + '</td>' +
        '<td class="text-end">' + (row.wo_count || 0) + '</td>' +
        '<td class="text-end">' + (row.labor_entries || 0) + '</td></tr>';
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
    var chartWrap = document.getElementById("reportChartWrap");
    if (chartWrap) chartWrap.classList.add("d-none");

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

        // render chart if available
        renderChart(rd.chart_data || null);

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

  window.addEventListener("roobico:content-replaced", init);

  // Перерисовка графика при переключении темы: цвета осей/сетки/легенды
  // задаются при создании Chart и сами не обновятся.
  new MutationObserver(function () {
    if (_chartInstance && _lastChartData) {
      renderChart(_lastChartData);
    }
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
})();
