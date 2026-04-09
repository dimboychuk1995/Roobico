(function () {
  "use strict";

  var modalEl = document.getElementById("presetModal");
  var form = document.getElementById("presetForm");
  var modalTitle = document.getElementById("presetModalTitle");
  var saveBtn = document.getElementById("presetSaveBtn");
  var partsTbody = document.getElementById("presetPartsTbody");
  var partsJsonInput = document.getElementById("presetPartsJson");
  var addPartBtn = document.getElementById("addPresetPartBtn");
  var addPresetBtn = document.getElementById("addPresetBtn");
  var dropdown = document.getElementById("presetPartsDropdown");

  if (!modalEl || !form) return;

  var bsModal = null;
  function getModal() {
    if (!bsModal) bsModal = new bootstrap.Modal(modalEl);
    return bsModal;
  }

  /* ── helpers ── */

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function toNum(v) {
    var n = parseFloat(v);
    return isFinite(n) ? n : null;
  }

  function money(v) {
    return (toNum(v) || 0).toFixed(2);
  }

  function round2(v) {
    return Math.round(v * 100) / 100;
  }

  /* ── pricing rules calc ── */

  function matchPricingRule(cost, rules) {
    if (!isFinite(cost) || !Array.isArray(rules)) return null;
    for (var i = 0; i < rules.length; i++) {
      var r = rules[i];
      var from = toNum(r.from);
      var to = (r.to === null || r.to === undefined) ? null : toNum(r.to);
      var vp = toNum(r.value_percent);
      if (from === null || vp === null) continue;
      if (cost < from) continue;
      if (to === null) return { value_percent: vp };
      if (cost <= to) return { value_percent: vp };
    }
    return null;
  }

  function calcPriceFromCost(cost, pricing) {
    if (!pricing || !Array.isArray(pricing.rules) || !pricing.rules.length) return null;
    if (!isFinite(cost) || cost <= 0) return null;
    var rule = matchPricingRule(cost, pricing.rules);
    if (!rule) return null;
    var vp = rule.value_percent / 100;
    if (pricing.mode === "markup") return round2(cost * (1 + vp));
    var denom = 1 - vp;
    if (denom <= 0) return round2(cost);
    return round2(cost / denom);
  }

  /* ── estimate summary (read-only) ── */

  var estLabor = document.getElementById("presetEstLabor");
  var estParts = document.getElementById("presetEstParts");
  var estTotal = document.getElementById("presetEstTotal");

  function getStandardRate() {
    if (!Array.isArray(LABOR_RATES_DATA)) return 0;
    for (var i = 0; i < LABOR_RATES_DATA.length; i++) {
      if (LABOR_RATES_DATA[i].code === "standard") return toNum(LABOR_RATES_DATA[i].hourly_rate) || 0;
    }
    return LABOR_RATES_DATA.length ? (toNum(LABOR_RATES_DATA[0].hourly_rate) || 0) : 0;
  }

  function getRateByCode(code) {
    if (!code || !Array.isArray(LABOR_RATES_DATA)) return null;
    for (var i = 0; i < LABOR_RATES_DATA.length; i++) {
      if (LABOR_RATES_DATA[i].code === code) return toNum(LABOR_RATES_DATA[i].hourly_rate) || 0;
    }
    return null;
  }

  function recalcEstimate() {
    var hoursEl = document.getElementById("presetLaborHours");
    var rateEl = document.getElementById("presetLaborRate");
    var hours = toNum(hoursEl ? hoursEl.value : "") || 0;
    var rateCode = rateEl ? rateEl.value.trim() : "";
    var rate = getRateByCode(rateCode);
    if (rate === null) rate = getStandardRate();

    var laborTotal = round2(hours * rate);
    var partsTotal = getPartsTableSum();
    var miscTotal = getMiscChargesSum();
    var grandTotal = round2(laborTotal + partsTotal + miscTotal);

    if (estLabor) estLabor.textContent = "$" + money(laborTotal);
    if (estParts) estParts.textContent = "$" + money(partsTotal) + (miscTotal > 0 ? " (+ $" + money(miscTotal) + " misc)" : "");
    if (estTotal) estTotal.textContent = "$" + money(grandTotal);
  }

  function getMiscChargesSum() {
    var total = 0;
    var rows = partsTbody.querySelectorAll("tr.preset-misc-row");
    for (var i = 0; i < rows.length; i++) {
      var qty = toNum(rows[i].dataset.parentQty) || 1;
      var price = toNum(rows[i].dataset.miscPrice) || 0;
      total += qty * price;
    }
    return round2(total);
  }

  function recalcLineTotals() {
    var rows = partsTbody.querySelectorAll("tr.preset-part-row");
    for (var i = 0; i < rows.length; i++) {
      var qty = toNum(rows[i].querySelector(".pp-qty").value) || 0;
      var price = toNum(rows[i].querySelector(".pp-price").value) || 0;
      var lineEl = rows[i].querySelector(".pp-line-total");
      if (lineEl) lineEl.textContent = "$" + money(qty * price);
    }
  }

  function getPartsTableSum() {
    var total = 0;
    var rows = partsTbody.querySelectorAll("tr.preset-part-row");
    for (var i = 0; i < rows.length; i++) {
      var qty = toNum(rows[i].querySelector(".pp-qty").value) || 0;
      var price = toNum(rows[i].querySelector(".pp-price").value) || 0;
      total += qty * price;
    }
    return total;
  }

  /* Called when parts table rows change (add/remove/edit qty/price) */
  function updatePartsFromTable() {
    recalcLineTotals();
    recalcEstimate();
  }

  /* ── parts list state ── */

  var partsState = [];
  var partRowIndex = 0;
  var searchTimer = null;
  var activeSearchInput = null;
  var activeSearchRow = null;

  function syncPartsJson() {
    collectPartsFromDOM();
    partsJsonInput.value = JSON.stringify(partsState);
  }

  function collectPartsFromDOM() {
    partsState = [];
    var rows = partsTbody.querySelectorAll("tr.preset-part-row");
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var miscCharges = [];
      // Collect misc rows right after this part row
      var next = r.nextElementSibling;
      while (next && next.classList.contains("preset-misc-row")) {
        miscCharges.push({
          description: next.dataset.miscDesc || "",
          price: toNum(next.dataset.miscPrice) || 0,
        });
        next = next.nextElementSibling;
      }
      partsState.push({
        part_id: r.querySelector(".pp-part-id").value || null,
        part_number: r.querySelector(".pp-part-number").value.trim(),
        description: r.querySelector(".pp-description").value.trim(),
        qty: toNum(r.querySelector(".pp-qty").value) || 1,
        cost: toNum(r.querySelector(".pp-cost").value) || 0,
        price: toNum(r.querySelector(".pp-price").value) || 0,
        misc_charges: miscCharges.length ? miscCharges : undefined,
      });
    }
  }

  function renderPartsRows(parts) {
    partsTbody.innerHTML = "";
    partRowIndex = 0;
    if (!parts || !parts.length) return;
    for (var i = 0; i < parts.length; i++) {
      addPartRow(parts[i]);
    }
  }

  function addPartRow(data) {
    var idx = partRowIndex++;
    var tr = document.createElement("tr");
    tr.className = "preset-part-row";
    tr.dataset.idx = idx;

    var partId = (data && data.part_id) || "";
    var partNum = (data && data.part_number) || "";
    var desc = (data && data.description) || "";
    var qty = (data && data.qty) || 1;
    var cost = (data && data.cost) || "";
    var price = (data && data.price) || "";
    var lineTotal = (toNum(qty) || 0) * (toNum(price) || 0);

    tr.innerHTML =
      '<td class="p-1"><input type="hidden" class="pp-part-id" value="' + escapeHtml(partId) + '">' +
      '<input class="form-control form-control-sm pp-part-number" value="' + escapeHtml(partNum) + '" maxlength="64" autocomplete="off" placeholder="Search parts\u2026"></td>' +
      '<td class="p-1"><input class="form-control form-control-sm pp-description" value="' + escapeHtml(desc) + '" maxlength="200" autocomplete="off"></td>' +
      '<td class="p-1"><input class="form-control form-control-sm pp-qty" value="' + qty + '" inputmode="numeric" min="1"></td>' +
      '<td class="p-1"><input class="form-control form-control-sm pp-cost bg-light" value="' + (cost || '') + '" readonly tabindex="-1"></td>' +
      '<td class="p-1"><input class="form-control form-control-sm pp-price" value="' + (price || '') + '" inputmode="decimal" step="0.01" min="0" placeholder="0.00"></td>' +
      '<td class="p-1 align-middle pp-line-total">$' + money(lineTotal) + '</td>' +
      '<td class="p-1 text-center"><button type="button" class="btn btn-sm btn-outline-danger pp-delete">&times;</button></td>';

    partsTbody.appendChild(tr);

    // Render misc charge rows for this part
    var miscCharges = (data && data.misc_charges) || [];
    for (var m = 0; m < miscCharges.length; m++) {
      addMiscRow(miscCharges[m], qty, idx);
    }

    return tr;
  }

  function createMiscRow(misc, parentQty, parentIdx) {
    var mtr = document.createElement("tr");
    mtr.className = "preset-misc-row text-muted";
    mtr.dataset.parentIdx = parentIdx;
    mtr.dataset.miscDesc = misc.description || "";
    mtr.dataset.miscPrice = misc.price || 0;
    mtr.dataset.parentQty = parentQty || 1;
    var lineTotal = (toNum(parentQty) || 1) * (toNum(misc.price) || 0);
    mtr.innerHTML =
      '<td class="p-1 ps-4" colspan="4"><small><i class="bi bi-arrow-return-right me-1"></i>Misc: ' + escapeHtml(misc.description || "") + '</small></td>' +
      '<td class="p-1"><small>$' + money(misc.price || 0) + '</small></td>' +
      '<td class="p-1"><small>$' + money(lineTotal) + '</small></td>' +
      '<td></td>';
    return mtr;
  }

  function addMiscRow(misc, parentQty, parentIdx) {
    var mtr = createMiscRow(misc, parentQty, parentIdx);
    partsTbody.appendChild(mtr);
  }

  function removeMiscRowsForPart(partRow) {
    var next = partRow.nextElementSibling;
    while (next && next.classList.contains("preset-misc-row")) {
      var toRemove = next;
      next = next.nextElementSibling;
      toRemove.remove();
    }
  }

  function updateMiscRowsQty(partRow) {
    var qty = toNum(partRow.querySelector(".pp-qty").value) || 1;
    var next = partRow.nextElementSibling;
    while (next && next.classList.contains("preset-misc-row")) {
      next.dataset.parentQty = qty;
      var price = toNum(next.dataset.miscPrice) || 0;
      var cells = next.querySelectorAll("td small");
      if (cells.length >= 3) {
        cells[2].textContent = "$" + money(qty * price);
      }
      next = next.nextElementSibling;
    }
  }

  /* ── add part ── */
  addPartBtn.addEventListener("click", function () {
    addPartRow(null);
  });

  /* ── delete part row ── */
  partsTbody.addEventListener("click", function (e) {
    var btn = e.target.closest(".pp-delete");
    if (!btn) return;
    var tr = btn.closest("tr");
    removeMiscRowsForPart(tr);
    tr.remove();
    syncPartsJson();
    updatePartsFromTable();
  });

  /* ── recalc on qty/price change in parts table ── */
  partsTbody.addEventListener("input", function (e) {
    if (e.target.classList.contains("pp-qty")) {
      var tr = e.target.closest("tr.preset-part-row");
      if (tr) updateMiscRowsQty(tr);
      updatePartsFromTable();
    } else if (e.target.classList.contains("pp-price")) {
      updatePartsFromTable();
    }
  });

  /* ── recalc estimate on labor hours/rate change ── */
  var laborHoursInput = document.getElementById("presetLaborHours");
  var laborRateSelect = document.getElementById("presetLaborRate");
  if (laborHoursInput) laborHoursInput.addEventListener("input", recalcEstimate);
  if (laborRateSelect) laborRateSelect.addEventListener("change", recalcEstimate);

  /* ── parts search ── */

  function hideDropdown() {
    dropdown.style.display = "none";
    dropdown.innerHTML = "";
  }

  function placeDropdown(inputEl) {
    var rect = inputEl.getBoundingClientRect();
    dropdown.style.top = (rect.bottom + window.scrollY) + "px";
    dropdown.style.left = rect.left + "px";
    dropdown.style.width = Math.max(rect.width, 320) + "px";
    dropdown.style.display = "block";
  }

  function fetchParts(q) {
    return fetch(PARTS_SEARCH_URL + "?q=" + encodeURIComponent(q) + "&limit=15", {
      headers: { "Accept": "application/json" },
    })
      .then(function (r) { return r.ok ? r.json() : { items: [] }; })
      .then(function (d) { return Array.isArray(d.items) ? d.items : []; })
      .catch(function () { return []; });
  }

  function renderSearchResults(items) {
    if (!items.length) {
      dropdown.innerHTML = '<div style="padding:10px; color:#6c757d;">No matches</div>';
      return;
    }
    dropdown.innerHTML = items.map(function (it, idx) {
      var title = (it.part_number || "") + " \u2014 " + (it.description || "");
      var meta = "Stock: " + (it.in_stock || 0) + " \u00b7 Avg cost: $" + money(it.average_cost);
      if (it.misc_has_charge && Array.isArray(it.misc_charges) && it.misc_charges.length) {
        meta += " \u00b7 Misc charges: " + it.misc_charges.length;
      }
      return '<div class="pp-dd-item" data-idx="' + idx + '" style="padding:8px 12px; cursor:pointer; border-bottom:1px solid rgba(0,0,0,.06);">' +
        '<div style="font-weight:600; line-height:1.2;">' + escapeHtml(title) + '</div>' +
        '<div style="font-size:12px; color:#6c757d; margin-top:2px;">' + escapeHtml(meta) + '</div>' +
        '</div>';
    }).join("");
    dropdown._items = items;
  }

  partsTbody.addEventListener("input", function (e) {
    var inp = e.target;
    if (!inp.classList.contains("pp-part-number") && !inp.classList.contains("pp-description")) return;

    var tr = inp.closest("tr");
    activeSearchInput = inp;
    activeSearchRow = tr;

    clearTimeout(searchTimer);
    var q = inp.value.trim();
    if (q.length < 3) {
      hideDropdown();
      return;
    }

    searchTimer = setTimeout(function () {
      placeDropdown(inp);
      dropdown.innerHTML = '<div style="padding:10px; color:#6c757d;">Searching\u2026</div>';
      fetchParts(q).then(renderSearchResults);
    }, 200);
  });

  dropdown.addEventListener("mousedown", function (e) {
    var itemEl = e.target.closest(".pp-dd-item");
    if (!itemEl) return;
    e.preventDefault();

    var idx = Number(itemEl.dataset.idx);
    var it = dropdown._items && dropdown._items[idx];
    if (!it || !activeSearchRow) return;

    activeSearchRow.querySelector(".pp-part-id").value = it._id || it.id || "";
    activeSearchRow.querySelector(".pp-part-number").value = it.part_number || "";
    activeSearchRow.querySelector(".pp-description").value = it.description || "";

    // Fill cost
    var costInput = activeSearchRow.querySelector(".pp-cost");
    var avgCost = toNum(it.average_cost);
    if (costInput && avgCost !== null) {
      costInput.value = money(avgCost);
    }

    // Auto-fill price: use selling_price if set, otherwise calc from pricing rules
    var priceInput = activeSearchRow.querySelector(".pp-price");
    if (priceInput) {
      var autoPrice = null;
      if (it.has_selling_price && toNum(it.selling_price) > 0) {
        autoPrice = toNum(it.selling_price);
      } else {
        var cost = toNum(it.average_cost);
        if (cost !== null && cost > 0 && typeof PRICING_RULES !== "undefined" && PRICING_RULES) {
          autoPrice = calcPriceFromCost(cost, PRICING_RULES);
        }
      }
      if (autoPrice !== null) {
        priceInput.value = money(autoPrice);
      }
    }

    // Add misc charge rows if part has them
    removeMiscRowsForPart(activeSearchRow);
    if (it.misc_has_charge && Array.isArray(it.misc_charges) && it.misc_charges.length) {
      var parentIdx = activeSearchRow.dataset.idx;
      var qty = toNum(activeSearchRow.querySelector(".pp-qty").value) || 1;
      var insertAfter = activeSearchRow;
      for (var mi = 0; mi < it.misc_charges.length; mi++) {
        var mtr = createMiscRow(it.misc_charges[mi], qty, parentIdx);
        insertAfter.parentNode.insertBefore(mtr, insertAfter.nextSibling);
        insertAfter = mtr;
      }
    }

    hideDropdown();
    syncPartsJson();
    updatePartsFromTable();
  });

  document.addEventListener("click", function (e) {
    if (!dropdown.contains(e.target) && e.target !== activeSearchInput) {
      hideDropdown();
    }
  });

  /* ── open modal for create ── */

  addPresetBtn.addEventListener("click", function () {
    form.action = CREATE_URL;
    modalTitle.textContent = "New Preset";
    saveBtn.textContent = "Create";
    form.reset();
    partsState = [];
    renderPartsRows([]);
    syncPartsJson();
    recalcEstimate();
  });

  /* ── open modal for edit ── */

  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".edit-preset-btn");
    if (!btn) return;

    var presetId = btn.dataset.presetId;
    var url = DETAIL_URL_TPL.replace("__ID__", presetId);

    fetch(url, { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        form.action = UPDATE_URL_TPL.replace("__ID__", presetId);
        modalTitle.textContent = "Edit Preset";
        saveBtn.textContent = "Save";

        document.getElementById("presetName").value = data.name || "";
        document.getElementById("presetDescription").value = data.description || "";
        document.getElementById("presetLaborHours").value = data.labor_hours != null ? data.labor_hours : "";
        document.getElementById("presetLaborRate").value = data.labor_rate_code || "";
        document.getElementById("presetAllowDiscount").checked = !!data.allow_discount;

        partsState = data.parts || [];
        renderPartsRows(partsState);
        syncPartsJson();
        recalcEstimate();

        getModal().show();
      })
      .catch(function (err) {
        console.error("Failed to load preset:", err);
        alert("Failed to load preset data.");
      });
  });

  /* ── sync parts JSON before submit ── */

  form.addEventListener("submit", function () {
    syncPartsJson();
  });

})();
