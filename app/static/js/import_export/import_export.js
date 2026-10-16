// Import / Export page: двухшаговый импорт (заголовки → маппинг → импорт).
// Экспорт — обычные ссылки на /import-export/export/<entity>, JS не нужен.
(function () {
  "use strict";
  if (document.body.dataset.importExportBound) return;
  document.body.dataset.importExportBound = "1";

  var entityType = window.__importEntityType || "";
  var entityFields = Array.isArray(window.__importEntityFields) ? window.__importEntityFields : [];

  var els = {
    step1: document.getElementById("importStep1"),
    step2: document.getElementById("importStep2"),
    step3: document.getElementById("importStep3"),
    file: document.getElementById("importFile"),
    uploadBtn: document.getElementById("importUploadBtn"),
    fileError: document.getElementById("importFileError"),
    mappingBody: document.getElementById("importMappingBody"),
    runBtn: document.getElementById("importRunBtn"),
    backBtn: document.getElementById("importBackBtn"),
    resultContent: document.getElementById("importResultContent"),
    resetBtn: document.getElementById("importResetBtn"),
    spinner: document.getElementById("importSpinner"),
  };
  // Секция импорта может быть скрыта правами — тогда просто ничего не делаем.
  if (!els.step1 || !els.file || !els.uploadBtn) return;

  // ── Units: target customer (list rendered by the server, searchable) ──
  var customers = Array.isArray(window.__importCustomers) ? window.__importCustomers : [];
  var custEls = {
    id: document.getElementById("importCustomerId"),
    search: document.getElementById("importCustomerSearch"),
    drop: document.getElementById("importCustomerDrop"),
    label: document.getElementById("importCustomerLabel"),
  };
  var needsCustomer = entityType === "units" && !!custEls.id;

  function renderCustomerDrop(q) {
    if (!custEls.drop) return;
    var needle = String(q || "").trim().toLowerCase();
    var items = customers.filter(function (c) {
      return !needle || String(c.label || "").toLowerCase().indexOf(needle) !== -1;
    }).slice(0, 50);
    custEls.drop.innerHTML = "";
    if (!items.length) { custEls.drop.style.display = "none"; return; }
    items.forEach(function (c) {
      var item = document.createElement("button");
      item.type = "button";
      item.className = "list-group-item list-group-item-action";
      item.textContent = c.label;
      item.addEventListener("mousedown", function (e) {
        e.preventDefault();
        custEls.id.value = c.id;
        custEls.search.value = c.label;
        custEls.drop.style.display = "none";
        updateCustomerState();
      });
      custEls.drop.appendChild(item);
    });
    custEls.drop.style.display = "";
  }

  function updateCustomerState() {
    if (custEls.label) {
      custEls.label.textContent = custEls.id && custEls.id.value ? "— for " + custEls.search.value : "";
    }
    validateMapping();
  }

  if (needsCustomer && custEls.search) {
    custEls.search.addEventListener("input", function () {
      custEls.id.value = ""; // typing invalidates the choice until an item is picked
      renderCustomerDrop(custEls.search.value);
      updateCustomerState();
    });
    custEls.search.addEventListener("focus", function () { renderCustomerDrop(custEls.search.value); });
    custEls.search.addEventListener("blur", function () {
      setTimeout(function () { if (custEls.drop) custEls.drop.style.display = "none"; }, 150);
    });
  }

  var alertFn = window.appAlert || function (msg) { window.alert(msg); };

  function show(el, on) { if (el) el.style.display = on ? "" : "none"; }

  function showError(msg) {
    if (!els.fileError) return alertFn(msg, "error");
    els.fileError.textContent = msg;
    show(els.fileError, true);
  }

  function setBusy(on) {
    show(els.spinner, on);
    els.uploadBtn.disabled = on || !els.file.files.length;
    if (els.runBtn) {
      if (on) els.runBtn.disabled = true;
      else validateMapping();
    }
  }

  // fetch + JSON: любой не-JSON ответ (500, HTML-редирект от «Access denied»)
  // превращается в понятную ошибку, а не «Unexpected token <».
  function fetchJson(url, options) {
    return fetch(url, options).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        try { data = JSON.parse(text); } catch (e) { /* not JSON */ }
        if (data === null) {
          throw new Error(res.ok
            ? "Unexpected server response."
            : "Request failed (" + res.status + "). You may not have permission for this action.");
        }
        return data;
      });
    });
  }

  // ── Step 1: read headers ─────────────────────────────────────────
  els.file.addEventListener("change", function () {
    els.uploadBtn.disabled = !els.file.files.length;
    show(els.fileError, false);
  });

  els.uploadBtn.addEventListener("click", function () {
    if (!els.file.files.length) return;
    show(els.fileError, false);
    setBusy(true);

    var fd = new FormData();
    fd.append("file", els.file.files[0]);

    fetchJson("/import-export/upload-headers", { method: "POST", body: fd })
      .then(function (data) {
        if (!data.ok) { showError(data.error || "Could not read the file."); return; }
        buildMapping(data.headers || []);
        show(els.step1, false);
        show(els.step2, true);
      })
      .catch(function (e) { showError(e.message || "Network error."); })
      .finally(function () { setBusy(false); });
  });

  // ── Step 2: mapping ──────────────────────────────────────────────
  function normalize(s) {
    return String(s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  }

  function autoMatch(header) {
    var n = normalize(header);
    if (!n) return "";
    for (var i = 0; i < entityFields.length; i++) {
      var f = entityFields[i];
      if (normalize(f.label) === n || normalize(f.key) === n) return f.key;
    }
    return "";
  }

  function buildMapping(headers) {
    els.mappingBody.textContent = "";
    headers.forEach(function (header) {
      var tr = document.createElement("tr");

      var tdName = document.createElement("td");
      tdName.className = "fw-medium";
      tdName.textContent = header;
      tr.appendChild(tdName);

      var tdSel = document.createElement("td");
      var sel = document.createElement("select");
      sel.className = "form-select form-select-sm";
      sel.dataset.fileHeader = header;

      var skip = document.createElement("option");
      skip.value = "";
      skip.textContent = "— Skip —";
      sel.appendChild(skip);

      entityFields.forEach(function (f) {
        var opt = document.createElement("option");
        opt.value = f.key;
        opt.textContent = f.label;
        sel.appendChild(opt);
      });
      sel.value = autoMatch(header);
      sel.addEventListener("change", validateMapping);

      tdSel.appendChild(sel);
      tr.appendChild(tdSel);
      els.mappingBody.appendChild(tr);
    });
    validateMapping();
  }

  function getMapping() {
    var mapping = {};
    els.mappingBody.querySelectorAll("select").forEach(function (sel) {
      if (sel.value) mapping[sel.dataset.fileHeader] = sel.value;
    });
    return mapping;
  }

  function validateMapping() {
    if (!els.runBtn) return;
    var mapped = Object.keys(getMapping()).length > 0;
    var customerOk = !needsCustomer || !!(custEls.id && custEls.id.value);
    els.runBtn.disabled = !mapped || !customerOk;
    els.runBtn.title = (needsCustomer && !customerOk) ? "Choose the customer to import units for" : "";
  }

  if (els.backBtn) {
    els.backBtn.addEventListener("click", function () {
      show(els.step2, false);
      show(els.step1, true);
    });
  }

  // ── Step 3: run import + result ──────────────────────────────────
  if (els.runBtn) {
    els.runBtn.addEventListener("click", function () {
      if (!els.file.files.length) return;
      setBusy(true);

      if (needsCustomer && !(custEls.id && custEls.id.value)) {
        setBusy(false);
        alertFn("Choose the customer to import units for.", "warning");
        return;
      }

      var fd = new FormData();
      fd.append("file", els.file.files[0]);
      fd.append("entity_type", entityType);
      fd.append("mapping", JSON.stringify(getMapping()));
      if (custEls.id && custEls.id.value) fd.append("customer_id", custEls.id.value);

      fetchJson("/import-export/import", { method: "POST", body: fd })
        .then(function (data) {
          if (!data.ok) { showResult(null, data.error || "Import failed."); return; }
          showResult(data, null);
        })
        .catch(function (e) { showResult(null, e.message || "Network error."); })
        .finally(function () { setBusy(false); });
    });
  }

  function showResult(data, errorMsg) {
    els.resultContent.textContent = "";

    var box = document.createElement("div");
    if (errorMsg) {
      box.className = "alert alert-danger mb-0";
      box.textContent = errorMsg;
      els.resultContent.appendChild(box);
    } else {
      box.className = data.skipped ? "alert alert-warning" : "alert alert-success";
      box.textContent = data.imported + " imported, " + data.skipped +
        " skipped out of " + data.total + " rows.";
      els.resultContent.appendChild(box);

      if (Array.isArray(data.errors) && data.errors.length) {
        var title = document.createElement("div");
        title.className = "text-muted small mb-1";
        title.textContent = "Skipped rows:";
        els.resultContent.appendChild(title);

        var list = document.createElement("ul");
        list.className = "small mb-0";
        data.errors.forEach(function (msg) {
          var li = document.createElement("li");
          li.textContent = msg;
          list.appendChild(li);
        });
        els.resultContent.appendChild(list);
      }
    }

    show(els.step2, false);
    show(els.step3, true);
  }

  if (els.resetBtn) {
    els.resetBtn.addEventListener("click", function () {
      els.file.value = "";
      els.uploadBtn.disabled = true;
      show(els.step3, false);
      show(els.step1, true);
    });
  }
})();
