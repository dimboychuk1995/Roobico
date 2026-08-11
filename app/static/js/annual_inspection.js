// Annual Vehicle Inspection (AVIR) — общая логика модалки.
// Разметка — components/annual_inspection.html (macro annual_inspection_modal).
// Открытие: window.AnnualInspection.open({customer_id, unit_id, vin, ...})
// или кнопкой с [data-avi-open] и data-avi-* атрибутами.
(function () {
  "use strict";

  const modalEl = document.getElementById("annualInspectionModal");
  if (!modalEl || window.AnnualInspection) return;

  // Контекст текущего открытия (не редактируется в форме)
  let ctx = { customer_id: "", unit_id: "", work_order_id: "", reload_after: false };
  let previewUrl = null;

  function readJson(id, fallback) {
    try {
      return JSON.parse(document.getElementById(id)?.textContent || "") ?? fallback;
    } catch (err) {
      return fallback;
    }
  }
  const typeDefaults = readJson("aviTypeDefaults", {});

  function debounce(fn, ms) {
    let t = null;
    return function () {
      clearTimeout(t);
      t = setTimeout(fn, ms);
    };
  }

  function toast(message, icon) {
    if (typeof Swal !== "undefined") {
      Swal.fire({ toast: true, position: "bottom-end", icon: icon || "success", title: message, showConfirmButton: false, timer: 3000 });
    }
  }

  const val = (id) => String(document.getElementById(id)?.value || "").trim();

  function collectComponents() {
    const out = {};
    modalEl.querySelectorAll("#aviChecklist .avi-item").forEach((row) => {
      const key = row.getAttribute("data-avi-key");
      if (!key) return;
      const checked = row.querySelector(".avi-status:checked");
      if (!checked) return; // неотмеченный пункт — пустая клетка в форме
      const entry = { status: checked.value };
      if (checked.value === "repair") {
        const rdate = row.querySelector(".avi-rdate");
        if (rdate && rdate.value) entry.repaired_date = rdate.value;
      }
      out[key] = entry;
    });
    return out;
  }

  function collectFields() {
    return {
      customer_id: ctx.customer_id,
      unit_id: ctx.unit_id,
      work_order_id: ctx.work_order_id || "",
      report_number: val("aviReportNumberInput"),
      date: val("aviDateInput"),
      motor_carrier_operator: val("aviCarrierInput"),
      address: val("aviAddressInput"),
      city_state_zip: val("aviCityStateZipInput"),
      inspector_name: val("aviInspectorInput"),
      inspector_qualified: !!document.getElementById("aviQualifiedCheck")?.checked,
      vin: val("aviVinInput").toUpperCase(),
      inspection_agency: val("aviAgencyInput"),
      vehicle_type: val("aviVehicleTypeSelect"),
      components: collectComponents(),
    };
  }

  async function refreshPreview() {
    const frame = document.getElementById("aviPreviewFrame");
    if (!frame) return;
    // Чеклист не помещается в query string — превью запрашивается POST'ом,
    // PDF отдаётся в iframe через blob URL.
    try {
      const res = await fetch("/work_orders/api/annual_inspections/preview-pdf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(collectFields()),
      });
      if (!res.ok) return;
      const blob = await res.blob();
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(blob);
      frame.src = `${previewUrl}#toolbar=0&navpanes=0`;
    } catch (err) {
      console.error("AVI preview failed", err);
    }
  }
  const debouncedPreview = debounce(refreshPreview, 600);

  ["aviDateInput", "aviReportNumberInput", "aviCarrierInput", "aviAddressInput",
   "aviCityStateZipInput", "aviInspectorInput", "aviQualifiedCheck", "aviVinInput",
   "aviAgencyInput", "aviVehicleTypeSelect"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("input", debouncedPreview);
    el.addEventListener("change", debouncedPreview);
  });

  const checklistEl = document.getElementById("aviChecklist");

  function setChecklist(status) {
    checklistEl?.querySelectorAll(".avi-item").forEach((row) => {
      row.querySelectorAll(".avi-status").forEach((radio) => {
        radio.checked = status !== null && radio.value === status;
      });
      const rdate = row.querySelector(".avi-rdate");
      if (rdate) rdate.style.display = "none";
    });
    debouncedPreview();
  }

  // Пресет OK/NA по типу техники (пункты вне пресета не трогаем)
  function applyTypeDefaults() {
    const type = val("aviVehicleTypeSelect");
    const defaults = typeDefaults[type];
    if (!defaults) return;
    checklistEl?.querySelectorAll(".avi-item").forEach((row) => {
      const status = defaults[row.getAttribute("data-avi-key")];
      if (!status) return;
      row.querySelectorAll(".avi-status").forEach((radio) => {
        radio.checked = radio.value === status;
      });
      const rdate = row.querySelector(".avi-rdate");
      if (rdate) rdate.style.display = status === "repair" ? "" : "none";
    });
    debouncedPreview();
  }

  checklistEl?.addEventListener("change", (e) => {
    const radio = e.target.closest(".avi-status");
    if (radio) {
      const rdate = radio.closest(".avi-item")?.querySelector(".avi-rdate");
      if (rdate) rdate.style.display = radio.value === "repair" ? "" : "none";
    }
    debouncedPreview();
  });
  document.getElementById("aviAllOkBtn")?.addEventListener("click", () => setChecklist("ok"));
  // Clear = пустая форма (никаких отметок) — для заполнения от руки.
  document.getElementById("aviClearBtn")?.addEventListener("click", () => setChecklist(null));
  document.getElementById("aviVehicleTypeSelect")?.addEventListener("change", applyTypeDefaults);

  function showError(msg) {
    const errEl = document.getElementById("aviError");
    if (errEl) {
      errEl.textContent = msg;
      errEl.style.display = msg ? "" : "none";
    }
  }

  document.getElementById("aviSaveBtn")?.addEventListener("click", async function () {
    const btn = this;
    const payload = collectFields();

    if (!payload.unit_id) { showError("Select a unit first."); return; }
    if (!payload.vin) { showError("VIN is required."); return; }
    if (!payload.vehicle_type) { showError("Select a vehicle type."); return; }

    btn.disabled = true;
    try {
      const res = await fetch("/work_orders/api/annual_inspections/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!data || !data.ok) {
        throw new Error((data && data.error) || "Failed to create inspection");
      }
      if (window.bootstrap && window.bootstrap.Modal) {
        window.bootstrap.Modal.getInstance(modalEl)?.hide();
      }
      toast("Annual inspection created", "success");
      if (data.id) {
        window.location.href = `/work_orders/api/annual_inspections/${encodeURIComponent(data.id)}/download-pdf`;
      }
      // Страницы с карточкой инспекции просят перезагрузку, чтобы показать
      // свежую запись; пауза — чтобы успел стартовать download PDF.
      if (ctx.reload_after) {
        setTimeout(() => window.location.reload(), 1500);
      }
    } catch (err) {
      showError(err.message || "Failed to create inspection");
    } finally {
      btn.disabled = false;
    }
  });

  function open(options) {
    options = options || {};
    ctx = {
      customer_id: String(options.customer_id || ""),
      unit_id: String(options.unit_id || ""),
      work_order_id: String(options.work_order_id || ""),
      reload_after: !!options.reload_after,
    };

    const setVal = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.value = String(value || "").trim();
    };
    setVal("aviReportNumberInput", "");
    setVal("aviCarrierInput", options.carrier || "");
    setVal("aviAddressInput", options.address || "");
    setVal("aviCityStateZipInput", "");
    setVal("aviVinInput", String(options.vin || "").toUpperCase());
    showError("");

    if (window.bootstrap && window.bootstrap.Modal) {
      window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
    }
    setChecklist("ok");     // каждая новая инспекция начинается со всех OK
    applyTypeDefaults();    // если тип уже выбран — сразу его пресет
    refreshPreview();
  }

  // Кнопки с data-avi-open открывают модалку с контекстом из data-атрибутов
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-avi-open]");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    open({
      unit_id: btn.getAttribute("data-avi-unit-id"),
      customer_id: btn.getAttribute("data-avi-customer-id"),
      vin: btn.getAttribute("data-avi-vin"),
      carrier: btn.getAttribute("data-avi-carrier"),
      address: btn.getAttribute("data-avi-address"),
      reload_after: btn.getAttribute("data-avi-reload") === "1",
    });
  });

  window.AnnualInspection = { open: open };
})();
