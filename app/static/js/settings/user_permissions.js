/* eslint-disable */
/* global bootstrap */
(function () {
  "use strict";

  const E = window.USER_PERMS_ENDPOINTS;
  if (!E) return;

  const modalEl = document.getElementById("userPermsModal");
  if (!modalEl) return;
  const bsModal = new bootstrap.Modal(modalEl);

  const $name = document.getElementById("userPermsName");
  const $role = document.getElementById("userPermsRole");
  const $alert = document.getElementById("userPermsAlert");
  const $loading = document.getElementById("userPermsLoading");
  const $body = document.getElementById("userPermsBody");
  const $tbody = document.getElementById("userPermsTbody");
  const $filter = document.getElementById("userPermsFilter");
  const $saveBtn = document.getElementById("userPermsSaveBtn");
  const $statRole = document.getElementById("up-stat-role");
  const $statAllow = document.getElementById("up-stat-allow");
  const $statDeny = document.getElementById("up-stat-deny");
  const $statEff = document.getElementById("up-stat-eff");
  const viewBtns = modalEl.querySelectorAll("[data-up-view]");

  const state = {
    userId: null,
    rolePerms: new Set(),
    allow: new Set(),
    deny: new Set(),
    groups: [],
    labels: {},
    isProtectedRole: false,
    dirty: false,
    view: "all",      // all | overrides | effective
    filter: "",
  };

  function fmtUrl(tpl, id) { return tpl.replace("__id__", encodeURIComponent(id)); }
  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  async function api(url, opts) {
    const res = await fetch(url, Object.assign({
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      credentials: "same-origin",
    }, opts || {}));
    let data = null;
    try { data = await res.json(); } catch (e) {}
    if (!res.ok || (data && data.ok === false)) {
      throw new Error((data && data.error) || `HTTP ${res.status}`);
    }
    return data;
  }
  function setDirty(v) {
    state.dirty = !!v;
    $saveBtn.disabled = !state.dirty || state.isProtectedRole;
  }
  function effective(pk) {
    if (state.isProtectedRole) return true;
    if (state.deny.has(pk)) return false;
    if (state.allow.has(pk)) return true;
    return state.rolePerms.has(pk);
  }

  function rowVisible(pk) {
    if (state.view === "overrides" && !state.allow.has(pk) && !state.deny.has(pk)) return false;
    if (state.view === "effective" && !effective(pk)) return false;
    if (!state.filter) return true;
    const lbl = (state.labels[pk] || pk).toLowerCase();
    return pk.toLowerCase().includes(state.filter) || lbl.includes(state.filter);
  }

  function renderStats() {
    let role = 0, allow = 0, deny = 0, eff = 0;
    state.groups.forEach(g => (g.items || []).forEach(pk => {
      if (state.rolePerms.has(pk)) role++;
      if (state.allow.has(pk)) allow++;
      if (state.deny.has(pk)) deny++;
      if (effective(pk)) eff++;
    }));
    $statRole.textContent = role;
    $statAllow.textContent = allow;
    $statDeny.textContent = deny;
    $statEff.textContent = eff;
  }

  function renderTable() {
    const rows = [];
    state.groups.forEach(g => {
      const items = (g.items || []).filter(rowVisible);
      if (!items.length) return;
      rows.push(`<tr class="rp-up-group"><td colspan="5">${escapeHtml(g.label)}</td></tr>`);
      items.forEach(pk => {
        const fromRole = state.rolePerms.has(pk);
        const isAllow = state.allow.has(pk);
        const isDeny = state.deny.has(pk);
        const eff = effective(pk);
        const disabled = state.isProtectedRole ? "disabled" : "";
        rows.push(`
          <tr data-perm="${escapeHtml(pk)}">
            <td>
              <div style="font-weight:500;color:#1e293b;">${escapeHtml(state.labels[pk] || pk)}</div>
              <div class="text-muted" style="font-size:.72rem;font-family:ui-monospace,Menlo,monospace;">${escapeHtml(pk)}</div>
            </td>
            <td class="text-center">
              ${fromRole ? '<span class="rp-pill-yes">Yes</span>' : '<span class="rp-pill-no">No</span>'}
            </td>
            <td class="rp-toggle-cell">
              <label class="rp-toggle is-allow">
                <input type="checkbox" class="perm-allow" ${isAllow ? "checked" : ""} ${disabled}>
                <span class="slider"></span>
              </label>
            </td>
            <td class="rp-toggle-cell">
              <label class="rp-toggle is-deny">
                <input type="checkbox" class="perm-deny" ${isDeny ? "checked" : ""} ${disabled}>
                <span class="slider"></span>
              </label>
            </td>
            <td class="text-center">
              ${eff ? '<span class="rp-pill-effective-on">Granted</span>' : '<span class="rp-pill-effective-off">Denied</span>'}
            </td>
          </tr>`);
      });
    });

    $tbody.innerHTML = rows.join("") || `
      <tr><td colspan="5" class="text-center text-muted py-4">No matching permissions.</td></tr>`;

    $tbody.querySelectorAll("tr[data-perm]").forEach(tr => {
      const pk = tr.dataset.perm;
      const cbA = tr.querySelector(".perm-allow");
      const cbD = tr.querySelector(".perm-deny");
      cbA.addEventListener("change", () => {
        if (cbA.checked) { state.allow.add(pk); state.deny.delete(pk); }
        else { state.allow.delete(pk); }
        setDirty(true); renderStats(); renderTable();
      });
      cbD.addEventListener("change", () => {
        if (cbD.checked) { state.deny.add(pk); state.allow.delete(pk); }
        else { state.deny.delete(pk); }
        setDirty(true); renderStats(); renderTable();
      });
    });

    renderStats();
  }

  function setView(v) {
    state.view = v;
    viewBtns.forEach(b => {
      const active = b.dataset.upView === v;
      b.classList.toggle("rp-btn-primary", active);
      b.classList.toggle("rp-btn-ghost", !active);
    });
    renderTable();
  }

  async function open(userId, userName) {
    state.userId = userId;
    state.dirty = false;
    state.view = "all";
    state.filter = "";
    $filter.value = "";
    $alert.classList.add("d-none"); $alert.textContent = "";
    $loading.classList.remove("d-none");
    $body.classList.add("d-none");
    $name.textContent = userName || "user";
    $role.textContent = "—";
    $saveBtn.disabled = true;
    bsModal.show();

    try {
      const data = await api(fmtUrl(E.getTpl, userId));
      state.rolePerms = new Set(data.role_permissions || []);
      state.allow = new Set(data.allow_permissions || []);
      state.deny = new Set(data.deny_permissions || []);
      state.groups = data.groups || [];
      state.labels = data.labels || {};
      state.isProtectedRole = !!(data.user && data.user.is_protected_role);
      $role.textContent = (data.user && data.user.role_name) || (data.user && data.user.role) || "—";

      if (state.isProtectedRole) {
        $alert.innerHTML = '<strong>Protected role.</strong> This user has full access. Per-user overrides are disabled.';
        $alert.classList.remove("d-none");
      }
      $loading.classList.add("d-none");
      $body.classList.remove("d-none");
      setView("all");
    } catch (e) {
      $loading.classList.add("d-none");
      $alert.textContent = "Failed to load: " + e.message;
      $alert.classList.remove("d-none");
    }
  }

  let filterTimer = null;
  $filter.addEventListener("input", () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(() => {
      state.filter = ($filter.value || "").trim().toLowerCase();
      renderTable();
    }, 120);
  });

  viewBtns.forEach(b => b.addEventListener("click", () => setView(b.dataset.upView)));

  $saveBtn.addEventListener("click", async () => {
    if (!state.userId || !state.dirty || state.isProtectedRole) return;
    $saveBtn.disabled = true;
    try {
      await api(fmtUrl(E.putTpl, state.userId), {
        method: "PUT",
        body: JSON.stringify({
          allow_permissions: Array.from(state.allow),
          deny_permissions: Array.from(state.deny),
        }),
      });
      state.dirty = false;
      bsModal.hide();
      if (window.appAlert) window.appAlert("Permissions saved", "success");
    } catch (e) {
      $alert.textContent = "Failed to save: " + e.message;
      $alert.classList.remove("d-none");
      $saveBtn.disabled = false;
    }
  });

  document.querySelectorAll(".user-perms-btn").forEach(btn => {
    btn.addEventListener("click", () => open(btn.dataset.userId, btn.dataset.userName));
  });
})();
