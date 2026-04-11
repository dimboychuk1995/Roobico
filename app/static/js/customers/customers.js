(function () {
  "use strict";

  if (document.body && document.body.dataset.customersRowsBound === "1") {
    return;
  }
  if (document.body) {
    document.body.dataset.customersRowsBound = "1";
  }

  function shouldIgnoreClick(target) {
    return !!target.closest("a, button, form, input, select, textarea, label");
  }

  function bindRowNavigation(rowSelector) {
    document.addEventListener("click", function (event) {
      var row = event.target.closest(rowSelector);
      if (!row || shouldIgnoreClick(event.target)) {
        return;
      }

      var href = row.getAttribute("data-href");
      if (href) {
        window.location.href = href;
      }
    });

    document.addEventListener("keydown", function (event) {
      var row = event.target.closest(rowSelector);
      if (!row || shouldIgnoreClick(event.target)) {
        return;
      }

      if (event.key !== "Enter" && event.key !== " ") {
        return;
      }

      event.preventDefault();
      var href = row.getAttribute("data-href");
      if (href) {
        window.location.href = href;
      }
    });
  }

  bindRowNavigation(".js-customer-row");
  bindRowNavigation(".js-unit-row");

  document.addEventListener("click", async function (event) {
    var btn = event.target.closest(".js-delete-work-order-payment");
    if (!btn) {
      return;
    }

    var paymentId = String(btn.getAttribute("data-payment-id") || "").trim();
    if (!paymentId) {
      return;
    }

    if (!await appConfirm("Delete this payment?")) {
      return;
    }

    var originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Deleting...";

    try {
      var res = await fetch("/work_orders/api/payments/" + encodeURIComponent(paymentId) + "/delete", {
        method: "POST",
        headers: { "Accept": "application/json" }
      });
      var data = await res.json();
      if (!res.ok || !data || data.ok !== true) {
        throw new Error((data && (data.error || data.message)) || "Failed to delete payment.");
      }

      window.location.reload();
    } catch (err) {
      appAlert(err.message || "Failed to delete payment.", 'error');
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });
})();

/* ── Expand / collapse work order labors on unit details ── */
(function () {
  "use strict";

  if (document.body && document.body.dataset.woLaborsExpandBound === "1") return;
  if (document.body) document.body.dataset.woLaborsExpandBound = "1";

  var _woLaborsCache = {};

  function _esc(val) {
    return String(val == null ? "" : val)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function _money(n) {
    var x = Number(n || 0);
    return Number.isFinite(x) ? x.toFixed(2) : "0.00";
  }

  function _renderLaborsHtml(labors) {
    if (!labors || !labors.length) {
      return '<span class="text-muted small">No labors in this work order.</span>';
    }
    var html = "";
    for (var i = 0; i < labors.length; i++) {
      var lb = labors[i];
      html += '<div class="mb-2">';
      html += '<div class="fw-semibold small">';
      html += _esc(lb.description || "Labor " + (i + 1));
      if (lb.hours) html += ' <span class="text-muted">(' + _esc(lb.hours) + ' hrs)</span>';
      html += ' <span class="text-muted">— $' + _money(lb.labor_total) + '</span>';
      html += '</div>';

      var parts = lb.parts || [];
      if (parts.length) {
        html += '<table class="table table-sm table-borderless mb-0 ms-3" style="max-width:600px;">';
        html += '<thead><tr class="small text-muted"><th>Part #</th><th>Description</th><th class="text-end">Qty</th><th class="text-end">Price</th></tr></thead><tbody>';
        for (var j = 0; j < parts.length; j++) {
          var p = parts[j];
          html += '<tr class="small">';
          html += '<td>' + _esc(p.part_number || "-") + '</td>';
          html += '<td>' + _esc(p.description || "-") + '</td>';
          html += '<td class="text-end">' + (p.qty || 0) + '</td>';
          html += '<td class="text-end">$' + _money(p.price) + '</td>';
          html += '</tr>';
        }
        html += '</tbody></table>';
      }
      html += '</div>';
    }
    return html;
  }

  document.addEventListener("click", async function (event) {
    var btn = event.target.closest(".js-expand-wo-labors");
    if (!btn) return;

    var woId = btn.getAttribute("data-wo-id");
    if (!woId) return;

    var detailRow = document.querySelector('tr.js-wo-labors-row[data-wo-id="' + woId + '"]');
    if (!detailRow) return;

    var isOpen = !detailRow.classList.contains("d-none");
    if (isOpen) {
      detailRow.classList.add("d-none");
      btn.querySelector("i").className = "bi bi-chevron-down me-1";
      btn.lastChild.textContent = "Show labor details";
      return;
    }

    detailRow.classList.remove("d-none");

    btn.querySelector("i").className = "bi bi-chevron-up me-1";
    btn.lastChild.textContent = "Hide labor details";

    var content = detailRow.querySelector(".js-wo-labors-content");
    if (!content) return;

    if (_woLaborsCache[woId]) {
      content.innerHTML = _renderLaborsHtml(_woLaborsCache[woId]);
      return;
    }

    content.innerHTML = '<span class="text-muted small">Loading...</span>';

    try {
      var res = await fetch("/customers/api/work_orders/" + encodeURIComponent(woId) + "/labors", {
        method: "GET",
        headers: { "Accept": "application/json" }
      });
      var data = await res.json();
      if (!res.ok || !data || !data.ok) {
        content.innerHTML = '<span class="text-danger small">' + _esc(data && data.error || "Failed to load labors") + '</span>';
        return;
      }
      _woLaborsCache[woId] = data.labors || [];
      content.innerHTML = _renderLaborsHtml(_woLaborsCache[woId]);
    } catch (err) {
      content.innerHTML = '<span class="text-danger small">Network error</span>';
    }
  });
})();

/* ── Lazy-load customer balances ── */
(function () {
  "use strict";

  // Customer list page: load balances for all visible customers
  var cells = document.querySelectorAll(".js-customer-balance");
  if (cells.length) {
    var ids = [];
    cells.forEach(function (cell) {
      var cid = cell.getAttribute("data-customer-id");
      if (cid) ids.push(cid);
    });

    if (ids.length) {
      var params = new URLSearchParams();
      ids.forEach(function (id) { params.append("ids", id); });

      fetch("/customers/api/balances?" + params.toString(), {
        method: "GET",
        headers: { "Accept": "application/json" }
      })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (!data || !data.ok) return;
          var balances = data.balances || {};
          cells.forEach(function (cell) {
            var cid = cell.getAttribute("data-customer-id");
            var val = Number(balances[cid] || 0);
            cell.textContent = "$" + val.toFixed(2);
          });
        })
        .catch(function () {
          cells.forEach(function (cell) {
            cell.textContent = "$—";
          });
        });
    }
  }

  // Customer details page: load balance for single customer
  var detailCell = document.querySelector(".js-customer-detail-balance");
  if (detailCell) {
    var cid = detailCell.getAttribute("data-customer-id");
    if (cid) {
      var params = new URLSearchParams();
      params.append("ids", cid);
      fetch("/customers/api/balances?" + params.toString(), {
        method: "GET",
        headers: { "Accept": "application/json" }
      })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (!data || !data.ok) return;
          var val = Number((data.balances || {})[cid] || 0);
          detailCell.textContent = "$" + val.toFixed(2);
        })
        .catch(function () {
          detailCell.textContent = "$—";
        });
    }
  }
})();
