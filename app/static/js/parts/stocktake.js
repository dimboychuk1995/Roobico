/*
 * Экран инвентаризации (stocktake): ввод фактических количеств, фильтры,
 * добавление найденных партов, завершение/отмена сессии.
 * CSRF добавляется автоматически (static/js/csrf.js).
 */
(function () {
	"use strict";

	function initStocktakePage() {
		const page = document.getElementById("stocktakePage");
		if (!page) return;

		const stocktakeId = page.dataset.stocktakeId;
		const isOpen = page.dataset.stocktakeStatus === "open";

		function money(v) {
			const n = Number(v || 0);
			const abs = Math.abs(n).toFixed(2);
			if (n < 0) return '<span class="text-danger">-$' + abs + "</span>";
			if (n > 0) return '<span class="text-success">+$' + abs + "</span>";
			return '<span class="text-muted">$0.00</span>';
		}

		// -------- Фильтры и поиск (клиентские) --------
		const searchInput = document.getElementById("stSearchInput");
		const filterRadios = Array.from(document.querySelectorAll('input[name="stFilter"]'));

		function applyFilters() {
			const q = (searchInput && searchInput.value || "").trim().toLowerCase();
			const mode = (filterRadios.find(function (r) { return r.checked; }) || {}).value || "all";
			document.querySelectorAll(".st-item-row").forEach(function (row) {
				const status = row.dataset.status || "pending";
				const varianceRaw = row.dataset.variance;
				const variance = varianceRaw === "" ? null : Number(varianceRaw);
				let visible = true;
				if (mode === "pending") visible = status === "pending";
				else if (mode === "counted") visible = status === "counted";
				else if (mode === "discrepancy") visible = status === "counted" && variance !== null && variance !== 0;
				if (visible && q) {
					visible = (row.dataset.search || "").indexOf(q) !== -1;
				}
				row.classList.toggle("d-none", !visible);
			});
		}

		if (searchInput) searchInput.addEventListener("input", applyFilters);
		filterRadios.forEach(function (r) { r.addEventListener("change", applyFilters); });

		// -------- Сводка --------
		function refreshSummary() {
			let counted = 0;
			let discrepancies = 0;
			document.querySelectorAll(".st-item-row").forEach(function (row) {
				if (row.dataset.status === "counted") {
					counted += 1;
					const v = row.dataset.variance === "" ? 0 : Number(row.dataset.variance);
					if (v !== 0) discrepancies += 1;
				}
			});
			const countedBadge = document.getElementById("stCountedBadge");
			const discBadge = document.getElementById("stDiscrepanciesBadge");
			if (countedBadge) countedBadge.textContent = String(counted);
			if (discBadge) discBadge.textContent = String(discrepancies);
		}

		// -------- Сохранение факта по позиции --------
		async function saveCount(row) {
			const input = row.querySelector(".st-count-input");
			if (!input || input.value === "") {
				appAlert("Enter the counted quantity first.", "warning");
				return;
			}
			const itemId = row.dataset.itemId;
			try {
				const res = await fetch(`/parts/stocktakes/${encodeURIComponent(stocktakeId)}/count`, {
					method: "POST",
					headers: { "Content-Type": "application/json", "Accept": "application/json" },
					body: JSON.stringify({ item_id: itemId, counted_qty: input.value })
				});
				const data = await res.json();
				if (!res.ok || !data.ok) {
					appAlert((data && data.error) || "Failed to save count", "error");
					return;
				}
				const item = data.item || {};
				row.dataset.status = "counted";
				row.dataset.variance = String(item.variance);

				const expectedCell = row.querySelector(".st-expected");
				if (expectedCell) expectedCell.textContent = String(item.expected_at_count);

				const varianceCell = row.querySelector(".st-variance");
				if (varianceCell) {
					if (item.variance > 0) varianceCell.innerHTML = '<span class="text-success fw-semibold">+' + item.variance + "</span>";
					else if (item.variance < 0) varianceCell.innerHTML = '<span class="text-danger fw-semibold">' + item.variance + "</span>";
					else varianceCell.innerHTML = '<span class="text-muted">0</span>';
				}

				const valueCell = row.querySelector(".st-variance-value");
				if (valueCell) valueCell.innerHTML = money(item.variance_value);

				const statusCell = row.querySelector(".st-status-cell");
				if (statusCell) {
					statusCell.innerHTML = '<span class="badge text-bg-success st-status-badge">Counted</span>';
				}

				refreshSummary();
				applyFilters();
			} catch (err) {
				appAlert("Network error while saving count", "error");
			}
		}

		document.addEventListener("click", function (e) {
			const btn = e.target.closest(".st-count-save");
			if (!btn) return;
			const row = btn.closest(".st-item-row");
			if (row) saveCount(row);
		});

		document.addEventListener("keydown", function (e) {
			if (e.key !== "Enter") return;
			const input = e.target.closest(".st-count-input");
			if (!input) return;
			e.preventDefault();
			const row = input.closest(".st-item-row");
			if (row) saveCount(row);
		});

		// -------- Добавление найденного парта --------
		const addSearch = document.getElementById("stAddPartSearch");
		const addPartId = document.getElementById("stAddPartId");
		const addResults = document.getElementById("stAddPartResults");
		let searchTimer = null;

		function hideResults() {
			if (addResults) {
				addResults.classList.add("d-none");
				addResults.innerHTML = "";
			}
		}

		if (addSearch && addResults) {
			addSearch.addEventListener("input", function () {
				if (addPartId) addPartId.value = "";
				const q = addSearch.value.trim();
				if (searchTimer) clearTimeout(searchTimer);
				if (q.length < 2) {
					hideResults();
					return;
				}
				searchTimer = setTimeout(async function () {
					try {
						const res = await fetch(`/parts/api/search?q=${encodeURIComponent(q)}&limit=15`, {
							headers: { "Accept": "application/json" }
						});
						const data = await res.json();
						const items = (data && data.items) || [];
						if (!items.length) {
							hideResults();
							return;
						}
						addResults.innerHTML = items.map(function (it) {
							const label = (it.part_number || "") + (it.description ? " — " + it.description : "");
							return '<button type="button" class="list-group-item list-group-item-action st-add-result" data-part-id="' +
								it.id + '">' + label.replace(/</g, "&lt;") + "</button>";
						}).join("");
						addResults.classList.remove("d-none");
					} catch (err) {
						hideResults();
					}
				}, 250);
			});

			document.addEventListener("click", function (e) {
				const opt = e.target.closest(".st-add-result");
				if (opt) {
					if (addPartId) addPartId.value = opt.dataset.partId || "";
					addSearch.value = opt.textContent;
					hideResults();
					return;
				}
				if (!e.target.closest("#stAddPartResults") && !e.target.closest("#stAddPartSearch")) {
					hideResults();
				}
			});
		}

		const addBtn = document.getElementById("stAddItemBtn");
		if (addBtn) {
			addBtn.addEventListener("click", async function () {
				const partId = addPartId ? addPartId.value : "";
				if (!partId) {
					appAlert("Search and pick a part first.", "warning");
					return;
				}
				const locSel = document.getElementById("stAddLocation");
				try {
					const res = await fetch(`/parts/stocktakes/${encodeURIComponent(stocktakeId)}/items/add`, {
						method: "POST",
						headers: { "Content-Type": "application/json", "Accept": "application/json" },
						body: JSON.stringify({
							part_id: partId,
							location_id: locSel ? locSel.value : ""
						})
					});
					const data = await res.json();
					if (!res.ok || !data.ok) {
						appAlert((data && data.error) || "Failed to add item", "error");
						return;
					}
					window.location.reload();
				} catch (err) {
					appAlert("Network error while adding item", "error");
				}
			});
		}

		// -------- Завершение / отмена --------
		const completeBtn = document.getElementById("stCompleteBtn");
		if (completeBtn && isOpen) {
			completeBtn.addEventListener("click", async function () {
				let pending = 0;
				document.querySelectorAll(".st-item-row").forEach(function (row) {
					if (row.dataset.status !== "counted") pending += 1;
				});
				const swalOpts = {
					title: "Complete stocktake?",
					html: "Counted discrepancies will be applied to inventory as adjustments.",
					icon: "question",
					showCancelButton: true,
					confirmButtonText: "Complete & apply",
					cancelButtonText: "Not yet"
				};
				if (pending) {
					// Полная инвентаризация: непосчитанное = не найдено → 0.
					// Циклический пересчёт: непосчитанное не трогаем.
					swalOpts.html += "<br><b>" + pending + "</b> line(s) were not counted.";
					swalOpts.input = "checkbox";
					swalOpts.inputValue = 0;
					swalOpts.inputPlaceholder = "Set uncounted lines to zero (full inventory: not counted = not found)";
				}
				const result = await Swal.fire(swalOpts);
				if (!result.isConfirmed) return;
				const zeroUncounted = pending ? Boolean(result.value) : false;
				try {
					const res = await fetch(`/parts/stocktakes/${encodeURIComponent(stocktakeId)}/complete`, {
						method: "POST",
						headers: { "Content-Type": "application/json", "Accept": "application/json" },
						body: JSON.stringify({ zero_uncounted: zeroUncounted })
					});
					const data = await res.json();
					if (!res.ok || !data.ok) {
						appAlert((data && data.error) || "Failed to complete stocktake", "error");
						return;
					}
					window.location.reload();
				} catch (err) {
					appAlert("Network error while completing stocktake", "error");
				}
			});
		}

		const cancelBtn = document.getElementById("stCancelBtn");
		if (cancelBtn && isOpen) {
			cancelBtn.addEventListener("click", async function () {
				const result = await Swal.fire({
					title: "Cancel stocktake?",
					text: "Counted quantities will be discarded and no adjustments applied.",
					icon: "warning",
					showCancelButton: true,
					confirmButtonText: "Cancel stocktake",
					cancelButtonText: "Keep counting"
				});
				if (!result.isConfirmed) return;
				try {
					const res = await fetch(`/parts/stocktakes/${encodeURIComponent(stocktakeId)}/cancel`, {
						method: "POST",
						headers: { "Content-Type": "application/json", "Accept": "application/json" }
					});
					const data = await res.json();
					if (!res.ok || !data.ok) {
						appAlert((data && data.error) || "Failed to cancel stocktake", "error");
						return;
					}
					window.location.href = "/parts?tab=stocktakes";
				} catch (err) {
					appAlert("Network error while cancelling stocktake", "error");
				}
			});
		}
	}

	document.addEventListener("DOMContentLoaded", initStocktakePage);
	document.addEventListener("roobico:content-replaced", initStocktakePage);
})();
