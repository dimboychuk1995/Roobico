(function () {
  let activeRequestId = 0;

  function asNumber(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : 0;
  }

  function money(value) {
    return `$${asNumber(value).toFixed(2)}`;
  }

  function percent1(value) {
    return `${asNumber(value).toFixed(1)}%`;
  }

  function clampPercent(value) {
    const n = asNumber(value);
    if (n < 0) return 0;
    if (n > 100) return 100;
    return n;
  }

  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function setDonutVar(id, name, value) {
    const el = document.getElementById(id);
    if (el) el.style.setProperty(name, `${clampPercent(value).toFixed(2)}%`);
  }

  function renderDonutBackgrounds(data) {
    const paid = clampPercent(data.paid_percent);
    const woDonut = document.getElementById('dashWoDonut');
    if (woDonut) {
      woDonut.style.background =
        `conic-gradient(from -90deg, #2d8b58 0 ${paid.toFixed(2)}%, #d25a5a ${paid.toFixed(2)}% 100%), ` +
        'radial-gradient(circle at 30% 20%, rgba(255, 255, 255, 0.55) 0%, rgba(255, 255, 255, 0) 45%)';
    }

    const outer = clampPercent(data.parts_orders_received_percent);
    const inner = clampPercent(data.parts_orders_paid_percent_by_amount);
    const outerRing = document.getElementById('dashPoOuterRing');
    const innerRing = document.getElementById('dashPoInnerRing');
    if (outerRing) {
      outerRing.style.background =
        `conic-gradient(from -90deg, #2d6ca8 0 ${outer.toFixed(2)}%, #cf7a2d ${outer.toFixed(2)}% 100%)`;
    }
    if (innerRing) {
      innerRing.style.background =
        `conic-gradient(from -90deg, #2a7a4f 0 ${inner.toFixed(2)}%, #c44545 ${inner.toFixed(2)}% 100%)`;
    }
  }

  function hideLoaders() {
    document.querySelectorAll('.dashboard-async-card').forEach((card) => {
      card.classList.add('dashboard-loaded');
    });
  }

  function showLoaders() {
    document.querySelectorAll('.dashboard-async-card').forEach((card) => {
      card.classList.remove('dashboard-loaded');
      card.classList.remove('dashboard-load-error');
    });
  }

  function setLoadError() {
    document.querySelectorAll('.dashboard-async-card').forEach((card) => {
      card.classList.add('dashboard-load-error');
      card.classList.add('dashboard-loaded');
    });
  }

  function buildQueryString() {
    const form = document.getElementById('dashboardFiltersForm');
    if (!form) {
      return window.location.search || '';
    }

    const data = new FormData(form);
    const params = new URLSearchParams();
    data.forEach((value, key) => {
      params.append(key, String(value));
    });
    const qs = params.toString();
    return qs ? `?${qs}` : '';
  }

  function renderMechanicHours(rows) {
    const tableWrap = document.getElementById('dashMechanicHoursTableWrap');
    const body = document.getElementById('dashMechanicHoursBody');
    const empty = document.getElementById('dashMechanicHoursEmpty');
    if (!tableWrap || !body || !empty) return;

    body.innerHTML = '';
    if (!Array.isArray(rows) || rows.length === 0) {
      tableWrap.style.display = 'none';
      empty.style.display = '';
      return;
    }

    rows.forEach((row) => {
      const tr = document.createElement('tr');
      const tdName = document.createElement('td');
      const tdHours = document.createElement('td');
      tdName.textContent = row && row.name ? String(row.name) : 'Unknown mechanic';
      tdHours.className = 'text-end';
      tdHours.textContent = `${asNumber(row && row.hours).toFixed(2)} h`;
      tr.appendChild(tdName);
      tr.appendChild(tdHours);
      body.appendChild(tr);
    });

    tableWrap.style.display = '';
    empty.style.display = 'none';
  }

  function render(data) {
    setDonutVar('dashWoDonut', '--paid', data.paid_percent);
    renderDonutBackgrounds(data);
    setText('dashPeriodMoneyTotal', money(data.period_money_total));
    setText('dashPeriodTotal', String(asNumber(data.period_total)));
    setText('dashPaidPercent', `${percent1(data.paid_percent)} paid`);
    setText('dashPeriodPaidAmount', money(data.period_paid_amount));
    setText('dashPeriodUnpaidAmount', money(data.period_unpaid_amount));
    setText('dashPeriodLaborTotal', money(data.period_labor_total));
    setText('dashPeriodPartsTotal', money(data.period_parts_total));
    setText('dashPeriodGrandTotal', money(data.period_grand_total));

    setDonutVar('dashPoOuterRing', '--outer', data.parts_orders_received_percent);
    setDonutVar('dashPoInnerRing', '--inner', data.parts_orders_paid_percent_by_amount);
    setText('dashPeriodPartsOrdersTotal', String(asNumber(data.period_parts_orders_total)));
    setText('dashPartsOrdersReceivedPercent', `${percent1(data.parts_orders_received_percent)} received (count)`);
    setText('dashPartsOrdersPaidPercentByAmount', `${percent1(data.parts_orders_paid_percent_by_amount)} paid (amount)`);
    setText('dashPeriodPartsOrdersReceived', String(asNumber(data.period_parts_orders_received)));
    setText('dashPeriodPartsOrdersOrdered', String(asNumber(data.period_parts_orders_ordered)));
    setText('dashPeriodPartsOrdersPaidCount', String(asNumber(data.period_parts_orders_paid_count)));
    setText('dashPeriodPartsOrdersUnpaidCount', String(asNumber(data.period_parts_orders_unpaid_count)));
    setText('dashPeriodPartsOrdersPaidAmount', `${asNumber(data.period_parts_orders_paid_amount).toFixed(2)}$`);
    setText('dashPeriodPartsOrdersUnpaidAmount', `${asNumber(data.period_parts_orders_unpaid_amount).toFixed(2)}$`);
    setText('dashPeriodPartsOrdersItemsAmount', money(data.period_parts_orders_items_amount));
    setText('dashPeriodPartsOrdersNonInventoryAmount', money(data.period_parts_orders_non_inventory_amount));
    setText('dashPeriodPartsOrdersTotalAmount', money(data.period_parts_orders_total_amount));

    const goalCount = asNumber(data.goal_count);
    const goalPercent = clampPercent(data.goal_percent);
    setText('dashGoalCount', String(goalCount));
    const goalProgress = document.getElementById('dashGoalProgress');
    const goalProgressBar = document.getElementById('dashGoalProgressBar');
    if (goalProgress) goalProgress.setAttribute('aria-valuenow', String(Math.round(goalPercent)));
    if (goalProgressBar) goalProgressBar.style.width = `${goalPercent.toFixed(2)}%`;
    setText(
      'dashGoalSummary',
      `Current: ${asNumber(data.period_wo_total)} / ${goalCount} (${goalPercent.toFixed(1)}%)`
    );

    setText('dashOutstandingBalance', money(data.outstanding_balance));

    renderMechanicHours(data.mechanic_hours_rows);
  }

  async function loadMetrics() {
    const baseUrl = window.DASHBOARD_METRICS_API_URL;
    if (!baseUrl) return;

    const thisRequestId = ++activeRequestId;
    showLoaders();
    const qs = buildQueryString();
    const url = `${baseUrl}${qs}`;

    let lastError = null;
    const maxAttempts = 3;
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      const controller = new AbortController();
      const timeoutId = window.setTimeout(() => controller.abort(), 45000);
      try {
        const res = await fetch(url, {
          headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
          cache: 'no-store',
          credentials: 'same-origin',
          signal: controller.signal,
        });
        window.clearTimeout(timeoutId);

        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const data = await res.json();
        if (!data || !data.ok) {
          throw new Error('Metrics payload is invalid');
        }
        if (thisRequestId !== activeRequestId) {
          return;
        }
        render(data);
        hideLoaders();
        return;
      } catch (err) {
        window.clearTimeout(timeoutId);
        lastError = err;
        if (thisRequestId !== activeRequestId) {
          return;
        }
        if (attempt < maxAttempts) {
          await new Promise((resolve) => window.setTimeout(resolve, 300 * attempt));
        }
      }
    }

    if (thisRequestId !== activeRequestId) {
      return;
    }
    if (lastError) {
      setLoadError();
    }
  }

  function init() {
    loadMetrics();
  }

  window.smallshopInitDashboardMetrics = init;

  window.addEventListener('smallshop:content-replaced', function () {
    if (document.getElementById('dashboardFiltersForm')) {
      init();
    }
  });

  window.addEventListener('pageshow', function () {
    if (document.getElementById('dashboardFiltersForm')) {
      init();
    }
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
