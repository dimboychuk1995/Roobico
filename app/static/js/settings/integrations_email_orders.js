// Settings → Integrations → Parts orders from email (per location).
// Markup: templates/components/integration_email_orders_modal.html.
// One switch: toggling saves immediately; the address + instructions appear
// once enabled. Mode stays "auto" (backend still supports "suggest").
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

  const enabledCb = document.getElementById("email-orders-enabled");
  const setupBox = document.getElementById("email-orders-setup");
  const addressInput = document.getElementById("email-orders-address");
  const copyBtn = document.getElementById("email-orders-copy");
  const rotateBtn = document.getElementById("email-orders-rotate");
  const feedback = document.getElementById("email-orders-feedback");
  const inboxBox = document.getElementById("email-orders-inbox");
  const inboxCounts = document.getElementById("email-orders-inbox-counts");
  const inboxRefreshBtn = document.getElementById("email-orders-inbox-refresh");
  const inboxWrap = document.getElementById("email-orders-inbox-wrap");
  const viewModalEl = document.getElementById("email-orders-view-modal");
  const viewBody = document.getElementById("email-orders-view-body");
  const cardBadge = document.querySelector(".js-email-orders-badge");

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function notify(message, type) {
    if (typeof window.appAlert === "function") {
      window.appAlert(message, type || "info");
    } else if (feedback) {
      feedback.innerHTML = `<div class="alert alert-${type === "error" ? "danger" : "success"} py-2">${escapeHtml(message)}</div>`;
    }
  }

  async function confirmDialog(message) {
    if (typeof window.appConfirm === "function") return window.appConfirm(message);
    return window.confirm(message);
  }

  function formatDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleString();
  }

  function applyState(state) {
    if (!state) return;
    const on = !!state.enabled && !!state.has_address;
    if (enabledCb) enabledCb.checked = !!state.enabled;
    if (addressInput) addressInput.value = state.address || "";
    if (setupBox) setupBox.hidden = !on;
    if (cardBadge) {
      cardBadge.textContent = state.enabled ? "On" : "Off";
      cardBadge.classList.toggle("text-bg-success", !!state.enabled);
      cardBadge.classList.toggle("text-bg-secondary", !state.enabled);
    }
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

  // ── the switch (saves on change) ─────────────────────────────────────
  enabledCb?.addEventListener("change", async function () {
    const wanted = enabledCb.checked;
    enabledCb.disabled = true;
    try {
      const data = await postJson(urls.save, { enabled: wanted });
      if (!data.ok) {
        notify(data.error || "Failed to save", "error");
        enabledCb.checked = !wanted;
        return;
      }
      applyState(data.state);
      if (wanted) {
        notify("Turned on. Forward your vendor emails to the address shown.", "success");
        loadInbox();
      } else {
        notify("Turned off. Emails sent to the address are no longer read.", "success");
      }
    } catch (e) {
      notify("Network error: " + e.message, "error");
      enabledCb.checked = !wanted;
    } finally {
      enabledCb.disabled = false;
    }
  });

  rotateBtn?.addEventListener("click", async function () {
    if (!(await confirmDialog("Issue a new address? The current one stops working immediately and you will need to update your forwarding rule."))) return;
    rotateBtn.disabled = true;
    try {
      const data = await postJson(urls.rotate, {});
      if (!data.ok) { notify(data.error || "Failed to issue a new address", "error"); return; }
      applyState(data.state);
      notify("New address: " + data.state.address, "success");
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
    } catch (e) {
      addressInput.select();
      document.execCommand("copy");
    }
    notify("Address copied", "success");
  });

  // ── recent emails ────────────────────────────────────────────────────
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
    if (e.reason) hint.push(e.reason);
    if (e.error) hint.push("Error: " + e.error);
    const attachments = (e.attachments || []).map((a) => escapeHtml(a.filename)).join(", ");

    let result = "";
    if (e.parts_order_id && e.order_number != null) {
      const href = urls.partsOrders + "&open_order=" + encodeURIComponent(e.parts_order_id);
      result = e.order_active === false
        ? `<span class="badge text-bg-secondary">Order #${escapeHtml(e.order_number)} removed</span>`
        : `<a class="badge text-bg-secondary text-decoration-none" href="${href}">Order #${escapeHtml(e.order_number)}</a>`;
    } else if (e.vendor_name || e.items_count) {
      result = `<span class="text-muted">${escapeHtml(e.vendor_name || "")}${e.items_count ? " · " + e.items_count + " line(s)" : ""}</span>`;
    }

    const actions = [];
    actions.push(`<button type="button" class="btn btn-sm btn-outline-secondary js-email-view" data-id="${e.id}">View</button>`);
    if (FORCEABLE.has(e.status)) {
      actions.push(`<button type="button" class="btn btn-sm btn-primary js-email-process" data-id="${e.id}" data-force="1" title="This is an order — create it">Create order</button>`);
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
        inboxCounts.textContent = data.counts ? `(${data.counts.total} · ${data.counts.orders} became orders)` : "";
      }
      if (!data.emails || !data.emails.length) {
        inboxBox.innerHTML = '<div class="text-muted">Nothing yet. Forward a vendor invoice to the address above to test.</div>';
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
  inboxWrap?.addEventListener("show.bs.collapse", loadInbox);

  inboxBox?.addEventListener("click", async function (e) {
    const processBtn = e.target.closest(".js-email-process");
    if (processBtn) {
      const id = processBtn.dataset.id;
      const force = processBtn.dataset.force === "1";
      if (force && !(await confirmDialog("Create a parts order from this email? It gets the Not confirmed flag for you to review."))) return;
      processBtn.disabled = true;
      processBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
      try {
        const data = await postJson(`${urls.inbox}/${encodeURIComponent(id)}/process`, { force });
        notify(data.ok ? (data.message || "Done") : (data.error || "Processing failed"), data.ok ? "success" : "error");
      } catch (err) {
        notify("Network error: " + err.message, "error");
      }
      loadInbox();
      return;
    }

    const ignoreBtn = e.target.closest(".js-email-ignore");
    if (ignoreBtn) {
      ignoreBtn.disabled = true;
      try {
        const data = await postJson(`${urls.inbox}/${encodeURIComponent(ignoreBtn.dataset.id)}/ignore`, {});
        if (!data.ok) notify(data.error || "Failed", "error");
      } catch (err) {
        notify("Network error: " + err.message, "error");
      }
      loadInbox();
      return;
    }

    const viewBtn = e.target.closest(".js-email-view");
    if (viewBtn) openView(viewBtn.dataset.id);
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
    fetch(urls.state, { headers: { "Accept": "application/json" } })
      .then((r) => r.json())
      .then((d) => {
        if (!d || !d.ok) return;
        applyState(d.state);
        if (d.state.enabled) loadInbox();
      })
      .catch(() => {});
  });
})();
