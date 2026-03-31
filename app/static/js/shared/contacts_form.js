(function () {
  "use strict";

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      if (ch === "&") return "&amp;";
      if (ch === "<") return "&lt;";
      if (ch === ">") return "&gt;";
      if (ch === '"') return "&quot;";
      return "&#39;";
    });
  }

  function getRoots() {
    return Array.prototype.slice.call(document.querySelectorAll("[data-contacts-form]"));
  }

  function getList(root) {
    return root ? root.querySelector("[data-contacts-list]") : null;
  }

  function getRows(root) {
    var list = getList(root);
    return list ? Array.prototype.slice.call(list.querySelectorAll("[data-contact-row]")) : [];
  }

  function buildRowHtml(contact, index, title) {
    var item = contact || {};
    var rowTitle = title || "Contact";
    return (
      '<div class="border rounded p-3" data-contact-row>' +
      '<div class="d-flex align-items-center justify-content-between mb-3">' +
      '<div class="fw-semibold" data-contact-title>' + escapeHtml(rowTitle) + " " + escapeHtml(String(index + 1)) + "</div>" +
      '<button type="button" class="btn btn-sm btn-outline-danger" data-remove-contact>Remove</button>' +
      '</div>' +
      '<div class="row g-3">' +
      '<div class="col-12 col-md-3">' +
      '<label class="form-label">First Name</label>' +
      '<input type="text" class="form-control" name="contact_first_name" value="' + escapeHtml(item.first_name || "") + '">' +
      '</div>' +
      '<div class="col-12 col-md-3">' +
      '<label class="form-label">Last Name</label>' +
      '<input type="text" class="form-control" name="contact_last_name" value="' + escapeHtml(item.last_name || "") + '">' +
      '</div>' +
      '<div class="col-12 col-md-3">' +
      '<label class="form-label">Phone</label>' +
      '<input type="text" class="form-control" name="contact_phone" value="' + escapeHtml(item.phone || "") + '">' +
      '</div>' +
      '<div class="col-12 col-md-3">' +
      '<label class="form-label">Email</label>' +
      '<input type="email" class="form-control" name="contact_email" value="' + escapeHtml(item.email || "") + '">' +
      '</div>' +
      '<div class="col-12">' +
      '<div class="form-check">' +
      '<input class="form-check-input" type="radio" data-contact-main name="contact_main_index" value="' + escapeHtml(String(index)) + '"' + (item.is_main ? ' checked' : '') + '>' +
      '<label class="form-check-label">Main contact</label>' +
      '</div>' +
      '</div>' +
      '</div>' +
      '</div>'
    );
  }

  function normalizeContacts(rawContacts) {
    var list = Array.isArray(rawContacts) ? rawContacts : [];
    var out = list
      .map(function (item) {
        return {
          first_name: String(item && item.first_name || "").trim(),
          last_name: String(item && item.last_name || "").trim(),
          phone: String(item && item.phone || "").trim(),
          email: String(item && item.email || "").trim(),
          is_main: !!(item && item.is_main),
        };
      })
      .filter(function (item) {
        return !!(item.first_name || item.last_name || item.phone || item.email);
      });

    if (!out.length) {
      return [];
    }

    var mainIndex = out.findIndex(function (item) { return item.is_main; });
    if (mainIndex < 0) {
      mainIndex = 0;
    }
    out.forEach(function (item, index) {
      item.is_main = index === mainIndex;
    });
    return out;
  }

  function reindex(root) {
    var rows = getRows(root);
    var title = (root && root.getAttribute("data-contact-title")) || "Contact";
    rows.forEach(function (row, index) {
      var titleEl = row.querySelector("[data-contact-title]");
      var mainInput = row.querySelector("[data-contact-main]");
      if (titleEl) {
        titleEl.textContent = title + " " + String(index + 1);
      }
      if (mainInput) {
        mainInput.value = String(index);
      }
    });

    if (!rows.length) {
      addContact(root, { is_main: true });
      rows = getRows(root);
    }

    var hasChecked = rows.some(function (row) {
      var mainInput = row.querySelector("[data-contact-main]");
      return !!(mainInput && mainInput.checked);
    });

    if (!hasChecked && rows[0]) {
      var firstMainInput = rows[0].querySelector("[data-contact-main]");
      if (firstMainInput) {
        firstMainInput.checked = true;
      }
    }

    rows.forEach(function (row) {
      var removeBtn = row.querySelector("[data-remove-contact]");
      if (removeBtn) {
        removeBtn.disabled = rows.length <= 1;
      }
    });
  }

  function addContact(root, contact) {
    var list = getList(root);
    if (!list) return;
    var rows = getRows(root);
    list.insertAdjacentHTML("beforeend", buildRowHtml(contact || {}, rows.length, root.getAttribute("data-contact-title") || "Contact"));
    reindex(root);
  }

  function getContacts(root) {
    var rows = getRows(root);
    var out = rows.map(function (row) {
      var mainInput = row.querySelector("[data-contact-main]");
      return {
        first_name: String((row.querySelector('[name="contact_first_name"]') || {}).value || "").trim(),
        last_name: String((row.querySelector('[name="contact_last_name"]') || {}).value || "").trim(),
        phone: String((row.querySelector('[name="contact_phone"]') || {}).value || "").trim(),
        email: String((row.querySelector('[name="contact_email"]') || {}).value || "").trim(),
        is_main: !!(mainInput && mainInput.checked),
      };
    });
    return normalizeContacts(out);
  }

  function setContacts(root, contacts) {
    var list = getList(root);
    if (!list) return;
    var normalized = normalizeContacts(contacts);
    list.innerHTML = "";
    if (!normalized.length) {
      normalized = [{ is_main: true }];
    }
    normalized.forEach(function (contact, index) {
      list.insertAdjacentHTML("beforeend", buildRowHtml(contact, index, root.getAttribute("data-contact-title") || "Contact"));
    });
    reindex(root);
  }

  function initRoot(root) {
    if (!root || root.dataset.contactsInitialized === "1") {
      return;
    }
    root.dataset.contactsInitialized = "1";

    root.addEventListener("click", function (event) {
      var addBtn = event.target.closest("[data-add-contact]");
      if (addBtn) {
        addContact(root, {});
        return;
      }

      var removeBtn = event.target.closest("[data-remove-contact]");
      if (!removeBtn) {
        return;
      }

      var row = removeBtn.closest("[data-contact-row]");
      if (row) {
        row.remove();
        reindex(root);
      }
    });

    root.addEventListener("change", function (event) {
      if (event.target && event.target.matches("[data-contact-main]")) {
        reindex(root);
      }
    });

    reindex(root);
  }

  function initAll() {
    getRoots().forEach(initRoot);
  }

  window.SmallShopContacts = {
    init: initRoot,
    initAll: initAll,
    getContacts: getContacts,
    setContacts: setContacts,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
})();