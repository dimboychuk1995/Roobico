(function () {
  let activeBatchId = 0;
  const blockRequestIds = new Map();

  function asNumber(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : 0;
  }

  function money(value) {
    return `$${asNumber(value).toFixed(2)}`;
  }

  function percent1(value) {
    const n = asNumber(value);
    if (n > 0 && n < 0.1) return `${n.toFixed(2)}%`;
    return `${n.toFixed(1)}%`;
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

  function buildConicGradient(primaryColor, primaryPercent, secondaryColor) {
    const primary = clampPercent(primaryPercent);
    return `conic-gradient(from -90deg, ${primaryColor} 0%, ${primaryColor} ${primary.toFixed(2)}%, ${secondaryColor} ${primary.toFixed(2)}%, ${secondaryColor} 100%)`;
  }

  function renderWoMoneyDonut(data) {
    const paid = clampPercent(data.paid_percent);
    const woDonut = document.getElementById('dashWoDonut');
    if (woDonut) {
      woDonut.style.background =
        `${buildConicGradient('#1a7a42', paid, '#c43b3b')}`;
    }
  }

  function renderPartsOrdersDonut(data) {
    const outer = clampPercent(data.parts_orders_received_percent);
    const inner = clampPercent(data.parts_orders_paid_percent_by_amount);
    const outerRing = document.getElementById('dashPoOuterRing');
    const innerRing = document.getElementById('dashPoInnerRing');
    if (outerRing) {
      outerRing.style.background = buildConicGradient('#2d8b58', outer, '#c48a1a');
    }
    if (innerRing) {
      innerRing.style.background = buildConicGradient('#1a7a42', inner, '#c43b3b');
    }
  }

  function setCardLoading(card) {
    if (!card) return;
    card.classList.remove('dashboard-loaded');
    card.classList.remove('dashboard-load-error');
  }

  function setCardLoaded(card) {
    if (!card) return;
    card.classList.add('dashboard-loaded');
    card.classList.remove('dashboard-load-error');
  }

  function setCardLoadError(card) {
    if (!card) return;
    card.classList.add('dashboard-load-error');
    card.classList.add('dashboard-loaded');
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

    // Re-init sorting for refreshed table
    var tbl = tableWrap.querySelector("table");
    if (tbl && window.TableSort) window.TableSort.refresh(tbl);
  }

  function renderWoMoney(data) {
    setDonutVar('dashWoDonut', '--paid', data.paid_percent);
    renderWoMoneyDonut(data);
    setText('dashPeriodMoneyTotal', money(data.period_money_total));
    setText('dashPeriodTotal', String(asNumber(data.period_total)));
    setText('dashPaidPercent', `${percent1(data.paid_percent)} paid`);
    setText('dashPeriodPaidAmount', money(data.period_paid_amount));
    setText('dashPeriodUnpaidAmount', money(data.period_unpaid_amount));
    setText('dashPeriodLaborTotal', money(data.period_labor_total));
    setText('dashPeriodPartsTotal', money(data.period_parts_total));
    setText('dashPeriodGrandTotal', money(data.period_grand_total));
  }

  function renderPartsOrders(data) {
    setDonutVar('dashPoOuterRing', '--outer', data.parts_orders_received_percent);
    setDonutVar('dashPoInnerRing', '--inner', data.parts_orders_paid_percent_by_amount);
    renderPartsOrdersDonut(data);
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
  }

  function renderGoalProgress(data) {
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
  }

  function renderOutstandingBalance(data) {
    setText('dashOutstandingBalance', money(data.outstanding_balance));
  }

  function renderMechanicHoursBlock(data) {
    renderMechanicHours(data.mechanic_hours_rows);
  }

  const blockRenderers = {
    'wo-money': renderWoMoney,
    'parts-orders': renderPartsOrders,
    'goal-progress': renderGoalProgress,
    'outstanding-balance': renderOutstandingBalance,
    'mechanic-hours': renderMechanicHoursBlock,
  };

  function buildBlockUrl(blockName) {
    const template = window.DASHBOARD_METRICS_BLOCK_API_TEMPLATE;
    if (!template) return '';

    const baseUrl = template.replace('__BLOCK__', encodeURIComponent(blockName));
    const qs = buildQueryString();
    return qs ? `${baseUrl}${qs}` : baseUrl;
  }

  async function loadBlockMetrics(card, batchId) {
    if (!card) return;

    const blockName = String(card.dataset.block || '').trim();
    const renderBlock = blockRenderers[blockName];
    const url = buildBlockUrl(blockName);
    if (!blockName || !renderBlock || !url) {
      setCardLoadError(card);
      return;
    }

    const requestId = (blockRequestIds.get(blockName) || 0) + 1;
    blockRequestIds.set(blockName, requestId);
    setCardLoading(card);

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
        const payload = await res.json();
        if (!payload || !payload.ok || !payload.data) {
          throw new Error('Metrics payload is invalid');
        }
        if (batchId !== activeBatchId || requestId !== blockRequestIds.get(blockName) || !card.isConnected) {
          return;
        }
        renderBlock(payload.data);
        setCardLoaded(card);
        return;
      } catch (err) {
        window.clearTimeout(timeoutId);
        lastError = err;
        if (batchId !== activeBatchId || requestId !== blockRequestIds.get(blockName)) {
          return;
        }
        if (attempt < maxAttempts) {
          await new Promise((resolve) => window.setTimeout(resolve, 300 * attempt));
        }
      }
    }

    if (batchId !== activeBatchId || requestId !== blockRequestIds.get(blockName)) {
      return;
    }
    if (lastError) {
      setCardLoadError(card);
    }
  }

  function loadMetrics() {
    activeBatchId += 1;
    const batchId = activeBatchId;
    document.querySelectorAll('.dashboard-async-card[data-block]').forEach((card) => {
      loadBlockMetrics(card, batchId);
    });
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
