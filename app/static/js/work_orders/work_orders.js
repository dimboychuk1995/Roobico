(function () {
  "use strict";

  const WORK_ORDERS_ACTIVE_TAB_KEY = "workOrders.activeTab";
  const APP_TIMEZONE = document.body?.dataset?.appTimezone || "UTC";

  function formatDateMMDDYYYY(value) {
    if (!value) return "-";
    const dt = new Date(value);
    if (Number.isNaN(dt.getTime())) return "-";
    return new Intl.DateTimeFormat("en-US", {
      timeZone: APP_TIMEZONE,
      month: "2-digit",
      day: "2-digit",
      year: "numeric",
    }).format(dt);
  }

  function safeGetLocalStorage(key) {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  }

  function safeSetLocalStorage(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      // ignore storage errors
    }
  }

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(body || {}),
    });

    let data = null;
    try { data = await res.json(); } catch { data = null; }

    if (!res.ok || !data || data.ok !== true) {
      const msg = (data && (data.error || data.message)) ? (data.error || data.message) : "Failed to update.";
      throw new Error(msg);
    }

    return data;
  }

  async function getJson(url) {
    const res = await fetch(url, {
      method: "GET",
      headers: { "Accept": "application/json" },
    });

    let data = null;
    try { data = await res.json(); } catch { data = null; }

    if (!res.ok || !data || data.ok !== true) {
      const msg = (data && (data.error || data.message)) ? (data.error || data.message) : "Failed to fetch.";
      throw new Error(msg);
    }

    return data;
  }

  let currentWorkOrderId = null;
  let paymentsLoaded = false;
  let _paymentListPendingAttId = "";
  const body = document.body;

  function _genTempId() {
    var h = ''; for (var i = 0; i < 24; i++) h += Math.floor(Math.random() * 16).toString(16); return h;
  }

  // ========== MARK PAID BUTTON LOGIC ==========
  if (!body || body.dataset.workOrdersMarkPaidBound !== "1") {
    if (body) body.dataset.workOrdersMarkPaidBound = "1";
    document.addEventListener("click", async function (e) {
      const btn = e.target.closest(".js-mark-paid");
      if (!btn) return;

    const workOrderId = String(btn.dataset.workOrderId || "").trim();
    if (!workOrderId) return;

    currentWorkOrderId = workOrderId;

    // Fetch payment info
    try {
      const data = await getJson(`/work_orders/api/work_orders/${encodeURIComponent(workOrderId)}/payments`);
      
      // Update modal with balance info
      document.getElementById("paymentListInvoiceTotal").textContent = `$${(data.grand_total || 0).toFixed(2)}`;
      document.getElementById("paymentListAlreadyPaid").textContent = `$${(data.paid_amount || 0).toFixed(2)}`;
      document.getElementById("paymentListRemainingBalance").textContent = `$${(data.remaining_balance || 0).toFixed(2)}`;
      
      // Pre-fill amount with remaining balance
      const remainingBalance = data.remaining_balance || 0;
      document.getElementById("paymentListAmountInput").value = remainingBalance > 0 ? remainingBalance.toFixed(2) : "";
      document.getElementById("paymentListMethodInput").value = "cash";
      document.getElementById("paymentListNotesInput").value = "";
      const paymentDateInput = document.getElementById("paymentListDateInput");
      if (paymentDateInput) {
        paymentDateInput.value = paymentDateInput.defaultValue || paymentDateInput.value || "";
        if (paymentDateInput._flatpickr) { paymentDateInput._flatpickr.setDate(paymentDateInput.value || null, false, "Y-m-d"); }
      }

      // Show modal
      const modal = new bootstrap.Modal(document.getElementById("paymentModalList"));

      // Init attachment block with temp ID
      _paymentListPendingAttId = _genTempId();
      var attWrap = document.getElementById("paymentListAttBlock");
      var attEl = attWrap ? attWrap.querySelector(".attachments-block") : null;
      if (attEl) {
        attEl.dataset.entityId = _paymentListPendingAttId;
        if (attEl._attBlock) {
          attEl._attBlock.setEntityId(_paymentListPendingAttId);
          attEl._attBlock.items = [];
          attEl._attBlock.render();
        } else if (typeof window.AttachmentsInit === "function") {
          window.AttachmentsInit();
        }
      }

      modal.show();
    } catch (err) {
      appAlert(err.message || "Failed to load payment info.", 'error');
    }
    });
  }

  if (!body || body.dataset.workOrdersPaymentSubmitBound !== "1") {
    if (body) body.dataset.workOrdersPaymentSubmitBound = "1";
    document.addEventListener("click", async function (e) {
      const submitBtn = e.target.closest("#paymentListSubmitBtn");
      if (!submitBtn) return;
      if (!currentWorkOrderId) return;

    const amount = parseFloat(document.getElementById("paymentListAmountInput").value || "0");
    const paymentMethod = document.getElementById("paymentListMethodInput").value;
    const notes = document.getElementById("paymentListNotesInput").value;
    const paymentDate = String(document.getElementById("paymentListDateInput")?.value || "").trim();

    if (amount <= 0) {
      appAlert("Please enter a valid payment amount.", 'warning');
      return;
    }

    if (!paymentDate) {
      appAlert("Please select payment date.", 'warning');
      return;
    }

    const btn = submitBtn;
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Saving...";

    try {
      const data = await postJson(`/work_orders/api/work_orders/${encodeURIComponent(currentWorkOrderId)}/payment`, {
        amount,
        payment_method: paymentMethod,
        notes,
        payment_date: paymentDate,
        pending_attachment_id: _paymentListPendingAttId || "",
      });

      // Close modal
      const modal = bootstrap.Modal.getInstance(document.getElementById("paymentModalList"));
      modal.hide();

      // Update UI
      if (data.is_fully_paid) {
        const row = document.querySelector(`button[data-work-order-id="${currentWorkOrderId}"]`)?.closest("tr");
        if (row) {
          const td = row.querySelector("td:nth-child(7)");
          if (td) {
            td.innerHTML = '<span class="badge bg-success">Paid</span>';
          }
        }
      }

      appAlert("Payment recorded successfully!", 'success');
      currentWorkOrderId = null;
      
      // Refresh payments tab if it's loaded
      if (paymentsLoaded) {
        loadPaymentsData();
      }
    } catch (err) {
      appAlert(err.message || "Failed to record payment.", 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
    });
  }

  // ========== PAYMENTS TAB LOGIC ==========

  let _paymentsCurrentPage = 1;

  async function loadPaymentsData(page) {
    if (typeof page === "number" && page >= 1) {
      _paymentsCurrentPage = page;
    }
    const loadingEl = document.getElementById("payments-loading");
    const contentEl = document.getElementById("payments-content");
    const emptyEl = document.getElementById("payments-empty");

    loadingEl.classList.remove("d-none");
    contentEl.classList.add("d-none");
    emptyEl.classList.add("d-none");

    try {
      const params = new URLSearchParams(window.location.search || "");
      const q = String(params.get("q") || "").trim();
      const datePreset = String(params.get("date_preset") || "").trim();
      const dateFrom = String(params.get("date_from") || "").trim();
      const dateTo = String(params.get("date_to") || "").trim();
      const apiParams = new URLSearchParams();
      if (q) apiParams.set("q", q);
      if (datePreset) apiParams.set("date_preset", datePreset);
      if (dateFrom) apiParams.set("date_from", dateFrom);
      if (dateTo) apiParams.set("date_to", dateTo);
      apiParams.set("payments_page", String(_paymentsCurrentPage));
      const endpoint = `/work_orders/api/work_orders/all-payments?${apiParams.toString()}`;

      const response = await fetch(endpoint, {
        method: "GET",
        headers: { "Accept": "application/json" },
      });

      let allPaymentsData = [];

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();
      
      if (!data.ok) {
        throw new Error(data.error || "API returned error");
      }

      allPaymentsData = data.payments || [];
      const pg = data.pagination || {};
      loadingEl.classList.add("d-none");

      if (allPaymentsData.length === 0 && (_paymentsCurrentPage <= 1)) {
        emptyEl.classList.remove("d-none");
        return;
      }

      // Build payments table
      let html = `
        <div class="table-responsive">
          <table class="table table-sm align-middle sortable">
            <thead>
              <tr>
                <th>WO #</th>
                <th>Customer</th>
                <th>Amount</th>
                <th>Method</th>
                <th>Date</th>
                <th>Notes</th>
                <th class="text-end no-sort">Actions</th>
              </tr>
            </thead>
            <tbody>
      `;

      allPaymentsData.forEach(payment => {
        try {
          const createdAt = formatDateMMDDYYYY(payment.payment_date || payment.created_at);

          const woNumber = String(payment.wo_number || "").trim() || "—";
          const customer = String(payment.customer || "").trim() || "—";
          const amount = parseFloat(payment.amount) || 0;
          const method = String(payment.payment_method || "cash").toLowerCase();
          const notes = String(payment.notes || "").trim();
          const paymentId = String(payment.id || "");

          html += `
            <tr>
              <td><span class="badge bg-secondary">${woNumber}</span></td>
              <td>${customer}</td>
              <td class="fw-semibold">$${amount.toFixed(2)}</td>
              <td><span class="badge bg-secondary">${method}</span></td>
              <td><small>${createdAt}</small></td>
              <td>${notes ? `<small>${notes}</small>` : "<small class='text-muted'>—</small>"}</td>
              <td class="text-end"><button type="button" class="btn btn-sm btn-outline-secondary js-open-att-modal me-1" data-entity-type="work_order_payment" data-entity-id="${paymentId}" data-bs-toggle="modal" data-bs-target="#attachmentsModal" title="Attachments"><i class="bi bi-paperclip me-1"></i>Attachments</button><button type="button" class="btn btn-sm btn-outline-danger js-delete-work-order-payment" data-payment-id="${paymentId}" title="Delete payment">Delete</button></td>
            </tr>
          `;
        } catch (itemErr) {
          console.warn("Error formatting payment:", payment, itemErr);
        }
      });

      html += `
            </tbody>
          </table>
        </div>
      `;

      // Pagination controls
      if (pg.pages && pg.pages > 1) {
        const prevDisabled = !pg.has_prev ? " disabled" : "";
        const nextDisabled = !pg.has_next ? " disabled" : "";
        html += `
          <div class="wo-pagination-row mt-3">
            <div class="small text-muted wo-pagination-meta">
              Page ${pg.page} of ${pg.pages} &middot; ${pg.total} total
            </div>
            <div class="wo-pagination-actions">
              <div class="btn-group btn-group-sm" role="group" aria-label="Payments pagination">
                <button type="button" class="btn btn-outline-secondary js-payments-page${prevDisabled}" data-page="${pg.prev_page}"${prevDisabled ? ' tabindex="-1"' : ""}>Prev</button>
                <button type="button" class="btn btn-outline-secondary js-payments-page${nextDisabled}" data-page="${pg.next_page}"${nextDisabled ? ' tabindex="-1"' : ""}>Next</button>
              </div>
            </div>
          </div>
        `;
      } else if (pg.total) {
        html += `
          <div class="mt-3">
            <div class="small text-muted">${pg.total} total</div>
          </div>
        `;
      }

      contentEl.innerHTML = html;
      contentEl.classList.remove("d-none");
      paymentsLoaded = true;
    } catch (err) {
      console.error("Error loading payments:", err);
      loadingEl.classList.add("d-none");
      emptyEl.classList.remove("d-none");
      emptyEl.innerHTML = `<div class="alert alert-danger mb-0">Error loading payments: ${err.message}</div>`;
    }
  }

  // Pagination click handler for payments
  document.addEventListener("click", function (e) {
    const btn = e.target.closest(".js-payments-page");
    if (!btn || btn.classList.contains("disabled")) return;
    const page = parseInt(btn.dataset.page, 10);
    if (page >= 1) loadPaymentsData(page);
  });

  // Listen for Payments tab activation
  if (!body || body.dataset.workOrdersPaymentsTabBound !== "1") {
    if (body) body.dataset.workOrdersPaymentsTabBound = "1";
    document.addEventListener("shown.bs.tab", function (event) {
      if (event?.target?.id !== "tab-payments") return;
      if (!paymentsLoaded) {
        loadPaymentsData();
      }
    });
  }

  if (!body || body.dataset.workOrdersDeletePaymentBound !== "1") {
    if (body) body.dataset.workOrdersDeletePaymentBound = "1";
    document.addEventListener("click", async function (event) {
      const btn = event.target.closest(".js-delete-work-order-payment");
      if (!btn) return;

      const paymentId = String(btn.dataset.paymentId || "").trim();
      if (!paymentId) return;

      if (!await appConfirm("Delete this payment?")) return;

      const originalText = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Deleting...";

      try {
        await postJson(`/work_orders/api/payments/${encodeURIComponent(paymentId)}/delete`, {});
        appAlert("Payment deleted successfully!", 'success');
        window.location.reload();
      } catch (err) {
        appAlert(err.message || "Failed to delete payment.", 'error');
        btn.disabled = false;
        btn.textContent = originalText;
      }
    });
  }

  if (!body || body.dataset.workOrdersDeleteWorkOrderBound !== "1") {
    if (body) body.dataset.workOrdersDeleteWorkOrderBound = "1";
    document.addEventListener("click", async function (event) {
      const btn = event.target.closest(".js-delete-work-order");
      if (!btn) return;

      const workOrderId = String(btn.dataset.workOrderId || "").trim();
      if (!workOrderId) return;

      const ok = await appConfirm(
        "Delete this work order? Any parts used will be returned to inventory and all payments will be removed. This cannot be undone.",
        { title: "Delete work order?", confirmText: "Delete", icon: "warning" }
      );
      if (!ok) return;

      const originalText = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Deleting...";

      try {
        await postJson(`/work_orders/api/work_orders/${encodeURIComponent(workOrderId)}/delete`, {});
        appAlert("Work order deleted.", 'success');
        window.location.reload();
      } catch (err) {
        appAlert(err.message || "Failed to delete work order.", 'error');
        btn.disabled = false;
        btn.textContent = originalText;
      }
    });
  }

  // ========== TAB PERSISTENCE LOGIC ==========
  const workOrdersTabIds = ["tab-work-orders", "tab-payments", "tab-estimates"];
  const allTabs = workOrdersTabIds
    .map((id) => document.getElementById(id))
    .filter((el) => !!el);

  const tabIdByPaneId = {
    "content-work-orders": "tab-work-orders",
    "content-payments": "tab-payments",
    "content-estimates": "tab-estimates",
  };

  const paneIdByTabId = {
    "tab-work-orders": "content-work-orders",
    "tab-payments": "content-payments",
    "tab-estimates": "content-estimates",
  };

  function activateTabFallback(tabId) {
    const paneId = paneIdByTabId[tabId];
    if (!paneId) return;

    allTabs.forEach((btn) => {
      const isActive = btn.id === tabId;
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
    });

    Object.entries(paneIdByTabId).forEach(([tid, pid]) => {
      const pane = document.getElementById(pid);
      if (!pane) return;
      const isActive = tid === tabId;
      pane.classList.toggle("active", isActive);
      pane.classList.toggle("show", isActive);
    });
  }

  function restoreSavedTab() {
    let desiredTabId = null;

    const hashPaneId = String(window.location.hash || "").replace(/^#/, "").trim();
    if (hashPaneId && tabIdByPaneId[hashPaneId]) {
      desiredTabId = tabIdByPaneId[hashPaneId];
    }

    if (!desiredTabId) {
      const savedTabId = safeGetLocalStorage(WORK_ORDERS_ACTIVE_TAB_KEY);
      if (savedTabId && workOrdersTabIds.includes(savedTabId)) {
        desiredTabId = savedTabId;
      }
    }

    if (!desiredTabId) return;

    const savedTabButton = document.getElementById(desiredTabId);
    if (!savedTabButton) return;

    try {
      if (window.bootstrap?.Tab?.getOrCreateInstance) {
        window.bootstrap.Tab.getOrCreateInstance(savedTabButton).show();
      } else {
        activateTabFallback(desiredTabId);
      }
    } catch {
      activateTabFallback(desiredTabId);
    }
  }

  allTabs.forEach((tabBtn) => {
    tabBtn.addEventListener("click", function (event) {
      const clickedTabId = event?.currentTarget?.id;
      if (clickedTabId) {
        safeSetLocalStorage(WORK_ORDERS_ACTIVE_TAB_KEY, clickedTabId);
      }
    });

    tabBtn.addEventListener("shown.bs.tab", function (event) {
      const activatedTabId = event?.target?.id;
      if (activatedTabId) {
        safeSetLocalStorage(WORK_ORDERS_ACTIVE_TAB_KEY, activatedTabId);
        const paneId = paneIdByTabId[activatedTabId];
        if (paneId) {
          window.location.hash = paneId;
        }
      }
    });
  });

  if (!body || body.dataset.workOrdersWindowHooksBound !== "1") {
    if (body) body.dataset.workOrdersWindowHooksBound = "1";
    window.addEventListener("load", restoreSavedTab);
    window.addEventListener("roobico:content-replaced", function () {
      paymentsLoaded = false;
      _paymentsCurrentPage = 1;
      _estimatesLoaded = false;
      restoreSavedTab();
    });
  }

  // ========== LAZY LOAD ESTIMATES TAB ==========
  var _estimatesLoaded = false;

  function _esc(val) {
    return String(val == null ? "" : val)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function _money(n) {
    var x = Number(n || 0);
    return Number.isFinite(x) ? x.toFixed(2) : "0.00";
  }

  function loadEstimates() {
    if (_estimatesLoaded) return;
    _estimatesLoaded = true;

    var loadingEl = document.getElementById("estimates-loading");
    var contentEl = document.getElementById("estimates-content");
    var emptyEl = document.getElementById("estimates-empty");
    var tbody = document.getElementById("estimates-tbody");
    var paginationEl = document.getElementById("estimates-pagination");

    if (!tbody) return;

    if (loadingEl) loadingEl.classList.remove("d-none");
    if (contentEl) contentEl.classList.add("d-none");
    if (emptyEl) emptyEl.classList.add("d-none");

    var params = new URLSearchParams(window.location.search);
    params.delete("tab");
    params.delete("page");
    params.delete("per_page");

    fetch("/work_orders/api/estimates?" + params.toString(), {
      method: "GET",
      headers: { "Accept": "application/json" }
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (loadingEl) loadingEl.classList.add("d-none");

        if (!data || !data.ok || !data.estimates || !data.estimates.length) {
          if (emptyEl) emptyEl.classList.remove("d-none");
          return;
        }

        tbody.innerHTML = "";
        data.estimates.forEach(function (e) {
          var tr = document.createElement("tr");
          tr.innerHTML =
            '<td><span class="badge bg-secondary">' + _esc(e.wo_number || "-") + '</span></td>' +
            '<td>' + _esc(e.customer) + '</td>' +
            '<td>' + _esc(e.date) + '</td>' +
            '<td>' + _esc(e.unit) + '</td>' +
            '<td><span class="badge bg-info text-dark">' + _esc((e.status || "estimate").charAt(0).toUpperCase() + (e.status || "estimate").slice(1)) + '</span></td>' +
            '<td class="text-end">$' + _money(e.labor_total) + '</td>' +
            '<td class="text-end">$' + _money(e.parts_total) + '</td>' +
            '<td class="text-end">$' + _money(e.sales_tax_total) + '</td>' +
            '<td class="text-end fw-semibold">$' + _money(e.grand_total) + '</td>' +
            '<td class="text-end"><a class="btn btn-outline-primary btn-sm" href="/work_orders/details?work_order_id=' + _esc(e.id) + '" target="_blank" rel="noopener noreferrer">Edit</a></td>';
          tbody.appendChild(tr);
        });

        if (contentEl) contentEl.classList.remove("d-none");

        var pg = data.pagination;
        if (paginationEl && pg && pg.pages > 1) {
          paginationEl.innerHTML =
            '<div class="small text-muted">Page ' + pg.page + ' of ' + pg.pages + ' · ' + pg.total + ' total</div>';
        }
      })
      .catch(function () {
        if (loadingEl) loadingEl.classList.add("d-none");
        if (emptyEl) {
          emptyEl.textContent = "Failed to load estimates.";
          emptyEl.classList.remove("d-none");
        }
      });
  }

  var estimatesTab = document.getElementById("tab-estimates");
  if (estimatesTab) {
    estimatesTab.addEventListener("shown.bs.tab", loadEstimates);
    estimatesTab.addEventListener("click", function () {
      setTimeout(loadEstimates, 50);
    });
  }

})();
