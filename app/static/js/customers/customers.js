(function () {
  "use strict";

  if (document.body && document.body.dataset.customersRowsBound === "1") {
    return;
  }
  if (document.body) {
    document.body.dataset.customersRowsBound = "1";
  }

  function shouldIgnoreClick(target) {
    return !!target.closest("a, button, form, input, select, textarea, label");
  }

  function bindRowNavigation(rowSelector) {
    document.addEventListener("click", function (event) {
      var row = event.target.closest(rowSelector);
      if (!row || shouldIgnoreClick(event.target)) {
        return;
      }

      var href = row.getAttribute("data-href");
      if (href) {
        window.location.href = href;
      }
    });

    document.addEventListener("keydown", function (event) {
      var row = event.target.closest(rowSelector);
      if (!row || shouldIgnoreClick(event.target)) {
        return;
      }

      if (event.key !== "Enter" && event.key !== " ") {
        return;
      }

      event.preventDefault();
      var href = row.getAttribute("data-href");
      if (href) {
        window.location.href = href;
      }
    });
  }

  bindRowNavigation(".js-customer-row");
  bindRowNavigation(".js-unit-row");
})();
