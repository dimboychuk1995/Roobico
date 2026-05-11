/* eslint-disable */
(function () {
  "use strict";

  const D = window.ROLES_DATA;
  if (!D) return;

  // ── icons per group key (svg path data) ────────────────────────────
  const GROUP_ICONS = {
    dashboard:     '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>',
    calendar:      '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>',
    customers:     '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3"/><path d="M3 20a6 6 0 0 1 12 0"/><circle cx="17" cy="9" r="2.5"/><path d="M15 20a4 4 0 0 1 6 0"/></svg>',
    vendors:       '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 9h20l-1.5 11a2 2 0 0 1-2 1.7H5.5a2 2 0 0 1-2-1.7L2 9z"/><path d="M7 9V6a5 5 0 0 1 10 0v3"/></svg>',
    parts:         '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a4 4 0 1 1-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-3 3-2-2 3-3z"/></svg>',
    parts_orders:  '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 3h5v5"/><path d="M21 3 11 13"/><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"/></svg>',
    work_orders:   '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h8M8 11h8M8 15h5"/></svg>',
    attachments:   '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.4 11.05 12.5 20a5 5 0 0 1-7.07-7.07l9-9a3.5 3.5 0 0 1 4.95 4.95l-9 9a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>',
    reports:       '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 14l3-3 3 3 5-5"/></svg>',
    import_export: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m6 9 6-6 6 6"/><path d="M5 21h14"/></svg>',
    settings:      '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  };
  function groupIcon(key) { return GROUP_ICONS[key] || GROUP_ICONS.settings; }

  // Acronym for role icon (e.g. "Senior Mechanic" → "SM")
  function roleAcronym(name) {
    if (!name) return "?";
    const parts = String(name).trim().split(/\s+/).slice(0, 2);
    return parts.map(p => p.charAt(0).toUpperCase()).join("") || "?";
  }

  // ── state ───────────────────────────────────────────────────────────
  const state = {
    roles: [],
    selectedId: null,
    selectedPerms: new Set(),
    dirty: false,
    openGroups: new Set(),
    filter: "",
  };

  // ── DOM ─────────────────────────────────────────────────────────────
  const $list = document.getElementById("roles-list");
  const $treeWrap = document.getElementById("permissions-tree");
  const $editor = document.getElementById("role-editor");
  const $editorEmpty = document.getElementById("role-editor-empty");
  const $name = document.getElementById("role-name-input");
  const $meta = document.getElementById("role-meta");
  const $btnNew = document.getElementById("btn-new-role");
  const $btnSave = document.getElementById("btn-save-role");
  const $btnDelete = document.getElementById("btn-delete-role");
  const $btnClone = document.getElementById("btn-clone-role");
  const $btnExpand = document.getElementById("btn-expand-all");
  const $btnCollapse = document.getElementById("btn-collapse-all");
  const $btnSelectAll = document.getElementById("btn-select-all");
  const $btnClearAll = document.getElementById("btn-clear-all");
  const $filter = document.getElementById("perm-filter");
  const $summary = document.getElementById("perm-summary");

  // ── helpers ─────────────────────────────────────────────────────────
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
    try { data = await res.json(); } catch (e) { /* ignore */ }
    if (!res.ok || (data && data.ok === false)) {
      throw new Error((data && data.error) || `HTTP ${res.status}`);
    }
    return data;
  }
  function selectedRole() { return state.roles.find(r => r.id === state.selectedId) || null; }
  function isCurrentProtected() { const r = selectedRole(); return !!(r && r.is_protected); }
  function setDirty(v) {
    state.dirty = !!v;
    $btnSave.disabled = !state.dirty || !state.selectedId || isCurrentProtected();
  }

  // ── roles list ──────────────────────────────────────────────────────
  function renderRolesList() {
    if (!state.roles.length) {
      $list.innerHTML = `
        <li class="rp-empty" style="min-height:140px;">
          <div class="rp-empty-hint">No roles yet.</div>
        </li>`;
      return;
    }
    $list.innerHTML = state.roles.map(r => {
      const active = r.id === state.selectedId ? " is-active" : "";
      const tags = [];
      if (r.is_protected) tags.push('<span class="rp-tag rp-tag-protected" title="Cannot be edited">Protected</span>');
      else if (r.is_system) tags.push('<span class="rp-tag rp-tag-system">System</span>');
      else tags.push('<span class="rp-tag rp-tag-custom">Custom</span>');
      if (r.user_count) tags.push(`<span class="rp-tag rp-tag-users">${r.user_count} user${r.user_count===1?"":"s"}</span>`);
      return `
        <li class="rp-role-row${active}" data-id="${r.id}">
          <div class="rp-role-icon">${escapeHtml(roleAcronym(r.name))}</div>
          <div class="rp-role-meta">
            <div class="rp-role-name">${escapeHtml(r.name)}</div>
            <div class="rp-role-key">${escapeHtml(r.key)} · ${(r.permissions || []).length} perms</div>
          </div>
          <div class="rp-role-tags">${tags.join("")}</div>
        </li>`;
    }).join("");
    $list.querySelectorAll(".rp-role-row").forEach(el => {
      el.addEventListener("click", () => selectRole(el.dataset.id));
    });
  }

  // ── tree ────────────────────────────────────────────────────────────
  function renderTree() {
    const groups = D.permissionGroups || [];
    const labels = D.permissionLabels || {};
    const filter = (state.filter || "").trim().toLowerCase();

    const html = groups.map(g => {
      const items = (g.items || []).filter(pk => {
        if (!filter) return true;
        const lbl = (labels[pk] || pk).toLowerCase();
        return pk.toLowerCase().includes(filter) || lbl.includes(filter);
      });
      if (filter && !items.length) return "";

      const total = (g.items || []).length;
      let on = 0;
      (g.items || []).forEach(pk => { if (state.selectedPerms.has(pk)) on++; });
      const countCls = on === 0 ? "" : (on === total ? "is-full" : "is-partial");
      const isOpen = state.openGroups.has(g.key) || !!filter;

      const itemsHtml = items.map(pk => {
        const checked = state.selectedPerms.has(pk);
        const label = labels[pk] || pk;
        return `
          <label class="rp-perm${checked ? " is-checked" : ""}" data-perm="${escapeHtml(pk)}">
            <input type="checkbox" class="perm-check" data-perm="${escapeHtml(pk)}" ${checked ? "checked" : ""}>
            <div class="rp-perm-text">
              <div class="rp-perm-label">${escapeHtml(label)}</div>
              <div class="rp-perm-key">${escapeHtml(pk)}</div>
            </div>
          </label>`;
      }).join("");

      return `
        <div class="rp-group${isOpen ? " is-open" : ""}" data-group="${escapeHtml(g.key)}">
          <div class="rp-group-head" data-toggle-group="${escapeHtml(g.key)}">
            <svg class="rp-group-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>
            <span class="rp-group-icon">${groupIcon(g.key)}</span>
            <span class="rp-group-title">${escapeHtml(g.label)}</span>
            <span class="rp-group-count ${countCls}" data-counter="${escapeHtml(g.key)}">${on}/${total}</span>
            <span class="rp-group-actions">
              <button type="button" class="rp-mini-btn group-all" data-group="${escapeHtml(g.key)}">All</button>
              <button type="button" class="rp-mini-btn group-none" data-group="${escapeHtml(g.key)}">None</button>
            </span>
          </div>
          <div class="rp-group-body${isOpen ? "" : " is-hidden"}" data-body="${escapeHtml(g.key)}">${itemsHtml}</div>
        </div>`;
    }).join("");

    $treeWrap.innerHTML = html || `
      <div class="rp-empty">
        <div class="rp-empty-icon">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
        </div>
        <div class="rp-empty-title">No matching permissions</div>
      </div>`;

    // wire
    const protectedNow = isCurrentProtected();
    $treeWrap.querySelectorAll(".perm-check").forEach(cb => {
      cb.disabled = protectedNow;
      cb.addEventListener("change", () => {
        const pk = cb.dataset.perm;
        if (cb.checked) state.selectedPerms.add(pk);
        else state.selectedPerms.delete(pk);
        const row = cb.closest(".rp-perm");
        if (row) row.classList.toggle("is-checked", cb.checked);
        updateGroupCounters();
        updateSummary();
        setDirty(true);
      });
    });
    $treeWrap.querySelectorAll("[data-toggle-group]").forEach(head => {
      head.addEventListener("click", (e) => {
        if (e.target.closest(".group-all,.group-none")) return;
        const key = head.dataset.toggleGroup;
        if (state.openGroups.has(key)) state.openGroups.delete(key);
        else state.openGroups.add(key);
        const grp = $treeWrap.querySelector(`.rp-group[data-group="${cssEscape(key)}"]`);
        const body = $treeWrap.querySelector(`[data-body="${cssEscape(key)}"]`);
        if (grp) grp.classList.toggle("is-open");
        if (body) body.classList.toggle("is-hidden");
      });
    });
    $treeWrap.querySelectorAll(".group-all").forEach(b => {
      b.disabled = protectedNow;
      b.addEventListener("click", (e) => { e.stopPropagation(); toggleGroup(b.dataset.group, true); });
    });
    $treeWrap.querySelectorAll(".group-none").forEach(b => {
      b.disabled = protectedNow;
      b.addEventListener("click", (e) => { e.stopPropagation(); toggleGroup(b.dataset.group, false); });
    });

    updateSummary();
  }

  function toggleGroup(groupKey, on) {
    if (isCurrentProtected()) return;
    const g = (D.permissionGroups || []).find(x => x.key === groupKey);
    if (!g) return;
    (g.items || []).forEach(pk => {
      if (on) state.selectedPerms.add(pk);
      else state.selectedPerms.delete(pk);
    });
    state.openGroups.add(groupKey);
    renderTree();
    setDirty(true);
  }

  function updateGroupCounters() {
    (D.permissionGroups || []).forEach(g => {
      const total = (g.items || []).length;
      let on = 0;
      (g.items || []).forEach(pk => { if (state.selectedPerms.has(pk)) on++; });
      const el = $treeWrap.querySelector(`[data-counter="${cssEscape(g.key)}"]`);
      if (el) {
        el.textContent = `${on}/${total}`;
        el.classList.remove("is-full", "is-partial");
        if (on === total && total > 0) el.classList.add("is-full");
        else if (on > 0) el.classList.add("is-partial");
      }
    });
  }

  function updateSummary() {
    let total = 0;
    (D.permissionGroups || []).forEach(g => { total += (g.items || []).length; });
    $summary.textContent = `${state.selectedPerms.size} / ${total}`;
  }

  // ── select / load role ─────────────────────────────────────────────
  async function selectRole(id) {
    if (id === state.selectedId) return;            // ← не спрашиваем, если та же роль
    if (state.dirty) {
      const ok = await (window.appConfirm
        ? window.appConfirm("Discard your unsaved changes?", {
            title: "Unsaved changes", icon: "warning",
            confirmText: "Discard", cancelText: "Keep editing",
          })
        : Promise.resolve(window.confirm("You have unsaved changes. Discard them?")));
      if (!ok) return;
    }
    state.selectedId = id;
    const r = selectedRole();
    if (!r) { hideEditor(); return; }
    state.selectedPerms = new Set(r.permissions || []);
    state.openGroups = new Set();
    showEditor(r);
    renderTree();
    renderRolesList();
    setDirty(false);
  }

  function hideEditor() {
    $editor.classList.add("d-none");
    $editorEmpty.classList.remove("d-none");
    $name.value = "";
    $name.disabled = true;
    $name.placeholder = "Select a role to edit";
    $meta.innerHTML = "";
    $btnSave.disabled = true;
    $btnDelete.disabled = true;
    $btnClone.disabled = true;
  }

  function showEditor(r) {
    $editor.classList.remove("d-none");
    $editorEmpty.classList.add("d-none");
    $name.value = r.name || "";
    $name.disabled = !!r.is_protected;
    $name.placeholder = "Role name";

    const tags = [];
    if (r.is_protected) tags.push('<span class="rp-tag rp-tag-protected">Protected · read-only</span>');
    else if (r.is_system) tags.push('<span class="rp-tag rp-tag-system">System</span>');
    else tags.push('<span class="rp-tag rp-tag-custom">Custom</span>');
    tags.push(`<code>${escapeHtml(r.key)}</code>`);
    if (r.user_count) tags.push(`<span class="rp-tag rp-tag-users">${r.user_count} user${r.user_count===1?"":"s"}</span>`);
    $meta.innerHTML = tags.join("");

    $btnDelete.disabled = !!r.is_system;
    $btnClone.disabled = false;
    $btnSave.disabled = true;
  }

  // ── data load ──────────────────────────────────────────────────────
  async function loadRoles(selectId) {
    try {
      const data = await api(D.endpoints.listRoles);
      state.roles = data.roles || [];
      renderRolesList();
      if (selectId) {
        // принудительный select после save/clone — обходим защиту
        state.selectedId = null;
        state.dirty = false;
        await selectRole(selectId);
      } else if (state.selectedId && selectedRole()) {
        const id = state.selectedId;
        state.selectedId = null;
        state.dirty = false;
        await selectRole(id);
      } else {
        hideEditor();
      }
    } catch (e) {
      $list.innerHTML = `<li class="rp-empty"><div class="rp-empty-hint text-danger">Failed to load: ${escapeHtml(e.message)}</div></li>`;
    }
  }

  // ── actions ────────────────────────────────────────────────────────
  async function promptText(message, opts) {
    opts = opts || {};
    if (typeof Swal !== "undefined") {
      const noAnim = { popup: "", backdrop: "" };
      const res = await Swal.fire({
        title: opts.title || message,
        input: "text",
        inputPlaceholder: opts.placeholder || "",
        inputValue: opts.value || "",
        showCancelButton: true,
        confirmButtonText: opts.confirmText || "Create",
        cancelButtonText: "Cancel",
        confirmButtonColor: "#1a7a42",
        cancelButtonColor: "#6c757d",
        showClass: noAnim, hideClass: noAnim,
        inputValidator: (v) => (!v || !v.trim()) ? "Required" : undefined,
      });
      return res.isConfirmed ? (res.value || "").trim() : null;
    }
    const v = window.prompt(message, opts.value || "");
    return v && v.trim() ? v.trim() : null;
  }
  function notifyError(msg) { (window.appAlert || window.alert)(msg, "error"); }
  function notifySuccess(msg) { (window.appAlert || window.alert)(msg, "success"); }

  $btnNew.addEventListener("click", async () => {
    const name = await promptText("New role name", { title: "Create new role", confirmText: "Create" });
    if (!name) return;
    try {
      const res = await api(D.endpoints.createRole, {
        method: "POST",
        body: JSON.stringify({ name: name, permissions: [] }),
      });
      state.dirty = false;
      await loadRoles(res.role && res.role.id);
    } catch (e) { notifyError("Failed to create role: " + e.message); }
  });

  $btnSave.addEventListener("click", async () => {
    if (!state.selectedId) return;
    const r = selectedRole();
    if (!r || r.is_protected) return;
    const newName = $name.value.trim();
    if (!newName) { notifyError("Role name cannot be empty."); return; }
    $btnSave.disabled = true;
    try {
      await api(fmtUrl(D.endpoints.updateRoleTpl, state.selectedId), {
        method: "PUT",
        body: JSON.stringify({ name: newName, permissions: Array.from(state.selectedPerms) }),
      });
      state.dirty = false;                          // ← сброс ДО reload
      const id = state.selectedId;
      await loadRoles(id);
      notifySuccess("Role saved");
    } catch (e) {
      notifyError("Failed to save: " + e.message);
      setDirty(true);
    }
  });

  $btnDelete.addEventListener("click", async () => {
    if (!state.selectedId) return;
    const r = selectedRole();
    if (!r || r.is_system) return;
    const ok = await (window.appConfirm
      ? window.appConfirm(`This cannot be undone.`, {
          title: `Delete role "${r.name}"?`, icon: "warning",
          confirmText: "Delete", cancelText: "Cancel",
        })
      : Promise.resolve(window.confirm(`Delete role "${r.name}"?`)));
    if (!ok) return;
    try {
      await api(fmtUrl(D.endpoints.deleteRoleTpl, state.selectedId), { method: "DELETE" });
      state.selectedId = null; state.selectedPerms = new Set(); state.dirty = false;
      hideEditor(); await loadRoles();
      notifySuccess("Role deleted");
    } catch (e) { notifyError("Failed to delete: " + e.message); }
  });

  $btnClone.addEventListener("click", async () => {
    if (!state.selectedId) return;
    try {
      const res = await api(fmtUrl(D.endpoints.cloneRoleTpl, state.selectedId), { method: "POST" });
      state.dirty = false;
      await loadRoles(res.role && res.role.id);
    } catch (e) { notifyError("Failed to clone: " + e.message); }
  });

  $name.addEventListener("input", () => setDirty(true));

  $btnExpand.addEventListener("click", () => {
    (D.permissionGroups || []).forEach(g => state.openGroups.add(g.key));
    renderTree();
  });
  $btnCollapse.addEventListener("click", () => {
    state.openGroups.clear();
    renderTree();
  });
  $btnSelectAll.addEventListener("click", () => {
    if (isCurrentProtected()) return;
    (D.permissionGroups || []).forEach(g => (g.items || []).forEach(p => state.selectedPerms.add(p)));
    renderTree(); setDirty(true);
  });
  $btnClearAll.addEventListener("click", () => {
    if (isCurrentProtected()) return;
    state.selectedPerms.clear();
    renderTree(); setDirty(true);
  });

  let filterTimer = null;
  $filter.addEventListener("input", () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(() => {
      state.filter = $filter.value;
      renderTree();
    }, 120);
  });

  // ── utils ──────────────────────────────────────────────────────────
  function cssEscape(s) {
    if (window.CSS && CSS.escape) return CSS.escape(s);
    return String(s).replace(/[^a-zA-Z0-9_\-]/g, "\\$&");
  }

  // ── boot ───────────────────────────────────────────────────────────
  loadRoles();
})();
