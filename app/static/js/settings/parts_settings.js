(function () {
  function initPartsSettingsPage() {
  // Elements
  const body = document.getElementById('mmRulesBody');
  const addBtn = document.getElementById('mmAddRow');
  const normalizeBtn = document.getElementById('mmNormalize');
  const dumpBtn = document.getElementById('mmDumpJson');
  const previewWrap = document.getElementById('mmJsonPreviewWrap');
  const previewEl = document.getElementById('mmJsonPreview');

  const modeMargin = document.getElementById('mode_margin');
  const modeMarkup = document.getElementById('mode_markup');
  const valueLabel = document.getElementById('mmValueLabel');

  const saveBtn = document.getElementById('mmSaveRules');      // optional but expected
  const reloadBtn = document.getElementById('mmReloadRules');  // optional

  const cardBody = body ? body.closest('.card-body') : null;

  // If page doesn't have this block - exit
  if (!body || !addBtn || !normalizeBtn || !dumpBtn || !previewWrap || !previewEl || !modeMargin || !modeMarkup || !valueLabel) {
    return;
  }

  // Backend endpoints (adjust if your prefix differs)
  const URL_GET = '/settings/parts-settings/pricing-rules';
  const URL_SAVE = '/settings/parts-settings/pricing-rules/save';

  // ---------------------------
  // UI helpers
  // ---------------------------
  function currentMode() {
    return modeMarkup.checked ? 'markup' : 'margin';
  }

  function setMode(mode) {
    const m = (mode || '').toLowerCase();
    if (m === 'markup') {
      modeMarkup.checked = true;
      modeMargin.checked = false;
    } else {
      modeMargin.checked = true;
      modeMarkup.checked = false;
    }
    updateValueLabel();
  }

  function updateValueLabel() {
    valueLabel.textContent = currentMode() === 'markup' ? 'Markup %' : 'Margin %';
  }

  function clearPreview() {
    previewWrap.classList.add('d-none');
    previewEl.textContent = '';
  }

  function removeExistingAlert() {
    if (!cardBody) return;
    const old = cardBody.querySelector('#mmAlert');
    if (old) old.remove();
  }

  function showAlert(message, type = 'info') {
    if (!cardBody) {
      alert(message);
      return;
    }
    removeExistingAlert();
    const div = document.createElement('div');
    div.id = 'mmAlert';
    div.className = `alert alert-${type} py-2 mt-2`;
    div.innerHTML = `<div class="small mb-0">${escapeHtml(String(message || ''))}</div>`;
    // place under header line (after alert-info tip if present), otherwise at top
    const tip = cardBody.querySelector('.alert.alert-info');
    if (tip && tip.parentNode) {
      tip.insertAdjacentElement('afterend', div);
    } else {
      cardBody.insertAdjacentElement('afterbegin', div);
    }
  }

  function escapeHtml(str) {
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function setLoading(isLoading) {
    const allBtns = [addBtn, normalizeBtn, dumpBtn, saveBtn, reloadBtn].filter(Boolean);
    allBtns.forEach((b) => (b.disabled = !!isLoading));
  }

  // ---------------------------
  // Data helpers
  // ---------------------------
  function parseNumber(val) {
    if (val === null || val === undefined) return null;
    const s = String(val).trim();
    if (!s) return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  }

  function addRow(fromVal = null, toVal = null, percentVal = null) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><input type="number" step="0.01" min="0" class="form-control form-control-sm mm-from" value="${fromVal ?? ''}"></td>
      <td><input type="number" step="0.01" min="0" class="form-control form-control-sm mm-to" value="${toVal ?? ''}"></td>
      <td>
        <div class="input-group input-group-sm">
          <input type="number" step="0.01" class="form-control mm-val" value="${percentVal ?? ''}">
          <span class="input-group-text">%</span>
        </div>
      </td>
      <td class="text-end">
        <button class="btn btn-sm btn-outline-danger mm-del" type="button" title="Remove">
          <i class="bi bi-trash"></i> Remove
        </button>
      </td>
    `;
    body.appendChild(tr);
    clearPreview();
  }

  function removeRow(btn) {
    const tr = btn.closest('tr');
    if (tr) tr.remove();
    clearPreview();
  }

  function clearRows() {
    body.innerHTML = '';
    clearPreview();
  }

  function renderRules(rules) {
    clearRows();
    const arr = Array.isArray(rules) ? rules : [];
    if (arr.length === 0) {
      // fallback 1 row
      addRow(0, null, null);
      return;
    }
    arr.forEach((r) => {
      const from = (r && r.from !== undefined) ? r.from : null;
      const to = (r && r.to !== undefined) ? r.to : null;
      const val = (r && (r.value_percent !== undefined)) ? r.value_percent : null;
      addRow(from, to, val);
    });
  }

  function getRules() {
    const rows = Array.from(body.querySelectorAll('tr'));
    const out = rows.map((tr) => {
      const from = parseNumber(tr.querySelector('.mm-from')?.value);
      const to = parseNumber(tr.querySelector('.mm-to')?.value); // null => infinity
      const percent = parseNumber(tr.querySelector('.mm-val')?.value);
      return { from, to, value_percent: percent };
    });

    // keep as-is (server will validate), but drop completely empty lines
    return out.filter((r) => !(r.from === null && r.to === null && r.value_percent === null));
  }

  function autoFillNextFrom() {
    const rows = Array.from(body.querySelectorAll('tr'));
    if (rows.length === 0) {
      addRow(0, null, null);
      return;
    }
    const last = rows[rows.length - 1];
    const lastTo = parseNumber(last.querySelector('.mm-to')?.value);
    const nextFrom = lastTo !== null ? lastTo : null;
    addRow(nextFrom, null, null);
  }

  // ---------------------------
  // Backend calls
  // ---------------------------
  async function fetchJson(url, options) {
    const res = await fetch(url, options);
    const data = await res.json().catch(() => ({}));
    return { res, data };
  }

  async function reloadFromBackend() {
    setLoading(true);
    removeExistingAlert();
    try {
      const { res, data } = await fetchJson(URL_GET, { method: 'GET' });
      if (!res.ok || !data.ok) {
        const msg = (data && data.error) ? data.error : 'Failed to load pricing rules.';
        showAlert(msg, 'warning');
        return;
      }
      setMode(data.mode || 'margin');
      renderRules(data.rules || []);
      showAlert('Loaded pricing rules from database.', 'success');
    } catch (e) {
      showAlert('Network error while loading pricing rules.', 'danger');
    } finally {
      setLoading(false);
    }
  }

  async function saveToBackend() {
    setLoading(true);
    removeExistingAlert();
    try {
      const payload = {
        mode: currentMode(),
        rules: getRules(),
      };

      const { res, data } = await fetchJson(URL_SAVE, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!res.ok || !data.ok) {
        const msg = (data && data.error) ? data.error : 'Failed to save pricing rules.';
        showAlert(msg, 'warning');
        return;
      }

      showAlert('Saved pricing rules.', 'success');
    } catch (e) {
      showAlert('Network error while saving pricing rules.', 'danger');
    } finally {
      setLoading(false);
    }
  }

  // ---------------------------
  // Events
  // ---------------------------
  addBtn.addEventListener('click', () => autoFillNextFrom());
  normalizeBtn.addEventListener('click', () => autoFillNextFrom());

  dumpBtn.addEventListener('click', () => {
    const payload = {
      mode: currentMode(),
      rules: getRules(),
    };
    previewEl.textContent = JSON.stringify(payload, null, 2);
    previewWrap.classList.remove('d-none');
  });

  body.addEventListener('click', (e) => {
    const btn = e.target.closest('.mm-del');
    if (btn) removeRow(btn);
  });

  modeMargin.addEventListener('change', () => {
    updateValueLabel();
    clearPreview();
  });
  modeMarkup.addEventListener('change', () => {
    updateValueLabel();
    clearPreview();
  });

  if (saveBtn) {
    saveBtn.addEventListener('click', () => saveToBackend());
  }

  if (reloadBtn) {
    reloadBtn.addEventListener('click', () => reloadFromBackend());
  }

  // Init
  updateValueLabel();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initPartsSettingsPage, { once: true });
  } else {
    initPartsSettingsPage();
  }
})();
