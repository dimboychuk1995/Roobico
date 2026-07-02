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
        ["total_hours", "mechanics_count"],
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

    // ── Custom layout for General Revenue: hero + money-in/out + activity ─
    if (tab === "general_revenue") {
      var s = summary;
      var html = "";

      var hasPayroll = s.hasOwnProperty("labor_cost") && (s.labor_cost || 0) > 0;
      var revenue = Number(s.sales_revenue || 0);
      var partsOrders = Number(s.po_total_spent || 0);
      var partsCost = Number(s.parts_cost || 0);
      var partsSale = Number(s.parts_sale || 0);
      var partsProfit = Number(s.parts_profit || 0);
      var cores = Number(s.core_charges || 0);
      var laborSale = Number(s.sales_labor || 0);
      var salaries = Number(s.labor_cost || 0);
      var netPO = Number(s.net_revenue_parts_orders || 0);
      var netCost = Number(s.net_revenue_parts_cost || 0);
      var weeks = s.payroll_weeks ? (Math.round(Number(s.payroll_weeks) * 100) / 100) : null;

      function fmt(v) { return "$" + fmtMoney(v); }
      function signColor(v) { return v >= 0 ? "#198754" : "#dc3545"; }
      function rgba(v, a) { return "rgba(" + (v >= 0 ? "25,135,84" : "220,53,69") + "," + a + ")"; }

      // ── HERO: Net Revenue, two views side-by-side ─────────────────────
      function netBlock(label, value, formula, accent) {
        return '<div class="col-md-6">' +
          '<div class="rounded p-3 h-100" style="background:' + rgba(value, 0.08) + '; border:1px solid ' + rgba(value, 0.35) + '; border-left:5px solid ' + accent + '">' +
            '<div class="small text-muted text-uppercase" style="letter-spacing:.05em">' + escapeHtml(label) + '</div>' +
            '<div class="fw-bold" style="font-size:1.75rem; color:' + signColor(value) + '">' + fmt(value) + '</div>' +
            '<div class="small text-muted">' + escapeHtml(formula) + '</div>' +
          '</div>' +
        '</div>';
      }
      html += '<div class="mb-3"><div class="small fw-semibold text-uppercase text-secondary mb-2" style="letter-spacing:.05em">Net Revenue · two views</div>' +
        '<div class="row g-2">' +
          netBlock("Net Revenue — Parts Orders basis", netPO, "Revenue − Parts Orders (vendor spend) − Salaries", "#6f42c1") +
          netBlock("Net Revenue — Parts (Cost) basis", netCost, "Revenue − Parts (Cost in WO) − Salaries", "#0d6efd") +
        '</div>' +
      '</div>';

      // ── Money In vs Money Out ─────────────────────────────────────────
      function lineRow(label, value, opts) {
        opts = opts || {};
        var cls = opts.bold ? ' fw-semibold' : '';
        var color = opts.color ? ' style="color:' + opts.color + '"' : '';
        var note = opts.note ? ' <span class="text-muted small">' + escapeHtml(opts.note) + '</span>' : '';
        return '<div class="d-flex justify-content-between py-1' + (opts.border ? ' border-top' : '') + (opts.muted ? ' text-muted' : '') + '">' +
          '<span class="small">' + escapeHtml(label) + note + '</span>' +
          '<span class="' + cls + '"' + color + '>' + escapeHtml(value) + '</span>' +
        '</div>';
      }

      // Money In — revenue breakdown
      var inHtml = "";
      inHtml += lineRow("Labor", fmt(laborSale));
      inHtml += lineRow("Parts (sale price)", fmt(partsSale));
      inHtml += lineRow("Core charges", fmt(cores));
      inHtml += lineRow("Total Revenue", fmt(revenue), { bold: true, border: true, color: "#198754" });
      // Sub-info: parts profit only (cost is intentionally omitted here — shown in Money Out)
      inHtml += '<div class="mt-2 pt-2 border-top">' +
        '<div class="small text-muted mb-1">Parts margin (informational)</div>' +
        '<div class="d-flex justify-content-between py-1 align-items-baseline">' +
          '<span class="small">Parts profit (Sale − Cost)</span>' +
          '<span class="fw-semibold">' +
            fmt(partsSale) +
            ' − <span style="color:#dc3545">' + fmt(partsCost) + '</span>' +
            ' = <span style="color:' + signColor(partsProfit) + '">' + fmt(partsProfit) + '</span>' +
          '</span>' +
        '</div>' +
      '</div>';

      // Money Out — split into two cards (PO basis + Cost basis), stacked on the right
      var totalOutPO = partsOrders + salaries;
      var totalOutCost = partsCost + salaries;

      var outPoHtml = "";
      outPoHtml += lineRow("Parts Orders (vendor spend)", fmt(partsOrders));
      if (hasPayroll) {
        outPoHtml += lineRow(
          "Salaries (period)",
          fmt(salaries),
          { note: weeks ? "(" + weeks + " wks)" : "" }
        );
      } else if (s.customer_filter_active) {
        outPoHtml += lineRow("Salaries", "hidden — customer filter active");
      } else {
        outPoHtml += lineRow("Salaries", "—");
      }
      outPoHtml += lineRow("Total Costs (PO basis)", fmt(totalOutPO), { bold: true, border: true, color: "#dc3545" });

      var outCostHtml = "";
      outCostHtml += lineRow("Parts (cost in WO)", fmt(partsCost));
      if (hasPayroll) {
        outCostHtml += lineRow(
          "Salaries (period)",
          fmt(salaries),
          { note: weeks ? "(" + weeks + " wks)" : "" }
        );
      } else if (s.customer_filter_active) {
        outCostHtml += lineRow("Salaries", "hidden — customer filter active");
      } else {
        outCostHtml += lineRow("Salaries", "—");
      }
      outCostHtml += lineRow("Total Costs (Cost basis)", fmt(totalOutCost), { bold: true, border: true, color: "#dc3545" });

      html += '<div class="row g-2 mb-3">' +
        // Left: Money In (full height)
        '<div class="col-md-6">' +
          '<div class="border rounded p-3 h-100" style="border-left:4px solid #198754 !important">' +
            '<div class="d-flex justify-content-between align-items-baseline mb-2">' +
              '<div class="fw-semibold text-uppercase small text-secondary" style="letter-spacing:.04em">Money In · Revenue</div>' +
              '<div class="fw-bold" style="color:#198754">' + fmt(revenue) + '</div>' +
            '</div>' +
            inHtml +
          '</div>' +
        '</div>' +
        // Right: two stacked Money Out cards
        '<div class="col-md-6 d-flex flex-column gap-2">' +
          '<div class="border rounded p-3" style="border-left:4px solid #dc3545 !important">' +
            '<div class="d-flex justify-content-between align-items-baseline mb-2">' +
              '<div class="fw-semibold text-uppercase small text-secondary" style="letter-spacing:.04em">Money Out · Costs <span class="text-muted">(PO basis)</span></div>' +
              '<div class="fw-bold" style="color:#dc3545">' + fmt(totalOutPO) + '</div>' +
            '</div>' +
            outPoHtml +
          '</div>' +
          '<div class="border rounded p-3" style="border-left:4px solid #fd7e14 !important">' +
            '<div class="d-flex justify-content-between align-items-baseline mb-2">' +
              '<div class="fw-semibold text-uppercase small text-secondary" style="letter-spacing:.04em">Money Out · Costs <span class="text-muted">(Cost basis)</span></div>' +
              '<div class="fw-bold" style="color:#dc3545">' + fmt(totalOutCost) + '</div>' +
            '</div>' +
            outCostHtml +
          '</div>' +
        '</div>' +
      '</div>';

      // ── Activity strip (compact, single line) ─────────────────────────
      var actParts = [];
      if (s.hasOwnProperty("wo_count")) actParts.push('<span><strong>' + (s.wo_count || 0) + '</strong> WO</span>');
      if (s.hasOwnProperty("po_count")) actParts.push('<span><strong>' + (s.po_count || 0) + '</strong> PO</span>');
      if (s.hasOwnProperty("invoiced_hours")) actParts.push('<span><strong>' + fmtHours(s.invoiced_hours) + '</strong> invoiced hrs</span>');
      if (s.hasOwnProperty("total_mech_hours")) actParts.push('<span><strong>' + fmtHours(s.total_mech_hours) + '</strong> mech hrs</span>');
      if (weeks) actParts.push('<span><strong>' + weeks + '</strong> wk period</span>');
      if (actParts.length) {
        html += '<div class="border rounded p-2 mb-3 d-flex flex-wrap gap-3 small text-muted align-items-center">' +
          '<span class="text-uppercase fw-semibold text-secondary" style="letter-spacing:.04em">Activity</span>' +
          actParts.join('<span class="text-muted">·</span>') +
        '</div>';
      }

      // ── Disclaimer ────────────────────────────────────────────────────
      var notes = [];
      notes.push("Two Net Revenue views differ in how parts cost is counted: <em>Parts Orders basis</em> uses real money spent at vendors in the period; <em>Parts Cost basis</em> uses the cost of parts actually billed on Work Orders.");
      if (hasPayroll) {
        notes.push("Salaries = internal weekly salary × " + (weeks || 0) + " weeks + uAttend hourly punches × rate (AI-matched employees counted once).");
      } else if (s.customer_filter_active) {
        notes.push("<strong>Customer filter is active</strong> — salaries and parts orders are hidden because they cannot be attributed to specific customers.");
      } else {
        notes.push("Salaries unavailable — configure the uAttend integration and per-user pay rates to include payroll.");
      }
      notes.push("Full line-by-line breakdown is in the table below.");
      html += '<div class="alert alert-secondary py-2 small mb-3" style="border:1px solid var(--bs-border-color)">' +
        '<strong>How to read:</strong> ' + notes.join(" ") +
      '</div>';

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
      return '<tr class="text-muted"><th>Category</th><th class="text-end">Amount</th></tr>';
    }
    if (tab === "mechanic_hours") {
      return '<tr class="text-muted"><th>Mechanic</th><th class="text-end">Hours</th><th class="text-end">Work Orders</th><th class="text-end">Labor Entries</th></tr>';
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
      var isBold = cat.indexOf("Total") !== -1 || cat.indexOf("Profit") !== -1 || cat.indexOf("Net") !== -1;
      var cls = isBold ? ' class="fw-semibold"' : '';
      var isSep = cat.indexOf("Parts Orders") === 0 && cat.indexOf("Parts Orders \u2014 Parts") === 0;
      var sep = isSep ? '<tr><td colspan="2" class="border-0 py-1"></td></tr>' : '';
      // Empty separator row
      if (!cat && row.amount == null) {
        return '<tr><td colspan="2" class="border-0 py-2"></td></tr>';
      }
      // Section header injection — detect first row of each logical group.
      var sectionHeader = "";
      if (cat === "Sales — Labor") {
        sectionHeader = '<tr class="table-secondary"><td colspan="2" class="fw-semibold small text-uppercase" style="letter-spacing:.04em">Sales (Work Orders)</td></tr>';
      } else if (cat === "Parts Orders — Parts") {
        sectionHeader = '<tr class="table-secondary"><td colspan="2" class="fw-semibold small text-uppercase" style="letter-spacing:.04em">Parts Orders</td></tr>';
      } else if (cat.indexOf("Labor Payroll — Salary") === 0) {
        sectionHeader = '<tr class="table-secondary"><td colspan="2" class="fw-semibold small text-uppercase" style="letter-spacing:.04em">Labor Payroll (Salary × weeks + uAttend hourly)</td></tr>';
      } else if (cat === "Mechanic Hours — Total") {
        sectionHeader = '<tr class="table-secondary"><td colspan="2" class="fw-semibold small text-uppercase" style="letter-spacing:.04em">Mechanic Hours (from Work Orders)</td></tr>';
      }
      // Hours rows (mechanic section)
      if (row.is_hours) {
        return sectionHeader + sep + '<tr><td' + cls + '>' + escapeHtml(cat) + '</td>' +
          '<td class="text-end' + (isBold ? ' fw-semibold' : '') + '">' + fmtHours(row.amount) + ' hrs</td></tr>';
      }
      return sectionHeader + sep + '<tr><td' + cls + '>' + escapeHtml(cat) + '</td>' +
        '<td class="text-end' + (isBold ? ' fw-semibold' : '') + '">$' + fmtMoney(row.amount) + '</td></tr>';
    }
    if (tab === "mechanic_hours") {
      return '<tr><td>' + escapeHtml(row.mechanic_name) + '</td>' +
        '<td class="text-end fw-semibold">' + fmtHours(row.total_hours) + '</td>' +
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
