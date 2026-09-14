// Settings → Integrations → Email orders inbox (per location).
// Markup: templates/components/integration_email_orders_modal.html.
(function () {
  const modalEl = document.getElementById("integration-modal-email-orders");
  if (!modalEl) return;

  const urls = {
    state: modalEl.dataset.stateUrl,
    save: modalEl.dataset.saveUrl,
    rotate: modalEl.dataset.rotateUrl,
    inbox: modalEl.dataset.inboxUrl,
    partsOrders: modalEl.dataset.partsOrdersUrl,
  };

  const addressInput = document.getElementById("email-orders-address");
  const copyBtn = document.getElementById("email-orders-copy");
  const rotateBtn = document.getElementById("email-orders-rotate");
  const enabledCb = document.getElementById("email-orders-enabled");
  const saveBtn = document.getElementById("email-orders-save");
  const feedback = document.getElementById("email-orders-feedback");
  const statusLine = document.getElementById("email-orders-status-line");
  const inboxBox = document.getElementById("email-orders-inbox");
  const inboxCounts = document.getElementById("email-orders-inbox-counts");
  const inboxRefreshBtn = document.getElementById("email-orders-inbox-refresh");
  const viewModalEl = document.getElementById("email-orders-view-modal");
  const viewBody = document.getElementById("email-orders-view-body");

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function notify(message, type) {
    if (typeof window.appAlert === "function") {
      window.appAlert(message, type || "info");
    } else {
      feedback.innerHTML = `<div class="alert alert-${type === "error" ? "danger" : "success"} py-2 mb-0">${escapeHtml(message)}</div>`;
    }
  }

  async function confirmDialog(message) {
    if (typeof window.appConfirm === "function") return window.appConfirm(message);
    return window.confirm(message);
  }

  function selectedMode() {
    const checked = modalEl.querySelector('input[name="email-orders-mode"]:checked');
    return checked ? checked.value : "auto";
  }

  function applyState(state) {
    if (!state) return;
    if (addressInput) addressInput.value = state.address || "";
    if (copyBtn) copyBtn.disabled = !state.has_address;
    if (rotateBtn) rotateBtn.disabled = !state.has_address;
    if (enabledCb) enabledCb.checked = !!state.enabled;
    const modeInput = modalEl.querySelector(`input[name="email-orders-mode"][value="${state.mode || "auto"}"]`);
    if (modeInput) modeInput.checked = true;
    const addrEls = document.querySelectorAll(".js-email-orders-address");
    addrEls.forEach((el) => { el.textContent = state.address || ""; });
    if (statusLine) {
      const parts = [];
      parts.push(state.enabled ? "Inbox enabled" : "Inbox paused");
      parts.push(`${state.emails_received || 0} email(s) received`);
      parts.push(`${state.orders_created || 0} order(s) created`);
      if (state.last_email_at) parts.push("last email " + formatDate(state.last_email_at));
      statusLine.textContent = parts.join(" · ");
    }
  }

  function formatDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleString();
  }

  async function postJson(url, payload) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!data) data = { ok: false, error: `HTTP ${res.status}` };
    return data;
  }

  // ── settings ─────────────────────────────────────────────────────────
  saveBtn?.addEventListener("click", async function () {
    saveBtn.disabled = true;
    try {
      const data = await postJson(urls.save, { enabled: !!enabledCb?.checked, mode: selectedMode() });
      if (!data.ok) { notify(data.error || "Failed to save", "error"); return; }
      applyState(data.state);
      notify(data.state.enabled ? "Inbox enabled. Forward your vendor mail to the address shown." : "Settings saved.", "success");
      // Карточка на странице показывает адрес/статус — перерисовать проще перезагрузкой.
      setTimeout(() => window.location.reload(), 900);
    } catch (e) {
      notify("Network error: " + e.message, "error");
    } finally {
      saveBtn.disabled = false;
    }
  });

  rotateBtn?.addEventListener("click", async function () {
    if (!(await confirmDialog("Issue a new mailbox address? The current address stops working immediately and you will need to update your forwarding rule."))) return;
    rotateBtn.disabled = true;
    try {
      const data = await postJson(urls.rotate, {});
      if (!data.ok) { notify(data.error || "Failed to issue a new address", "error"); return; }
      applyState(data.state);
      notify("New address issued: " + data.state.address, "success");
    } catch (e) {
      notify("Network error: " + e.message, "error");
    } finally {
      rotateBtn.disabled = false;
    }
  });

  copyBtn?.addEventListener("click", async function () {
    const value = addressInput?.value || "";
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      notify("Address copied", "success");
    } catch (e) {
      addressInput.select();
      document.execCommand("copy");
      notify("Address copied", "success");
    }
  });

  // ── inbox history ────────────────────────────────────────────────────
  const STATUS_META = {
    order_created: { label: "Order created", cls: "text-bg-success" },
    linked: { label: "Linked to order", cls: "text-bg-info" },
    suggested: { label: "Needs review", cls: "text-bg-warning" },
    ignored: { label: "Ignored", cls: "text-bg-secondary" },
    received: { label: "Waiting", cls: "text-bg-primary" },
    processing: { label: "Processing…", cls: "text-bg-primary" },
    error: { label: "Error", cls: "text-bg-danger" },
    rejected: { label: "Rejected", cls: "text-bg-dark" },
  };
  const FORCEABLE = new Set(["received", "ignored", "suggested", "error", "rejected"]);
  const RETRYABLE = new Set(["received", "error"]);
  const IGNORABLE = new Set(["received", "suggested", "error"]);

  function rowHtml(e) {
    const meta = STATUS_META[e.status] || { label: e.status, cls: "text-bg-light border" };
    const who = e.from_name ? `${e.from_name} <${e.from_email}>` : (e.from_email || "—");
    const hint = [];
    if (e.kind) hint.push(e.kind.replace(/_/g, " "));
    if (typeof e.confidence === "number") hint.push(Math.round(e.confidence * 100) + "%");
    if (e.reason) hint.push(e.reason);
    if (e.error) hint.push("Error: " + e.error);
    const attachments = (e.attachments || []).map((a) => escapeHtml(a.filename)).join(", ");

    let result = "";
    if (e.parts_order_id && e.order_number != null) {
      const href = urls.partsOrders + "&open_order=" + encodeURIComponent(e.parts_order_id) + (e.order_active === false ? "" : "&paid_status=all");
      result = e.order_active === false
        ? `<span class="badge text-bg-secondary">Order #${escapeHtml(e.order_number)} removed</span>`
        : `<a class="badge text-bg-secondary text-decoration-none" href="${href}">Order #${escapeHtml(e.order_number)}</a>`;
    } else if (e.vendor_name || e.items_count) {
      result = `<span class="text-muted">${escapeHtml(e.vendor_name || "")}${e.items_count ? " · " + e.items_count + " line(s)" : ""}</span>`;
    }

    const actions = [];
    actions.push(`<button type="button" class="btn btn-sm btn-outline-secondary js-email-view" data-id="${e.id}">View</button>`);
    if (FORCEABLE.has(e.status)) {
      actions.push(`<button type="button" class="btn btn-sm btn-primary js-email-process" data-id="${e.id}" data-force="1" title="Treat this email as an order and create it">Create order</button>`);
    }
    if (RETRYABLE.has(e.status)) {
      actions.push(`<button type="button" class="btn btn-sm btn-outline-primary js-email-process" data-id="${e.id}" data-force="0" title="Run the AI check again">Retry</button>`);
    }
    if (IGNORABLE.has(e.status)) {
      actions.push(`<button type="button" class="btn btn-sm btn-outline-secondary js-email-ignore" data-id="${e.id}">Ignore</button>`);
    }

    return `<tr data-email-id="${e.id}">
      <td class="text-nowrap text-muted">${escapeHtml(formatDate(e.received_at))}</td>
      <td>
        <div class="fw-semibold text-break">${escapeHtml(e.subject || "(no subject)")}</div>
        <div class="text-muted text-break">${escapeHtml(who)}</div>
        ${attachments ? `<div class="text-muted"><i class="bi bi-paperclip"></i> ${attachments}</div>` : ""}
      </td>
      <td>
        <span class="badge ${meta.cls}">${escapeHtml(meta.label)}</span>
        ${hint.length ? `<div class="text-muted">${escapeHtml(hint.join(" · "))}</div>` : ""}
      </td>
      <td>${result}</td>
      <td class="text-end text-nowrap"><div class="d-inline-flex gap-1 flex-wrap justify-content-end">${actions.join("")}</div></td>
    </tr>`;
  }

  async function loadInbox() {
    if (!inboxBox) return;
    inboxBox.innerHTML = '<div class="text-muted">Loading…</div>';
    try {
      const res = await fetch(urls.inbox + "?limit=50", { headers: { "Accept": "application/json" } });
      const data = await res.json();
      if (!data.ok) {
        inboxBox.innerHTML = `<div class="text-danger">Failed: ${escapeHtml(data.error || "Unknown error")}</div>`;
        return;
      }
      if (inboxCounts) {
        inboxCounts.textContent = data.counts ? `${data.counts.total} email(s) · ${data.counts.orders} became orders` : "";
      }
      if (!data.emails || !data.emails.length) {
        inboxBox.innerHTML = '<div class="text-muted">No emails yet. Forward a vendor invoice to the address above to test.</div>';
        return;
      }
      inboxBox.innerHTML =
        '<div class="table-responsive" style="max-height:420px;overflow:auto;">'
        + '<table class="table table-sm align-middle mb-0"><thead><tr>'
        + '<th>Received</th><th>Email</th><th>Result</th><th>Order</th><th class="text-end">Actions</th>'
        + '</tr></thead><tbody>'
        + data.emails.map(rowHtml).join("")
        + '</tbody></table></div>';
    } catch (e) {
      inboxBox.innerHTML = `<div class="text-danger">Network error: ${escapeHtml(e.message)}</div>`;
    }
  }

  inboxRefreshBtn?.addEventListener("click", loadInbox);

  inboxBox?.addEventListener("click", async function (e) {
    const processBtn = e.target.closest(".js-email-process");
    if (processBtn) {
      const id = processBtn.dataset.id;
      const force = processBtn.dataset.force === "1";
      if (force && !(await confirmDialog("Create a parts order from this email? AI will read the vendor and the lines; the order gets the Not confirmed flag for you to review."))) return;
      processBtn.disabled = true;
      processBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
      try {
        const data = await postJson(`${urls.inbox}/${encodeURIComponent(id)}/process`, { force });
        if (!data.ok) {
          notify(data.error || "Processing failed", "error");
        } else {
          notify(data.message || "Done", "success");
        }
      } catch (err) {
        notify("Network error: " + err.message, "error");
      }
      loadInbox();
      return;
    }

    const ignoreBtn = e.target.closest(".js-email-ignore");
    if (ignoreBtn) {
      const id = ignoreBtn.dataset.id;
      ignoreBtn.disabled = true;
      try {
        const data = await postJson(`${urls.inbox}/${encodeURIComponent(id)}/ignore`, {});
        if (!data.ok) notify(data.error || "Failed", "error");
      } catch (err) {
        notify("Network error: " + err.message, "error");
      }
      loadInbox();
      return;
    }

    const viewBtn = e.target.closest(".js-email-view");
    if (viewBtn) {
      const id = viewBtn.dataset.id;
      openView(id);
    }
  });

  async function openView(id) {
    if (!viewModalEl || !viewBody) return;
    viewBody.innerHTML = '<div class="text-muted">Loading…</div>';
    const ModalClass = window.bootstrap && window.bootstrap.Modal ? window.bootstrap.Modal : null;
    if (ModalClass) ModalClass.getOrCreateInstance(viewModalEl).show();
    try {
      const res = await fetch(`${urls.inbox}/${encodeURIComponent(id)}`, { headers: { "Accept": "application/json" } });
      const data = await res.json();
      if (!data.ok) { viewBody.innerHTML = `<div class="text-danger">${escapeHtml(data.error || "Failed")}</div>`; return; }
      const e = data.email;
      const meta = STATUS_META[e.status] || { label: e.status, cls: "text-bg-light border" };
      let html = `<dl class="row mb-2">
        <dt class="col-sm-3">From</dt><dd class="col-sm-9 text-break">${escapeHtml(e.from_name ? `${e.from_name} <${e.from_email}>` : e.from_email)}</dd>
        <dt class="col-sm-3">To</dt><dd class="col-sm-9 text-break">${escapeHtml((e.to || []).join(", "))}</dd>
        <dt class="col-sm-3">Subject</dt><dd class="col-sm-9 text-break">${escapeHtml(e.subject || "(no subject)")}</dd>
        <dt class="col-sm-3">Received</dt><dd class="col-sm-9">${escapeHtml(formatDate(e.received_at))}</dd>
        <dt class="col-sm-3">Result</dt><dd class="col-sm-9"><span class="badge ${meta.cls}">${escapeHtml(meta.label)}</span> ${e.reason ? escapeHtml(e.reason) : ""}${e.error ? `<div class="text-danger">${escapeHtml(e.error)}</div>` : ""}</dd>
      </dl>`;
      if (e.attachments && e.attachments.length) {
        html += `<div class="mb-2"><strong>Attachments:</strong> ${e.attachments.map((a) => escapeHtml(a.filename)).join(", ")}</div>`;
      }
      if (e.extracted_items && e.extracted_items.length) {
        html += '<div class="mb-1"><strong>Lines the AI read:</strong></div><div class="table-responsive mb-2"><table class="table table-sm table-bordered mb-0"><thead><tr><th>Part #</th><th>Description</th><th class="text-end">Qty</th><th class="text-end">Price</th></tr></thead><tbody>';
        for (const it of e.extracted_items) {
          html += `<tr><td>${escapeHtml(it.part_number)}</td><td>${escapeHtml(it.description)}</td><td class="text-end">${escapeHtml(it.quantity)}</td><td class="text-end">$${Number(it.price || 0).toFixed(2)}</td></tr>`;
        }
        html += "</tbody></table></div>";
      }
      html += `<div class="mb-1"><strong>Text:</strong></div><pre class="border rounded p-2 mb-0" style="white-space:pre-wrap;max-height:360px;overflow:auto;">${escapeHtml(e.text || "(empty)")}</pre>`;
      viewBody.innerHTML = html;
    } catch (err) {
      viewBody.innerHTML = `<div class="text-danger">Network error: ${escapeHtml(err.message)}</div>`;
    }
  }

  modalEl.addEventListener("shown.bs.modal", function () {
    loadInbox();
    fetch(urls.state, { headers: { "Accept": "application/json" } })
      .then((r) => r.json())
      .then((d) => { if (d && d.ok) applyState(d.state); })
      .catch(() => {});
  });
})();
