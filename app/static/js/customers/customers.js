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

  document.addEventListener("click", async function (event) {
    var btn = event.target.closest(".js-delete-work-order-payment");
    if (!btn) {
      return;
    }

    var paymentId = String(btn.getAttribute("data-payment-id") || "").trim();
    if (!paymentId) {
      return;
    }

    if (!window.confirm("Delete this payment?")) {
      return;
    }

    var originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Deleting...";

    try {
      var res = await fetch("/work_orders/api/payments/" + encodeURIComponent(paymentId) + "/delete", {
        method: "POST",
        headers: { "Accept": "application/json" }
      });
      var data = await res.json();
      if (!res.ok || !data || data.ok !== true) {
        throw new Error((data && (data.error || data.message)) || "Failed to delete payment.");
      }

      window.location.reload();
    } catch (err) {
      window.alert(err.message || "Failed to delete payment.");
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });
})();
