(function () {
  "use strict";

  if (document.body.dataset.importExportBound === "1") return;
  document.body.dataset.importExportBound = "1";

  const entityType = window.__importEntityType;
  const entityFields = window.__importEntityFields || [];

  const fileInput = document.getElementById("importFile");
  const uploadBtn = document.getElementById("importUploadBtn");
  const fileError = document.getElementById("importFileError");

  const step1 = document.getElementById("importStep1");
  const step2 = document.getElementById("importStep2");
  const step3 = document.getElementById("importStep3");
  const spinner = document.getElementById("importSpinner");

  const mappingBody = document.getElementById("importMappingBody");
  const runBtn = document.getElementById("importRunBtn");
  const backBtn = document.getElementById("importBackBtn");
  const resetBtn = document.getElementById("importResetBtn");
  const resultContent = document.getElementById("importResultContent");

  let fileHeaders = [];

  // ── helpers ──

  function showStep(n) {
    step1.style.display = n === 1 ? "" : "none";
    step2.style.display = n === 2 ? "" : "none";
    step3.style.display = n === 3 ? "" : "none";
  }

  function showError(msg) {
    fileError.textContent = msg;
    fileError.style.display = msg ? "" : "none";
  }

  function setSpinner(on) {
    spinner.style.display = on ? "" : "none";
  }

  function buildSelect(fileHeader) {
    const sel = document.createElement("select");
    sel.className = "form-select form-select-sm";
    sel.dataset.fileHeader = fileHeader;

    const skip = document.createElement("option");
    skip.value = "";
    skip.textContent = "— Skip —";
    sel.appendChild(skip);

    const headerLower = fileHeader.toLowerCase().replace(/[^a-z0-9]/g, "");

    let bestMatch = "";

    entityFields.forEach(function (f) {
      const opt = document.createElement("option");
      opt.value = f.key;
      opt.textContent = f.label;
      sel.appendChild(opt);

      // Auto-match by fuzzy comparison
      const labelLower = f.label.toLowerCase().replace(/[^a-z0-9]/g, "");
      const keyLower = f.key.toLowerCase().replace(/[^a-z0-9]/g, "");
      if (headerLower === labelLower || headerLower === keyLower) {
        bestMatch = f.key;
      }
    });

    if (bestMatch) {
      sel.value = bestMatch;
    }

    sel.addEventListener("change", validateMapping);
    return sel;
  }

  function validateMapping() {
    const selects = mappingBody.querySelectorAll("select");
    let hasMapped = false;
    for (const s of selects) {
      if (s.value) {
        hasMapped = true;
        break;
      }
    }
    runBtn.disabled = !hasMapped;
  }

  function getMapping() {
    const selects = mappingBody.querySelectorAll("select");
    const mapping = {};
    selects.forEach(function (s) {
      if (s.value) {
        mapping[s.dataset.fileHeader] = s.value;
      }
    });
    return mapping;
  }

  // ── Step 1: Upload ──

  fileInput.addEventListener("change", function () {
    uploadBtn.disabled = !fileInput.files.length;
    showError("");
  });

  uploadBtn.addEventListener("click", function () {
    if (!fileInput.files.length) return;
    showError("");
    setSpinner(true);
    uploadBtn.disabled = true;

    const fd = new FormData();
    fd.append("file", fileInput.files[0]);

    fetch("/import-export/upload-headers", { method: "POST", body: fd })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        setSpinner(false);
        if (!data.ok) {
          showError(data.error || "Failed to read file.");
          uploadBtn.disabled = false;
          return;
        }

        fileHeaders = data.headers;
        mappingBody.innerHTML = "";

        fileHeaders.forEach(function (h) {
          const tr = document.createElement("tr");

          const td1 = document.createElement("td");
          td1.className = "fw-semibold";
          td1.textContent = h;
          tr.appendChild(td1);

          const td2 = document.createElement("td");
          td2.appendChild(buildSelect(h));
          tr.appendChild(td2);

          mappingBody.appendChild(tr);
        });

        validateMapping();
        showStep(2);
      })
      .catch(function (err) {
        setSpinner(false);
        showError("Network error: " + err.message);
        uploadBtn.disabled = false;
      });
  });

  // ── Step 2: Map & Import ──

  backBtn.addEventListener("click", function () {
    showStep(1);
    uploadBtn.disabled = !fileInput.files.length;
  });

  runBtn.addEventListener("click", function () {
    if (!fileInput.files.length) return;
    const mapping = getMapping();
    if (!Object.keys(mapping).length) return;

    setSpinner(true);
    runBtn.disabled = true;

    const fd = new FormData();
    fd.append("file", fileInput.files[0]);
    fd.append("entity_type", entityType);
    fd.append("mapping", JSON.stringify(mapping));

    fetch("/import-export/import", { method: "POST", body: fd })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        setSpinner(false);

        if (!data.ok) {
          resultContent.innerHTML =
            '<div class="alert alert-danger py-2">' +
            '<strong>Error:</strong> ' + (data.error || "Import failed.") +
            "</div>";
        } else {
          let html =
            '<div class="alert alert-success py-2">' +
            "<strong>Import complete.</strong> " +
            data.imported + " imported, " +
            data.skipped + " skipped out of " +
            data.total + " rows." +
            "</div>";

          if (data.errors && data.errors.length) {
            html += '<div class="alert alert-warning py-2 mt-2"><strong>Warnings:</strong><ul class="mb-0 small">';
            data.errors.forEach(function (e) {
              html += "<li>" + e.replace(/</g, "&lt;") + "</li>";
            });
            html += "</ul></div>";
          }

          resultContent.innerHTML = html;
        }

        showStep(3);
      })
      .catch(function (err) {
        setSpinner(false);
        resultContent.innerHTML =
          '<div class="alert alert-danger py-2">Network error: ' + err.message + "</div>";
        showStep(3);
      });
  });

  // ── Step 3: Reset ──

  resetBtn.addEventListener("click", function () {
    fileInput.value = "";
    uploadBtn.disabled = true;
    mappingBody.innerHTML = "";
    resultContent.innerHTML = "";
    runBtn.disabled = true;
    showStep(1);
    showError("");
  });
})();
