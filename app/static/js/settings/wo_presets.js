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

  /* Значение идёт и в текст, и в value="…"/title="…": кавычки экранируем
     тоже — иначе описание «3/8" Fuel Hose» обрезается на кавычке. */
  function escapeHtml(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
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
    // Как в WO (matchRule) и на сервере (match_pricing_rule): cost между
    // ступенями (5.004 при ступенях … – 5.00 и 5.01 – …) берёт ступень по
    // значению, округлённому до цента — тем же способом, что round2 на
    // сервере (round(cost + 1e-12, 2): половина цента — вверх, 1.005 → 1.01).
    var candidates = [cost, Number((cost + 1e-12).toFixed(2))];
    for (var c = 0; c < candidates.length; c++) {
      for (var i = 0; i < rules.length; i++) {
        var r = rules[i];
        var from = toNum(r.from);
        var to = (r.to === null || r.to === undefined) ? null : toNum(r.to);
        var vp = toNum(r.value_percent);
        if (from === null || vp === null) continue;
        if (candidates[c] < from) continue;
        if (to === null) return { value_percent: vp };
        if (candidates[c] <= to) return { value_percent: vp };
      }
    }
    return null;
  }

  /* Автоцена запчасти для кастомера без своей шкалы — то же правило, что у
     work order (сервер: price_part): фиксированная Selling price запчасти,
     иначе дефолтная шкала от average cost; шкала не подошла — по cost; cost
     не задан — цены нет. source: fixed | matrix | cost | none. */
  function autoPriceFor(part) {
    var fixed = toNum(part.selling_price);
    if (part.has_selling_price && fixed !== null && fixed > 0) {
      return { price: round2(fixed), source: "fixed" };
    }
    var cost = toNum(part.average_cost);
    if (cost === null || cost <= 0) return { price: 0, source: "none" };

    var pricing = (typeof PRICING_RULES !== "undefined") ? PRICING_RULES : null;
    var rule = (pricing && Array.isArray(pricing.rules)) ? matchPricingRule(cost, pricing.rules) : null;
    if (!rule) return { price: round2(cost), source: "cost" };

    var vp = rule.value_percent / 100;
    if (pricing.mode === "markup") return { price: round2(cost * (1 + vp)), source: "matrix" };
    var denom = 1 - vp;
    if (denom <= 0) return { price: round2(cost), source: "matrix" };
    return { price: round2(cost / denom), source: "matrix" };
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

  /* Ручная цена: фиксируется в пресете и применяется в WO вместо
     динамической (selling price парта / прайс-матрица). Пустое поле по blur
     возвращает автоцену и снимает фиксацию. */
  function markPriceOverridden(tr, on) {
    var inp = tr.querySelector(".pp-price");
    if (!inp) return;
    if (on) tr.dataset.priceOverridden = "1";
    else delete tr.dataset.priceOverridden;
    inp.classList.toggle("border-warning", !!on);
    inp.title = on
      ? "Custom price — saved with the preset. Clear the field to return to automatic pricing."
      : "";
    renderPriceFlags(tr);
  }

  /* Откуда цена строки и не ниже ли она себестоимости — бейджи под полем
     Price (те же, что на карточке темплейта). */
  var PRICE_FLAGS = {
    custom: {
      cls: "text-bg-warning", text: "custom price",
      title: "Typed into this template: saved with it and used in work orders as is. Clear the field to return to automatic pricing.",
    },
    fixed: {
      cls: "text-bg-light border", text: "fixed price",
      title: "The part's own Selling price from the catalog. It does not follow the average cost — change or remove it in Parts (edit the part).",
    },
    cost: {
      cls: "text-bg-warning", text: "no markup",
      title: "No tier of the pricing scale covers this cost. Work orders created by a mechanic or in the mobile app price the part at cost; the office work order editor leaves the price empty. Add a tier for this cost in Settings → Parts & Pricing.",
    },
    none: {
      cls: "text-bg-warning", text: "no price",
      title: "The part has no average cost, so no price can be calculated: a work order gets $0. Receive the part, set its Selling price, or type a price here.",
    },
    snapshot: {
      cls: "text-bg-light border", text: "not in catalog",
      title: "Not a catalog part (a free-text line, or the part was deleted or deactivated). The price saved with the template is shown: the office work order editor uses it, a work order created by a mechanic gets this line without a price.",
    },
  };

  function flagBadge(def, title) {
    return '<span class="badge ' + def.cls + ' me-1" title="' + escapeHtml(title || def.title) + '">' +
      escapeHtml(def.text) + '</span>';
  }

  function renderPriceFlags(tr) {
    var box = tr.querySelector(".pp-price-flags");
    if (!box) return;
    var html = "";
    var pinned = tr.dataset.priceOverridden === "1";
    var source = pinned ? "custom" : (tr.dataset.autoSource || "");
    if (PRICE_FLAGS[source] && source !== "matrix") html += flagBadge(PRICE_FLAGS[source]);

    // «Ниже себестоимости» — только для запчасти из каталога с известной
    // текущей cost: у строки не из каталога в поле Cost лежит старый снимок.
    var live = tr.dataset.autoSource || "";
    var price = toNum(tr.querySelector(".pp-price").value);
    var cost = toNum(tr.querySelector(".pp-cost").value);
    if (live && live !== "none" && live !== "snapshot" && price !== null && cost !== null && cost > 0 && price < round2(cost)) {
      html += flagBadge(
        { cls: "text-bg-danger", text: "below cost" },
        "Lower than the part's current average cost ($" + money(cost) + "): every sale at this price loses money."
      );
    }
    box.innerHTML = html;
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
      // Пустое поле цены — не «закреплено $0»: строка остаётся на автоцене
      // (Enter в форме отправляет её без blur, который снимает фиксацию).
      var priceValue = toNum(r.querySelector(".pp-price").value);
      var pinned = r.dataset.priceOverridden === "1" && priceValue !== null;
      if (priceValue === null) priceValue = toNum(r.dataset.autoPrice);
      // Автоцены нет (строка не из каталога) — явный 0, как и раньше.
      if (priceValue === null) priceValue = 0;
      partsState.push({
        part_id: r.querySelector(".pp-part-id").value || null,
        part_number: r.querySelector(".pp-part-number").value.trim(),
        description: r.querySelector(".pp-description").value.trim(),
        qty: toNum(r.querySelector(".pp-qty").value) || 1,
        cost: toNum(r.querySelector(".pp-cost").value) || 0,
        price: priceValue,
        price_overridden: pinned,
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
    // Cost \u0438 Price \u2014 \u0434\u0435\u043d\u044c\u0433\u0438: \u043f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0435\u043c \u0441 \u0446\u0435\u043d\u0442\u0430\u043c\u0438 (average cost \u0445\u0440\u0430\u043d\u0438\u0442\u0441\u044f \u0442\u043e\u0447\u043d\u0435\u0435).
    var costNum = data ? toNum(data.cost) : null;
    var cost = (costNum !== null && costNum > 0) ? money(costNum) : "";
    var priceNum = data ? toNum(data.price) : null;
    // Закреплённые $0 (бесплатная позиция) показываем как 0.00: пустое поле
    // при сохранении означает «вернуть автоцену».
    var price = (priceNum !== null && (priceNum > 0 || (data && data.price_overridden))) ? money(priceNum) : "";
    var lineTotal = (toNum(qty) || 0) * (toNum(price) || 0);

    tr.innerHTML =
      '<td class="p-1"><input type="hidden" class="pp-part-id" value="' + escapeHtml(partId) + '">' +
      '<input class="form-control form-control-sm pp-part-number" value="' + escapeHtml(partNum) + '" maxlength="64" autocomplete="off" placeholder="Search parts\u2026"></td>' +
      '<td class="p-1"><input class="form-control form-control-sm pp-description" value="' + escapeHtml(desc) + '" maxlength="200" autocomplete="off"></td>' +
      '<td class="p-1"><input class="form-control form-control-sm pp-qty" value="' + qty + '" inputmode="numeric" min="1"></td>' +
      '<td class="p-1"><input class="form-control form-control-sm pp-cost" value="' + cost + '" readonly tabindex="-1"></td>' +
      '<td class="p-1"><input class="form-control form-control-sm pp-price" value="' + price + '" inputmode="decimal" step="0.01" min="0" placeholder="0.00">' +
      '<div class="pp-price-flags"></div></td>' +
      '<td class="p-1 align-middle pp-line-total">$' + money(lineTotal) + '</td>' +
      '<td class="p-1 text-center"><button type="button" class="btn btn-sm btn-outline-danger pp-delete">&times;</button></td>';

    partsTbody.appendChild(tr);

    var autoPrice = (data && data.auto_price != null) ? toNum(data.auto_price) : null;
    if (autoPrice !== null) tr.dataset.autoPrice = String(autoPrice);
    // \u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a \u0430\u0432\u0442\u043e\u0446\u0435\u043d\u044b \u0441 \u0441\u0435\u0440\u0432\u0435\u0440\u0430; \u00absnapshot\u00bb \u2014 \u0437\u0430\u043f\u0447\u0430\u0441\u0442\u0438 \u043d\u0435\u0442 \u0432 \u043a\u0430\u0442\u0430\u043b\u043e\u0433\u0435.
    // Строка не из каталога остаётся такой и с закреплённой ценой (автоцены
    // у неё нет) — после снятия фиксации бейдж «not in catalog» на месте.
    if (data && data.auto_source) tr.dataset.autoSource = String(data.auto_source);
    else if (data && (data.price_source === "snapshot" ||
        (data.price_source === "pinned" && data.auto_price == null))) tr.dataset.autoSource = "snapshot";
    markPriceOverridden(tr, !!(data && data.price_overridden));

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
      var priceTr = e.target.closest("tr.preset-part-row");
      if (priceTr) {
        // Закрепляем только цену, которая отличается от автоцены: набрать
        // то же число заново (или стереть поле) — не «своя цена».
        var typed = toNum(e.target.value);
        var auto = toNum(priceTr.dataset.autoPrice);
        // Автоцены нет ($0) — набранный 0 считаем своей ценой: позиция
        // останется бесплатной, даже когда у запчасти появится cost.
        var sameAsAuto = typed !== null && auto !== null && auto > 0 && Math.abs(typed - auto) < 0.005;
        markPriceOverridden(priceTr, typed !== null && !sameAsAuto);
      }
      updatePartsFromTable();
    }
  });

  /* Пустая цена по blur — вернуться к автоцене и снять фиксацию */
  partsTbody.addEventListener("focusout", function (e) {
    if (!e.target.classList.contains("pp-price")) return;
    if (e.target.value.trim() !== "") return;
    var tr = e.target.closest("tr.preset-part-row");
    if (!tr) return;
    var autoPrice = toNum(tr.dataset.autoPrice);
    if (autoPrice !== null && autoPrice > 0) e.target.value = money(autoPrice);
    markPriceOverridden(tr, false);
    updatePartsFromTable();
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
      dropdown.innerHTML = '<div class="list-group-item text-muted small">No matches</div>';
      return;
    }
    dropdown.innerHTML = items.map(function (it, idx) {
      var title = (it.part_number || "") + " \u2014 " + (it.description || "");
      var meta = "Stock: " + (it.in_stock || 0) + " \u00b7 Avg cost: $" + money(it.average_cost);
      if (it.misc_has_charge && Array.isArray(it.misc_charges) && it.misc_charges.length) {
        meta += " \u00b7 Misc charges: " + it.misc_charges.length;
      }
      return '<button type="button" class="list-group-item list-group-item-action pp-dd-item" data-idx="' + idx + '">' +
        '<div class="fw-semibold">' + escapeHtml(title) + '</div>' +
        '<div class="text-muted small">' + escapeHtml(meta) + '</div>' +
        '</button>';
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
      dropdown.innerHTML = '<div class="list-group-item text-muted small">Searching\u2026</div>';
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
    if (costInput) {
      // Без cost в ответе поле очищаем — иначе останется cost прежнего парта.
      costInput.value = (avgCost !== null && avgCost > 0) ? money(avgCost) : "";
    }

    // Auto-fill price exactly like a work order does: the part's fixed selling
    // price, otherwise the default pricing scale on the current average cost.
    var priceInput = activeSearchRow.querySelector(".pp-price");
    if (priceInput) {
      if (it.average_cost === undefined && it.selling_price === undefined) {
        // Поиск отдал парт без денег (у пользователя нет права видеть
        // cost/цены): автоцену здесь не знаем — сервер посчитает её сам,
        // когда темплейт откроют или применят. Цену прежнего парта убираем.
        priceInput.value = "";
        delete activeSearchRow.dataset.autoPrice;
        delete activeSearchRow.dataset.autoSource;
      } else {
        var auto = autoPriceFor(it);
        priceInput.value = auto.price > 0 ? money(auto.price) : "";
        activeSearchRow.dataset.autoPrice = String(auto.price);
        activeSearchRow.dataset.autoSource = auto.source;
      }
      // Новый парт в строке — цена снова автоматическая.
      markPriceOverridden(activeSearchRow, false);
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
        if (window.appAlert) window.appAlert("Failed to load preset data.", "error");
        else window.alert("Failed to load preset data.");
      });
  });

  /* ── sync parts JSON before submit ── */

  form.addEventListener("submit", function () {
    syncPartsJson();
  });

})();
