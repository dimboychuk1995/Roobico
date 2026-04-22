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
  const marginHint = document.getElementById('mmMarginHint');

  const saveBtn = document.getElementById('mmSaveRules');      // optional but expected
  const reloadBtn = document.getElementById('mmReloadRules');  // optional

  const scaleSelect = document.getElementById('mmScaleSelect');
  const createScaleBtn = document.getElementById('mmCreateScaleBtn');
  const setDefaultBtn = document.getElementById('mmSetDefaultBtn');
  const deleteScaleBtn = document.getElementById('mmDeleteScaleBtn');

  const cardBody = body ? body.closest('.card-body') : null;

  // If page doesn't have this block - exit
  if (!body || !addBtn || !normalizeBtn || !dumpBtn || !previewWrap || !previewEl || !modeMargin || !modeMarkup || !valueLabel) {
    return;
  }

  // Backend endpoints
  const URL_LIST = '/settings/parts-settings/pricing-rules';
  const URL_GET_ONE = (id) => `/settings/parts-settings/pricing-rules/${encodeURIComponent(id)}`;
  const URL_SAVE = '/settings/parts-settings/pricing-rules/save';
  const URL_CREATE = '/settings/parts-settings/pricing-rules/create';
  const URL_DELETE = (id) => `/settings/parts-settings/pricing-rules/${encodeURIComponent(id)}/delete`;
  const URL_SET_DEFAULT = (id) => `/settings/parts-settings/pricing-rules/${encodeURIComponent(id)}/set-default`;

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
    applyModeConstraints();
  }

  function updateValueLabel() {
    valueLabel.textContent = currentMode() === 'markup' ? 'Markup %' : 'Margin %';
  }

  function applyModeConstraints() {
    const isMargin = currentMode() === 'margin';
    if (marginHint) marginHint.classList.toggle('d-none', !isMargin);
    body.querySelectorAll('input.mm-val').forEach((inp) => {
      if (isMargin) {
        inp.setAttribute('max', '99.99');
      } else {
        inp.removeAttribute('max');
      }
    });
  }

  function clearPreview() {
    previewWrap.classList.add('d-none');
    previewEl.textContent = '';
  }

  function removeExistingAlert() { /* legacy no-op: alerts shown via Swal */ }

  function showAlert(message, type = 'info') {
    // Map bootstrap-style types to appAlert/Swal types.
    let mapped;
    switch (type) {
      case 'danger': mapped = 'error'; break;
      case 'warning': mapped = 'warning'; break;
      case 'success': mapped = 'success'; break;
      default: mapped = 'info';
    }
    if (typeof appAlert === 'function') {
      appAlert(String(message || ''), mapped);
    } else if (typeof Swal !== 'undefined') {
      Swal.fire({ icon: mapped === 'error' ? 'error' : mapped, title: String(message || '') });
    } else {
      window.alert(String(message || ''));
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
    const allBtns = [addBtn, normalizeBtn, dumpBtn, saveBtn, reloadBtn,
                     createScaleBtn, setDefaultBtn, deleteScaleBtn].filter(Boolean);
    allBtns.forEach((b) => (b.disabled = !!isLoading));
    if (scaleSelect) scaleSelect.disabled = !!isLoading;
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
    const isMargin = currentMode() === 'margin';
    const maxAttr = isMargin ? ' max="99.99"' : '';
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><input type="number" step="0.01" min="0" class="form-control form-control-sm mm-from" value="${fromVal ?? ''}"></td>
      <td><input type="number" step="0.01" min="0" class="form-control form-control-sm mm-to" value="${toVal ?? ''}"></td>
      <td>
        <div class="input-group input-group-sm">
          <input type="number" step="0.01" min="0"${maxAttr} class="form-control mm-val" value="${percentVal ?? ''}">
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
      const to = parseNumber(tr.querySelector('.mm-to')?.value);
      const percent = parseNumber(tr.querySelector('.mm-val')?.value);
      return { from, to, value_percent: percent };
    });
    return out.filter((r) => !(r.from === null && r.to === null && r.value_percent === null));
  }

  function validateClientSide(mode, rules) {
    if (mode === 'margin') {
      for (let i = 0; i < rules.length; i++) {
        const v = rules[i].value_percent;
        if (v !== null && v >= 100) {
          return `Rule #${i + 1}: margin must be less than 100%.`;
        }
      }
    }
    return null;
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

  function selectedScaleId() {
    return scaleSelect ? scaleSelect.value : '';
  }

  function setScaleOptions(scales, selectId) {
    if (!scaleSelect) return;
    scaleSelect.innerHTML = '';
    (scales || []).forEach((s) => {
      const opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = s.is_default ? `${s.name} (default)` : s.name;
      opt.dataset.mode = s.mode || 'margin';
      opt.dataset.default = s.is_default ? '1' : '0';
      scaleSelect.appendChild(opt);
    });
    if (selectId) scaleSelect.value = selectId;
  }

  async function loadScalesList(preferId) {
    const { res, data } = await fetchJson(URL_LIST, { method: 'GET' });
    if (!res.ok || !data.ok) return null;
    const scales = data.scales || [];
    let pickId = preferId;
    if (!pickId || !scales.some((s) => s.id === pickId)) {
      const def = scales.find((s) => s.is_default);
      pickId = (def || scales[0] || {}).id || '';
    }
    setScaleOptions(scales, pickId);
    return { scales, selectedId: pickId };
  }

  async function loadScale(id) {
    if (!id) {
      setMode('margin');
      renderRules([]);
      return;
    }
    const { res, data } = await fetchJson(URL_GET_ONE(id), { method: 'GET' });
    if (!res.ok || !data.ok) {
      showAlert((data && data.error) || 'Failed to load scale.', 'warning');
      return;
    }
    const sc = data.scale || {};
    setMode(sc.mode || 'margin');
    renderRules(sc.rules || []);
  }

  async function reloadFromBackend() {
    setLoading(true);
    removeExistingAlert();
    try {
      const info = await loadScalesList(selectedScaleId());
      if (info && info.selectedId) {
        await loadScale(info.selectedId);
      } else {
        renderRules([]);
      }
    } catch (e) {
      showAlert('Network error while loading pricing rules.', 'danger');
    } finally {
      setLoading(false);
    }
  }

  async function saveToBackend() {
    const id = selectedScaleId();
    if (!id) {
      showAlert('Select or create a pricing scale first.', 'warning');
      return;
    }
    setLoading(true);
    removeExistingAlert();
    try {
      const mode = currentMode();
      const rules = getRules();
      const clientErr = validateClientSide(mode, rules);
      if (clientErr) {
        showAlert(clientErr, 'warning');
        return;
      }

      const payload = { id, mode, rules };
      const { res, data } = await fetchJson(URL_SAVE, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!res.ok || !data.ok) {
        showAlert((data && data.error) || 'Failed to save pricing rules.', 'warning');
        return;
      }
      showAlert('Saved pricing rules.', 'success');
    } catch (e) {
      showAlert('Network error while saving pricing rules.', 'danger');
    } finally {
      setLoading(false);
    }
  }

  async function createNewScale() {
    let name = '';
    if (typeof Swal !== 'undefined') {
      const res = await Swal.fire({
        title: 'New pricing scale',
        input: 'text',
        inputLabel: 'Scale name',
        inputPlaceholder: 'e.g. Fleet, Wholesale, VIP',
        showCancelButton: true,
        confirmButtonText: 'Create',
        confirmButtonColor: '#1a7a42',
        cancelButtonColor: '#6c757d',
        inputValidator: (v) => (!v || !String(v).trim()) ? 'Name is required' : undefined,
      });
      if (!res.isConfirmed) return;
      name = String(res.value || '').trim();
    } else if (typeof appPrompt === 'function') {
      name = await appPrompt('New pricing scale name:', '');
    } else {
      name = window.prompt('New pricing scale name:', '');
    }
    name = (name || '').trim();
    if (!name) return;

    setLoading(true);
    removeExistingAlert();
    try {
      const mode = currentMode();
      const rules = getRules();
      const clientErr = validateClientSide(mode, rules);
      if (clientErr) {
        showAlert(clientErr, 'warning');
        return;
      }
      const payload = { name, mode, rules };
      const { res, data } = await fetchJson(URL_CREATE, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok || !data.ok) {
        showAlert((data && data.error) || 'Failed to create scale.', 'warning');
        return;
      }
      const newId = data.id;
      const info = await loadScalesList(newId);
      if (info && info.selectedId) await loadScale(info.selectedId);
      showAlert(`Created scale "${name}".`, 'success');
    } catch (e) {
      showAlert('Network error while creating scale.', 'danger');
    } finally {
      setLoading(false);
    }
  }

  async function deleteCurrentScale() {
    const id = selectedScaleId();
    if (!id) return;
    const opt = scaleSelect && scaleSelect.options[scaleSelect.selectedIndex];
    if (opt && opt.dataset.default === '1') {
      showAlert('Cannot delete the default scale.', 'warning');
      return;
    }
    const label = opt ? opt.textContent : 'this scale';
    let confirmed = false;
    if (typeof appConfirm === 'function') {
      confirmed = await appConfirm(`Delete pricing scale "${label}"? This cannot be undone.`);
    } else {
      confirmed = window.confirm(`Delete pricing scale "${label}"? This cannot be undone.`);
    }
    if (!confirmed) return;

    setLoading(true);
    removeExistingAlert();
    try {
      const { res, data } = await fetchJson(URL_DELETE(id), { method: 'POST' });
      if (!res.ok || !data.ok) {
        showAlert((data && data.error) || 'Failed to delete scale.', 'warning');
        return;
      }
      const info = await loadScalesList(null);
      if (info && info.selectedId) await loadScale(info.selectedId);
      showAlert('Scale deleted.', 'success');
    } catch (e) {
      showAlert('Network error while deleting scale.', 'danger');
    } finally {
      setLoading(false);
    }
  }

  async function setDefaultCurrent() {
    const id = selectedScaleId();
    if (!id) return;
    setLoading(true);
    removeExistingAlert();
    try {
      const { res, data } = await fetchJson(URL_SET_DEFAULT(id), { method: 'POST' });
      if (!res.ok || !data.ok) {
        showAlert((data && data.error) || 'Failed to set default.', 'warning');
        return;
      }
      await loadScalesList(id);
      showAlert('Default scale updated.', 'success');
    } catch (e) {
      showAlert('Network error while updating default.', 'danger');
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
      id: selectedScaleId(),
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
    applyModeConstraints();
    clearPreview();
  });
  modeMarkup.addEventListener('change', () => {
    updateValueLabel();
    applyModeConstraints();
    clearPreview();
  });

  if (saveBtn) saveBtn.addEventListener('click', () => saveToBackend());
  if (reloadBtn) reloadBtn.addEventListener('click', () => reloadFromBackend());
  if (createScaleBtn) createScaleBtn.addEventListener('click', () => createNewScale());
  if (setDefaultBtn) setDefaultBtn.addEventListener('click', () => setDefaultCurrent());
  if (deleteScaleBtn) deleteScaleBtn.addEventListener('click', () => deleteCurrentScale());
  if (scaleSelect) scaleSelect.addEventListener('change', () => loadScale(selectedScaleId()));

  // Init
  updateValueLabel();
  applyModeConstraints();

  // ── Sales Tax Rate ──
  var taxRateForm = document.getElementById("taxRateForm");
  var saveTaxRateBtn = document.getElementById("saveTaxRateBtn");
  var resetTaxRateBtn = document.getElementById("resetTaxRateBtn");
  var taxRateErrorEl = document.getElementById("taxRateError");

  if (taxRateForm && saveTaxRateBtn) {
    function setTaxRateError(message) {
      if (!taxRateErrorEl) return;
      if (!message) {
        taxRateErrorEl.textContent = "";
        taxRateErrorEl.classList.add("d-none");
        return;
      }
      taxRateErrorEl.textContent = message;
      taxRateErrorEl.classList.remove("d-none");
    }

    saveTaxRateBtn.addEventListener("click", function (event) {
      event.preventDefault();
      setTaxRateError("");

      var rateInput = taxRateForm.querySelector('input[name="custom_tax_rate"]');
      if (!rateInput) return;

      var rateStr = (rateInput.value || "").trim();
      if (!rateStr) {
        setTaxRateError("Please enter a tax rate.");
        return;
      }

      var rate = parseFloat(rateStr);
      if (isNaN(rate) || rate < 0 || rate > 100) {
        setTaxRateError("Tax rate must be between 0 and 100.");
        return;
      }

      taxRateForm.submit();
    });

    if (resetTaxRateBtn) {
      resetTaxRateBtn.addEventListener("click", async function (event) {
        event.preventDefault();

        if (typeof appConfirm === "function") {
          if (!await appConfirm("Reset tax rate to API-based lookup?")) return;
        } else {
          if (!confirm("Reset tax rate to API-based lookup?")) return;
        }

        var hiddenInput = document.createElement("input");
        hiddenInput.type = "hidden";
        hiddenInput.name = "reset_tax_rate";
        hiddenInput.value = "true";
        taxRateForm.appendChild(hiddenInput);

        taxRateForm.submit();
      });
    }
  }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initPartsSettingsPage, { once: true });
  } else {
    initPartsSettingsPage();
  }
})();
