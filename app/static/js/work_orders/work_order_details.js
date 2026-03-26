(function () {
  "use strict";

  let coreChargeDefaultEnabled = true;
  let currentSalesTaxRate = 0;
  let currentCustomerTaxable = false;
  const APP_TIMEZONE = document.body?.dataset?.appTimezone || "UTC";

  function $(id) { return document.getElementById(id); }

  function readJsonScript(id, fallback) {
    const el = $(id);
    if (!el) return fallback;
    try { return JSON.parse(el.textContent || "null") ?? fallback; }
    catch { return fallback; }
  }

  function toNum(v) {
    if (v === null || v === undefined) return null;
    const s = String(v).trim();
    if (!s) return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  }

  function parseMoneyText(v) {
    if (v === null || v === undefined) return 0;
    const s = String(v).replace(/[^0-9.-]/g, "").trim();
    const n = Number(s);
    return Number.isFinite(n) ? n : 0;
  }

  function money(n) { return Number.isFinite(n) ? n.toFixed(2) : ""; }
  function round2(n) { return Math.round(n * 100) / 100; }
  function escapeText(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function toast(msg, type) {
    if (!type) {
      var m = String(msg || '');
      if (/success|saved\.|added\.|unpaid\./i.test(m)) type = 'success';
      else if (/error|fail|network/i.test(m)) type = 'error';
      else if (/please|required|cannot|invalid/i.test(m)) type = 'warning';
      else type = 'info';
    }
    appAlert(msg, type);
  }

  function formatDateLabel(value) {
    if (!value) return "-";
    const raw = String(value).trim();
    const dateOnly = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (dateOnly) {
      return `${dateOnly[2]}/${dateOnly[3]}/${dateOnly[1]}`;
    }
    const dt = new Date(raw);
    if (Number.isNaN(dt.getTime())) return "-";
    return new Intl.DateTimeFormat("en-US", {
      timeZone: APP_TIMEZONE,
      month: "2-digit",
      day: "2-digit",
      year: "numeric",
    }).format(dt);
  }

  // ---------------- pricing ----------------
  function matchRule(cost, rules) {
    if (!Number.isFinite(cost) || !Array.isArray(rules)) return null;
    for (const r of rules) {
      const from = toNum(r.from);
      const to = (r.to === null || r.to === undefined) ? null : toNum(r.to);
      const vp = toNum(r.value_percent);
      if (from === null || vp === null) continue;
      if (cost < from) continue;
      if (to === null) return { value_percent: vp };
      if (cost <= to) return { value_percent: vp };
    }
    return null;
  }

  function calcPriceFromRule(cost, mode, valuePercent) {
    if (!Number.isFinite(cost) || cost <= 0) return null;
    if (!Number.isFinite(valuePercent)) return null;
    const vp = valuePercent / 100;

    if (mode === "markup") return round2(cost * (1 + vp));

    const denom = 1 - vp; // margin
    if (denom <= 0) return round2(cost);
    return round2(cost / denom);
  }

  function normalizeAssignedMechanics(raw) {
    if (!Array.isArray(raw)) return [];

    const out = [];
    const seen = new Set();
    raw.forEach((item) => {
      if (!item || typeof item !== "object") return;
      const userId = String(item.user_id || item.id || "").trim();
      if (!userId || seen.has(userId)) return;
      const percent = toNum(item.percent);
      out.push({
        user_id: userId,
        name: String(item.name || "").trim(),
        role: String(item.role || "").trim(),
        percent: Number.isFinite(percent) ? round2(percent) : 0,
      });
      seen.add(userId);
    });

    if (out.length === 1 && (!Number.isFinite(out[0].percent) || out[0].percent <= 0)) {
      out[0].percent = 100;
    }

    return out;
  }

  function getLaborAssignments(blockEl) {
    const input = blockEl?.querySelector(".labor-assignments-json");
    if (!input) return [];
    try {
      return normalizeAssignedMechanics(JSON.parse(input.value || "[]"));
    } catch {
      return [];
    }
  }

  function updateLaborAssignSummary(blockEl) {
    const summaryEl = blockEl?.querySelector(".laborAssignSummary");
    if (!summaryEl) return;
    const assignments = getLaborAssignments(blockEl);
    if (!assignments.length) {
      summaryEl.textContent = "Assigned: —";
      return;
    }

    const names = assignments
      .map((a) => {
        const name = String(a.name || "").trim() || "Mechanic";
        const pct = Number.isFinite(toNum(a.percent)) ? `${round2(toNum(a.percent))}%` : "0%";
        return `${name} (${pct})`;
      })
      .join(", ");
    summaryEl.textContent = `Assigned: ${names}`;
  }

  function setLaborAssignments(blockEl, assignments) {
    const input = blockEl?.querySelector(".labor-assignments-json");
    if (!input) return;
    const normalized = normalizeAssignedMechanics(assignments);
    input.value = JSON.stringify(normalized);
    updateLaborAssignSummary(blockEl);
  }

  // ---------------- labor ----------------
  function getHourlyRate(rates, code) {
    if (!Array.isArray(rates) || !code) return null;
    const found = rates.find(x => String(x.code) === String(code));
    if (!found) return null;
    return toNum(found.hourly_rate);
  }

  function getCustomerDefaultLaborRate(customers, customerId) {
    if (!Array.isArray(customers) || !customerId) return "";
    const found = customers.find(x => String(x.id || "") === String(customerId));
    if (!found) return "";
    return String(found.default_labor_rate || "").trim();
  }

  function getCustomerTaxable(customers, customerId) {
    if (!Array.isArray(customers) || !customerId) return false;
    const found = customers.find(x => String(x.id || "") === String(customerId));
    if (!found) return false;
    return !!found.taxable;
  }

  function normalizeRateKey(v) {
    const key = String(v || "")
      .trim()
      .toLowerCase()
      .replace(/[_\-\s]+/g, "");
    if (key === "standart") return "standard";
    return key;
  }

  function selectRateIfExists(selectEl, rateCode) {
    if (!selectEl || !rateCode) return false;
    const targetKey = normalizeRateKey(rateCode);
    const options = Array.from(selectEl.options || []);

    for (const opt of options) {
      if (!String(opt.value || "").trim()) continue;

      const optionCodeKey = normalizeRateKey(opt.value);
      const optionName = String(opt.textContent || "").split("(")[0].trim();
      const optionNameKey = normalizeRateKey(optionName);

      if (optionCodeKey === targetKey || optionNameKey === targetKey) {
        selectEl.value = String(opt.value);
        return true;
      }
    }

    return false;
  }

  function applyDefaultLaborRateToAll(blocksContainer, rateCode, onlyIfEmpty) {
    if (!blocksContainer || !rateCode) return;
    const selects = Array.from(blocksContainer.querySelectorAll(".wo-labor .labor-rate"));
    selects.forEach((sel) => {
      const current = String(sel.value || "").trim();
      if (onlyIfEmpty && current) return;
      selectRateIfExists(sel, rateCode);
    });
  }

  function applyDefaultLaborRateToBlock(blockEl, rateCode, onlyIfEmpty) {
    if (!blockEl || !rateCode) return;
    const sel = blockEl.querySelector(".labor-rate");
    if (!sel) return;
    const current = String(sel.value || "").trim();
    if (onlyIfEmpty && current) return;
    selectRateIfExists(sel, rateCode);
  }

  function calcLaborTotal(blockEl, laborRates) {
    const hours = toNum(blockEl.querySelector(".labor-hours")?.value);
    const code = String(blockEl.querySelector(".labor-rate")?.value || "").trim();
    if (hours === null || !code) return null;

    const hr = getHourlyRate(laborRates, code);
    if (hr === null) return null;

    return round2(hours * hr);
  }

  function setLaborTotalInput(blockEl, value) {
    const input = blockEl.querySelector(".labor-total-input");
    if (!input) return;
    const active = document.activeElement;
    const isEditing = active && active.classList?.contains("labor-total-input") && blockEl.contains(active);
    if (isEditing) return;
    input.value = Number.isFinite(value) ? money(value) : "";
  }

  function getLaborRateValue(blockEl, laborRates) {
    const code = String(blockEl.querySelector(".labor-rate")?.value || "").trim();
    if (!code) return null;
    return getHourlyRate(laborRates, code);
  }

  function syncLaborTotalFromHours(blockEl, laborRates) {
    const total = calcLaborTotal(blockEl, laborRates);
    setLaborTotalInput(blockEl, total);
  }

  function syncHoursFromLaborTotal(blockEl, laborRates) {
    const input = blockEl.querySelector(".labor-total-input");
    const hoursInput = blockEl.querySelector(".labor-hours");
    if (!input || !hoursInput) return;

    const total = toNum(input.value);
    const rate = getLaborRateValue(blockEl, laborRates);

    if (total === null || rate === null || rate <= 0) {
      if (total === null) {
        hoursInput.value = "";
      }
      return;
    }

    const hours = round2(total / rate);
    hoursInput.value = Number.isFinite(hours) ? String(hours) : "";
  }

  // ---------------- parts rows ----------------
  function makePartsRow(laborIndex, rowIndex) {
    const tr = document.createElement("tr");
    tr.className = "parts-row";
    tr.dataset.index = String(rowIndex);

    tr.innerHTML = `
      <td class="p-1 align-middle">
        <div class="p-1">
        <input class="form-control form-control-sm part-number" name="labors[${laborIndex}][parts][${rowIndex}][part_number]" maxlength="64" autocomplete="off" style="width: 92%;">
        <input type="hidden" class="part-id" name="labors[${laborIndex}][parts][${rowIndex}][part_id]" value="">
        <input type="hidden" class="part-one-time" name="labors[${laborIndex}][parts][${rowIndex}][one_time_part]" value="0">
        <input type="hidden" class="part-core-charge" name="labors[${laborIndex}][parts][${rowIndex}][core_charge]" value="0">
        <input type="hidden" class="part-misc-charge" name="labors[${laborIndex}][parts][${rowIndex}][misc_charge]" value="0">
        <input type="hidden" class="part-misc-charge-description" name="labors[${laborIndex}][parts][${rowIndex}][misc_charge_description]" value="">
        </div>
      </td>
      <td class="p-1 align-middle"><div class="p-1"><input class="form-control form-control-sm part-description" name="labors[${laborIndex}][parts][${rowIndex}][description]" maxlength="200" autocomplete="off" style="width: 96%;"></div></td>
      <td class="p-1 align-middle"><div class="p-1"><input class="form-control form-control-sm part-qty" name="labors[${laborIndex}][parts][${rowIndex}][qty]" inputmode="numeric" style="width: 86%;"></div></td>
      <td class="p-1 align-middle">
        <div class="p-1">
          <input class="form-control form-control-sm part-cost" name="labors[${laborIndex}][parts][${rowIndex}][cost]" inputmode="decimal" readonly tabindex="-1" style="width: 88%;">
          <div class="small text-muted mt-1 part-charges-meta"></div>
        </div>
      </td>
      <td class="p-1 align-middle"><div class="p-1"><input class="form-control form-control-sm part-price" name="labors[${laborIndex}][parts][${rowIndex}][price]" value="" inputmode="decimal" style="width: 88%;"></div></td>
      <td class="part-line-total p-1 align-middle">
        <div class="d-flex align-items-center gap-2 p-1">
          <div class="part-line-total-value"><span class="text-muted">—</span></div>
          <div class="form-check form-switch part-core-toggle-wrapper" style="display: none; margin: 0;">
            <input class="form-check-input part-core-toggle" type="checkbox">
            <label class="form-check-label small text-muted" style="margin-bottom: 0;">Core</label>
          </div>
        </div>
      </td>
      <td class="text-center p-1 align-middle">
        <div class="p-1">
          <button type="button" class="btn btn-sm btn-outline-danger delete-part-row" title="Delete this part">×</button>
        </div>
      </td>
    `;
    return tr;
  }

  function rowHasAnyInput(tr) {
    const pn = tr.querySelector(".part-number")?.value || "";
    const ds = tr.querySelector(".part-description")?.value || "";
    const q = tr.querySelector(".part-qty")?.value || "";
    const c = tr.querySelector(".part-cost")?.value || "";
    const p = tr.querySelector(".part-price")?.value || "";
    const core = toNum(tr.querySelector(".part-core-charge")?.value);
    const misc = toNum(tr.querySelector(".part-misc-charge")?.value);
    const miscDesc = tr.querySelector(".part-misc-charge-description")?.value || "";
    const hasCharges = (Number.isFinite(core) && core > 0) || (Number.isFinite(misc) && misc > 0);
    return !!(String(pn).trim() || String(ds).trim() || String(q).trim() || String(c).trim() || String(p).trim() || hasCharges || String(miscDesc).trim());
  }

  function isOneTimePartRow(tr) {
    const input = tr.querySelector(".part-one-time");
    const raw = String(input?.value || "").trim().toLowerCase();
    return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
  }

  function setOneTimePartRow(tr, enabled) {
    const input = tr.querySelector(".part-one-time");
    if (!input) return;
    input.value = enabled ? "1" : "0";
  }

  function updatePartCostEditableState(tr) {
    const cost = tr.querySelector(".part-cost");
    if (!cost) return;
    const editable = isOneTimePartRow(tr);
    cost.readOnly = !editable;
    if (editable) {
      cost.removeAttribute("tabindex");
    } else {
      cost.setAttribute("tabindex", "-1");
    }
  }

  function getRowMiscItems(tr) {
    const raw = String(tr.querySelector(".part-misc-charge-description")?.value || "").trim();
    if (!raw) return [];

    try {
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed
        .map((x) => ({
          description: String(x?.description || "").trim(),
          quantity: Number(x?.quantity || 1),
          price: toNum(x?.price),
          manual: x?.manual === true,
        }))
        .filter((x) => x.description);
    } catch {
      return raw
        .split("|")
        .map((x) => ({ description: String(x || "").trim(), quantity: 1, price: null, manual: false }))
        .filter((x) => x.description);
    }
  }

  function isCoreChargeEnabled(tr) {
    const toggle = tr.querySelector(".part-core-toggle");
    if (!toggle) return true;
    return !!toggle.checked;
  }

  function getRowCharges(tr) {
    const coreRaw = toNum(tr.querySelector(".part-core-charge")?.value);
    const miscRaw = toNum(tr.querySelector(".part-misc-charge")?.value);

    const coreCharge = (isCoreChargeEnabled(tr) && Number.isFinite(coreRaw) && coreRaw > 0)
      ? round2(coreRaw)
      : 0;
    const miscCharge = Number.isFinite(miscRaw) && miscRaw > 0 ? round2(miscRaw) : 0;
    return { coreCharge, miscCharge };
  }

  function setRowChargesMeta(tr) {
    const metaEl = tr.querySelector(".part-charges-meta");
    if (metaEl) metaEl.textContent = "";
  }

  function syncCoreChargeFromToggle(tr) {
    const toggle = tr.querySelector(".part-core-toggle");
    const coreInput = tr.querySelector(".part-core-charge");
    if (!toggle || !coreInput) return;

    const current = toNum(coreInput.value);
    const base = toNum(tr.dataset.coreChargeBase);

    if (toggle.checked) {
      const restored = Number.isFinite(base) ? base : (Number.isFinite(current) ? current : 0);
      coreInput.value = String(restored || 0);
      if (!Number.isFinite(base) && restored > 0) tr.dataset.coreChargeBase = String(restored);
    } else {
      if (Number.isFinite(current) && current > 0) tr.dataset.coreChargeBase = String(current);
      coreInput.value = "0";
    }
  }

  function clearRowCalc(tr) {
    const lineCell = tr.querySelector(".part-line-total");
    if (lineCell) {
      const valueEl = lineCell.querySelector(".part-line-total-value");
      if (valueEl) valueEl.innerHTML = `<span class="text-muted">—</span>`;
      const toggleWrapper = lineCell.querySelector(".part-core-toggle-wrapper");
      if (toggleWrapper) toggleWrapper.style.display = "none";
    }
    setRowChargesMeta(tr);
  }

  function calcRowLineTotal(tr, pricing) {
    const qty = toNum(tr.querySelector(".part-qty")?.value);
    const cost = toNum(tr.querySelector(".part-cost")?.value);
    const priceInput = tr.querySelector(".part-price");
    const lineCell = tr.querySelector(".part-line-total");
    const { coreCharge, miscCharge } = getRowCharges(tr);
    setRowChargesMeta(tr);

    let price = toNum(priceInput?.value);

    if (price === null && Number.isFinite(cost) && cost >= 0 && !tr.dataset.priceAutofilled && !tr.dataset.priceManuallyEdited) {
      if (pricing && Array.isArray(pricing.rules) && pricing.rules.length > 0) {
        const rule = matchRule(cost, pricing.rules);
        if (rule) {
          const autoPrice = calcPriceFromRule(cost, pricing.mode, rule.value_percent);
          if (autoPrice !== null && priceInput) {
            priceInput.value = money(autoPrice);
            tr.dataset.priceAutofilled = "1";
            price = autoPrice;
          }
        }
      }
    }

    if (qty === null || qty <= 0 || price === null || price < 0) {
      clearRowCalc(tr);
      return null;
    }

    const unitTotal = round2(price + coreCharge);
    const lt = round2(unitTotal * qty);
    if (lineCell) {
      const valueEl = lineCell.querySelector(".part-line-total-value");
      if (valueEl) {
        const coreLine = coreCharge > 0 ? round2(coreCharge * qty) : 0;
        const coreMeta = coreLine > 0 ? `<div class="small text-muted">+ $${money(coreLine)} core charge</div>` : "";
        valueEl.innerHTML = `<strong>$${money(lt)}</strong>${coreMeta}`;
      }
      const toggleWrapper = lineCell.querySelector(".part-core-toggle-wrapper");
      const baseCoreCharge = toNum(tr.dataset.coreChargeBase);
      if (toggleWrapper) {
        if (Number.isFinite(baseCoreCharge) && baseCoreCharge > 0) {
          toggleWrapper.style.display = "flex";
        } else {
          toggleWrapper.style.display = "none";
        }
      }
    }
    return lt;
  }

  function ensureTrailingEmptyRow(blockEl) {
    const tbody = blockEl.querySelector(".partsTbody");
    if (!tbody) return;

    const rows = Array.from(tbody.querySelectorAll("tr.parts-row"));
    if (rows.length === 0) {
      tbody.appendChild(makePartsRow(Number(blockEl.dataset.laborIndex), 0));
      return;
    }

    const last = rows[rows.length - 1];
    if (rowHasAnyInput(last)) {
      tbody.appendChild(makePartsRow(Number(blockEl.dataset.laborIndex), rows.length));
    }
  }

  function calcPartsTotal(blockEl, pricing) {
    const tbody = blockEl.querySelector(".partsTbody");
    if (!tbody) {
      return {
        partsTotal: null,
        coreTotal: 0,
        miscTotal: 0,
      };
    }

    let partsBaseTotal = 0;
    let coreTotal = 0;
    let miscTotal = 0;
    const miscBreakdownMap = new Map();
    const rows = Array.from(tbody.querySelectorAll("tr.parts-row"));
    const rowQtyByIndex = new Map();

    for (const tr of rows) {
      if (!rowHasAnyInput(tr)) {
        clearRowCalc(tr);
        continue;
      }
      const qty = toNum(tr.querySelector(".part-qty")?.value);
      calcRowLineTotal(tr, pricing);

      const rowIndex = Number(tr.dataset.index);
      if (Number.isFinite(rowIndex) && Number.isFinite(qty) && qty > 0) {
        rowQtyByIndex.set(rowIndex, round2(qty));
      }

      const rowPrice = toNum(tr.querySelector(".part-price")?.value);
      if (qty !== null && qty > 0 && Number.isFinite(rowPrice) && rowPrice >= 0) {
        partsBaseTotal += round2(rowPrice * qty);
      }

      const { coreCharge, miscCharge } = getRowCharges(tr);
      if (qty !== null && qty > 0) {
        coreTotal += round2(coreCharge * qty);
        miscTotal += round2(miscCharge * qty);
      }
    }

    // Misc charges are edited in a dedicated table and persisted in the first row JSON.
    // Rebuild totals from that JSON so misc remains correct regardless of where part rows move.
    const firstRow = rows[0] || null;
    const firstRowItems = firstRow ? getMiscItemsArray(firstRow) : [];
    if (firstRowItems.length > 0) {
      for (const rawItem of firstRowItems) {
        const unitPrice = toNum(rawItem?.price);
        if (!Number.isFinite(unitPrice) || unitPrice <= 0) continue;

        const description = String(rawItem?.description || "").trim() || "Misc charge";
        const itemQuantity = Number(rawItem?.quantity || 1);
        const isManual = rawItem?.manual === true;
        const partIndex = Number(rawItem?.partIndex);

        let effectiveQty = 0;
        if (isManual) {
          effectiveQty = Number.isFinite(itemQuantity) ? itemQuantity : 0;
        } else if (Number.isFinite(partIndex) && rowQtyByIndex.has(partIndex)) {
          // For auto items linked to part rows, quantity in JSON is already synchronized by qty handlers.
          effectiveQty = Number.isFinite(itemQuantity) ? itemQuantity : 0;
        } else {
          // Legacy/unknown format: use stored quantity if present.
          effectiveQty = Number.isFinite(itemQuantity) ? itemQuantity : 0;
        }

        if (!Number.isFinite(effectiveQty) || effectiveQty <= 0) continue;

        const roundedUnitPrice = round2(unitPrice);
        const key = `${description}__${roundedUnitPrice}`;
        const prev = miscBreakdownMap.get(key) || {
          description,
          unitPrice: roundedUnitPrice,
          count: 0,
          amount: 0,
        };
        prev.count = round2(prev.count + effectiveQty);
        prev.amount = round2(prev.amount + (roundedUnitPrice * effectiveQty));
        miscBreakdownMap.set(key, prev);
      }
    } else {
      // Backward compatibility: older rows may still store misc JSON in each part row.
      for (const tr of rows) {
        const qty = toNum(tr.querySelector(".part-qty")?.value);
        const miscItems = getRowMiscItems(tr);
        for (const item of miscItems) {
          const unitPrice = toNum(item.price);
          if (!Number.isFinite(unitPrice) || unitPrice <= 0) continue;

          const description = String(item.description || "").trim() || "Misc charge";
          const itemQuantity = Number(item.quantity || 1);
          const effectiveQty = item.manual === true
            ? itemQuantity
            : (Number.isFinite(qty) && qty > 0 ? qty : 0);
          if (!Number.isFinite(effectiveQty) || effectiveQty <= 0) continue;

          const roundedUnitPrice = round2(unitPrice);
          const key = `${description}__${roundedUnitPrice}`;
          const prev = miscBreakdownMap.get(key) || {
            description,
            unitPrice: roundedUnitPrice,
            count: 0,
            amount: 0,
          };
          prev.count = round2(prev.count + effectiveQty);
          prev.amount = round2(prev.amount + (roundedUnitPrice * effectiveQty));
          miscBreakdownMap.set(key, prev);
        }
      }
    }

    partsBaseTotal = round2(partsBaseTotal);
    coreTotal = round2(coreTotal);
    
    // Calculate misc total from breakdown (includes both automatic and manual charges)
    let miscTotalFromBreakdown = 0;
    for (const row of miscBreakdownMap.values()) {
      if (Number.isFinite(row.amount)) {
        miscTotalFromBreakdown += row.amount;
      }
    }
    miscTotal = round2(miscTotalFromBreakdown);

    // Calculate taxable portion of misc separately (for sales tax base)
    let miscTaxableTotal = 0;
    for (const rawItem of (firstRow ? getMiscItemsArray(firstRow) : [])) {
      if (rawItem?.taxable === false) continue;
      const unitPrice = toNum(rawItem?.price);
      if (!Number.isFinite(unitPrice) || unitPrice <= 0) continue;
      const itemQty = Number(rawItem?.quantity || 1);
      if (!Number.isFinite(itemQty) || itemQty <= 0) continue;
      miscTaxableTotal += round2(unitPrice * itemQty);
    }
    miscTaxableTotal = round2(miscTaxableTotal);

    return {
      partsTotal: partsBaseTotal > 0 ? partsBaseTotal : null,
      coreTotal,
      miscTotal,
      miscTaxableTotal,
      miscBreakdown: Array.from(miscBreakdownMap.values())
        .map((row) => ({
          description: String(row.description || "").trim() || "Misc charge",
          unitPrice: Number.isFinite(row.unitPrice) ? round2(row.unitPrice) : 0,
          count: Number.isFinite(row.count) ? round2(row.count) : 0,
          amount: Number.isFinite(row.amount) ? round2(row.amount) : 0,
        }))
        .sort((a, b) => {
          const byDescription = a.description.localeCompare(b.description);
          if (byDescription !== 0) return byDescription;
          return a.unitPrice - b.unitPrice;
        }),
    };
  }

  // ---------------- totals per block + grand ----------------
  function setBlockTotalsUI(blockEl, laborTotal, partsTotal, coreTotal, miscTotal, shopSupplyTotal, miscBreakdown) {
    const laborEl = blockEl.querySelector(".laborTotalDisplay");
    const partsEl = blockEl.querySelector(".partsTotalDisplay");
    const coreWrap = blockEl.querySelector(".coreTotalWrap");
    const coreEl = blockEl.querySelector(".coreTotalDisplay");
    const miscWrap = blockEl.querySelector(".miscTotalWrap");
    const miscEl = blockEl.querySelector(".miscTotalDisplay");
    const supplyWrap = blockEl.querySelector(".shopSupplyTotalWrap");
    const supplyEl = blockEl.querySelector(".shopSupplyTotalDisplay");
    const blockElTotal = blockEl.querySelector(".laborFullTotalDisplay");

    if (laborEl) laborEl.textContent = Number.isFinite(laborTotal) ? `$${money(laborTotal)}` : "—";
    setLaborTotalInput(blockEl, Number.isFinite(laborTotal) ? laborTotal : null);
    if (partsEl) partsEl.textContent = Number.isFinite(partsTotal) ? `$${money(partsTotal)}` : "—";
    const hasCore = Number.isFinite(coreTotal) && coreTotal > 0;
    const hasMisc = Number.isFinite(miscTotal) && miscTotal > 0;
    if (coreWrap) coreWrap.style.display = hasCore ? "" : "none";
    if (miscWrap) miscWrap.style.display = hasMisc ? "" : "none";
    if (supplyWrap) supplyWrap.style.display = "none";
    if (coreEl) coreEl.textContent = hasCore ? `$${money(coreTotal)}` : "—";
    if (miscEl) miscEl.textContent = hasMisc ? `$${money(miscTotal)}` : "—";
    if (supplyEl) supplyEl.textContent = "—";

    const laborBlockTotal =
      (Number.isFinite(laborTotal) ? laborTotal : 0)
      + (Number.isFinite(partsTotal) ? partsTotal : 0)
      + (Number.isFinite(coreTotal) ? coreTotal : 0)
      + (Number.isFinite(miscTotal) ? miscTotal : 0);
    if (blockElTotal) {
      blockElTotal.textContent = (Number.isFinite(laborTotal) || Number.isFinite(partsTotal) || Number.isFinite(coreTotal) || Number.isFinite(miscTotal))
        ? `$${money(round2(laborBlockTotal))}`
        : "—";
    }
  }

  function recalcBlock(blockEl, pricing, laborRates, shopSupplyPct) {
    ensureTrailingEmptyRow(blockEl);
    const laborTotal = calcLaborTotal(blockEl, laborRates);
    const partsTotals = calcPartsTotal(blockEl, pricing);
    const partsTotal = partsTotals.partsTotal;
    const coreTotal = partsTotals.coreTotal;
    const miscTotal = partsTotals.miscTotal;
    const miscTaxableTotal = partsTotals.miscTaxableTotal || 0;
    const miscBreakdown = partsTotals.miscBreakdown;
    const supplyTotal = 0;
    setBlockTotalsUI(blockEl, laborTotal, partsTotal, coreTotal, miscTotal, supplyTotal, miscBreakdown);
    const labor = Number.isFinite(laborTotal) ? laborTotal : 0;
    const parts = Number.isFinite(partsTotal) ? partsTotal : 0;
    const core = Number.isFinite(coreTotal) ? coreTotal : 0;
    const misc = Number.isFinite(miscTotal) ? miscTotal : 0;
    const supply = Number.isFinite(supplyTotal) ? supplyTotal : 0;
    return {
      labor,
      parts,
      core,
      misc,
      miscTaxable: round2(miscTaxableTotal),
      supply,
      total: round2(labor + parts + core + misc),
    };
  }

  function recalcAll(blocksContainer, pricing, laborRates, shopSupplyPct) {
    const blocks = Array.from(blocksContainer.querySelectorAll(".wo-labor"));
    let laborGrand = 0;
    let partsGrand = 0;
    let coreGrand = 0;
    let miscGrand = 0;
    let miscTaxableGrand = 0;
    let supplyGrand = 0;
    let grand = 0;
    for (const b of blocks) {
      const totals = recalcBlock(b, pricing, laborRates, shopSupplyPct);
      laborGrand += totals.labor;
      partsGrand += totals.parts;
      coreGrand += totals.core;
      miscGrand += totals.misc;
      miscTaxableGrand += totals.miscTaxable || 0;
      grand += totals.total;
    }
    laborGrand = round2(laborGrand);
    partsGrand = round2(partsGrand);
    coreGrand = round2(coreGrand);
    miscGrand = round2(miscGrand);
    miscTaxableGrand = round2(miscTaxableGrand);
    supplyGrand = (Number.isFinite(shopSupplyPct) && shopSupplyPct > 0)
      ? round2(laborGrand * (shopSupplyPct / 100))
      : 0;
    const salesTaxGrand = (currentCustomerTaxable && Number.isFinite(currentSalesTaxRate) && currentSalesTaxRate > 0)
      ? round2((partsGrand + miscTaxableGrand) * currentSalesTaxRate)
      : 0;
    const partsGrandTotal = round2(partsGrand + coreGrand + miscGrand);
    const laborGrandTotal = round2(laborGrand + supplyGrand);
    grand = round2(grand + supplyGrand + salesTaxGrand);
    supplyGrand = round2(supplyGrand);
    grand = round2(grand);

    const laborGrandEl = $("laborGrandTotalDisplay");
    if (laborGrandEl) laborGrandEl.textContent = blocks.length ? `$${money(laborGrandTotal)}` : "—";
    const laborGrandBaseEl = $("laborGrandBaseDisplay");
    if (laborGrandBaseEl) laborGrandBaseEl.textContent = blocks.length ? `$${money(laborGrand)}` : "—";

    const partsGrandEl = $("partsGrandTotalDisplay");
    if (partsGrandEl) partsGrandEl.textContent = blocks.length ? `$${money(partsGrandTotal)}` : "—";
    const partsGrandBaseEl = $("partsGrandBaseDisplay");
    if (partsGrandBaseEl) partsGrandBaseEl.textContent = blocks.length ? `$${money(partsGrand)}` : "—";

    const salesTaxRateEl = $("salesTaxRateLabel");
    if (salesTaxRateEl) salesTaxRateEl.textContent = `${(round2((currentSalesTaxRate || 0) * 10000) / 100).toFixed(2)}%`;
    const salesTaxEl = $("salesTaxGrandTotalDisplay");
    if (salesTaxEl) salesTaxEl.textContent = blocks.length ? `$${money(salesTaxGrand)}` : "—";

    const coreGrandWrap = $("coreGrandTotalWrap");
    const coreGrandEl = $("coreGrandTotalDisplay");
    const hasCoreGrand = coreGrand > 0;
    if (coreGrandWrap) coreGrandWrap.style.display = hasCoreGrand ? "" : "none";
    if (coreGrandEl) coreGrandEl.textContent = hasCoreGrand ? `$${money(coreGrand)}` : "—";

    const miscGrandWrap = $("miscGrandTotalWrap");
    const miscGrandEl = $("miscGrandTotalDisplay");
    const hasMiscGrand = miscGrand > 0;
    if (miscGrandWrap) miscGrandWrap.style.display = hasMiscGrand ? "" : "none";
    if (miscGrandEl) miscGrandEl.textContent = hasMiscGrand ? `$${money(miscGrand)}` : "—";

    const supplyGrandWrap = $("shopSupplyGrandTotalWrap");
    const supplyGrandEl = $("shopSupplyGrandTotalDisplay");
    const hasSupplyGrand = supplyGrand > 0;
    if (supplyGrandWrap) supplyGrandWrap.style.display = hasSupplyGrand ? "" : "none";
    if (supplyGrandEl) supplyGrandEl.textContent = hasSupplyGrand ? `$${money(supplyGrand)}` : "—";

    const grandEl = $("grandTotalDisplay");
    if (grandEl) grandEl.textContent = blocks.length ? `$${money(grand)}` : "—";

    const cnt = $("laborCount");
    if (cnt) cnt.textContent = String(blocks.length);

    blocks.forEach((b) => {
      const btn = b.querySelector(".removeLaborBtn");
      if (!btn) return;
      btn.disabled = blocks.length <= 1;
    });
  }

  // ---------------- totals serialization (FRONT -> BACK) ----------------
  function serializeTotals(blocksContainer) {
    const blocks = Array.from(blocksContainer.querySelectorAll(".wo-labor"));
    const outBlocks = [];

    let laborBaseSum = 0;
    let partsBaseSum = 0;
    let coreSum = 0;
    let miscSum = 0;
    let supplySum = 0;
    let grandSum = 0;

    blocks.forEach((bEl) => {
      const laborText = bEl.querySelector(".laborTotalDisplay")?.textContent || "0";
      const partsText = bEl.querySelector(".partsTotalDisplay")?.textContent || "0";
      const blockText = bEl.querySelector(".laborFullTotalDisplay")?.textContent || "0";
      const coreText = bEl.querySelector(".coreTotalDisplay")?.textContent || "0";
      const miscText = bEl.querySelector(".miscTotalDisplay")?.textContent || "0";

      const laborBase = round2(parseMoneyText(laborText));
      const partsBase = round2(parseMoneyText(partsText));
      const coreTotal = round2(parseMoneyText(coreText));
      const miscTotal = round2(parseMoneyText(miscText));
      const supplyTotal = 0;
      const blockTotal = round2(parseMoneyText(blockText));
      const laborTotal = round2(laborBase + supplyTotal);
      const partsTotal = round2(partsBase + coreTotal + miscTotal);

      laborBaseSum += laborBase;
      partsBaseSum += partsBase;
      coreSum += coreTotal;
      miscSum += miscTotal;
      supplySum += supplyTotal;
      grandSum += blockTotal;

      outBlocks.push({
        labor: laborBase,
        labor_total: laborTotal,
        parts: partsBase,
        parts_total: partsTotal,
        core_total: coreTotal,
        misc_total: miscTotal,
        cost_total: partsBase,
        shop_supply_total: supplyTotal,
        labor_full_total: blockTotal,
      });
    });

    const supplyGrandText = $("shopSupplyGrandTotalDisplay")?.textContent || "0";
    supplySum = round2(parseMoneyText(supplyGrandText));

    // Compute misc taxable total by scanning all blocks' misc items
    let miscTaxableSum = 0;
    blocks.forEach((bEl) => {
      const tbody = bEl.querySelector(".partsTbody");
      if (!tbody) return;
      const firstRow = tbody.querySelector("tr.parts-row");
      if (!firstRow) return;
      const items = getMiscItemsArray(firstRow);
      for (const item of items) {
        if (item?.taxable === false) continue;
        const unitPrice = toNum(item?.price);
        const itemQty = Number(item?.quantity || 1);
        if (Number.isFinite(unitPrice) && unitPrice > 0 && Number.isFinite(itemQty) && itemQty > 0) {
          miscTaxableSum += round2(unitPrice * itemQty);
        }
      }
    });
    miscTaxableSum = round2(miscTaxableSum);

    // grand_total берём из UI, но если там "—" / пусто — пересчитаем из блоков
    const grandText = $("grandTotalDisplay")?.textContent || "";
    const grandUi = round2(parseMoneyText(grandText));
    const grandFinal = grandUi > 0 ? grandUi : round2(grandSum + supplySum);

    const laborTotalSum = round2(laborBaseSum + supplySum);
    const partsTotalSum = round2(partsBaseSum + coreSum + miscSum);
    const salesTaxSum = (currentCustomerTaxable && Number.isFinite(currentSalesTaxRate) && currentSalesTaxRate > 0)
      ? round2((partsBaseSum + miscTaxableSum) * currentSalesTaxRate)
      : 0;

    return {
      labor: round2(laborBaseSum),
      labor_total: laborTotalSum,
      parts: round2(partsBaseSum),
      parts_total: partsTotalSum,
      core_total: round2(coreSum),
      misc_total: round2(miscSum),
      misc_taxable_total: miscTaxableSum,
      cost_total: round2(partsBaseSum),
      shop_supply_total: round2(supplySum),
      parts_taxable_total: round2(partsBaseSum + miscTaxableSum),
      sales_tax_rate: Number.isFinite(currentSalesTaxRate) ? Number(currentSalesTaxRate.toFixed(6)) : 0,
      sales_tax_total: salesTaxSum,
      is_taxable: !!currentCustomerTaxable,
      grand_total: round2(grandFinal > 0 ? grandFinal : (laborTotalSum + partsTotalSum + salesTaxSum)),
      labors: outBlocks,
    };
  }

  function applyTotalsSnapshotToUi(blocksContainer, totals, shopSupplyPct) {
    if (!totals || typeof totals !== "object" || !blocksContainer) return;

    const blockTotals = Array.isArray(totals.labors) ? totals.labors : [];
    const blocks = Array.from(blocksContainer.querySelectorAll(".wo-labor"));

    blocks.forEach((bEl, idx) => {
      const bt = blockTotals[idx] || {};
      const laborStored = toNum(bt.labor_total);
      const partsStored = toNum(bt.parts_total);
      const labor = toNum(bt.labor);
      const parts = toNum(bt.parts);
      const core = toNum(bt.core_total);
      const misc = toNum(bt.misc_total);
      const supply = toNum(bt.shop_supply_total);

      const laborBase = Number.isFinite(labor)
        ? labor
        : (Number.isFinite(laborStored) ? round2(laborStored - (Number.isFinite(supply) ? supply : 0)) : null);
      const partsBase = Number.isFinite(parts)
        ? parts
        : (Number.isFinite(partsStored)
          ? round2(partsStored - (Number.isFinite(core) ? core : 0) - (Number.isFinite(misc) ? misc : 0))
          : null);

      if (laborBase === null && partsBase === null && core === null && misc === null) return;
      setBlockTotalsUI(
        bEl,
        Number.isFinite(laborBase) ? round2(laborBase) : null,
        Number.isFinite(partsBase) ? round2(partsBase) : null,
        Number.isFinite(core) ? round2(core) : 0,
        Number.isFinite(misc) ? round2(misc) : 0,
        0,
        [],
      );
    });

    const laborGrandStored = toNum(totals.labor_total);
    const partsGrandStored = toNum(totals.parts_total);
    const laborGrandBaseStored = toNum(totals.labor);
    const partsGrandBaseStored = toNum(totals.parts);
    const coreGrand = toNum(totals.core_total);
    const miscGrand = toNum(totals.misc_total);
    const salesTaxGrand = toNum(totals.sales_tax_total);
    const salesTaxRate = toNum(totals.sales_tax_rate);
    const grand = toNum(totals.grand_total);
    const supplyGrandStored = toNum(totals.shop_supply_total);
    const supplyGrandCalculated = (
      Number.isFinite(laborGrandBaseStored) && Number.isFinite(shopSupplyPct) && shopSupplyPct > 0
    )
      ? round2(laborGrandBaseStored * (shopSupplyPct / 100))
      : 0;
    const supplyGrand = (Number.isFinite(supplyGrandStored) && supplyGrandStored > 0)
      ? round2(supplyGrandStored)
      : supplyGrandCalculated;

    const laborGrandBase = Number.isFinite(laborGrandBaseStored)
      ? round2(laborGrandBaseStored)
      : (Number.isFinite(laborGrandStored) ? round2(laborGrandStored - supplyGrand) : null);

    const partsGrandBase = Number.isFinite(partsGrandBaseStored)
      ? round2(partsGrandBaseStored)
      : (Number.isFinite(partsGrandStored)
        ? round2(partsGrandStored - (Number.isFinite(coreGrand) ? coreGrand : 0) - (Number.isFinite(miscGrand) ? miscGrand : 0))
        : null);

    const laborGrandTotal = Number.isFinite(laborGrandBase) ? round2(laborGrandBase + supplyGrand) : null;
    const partsGrandTotal = (Number.isFinite(partsGrandBase) || Number.isFinite(coreGrand) || Number.isFinite(miscGrand))
      ? round2((Number.isFinite(partsGrandBase) ? partsGrandBase : 0) + (Number.isFinite(coreGrand) ? coreGrand : 0) + (Number.isFinite(miscGrand) ? miscGrand : 0))
      : null;
    const calculatedGrand = (Number.isFinite(laborGrandTotal) && Number.isFinite(partsGrandTotal))
      ? round2(laborGrandTotal + partsGrandTotal + (Number.isFinite(salesTaxGrand) ? salesTaxGrand : 0))
      : null;

    const laborGrandEl = $("laborGrandTotalDisplay");
    if (laborGrandEl && Number.isFinite(laborGrandTotal)) laborGrandEl.textContent = `$${money(round2(laborGrandTotal))}`;
    const laborGrandBaseEl = $("laborGrandBaseDisplay");
    if (laborGrandBaseEl && Number.isFinite(laborGrandBase)) laborGrandBaseEl.textContent = `$${money(round2(laborGrandBase))}`;

    const partsGrandEl = $("partsGrandTotalDisplay");
    if (partsGrandEl && Number.isFinite(partsGrandTotal)) partsGrandEl.textContent = `$${money(round2(partsGrandTotal))}`;
    const partsGrandBaseEl = $("partsGrandBaseDisplay");
    if (partsGrandBaseEl && Number.isFinite(partsGrandBase)) partsGrandBaseEl.textContent = `$${money(round2(partsGrandBase))}`;

    const salesTaxRateEl = $("salesTaxRateLabel");
    const finalRate = Number.isFinite(salesTaxRate) ? salesTaxRate : (Number.isFinite(currentSalesTaxRate) ? currentSalesTaxRate : 0);
    if (salesTaxRateEl) salesTaxRateEl.textContent = `${(round2(finalRate * 10000) / 100).toFixed(2)}%`;
    const salesTaxEl = $("salesTaxGrandTotalDisplay");
    if (salesTaxEl) {
      const finalTax = Number.isFinite(salesTaxGrand) ? salesTaxGrand : 0;
      salesTaxEl.textContent = `$${money(round2(finalTax))}`;
    }

    const coreGrandWrap = $("coreGrandTotalWrap");
    const coreGrandEl = $("coreGrandTotalDisplay");
    const hasCoreGrand = Number.isFinite(coreGrand) && coreGrand > 0;
    if (coreGrandWrap) coreGrandWrap.style.display = hasCoreGrand ? "" : "none";
    if (coreGrandEl) coreGrandEl.textContent = hasCoreGrand ? `$${money(round2(coreGrand))}` : "—";

    const miscGrandWrap = $("miscGrandTotalWrap");
    const miscGrandEl = $("miscGrandTotalDisplay");
    const hasMiscGrand = Number.isFinite(miscGrand) && miscGrand > 0;
    if (miscGrandWrap) miscGrandWrap.style.display = hasMiscGrand ? "" : "none";
    if (miscGrandEl) miscGrandEl.textContent = hasMiscGrand ? `$${money(round2(miscGrand))}` : "—";

    const supplyGrandWrap = $("shopSupplyGrandTotalWrap");
    const supplyGrandEl = $("shopSupplyGrandTotalDisplay");
    const hasSupplyGrand = Number.isFinite(supplyGrand) && supplyGrand > 0;
    if (supplyGrandWrap) supplyGrandWrap.style.display = hasSupplyGrand ? "" : "none";
    if (supplyGrandEl) supplyGrandEl.textContent = hasSupplyGrand ? `$${money(round2(supplyGrand))}` : "—";

    const grandEl = $("grandTotalDisplay");
    if (grandEl) {
      if (Number.isFinite(calculatedGrand)) {
        grandEl.textContent = `$${money(calculatedGrand)}`;
      } else if (Number.isFinite(grand)) {
        grandEl.textContent = `$${money(round2(grand))}`;
      }
    }
  }

  function upsertHiddenJsonInput(formEl, name, obj) {
    if (!formEl) return;
    let input = formEl.querySelector(`input[name="${CSS.escape(name)}"]`);
    if (!input) {
      input = document.createElement("input");
      input.type = "hidden";
      input.name = name;
      formEl.appendChild(input);
    }
    input.value = JSON.stringify(obj || {});
  }

  // ---------------- backend search dropdown ----------------
  function debounce(fn, ms) {
    let t = null;
    return function (...args) {
      if (t) clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), ms);
    };
  }

  async function fetchVinDetails(vin) {
    try {
      const url = `/work_orders/api/vin?vin=${encodeURIComponent(vin)}`;
      console.log("[VIN] Fetching details for:", vin);
      const res = await fetch(url, { headers: { "Accept": "application/json" } });
      if (!res.ok) {
        console.error("[VIN] Response not OK:", res.status);
        // Show error message to user
        if (res.status === 401 || res.status === 403) {
          toast("Error: Not authorized to lookup VIN");
        } else if (res.status >= 500) {
          toast("Server error while looking up VIN");
        }
        return null;
      }
      const data = await res.json();
      console.log("[VIN] Received data:", data);
      
      // Check if API returned an error
      if (data && !data.ok) {
        console.warn("[VIN] API returned error:", data.error);
        if (data.error === "vin_length") {
          // Don't show error - validation happens on input
        } else if (data.error === "vin_invalid_chars") {
          toast(data.message || "VIN cannot contain I, O, or Q characters");
        } else if (data.error === "vin_lookup_failed") {
          toast(data.message || "Failed to lookup VIN. Please try again later.");
        } else if (data.error === "vin_no_results") {
          toast(data.message || "No information found for this VIN number.");
        } else if (data.error === "vin_invalid") {
          toast(data.message || "Invalid VIN number");
        }
        return null;
      }
      
      return data && data.ok ? data : null;
    } catch (err) {
      console.error("[VIN] Error fetching VIN details:", err);
      toast("Network error while looking up VIN. Please check your connection.");
      return null;
    }
  }

  function ensureDropdown() {
    let dd = document.getElementById("partsSearchDropdown");
    if (dd) return dd;

    dd = document.createElement("div");
    dd.id = "partsSearchDropdown";
    dd.style.position = "absolute";
    dd.style.zIndex = "2000";
    dd.style.background = "#fff";
    dd.style.border = "1px solid rgba(0,0,0,.15)";
    dd.style.borderRadius = "8px";
    dd.style.boxShadow = "0 6px 18px rgba(0,0,0,.1)";
    dd.style.maxHeight = "280px";
    dd.style.overflow = "auto";
    dd.style.display = "none";
    dd.style.minWidth = "320px";
    document.body.appendChild(dd);
    return dd;
  }

  function placeDropdownNearInput(dd, inputEl) {
    const r = inputEl.getBoundingClientRect();
    dd.style.left = `${window.scrollX + r.left}px`;
    dd.style.top = `${window.scrollY + r.bottom + 4}px`;
    dd.style.minWidth = `${Math.max(320, r.width)}px`;
  }

  function hideDropdown(dd) {
    dd.style.display = "none";
    dd.innerHTML = "";
    dd._items = [];
    dd._targetInput = null;
    dd._targetRow = null;
    dd._targetBlock = null;
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function renderDropdown(dd, items) {
    const list = Array.isArray(items) ? items : [];
    const oneTimeItem = { __one_time_part: true };
    const displayItems = [oneTimeItem, ...list];
    dd._items = displayItems;

    dd.innerHTML = displayItems.map((it, idx) => {
      if (it.__one_time_part) {
        return `
          <div class="parts-dd-item" data-idx="${idx}"
               style="padding:10px 12px; cursor:pointer; border-bottom:1px solid rgba(0,0,0,.06); background:#f8f9fa;">
            <div style="font-weight:600; line-height:1.2;">+ One-time part</div>
            <div style="font-size:12px; color:#6c757d; margin-top:2px;">Manual line (no inventory link, no part_id)</div>
          </div>
        `;
      }

      const title = `${it.part_number || ""} — ${it.description || ""}`.trim();
      const coreCost = (it.core_has_charge && Number.isFinite(toNum(it.core_cost))) ? toNum(it.core_cost) : 0;
      const miscCost = (it.misc_has_charge && Array.isArray(it.misc_charges))
        ? round2(it.misc_charges.reduce((sum, ch) => sum + (toNum(ch?.price) || 0), 0))
        : 0;
      const chargesText = (coreCost > 0 || miscCost > 0)
        ? ` • Charges: core $${money(coreCost)}${miscCost > 0 ? `, misc $${money(miscCost)}` : ""}`
        : "";
      const trackingText = it.do_not_track_inventory ? " • Not tracked" : "";
      const meta = `Stock: ${it.in_stock ?? 0} • Avg cost: $${money(toNum(it.average_cost) ?? 0)}${trackingText}${chargesText}`;
      return `
        <div class="parts-dd-item" data-idx="${idx}"
             style="padding:10px 12px; cursor:pointer; border-bottom:1px solid rgba(0,0,0,.06);">
          <div style="font-weight:600; line-height:1.2;">${escapeHtml(title)}</div>
          <div style="font-size:12px; color:#6c757d; margin-top:2px;">${escapeHtml(meta)}</div>
        </div>
      `;
    }).join("");

    if (list.length === 0) {
      dd.innerHTML += `<div style="padding:10px; color:#6c757d;">No inventory matches</div>`;
    }
  }

  async function fetchParts(q) {
    const url = `/work_orders/api/parts/search?q=${encodeURIComponent(q)}&limit=20`;
    const res = await fetch(url, { headers: { "Accept": "application/json" } });
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data.items) ? data.items : [];
  }

  function fillRowFromPart(tr, part) {
    const pn = tr.querySelector(".part-number");
    const partIdInput = tr.querySelector(".part-id");
    const ds = tr.querySelector(".part-description");
    const cost = tr.querySelector(".part-cost");
    const coreInput = tr.querySelector(".part-core-charge");
    const miscInput = tr.querySelector(".part-misc-charge");
    const miscDescriptionInput = tr.querySelector(".part-misc-charge-description");

    setOneTimePartRow(tr, false);
    updatePartCostEditableState(tr);

    if (pn) pn.value = part.part_number || "";
    if (partIdInput) partIdInput.value = String(part?.id || "").trim();
    if (ds) {
      const d = (part.description || "").trim();
      const ref = (part.reference || "").trim();
      ds.value = ref && ref !== d ? `${d} (${ref})` : d;
    }
    if (cost) cost.value = (part.average_cost != null) ? String(part.average_cost) : "";

    const coreCharge = (part?.core_has_charge && Number.isFinite(toNum(part?.core_cost)))
      ? Math.max(0, round2(toNum(part.core_cost)))
      : 0;
    const miscCharge = (part?.misc_has_charge && Array.isArray(part?.misc_charges))
      ? Math.max(0, round2(part.misc_charges.reduce((sum, ch) => sum + (toNum(ch?.price) || 0), 0)))
      : 0;
    const miscItems = (Array.isArray(part?.misc_charges) ? part.misc_charges : [])
      .map((ch) => ({
        description: String(ch?.description || "").trim(),
        price: Number.isFinite(toNum(ch?.price)) ? round2(toNum(ch?.price)) : null,
        taxable: ch?.taxable !== false,
      }))
      .filter((x) => x.description);

    tr.dataset.coreChargeBase = String(coreCharge);
    if (coreInput) coreInput.value = isCoreChargeEnabled(tr) ? String(coreCharge) : "0";
    if (miscInput) miscInput.value = String(miscCharge);
    if (miscDescriptionInput) {
      // Always store misc charges in the FIRST row of this block
      // This keeps all automatic charges in one place for easier management
      const blockEl = tr.closest(".wo-labor");
      const tbody = blockEl.querySelector(".partsTbody");
      const firstRow = tbody.querySelector("tr.parts-row");
      const firstMiscInput = firstRow.querySelector(".part-misc-charge-description");
      
      if (firstMiscInput) {
        // Get existing items from first row, preserve manual charges AND items from other rows
        const existingItems = getMiscItemsArray(firstRow);
        
        // Get this row's index for tracking
        const rows = Array.from(tbody.querySelectorAll("tr.parts-row"));
        const rowIndex = rows.indexOf(tr);
        
        // Keep manual items + items from OTHER rows
        const itemsToKeep = existingItems.filter(item => 
          item.manual === true || item.partIndex !== rowIndex
        );
        
        // Add new auto items with quantity multiplied by this row's qty
        const rowQty = Number(tr.querySelector(".part-qty")?.value || 1) || 1;
        const autoItemsForThisRow = miscItems.map(item => ({
          ...item,
          quantity: (Number(item.quantity || 1)) * rowQty,
          partIndex: rowIndex  // Track which part this is from
        }));
        
        const allItems = [...itemsToKeep, ...autoItemsForThisRow];
        firstMiscInput.value = JSON.stringify(allItems);
        
        // Update baseline - store items with un-multiplied quantity (baseline = quantity: 1)
        // This allows us to recalculate properly when part qty changes later
        let baseline = [];
        try {
          const existing = JSON.parse(firstRow.dataset.autoMiscItemsBaseline || "[]");
          baseline = existing.filter(item => item.partIndex !== rowIndex);  // Remove old items for this row
        } catch (err) {
          // Ignore parse errors
        }
        
        // Store baseline with quantity = 1 for each item (not multiplied by rowQty)
        const baselineItemsForThisRow = miscItems.map(item => ({
          description: item.description,
          price: item.price,
          quantity: 1,  // BASELINE: always 1, will be multiplied by actual part qty
          partIndex: rowIndex,
          manual: false
        }));
        
        const newBaseline = baseline.concat(baselineItemsForThisRow);
        firstRow.dataset.autoMiscItemsBaseline = JSON.stringify(newBaseline);
      }
      
      // Clear misc charges from other rows
      if (tr !== firstRow) {
        miscDescriptionInput.value = "";
        delete tr.dataset.autoMiscItemsBaseline;
      }
    }
    setRowChargesMeta(tr);
    syncCoreChargeFromToggle(tr);
    
    // Show toggle only if there's a core charge
    const lineCell = tr.querySelector(".part-line-total");
    if (lineCell) {
      const toggleWrapper = lineCell.querySelector(".part-core-toggle-wrapper");
      if (toggleWrapper && Number.isFinite(coreCharge) && coreCharge > 0) {
        toggleWrapper.style.display = "flex";
      } else if (toggleWrapper) {
        toggleWrapper.style.display = "none";
      }
    }

    const price = tr.querySelector(".part-price");
    if (price) price.value = "";
    delete tr.dataset.priceAutofilled;
    delete tr.dataset.priceManuallyEdited;
  }

  function fillRowAsOneTimePart(tr, searchText) {
    const pn = tr.querySelector(".part-number");
    const partIdInput = tr.querySelector(".part-id");
    const cost = tr.querySelector(".part-cost");
    const coreInput = tr.querySelector(".part-core-charge");
    const miscInput = tr.querySelector(".part-misc-charge");
    const miscDescriptionInput = tr.querySelector(".part-misc-charge-description");
    const toggle = tr.querySelector(".part-core-toggle");

    if (partIdInput) partIdInput.value = "";
    setOneTimePartRow(tr, true);
    updatePartCostEditableState(tr);

    const typed = String(searchText || "").trim();
    if (pn && !String(pn.value || "").trim() && typed) {
      pn.value = typed;
    }

    if (cost) cost.value = "";
    if (coreInput) coreInput.value = "0";
    if (miscInput) miscInput.value = "0";
    if (miscDescriptionInput) miscDescriptionInput.value = "";
    if (toggle) toggle.checked = coreChargeDefaultEnabled;
    delete tr.dataset.autoMiscItemsBaseline;
    delete tr.dataset.coreChargeBase;
    delete tr.dataset.priceAutofilled;
    delete tr.dataset.priceManuallyEdited;

    setRowChargesMeta(tr);
    syncCoreChargeFromToggle(tr);

    const lineCell = tr.querySelector(".part-line-total");
    if (lineCell) {
      const toggleWrapper = lineCell.querySelector(".part-core-toggle-wrapper");
      if (toggleWrapper) toggleWrapper.style.display = "none";
    }
  }

  const debouncedSearch = debounce(async function (dd, inputEl, tr, blockEl) {
    const q = String(inputEl.value || "").trim();
    if (q.length < 3) { hideDropdown(dd); return; }

    placeDropdownNearInput(dd, inputEl);
    dd.style.display = "block";
    dd.innerHTML = `<div style="padding:10px; color:#6c757d;">Searching…</div>`;

    const items = await fetchParts(q);
    dd._targetInput = inputEl;
    dd._targetRow = tr;
    dd._targetBlock = blockEl;
    renderDropdown(dd, items);
  }, 150);

  // ---------------- blocks add/remove ----------------
  function renumberBlock(blockEl, idx) {
    blockEl.dataset.laborIndex = String(idx);
    blockEl.querySelector(".labor-number").textContent = String(idx + 1);

    blockEl.querySelector(".labor-description").name = `labors[${idx}][labor_description]`;
    blockEl.querySelector(".labor-hours").name = `labors[${idx}][labor_hours]`;
    blockEl.querySelector(".labor-rate").name = `labors[${idx}][labor_rate_code]`;
    const laborTotalInput = blockEl.querySelector(".labor-total-input");
    if (laborTotalInput) laborTotalInput.name = `labors[${idx}][labor_total_ui]`;
    const laborAssignmentsInput = blockEl.querySelector(".labor-assignments-json");
    if (laborAssignmentsInput) laborAssignmentsInput.name = `labors[${idx}][assigned_mechanics_json]`;

    const tbody = blockEl.querySelector(".partsTbody");
    const rows = Array.from(tbody.querySelectorAll("tr.parts-row"));
    rows.forEach((tr, rIdx) => {
      tr.dataset.index = String(rIdx);
      tr.querySelector(".part-number").name = `labors[${idx}][parts][${rIdx}][part_number]`;
      tr.querySelector(".part-id").name = `labors[${idx}][parts][${rIdx}][part_id]`;
      tr.querySelector(".part-one-time").name = `labors[${idx}][parts][${rIdx}][one_time_part]`;
      tr.querySelector(".part-core-charge").name = `labors[${idx}][parts][${rIdx}][core_charge]`;
      tr.querySelector(".part-misc-charge").name = `labors[${idx}][parts][${rIdx}][misc_charge]`;
      tr.querySelector(".part-misc-charge-description").name = `labors[${idx}][parts][${rIdx}][misc_charge_description]`;
      tr.querySelector(".part-description").name = `labors[${idx}][parts][${rIdx}][description]`;
      tr.querySelector(".part-qty").name = `labors[${idx}][parts][${rIdx}][qty]`;
      tr.querySelector(".part-cost").name = `labors[${idx}][parts][${rIdx}][cost]`;
      tr.querySelector(".part-price").name = `labors[${idx}][parts][${rIdx}][price]`;
    });
  }

  function cloneBlock(blocksContainer) {
    const blocks = Array.from(blocksContainer.querySelectorAll(".wo-labor"));
    const last = blocks[blocks.length - 1];
    const clone = last.cloneNode(true);

    clone.querySelectorAll("input").forEach(i => {
      i.value = "";
    });
    clone.querySelectorAll(".labor-assignments-json").forEach(i => {
      i.value = "[]";
    });
    clone.querySelectorAll(".laborAssignSummary").forEach(el => {
      el.textContent = "Assigned: —";
    });
    clone.querySelectorAll(".part-core-charge, .part-misc-charge").forEach(i => {
      i.value = "0";
    });
    clone.querySelectorAll(".part-misc-charge-description").forEach(i => {
      i.value = "";
    });
    clone.querySelectorAll("tr.parts-row").forEach(tr => {
      setOneTimePartRow(tr, false);
      updatePartCostEditableState(tr);
      delete tr.dataset.autoMiscItemsBaseline;
      delete tr.dataset.coreChargeBase;
      const toggle = tr.querySelector(".part-core-toggle");
      if (toggle) toggle.checked = coreChargeDefaultEnabled;
      const toggleWrapper = tr.querySelector(".part-core-toggle-wrapper");
      if (toggleWrapper) toggleWrapper.style.display = "none";
    });
    clone.querySelectorAll(".part-line-total-value").forEach(td => td.innerHTML = `<span class="text-muted">—</span>`);
    clone.querySelectorAll(".laborTotalDisplay, .partsTotalDisplay, .laborFullTotalDisplay").forEach(el => el.textContent = "—");

    // Clear misc charges table
    const miscTbody = clone.querySelector(".miscChargesTbody");
    if (miscTbody) miscTbody.innerHTML = "";
    const miscWrap = clone.querySelector(".miscChargesEditWrap");
    if (miscWrap) miscWrap.style.display = "none";

    const tbody = clone.querySelector(".partsTbody");
    tbody.innerHTML = "";
    tbody.appendChild(makePartsRow(blocks.length, 0));

    blocksContainer.appendChild(clone);
    return clone;
  }

  function wireBlockEvents(blocksContainer, pricing, laborRates, shopSupplyPct) {
    let isLaborSyncing = false;
    blocksContainer.addEventListener("input", function (e) {
      const t = e.target;
      if (!t) return;

      if (t.classList?.contains("part-number") || t.classList?.contains("part-description")) {
        const tr = t.closest("tr.parts-row");
        if (tr) {
          const partIdInput = tr.querySelector(".part-id");
          const coreInput = tr.querySelector(".part-core-charge");
          const miscInput = tr.querySelector(".part-misc-charge");
          const miscDescriptionInput = tr.querySelector(".part-misc-charge-description");
          const toggle = tr.querySelector(".part-core-toggle");
          if (partIdInput) partIdInput.value = "";
          if (coreInput) coreInput.value = "0";
          if (miscInput) miscInput.value = "0";
          if (miscDescriptionInput) miscDescriptionInput.value = "";
          if (toggle) toggle.checked = coreChargeDefaultEnabled;
          delete tr.dataset.autoMiscItemsBaseline;
          delete tr.dataset.coreChargeBase;
          setRowChargesMeta(tr);
          syncCoreChargeFromToggle(tr);
          delete tr.dataset.priceAutofilled;
          delete tr.dataset.priceManuallyEdited;
          updatePartCostEditableState(tr);
          
          // Hide toggle when core is cleared
          const lineCell = tr.querySelector(".part-line-total");
          if (lineCell) {
            const toggleWrapper = lineCell.querySelector(".part-core-toggle-wrapper");
            if (toggleWrapper) toggleWrapper.style.display = "none";
          }
        }
      }

      if (t.classList?.contains("part-core-toggle")) {
        const tr = t.closest("tr.parts-row");
        if (tr) syncCoreChargeFromToggle(tr);
      }

      if (t.classList?.contains("part-price")) {
        const tr = t.closest("tr.parts-row");
        if (tr) {
          const rawPrice = String(t.value || "").trim();
          if (!rawPrice) {
            delete tr.dataset.priceManuallyEdited;
            delete tr.dataset.priceAutofilled;
          } else {
            tr.dataset.priceManuallyEdited = "1";
            delete tr.dataset.priceAutofilled;
          }
        }
      }

      if (t.classList?.contains("part-cost")) {
        const tr = t.closest("tr.parts-row");
        if (tr) {
          const manualPrice = tr.dataset.priceManuallyEdited === "1";
          if (!manualPrice) {
            const priceInput = tr.querySelector(".part-price");
            if (priceInput) priceInput.value = "";
            delete tr.dataset.priceAutofilled;
          }
        }
      }

      if (!isLaborSyncing && t.classList?.contains("labor-total-input")) {
        const blockEl = t.closest(".wo-labor");
        if (blockEl) {
          isLaborSyncing = true;
          syncHoursFromLaborTotal(blockEl, laborRates);
          isLaborSyncing = false;
        }
      }

      if (!isLaborSyncing && (t.classList?.contains("labor-hours") || t.classList?.contains("labor-rate"))) {
        const blockEl = t.closest(".wo-labor");
        if (blockEl) {
          isLaborSyncing = true;
          syncLaborTotalFromHours(blockEl, laborRates);
          isLaborSyncing = false;
        }
      }

      // When quantity changes, adjust automatic misc charges quantity
      if (t.classList?.contains("part-qty")) {
        const tr = t.closest("tr.parts-row");
        if (tr) {
          const blockEl = tr.closest(".wo-labor");
          const tbody = blockEl.querySelector(".partsTbody");
          const rows = Array.from(tbody.querySelectorAll("tr.parts-row"));
          const rowIndex = rows.indexOf(tr);
          const newQty = Number(tr.querySelector(".part-qty")?.value || 1) || 1;
          
          // Get first row (where all charges are stored)
          const firstRow = rows[0];
          const firstMiscInput = firstRow.querySelector(".part-misc-charge-description");
          const baselineStr = firstRow.dataset.autoMiscItemsBaseline;
          
          if (firstMiscInput && baselineStr && rowIndex >= 0) {
            try {
              const baseline = JSON.parse(baselineStr);
              const items = JSON.parse(firstMiscInput.value || "[]");
              
              // Recalculate items for this row based on baseline
              const baselineForThisRow = baseline.filter(item => item.partIndex === rowIndex);
              if (baselineForThisRow.length > 0) {
                // Multiply baseline items by new quantity
                const adjusted = baselineForThisRow.map(item => ({
                  description: item.description,
                  price: item.price,
                  quantity: (Number(item.quantity || 1)) * newQty,
                  partIndex: item.partIndex,
                  manual: false
                }));
                
                // Remove old items for this row and keep all other items (manual + other rows)
                const otherItems = items.filter(item => 
                  item.manual === true || item.partIndex !== rowIndex
                );
                const allItems = [...otherItems, ...adjusted];
                firstMiscInput.value = JSON.stringify(allItems);
                
                // Mark that we need to re-render the misc charges table
                blockEl.dataset.shouldReRenderMiscTable = "true";
              }
            } catch (err) {
              // JSON parse error, ignore
            }
          }
        }
      }

      const blockEl = t.closest(".wo-labor");
      if (!blockEl) return;
      
      recalcAll(blocksContainer, pricing, laborRates, shopSupplyPct);
      
      // Re-render misc charges table if qty was changed (to show updated quantities)
      if (blockEl.dataset.shouldReRenderMiscTable === "true") {
        renderMiscChargesTable(blockEl);
        delete blockEl.dataset.shouldReRenderMiscTable;
      }
    });

    blocksContainer.addEventListener("blur", function (e) {
      const t = e.target;
      if (!t || !t.classList?.contains("labor-total-input")) return;
      const blockEl = t.closest(".wo-labor");
      if (!blockEl) return;
      const total = toNum(t.value);
      t.value = Number.isFinite(total) ? money(total) : "";
    }, true);

    blocksContainer.addEventListener("click", function (e) {
      const btn = e.target.closest(".removeLaborBtn");
      if (!btn) return;

      const blocks = Array.from(blocksContainer.querySelectorAll(".wo-labor"));
      if (blocks.length <= 1) return;

      const blockEl = btn.closest(".wo-labor");
      if (!blockEl) return;

      blockEl.remove();
      Array.from(blocksContainer.querySelectorAll(".wo-labor")).forEach((b, idx) => renumberBlock(b, idx));
      recalcAll(blocksContainer, pricing, laborRates, shopSupplyPct);
    });
  }

  // ---------------- customer/unit ----------------
  function setSelectOptions(selectEl, items, placeholder) {
    if (!selectEl) return;

    if (selectEl.tomSelect) {
      const ts = selectEl.tomSelect;
      ts.clear(true);
      ts.clearOptions();
      ts.addOption({ value: "", text: placeholder || "-- Select --" });
      (items || []).forEach(it => {
        ts.addOption({ value: String(it.id || ""), text: String(it.label || "") });
      });
      ts.refreshOptions(false);
      ts.setValue("", true);
      return;
    }

    selectEl.innerHTML = "";

    const ph = document.createElement("option");
    ph.value = "";
    ph.textContent = placeholder || "-- Select --";
    selectEl.appendChild(ph);

    (items || []).forEach(it => {
      const opt = document.createElement("option");
      opt.value = String(it.id || "");
      opt.textContent = String(it.label || "");
      selectEl.appendChild(opt);
    });
  }

  function setSelectDisabled(selectEl, disabled) {
    if (!selectEl) return;
    selectEl.disabled = disabled;
    if (selectEl.tomSelect) {
      if (disabled) selectEl.tomSelect.disable();
      else selectEl.tomSelect.enable();
    }
  }

  async function fetchUnits(customerId) {
    const url = `/work_orders/api/units?customer_id=${encodeURIComponent(customerId)}`;
    const res = await fetch(url, { headers: { "Accept": "application/json" } });
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data.items) ? data.items : [];
  }

  async function fetchUnitDetails(unitId) {
    const url = `/work_orders/api/unit?id=${encodeURIComponent(unitId)}`;
    const res = await fetch(url, { headers: { "Accept": "application/json" } });
    if (!res.ok) return null;
    const data = await res.json();
    return data.ok && data.item ? data.item : null;
  }

  // ---------------- restore initial data ----------------
  function ensureBlocksCount(blocksContainer, desiredCount) {
    const blocks = Array.from(blocksContainer.querySelectorAll(".wo-labor"));
    while (blocks.length < desiredCount) {
      cloneBlock(blocksContainer);
      blocks.push(blocksContainer.querySelectorAll(".wo-labor")[blocks.length]);
    }
    Array.from(blocksContainer.querySelectorAll(".wo-labor")).forEach((b, idx) => renumberBlock(b, idx));
  }

  function applyInitialDataToUi(blocksContainer, initialBlocks) {
    if (!Array.isArray(initialBlocks) || initialBlocks.length === 0) return;

    ensureBlocksCount(blocksContainer, initialBlocks.length);
    const blockEls = Array.from(blocksContainer.querySelectorAll(".wo-labor"));

    initialBlocks.forEach((b, bIdx) => {
      const el = blockEls[bIdx];
      if (!el) return;

      const ld = el.querySelector(".labor-description");
      const lh = el.querySelector(".labor-hours");
      const lr = el.querySelector(".labor-rate");

      const laborDesc = (b?.labor?.description ?? b?.labor_description ?? "");
      const laborHours = (b?.labor?.hours ?? b?.labor_hours ?? "");
      const laborRate = (b?.labor?.rate_code ?? b?.labor_rate_code ?? "");

      if (ld) ld.value = String(laborDesc ?? "");
      if (lh) lh.value = String(laborHours ?? "");
      if (lr) lr.value = String(laborRate ?? "");

      const assignments = normalizeAssignedMechanics(
        b?.labor?.assigned_mechanics ?? b?.assigned_mechanics ?? []
      );
      setLaborAssignments(el, assignments);

      const tbody = el.querySelector(".partsTbody");
      if (!tbody) return;

      tbody.innerHTML = "";

      const parts = Array.isArray(b?.parts) ? b.parts : [];
      if (parts.length === 0) {
        tbody.appendChild(makePartsRow(bIdx, 0));
        return;
      }

      parts.forEach((p, rIdx) => {
        const tr = makePartsRow(bIdx, rIdx);
        tr.querySelector(".part-number").value = String(p?.part_number ?? "");
        tr.querySelector(".part-id").value = String(p?.part_id ?? "");
        setOneTimePartRow(tr, !!p?.one_time_part);
        tr.querySelector(".part-description").value = String(p?.description ?? "");
        tr.querySelector(".part-qty").value = String(p?.qty ?? "");
        tr.querySelector(".part-cost").value = String(p?.cost ?? "");
        updatePartCostEditableState(tr);
        tr.querySelector(".part-price").value = String(p?.price ?? "");
        const coreValue = toNum(p?.core_charge ?? p?.core_cost ?? 0) || 0;
        const coreBaseValue = toNum(p?.core_charge_base);
        const effectiveCoreBase = (Number.isFinite(coreBaseValue) && coreBaseValue > 0)
          ? coreBaseValue
          : coreValue;
        tr.querySelector(".part-core-charge").value = String(coreValue);
        tr.querySelector(".part-misc-charge").value = String(p?.misc_charge ?? 0);
        tr.querySelector(".part-misc-charge-description").value = String(p?.misc_charge_description ?? "");
        tr.dataset.coreChargeBase = String(Number.isFinite(effectiveCoreBase) ? effectiveCoreBase : 0);
        const toggle = tr.querySelector(".part-core-toggle");
        if (toggle) toggle.checked = coreValue > 0;
        syncCoreChargeFromToggle(tr);
        setRowChargesMeta(tr);
        
        // Show toggle only if there's a core charge
        const lineCell = tr.querySelector(".part-line-total");
        if (lineCell) {
          const toggleWrapper = lineCell.querySelector(".part-core-toggle-wrapper");
          if (toggleWrapper && Number.isFinite(effectiveCoreBase) && effectiveCoreBase > 0) {
            toggleWrapper.style.display = "flex";
          } else if (toggleWrapper) {
            toggleWrapper.style.display = "none";
          }
        }
        
        if (String(p?.price ?? "").trim()) tr.dataset.priceAutofilled = "1";
        tbody.appendChild(tr);
      });

      tbody.appendChild(makePartsRow(bIdx, parts.length));
    });

    Array.from(blocksContainer.querySelectorAll(".wo-labor")).forEach((b, idx) => renumberBlock(b, idx));
    Array.from(blocksContainer.querySelectorAll(".wo-labor")).forEach((b) => updateLaborAssignSummary(b));
  }

  // ---------------- serialize current UI -> blocks[] ----------------
  function serializeBlocks(blocksContainer) {
    const blocks = Array.from(blocksContainer.querySelectorAll(".wo-labor"));
    const out = [];

    blocks.forEach((bEl) => {
      const labor_description = String(bEl.querySelector(".labor-description")?.value || "").trim();
      const labor_hours = String(bEl.querySelector(".labor-hours")?.value || "").trim();
      const labor_rate_code = String(bEl.querySelector(".labor-rate")?.value || "").trim();
      const labor_full_total = round2(parseMoneyText(bEl.querySelector(".laborFullTotalDisplay")?.textContent || "0"));
      const assigned_mechanics = getLaborAssignments(bEl);

      const parts = [];
      const rows = Array.from(bEl.querySelectorAll("tbody.partsTbody tr.parts-row"));
      rows.forEach((tr) => {
        const part_number = String(tr.querySelector(".part-number")?.value || "").trim();
        const part_id = String(tr.querySelector(".part-id")?.value || "").trim();
        const one_time_part = isOneTimePartRow(tr);
        const description = String(tr.querySelector(".part-description")?.value || "").trim();
        const qty = String(tr.querySelector(".part-qty")?.value || "").trim();
        const cost = String(tr.querySelector(".part-cost")?.value || "").trim();
        const price = String(tr.querySelector(".part-price")?.value || "").trim();
        const coreCharge = String(tr.querySelector(".part-core-charge")?.value || "").trim();
        const miscCharge = String(tr.querySelector(".part-misc-charge")?.value || "").trim();
        const miscChargeDescription = String(tr.querySelector(".part-misc-charge-description")?.value || "").trim();

        const qtyNum = toNum(qty);
        const costNum = toNum(cost);
        const priceNum = toNum(price);
        const coreNum = toNum(coreCharge);
        const miscNum = toNum(miscCharge);
        const hasManualFields = !!(part_number || part_id || description || miscChargeDescription);
        const hasPositiveValues = (
          (Number.isFinite(qtyNum) && qtyNum > 0)
          || (Number.isFinite(costNum) && costNum > 0)
          || (Number.isFinite(priceNum) && priceNum > 0)
          || (Number.isFinite(coreNum) && coreNum > 0)
          || (Number.isFinite(miscNum) && miscNum > 0)
        );
        if (!hasManualFields && !hasPositiveValues) return;

        parts.push({
          part_id,
          one_time_part,
          part_number,
          description,
          qty: qty === "" ? 0 : Number(qty),
          cost: cost === "" ? 0 : Number(cost),
          price: price === "" ? 0 : Number(price),
          core_charge: coreCharge === "" ? 0 : Number(coreCharge),
          misc_charge: miscCharge === "" ? 0 : Number(miscCharge),
          misc_charge_description: miscChargeDescription,
        });
      });

      out.push({
        labor_description,
        labor_hours: labor_hours === "" ? 0 : Number(labor_hours),
        labor_rate_code,
        labor_full_total,
        assigned_mechanics,
        parts,
      });
    });

    return out;
  }

  // ---------------- API ----------------
  async function apiPostJson(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(body || {}),
    });

    let data = null;
    try { data = await res.json(); } catch { data = null; }

    if (!res.ok) {
      const msg = (data && (data.error || data.message)) ? (data.error || data.message) : `Request failed (${res.status})`;
      throw new Error(msg);
    }

    return data;
  }

  // ---------------- UI state ----------------
  function setEditingMode(isEditing, els) {
    const { editor, customerSel, unitSel, addUnitBtn, addLaborBtn } = els;

    if (!editor) return;

    if (isEditing) {
      editor.style.pointerEvents = "";
      editor.style.opacity = "";
      // При редактировании разрешаем менять customer/unit
      setSelectDisabled(customerSel, false);
      setSelectDisabled(unitSel, false);
      if (addUnitBtn) addUnitBtn.disabled = false;
      if (addLaborBtn) addLaborBtn.disabled = false;
      document.querySelectorAll(".removeLaborBtn").forEach(b => { b.disabled = false; });
    } else {
      editor.style.pointerEvents = "none";
      editor.style.opacity = "0.75";
      setSelectDisabled(customerSel, true);
      setSelectDisabled(unitSel, true);
      if (addUnitBtn) addUnitBtn.disabled = true;
      if (addLaborBtn) addLaborBtn.disabled = true;
      document.querySelectorAll(".removeLaborBtn").forEach(b => { b.disabled = true; });
    }
  }

  function setButtonsState(mode, els) {
    // mode: "creating" | "created_locked_open" | "editing_open" | "paid"
    const { createBtn, editBtn, saveBtn, paidBtn, unpaidBtn, emailBtn } = els;

    const show = (el, v) => { if (el) el.style.display = v ? "" : "none"; };
    const enable = (el, v) => { if (el) el.disabled = !v; };

    if (mode === "creating") {
      show(createBtn, true); enable(createBtn, false);
      if (createBtn) createBtn.textContent = "Creating…";
      show(editBtn, false);
      show(saveBtn, false);
      show(paidBtn, false);
      show(unpaidBtn, false);
      show(emailBtn, false);
      return;
    }

    if (mode === "created_locked_open") {
      show(createBtn, false);
      show(editBtn, true); enable(editBtn, true);
      show(saveBtn, false);
      show(paidBtn, true); enable(paidBtn, true);
      show(unpaidBtn, false);
      show(emailBtn, true); enable(emailBtn, true);
      return;
    }

    if (mode === "editing_open") {
      show(createBtn, false);
      show(editBtn, false);
      show(saveBtn, true); enable(saveBtn, true);
      show(paidBtn, true); enable(paidBtn, false); // пока редактируем — paid запрещаем
      show(unpaidBtn, false);
      show(emailBtn, true); enable(emailBtn, true);
      return;
    }

    if (mode === "paid") {
      show(createBtn, false);
      show(editBtn, false);
      show(saveBtn, false);
      show(paidBtn, false);
      show(unpaidBtn, true); enable(unpaidBtn, true);
      show(emailBtn, true); enable(emailBtn, true);
      return;
    }
  }

  // -------- manual misc charges --------
  function getMiscItemsArray(tr) {
    const raw = String(tr.querySelector(".part-misc-charge-description")?.value || "").trim();
    if (!raw) return [];
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed;
    } catch { }
    return [];
  }

  function saveMiscItemsArray(tr, items) {
    const input = tr.querySelector(".part-misc-charge-description");
    if (!input) return;
    input.value = JSON.stringify(items || []);
  }

  function addMiscChargeToRow(tr, description, quantity, price, taxable) {
    const desc = String(description || "").trim();
    const qty = Number(quantity || 1);
    const prc = toNum(price);
    if (!desc || !Number.isFinite(qty) || qty <= 0 || !Number.isFinite(prc) || prc < 0) {
      toast("Description, Quantity > 0, and valid price are required.");
      return false;
    }

    const items = getMiscItemsArray(tr);
    items.push({
      description: desc,
      quantity: qty,
      price: round2(prc),
      manual: true,
      taxable: taxable !== false,
    });
    saveMiscItemsArray(tr, items);
    return true;
  }

  function renderMiscChargesTable(blockEl) {
    const wrapEl = blockEl.querySelector(".miscChargesEditWrap");
    const tbody = blockEl.querySelector(".miscChargesTbody");
    if (!wrapEl || !tbody) return;

    const partsTable = blockEl.querySelector("table tbody.partsTbody");
    if (!partsTable) return;
    
    // Get misc items from first row (where all charges are stored)
    const firstRow = partsTable.querySelector("tr.parts-row");
    if (!firstRow) return;
    
    const miscItems = getMiscItemsArray(firstRow);
    
    if (miscItems.length === 0) {
      wrapEl.style.display = "none";
      tbody.innerHTML = "";
      return;
    }

    wrapEl.style.display = "";
    tbody.innerHTML = "";

    miscItems.forEach((item, idx) => {
      const qty = Number(item.quantity || 1);
      const price = Number(item.price || 0);
      const total = round2(qty * price);
      const isManual = item.manual === true;
      const isTaxable = item.taxable !== false;
      
      const row = document.createElement("tr");
      row.dataset.miscIndex = String(idx);
      row.innerHTML = `
        <td>
          <input type="text" class="form-control form-control-sm misc-desc-input" value="${escapeText(item.description)}" placeholder="Description">
        </td>
        <td>
          <input type="number" class="form-control form-control-sm misc-qty-input" value="${qty}" step="1" min="0" max="999999" placeholder="Qty">
        </td>
        <td>
          <input type="number" class="form-control form-control-sm misc-price-input" value="${price}" step="0.01" min="0" max="999999" placeholder="Price">
        </td>
        <td class="text-center align-middle">
          <input type="checkbox" class="form-check-input misc-taxable-input" ${isTaxable ? "checked" : ""} title="Taxable">
        </td>
        <td class="misc-total-display align-middle" style="font-weight: 500;">$${money(total)}</td>
        <td>
          <button type="button" class="btn btn-sm btn-outline-danger misc-delete-btn" title="Delete">&times;</button>
        </td>
      `;
      tbody.appendChild(row);
    });
  }

  function updateMiscChargeInRow(tr, index, newDesc, newQty, newPrice) {
    const blockEl = tr.closest(".wo-labor");
    if (!blockEl) return false;
    
    const desc = String(newDesc || "").trim();
    const qty = Number(newQty || 0);
    const price = toNum(newPrice);
    
    // Allow qty>=0 and valid price>=0
    if (!desc || !Number.isFinite(qty) || qty < 0 || !Number.isFinite(price) || price < 0) {
      return false;
    }
    
    // Update in first row (where all charges are stored)
    const tbody = blockEl.querySelector(".partsTbody");
    const firstRow = tbody.querySelector("tr.parts-row");
    if (!firstRow) return false;
    
    const items = getMiscItemsArray(firstRow);
    if (index < 0 || index >= items.length) return false;
    
    items[index].description = desc;
    items[index].quantity = qty;
    items[index].price = round2(price);
    saveMiscItemsArray(firstRow, items);
    return true;
  }

  function removeMiscChargeFromRow(tr, index) {
    const blockEl = tr.closest(".wo-labor");
    if (!blockEl) return false;
    
    // Remove from first row (where all charges are stored)
    const tbody = blockEl.querySelector(".partsTbody");
    const firstRow = tbody.querySelector("tr.parts-row");
    if (!firstRow) return false;
    
    const items = getMiscItemsArray(firstRow);
    if (index < 0 || index >= items.length) return false;
    
    items.splice(index, 1);
    saveMiscItemsArray(firstRow, items);
    return true;
  }

  // ---------------- init ----------------
  function initWorkOrderDetailsPage() {
    const customersData = readJsonScript("customersData", []);
    const laborRates = readJsonScript("laborRatesData", []);
    const mechanicsData = readJsonScript("mechanicsData", []);
    const pricing = readJsonScript("partsPricingRulesData", null);
    const shopSupplyData = readJsonScript("shopSupplyData", { percentage: 0 });
    const salesTaxData = readJsonScript("salesTaxData", { rate: 0, zip_code: "" });
    const coreChargeData = readJsonScript("coreChargeDefaultData", { enabled: true });
    const totalsSnapshot = readJsonScript("workOrderInitialTotalsData", {});

    const shopSupplyPct = toNum(shopSupplyData?.percentage ?? shopSupplyData) || 0;
    currentSalesTaxRate = Math.max(0, toNum(salesTaxData?.rate) || 0);
    coreChargeDefaultEnabled = !!coreChargeData?.enabled;

    const blocksContainer = $("laborsContainer");
    if (!blocksContainer) return;
    const woPageRootMarker = blocksContainer;

    function isWorkOrderPageAlive() {
      return !!(woPageRootMarker && document.body && document.body.contains(woPageRootMarker));
    }

    blocksContainer.querySelectorAll("tr.parts-row").forEach((tr) => {
      const toggle = tr.querySelector(".part-core-toggle");
      if (toggle) toggle.checked = coreChargeDefaultEnabled;
      updatePartCostEditableState(tr);
      syncCoreChargeFromToggle(tr);
    });

    const customerSel = $("customerSelect");
    const unitSel = $("unitSelect");
    const editor = $("workOrderEditor");
    const hint = $("selectHint");

    const customerHidden = $("selectedCustomerHidden");
    const unitHidden = $("selectedUnitHidden");
    const createUnitCustomerHidden = $("createUnitCustomerHidden");

    const woForm = $("workOrderForm");

    const createBtn = $("createWorkOrderBtn");
    const editBtn = $("editWorkOrderBtn");
    const saveBtn = $("saveWorkOrderBtn");
    const paidBtn = $("paidWorkOrderBtn");
    const unpaidBtn = $("unpaidWorkOrderBtn");
    const emailBtn = $("emailWorkOrderBtn");

    const addLaborBtn = $("addLaborBtn");
    const addUnitBtn = $("addUnitBtn");

    const unitVinInput = $("unitVinInput");
    const unitMakeInput = $("unitMakeInput");
    const unitModelInput = $("unitModelInput");
    const unitYearInput = $("unitYearInput");
    const unitTypeInput = $("unitTypeInput");
    const vinLoadingSpinner = $("vinLoadingSpinner");
    const unitMileageInput = $("unitMileageInput");
    const unitMileageHidden = $("unitMileageHidden");
    const workOrderDateInput = $("workOrderDateInput");
    const paymentDateInput = $("paymentDateInput");
    const workOrderDatesBlock = $("workOrderDatesBlock");
    const woMetaCreated = $("woMetaCreated");
    const woMetaPaymentsBody = $("woMetaPaymentsBody");

    const assignMechanicsModal = $("assignMechanicsModal");
    const assignMechanicsTbody = $("assignMechanicsTbody");
    const assignMechanicsEmpty = $("assignMechanicsEmpty");
    const assignMechanicsError = $("assignMechanicsError");
    const assignMechanicsSaveBtn = $("assignMechanicsSaveBtn");

    const els = {
      editor, customerSel, unitSel, addUnitBtn, addLaborBtn,
      createBtn, editBtn, saveBtn, paidBtn, unpaidBtn, emailBtn
    };

    let targetAssignBlock = null;

    async function loadWorkOrderTimeline() {
      if (!isCreated || !workOrderId || !workOrderDatesBlock || !woMetaPaymentsBody) return;

      try {
        const res = await fetch(`/work_orders/api/work_orders/${encodeURIComponent(workOrderId)}/payments`, {
          method: "GET",
          headers: { "Accept": "application/json" }
        });
        const data = await res.json();
        if (!res.ok || !data || !data.ok) {
          throw new Error((data && (data.error || data.message)) || "Failed to load timeline");
        }

        customerEmail = String(data.customer_email || "").trim();
        workOrderDatesBlock.style.display = "";
        if (woMetaCreated) {
          woMetaCreated.textContent = data.created_at_label || formatDateLabel(data.created_at);
        }

        const payments = Array.isArray(data.payments) ? data.payments : [];
        if (!payments.length) {
          woMetaPaymentsBody.innerHTML = `<tr><td colspan="5" class="text-muted">No payments.</td></tr>`;
          return;
        }

        woMetaPaymentsBody.innerHTML = payments.map((p) => {
          const pid = String(p.id || "");
          const amount = Number(p.amount || 0);
          const method = String(p.payment_method || "cash");
          const notes = String(p.notes || "");
          const dateLabel = p.payment_date_label || formatDateLabel(p.payment_date || p.created_at);
          return `
            <tr>
              <td>${escapeText(dateLabel)}</td>
              <td class="text-end fw-semibold">$${Number.isFinite(amount) ? amount.toFixed(2) : "0.00"}</td>
              <td>${escapeText(method)}</td>
              <td>${notes ? escapeText(notes) : "-"}</td>
              <td class="text-end">
                <button type="button" class="btn btn-sm btn-outline-secondary js-email-wo-payment-inline me-1" data-payment-id="${escapeText(pid)}">&#9993; Receipt</button>
                <button type="button" class="btn btn-sm btn-outline-danger js-delete-wo-payment-inline" data-payment-id="${escapeText(pid)}">Delete</button>
              </td>
            </tr>
          `;
        }).join("");
      } catch (err) {
        workOrderDatesBlock.style.display = "";
        woMetaPaymentsBody.innerHTML = `<tr><td colspan="5" class="text-danger">Failed to load payment data.</td></tr>`;
      }
    }

    function setAssignError(message) {
      if (!assignMechanicsError) return;
      const text = String(message || "").trim();
      assignMechanicsError.textContent = text;
      assignMechanicsError.style.display = text ? "" : "none";
    }

    function renderAssignMechanicsRows(blockEl) {
      if (!assignMechanicsTbody) return;
      const assigned = getLaborAssignments(blockEl);
      const assignedMap = new Map(assigned.map((a) => [String(a.user_id), a]));

      assignMechanicsTbody.innerHTML = "";

      if (!Array.isArray(mechanicsData) || mechanicsData.length === 0) {
        if (assignMechanicsEmpty) assignMechanicsEmpty.style.display = "";
        if (assignMechanicsSaveBtn) assignMechanicsSaveBtn.disabled = true;
        return;
      }

      if (assignMechanicsEmpty) assignMechanicsEmpty.style.display = "none";
      if (assignMechanicsSaveBtn) assignMechanicsSaveBtn.disabled = false;

      mechanicsData.forEach((m) => {
        const id = String(m?.id || "").trim();
        if (!id) return;

        const existing = assignedMap.get(id);
        const isChecked = !!existing;
        const percent = isChecked
          ? (Number.isFinite(toNum(existing.percent)) ? round2(toNum(existing.percent)) : "")
          : "";

        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>
            <input type="checkbox" class="form-check-input mechanic-check" data-id="${escapeText(id)}" ${isChecked ? "checked" : ""}>
          </td>
          <td>${escapeText(String(m?.name || ""))}</td>
          <td>${escapeText(String(m?.role || ""))}</td>
          <td>
            <input type="number" class="form-control form-control-sm mechanic-percent" min="0" max="100" step="0.01" value="${escapeText(String(percent))}" ${isChecked ? "" : "disabled"}>
          </td>
        `;
        assignMechanicsTbody.appendChild(tr);
      });

      updateAssignPercentInputs(false);
    }

    function updateAssignPercentInputs(autoDistribute) {
      if (!assignMechanicsTbody) return;

      const rows = Array.from(assignMechanicsTbody.querySelectorAll("tr"));
      const selectedRows = rows.filter((tr) => tr.querySelector(".mechanic-check")?.checked);

      rows.forEach((tr) => {
        const check = tr.querySelector(".mechanic-check");
        const percentInput = tr.querySelector(".mechanic-percent");
        if (!check || !percentInput) return;

        if (!check.checked) {
          percentInput.disabled = true;
          percentInput.value = "";
          return;
        }

        if (selectedRows.length <= 1) {
          percentInput.disabled = true;
          percentInput.value = "";
        } else {
          percentInput.disabled = false;
        }
      });

      if (!autoDistribute || selectedRows.length <= 1) return;

      const totalHundredths = 10000;
      const count = selectedRows.length;
      const base = Math.floor(totalHundredths / count);
      let remainder = totalHundredths - (base * count);

      selectedRows.forEach((tr) => {
        const percentInput = tr.querySelector(".mechanic-percent");
        if (!percentInput) return;
        let valueHundredths = base;
        if (remainder > 0) {
          valueHundredths += 1;
          remainder -= 1;
        }
        percentInput.value = String((valueHundredths / 100).toFixed(2));
      });
    }

    assignMechanicsTbody?.addEventListener("change", function (e) {
      const check = e.target?.closest(".mechanic-check");
      if (!check) return;
      updateAssignPercentInputs(true);
      setAssignError("");
    });

    assignMechanicsTbody?.addEventListener("input", function () {
      setAssignError("");
    });

    let lastVinLookup = "";
    const debouncedVinLookup = debounce(async function () {
      if (!unitVinInput) return;
      const vin = String(unitVinInput.value || "").trim().toUpperCase();
      
      // Update input to uppercase
      if (unitVinInput.value !== vin) {
        unitVinInput.value = vin;
      }
      
      if (vin.length !== 17) {
        console.log("[VIN] Invalid length:", vin.length);
        if (vinLoadingSpinner) vinLoadingSpinner.style.display = "none";
        return;
      }
      
      if (vin === lastVinLookup) {
        console.log("[VIN] Already looked up:", vin);
        return;
      }
      
      lastVinLookup = vin;
      console.log("[VIN] Starting lookup for:", vin);

      // Show loading spinner
      if (vinLoadingSpinner) vinLoadingSpinner.style.display = "block";

      // Add visual feedback
      if (unitVinInput) {
        unitVinInput.style.borderColor = "#0d6efd";
        unitVinInput.style.backgroundColor = "#e7f1ff";
      }

      const data = await fetchVinDetails(vin);
      
      // Hide loading spinner
      if (vinLoadingSpinner) vinLoadingSpinner.style.display = "none";
      
      // Remove visual feedback
      if (unitVinInput) {
        unitVinInput.style.borderColor = "";
        unitVinInput.style.backgroundColor = "";
      }
      
      if (!data) {
        console.warn("[VIN] No data received for:", vin);
        // Show error state
        if (unitVinInput) {
          unitVinInput.style.borderColor = "#dc3545";
          setTimeout(() => {
            if (unitVinInput) unitVinInput.style.borderColor = "";
          }, 2000);
        }
        return;
      }

      console.log("[VIN] Autofilling fields with data:", data);
      
      if (unitMakeInput && data.make) {
        unitMakeInput.value = data.make;
        console.log("[VIN] Set Make:", data.make);
      }
      if (unitModelInput && data.model) {
        unitModelInput.value = data.model;
        console.log("[VIN] Set Model:", data.model);
      }
      if (unitYearInput && data.year) {
        unitYearInput.value = data.year;
        console.log("[VIN] Set Year:", data.year);
      }
      if (unitTypeInput && data.type) {
        unitTypeInput.value = data.type;
        console.log("[VIN] Set Type:", data.type);
      }
      
      // Flash success
      if (unitVinInput) {
        unitVinInput.style.borderColor = "#198754";
        setTimeout(() => {
          if (unitVinInput) unitVinInput.style.borderColor = "";
        }, 1500);
      }
    }, 500);

    function setEditorEnabled(enabled) {
      if (editor) editor.disabled = !enabled;
      if (hint) hint.style.display = enabled ? "none" : "";
    }

    function submitCreate() {
      if (!woForm) return;

      // ✅ перед отправкой формы на create кладём totals_json
      const totals = serializeTotals(blocksContainer);
      upsertHiddenJsonInput(woForm, "totals_json", totals);
      
      // ✅ Sync mileage to hidden field before submit
      if (unitMileageInput && unitMileageHidden) {
        unitMileageHidden.value = String(unitMileageInput.value || "").trim();
      }

      if (typeof woForm.requestSubmit === "function") woForm.requestSubmit();
      else woForm.submit();

      setButtonsState("creating", els);
    }

    // create still uses normal form POST
    createBtn?.addEventListener("click", submitCreate);

    unitVinInput?.addEventListener("input", debouncedVinLookup);
    unitVinInput?.addEventListener("blur", debouncedVinLookup);

    // ---------- restore initial saved data FIRST ----------
    const initialBlocks = readJsonScript("workOrderInitialData", []);
    applyInitialDataToUi(blocksContainer, initialBlocks);

    // wire + totals
    wireBlockEvents(blocksContainer, pricing, laborRates, shopSupplyPct);
    Array.from(blocksContainer.querySelectorAll(".wo-labor")).forEach((b, idx) => renumberBlock(b, idx));
    recalcAll(blocksContainer, pricing, laborRates, shopSupplyPct);
    
    // Render misc charges tables for all blocks
    Array.from(blocksContainer.querySelectorAll(".wo-labor")).forEach(blockEl => {
      renderMiscChargesTable(blockEl);
    });

    // -------- misc charges table event handlers --------
    blocksContainer.addEventListener("input", function (e) {
      const target = e.target;
      if (target.classList.contains("misc-desc-input") || 
          target.classList.contains("misc-qty-input") || 
          target.classList.contains("misc-price-input")) {
        const row = target.closest("tr");
        if (!row) return;
        const index = Number(row.dataset.miscIndex);
        const blockEl = target.closest(".wo-labor");
        if (!blockEl) return;
        
        const partsTable = blockEl.querySelector("table tbody.partsTbody");
        if (!partsTable) return;
        const firstRow = partsTable.querySelector("tr.parts-row");
        if (!firstRow) return;
        
        const descInput = row.querySelector(".misc-desc-input");
        const qtyInput = row.querySelector(".misc-qty-input");
        const priceInput = row.querySelector(".misc-price-input");
        const totalDisplay = row.querySelector(".misc-total-display");
        
        if (descInput && qtyInput && priceInput) {
          const newDesc = descInput.value;
          const newQty = qtyInput.value;
          const newPrice = priceInput.value;
          
          // Update total display immediately
          const qty = Number(newQty || 0);
          const price = toNum(newPrice);
          if (Number.isFinite(qty) && Number.isFinite(price)) {
            const total = round2(qty * price);
            if (totalDisplay) totalDisplay.textContent = `$${money(total)}`;
          }
          
          // Update data without re-rendering table (just update the hidden field)
          const items = getMiscItemsArray(firstRow);
          if (index >= 0 && index < items.length) {
            const desc = String(newDesc || "").trim();
            const finalQty = Number(newQty || 0);
            const finalPrice = toNum(newPrice);
            
            if (desc && Number.isFinite(finalQty) && finalQty >= 0 && Number.isFinite(finalPrice) && finalPrice >= 0) {
              items[index].description = desc;
              items[index].quantity = finalQty;
              items[index].price = round2(finalPrice);
              saveMiscItemsArray(firstRow, items);
              
              // Recalculate totals
              recalcAll(blocksContainer, pricing, laborRates, shopSupplyPct);
            }
          }
        }
      }
    });

    // taxable checkbox toggle in misc charges table
    blocksContainer.addEventListener("change", function (e) {
      const target = e.target;
      if (target.classList.contains("misc-taxable-input")) {
        const row = target.closest("tr");
        if (!row) return;
        const index = Number(row.dataset.miscIndex);
        const blockEl = target.closest(".wo-labor");
        if (!blockEl) return;
        const partsTable = blockEl.querySelector("table tbody.partsTbody");
        if (!partsTable) return;
        const firstRow = partsTable.querySelector("tr.parts-row");
        if (!firstRow) return;
        const items = getMiscItemsArray(firstRow);
        if (index >= 0 && index < items.length) {
          items[index].taxable = target.checked;
          saveMiscItemsArray(firstRow, items);
          recalcAll(blocksContainer, pricing, laborRates, shopSupplyPct);
        }
      }
    });

    blocksContainer.addEventListener("click", function (e) {
      // -------- delete part row --------
      const deletePartBtn = e.target.closest(".delete-part-row");
      if (deletePartBtn) {
        e.preventDefault();
        const row = deletePartBtn.closest("tr.parts-row");
        if (row) {
          const blockEl = row.closest(".wo-labor");
          
          // Remove only auto misc charges for THIS part row (keep manual charges and charges from other parts)
          if (blockEl) {
            const tbody = blockEl.querySelector(".partsTbody");
            const rows = Array.from(tbody.querySelectorAll("tr.parts-row"));
            const rowIndex = rows.indexOf(row);
            
            const firstRow = rows[0];
            if (firstRow && rowIndex >= 0) {
              const miscInput = firstRow.querySelector(".part-misc-charge-description");
              if (miscInput) {
                const items = getMiscItemsArray(firstRow);
                // Keep only: manual items + items from OTHER parts (not this row)
                const filteredItems = items.filter(item => 
                  item.manual === true || item.partIndex !== rowIndex
                );
                saveMiscItemsArray(firstRow, filteredItems);
              }
              
              // Also update baseline (remove auto items for this row)
              try {
                const baseline = JSON.parse(firstRow.dataset.autoMiscItemsBaseline || "[]");
                const filteredBaseline = baseline.filter(item => item.partIndex !== rowIndex);
                firstRow.dataset.autoMiscItemsBaseline = JSON.stringify(filteredBaseline);
              } catch (err) {
                // Ignore parsing errors
              }
              
              // Render the misc charges table (will show remaining charges)
              renderMiscChargesTable(blockEl);
            }
          }
          
          row.remove();
          recalcAll(blocksContainer, pricing, laborRates, shopSupplyPct);
        }
        return;
      }

      // -------- delete misc charge --------
      const btn = e.target.closest(".misc-delete-btn");
      if (!btn) return;
      
      e.preventDefault();
      const row = btn.closest("tr");
      if (!row) return;
      const index = Number(row.dataset.miscIndex);
      const blockEl = btn.closest(".wo-labor");
      if (!blockEl) return;
      
      const partsTable = blockEl.querySelector("table tbody.partsTbody");
      if (!partsTable) return;
      const firstRow = partsTable.querySelector("tr.parts-row");
      if (!firstRow) return;
      
      if (removeMiscChargeFromRow(firstRow, index)) {
        recalcAll(blocksContainer, pricing, laborRates, shopSupplyPct);
        renderMiscChargesTable(blockEl);
      }
    });

    // -------- misc charge modal --------
    const miscChargeModal = $("addMiscChargeModal");
    const miscChargeDescInput = $("miscChargeDescInput");
    const miscChargeQtyInput = $("miscChargeQtyInput");
    const miscChargePriceInput = $("miscChargePriceInput");
    const miscChargeTaxableInput = $("miscChargeTaxableInput");
    const miscChargeAddBtn = $("miscChargeAddBtn");
    let targetMiscBlock = null;

    function setupMiscChargeButton(blockDiv) {
      const btn = blockDiv.querySelector("#addMiscChargePartBtn");
      if (!btn) return;
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        targetMiscBlock = blockDiv;
        if (miscChargeDescInput) miscChargeDescInput.value = "";
        if (miscChargeQtyInput) miscChargeQtyInput.value = "1";
        if (miscChargePriceInput) miscChargePriceInput.value = "";
        if (miscChargeTaxableInput) miscChargeTaxableInput.checked = true;

        if (miscChargeModal && window.bootstrap && window.bootstrap.Modal) {
          const modal = window.bootstrap.Modal.getOrCreateInstance(miscChargeModal);
          modal.show();
        }
      });
    }

    Array.from(blocksContainer.querySelectorAll(".wo-labor")).forEach((block) => {
      setupMiscChargeButton(block);
    });


    miscChargeAddBtn?.addEventListener("click", function () {
      if (!targetMiscBlock) return;

      const partsTable = targetMiscBlock.querySelector("table tbody");
      if (!partsTable) return;
      const tr = partsTable.querySelector("tr.parts-row");
      if (!tr) return;

      const desc = miscChargeDescInput?.value || "";
      const qty = miscChargeQtyInput?.value || "1";
      const price = miscChargePriceInput?.value || "";
      const taxable = miscChargeTaxableInput ? miscChargeTaxableInput.checked : true;

      if (addMiscChargeToRow(tr, desc, qty, price, taxable)) {
        recalcAll(blocksContainer, pricing, laborRates, shopSupplyPct);
        
        const blockEl = targetMiscBlock;
        renderMiscChargesTable(blockEl);

        if (miscChargeModal && window.bootstrap && window.bootstrap.Modal) {
          const modal = window.bootstrap.Modal.getInstance(miscChargeModal);
          if (modal) modal.hide();
        }

        toast("Misc charge added.");
        targetMiscBlock = null;
      }
    });

    // initial enable state (before create)
    setEditorEnabled(!!(unitSel && String(unitSel.value || "").trim()));

    // customer/unit change (только пока НЕ создано)
    customerSel?.addEventListener("change", async function () {
      const customerId = String(customerSel.value || "").trim();
      currentCustomerTaxable = getCustomerTaxable(customersData, customerId);
      if (customerHidden) customerHidden.value = customerId;
      if (createUnitCustomerHidden) createUnitCustomerHidden.value = customerId;
      if (addUnitBtn) addUnitBtn.disabled = !customerId;

      const defaultRateCode = getCustomerDefaultLaborRate(customersData, customerId);
      applyDefaultLaborRateToAll(blocksContainer, defaultRateCode, false);
      recalcAll(blocksContainer, pricing, laborRates, shopSupplyPct);

      if (unitHidden) unitHidden.value = "";
      if (unitSel) {
        setSelectDisabled(unitSel, !customerId);
        setSelectOptions(unitSel, [], customerId ? "Loading…" : "-- Select unit --");
      }
      
      // Clear mileage when customer changes
      if (unitMileageInput) unitMileageInput.value = "";
      if (unitMileageHidden) unitMileageHidden.value = "";

      setEditorEnabled(false);

      if (!customerId || !unitSel) return;
      const units = await fetchUnits(customerId);
      setSelectOptions(unitSel, units, "-- Select unit --");
      setSelectDisabled(unitSel, false);

      if (Array.isArray(units) && units.length === 0) {
        const createUnitModalEl = $("createUnitModal");
        if (createUnitModalEl && window.bootstrap && window.bootstrap.Modal) {
          const modal = window.bootstrap.Modal.getOrCreateInstance(createUnitModalEl);
          modal.show();
        }
      }
    });

    unitSel?.addEventListener("change", async function () {
      const unitId = String(unitSel.value || "").trim();
      if (unitHidden) unitHidden.value = unitId;
      setEditorEnabled(!!unitId);
      
      // Clear mileage if no unit selected
      if (!unitId) {
        if (unitMileageInput) unitMileageInput.value = "";
        if (unitMileageHidden) unitMileageHidden.value = "";
        return;
      }
      
      // Fetch and display current mileage
      const unitDetails = await fetchUnitDetails(unitId);
      if (unitDetails) {
        if (unitMileageInput) unitMileageInput.value = unitDetails.mileage || "";
        if (unitMileageHidden) unitMileageHidden.value = unitDetails.mileage || "";
      }
    });

    // Update hidden mileage field when user changes the mileage input
    unitMileageInput?.addEventListener("change", function () {
      const mileageValue = String(unitMileageInput.value || "").trim();
      if (unitMileageHidden) unitMileageHidden.value = mileageValue;
    });

    // dropdown
    const dd = ensureDropdown();

    blocksContainer.addEventListener("click", function (e) {
      const assignBtn = e.target.closest(".laborAssignBtn");
      if (!assignBtn) return;

      const blockEl = assignBtn.closest(".wo-labor");
      if (!blockEl) return;

      targetAssignBlock = blockEl;
      renderAssignMechanicsRows(blockEl);
      setAssignError("");

      if (assignMechanicsModal && window.bootstrap && window.bootstrap.Modal) {
        const modal = window.bootstrap.Modal.getOrCreateInstance(assignMechanicsModal);
        modal.show();
      }
    });

    assignMechanicsSaveBtn?.addEventListener("click", function () {
      if (!targetAssignBlock || !assignMechanicsTbody) return;

      const rows = Array.from(assignMechanicsTbody.querySelectorAll("tr"));
      const selected = [];
      rows.forEach((tr) => {
        const check = tr.querySelector(".mechanic-check");
        if (!check || !check.checked) return;

        const id = String(check.dataset.id || "").trim();
        if (!id) return;

        const mechanic = Array.isArray(mechanicsData)
          ? mechanicsData.find((m) => String(m?.id || "") === id)
          : null;
        if (!mechanic) return;

        const percentInput = tr.querySelector(".mechanic-percent");
        const percent = toNum(percentInput?.value);
        selected.push({
          user_id: id,
          name: String(mechanic?.name || "").trim(),
          role: String(mechanic?.role || "").trim(),
          percent: Number.isFinite(percent) ? round2(percent) : 0,
        });
      });

      if (selected.length > 1) {
        const hasZero = selected.some((x) => !Number.isFinite(x.percent) || x.percent <= 0);
        if (hasZero) {
          setAssignError("For multiple mechanics, each percent must be greater than 0.");
          return;
        }
        const sum = round2(selected.reduce((acc, x) => acc + x.percent, 0));
        if (Math.abs(sum - 100) > 0.01) {
          setAssignError("For multiple mechanics, total percent must equal 100.");
          return;
        }
      }

      if (selected.length === 1 && (!Number.isFinite(selected[0].percent) || selected[0].percent <= 0)) {
        selected[0].percent = 100;
      }

      setLaborAssignments(targetAssignBlock, selected);

      if (assignMechanicsModal && window.bootstrap && window.bootstrap.Modal) {
        const modal = window.bootstrap.Modal.getInstance(assignMechanicsModal);
        if (modal) modal.hide();
      }
    });

    dd.addEventListener("mousedown", function (e) {
      const itemEl = e.target.closest(".parts-dd-item");
      if (!itemEl) return;
      e.preventDefault();

      const idx = Number(itemEl.dataset.idx);
      const it = dd._items?.[idx];
      const tr = dd._targetRow;
      const blockEl = dd._targetBlock;
      if (!it || !tr || !blockEl) return;

      if (it.__one_time_part) {
        fillRowAsOneTimePart(tr, dd._targetInput?.value || "");
      } else {
        fillRowFromPart(tr, it);
      }
      hideDropdown(dd);
      recalcAll(blocksContainer, pricing, laborRates, shopSupplyPct);
      renderMiscChargesTable(blockEl);
    });

    document.addEventListener("click", function (e) {
      if (!isWorkOrderPageAlive()) return;
      if (dd.style.display === "none") return;
      if (e.target.closest("#partsSearchDropdown")) return;
      hideDropdown(dd);
    });

    document.addEventListener("scroll", function () {
      if (!isWorkOrderPageAlive()) return;
      if (dd.style.display !== "none") hideDropdown(dd);
    }, { passive: true });

    function triggerPartsSearch(target) {
      if (!(target instanceof HTMLInputElement)) return;
      if (!(target.classList.contains("part-number") || target.classList.contains("part-description"))) return;

      const tr = target.closest("tr.parts-row");
      const blockEl = target.closest(".wo-labor");
      if (!tr || !blockEl) return;

      if (isOneTimePartRow(tr)) {
        hideDropdown(dd);
        return;
      }

      debouncedSearch(dd, target, tr, blockEl);
    }

    // bind search on inputs (part-number / part-description)
    blocksContainer.addEventListener("focusin", function (e) {
      const target = e.target;
      triggerPartsSearch(target);
    }, { passive: true });

    blocksContainer.addEventListener("input", function (e) {
      const target = e.target;
      triggerPartsSearch(target);
    });

    addLaborBtn?.addEventListener("click", function () {
      const cloned = cloneBlock(blocksContainer);
      setupMiscChargeButton(cloned);
      Array.from(blocksContainer.querySelectorAll(".wo-labor")).forEach((b, idx) => renumberBlock(b, idx));

      const customerId = String(customerSel?.value || "").trim();
      const defaultRateCode = getCustomerDefaultLaborRate(customersData, customerId);
      applyDefaultLaborRateToBlock(cloned, defaultRateCode, true);

      recalcAll(blocksContainer, pricing, laborRates, shopSupplyPct);
    });

    // ---------- created/paid state ----------
    let workOrderId = "";
    let workOrderStatus = "open"; // "open" | "paid"
    let isCreated = false;
    let customerEmail = "";

    const createdInfo = readJsonScript("workOrderCreatedData", { created: false, id: "", status: "open" });
    if (createdInfo && createdInfo.created && createdInfo.id) {
      isCreated = true;
      workOrderId = String(createdInfo.id);
      workOrderStatus = String(createdInfo.status || "open").trim().toLowerCase();
    }

    function applyStateFromStatus() {
      if (!isCreated) {
        // до create: только create, форма активна по unit
        setButtonsState("created_locked_open", els); // не показываем
        if (createBtn) createBtn.style.display = "";
        if (editBtn) editBtn.style.display = "none";
        if (saveBtn) saveBtn.style.display = "none";
        if (paidBtn) paidBtn.style.display = "none";
        if (unpaidBtn) unpaidBtn.style.display = "none";
        return;
      }

      // created: customer/unit больше не меняем
      setSelectDisabled(customerSel, true);
      setSelectDisabled(unitSel, true);
      if (addUnitBtn) addUnitBtn.disabled = true;

      if (workOrderStatus === "paid") {
        setEditingMode(false, els);
        setButtonsState("paid", els);
      } else {
        setEditingMode(false, els);
        setButtonsState("created_locked_open", els);
      }
    }

    // edit -> enable
    editBtn?.addEventListener("click", function () {
      if (!isCreated || !workOrderId) return;
      if (workOrderStatus === "paid") return;

      setEditingMode(true, els);
      setButtonsState("editing_open", els);
    });

    // save -> API update labors + totals
    saveBtn?.addEventListener("click", async function () {
      if (!isCreated || !workOrderId) return;

      try {
        const labors = serializeBlocks(blocksContainer);
        const totals = serializeTotals(blocksContainer);
        
        // ✅ Get current mileage from input
        const unit_mileage = unitMileageInput ? String(unitMileageInput.value || "").trim() : "";

        await apiPostJson(
          `/work_orders/api/work_orders/${encodeURIComponent(workOrderId)}/update`,
          {
            labors,
            totals,
            unit_mileage: unit_mileage ? unit_mileage : undefined,
            work_order_date: workOrderDateInput ? String(workOrderDateInput.value || "").trim() : undefined,
          }
        );

        // после сохранения снова лочим
        setEditingMode(false, els);
        setButtonsState("created_locked_open", els);
        toast("Saved.");
      } catch (e) {
        toast(e.message || "Save failed.");
      }
    });

    // paid -> open payment modal
    paidBtn?.addEventListener("click", async function () {
      if (!isCreated || !workOrderId) return;

      try {
        // Fetch current payment info
        const res = await fetch(`/work_orders/api/work_orders/${encodeURIComponent(workOrderId)}/payments`, {
          method: "GET",
          headers: { "Accept": "application/json" }
        });
        const data = await res.json();

        if (!data.ok) throw new Error(data.error || "Failed to load payment info");

        // Update modal with payment info
        document.getElementById("paymentInvoiceTotal").textContent = `$${(data.grand_total || 0).toFixed(2)}`;
        document.getElementById("paymentAlreadyPaid").textContent = `$${(data.paid_amount || 0).toFixed(2)}`;
        document.getElementById("paymentRemainingBalance").textContent = `$${(data.remaining_balance || 0).toFixed(2)}`;

        // Pre-fill amount with remaining balance
        const remainingBalance = data.remaining_balance || 0;
        document.getElementById("paymentAmountInput").value = remainingBalance > 0 ? remainingBalance.toFixed(2) : "";
        document.getElementById("paymentMethodInput").value = "cash";
        document.getElementById("paymentNotesInput").value = "";
        if (paymentDateInput) {
          paymentDateInput.value = paymentDateInput.defaultValue || paymentDateInput.value || "";
        }

        // Show modal
        const modal = new bootstrap.Modal(document.getElementById("paymentModal"));
        modal.show();
      } catch (e) {
        toast(e.message || "Failed to load payment info.");
      }
    });

    // unpaid -> back to open and show edit again
    unpaidBtn?.addEventListener("click", async function () {
      if (!isCreated || !workOrderId) return;

      try {
        await apiPostJson(`/work_orders/api/work_orders/${encodeURIComponent(workOrderId)}/status`, { status: "open" });
        workOrderStatus = "open";
        applyStateFromStatus();
        await loadWorkOrderTimeline();
        toast("Marked as unpaid.");
      } catch (e) {
        toast(e.message || "Failed to set unpaid.");
      }
    });

    document.addEventListener("click", async function (event) {
      const btn = event.target.closest(".js-delete-wo-payment-inline");
      if (!btn) return;
      if (!isCreated || !workOrderId) return;

      const paymentId = String(btn.dataset.paymentId || "").trim();
      if (!paymentId) return;
      if (!await appConfirm("Delete this payment?")) return;

      const originalText = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Deleting...";

      try {
        const res = await fetch(`/work_orders/api/payments/${encodeURIComponent(paymentId)}/delete`, {
          method: "POST",
          headers: { "Accept": "application/json" },
        });
        const data = await res.json();
        if (!res.ok || !data || !data.ok) {
          throw new Error((data && (data.error || data.message)) || "Failed to delete payment");
        }

        workOrderStatus = (data.status || "open").toLowerCase();
        applyStateFromStatus();
        await loadWorkOrderTimeline();
      } catch (err) {
        toast(err.message || "Failed to delete payment.");
        btn.disabled = false;
        btn.textContent = originalText;
      }
    });

    async function askEmailAddress(defaultEmail, title) {
      const result = await Swal.fire({
        title: title || "Send Email",
        input: "email",
        inputValue: defaultEmail || "",
        inputPlaceholder: "customer@example.com",
        showCancelButton: true,
        confirmButtonText: "Send",
        confirmButtonColor: "#1f6b43",
        cancelButtonColor: "#6c757d",
        showClass: { popup: "", backdrop: "" },
        hideClass: { popup: "", backdrop: "" },
      });
      if (!result.isConfirmed) return null;
      return String(result.value || "").trim();
    }

    if (emailBtn) {
      emailBtn.addEventListener("click", async function () {
        if (!isCreated || !workOrderId) return;
        const addr = await askEmailAddress(customerEmail, "Send Work Order");
        if (!addr) return;

        emailBtn.disabled = true;
        const originalText = emailBtn.textContent;
        emailBtn.textContent = "Sending…";

        try {
          const res = await fetch(`/work_orders/api/work_orders/${encodeURIComponent(workOrderId)}/send-email`, {
            method: "POST",
            headers: { "Accept": "application/json", "Content-Type": "application/json" },
            body: JSON.stringify({ email: addr }),
          });
          const data = await res.json();
          if (!res.ok || !data || !data.ok) {
            throw new Error((data && (data.error || data.message)) || "Failed to send email");
          }
          toast(`Work order sent to ${data.sent_to}`, "success");
        } catch (err) {
          toast(err.message || "Failed to send email.", "error");
        } finally {
          emailBtn.disabled = false;
          emailBtn.textContent = originalText;
        }
      });
    }

    document.addEventListener("click", async function (event) {
      const btn = event.target.closest(".js-email-wo-payment-inline");
      if (!btn) return;
      if (!isCreated || !workOrderId) return;

      const paymentId = String(btn.dataset.paymentId || "").trim();
      if (!paymentId) return;

      const addr = await askEmailAddress(customerEmail, "Send Payment Receipt");
      if (!addr) return;

      const originalText = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Sending…";

      try {
        const res = await fetch(`/work_orders/api/payments/${encodeURIComponent(paymentId)}/send-receipt`, {
          method: "POST",
          headers: { "Accept": "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({ email: addr }),
        });
        const data = await res.json();
        if (!res.ok || !data || !data.ok) {
          throw new Error((data && (data.error || data.message)) || "Failed to send receipt");
        }
        toast(`Receipt sent to ${data.sent_to}`, "success");
      } catch (err) {
        toast(err.message || "Failed to send receipt.", "error");
        btn.disabled = false;
        btn.textContent = originalText;
      }
    });

    // initial state
    if (addUnitBtn) {
      const initialCustomerId = String(customerSel?.value || "").trim();
      addUnitBtn.disabled = !initialCustomerId;
    }

    const initialCustomerId = String(customerSel?.value || "").trim();
    currentCustomerTaxable = getCustomerTaxable(customersData, initialCustomerId);
    const initialDefaultRateCode = getCustomerDefaultLaborRate(customersData, initialCustomerId);
    applyDefaultLaborRateToAll(blocksContainer, initialDefaultRateCode, true);
    recalcAll(blocksContainer, pricing, laborRates, shopSupplyPct);

    if (isCreated) {
      applyTotalsSnapshotToUi(blocksContainer, totalsSnapshot, shopSupplyPct);
      
      // ✅ Load unit mileage when editing an existing work order
      (async () => {
        const selectedUnitId = String(unitSel?.value || "").trim();
        if (selectedUnitId) {
          const unitDetails = await fetchUnitDetails(selectedUnitId);
          if (unitDetails) {
            if (unitMileageInput) {
              unitMileageInput.value = unitDetails.mileage || "";
              if (unitMileageHidden) unitMileageHidden.value = unitDetails.mileage || "";
            }
          }
        }
      })();

      loadWorkOrderTimeline();
    }

    // Payment modal handler
    const paymentSubmitBtn = $("paymentSubmitBtn");
    paymentSubmitBtn?.addEventListener("click", async function () {
      if (!isCreated || !workOrderId) return;

      const amount = parseFloat(document.getElementById("paymentAmountInput").value || "0");
      const paymentMethod = document.getElementById("paymentMethodInput").value;
      const notes = document.getElementById("paymentNotesInput").value;
      const paymentDate = paymentDateInput ? String(paymentDateInput.value || "").trim() : "";

      if (amount <= 0) {
        toast("Please enter a valid payment amount.");
        return;
      }

      if (!paymentDate) {
        toast("Please select payment date.");
        return;
      }

      const btn = this;
      const originalText = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Saving...";

      try {
        const res = await fetch(`/work_orders/api/work_orders/${encodeURIComponent(workOrderId)}/payment`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "Accept": "application/json" },
          body: JSON.stringify({
            amount,
            payment_method: paymentMethod,
            notes,
            payment_date: paymentDate
          })
        });

        const data = await res.json();
        if (!data.ok) throw new Error(data.message || data.error || "Failed to record payment");

        // Close modal
        const modal = bootstrap.Modal.getInstance(document.getElementById("paymentModal"));
        modal.hide();

        // If fully paid, update button states
        if (data.is_fully_paid) {
          workOrderStatus = "paid";
          applyStateFromStatus();
        } else {
          workOrderStatus = "open";
          applyStateFromStatus();
        }

        await loadWorkOrderTimeline();

        toast("Payment recorded successfully!");
      } catch (e) {
        toast(e.message || "Failed to record payment.");
      } finally {
        btn.disabled = false;
        btn.textContent = originalText;
      }
    });

    applyStateFromStatus();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initWorkOrderDetailsPage, { once: true });
  } else {
    initWorkOrderDetailsPage();
  }
})();
