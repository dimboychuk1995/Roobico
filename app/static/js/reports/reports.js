(function () {
  "use strict";

  function initReportsPage() {
    var form = document.getElementById("standardReportsFilterForm");
    if (!form) return;

    var tabInput = form.querySelector('input[name="tab"]');
    if (!tabInput) return;

    document.querySelectorAll("[data-report-tab]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var tab = (btn.getAttribute("data-report-tab") || "").trim();
        if (!tab) return;
        tabInput.value = tab;
        form.submit();
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initReportsPage, { once: true });
  } else {
    initReportsPage();
  }

  window.addEventListener("smallshop:content-replaced", initReportsPage);
})();
