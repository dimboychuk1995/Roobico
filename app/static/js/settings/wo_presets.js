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

  /* ── pricing summary (2-of-3 auto-calc) ── */

  var fLabor = document.getElementById("presetFixedLabor");
  var fParts = document.getElementById("presetFixedParts");
  var fTotal = document.getElementById("presetFixedTotal");
  var pricingHint = document.getElementById("pricingSummaryHint");
  var autoField = null;   // "labor" | "parts" | "total" | null
  var insideAutoCalc = false;

  function getFieldVal(field) {
    var el = field === "labor" ? fLabor : field === "parts" ? fParts : fTotal;
    var v = toNum(el.value);
    return (v !== null && v >= 0) ? v : null;
  }

  function setFieldVal(field, value) {
    var el = field === "labor" ? fLabor : field === "parts" ? fParts : fTotal;
    el.value = money(value);
  }

  function highlightAutoField() {
    var fields = [
      { el: fLabor, name: "labor" },
      { el: fParts, name: "parts" },
      { el: fTotal, name: "total" }
    ];
    for (var i = 0; i < fields.length; i++) {
      fields[i].el.style.backgroundColor = (fields[i].name === autoField) ? "#e8f4fd" : "";
    }
    if (pricingHint) {
      if (autoField) {
        var label = autoField === "labor" ? "Labor Price" : autoField === "parts" ? "Parts Total" : "Grand Total";
        pricingHint.textContent = label + " is auto-calculated. Clear any field to change the calculation.";
        pricingHint.style.display = "";
      } else {
        pricingHint.style.display = "none";
      }
    }
  }

  function calcField(field) {
    var labor = getFieldVal("labor");
    var parts = getFieldVal("parts");
    var total = getFieldVal("total");

    if (field === "total" && labor !== null && parts !== null) {
      setFieldVal("total", round2(labor + parts));
    } else if (field === "parts" && labor !== null && total !== null) {
      var v = round2(total - labor);
      if (v < 0) v = 0;
      setFieldVal("parts", v);
      scalePartPrices(v);
    } else if (field === "labor" && parts !== null && total !== null) {
      var v = round2(total - parts);
      if (v < 0) v = 0;
      setFieldVal("labor", v);
    }
    autoField = field;
    highlightAutoField();
  }

  function tryAutoCalc(justEdited) {
    if (insideAutoCalc) return;
    insideAutoCalc = true;

    var labor = getFieldVal("labor");
    var parts = getFieldVal("parts");
    var total = getFieldVal("total");

    var filled = [];
    if (labor !== null) filled.push("labor");
    if (parts !== null) filled.push("parts");
    if (total !== null) filled.push("total");

    if (filled.length < 2) {
      autoField = null;
      highlightAutoField();
      insideAutoCalc = false;
      return;
    }

    if (filled.length === 2) {
      var all = ["labor", "parts", "total"];
      var empty = null;
      for (var i = 0; i < all.length; i++) {
        if (filled.indexOf(all[i]) === -1) { empty = all[i]; break; }
      }
      calcField(empty);
      insideAutoCalc = false;
      return;
    }

    // All 3 filled — recalculate autoField if it differs from justEdited
    if (autoField && autoField !== justEdited) {
      calcField(autoField);
      insideAutoCalc = false;
      return;
    }

    // autoField is null or equals justEdited → pick new auto
    // Priority: total first (least destructive), then parts, then labor
    var priority = ["total", "parts", "labor"];
    for (var i = 0; i < priority.length; i++) {
      if (priority[i] !== justEdited) {
        calcField(priority[i]);
        break;
      }
    }
    insideAutoCalc = false;
  }

  function scalePartPrices(targetTotal) {
    var rows = partsTbody.querySelectorAll("tr.preset-part-row");
    if (!rows.length || targetTotal <= 0) return;

    // Distribute target total proportionally to cost × qty
    var totalCostWeighted = 0;
    for (var i = 0; i < rows.length; i++) {
      var qty = toNum(rows[i].querySelector(".pp-qty").value) || 0;
      var cost = toNum(rows[i].querySelector(".pp-cost").value) || 0;
      totalCostWeighted += cost * qty;
    }

    // Fallback: if no costs, distribute evenly
    if (totalCostWeighted <= 0) {
      var perRow = targetTotal / rows.length;
      for (var i = 0; i < rows.length; i++) {
        var qty = toNum(rows[i].querySelector(".pp-qty").value) || 1;
        rows[i].querySelector(".pp-price").value = money(round2(perRow / qty));
      }
      recalcLineTotals();
      return;
    }

    for (var i = 0; i < rows.length; i++) {
      var qty = toNum(rows[i].querySelector(".pp-qty").value) || 1;
      var cost = toNum(rows[i].querySelector(".pp-cost").value) || 0;
      var share = (cost * qty) / totalCostWeighted;
      var lineAmount = targetTotal * share;
      var priceInput = rows[i].querySelector(".pp-price");
      priceInput.value = money(round2(lineAmount / qty));
    }
    recalcLineTotals();
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
    var sum = getPartsTableSum();
    fParts.value = sum > 0 ? money(sum) : "";
    tryAutoCalc("parts");
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
      partsState.push({
        part_id: r.querySelector(".pp-part-id").value || null,
        part_number: r.querySelector(".pp-part-number").value.trim(),
        description: r.querySelector(".pp-description").value.trim(),
        qty: toNum(r.querySelector(".pp-qty").value) || 1,
        cost: toNum(r.querySelector(".pp-cost").value) || 0,
        price: toNum(r.querySelector(".pp-price").value) || 0,
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
    return tr;
  }

  /* ── add part ── */
  addPartBtn.addEventListener("click", function () {
    addPartRow(null);
  });

  /* ── delete part row ── */
  partsTbody.addEventListener("click", function (e) {
    var btn = e.target.closest(".pp-delete");
    if (!btn) return;
    btn.closest("tr").remove();
    syncPartsJson();
    updatePartsFromTable();
  });

  /* ── recalc on qty/price change in parts table ── */
  partsTbody.addEventListener("input", function (e) {
    if (e.target.classList.contains("pp-qty") || e.target.classList.contains("pp-price")) {
      updatePartsFromTable();
    }
  });

  /* ── pricing summary input handlers ── */
  fLabor.addEventListener("input", function () {
    if (toNum(fLabor.value) === null && fLabor.value.trim() === "") {
      if (autoField === "labor") autoField = null;
      highlightAutoField();
      return;
    }
    tryAutoCalc("labor");
  });

  fParts.addEventListener("input", function () {
    if (toNum(fParts.value) === null && fParts.value.trim() === "") {
      if (autoField === "parts") autoField = null;
      highlightAutoField();
      return;
    }
    // User manually typed a parts total — scale individual prices to match
    var target = toNum(fParts.value);
    if (target !== null && target >= 0) {
      scalePartPrices(target);
      syncPartsJson();
    }
    tryAutoCalc("parts");
  });

  fTotal.addEventListener("input", function () {
    if (toNum(fTotal.value) === null && fTotal.value.trim() === "") {
      if (autoField === "total") autoField = null;
      highlightAutoField();
      return;
    }
    tryAutoCalc("total");
  });

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
    autoField = null;
    renderPartsRows([]);
    syncPartsJson();
    highlightAutoField();
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

        // Load pricing summary
        fLabor.value = data.fixed_labor_price != null ? data.fixed_labor_price : "";
        fParts.value = data.fixed_parts_total != null ? data.fixed_parts_total : "";
        fTotal.value = data.fixed_total_price != null ? data.fixed_total_price : "";

        partsState = data.parts || [];
        renderPartsRows(partsState);
        syncPartsJson();

        // Detect which field was auto-calculated
        detectAutoField();

        getModal().show();
      })
      .catch(function (err) {
        console.error("Failed to load preset:", err);
        alert("Failed to load preset data.");
      });
  });

  function detectAutoField() {
    var labor = getFieldVal("labor");
    var parts = getFieldVal("parts");
    var total = getFieldVal("total");

    var filled = [];
    if (labor !== null) filled.push("labor");
    if (parts !== null) filled.push("parts");
    if (total !== null) filled.push("total");

    if (filled.length < 3) {
      autoField = null;
    } else {
      if (Math.abs(total - (labor + parts)) < 0.02) {
        autoField = "total";
      } else {
        autoField = null;
      }
    }
    highlightAutoField();
  }

  /* ── sync parts JSON before submit ── */

  form.addEventListener("submit", function () {
    syncPartsJson();
  });

})();
