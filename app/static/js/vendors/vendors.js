(function () {
  "use strict";

  var ordersState = {
    vendorId: "",
    vendorName: "",
    page: 1,
    perPage: 10,
    pagination: null,
    datePreset: "this_month",
    dateFrom: "",
    dateTo: "",
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
      contactsForm: document.getElementById("vendorContactsForm"),
      editingVendorId: document.getElementById("editingVendorId"),
      modalTitle: document.getElementById("createVendorModalLabel"),
      submitBtn: document.getElementById("vendorSubmitBtn"),
      activeGroup: document.getElementById("vendorActiveGroup"),
      nameInput: document.getElementById("vendorName"),
      websiteInput: document.getElementById("vendorWebsite"),
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
      datePreset: document.getElementById("vendorOrdersDatePreset"),
      dateFrom: document.getElementById("vendorOrdersDateFrom"),
      dateTo: document.getElementById("vendorOrdersDateTo"),
      dateReset: document.getElementById("vendorOrdersDateReset"),
      searchBtn: document.getElementById("vendorOrdersSearchBtn"),
      summaryBlock: document.getElementById("vendorOrdersSummaryBlock"),
      summaryTotalOrders: document.getElementById("summaryTotalOrders"),
      summaryTotalAmount: document.getElementById("summaryTotalAmount"),
      summaryTotalPaid: document.getElementById("summaryTotalPaid"),
      summaryUnpaid: document.getElementById("summaryUnpaid"),
      summaryReceived: document.getElementById("summaryReceived"),
      summaryNotReceived: document.getElementById("summaryNotReceived"),
    };
  }

  function renderStatusBadge(status) {
    var normalized = String(status || "ordered").toLowerCase();
    if (normalized === "received") {
      return '<span class="badge bg-success">received</span>';
    }
    return '<span class="badge bg-warning text-dark">' + escapeHtml(normalized) + "</span>";
  }

  function formatMoney(value) {
    var num = parseFloat(value);
    if (isNaN(num)) return "0.00";
    return num.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function renderOrdersSummary(summary) {
    var els = getOrdersEls();
    if (!summary || !els.summaryBlock) {
      if (els.summaryBlock) els.summaryBlock.style.display = "none";
      return;
    }
    els.summaryBlock.style.display = "";
    if (els.summaryTotalOrders) els.summaryTotalOrders.textContent = String(summary.total_orders || 0);
    if (els.summaryTotalAmount) els.summaryTotalAmount.textContent = "$" + formatMoney(summary.total_amount);
    if (els.summaryTotalPaid) els.summaryTotalPaid.textContent = "$" + formatMoney(summary.total_paid);
    if (els.summaryUnpaid) els.summaryUnpaid.textContent = "$" + formatMoney(summary.unpaid);
    if (els.summaryReceived) els.summaryReceived.textContent = String(summary.received || 0);
    if (els.summaryNotReceived) els.summaryNotReceived.textContent = String(summary.not_received || 0);
  }

  function syncDateInputsToState() {
    var els = getOrdersEls();
    if (els.dateFrom) ordersState.dateFrom = els.dateFrom.value || "";
    if (els.dateTo) ordersState.dateTo = els.dateTo.value || "";
    if (els.datePreset) ordersState.datePreset = els.datePreset.value || "all_time";
  }

  function updateDateInputValue(input, ymd) {
    if (!input) return;
    input.value = ymd || "";
    if (input._flatpickr) {
      input._flatpickr.setDate(ymd || null, false, "Y-m-d");
    }
  }

  function toYmd(d) {
    var mm = String(d.getMonth() + 1).padStart(2, "0");
    var dd = String(d.getDate()).padStart(2, "0");
    return d.getFullYear() + "-" + mm + "-" + dd;
  }

  function applyVendorDatePreset(presetValue) {
    var els = getOrdersEls();
    var preset = String(presetValue || "").trim().toLowerCase();
    if (!preset || preset === "custom") return;
    if (preset === "all_time") {
      updateDateInputValue(els.dateFrom, "");
      updateDateInputValue(els.dateTo, "");
      return;
    }
    var today = new Date();
    var fromDate = null;
    var toDate = today;
    if (preset === "today") {
      fromDate = new Date(today);
    } else if (preset === "yesterday") {
      var y = new Date(today); y.setDate(y.getDate() - 1);
      fromDate = y; toDate = y;
    } else if (preset === "this_week") {
      fromDate = new Date(today);
      var dow = fromDate.getDay();
      var diff = dow === 0 ? 6 : dow - 1;
      fromDate.setDate(fromDate.getDate() - diff);
    } else if (preset === "last_week") {
      var thisWeekStart = new Date(today);
      var dow2 = thisWeekStart.getDay();
      var diff2 = dow2 === 0 ? 6 : dow2 - 1;
      thisWeekStart.setDate(thisWeekStart.getDate() - diff2);
      toDate = new Date(thisWeekStart); toDate.setDate(toDate.getDate() - 1);
      fromDate = new Date(toDate); fromDate.setDate(fromDate.getDate() - 6);
    } else if (preset === "this_month") {
      fromDate = new Date(today.getFullYear(), today.getMonth(), 1);
    } else if (preset === "last_month") {
      var lm = new Date(today.getFullYear(), today.getMonth(), 0);
      fromDate = new Date(lm.getFullYear(), lm.getMonth(), 1);
      toDate = lm;
    } else if (preset === "this_quarter") {
      var qm = Math.floor(today.getMonth() / 3) * 3;
      fromDate = new Date(today.getFullYear(), qm, 1);
    } else if (preset === "last_quarter") {
      var qm2 = Math.floor(today.getMonth() / 3) * 3;
      var lqEnd = new Date(today.getFullYear(), qm2, 0);
      var lqm = Math.floor(lqEnd.getMonth() / 3) * 3;
      fromDate = new Date(lqEnd.getFullYear(), lqm, 1);
      toDate = lqEnd;
    } else if (preset === "this_year") {
      fromDate = new Date(today.getFullYear(), 0, 1);
    } else if (preset === "last_year") {
      fromDate = new Date(today.getFullYear() - 1, 0, 1);
      toDate = new Date(today.getFullYear() - 1, 11, 31);
    }
    updateDateInputValue(els.dateFrom, fromDate ? toYmd(fromDate) : "");
    updateDateInputValue(els.dateTo, toDate ? toYmd(toDate) : "");
  }

  function setOrdersLoading(message) {
    var els = getOrdersEls();
    if (!els.body) return;
    els.body.innerHTML =
      '<tr><td colspan="5" class="text-center text-muted py-4">' + escapeHtml(message) + "</td></tr>";
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

      if (ordersState.datePreset) {
        url += "&date_preset=" + encodeURIComponent(ordersState.datePreset);
      }
      if (ordersState.dateFrom) {
        url += "&date_from=" + encodeURIComponent(ordersState.dateFrom);
      }
      if (ordersState.dateTo) {
        url += "&date_to=" + encodeURIComponent(ordersState.dateTo);
      }

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
          '<tr><td colspan="5" class="text-center text-muted py-4">No part orders for this vendor.</td></tr>';
      } else {
        els.body.innerHTML = items
          .map(function (item) {
            var orderUrl = "/parts/?tab=orders&open_order=" + encodeURIComponent(item.id);
            return (
              '<tr class="vendor-order-row" role="button" style="cursor:pointer;" data-order-url="' + escapeHtml(orderUrl) + '">' +
              '<td><span class="badge bg-secondary">' +
              escapeHtml(item.order_number || "-") +
              "</span></td>" +
              '<td class="text-end">' +
              escapeHtml(String(item.items_count == null ? 0 : item.items_count)) +
              "</td>" +
              '<td class="text-end">$' +
              escapeHtml(formatMoney(item.total_amount)) +
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

      // Render summary
      renderOrdersSummary(data.summary);

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
        appAlert((data && data.error) || "Failed to load vendor data", 'error');
        return;
      }

      var vendor = data.item || {};

      els.editingVendorId.value = vendor._id || "";
      els.modalTitle.textContent = "Edit vendor";
      els.submitBtn.textContent = "Update Vendor";
      if (els.activeGroup) els.activeGroup.style.display = "block";

      if (els.nameInput) els.nameInput.value = vendor.name || "";
      if (els.websiteInput) els.websiteInput.value = vendor.website || "";
      if (els.addressInput) els.addressInput.value = vendor.address || "";
      if (els.notesInput) els.notesInput.value = vendor.notes || "";
      if (els.isActiveInput) els.isActiveInput.checked = vendor.is_active !== false;
      if (els.contactsForm && window.SmallShopContacts) {
        window.SmallShopContacts.setContacts(els.contactsForm, Array.isArray(vendor.contacts) ? vendor.contacts : []);
      }
    } catch (err) {
      appAlert("Network error while loading vendor data", 'error');
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

        // Reset date filters to This Month (project default)
        var oEls = getOrdersEls();
        if (oEls.datePreset) oEls.datePreset.value = "this_month";
        ordersState.datePreset = "this_month";
        applyVendorDatePreset("this_month");
        syncDateInputsToState();

        loadVendorOrders(1);
      });
      ordersEls.modal.addEventListener("shown.bs.modal", function () {
        window.dispatchEvent(new CustomEvent("smallshop:content-replaced"));
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
        if (current.contactsForm && window.SmallShopContacts) {
          window.SmallShopContacts.setContacts(current.contactsForm, []);
        }
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

    // Date preset change — sync date inputs (no auto-load)
    if (ordersEls.datePreset && ordersEls.datePreset.dataset.bound !== "1") {
      ordersEls.datePreset.dataset.bound = "1";
      ordersEls.datePreset.addEventListener("change", function () {
        applyVendorDatePreset(ordersEls.datePreset.value);
      });
    }

    // Date from/to manual change — switch preset to custom
    function onDateInputChange() {
      var els2 = getOrdersEls();
      if (els2.datePreset && els2.datePreset.value !== "custom") {
        els2.datePreset.value = "custom";
      }
    }
    if (ordersEls.dateFrom && ordersEls.dateFrom.dataset.bound !== "1") {
      ordersEls.dateFrom.dataset.bound = "1";
      ordersEls.dateFrom.addEventListener("change", onDateInputChange);
    }
    if (ordersEls.dateTo && ordersEls.dateTo.dataset.bound !== "1") {
      ordersEls.dateTo.dataset.bound = "1";
      ordersEls.dateTo.addEventListener("change", onDateInputChange);
    }

    // Search button — apply filters and reload
    if (ordersEls.searchBtn && ordersEls.searchBtn.dataset.bound !== "1") {
      ordersEls.searchBtn.dataset.bound = "1";
      ordersEls.searchBtn.addEventListener("click", function () {
        syncDateInputsToState();
        ordersState.page = 1;
        loadVendorOrders(1);
      });
    }

    // Reset button
    if (ordersEls.dateReset && ordersEls.dateReset.dataset.bound !== "1") {
      ordersEls.dateReset.dataset.bound = "1";
      ordersEls.dateReset.addEventListener("click", function () {
        var els2 = getOrdersEls();
        if (els2.datePreset) els2.datePreset.value = "this_month";
        applyVendorDatePreset("this_month");
        ordersState.datePreset = "this_month";
        syncDateInputsToState();
        ordersState.page = 1;
        loadVendorOrders(1);
      });
    }

    // Click on order row → open in new tab on parts page
    if (ordersEls.modal && ordersEls.modal.dataset.orderRowClickBound !== "1") {
      ordersEls.modal.dataset.orderRowClickBound = "1";
      ordersEls.modal.addEventListener("click", function (e) {
        var row = e.target && e.target.closest ? e.target.closest(".vendor-order-row") : null;
        if (!row) return;
        var url = row.getAttribute("data-order-url");
        if (url) {
          window.open(url, "_blank");
        }
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
          website: (current.websiteInput && current.websiteInput.value || "").trim(),
          contacts: current.contactsForm && window.SmallShopContacts
            ? window.SmallShopContacts.getContacts(current.contactsForm)
            : [],
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
            appAlert((data && data.error) || "Failed to update vendor", 'error');
            if (current.submitBtn) {
              current.submitBtn.disabled = false;
              current.submitBtn.textContent = "Update Vendor";
            }
            return;
          }

          window.location.reload();
        } catch (err) {
          appAlert("Network error while updating vendor", 'error');
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
