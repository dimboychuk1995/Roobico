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
  const body = document.body;

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

      // Show modal
      const modal = new bootstrap.Modal(document.getElementById("paymentModalList"));
      modal.show();
    } catch (err) {
      alert(err.message || "Failed to load payment info.");
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

    if (amount <= 0) {
      alert("Please enter a valid payment amount.");
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
        notes
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

      alert("Payment recorded successfully!");
      currentWorkOrderId = null;
      
      // Refresh payments tab if it's loaded
      if (paymentsLoaded) {
        loadPaymentsData();
      }
    } catch (err) {
      alert(err.message || "Failed to record payment.");
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
    });
  }

  // ========== PAYMENTS TAB LOGIC ==========
  
  async function loadPaymentsData() {
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
      const endpoint = apiParams.toString()
        ? `/work_orders/api/work_orders/all-payments?${apiParams.toString()}`
        : "/work_orders/api/work_orders/all-payments";

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
      loadingEl.classList.add("d-none");

      if (allPaymentsData.length === 0) {
        emptyEl.classList.remove("d-none");
        return;
      }

      // Build payments table
      let html = `
        <div class="table-responsive">
          <table class="table table-sm align-middle">
            <thead>
              <tr>
                <th>Work Order ID</th>
                <th>Customer</th>
                <th>Amount</th>
                <th>Method</th>
                <th>Date</th>
                <th>Notes</th>
                <th class="text-end">Actions</th>
              </tr>
            </thead>
            <tbody>
      `;

      allPaymentsData.forEach(payment => {
        try {
          const createdAt = formatDateMMDDYYYY(payment.created_at);

          const woId = String(payment.work_order_id || "").substring(0, 8) || "—";
          const customer = String(payment.customer || "").trim() || "—";
          const amount = parseFloat(payment.amount) || 0;
          const method = String(payment.payment_method || "cash").toLowerCase();
          const notes = String(payment.notes || "").trim();

          html += `
            <tr>
              <td><code>${woId}</code></td>
              <td>${customer}</td>
              <td class="fw-semibold">$${amount.toFixed(2)}</td>
              <td><span class="badge bg-secondary">${method}</span></td>
              <td><small>${createdAt}</small></td>
              <td>${notes ? `<small>${notes}</small>` : "<small class='text-muted'>—</small>"}</td>
              <td class="text-end"><button type="button" class="btn btn-sm btn-outline-danger js-delete-work-order-payment" data-payment-id="${String(payment.id || "")}" title="Delete payment">Delete</button></td>
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

      if (!window.confirm("Delete this payment?")) return;

      const originalText = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Deleting...";

      try {
        await postJson(`/work_orders/api/payments/${encodeURIComponent(paymentId)}/delete`, {});
        alert("Payment deleted successfully!");
        window.location.reload();
      } catch (err) {
        alert(err.message || "Failed to delete payment.");
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
    window.addEventListener("smallshop:content-replaced", function () {
      paymentsLoaded = false;
      restoreSavedTab();
    });
  }

})();
