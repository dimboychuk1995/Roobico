(function () {
	"use strict";

	function initSettingsWorkOrdersPage() {
		const editButtons = document.querySelectorAll(".edit-labor-rate-btn");
		const form = document.getElementById("editLaborRateForm");
		const idInput = document.getElementById("editLaborRateId");
		const nameInput = document.getElementById("editLaborRateName");
		const hourlyInput = document.getElementById("editLaborRateHourly");

		if (!form || !nameInput || !hourlyInput) return;

		editButtons.forEach((btn) => {
			btn.addEventListener("click", function () {
				const updateUrl = btn.getAttribute("data-update-url") || "";
				const rateId = btn.getAttribute("data-rate-id") || "";
				const name = btn.getAttribute("data-rate-name") || "";
				const hourly = btn.getAttribute("data-rate-hourly") || "0.00";

				form.action = updateUrl;
				if (idInput) idInput.value = rateId;
				nameInput.value = name;
				hourlyInput.value = hourly;
			});
		});
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", initSettingsWorkOrdersPage, { once: true });
	} else {
		initSettingsWorkOrdersPage();
	}
})();
