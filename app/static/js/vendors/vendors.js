(function () {
  "use strict";

  var ordersState = {
    vendorId: "",
    vendorName: "",
    page: 1,
    perPage: 10,
    pagination: null,
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      if (ch === "&") return "&amp;";
      if (ch === "<") return "&lt;";
      if (ch === ">") return "&gt;";
      if (ch === '"') return "&quot;";
      return "&#39;";
    });
  }

  function getVendorEls() {
    var form = document.getElementById("vendorForm");
    return {
      modal: document.getElementById("createVendorModal"),
      form: form,
      editingVendorId: document.getElementById("editingVendorId"),
      modalTitle: document.getElementById("createVendorModalLabel"),
      submitBtn: document.getElementById("vendorSubmitBtn"),
      activeGroup: document.getElementById("vendorActiveGroup"),
      nameInput: document.getElementById("vendorName"),
      phoneInput: document.getElementById("vendorPhone"),
      emailInput: document.getElementById("vendorEmail"),
      websiteInput: document.getElementById("vendorWebsite"),
      pcFirstInput: document.getElementById("vendorPCFirst"),
      pcLastInput: document.getElementById("vendorPCLast"),
      addressInput: document.getElementById("vendorAddress"),
      notesInput: document.getElementById("vendorNotes"),
      isActiveInput: document.getElementById("vendorIsActive"),
    };
  }

  function getOrdersEls() {
    return {
      modal: document.getElementById("vendorOrdersModal"),
      title: document.getElementById("vendorOrdersModalLabel"),
      summary: document.getElementById("vendorOrdersSummary"),
      body: document.getElementById("vendorOrdersTableBody"),
      prevBtn: document.getElementById("vendorOrdersPrevBtn"),
      nextBtn: document.getElementById("vendorOrdersNextBtn"),
    };
  }

  function renderStatusBadge(status) {
    var normalized = String(status || "ordered").toLowerCase();
    if (normalized === "received") {
      return '<span class="badge bg-success">received</span>';
    }
    return '<span class="badge bg-warning text-dark">' + escapeHtml(normalized) + "</span>";
  }

  function setOrdersLoading(message) {
    var els = getOrdersEls();
    if (!els.body) return;
    els.body.innerHTML =
      '<tr><td colspan="4" class="text-center text-muted py-4">' + escapeHtml(message) + "</td></tr>";
  }

  function updateOrdersPaginationControls() {
    var els = getOrdersEls();
    var pg = ordersState.pagination;
    var hasPrev = !!(pg && pg.has_prev);
    var hasNext = !!(pg && pg.has_next);

    if (els.prevBtn) els.prevBtn.disabled = !hasPrev;
    if (els.nextBtn) els.nextBtn.disabled = !hasNext;
  }

  async function loadVendorOrders(page) {
    if (!ordersState.vendorId) return;

    var targetPage = Number(page) > 0 ? Number(page) : 1;
    ordersState.page = targetPage;
    ordersState.pagination = null;
    updateOrdersPaginationControls();
    setOrdersLoading("Loading part orders...");

    try {
      var url =
        "/vendors/api/" +
        encodeURIComponent(ordersState.vendorId) +
        "/part-orders?page=" +
        encodeURIComponent(String(targetPage)) +
        "&per_page=" +
        encodeURIComponent(String(ordersState.perPage));

      var res = await fetch(url, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      var data = await res.json();
      var els = getOrdersEls();

      if (!res.ok || !data.ok) {
        setOrdersLoading((data && data.error) || "Failed to load part orders.");
        if (els.summary) els.summary.textContent = "Unable to load data.";
        return;
      }

      var vendorName = (data && data.vendor && data.vendor.name) || ordersState.vendorName || "Vendor";
      ordersState.vendorName = vendorName;
      ordersState.pagination = data.pagination || null;

      if (els.title) {
        els.title.textContent = "Part Orders - " + vendorName;
      }

      var pg = ordersState.pagination || {};
      if (els.summary) {
        els.summary.textContent =
          "Page " +
          String(pg.page || 1) +
          " of " +
          String(pg.pages || 1) +
          " · " +
          String(pg.total || 0) +
          " total";
      }

      var items = Array.isArray(data.items) ? data.items : [];
      if (!els.body) return;

      if (items.length === 0) {
        els.body.innerHTML =
          '<tr><td colspan="4" class="text-center text-muted py-4">No part orders for this vendor.</td></tr>';
      } else {
        els.body.innerHTML = items
          .map(function (item) {
            return (
              "<tr>" +
              '<td><span class="badge bg-secondary">' +
              escapeHtml(item.order_number || "-") +
              "</span></td>" +
              '<td class="text-end">' +
              escapeHtml(String(item.items_count == null ? 0 : item.items_count)) +
              "</td>" +
              "<td>" +
              renderStatusBadge(item.status) +
              "</td>" +
              "<td>" +
              escapeHtml(item.created_at || "-") +
              "</td>" +
              "</tr>"
            );
          })
          .join("");
      }

      updateOrdersPaginationControls();
    } catch (err) {
      var fallbackEls = getOrdersEls();
      setOrdersLoading("Network error while loading part orders.");
      if (fallbackEls.summary) fallbackEls.summary.textContent = "Unable to load data.";
    }
  }

  function openVendorOrders(vendorId, vendorName) {
    if (!vendorId) return;

    ordersState.vendorId = vendorId;
    ordersState.vendorName = vendorName || "Vendor";
    ordersState.page = 1;
    ordersState.pagination = null;

    var els = getOrdersEls();
    if (els.title) {
      els.title.textContent = "Part Orders - " + ordersState.vendorName;
    }
    if (els.summary) {
      els.summary.textContent = "Loading...";
    }
    updateOrdersPaginationControls();
    setOrdersLoading("Loading part orders...");

    if (els.modal && window.bootstrap && window.bootstrap.Modal) {
      window.bootstrap.Modal.getOrCreateInstance(els.modal).show();
      return;
    }

    loadVendorOrders(1);
  }

  async function loadVendorIntoEditModal(vendorId) {
    if (!vendorId) return;

    var els = getVendorEls();
    if (!els.form || !els.editingVendorId || !els.submitBtn || !els.modalTitle) return;

    try {
      var res = await fetch("/vendors/api/" + encodeURIComponent(vendorId), {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      var data = await res.json();

      if (!res.ok || !data.ok) {
        alert((data && data.error) || "Failed to load vendor data");
        return;
      }

      var vendor = data.item || {};

      els.editingVendorId.value = vendor._id || "";
      els.modalTitle.textContent = "Edit vendor";
      els.submitBtn.textContent = "Update Vendor";
      if (els.activeGroup) els.activeGroup.style.display = "block";

      if (els.nameInput) els.nameInput.value = vendor.name || "";
      if (els.phoneInput) els.phoneInput.value = vendor.phone || "";
      if (els.emailInput) els.emailInput.value = vendor.email || "";
      if (els.websiteInput) els.websiteInput.value = vendor.website || "";
      if (els.pcFirstInput) els.pcFirstInput.value = vendor.primary_contact_first_name || "";
      if (els.pcLastInput) els.pcLastInput.value = vendor.primary_contact_last_name || "";
      if (els.addressInput) els.addressInput.value = vendor.address || "";
      if (els.notesInput) els.notesInput.value = vendor.notes || "";
      if (els.isActiveInput) els.isActiveInput.checked = vendor.is_active !== false;
    } catch (err) {
      alert("Network error while loading vendor data");
    }
  }

  function bindPageLocalHandlers() {
    var vendorEls = getVendorEls();
    var ordersEls = getOrdersEls();

    if (ordersEls.modal && ordersEls.modal.dataset.ordersModalBound !== "1") {
      ordersEls.modal.dataset.ordersModalBound = "1";
      ordersEls.modal.addEventListener("show.bs.modal", function (e) {
        var trigger = e.relatedTarget;
        var vendorId = (trigger && trigger.getAttribute("data-vendor-id")) || ordersState.vendorId;
        var vendorName =
          (trigger && trigger.getAttribute("data-vendor-name")) || ordersState.vendorName || "Vendor";
        if (!vendorId) return;

        ordersState.vendorId = vendorId;
        ordersState.vendorName = vendorName;
        loadVendorOrders(1);
      });
    }

    if (vendorEls.modal && vendorEls.modal.dataset.vendorModalBound !== "1") {
      vendorEls.modal.dataset.vendorModalBound = "1";
      vendorEls.modal.addEventListener("show.bs.modal", function (e) {
        var triggerBtn = e.relatedTarget;
        if (triggerBtn && triggerBtn.classList.contains("editVendorBtn")) {
          return;
        }

        var current = getVendorEls();
        if (!current.form || !current.editingVendorId || !current.modalTitle || !current.submitBtn) return;

        current.editingVendorId.value = "";
        current.modalTitle.textContent = "Create new vendor";
        current.submitBtn.textContent = "Create Vendor";
        if (current.activeGroup) current.activeGroup.style.display = "none";
        current.form.reset();
      });
    }

    if (ordersEls.prevBtn && ordersEls.prevBtn.dataset.bound !== "1") {
      ordersEls.prevBtn.dataset.bound = "1";
      ordersEls.prevBtn.addEventListener("click", function () {
        var pg = ordersState.pagination;
        if (!pg || !pg.has_prev) return;
        loadVendorOrders(pg.prev_page);
      });
    }

    if (ordersEls.nextBtn && ordersEls.nextBtn.dataset.bound !== "1") {
      ordersEls.nextBtn.dataset.bound = "1";
      ordersEls.nextBtn.addEventListener("click", function () {
        var pg = ordersState.pagination;
        if (!pg || !pg.has_next) return;
        loadVendorOrders(pg.next_page);
      });
    }

    if (vendorEls.form && vendorEls.form.dataset.vendorSubmitBound !== "1") {
      vendorEls.form.dataset.vendorSubmitBound = "1";
      vendorEls.form.addEventListener("submit", async function (e) {
        var current = getVendorEls();
        if (!current.form || !current.editingVendorId) return;

        var vendorId = current.editingVendorId.value;
        if (!vendorId) {
          return;
        }

        e.preventDefault();

        var formData = {
          name: (current.nameInput && current.nameInput.value || "").trim(),
          phone: (current.phoneInput && current.phoneInput.value || "").trim(),
          email: (current.emailInput && current.emailInput.value || "").trim(),
          website: (current.websiteInput && current.websiteInput.value || "").trim(),
          primary_contact_first_name: (current.pcFirstInput && current.pcFirstInput.value || "").trim(),
          primary_contact_last_name: (current.pcLastInput && current.pcLastInput.value || "").trim(),
          address: (current.addressInput && current.addressInput.value || "").trim(),
          notes: (current.notesInput && current.notesInput.value || "").trim(),
          is_active: !!(current.isActiveInput && current.isActiveInput.checked),
        };

        try {
          if (current.submitBtn) {
            current.submitBtn.disabled = true;
            current.submitBtn.textContent = "Saving...";
          }

          var res = await fetch("/vendors/api/" + encodeURIComponent(vendorId) + "/update", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Accept: "application/json",
            },
            body: JSON.stringify(formData),
          });

          var data = await res.json();

          if (!res.ok || !data.ok) {
            alert((data && data.error) || "Failed to update vendor");
            if (current.submitBtn) {
              current.submitBtn.disabled = false;
              current.submitBtn.textContent = "Update Vendor";
            }
            return;
          }

          window.location.reload();
        } catch (err) {
          alert("Network error while updating vendor");
          if (current.submitBtn) {
            current.submitBtn.disabled = false;
            current.submitBtn.textContent = "Update Vendor";
          }
        }
      });
    }
  }

  function bindGlobalDelegationOnce() {
    if (!document.body || document.body.dataset.vendorsDocBound === "1") {
      return;
    }
    document.body.dataset.vendorsDocBound = "1";

    document.addEventListener("click", function (e) {
      var editBtn = e.target && e.target.closest ? e.target.closest(".editVendorBtn") : null;
      if (editBtn) {
        var vendorId = editBtn.getAttribute("data-vendor-id") || "";
        if (vendorId) {
          loadVendorIntoEditModal(vendorId);
        }
        return;
      }

      var row = e.target && e.target.closest ? e.target.closest(".vendorOrdersRow") : null;
      if (!row) return;

      if (e.target.closest("a, button, form, input, select, textarea, label")) {
        return;
      }

      var opener = row.querySelector(".openVendorOrdersBtn");
      if (opener) {
        opener.click();
        return;
      }

      var vendorIdFromRow = row.getAttribute("data-vendor-id") || "";
      var vendorNameFromRow = row.getAttribute("data-vendor-name") || "Vendor";
      openVendorOrders(vendorIdFromRow, vendorNameFromRow);
    });

    document.addEventListener("keydown", function (e) {
      var row = e.target && e.target.closest ? e.target.closest(".vendorOrdersRow") : null;
      if (!row) return;

      if (e.target.closest("a, button, form, input, select, textarea, label")) {
        return;
      }

      if (e.key !== "Enter" && e.key !== " ") {
        return;
      }

      e.preventDefault();
      var opener = row.querySelector(".openVendorOrdersBtn");
      if (opener) {
        opener.click();
        return;
      }

      var vendorId = row.getAttribute("data-vendor-id") || "";
      var vendorName = row.getAttribute("data-vendor-name") || "Vendor";
      openVendorOrders(vendorId, vendorName);
    });
  }

  bindGlobalDelegationOnce();
  bindPageLocalHandlers();
})();
