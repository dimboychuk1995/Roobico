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

  // ── Labor hours trend: Actual / Invoiced / uAttend ───────────────────
  const SVG_NS = 'http://www.w3.org/2000/svg';
  let lastHoursChart = null;
  let hoursResizeBound = false;

  function svgEl(name, attrs) {
    const el = document.createElementNS(SVG_NS, name);
    Object.keys(attrs || {}).forEach((key) => el.setAttribute(key, String(attrs[key])));
    return el;
  }

  function hoursText(value) {
    return `${asNumber(value).toFixed(2)} h`;
  }

  function bucketLabel(iso) {
    const d = new Date(`${iso}T00:00:00Z`);
    return Number.isNaN(d.getTime())
      ? String(iso)
      : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  }

  function niceStep(raw) {
    if (!(raw > 0)) return 1;
    const pow = Math.pow(10, Math.floor(Math.log10(raw)));
    const base = raw / pow;
    let mult = 10;
    if (base <= 1) mult = 1;
    else if (base <= 2) mult = 2;
    else if (base <= 2.5) mult = 2.5;
    else if (base <= 5) mult = 5;
    return mult * pow;
  }

  function cumulativeSum(values) {
    let acc = 0;
    return values.map((v) => {
      acc += asNumber(v);
      return acc;
    });
  }

  // API отдаёт часы по дням; график накопительный — рисуем нарастающий
  // итог, дневная прибавка остаётся в тултипе.
  function hoursChartSeries(chart) {
    const series = [
      { key: 'actual', name: 'Actual', daily: chart.actual || [] },
      { key: 'invoiced', name: 'Invoiced', daily: chart.invoiced || [] },
    ];
    if (chart.uattend_connected && Array.isArray(chart.uattend)) {
      series.push({ key: 'uattend', name: 'uAttend', daily: chart.uattend });
    }
    series.forEach((s) => { s.values = cumulativeSum(s.daily); });
    return series;
  }

  function renderHoursChart(chart) {
    const wrap = document.getElementById('dashHoursChartWrap');
    const svg = document.getElementById('dashHoursChart');
    const tooltip = document.getElementById('dashHoursTooltip');
    const legend = document.getElementById('dashHoursLegend');
    const legendUattend = document.getElementById('dashHoursLegendUattend');
    const empty = document.getElementById('dashHoursEmpty');
    if (!wrap || !svg || !tooltip || !empty) return;

    const labels = (chart && chart.labels) || [];
    const series = chart ? hoursChartSeries(chart) : [];
    const hasData = labels.length > 0 && series.some((s) => s.daily.some((v) => asNumber(v) > 0));

    if (!hasData) {
      wrap.style.display = 'none';
      if (legend) legend.style.display = 'none';
      empty.style.display = '';
      return;
    }

    wrap.style.display = '';
    empty.style.display = 'none';
    if (legend) legend.style.display = '';
    if (legendUattend) {
      legendUattend.style.display = chart.uattend_connected ? '' : 'none';
    }

    while (svg.firstChild) svg.removeChild(svg.firstChild);
    tooltip.hidden = true;

    const width = Math.max(320, wrap.clientWidth || 640);
    const height = 240;
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('width', String(width));
    svg.setAttribute('height', String(height));

    const margin = { top: 12, right: 16, bottom: 26, left: 46 };
    const plotW = width - margin.left - margin.right;
    const plotH = height - margin.top - margin.bottom;
    const n = labels.length;

    let rawMax = 0;
    series.forEach((s) => s.values.forEach((v) => { rawMax = Math.max(rawMax, asNumber(v)); }));
    const step = niceStep(rawMax / 4);
    const yMax = Math.max(step, Math.ceil(rawMax / step) * step);

    // Слот 0 — нулевая стартовая точка (линии выходят из нуля),
    // данные занимают слоты 1..n.
    const xAtSlot = (s) => margin.left + (plotW * s) / n;
    const xAt = (i) => xAtSlot(i + 1);
    const yAt = (v) => margin.top + plotH - (plotH * Math.max(0, asNumber(v))) / yMax;

    // Y gridlines + ticks
    for (let t = 0; t <= yMax + 1e-9; t += step) {
      const y = yAt(t);
      svg.appendChild(svgEl('line', {
        x1: margin.left, x2: width - margin.right, y1: y, y2: y,
        class: t === 0 ? 'dashboard-hours-axis' : 'dashboard-hours-grid',
      }));
      const tick = svgEl('text', {
        x: margin.left - 8, y: y + 3, 'text-anchor': 'end', class: 'dashboard-hours-tick',
      });
      tick.textContent = Number.isInteger(t) ? String(t) : t.toFixed(1);
      svg.appendChild(tick);
    }

    // X ticks (~6, always the last one)
    const xStepCount = Math.max(1, Math.ceil(n / 6));
    for (let i = 0; i < n; i += 1) {
      const isLast = i === n - 1;
      if (i % xStepCount !== 0 && !isLast) continue;
      if (!isLast && n - 1 - i < xStepCount / 2) continue;
      const tick = svgEl('text', {
        x: xAt(i), y: height - 8, 'text-anchor': 'middle', class: 'dashboard-hours-tick',
      });
      tick.textContent = bucketLabel(labels[i]);
      svg.appendChild(tick);
    }

    // Lines + point markers (первая вершина — нулевая стартовая точка)
    series.forEach((s) => {
      const points = [`${xAtSlot(0).toFixed(1)},${yAt(0).toFixed(1)}`]
        .concat(s.values.map((v, i) => `${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`))
        .join(' ');
      svg.appendChild(svgEl('polyline', {
        points, class: `dashboard-hours-line dashboard-hours-line-${s.key}`,
      }));
      if (n <= 45) {
        s.values.forEach((v, i) => {
          svg.appendChild(svgEl('circle', {
            cx: xAt(i), cy: yAt(v), r: 4,
            class: `dashboard-hours-dot dashboard-hours-dot-${s.key}`,
          }));
        });
      }
    });

    // Hover: crosshair + snap dots + tooltip listing every series
    const crosshair = svgEl('line', {
      y1: margin.top, y2: margin.top + plotH, class: 'dashboard-hours-crosshair',
    });
    crosshair.style.display = 'none';
    svg.appendChild(crosshair);

    const hoverDots = series.map((s) => {
      const dot = svgEl('circle', { r: 4.5, class: `dashboard-hours-dot dashboard-hours-dot-${s.key}` });
      dot.style.display = 'none';
      svg.appendChild(dot);
      return dot;
    });

    const overlay = svgEl('rect', {
      x: margin.left, y: margin.top, width: plotW, height: plotH,
      fill: 'transparent',
    });
    svg.appendChild(overlay);

    function showTooltip(index, pointerX) {
      crosshair.setAttribute('x1', xAt(index));
      crosshair.setAttribute('x2', xAt(index));
      crosshair.style.display = '';
      series.forEach((s, si) => {
        hoverDots[si].setAttribute('cx', xAt(index));
        hoverDots[si].setAttribute('cy', yAt(s.values[index]));
        hoverDots[si].style.display = '';
      });

      while (tooltip.firstChild) tooltip.removeChild(tooltip.firstChild);
      const head = document.createElement('div');
      head.className = 'dashboard-hours-tooltip-head';
      head.textContent = bucketLabel(labels[index]);
      tooltip.appendChild(head);
      series.forEach((s) => {
        const row = document.createElement('div');
        row.className = 'dashboard-hours-tooltip-row';
        const key = document.createElement('span');
        key.className = `dashboard-hours-tooltip-key dashboard-hours-tooltip-key-${s.key}`;
        const name = document.createElement('span');
        name.className = 'dashboard-hours-tooltip-name';
        name.textContent = s.name;
        const delta = document.createElement('span');
        delta.className = 'dashboard-hours-tooltip-delta';
        delta.textContent = `+${asNumber(s.daily[index]).toFixed(2)}`;
        const value = document.createElement('strong');
        value.textContent = hoursText(s.values[index]);
        row.appendChild(key);
        row.appendChild(name);
        row.appendChild(delta);
        row.appendChild(value);
        tooltip.appendChild(row);
      });

      tooltip.hidden = false;
      const wrapRect = wrap.getBoundingClientRect();
      const tipW = tooltip.offsetWidth || 140;
      let left = pointerX + 14;
      if (left + tipW > wrapRect.width - 6) left = pointerX - tipW - 14;
      tooltip.style.left = `${Math.max(6, left)}px`;
      tooltip.style.top = '10px';
    }

    function hideTooltip() {
      crosshair.style.display = 'none';
      hoverDots.forEach((dot) => { dot.style.display = 'none'; });
      tooltip.hidden = true;
    }

    overlay.addEventListener('pointermove', (ev) => {
      const rect = svg.getBoundingClientRect();
      const x = ev.clientX - rect.left;
      const rel = (x - margin.left) / (plotW / n) - 1;
      const index = Math.min(n - 1, Math.max(0, Math.round(rel)));
      showTooltip(index, x);
    });
    overlay.addEventListener('pointerleave', hideTooltip);
  }

  function renderHoursSummary(chart) {
    const summaryWrap = document.getElementById('dashHoursSummary');
    const totalsBox = document.getElementById('dashHoursTotalsBox');
    const rowsWrap = document.getElementById('dashHoursRowsWrap');
    const rowsBody = document.getElementById('dashHoursRowsBody');
    if (!summaryWrap || !totalsBox || !rowsWrap || !rowsBody) return;

    const summary = (chart && chart.summary) || null;
    if (!summary) {
      summaryWrap.style.display = 'none';
      return;
    }
    summaryWrap.style.display = '';

    while (totalsBox.firstChild) totalsBox.removeChild(totalsBox.firstChild);
    const addPill = (label, valueText, titleText) => {
      const span = document.createElement('span');
      if (titleText) span.title = titleText;
      span.appendChild(document.createTextNode(`${label}: `));
      const strong = document.createElement('strong');
      strong.textContent = valueText;
      span.appendChild(strong);
      totalsBox.appendChild(span);
    };

    addPill('Actual', hoursText(summary.actual_total), 'Time mechanics tracked with job timers');
    addPill('Invoiced', hoursText(summary.invoiced_total), 'Labor hours billed on work orders');
    if (chart.uattend_connected && summary.uattend_total !== null && summary.uattend_total !== undefined) {
      addPill('uAttend', hoursText(summary.uattend_total), 'Hours from the uAttend time clock');
    }
    if (summary.efficiency_percent !== null && summary.efficiency_percent !== undefined) {
      addPill('Invoiced ÷ Actual', percent1(summary.efficiency_percent),
        'Billed hours per hour of tracked work. Above 100% — you bill more than the time spent.');
    }
    if (summary.utilization_percent !== null && summary.utilization_percent !== undefined) {
      addPill('Actual ÷ uAttend', percent1(summary.utilization_percent),
        'Share of clocked shift time spent working on work orders.');
    }

    const rows = Array.isArray(chart.rows) ? chart.rows : [];
    const showUattendCol = Boolean(chart.uattend_connected);
    const table = rowsWrap.querySelector('table');
    if (table) {
      table.querySelectorAll('.dashboard-hours-uattend-col').forEach((el) => {
        el.style.display = showUattendCol ? '' : 'none';
      });
    }

    rowsBody.innerHTML = '';
    if (!rows.length) {
      rowsWrap.style.display = 'none';
      return;
    }
    rows.forEach((row) => {
      const tr = document.createElement('tr');
      const tdName = document.createElement('td');
      tdName.textContent = row && row.name ? String(row.name) : 'Unknown mechanic';
      tr.appendChild(tdName);
      ['actual', 'invoiced'].concat(showUattendCol ? ['uattend'] : []).forEach((key) => {
        const td = document.createElement('td');
        td.className = 'text-end';
        if (key === 'uattend') td.classList.add('dashboard-hours-uattend-col');
        const value = row ? row[key] : null;
        td.textContent = value === null || value === undefined ? '—' : asNumber(value).toFixed(2);
        tr.appendChild(td);
      });
      rowsBody.appendChild(tr);
    });
    rowsWrap.style.display = '';

    if (table && window.TableSort) window.TableSort.refresh(table);
  }

  function renderHoursNote(chart) {
    const note = document.getElementById('dashHoursNote');
    if (!note) return;
    const parts = [];
    if (chart && chart.window_note) parts.push(String(chart.window_note));
    if (chart && chart.uattend_connected) {
      if (chart.uattend_error) {
        parts.push(`uAttend: ${chart.uattend_error}`);
      } else if (!Array.isArray(chart.uattend) && asNumber(chart.summary && chart.summary.uattend_total) > 0) {
        parts.push('uAttend hours are shown in totals only (no daily breakdown available).');
      }
    }
    note.textContent = parts.join(' ');
    note.style.display = parts.length ? '' : 'none';
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
    setText('dashPeriodUnpaidTotal', money(data.period_unpaid_amount));
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
    const periodLabel = data && data.goals_period_label ? String(data.goals_period_label) : 'Selected Period';
    const labelEl = document.getElementById('dashGoalsPeriodLabel');
    if (labelEl) labelEl.textContent = periodLabel;

    const proratedNote = document.getElementById('dashGoalsProrationNote');
    if (proratedNote) {
      const isMonth = periodLabel === 'This Month' || periodLabel === 'Last Month';
      proratedNote.style.display = isMonth ? 'none' : '';
    }

    const period = (data && data.goals_period) || {};
    const actual = (data && data.goals_actual) || {};
    const percent = (data && data.goals_percent) || {};

    const groups = [
      { key: 'labor', ringId: 'dashGoalLaborRing', percentId: 'dashGoalLaborPercent',
        actualId: 'dashGoalLaborActual', targetId: 'dashGoalLaborTarget' },
      { key: 'parts_sales', ringId: 'dashGoalPartsRing', percentId: 'dashGoalPartsPercent',
        actualId: 'dashGoalPartsActual', targetId: 'dashGoalPartsTarget' },
      { key: 'total', ringId: 'dashGoalTotalRing', percentId: 'dashGoalTotalPercent',
        actualId: 'dashGoalTotalActual', targetId: 'dashGoalTotalTarget' },
    ];

    groups.forEach((g) => {
      const pct = clampPercent(percent[g.key]);
      const ring = document.getElementById(g.ringId);
      if (ring) {
        ring.style.setProperty('--pct', `${pct.toFixed(2)}%`);
      }
      setText(g.percentId, `${pct.toFixed(1)}%`);
      setText(g.actualId, money(actual[g.key]));
      setText(g.targetId, money(period[g.key]));
    });
  }

  function initGoalsModal() {
    const saveBtn = document.getElementById('dashGoalsSaveBtn');
    if (!saveBtn || saveBtn.dataset.bound === '1') return;
    saveBtn.dataset.bound = '1';

    saveBtn.addEventListener('click', async function () {
      const url = window.DASHBOARD_GOALS_SAVE_URL;
      if (!url) return;

      const errEl = document.getElementById('dashGoalsFormError');
      if (errEl) { errEl.style.display = 'none'; errEl.textContent = ''; }

      const payload = {
        labor: asNumber(document.getElementById('dashGoalLaborInput')?.value),
        parts_sales: asNumber(document.getElementById('dashGoalPartsInput')?.value),
        total: asNumber(document.getElementById('dashGoalTotalInput')?.value),
      };

      saveBtn.disabled = true;
      try {
        const res = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
          },
          credentials: 'same-origin',
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data || !data.ok) {
          throw new Error((data && data.error) || `HTTP ${res.status}`);
        }

        const modalEl = document.getElementById('dashGoalsModal');
        if (modalEl && window.bootstrap && window.bootstrap.Modal) {
          const inst = window.bootstrap.Modal.getOrCreateInstance(modalEl);
          inst.hide();
        }

        // Refresh just the goal-progress card
        const card = document.querySelector('.dashboard-async-card[data-block="goal-progress"]');
        if (card) {
          activeBatchId += 1;
          loadBlockMetrics(card, activeBatchId);
        }
      } catch (err) {
        if (errEl) {
          errEl.textContent = `Failed to save: ${err && err.message ? err.message : err}`;
          errEl.style.display = '';
        }
      } finally {
        saveBtn.disabled = false;
      }
    });
  }

  function renderOutstandingBalance(data) {
    setText('dashOutstandingBalance', money(data.outstanding_balance));
  }

  function renderMechanicHoursBlock(data) {
    const chart = data && data.hours_chart ? data.hours_chart : null;
    lastHoursChart = chart;
    renderHoursChart(chart);
    renderHoursSummary(chart);
    renderHoursNote(chart);

    if (!hoursResizeBound) {
      hoursResizeBound = true;
      let resizeTimer = null;
      window.addEventListener('resize', () => {
        if (resizeTimer) window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(() => {
          const wrap = document.getElementById('dashHoursChartWrap');
          if (lastHoursChart && wrap && wrap.isConnected) {
            renderHoursChart(lastHoursChart);
          }
        }, 150);
      });
    }
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
    initGoalsModal();
  }

  window.roobicoInitDashboardMetrics = init;

  window.addEventListener('roobico:content-replaced', function () {
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
