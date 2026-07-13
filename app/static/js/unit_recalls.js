// NHTSA recalls modal for a unit.
// Any button with .js-open-recalls-modal opens #unitRecallsModal and loads
// recalls for the unit taken from data-unit-id, or — for dynamic pages like
// the work order editor — from the <select> pointed to by data-unit-select.
(function () {
  "use strict";

  function toast(message, icon) {
    if (typeof Swal !== "undefined") {
      Swal.fire({
        toast: true,
        position: "bottom-end",
        icon: icon || "info",
        title: message,
        showConfirmButton: false,
        timer: 3500,
        timerProgressBar: true,
      });
    } else {
      alert(message);
    }
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function resolveUnitId(btn) {
    const direct = String(btn.dataset.unitId || "").trim();
    if (direct) return direct;
    const selector = String(btn.dataset.unitSelect || "").trim();
    if (!selector) return "";
    const select = document.querySelector(selector);
    return select ? String(select.value || "").trim() : "";
  }

  function renderMeta(meta, data) {
    meta.innerHTML = "";
    const row = el("div", "d-flex flex-wrap align-items-center gap-2");
    const v = data.vehicle || {};
    row.appendChild(el("span", "fw-semibold", [v.year, v.make, v.model].filter(Boolean).join(" ")));
    row.appendChild(el("span", "badge text-bg-" + (data.count ? "warning" : "success"),
      data.count === 1 ? "1 recall" : (data.count || 0) + " recalls"));
    if (data.new_count > 0) {
      row.appendChild(el("span", "badge text-bg-danger",
        data.new_count + " new since last check"));
    }
    const models = (data.nhtsa_models || []).filter(Boolean);
    const differs = models.length &&
      !(models.length === 1 && models[0].toUpperCase() === String(v.model || "").toUpperCase());
    if (differs) {
      row.appendChild(el("span", "small text-muted", "NHTSA: " + models.join(", ")));
    }
    row.appendChild(el("span", "small text-muted ms-auto",
      data.prev_checked ? "Previously checked " + data.prev_checked : "First check for this unit"));
    meta.appendChild(row);
    meta.style.display = "";
  }

  function renderRecall(recall) {
    const item = el("div", "list-group-item py-3");

    const head = el("div", "d-flex flex-wrap align-items-center gap-2 mb-1");
    if (recall.is_new) head.appendChild(el("span", "badge text-bg-danger", "NEW"));
    if (recall.campaign_number) {
      head.appendChild(el("span", "badge text-bg-secondary", recall.campaign_number));
    }
    if (recall.park_it) head.appendChild(el("span", "badge text-bg-danger", "Do not drive"));
    if (recall.park_outside) head.appendChild(el("span", "badge text-bg-warning", "Park outside"));
    if (recall.report_date) {
      head.appendChild(el("span", "small text-muted ms-auto", recall.report_date));
    }
    item.appendChild(head);

    if (recall.component) item.appendChild(el("div", "fw-semibold small mb-1", recall.component));
    if (recall.summary) item.appendChild(el("div", "small", recall.summary));
    if (recall.consequence) {
      const p = el("div", "small text-muted mt-1");
      p.appendChild(el("strong", "", "Consequence: "));
      p.appendChild(document.createTextNode(recall.consequence));
      item.appendChild(p);
    }
    if (recall.remedy) {
      const p = el("div", "small text-muted mt-1");
      p.appendChild(el("strong", "", "Remedy: "));
      p.appendChild(document.createTextNode(recall.remedy));
      item.appendChild(p);
    }
    return item;
  }

  async function loadRecalls(unitId) {
    const loading = document.getElementById("unitRecallsLoading");
    const error = document.getElementById("unitRecallsError");
    const meta = document.getElementById("unitRecallsMeta");
    const list = document.getElementById("unitRecallsList");
    if (!loading || !error || !meta || !list) return;

    loading.style.display = "";
    error.style.display = "none";
    meta.style.display = "none";
    list.innerHTML = "";

    let data = null;
    try {
      const res = await fetch(
        "/work_orders/api/units/" + encodeURIComponent(unitId) + "/recalls",
        { headers: { "Accept": "application/json" } }
      );
      data = await res.json();
    } catch (err) {
      data = null;
    }

    loading.style.display = "none";

    if (!data || !data.ok) {
      error.textContent = (data && data.message)
        ? data.message
        : "Failed to load recalls. Please try again later.";
      error.style.display = "";
      return;
    }

    renderMeta(meta, data);

    if (!data.recalls || data.recalls.length === 0) {
      const empty = el("div", "text-center text-muted py-4");
      empty.appendChild(el("div", "fs-3 text-success", "✓"));
      empty.appendChild(el("div", "", "No open recalls found for this vehicle."));
      list.appendChild(empty);
      return;
    }

    data.recalls.forEach(function (recall) {
      list.appendChild(renderRecall(recall));
    });
  }

  document.addEventListener("click", function (e) {
    const btn = e.target.closest(".js-open-recalls-modal");
    if (!btn) return;

    const unitId = resolveUnitId(btn);
    if (!unitId) {
      toast("Select a unit first.", "info");
      return;
    }

    const modalEl = document.getElementById("unitRecallsModal");
    if (!modalEl || !window.bootstrap || !window.bootstrap.Modal) return;
    window.bootstrap.Modal.getOrCreateInstance(modalEl).show();

    loadRecalls(unitId);
  });
})();
