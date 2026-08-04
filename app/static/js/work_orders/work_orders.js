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
      const sortBy = String(params.get("sort_by") || "").trim();
      const sortDir = String(params.get("sort_dir") || "").trim();
      if (sortBy) apiParams.set("sort_by", sortBy);
      if (sortDir) apiParams.set("sort_dir", sortDir);
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
                <th data-sort-field="customer">Customer</th>
                <th data-sort-field="amount">Amount</th>
                <th data-sort-field="payment_method">Method</th>
                <th data-sort-field="payment_date">Date</th>
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
      const customerBadge = payment.customer_inactive
        ? ' <span class="badge text-bg-secondary" title="This customer is deactivated">Inactive</span>'
        : "";
          const amount = parseFloat(payment.amount) || 0;
          const method = String(payment.payment_method || "cash").toLowerCase();
          const notes = String(payment.notes || "").trim();
          const paymentId = String(payment.id || "");

          html += `
            <tr>
              <td><span class="badge bg-secondary">${woNumber}</span></td>
              <td>${customer}${customerBadge}</td>
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

      // Totals (по всей отфильтрованной выборке, не только по странице) + pagination
      const totals = data.totals || {};
      let totalsHtml = "";
      if (totals.count) {
        const byMethod = totals.by_method || {};
        const methodSpans = Object.keys(byMethod)
          .map(m => `<span>${m}: <strong>$${(parseFloat(byMethod[m]) || 0).toFixed(2)}</strong></span>`)
          .join("");
        totalsHtml = `
          <div class="wo-totals-center wo-pagination-totals">
            <div class="wo-totals-box">
              <span>Payments: <strong>${totals.count}</strong></span>
              ${methodSpans}
              <span>Total: <strong>$${(parseFloat(totals.amount_total) || 0).toFixed(2)}</strong></span>
            </div>
          </div>
        `;
      }

      if (pg.total) {
        const prevDisabled = !pg.has_prev ? " disabled" : "";
        const nextDisabled = !pg.has_next ? " disabled" : "";
        const pager = (pg.pages && pg.pages > 1) ? `
              <div class="btn-group btn-group-sm" role="group" aria-label="Payments pagination">
                <button type="button" class="btn btn-outline-secondary js-payments-page${prevDisabled}" data-page="${pg.prev_page}"${prevDisabled ? ' tabindex="-1"' : ""}>Prev</button>
                <button type="button" class="btn btn-outline-secondary js-payments-page${nextDisabled}" data-page="${pg.next_page}"${nextDisabled ? ' tabindex="-1"' : ""}>Next</button>
              </div>` : "";
        html += `
          <div class="wo-pagination-row mt-3">
            <div class="small text-muted wo-pagination-meta">
              Page ${pg.page || 1} of ${pg.pages || 1} &middot; ${pg.total} total
            </div>
            ${totalsHtml}
            <div class="wo-pagination-actions">${pager}</div>
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
  if (!body || body.dataset.workOrdersPaymentsPagingBound !== "1") {
    if (body) body.dataset.workOrdersPaymentsPagingBound = "1";
    document.addEventListener("click", function (e) {
      const btn = e.target.closest(".js-payments-page");
      if (!btn || btn.classList.contains("disabled")) return;
      const page = parseInt(btn.dataset.page, 10);
      if (page >= 1) loadPaymentsData(page);
    });
  }

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

  // ========== BULK PAYMENT LOGIC ==========
  // Один платёж клиента распределяется по нескольким неоплаченным инвойсам.
  let _bulkPayInvoices = [];
  let _bulkPayCustomerId = "";

  function _bulkPayEl(id) {
    return document.getElementById(id);
  }

  function _bulkPayResetInvoicesView() {
    _bulkPayInvoices = [];
    const tbody = _bulkPayEl("bulkPayInvoicesBody");
    if (tbody) tbody.innerHTML = "";
    _bulkPayEl("bulkPayInvoicesWrap")?.classList.add("d-none");
    _bulkPayEl("bulkPayInvoicesEmpty")?.classList.add("d-none");
    _bulkPayEl("bulkPayInvoicesLoading")?.classList.add("d-none");
    _bulkPayEl("bulkPayInvoicesHint")?.classList.remove("d-none");
    _bulkPayEl("bulkPaySummary")?.classList.add("d-none");
    const selectAll = _bulkPayEl("bulkPaySelectAll");
    if (selectAll) selectAll.checked = false;
    recomputeBulkPay();
  }

  function _bulkPayResetModal() {
    _bulkPayCustomerId = "";
    const select = _bulkPayEl("bulkPayCustomerSelect");
    if (select) select.innerHTML = '<option value="">Select customer...</option>';
    const amountInput = _bulkPayEl("bulkPayAmountInput");
    if (amountInput) amountInput.value = "";
    const methodInput = _bulkPayEl("bulkPayMethodInput");
    if (methodInput) methodInput.value = "cash";
    const notesInput = _bulkPayEl("bulkPayNotesInput");
    if (notesInput) notesInput.value = "";
    const dateInput = _bulkPayEl("bulkPayDateInput");
    if (dateInput) {
      dateInput.value = dateInput.defaultValue || dateInput.value || "";
      if (dateInput._flatpickr) { dateInput._flatpickr.setDate(dateInput.value || null, false, "Y-m-d"); }
    }
    _bulkPayResetInvoicesView();
  }

  async function _bulkPayLoadCustomers() {
    const select = _bulkPayEl("bulkPayCustomerSelect");
    if (!select) return;
    const data = await getJson("/work_orders/api/bulk-payments/customers");
    (data.customers || []).forEach(function (c) {
      const opt = document.createElement("option");
      opt.value = String(c.id || "");
      const invoices = Number(c.invoices_count || 0);
      opt.textContent = `${c.label} — $${_money(c.balance_due)} due (${invoices} invoice${invoices === 1 ? "" : "s"})`;
      select.appendChild(opt);
    });
  }

  function _bulkPayRenderInvoices() {
    const tbody = _bulkPayEl("bulkPayInvoicesBody");
    if (!tbody) return;

    tbody.innerHTML = _bulkPayInvoices.map(function (inv) {
      return `
        <tr>
          <td class="text-center">
            <input type="checkbox" class="form-check-input bulk-pay-row-check" data-wo-id="${_esc(inv.id)}" aria-label="Select invoice ${_esc(inv.wo_number || "")}">
          </td>
          <td><span class="badge text-bg-secondary">${_esc(inv.wo_number || "-")}</span></td>
          <td>${_esc(inv.date || "-")}</td>
          <td>${_esc(inv.unit || "-")}</td>
          <td class="text-end">$${_money(inv.grand_total)}</td>
          <td class="text-end">$${_money(inv.paid_amount)}</td>
          <td class="text-end fw-semibold">$${_money(inv.balance)}</td>
          <td class="text-end">
            <input type="number" class="form-control form-control-sm text-end ms-auto bulk-pay-alloc-input" style="width: 8rem;"
                   data-wo-id="${_esc(inv.id)}" inputmode="decimal" placeholder="0.00" step="0.01" min="0" max="${_money(inv.balance)}" disabled>
          </td>
        </tr>
      `;
    }).join("");

    const selectAll = _bulkPayEl("bulkPaySelectAll");
    if (selectAll) selectAll.checked = false;
    _bulkPayEl("bulkPayInvoicesWrap")?.classList.remove("d-none");
    recomputeBulkPay();
  }

  async function _bulkPayLoadInvoices(customerId) {
    _bulkPayInvoices = [];
    const tbody = _bulkPayEl("bulkPayInvoicesBody");
    if (tbody) tbody.innerHTML = "";
    _bulkPayEl("bulkPayInvoicesHint")?.classList.add("d-none");
    _bulkPayEl("bulkPayInvoicesWrap")?.classList.add("d-none");
    _bulkPayEl("bulkPayInvoicesEmpty")?.classList.add("d-none");
    _bulkPayEl("bulkPaySummary")?.classList.add("d-none");
    _bulkPayEl("bulkPayInvoicesLoading")?.classList.remove("d-none");

    try {
      const data = await getJson(`/work_orders/api/bulk-payments/customers/${encodeURIComponent(customerId)}/unpaid`);
      _bulkPayInvoices = data.work_orders || [];
      _bulkPayEl("bulkPayInvoicesLoading")?.classList.add("d-none");
      if (!_bulkPayInvoices.length) {
        _bulkPayEl("bulkPayInvoicesEmpty")?.classList.remove("d-none");
        recomputeBulkPay();
        return;
      }
      _bulkPayRenderInvoices();
    } catch (err) {
      _bulkPayEl("bulkPayInvoicesLoading")?.classList.add("d-none");
      _bulkPayEl("bulkPayInvoicesHint")?.classList.remove("d-none");
      appAlert(err.message || "Failed to load unpaid invoices.", 'error');
    }
  }

  function recomputeBulkPay() {
    const checks = Array.from(document.querySelectorAll("#bulkPayInvoicesBody .bulk-pay-row-check"));
    const balances = {};
    _bulkPayInvoices.forEach(function (inv) { balances[inv.id] = Number(inv.balance || 0); });

    let selected = 0;
    let allocated = 0;
    let invalid = false;

    checks.forEach(function (chk) {
      const woId = String(chk.dataset.woId || "");
      const input = document.querySelector(`#bulkPayInvoicesBody .bulk-pay-alloc-input[data-wo-id="${woId}"]`);
      if (!input) return;
      if (!chk.checked) {
        input.disabled = true;
        input.classList.remove("is-invalid");
        return;
      }
      input.disabled = false;
      selected++;
      const val = parseFloat(input.value || "0");
      const balance = balances[woId] || 0;
      if (!(val > 0) || val > balance + 0.005) {
        invalid = true;
        input.classList.add("is-invalid");
      } else {
        input.classList.remove("is-invalid");
        allocated += val;
      }
    });

    allocated = Math.round(allocated * 100) / 100;
    const received = parseFloat(_bulkPayEl("bulkPayAmountInput")?.value || "0") || 0;
    const leftover = Math.round((received - allocated) * 100) / 100;
    const overAllocated = received > 0 && allocated > received + 0.005;

    const selectAll = _bulkPayEl("bulkPaySelectAll");
    if (selectAll) selectAll.checked = checks.length > 0 && selected === checks.length;

    const summary = _bulkPayEl("bulkPaySummary");
    if (summary) summary.classList.toggle("d-none", selected === 0);
    const countEl = _bulkPayEl("bulkPaySelectedCount");
    if (countEl) countEl.textContent = String(selected);
    const allocatedEl = _bulkPayEl("bulkPayAllocatedTotal");
    if (allocatedEl) allocatedEl.textContent = `$${_money(allocated)}`;
    const leftoverRow = _bulkPayEl("bulkPayLeftoverRow");
    if (leftoverRow) leftoverRow.classList.toggle("d-none", !(received > 0 && leftover > 0.005 && !overAllocated));
    const leftoverEl = _bulkPayEl("bulkPayLeftover");
    if (leftoverEl) leftoverEl.textContent = `$${_money(leftover)}`;
    const overRow = _bulkPayEl("bulkPayOverAllocatedRow");
    if (overRow) overRow.classList.toggle("d-none", !overAllocated);

    const distributeBtn = _bulkPayEl("bulkPayDistributeBtn");
    if (distributeBtn) distributeBtn.disabled = !(_bulkPayInvoices.length > 0 && received > 0);
    const submitBtn = _bulkPayEl("bulkPaySubmitBtn");
    if (submitBtn) submitBtn.disabled = !(selected > 0 && allocated > 0 && !invalid && !overAllocated);
  }

  function _bulkPayAutoDistribute() {
    const received = parseFloat(_bulkPayEl("bulkPayAmountInput")?.value || "0") || 0;
    if (!(received > 0)) return;

    // Инвойсы приходят с сервера старыми вперёд — закрываем их по порядку.
    let remaining = received;
    _bulkPayInvoices.forEach(function (inv) {
      const chk = document.querySelector(`#bulkPayInvoicesBody .bulk-pay-row-check[data-wo-id="${inv.id}"]`);
      const input = document.querySelector(`#bulkPayInvoicesBody .bulk-pay-alloc-input[data-wo-id="${inv.id}"]`);
      if (!chk || !input) return;
      const apply = Math.min(Number(inv.balance || 0), remaining);
      if (apply > 0.004) {
        chk.checked = true;
        input.disabled = false;
        input.value = apply.toFixed(2);
        remaining = Math.round((remaining - apply) * 100) / 100;
      } else {
        chk.checked = false;
        input.value = "";
        input.disabled = true;
      }
    });
    recomputeBulkPay();
  }

  async function _bulkPaySubmit(submitBtn) {
    const paymentDate = String(_bulkPayEl("bulkPayDateInput")?.value || "").trim();
    if (!paymentDate) {
      appAlert("Please select payment date.", 'warning');
      return;
    }

    const allocations = [];
    document.querySelectorAll("#bulkPayInvoicesBody .bulk-pay-row-check").forEach(function (chk) {
      if (!chk.checked) return;
      const woId = String(chk.dataset.woId || "");
      const input = document.querySelector(`#bulkPayInvoicesBody .bulk-pay-alloc-input[data-wo-id="${woId}"]`);
      const amount = parseFloat(input?.value || "0");
      if (woId && amount > 0) allocations.push({ work_order_id: woId, amount });
    });

    if (!allocations.length) {
      appAlert("Select at least one invoice and enter an amount.", 'warning');
      return;
    }

    const originalText = submitBtn.textContent;
    submitBtn.disabled = true;
    submitBtn.textContent = "Saving...";

    try {
      const res = await fetch("/work_orders/api/bulk-payments", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({
          customer_id: _bulkPayCustomerId,
          payment_method: _bulkPayEl("bulkPayMethodInput")?.value || "cash",
          notes: _bulkPayEl("bulkPayNotesInput")?.value || "",
          payment_date: paymentDate,
          allocations,
        }),
      });
      let data = null;
      try { data = await res.json(); } catch { data = null; }
      if (!res.ok || !data || data.ok !== true) {
        throw new Error((data && (data.message || data.error)) || "Failed to record bulk payment.");
      }

      const modalEl = _bulkPayEl("bulkPaymentModal");
      const modal = modalEl ? bootstrap.Modal.getInstance(modalEl) : null;
      if (modal) modal.hide();

      appAlert(
        `Recorded ${data.results.length} payment${data.results.length === 1 ? "" : "s"} totaling $${_money(data.applied_total)}. ` +
        `${data.invoices_paid_in_full} invoice${data.invoices_paid_in_full === 1 ? "" : "s"} paid in full.`,
        'success'
      );
      window.location.reload();
    } catch (err) {
      appAlert(err.message || "Failed to record bulk payment.", 'error');
      submitBtn.disabled = false;
      submitBtn.textContent = originalText;
    }
  }

  if (!body || body.dataset.workOrdersBulkPayBound !== "1") {
    if (body) body.dataset.workOrdersBulkPayBound = "1";

    document.addEventListener("click", async function (e) {
      if (e.target.closest("#bulkPaymentBtn")) {
        const modalEl = _bulkPayEl("bulkPaymentModal");
        if (!modalEl) return;
        _bulkPayResetModal();
        new bootstrap.Modal(modalEl).show();
        try {
          await _bulkPayLoadCustomers();
        } catch (err) {
          appAlert(err.message || "Failed to load customers.", 'error');
        }
        return;
      }

      if (e.target.closest("#bulkPayDistributeBtn")) {
        _bulkPayAutoDistribute();
        return;
      }

      const submitBtn = e.target.closest("#bulkPaySubmitBtn");
      if (submitBtn) {
        await _bulkPaySubmit(submitBtn);
      }
    });

    document.addEventListener("change", function (e) {
      if (e.target.id === "bulkPayCustomerSelect") {
        _bulkPayCustomerId = String(e.target.value || "");
        if (_bulkPayCustomerId) {
          _bulkPayLoadInvoices(_bulkPayCustomerId);
        } else {
          _bulkPayResetInvoicesView();
        }
        return;
      }

      if (e.target.id === "bulkPaySelectAll") {
        const checked = !!e.target.checked;
        document.querySelectorAll("#bulkPayInvoicesBody .bulk-pay-row-check").forEach(function (chk) {
          chk.checked = checked;
          const woId = String(chk.dataset.woId || "");
          const input = document.querySelector(`#bulkPayInvoicesBody .bulk-pay-alloc-input[data-wo-id="${woId}"]`);
          const inv = _bulkPayInvoices.find(function (i) { return i.id === woId; });
          if (input && checked && !input.value && inv) input.value = Number(inv.balance || 0).toFixed(2);
          if (input && !checked) input.value = "";
        });
        recomputeBulkPay();
        return;
      }

      if (e.target.classList && e.target.classList.contains("bulk-pay-row-check")) {
        const woId = String(e.target.dataset.woId || "");
        const input = document.querySelector(`#bulkPayInvoicesBody .bulk-pay-alloc-input[data-wo-id="${woId}"]`);
        const inv = _bulkPayInvoices.find(function (i) { return i.id === woId; });
        if (input) {
          if (e.target.checked && !input.value && inv) input.value = Number(inv.balance || 0).toFixed(2);
          if (!e.target.checked) input.value = "";
        }
        recomputeBulkPay();
      }
    });

    document.addEventListener("input", function (e) {
      if (e.target.id === "bulkPayAmountInput" || (e.target.classList && e.target.classList.contains("bulk-pay-alloc-input"))) {
        recomputeBulkPay();
      }
    });
  }

  // ========== TAB PERSISTENCE LOGIC ==========
  const workOrdersTabIds = ["tab-work-orders", "tab-payments", "tab-estimates"];

  // Табы пересоздаются при мягкой подмене контента (public.js) —
  // всегда берём свежие узлы.
  function getAllTabs() {
    return workOrdersTabIds
      .map((id) => document.getElementById(id))
      .filter((el) => !!el);
  }

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

    getAllTabs().forEach((btn) => {
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

  function bindWorkOrdersTabButtons() {
    getAllTabs().forEach((tabBtn) => {
      if (tabBtn.dataset.woTabBound === "1") return;
      tabBtn.dataset.woTabBound = "1";

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
  }

  bindWorkOrdersTabButtons();

  if (!body || body.dataset.workOrdersWindowHooksBound !== "1") {
    if (body) body.dataset.workOrdersWindowHooksBound = "1";
    window.addEventListener("load", restoreSavedTab);
    window.addEventListener("roobico:content-replaced", function () {
      paymentsLoaded = false;
      _paymentsCurrentPage = 1;
      _estimatesLoaded = false;
      // Контент пересоздан — вешаем обработчики на свежие узлы.
      bindWorkOrdersTabButtons();
      bindEstimatesTab();
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
            '<td>' + _esc(e.customer) +
              (e.customer_inactive ? ' <span class="badge text-bg-secondary" title="This customer is deactivated">Inactive</span>' : '') +
            '</td>' +
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

  function bindEstimatesTab() {
    var estimatesTab = document.getElementById("tab-estimates");
    if (!estimatesTab || estimatesTab.dataset.woEstimatesBound === "1") return;
    estimatesTab.dataset.woEstimatesBound = "1";
    estimatesTab.addEventListener("shown.bs.tab", loadEstimates);
    estimatesTab.addEventListener("click", function () {
      setTimeout(loadEstimates, 50);
    });
  }

  bindEstimatesTab();

})();
