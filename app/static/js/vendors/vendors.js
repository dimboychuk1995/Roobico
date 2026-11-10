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
      websiteError: document.getElementById("vendorWebsiteError"),
      addressInput: document.getElementById("vendorAddress"),
      notesInput: document.getElementById("vendorNotes"),
      isActiveInput: document.getElementById("vendorIsActive"),
      // Секция «Portal login» рендерится только с vendors.view_credentials.
      credentialsGroup: document.getElementById("vendorCredentialsGroup"),
      portalLoginInput: document.getElementById("vendorPortalLogin"),
      portalPasswordInput: document.getElementById("vendorPortalPassword"),
      portalPasswordToggle: document.getElementById("vendorPortalPasswordToggle"),
      portalPasswordHelp: document.getElementById("vendorPortalPasswordHelp"),
      portalPasswordClearGroup: document.getElementById("vendorPortalPasswordClearGroup"),
      portalPasswordClear: document.getElementById("vendorPortalPasswordClear"),
    };
  }

  function notifyError(message) {
    if (typeof window.appAlert === "function") window.appAlert(message, "error");
    else window.alert(message);
  }

  function apiErrorMessage(data, fallback) {
    return (data && (data.message || data.error)) || fallback;
  }

  /* ── Сайт: проверка до отправки по ТЕМ ЖЕ правилам, что у сервера
     (app/utils/urls.py normalize_website — менять вместе). Обычная форма
     создания при отказе сервера уходит редиректом и теряет всё введённое
     (включая пароль), поэтому клиент не пропускает то, что сервер отвергнет;
     источник истины всё равно сервер. https:// дописывает сервер. ── */
  var WEBSITE_ERROR = "Enter a valid website, e.g. fleetpride.com";
  var WEBSITE_MAX_CHARS = 2048;
  // Пробельные символы ровно как str.isspace() в Python (strip и пробел в хосте).
  var PY_SPACE = "\\t\\n\\x0b\\x0c\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
  var PY_STRIP_RE = new RegExp("^[" + PY_SPACE + "]+|[" + PY_SPACE + "]+$", "g");
  var PY_SPACE_RE = new RegExp("[" + PY_SPACE + "]");
  var WEBSITE_CONTROL_RE = /[\x00-\x1f\x7f]/;
  var WEBSITE_SCHEME_RE = /^([A-Za-z][A-Za-z0-9+.\-]*):/;
  var WEBSITE_PORT_TAIL_RE = /^[0-9]{1,5}(?:[\/?#]|$)/;
  var WEBSITE_HOST_PORT_RE = /^([\s\S]*):([0-9]{1,5})$/;
  // Метка домена: буквы (в т. ч. IDN), цифры, «_», дефис не по краям — как \w в Python.
  var WEBSITE_LABEL_RE = (function () {
    try {
      return new RegExp("^[\\p{L}\\p{N}_](?:[\\p{L}\\p{N}_-]*[\\p{L}\\p{N}_])?$", "u");
    } catch (e) {
      // Браузер без \p{…}: проверка мягче, окончательно решит сервер.
      return /^[^\s.@\\:\/?#-](?:[^\s.@\\:\/?#]*[^\s.@\\:\/?#-])?$/;
    }
  })();

  function codePointLength(text) {
    return Array.from(text).length;
  }

  function validWebsiteHost(host) {
    if (!host || codePointLength(host) > 253) return false;
    if (host === "localhost") return true;
    var labels = host.split(".");
    if (labels.length < 2) return false;
    return labels.every(function (label) {
      var len = codePointLength(label);
      return len > 0 && len <= 63 && WEBSITE_LABEL_RE.test(label);
    });
  }

  // Нормализованный URL или null — зеркало normalize_website (Python).
  function normalizeWebsite(raw) {
    var text = String(raw == null ? "" : raw).replace(PY_STRIP_RE, "");
    if (!text || codePointLength(text) > WEBSITE_MAX_CHARS || WEBSITE_CONTROL_RE.test(text)) return null;
    var scheme = "https";
    var rest = text;
    var match = WEBSITE_SCHEME_RE.exec(text);
    if (match && !WEBSITE_PORT_TAIL_RE.test(text.slice(match[0].length))) {
      // Только http(s) и только с «//»: «http:x.com» — не адрес сайта.
      scheme = match[1].toLowerCase();
      rest = text.slice(match[0].length);
      if ((scheme !== "http" && scheme !== "https") || rest.slice(0, 2) !== "//") return null;
      rest = rest.slice(2);
    } else if (text.slice(0, 2) === "//") {
      rest = text.slice(2);
    }
    var parts = /^([^\/?#]*)([\s\S]*)$/.exec(rest);
    var authority = parts[1];
    var tail = parts[2];
    // «user:pass@host», «\» и пробелы в адресе не принимаем (как сервер).
    if (/[@\\]/.test(authority) || PY_SPACE_RE.test(authority)) return null;
    var host = authority;
    var port = "";
    var portMatch = WEBSITE_HOST_PORT_RE.exec(authority);
    if (portMatch) {
      host = portMatch[1];
      port = portMatch[2];
      var portNum = parseInt(port, 10);
      if (!(portNum > 0 && portNum <= 65535)) return null;
    } else if (authority.indexOf(":") >= 0) {
      return null;
    }
    host = host.toLowerCase();
    if (host.slice(-1) === ".") host = host.slice(0, -1);
    if (!validWebsiteHost(host)) return null;
    if (tail === "/") tail = "";
    return scheme + "://" + host + (port ? ":" + port : "") + tail;
  }

  function websiteError(raw) {
    if (!String(raw == null ? "" : raw).replace(PY_STRIP_RE, "")) return "";
    return normalizeWebsite(raw) ? "" : WEBSITE_ERROR;
  }

  function validateWebsiteInput(els) {
    if (!els.websiteInput) return true;
    var message = websiteError(els.websiteInput.value);
    els.websiteInput.classList.toggle("is-invalid", !!message);
    if (els.websiteError && message) els.websiteError.textContent = message;
    if (message) els.websiteInput.focus();
    return !message;
  }

  /* ── Учётка портала в форме: пароль никогда не подставляется в поле —
     пусто = оставить сохранённый, галочка Remove = удалить. ── */
  function setPasswordVisible(els, visible) {
    if (!els.portalPasswordInput) return;
    els.portalPasswordInput.type = visible ? "text" : "password";
    if (els.portalPasswordToggle) {
      els.portalPasswordToggle.textContent = visible ? "Hide" : "Show";
      els.portalPasswordToggle.setAttribute("aria-pressed", visible ? "true" : "false");
    }
  }

  function resetCredentialFields(els, vendor) {
    if (!els.credentialsGroup) return;
    var hasPassword = !!(vendor && vendor.has_portal_password);
    if (els.portalLoginInput) els.portalLoginInput.value = (vendor && vendor.portal_login) || "";
    if (els.portalPasswordInput) {
      els.portalPasswordInput.value = "";
      els.portalPasswordInput.disabled = false;
      els.portalPasswordInput.placeholder = hasPassword ? "•••••••• saved — leave blank to keep" : "Optional";
    }
    setPasswordVisible(els, false);
    if (els.portalPasswordHelp) els.portalPasswordHelp.hidden = !hasPassword;
    if (els.portalPasswordClearGroup) els.portalPasswordClearGroup.hidden = !hasPassword;
    if (els.portalPasswordClear) els.portalPasswordClear.checked = false;
  }

  function credentialPayload(els) {
    if (!els.credentialsGroup) return {};
    var payload = {
      portal_login: ((els.portalLoginInput && els.portalLoginInput.value) || "").trim(),
    };
    var password = (els.portalPasswordInput && els.portalPasswordInput.value) || "";
    if (password.trim()) payload.portal_password = password;
    if (els.portalPasswordClear && els.portalPasswordClear.checked) payload.portal_password_clear = true;
    return payload;
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

      // Re-init sorting for refreshed table
      var tbl = els.body && els.body.closest("table");
      if (tbl && window.TableSort) window.TableSort.refresh(tbl);
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
        notifyError(apiErrorMessage(data, "Failed to load vendor data"));
        return;
      }

      var vendor = data.item || {};

      els.editingVendorId.value = vendor._id || "";
      els.modalTitle.textContent = "Edit vendor";
      els.submitBtn.textContent = "Update Vendor";
      if (els.activeGroup) els.activeGroup.style.display = "block";

      if (els.nameInput) els.nameInput.value = vendor.name || "";
      if (els.websiteInput) {
        els.websiteInput.value = vendor.website || "";
        els.websiteInput.classList.remove("is-invalid");
      }
      resetCredentialFields(els, vendor);
      if (els.addressInput) els.addressInput.value = vendor.address || "";
      if (els.notesInput) els.notesInput.value = vendor.notes || "";
      if (els.isActiveInput) els.isActiveInput.checked = vendor.is_active !== false;
      if (els.contactsForm && window.RoobicoContacts) {
        window.RoobicoContacts.setContacts(els.contactsForm, Array.isArray(vendor.contacts) ? vendor.contacts : []);
      }

      // Show attachments block when editing
      var attGroup = document.getElementById('vendorAttachmentsGroup');
      if (attGroup) {
        var vid = vendor._id || vendorId;
        if (vid) {
          attGroup.style.display = '';
          var attBlock = attGroup.querySelector('.attachments-block');
          if (attBlock) {
            attBlock.setAttribute('data-entity-id', vid);
            var ctrl = window.AttachmentsGetBlock ? window.AttachmentsGetBlock(attBlock) : null;
            if (ctrl) { ctrl.setEntityId(vid); ctrl.load(); }
            else if (window.AttachmentsInit) window.AttachmentsInit();
          }
        } else {
          attGroup.style.display = 'none';
        }
      }
    } catch (err) {
      notifyError("Network error while loading vendor data");
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
        window.dispatchEvent(new CustomEvent("roobico:content-replaced"));
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
        if (current.websiteInput) current.websiteInput.classList.remove("is-invalid");
        resetCredentialFields(current, null);
        if (current.contactsForm && window.RoobicoContacts) {
          window.RoobicoContacts.setContacts(current.contactsForm, []);
        }
        // Hide attachments when creating
        var attGroup = document.getElementById('vendorAttachmentsGroup');
        if (attGroup) attGroup.style.display = 'none';
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

    // Date preset change — sync date inputs and reload
    if (ordersEls.datePreset && ordersEls.datePreset.dataset.bound !== "1") {
      ordersEls.datePreset.dataset.bound = "1";
      ordersEls.datePreset.addEventListener("change", function () {
        applyVendorDatePreset(ordersEls.datePreset.value);
        syncDateInputsToState();
        ordersState.page = 1;
        loadVendorOrders(1);
      });
    }

    // Date from/to manual change — switch preset to custom and reload
    function onDateInputChange() {
      var els2 = getOrdersEls();
      if (els2.datePreset && els2.datePreset.value !== "custom") {
        els2.datePreset.value = "custom";
      }
      syncDateInputsToState();
      ordersState.page = 1;
      loadVendorOrders(1);
    }
    if (ordersEls.dateFrom && ordersEls.dateFrom.dataset.bound !== "1") {
      ordersEls.dateFrom.dataset.bound = "1";
      ordersEls.dateFrom.addEventListener("change", onDateInputChange);
    }
    if (ordersEls.dateTo && ordersEls.dateTo.dataset.bound !== "1") {
      ordersEls.dateTo.dataset.bound = "1";
      ordersEls.dateTo.addEventListener("change", onDateInputChange);
    }

    // Search button removed — filters apply automatically on change.

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

        // Неверный сайт ловим до отправки: обычная форма создания иначе
        // потеряла бы всё введённое на редиректе с ошибкой.
        if (!validateWebsiteInput(current)) {
          e.preventDefault();
          return;
        }

        var vendorId = current.editingVendorId.value;
        if (!vendorId) {
          return;
        }

        e.preventDefault();

        var formData = Object.assign({
          name: (current.nameInput && current.nameInput.value || "").trim(),
          website: (current.websiteInput && current.websiteInput.value || "").trim(),
          contacts: current.contactsForm && window.RoobicoContacts
            ? window.RoobicoContacts.getContacts(current.contactsForm)
            : [],
          address: (current.addressInput && current.addressInput.value || "").trim(),
          notes: (current.notesInput && current.notesInput.value || "").trim(),
          is_active: !!(current.isActiveInput && current.isActiveInput.checked),
        }, credentialPayload(current));

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
            if (data && data.error === "invalid_website" && current.websiteInput) {
              current.websiteInput.classList.add("is-invalid");
              if (current.websiteError) current.websiteError.textContent = apiErrorMessage(data, WEBSITE_ERROR);
            }
            notifyError(apiErrorMessage(data, "Failed to update vendor"));
            if (current.submitBtn) {
              current.submitBtn.disabled = false;
              current.submitBtn.textContent = "Update Vendor";
            }
            return;
          }

          window.location.reload();
        } catch (err) {
          notifyError("Network error while updating vendor");
          if (current.submitBtn) {
            current.submitBtn.disabled = false;
            current.submitBtn.textContent = "Update Vendor";
          }
        }
      });
    }

    if (vendorEls.websiteInput && vendorEls.websiteInput.dataset.bound !== "1") {
      vendorEls.websiteInput.dataset.bound = "1";
      vendorEls.websiteInput.addEventListener("input", function () {
        vendorEls.websiteInput.classList.remove("is-invalid");
      });
    }

    // Show / Hide — только то, что пользователь набрал в поле (сохранённый
    // пароль в форму не подставляется никогда).
    if (vendorEls.portalPasswordToggle && vendorEls.portalPasswordToggle.dataset.bound !== "1") {
      vendorEls.portalPasswordToggle.dataset.bound = "1";
      vendorEls.portalPasswordToggle.addEventListener("click", function () {
        var els = getVendorEls();
        setPasswordVisible(els, !!(els.portalPasswordInput && els.portalPasswordInput.type === "password"));
      });
    }

    if (vendorEls.portalPasswordClear && vendorEls.portalPasswordClear.dataset.bound !== "1") {
      vendorEls.portalPasswordClear.dataset.bound = "1";
      vendorEls.portalPasswordClear.addEventListener("change", function () {
        var els = getVendorEls();
        if (!els.portalPasswordInput) return;
        var removing = !!els.portalPasswordClear.checked;
        if (removing) els.portalPasswordInput.value = "";
        els.portalPasswordInput.disabled = removing;
      });
    }
  }

  /* ── Portal logins: показ / копирование учёток ─────────────────────── */

  var PASSWORD_MASK = "••••••••";
  var PASSWORD_VISIBLE_MS = 30000;

  async function fetchCredentials(vendorId) {
    var res = await fetch("/vendors/api/" + encodeURIComponent(vendorId) + "/credentials", {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    var data = null;
    try {
      data = await res.json();
    } catch (e) {
      data = null;
    }
    if (!res.ok || !data || !data.ok) {
      var fallback = res.status === 403
        ? "You don't have permission to view vendor passwords."
        : res.status === 429
          ? "Too many password requests — please wait a few minutes and try again."
          : "Could not load the password.";
      // 403 от декоратора прав приходит без message (только код) — тогда
      // показываем понятный текст по статусу.
      throw new Error((data && data.message) || fallback);
    }
    return data;
  }

  // Копирование без потери user activation: Safari разрешает запись в буфер
  // только синхронно из клика, поэтому текст передаём промисом внутри
  // ClipboardItem; где это не работает — writeText / execCommand.
  function execCopy(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (e) {
      ok = false;
    }
    document.body.removeChild(area);
    if (!ok) throw new Error("Copy failed — your browser blocked clipboard access.");
  }

  function writeTextFallback(text) {
    if (window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).catch(function () { execCopy(text); });
    }
    execCopy(text);
    return Promise.resolve();
  }

  function copyToClipboard(textPromise) {
    var canWriteItem = window.isSecureContext && typeof window.ClipboardItem === "function" &&
      navigator.clipboard && typeof navigator.clipboard.write === "function";
    if (canWriteItem) {
      var item = null;
      try {
        item = new window.ClipboardItem({
          "text/plain": textPromise.then(function (text) { return new Blob([text], { type: "text/plain" }); }),
        });
      } catch (e) {
        item = null;
      }
      if (item) {
        // Ошибка сервера важнее ошибки буфера: если textPromise отклонён,
        // наружу уйдёт именно его сообщение.
        return navigator.clipboard.write([item]).catch(function () {
          return textPromise.then(writeTextFallback);
        });
      }
    }
    return textPromise.then(writeTextFallback);
  }

  // Показанный пароль держим только в JS-свойстве узла (не в data-атрибуте)
  // и стираем при скрытии.
  function shownPassword(box) {
    return box && box.dataset.revealed === "1" ? box._revealedValue || "" : null;
  }

  function hidePassword(box) {
    if (!box) return;
    if (box._hideTimer) {
      clearTimeout(box._hideTimer);
      box._hideTimer = null;
    }
    var valueEl = box.querySelector(".js-portal-password-value");
    var btn = box.querySelector(".js-show-password");
    if (valueEl) valueEl.textContent = PASSWORD_MASK;
    if (btn) btn.textContent = "Show";
    box.dataset.revealed = "";
    box._revealedValue = null;
  }

  async function togglePassword(btn) {
    var box = btn.closest(".js-portal-password");
    if (!box) return;
    if (box.dataset.revealed === "1") {
      hidePassword(box);
      return;
    }
    btn.disabled = true;
    try {
      var data = await fetchCredentials(box.getAttribute("data-vendor-id") || "");
      var valueEl = box.querySelector(".js-portal-password-value");
      if (valueEl) valueEl.textContent = data.password || "";
      box.dataset.revealed = "1";
      box._revealedValue = data.password || "";
      btn.textContent = "Hide";
      box._hideTimer = setTimeout(function () { hidePassword(box); }, PASSWORD_VISIBLE_MS);
    } catch (err) {
      notifyError(err && err.message ? err.message : "Could not load the password.");
    } finally {
      btn.disabled = false;
    }
  }

  function copyPassword(btn) {
    var box = btn.closest(".js-portal-password");
    if (!box) return;
    var visible = shownPassword(box);
    var textPromise = visible !== null
      ? Promise.resolve(visible)
      : fetchCredentials(box.getAttribute("data-vendor-id") || "").then(function (data) { return data.password || ""; });
    copyToClipboard(textPromise)
      .then(function () {
        if (typeof window.appAlert === "function") window.appAlert("Password copied", "success");
      })
      .catch(function (err) {
        notifyError(err && err.message ? err.message : "Could not copy the password.");
      });
  }

  function copyLogin(btn) {
    copyToClipboard(Promise.resolve(btn.getAttribute("data-copy-value") || ""))
      .then(function () {
        if (typeof window.appAlert === "function") window.appAlert("Login copied", "success");
      })
      .catch(function (err) {
        notifyError(err && err.message ? err.message : "Could not copy the login.");
      });
  }

  function bindGlobalDelegationOnce() {
    if (!document.body || document.body.dataset.vendorsDocBound === "1") {
      return;
    }
    document.body.dataset.vendorsDocBound = "1";

    document.addEventListener("click", function (e) {
      var credBtn = e.target && e.target.closest
        ? e.target.closest(".js-show-password, .js-copy-password, .js-copy-login")
        : null;
      if (credBtn) {
        if (credBtn.classList.contains("js-show-password")) togglePassword(credBtn);
        else if (credBtn.classList.contains("js-copy-password")) copyPassword(credBtn);
        else copyLogin(credBtn);
        return;
      }

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

  /* ── lazy-load vendor balances ── */
  function loadVendorBalances() {
    var cells = document.querySelectorAll(".js-vendor-balance");
    if (!cells.length) return;
    var ids = [];
    cells.forEach(function (c) { ids.push(c.getAttribute("data-vendor-id")); });
    var qs = ids.map(function (id) { return "ids=" + encodeURIComponent(id); }).join("&");
    fetch("/vendors/api/balances?" + qs)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        var bal = data.balances || {};
        cells.forEach(function (c) {
          var id = c.getAttribute("data-vendor-id");
          var v = parseFloat(bal[id]);
          c.textContent = "$" + (isNaN(v) ? "0.00" : v.toFixed(2));
        });
      })
      .catch(function () {
        cells.forEach(function (c) { c.textContent = "$0.00"; });
      });
  }

  function initVendorsPage() {
    // Повторные вызовы безопасны: bindPageLocalHandlers защищён dataset-гардами
    // на конкретных узлах, а после мягкой подмены контента узлы свежие.
    bindPageLocalHandlers();
    loadVendorBalances();
  }

  bindGlobalDelegationOnce();
  initVendorsPage();
  // Мягкий поиск/навигация (public.js) пересоздаёт .app-main-col.
  window.addEventListener("roobico:content-replaced", initVendorsPage);

})();
