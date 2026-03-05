
(function () {
	"use strict";

	document.addEventListener("DOMContentLoaded", function () {
		const toggle = document.getElementById("coreChargeToggle");
		const group = document.getElementById("coreCostGroup");
			const miscToggle = document.getElementById("miscChargeToggle");
			const miscGroup = document.getElementById("miscChargesGroup");
			const miscBody = document.getElementById("miscChargesBody");
			const addMiscBtn = document.getElementById("addMiscChargeBtn");

		if (toggle && group) {
			const syncCoreUi = () => {
				group.style.display = toggle.checked ? "" : "none";
			};

			toggle.addEventListener("change", syncCoreUi);
			syncCoreUi();
		}

			function buildMiscRow(idx, desc, price) {
				const tr = document.createElement("tr");
				tr.dataset.index = String(idx);
				tr.innerHTML = `
					<td>
						<input class="form-control form-control-sm misc-desc" name="misc_charges[${idx}][description]" value="${desc || ""}" maxlength="200" />
					</td>
					<td class="text-end">
						<input class="form-control form-control-sm text-end misc-price" name="misc_charges[${idx}][price]" type="number" min="0" step="0.01" value="${price || ""}" />
					</td>
					<td class="text-end">
						<button type="button" class="btn btn-sm btn-outline-danger remove-misc-btn">Remove</button>
					</td>
				`;
				return tr;
			}

			function renumberMiscRows() {
				if (!miscBody) return;
				const rows = Array.from(miscBody.querySelectorAll("tr"));
				rows.forEach((tr, idx) => {
					tr.dataset.index = String(idx);
					const desc = tr.querySelector(".misc-desc");
					const price = tr.querySelector(".misc-price");
					if (desc) desc.name = `misc_charges[${idx}][description]`;
					if (price) price.name = `misc_charges[${idx}][price]`;
				});
			}

			function addMiscRow() {
				if (!miscBody) return;
				const idx = miscBody.querySelectorAll("tr").length;
				miscBody.appendChild(buildMiscRow(idx));
			}

			function clearMiscRows() {
				if (!miscBody) return;
				miscBody.innerHTML = "";
			}

			function syncMiscUi() {
				if (!miscToggle || !miscGroup) return;
				miscGroup.style.display = miscToggle.checked ? "" : "none";
				if (miscToggle.checked && miscBody && miscBody.querySelectorAll("tr").length === 0) {
					addMiscRow();
				}
				if (!miscToggle.checked) {
					clearMiscRows();
				}
			}

			if (miscToggle && miscGroup && miscBody && addMiscBtn) {
				miscToggle.addEventListener("change", syncMiscUi);
				addMiscBtn.addEventListener("click", addMiscRow);
				miscBody.addEventListener("click", function (e) {
					const btn = e.target.closest(".remove-misc-btn");
					if (!btn) return;
					const tr = btn.closest("tr");
					if (tr) tr.remove();
					renumberMiscRows();
				});
				syncMiscUi();
			}

		const vendorSelect = document.getElementById("order_vendor");
		const partSearch = document.getElementById("partSearch");
		const dropdown = document.getElementById("partSearchDropdown");
		const itemsBody = document.getElementById("orderItemsBody");

		const createOrderBtn = document.getElementById("createOrderBtn");
		const receiveBtn = document.getElementById("receiveBtn");
		const createdOrderId = document.getElementById("createdOrderId");
		const orderCreatedBox = document.getElementById("orderCreatedBox");
		const orderAlert = document.getElementById("orderAlert");
		const orderTotalAmount = document.getElementById("orderTotalAmount");

		let orderItems = [];

			if (!vendorSelect || !partSearch || !dropdown || !itemsBody || !createOrderBtn || !receiveBtn || !createdOrderId || !orderCreatedBox || !orderAlert || !orderTotalAmount) {
			return;
		}

		let searchAbort = null;

		function escapeHtml(str) {
			return (str || "").replace(/[&<>"']/g, (m) => ({
				"&": "&amp;",
				"<": "&lt;",
				">": "&gt;",
				'"': "&quot;",
				"'": "&#039;",
			}[m]));
		}

		function showError(msg) {
			orderAlert.textContent = msg || "Error";
			orderAlert.classList.remove("d-none");
		}

		function clearError() {
			orderAlert.textContent = "";
			orderAlert.classList.add("d-none");
		}

		function calculateOrderTotal() {
			let total = 0;
			const rows = Array.from(itemsBody.querySelectorAll("tr[data-part-id]"));
			
			rows.forEach(tr => {
				const qty = parseInt((tr.querySelector(".qty-input")?.value || "0"), 10);
				const price = parseFloat((tr.querySelector(".price-input")?.value || "0"));
				const coreHasCharge = tr.getAttribute("data-core-has-charge") === "true";
				const coreCost = parseFloat(tr.getAttribute("data-core-cost") || "0");
				
				if (qty > 0 && price >= 0) {
					// Base price * quantity
					total += price * qty;
					
					// Add core charge per unit if applicable
					if (coreHasCharge && coreCost > 0) {
						total += coreCost * qty;
					}
				}
			});
			
			orderTotalAmount.textContent = "$" + total.toFixed(2);
		}

		function hideDropdown() {
			dropdown.style.display = "none";
			dropdown.innerHTML = "";
		}

		function showDropdown() {
			dropdown.style.display = "block";
		}

		async function searchParts(q) {
			if (searchAbort) searchAbort.abort();
			searchAbort = new AbortController();

			const res = await fetch(`/parts/api/search?q=${encodeURIComponent(q)}&limit=20`, {
				signal: searchAbort.signal,
			});
			return await res.json();
		}

		function ensureEmptyRowRemoved() {
			const empty = document.getElementById("emptyOrderRow");
			if (empty) empty.remove();
		}

		function ensureEmptyRowShown() {
			if (itemsBody.querySelectorAll("tr").length === 0) {
				const empty = document.createElement("tr");
				empty.id = "emptyOrderRow";
				empty.innerHTML = `<td colspan="5" class="text-muted">No items added.</td>`;
				itemsBody.appendChild(empty);
			}
		}

		function addOrIncrementItem(item) {
			const pid = item.id;
			if (!pid) return;

			const existing = itemsBody.querySelector(`tr[data-part-id="${pid}"]`);
			if (existing) {
				const qtyInput = existing.querySelector(".qty-input");
				if (qtyInput) {
					const cur = parseInt(qtyInput.value || "0", 10);
					qtyInput.value = String((cur || 0) + 1);
				}
				calculateOrderTotal();
				hideDropdown();
				partSearch.value = "";
				partSearch.focus();
				return;
			}

			ensureEmptyRowRemoved();

			const pn = item.part_number || "";
			const desc = item.description || "";
			const price = Number(item.average_cost || 0).toFixed(2);
			const coreHasCharge = item.core_has_charge || false;
			const coreCost = Number(item.core_cost || 0).toFixed(2);
			
			const coreIndicator = coreHasCharge && coreCost > 0
				? `<span class="badge bg-warning text-dark ms-1" title="Core charge: $${coreCost} per unit">Core</span>`
				: '';

			const tr = document.createElement("tr");
			tr.setAttribute("data-part-id", pid);
			tr.setAttribute("data-core-has-charge", coreHasCharge ? "true" : "false");
			tr.setAttribute("data-core-cost", coreCost);
			tr.innerHTML = `
				<td class="fw-semibold">${escapeHtml(pn)}${coreIndicator}</td>
				<td class="text-muted">${escapeHtml(desc) || "-"}</td>
				<td class="text-end">
					<input class="form-control form-control-sm text-end qty-input" type="number" min="1" step="1" value="1" required>
				</td>
				<td class="text-end">
					<input class="form-control form-control-sm text-end price-input" type="number" min="0" step="0.01" value="${price}" required>
				</td>
				<td class="text-end">
					<button type="button" class="btn btn-sm btn-outline-danger remove-item-btn">Remove</button>
				</td>
			`;
			itemsBody.appendChild(tr);

			calculateOrderTotal();
			hideDropdown();
			partSearch.value = "";
			partSearch.focus();
		}

		function renderOrderItems() {
			itemsBody.innerHTML = "";
			if (orderItems.length === 0) {
				ensureEmptyRowShown();
				calculateOrderTotal();
				return;
			}
			ensureEmptyRowRemoved();
			orderItems.forEach(item => {
				const coreIndicator = item.core_has_charge && item.core_cost > 0
					? `<span class="badge bg-warning text-dark ms-1" title="Core charge: $${Number(item.core_cost).toFixed(2)} per unit">Core</span>`
					: '';
				
				const tr = document.createElement("tr");
				tr.setAttribute("data-part-id", item.part_id);
				tr.setAttribute("data-core-has-charge", item.core_has_charge ? "true" : "false");
				tr.setAttribute("data-core-cost", item.core_cost || "0");
				tr.innerHTML = `
					<td class="fw-semibold">${escapeHtml(item.part_number)}${coreIndicator}</td>
					<td class="text-muted">${escapeHtml(item.description) || "-"}</td>
					<td class="text-end">
						<input class="form-control form-control-sm text-end qty-input" type="number" min="1" step="1" value="${item.quantity}" required>
					</td>
					<td class="text-end">
						<input class="form-control form-control-sm text-end price-input" type="number" min="0" step="0.01" value="${item.price}" required>
					</td>
					<td class="text-end">
						<button type="button" class="btn btn-sm btn-outline-danger remove-item-btn">Remove</button>
					</td>
				`;
				itemsBody.appendChild(tr);
			});
			calculateOrderTotal();
		}

		vendorSelect.addEventListener("change", function () {
			clearError();
			partSearch.disabled = !vendorSelect.value;
			partSearch.value = "";
			hideDropdown();
			if (vendorSelect.value) partSearch.focus();
		});

		partSearch.addEventListener("input", async function () {
			const q = (partSearch.value || "").trim();
			if (q.length < 2) { hideDropdown(); return; }

			try {
				const data = await searchParts(q);
				if (!data.ok) { hideDropdown(); return; }

				dropdown.innerHTML = "";

				if (!data.items || data.items.length === 0) {
					dropdown.innerHTML = `<div class="list-group-item text-muted">No results</div>`;
					showDropdown();
					return;
				}

				data.items.forEach((item) => {
					const el = document.createElement("button");
					el.type = "button";
					el.className = "list-group-item list-group-item-action";
					
					const priceDisplay = Number(item.average_cost || 0).toFixed(2);
					const coreInfo = item.core_has_charge && item.core_cost > 0 
						? `<span class="badge bg-warning text-dark ms-1" title="Core charge included">+Core $${Number(item.core_cost).toFixed(2)}</span>`
						: '';
					
					el.innerHTML = `
						<div class="d-flex justify-content-between">
							<div class="fw-semibold">${escapeHtml(item.part_number)}</div>
							<div class="text-muted small">$${priceDisplay}${coreInfo}</div>
						</div>
						<div class="text-muted small">${escapeHtml(item.description)}</div>
					`;
					el.addEventListener("click", () => addOrIncrementItem(item));
					dropdown.appendChild(el);
				});

				showDropdown();
			} catch (e) {
				hideDropdown();
			}
		});

		document.addEventListener("click", function (e) {
			if (!dropdown.contains(e.target) && e.target !== partSearch) hideDropdown();
		});

		itemsBody.addEventListener("click", function (e) {
			const btn = e.target.closest(".remove-item-btn");
			if (!btn) return;
			const tr = btn.closest("tr");
			if (tr) tr.remove();
			ensureEmptyRowShown();
			calculateOrderTotal();
		});

		// Recalculate total when quantity or price changes
		itemsBody.addEventListener("input", function (e) {
			if (e.target.classList.contains("qty-input") || e.target.classList.contains("price-input")) {
				calculateOrderTotal();
			}
		});

		async function createOrderAjax() {
			clearError();

			const vendorId = vendorSelect.value || "";
			if (!vendorId) { showError("Select vendor."); return; }

			const rows = Array.from(itemsBody.querySelectorAll("tr[data-part-id]"));
			if (rows.length === 0) { showError("Add at least one item."); return; }

			const items = [];
			for (const tr of rows) {
				const pid = tr.getAttribute("data-part-id");
				const qty = parseInt((tr.querySelector(".qty-input")?.value || "0"), 10);
				const price = parseFloat((tr.querySelector(".price-input")?.value || "0"));

				if (!pid) continue;
				if (!qty || qty <= 0) { showError("Qty must be > 0."); return; }
				if (price < 0) { showError("Price cannot be negative."); return; }

				items.push({ part_id: pid, quantity: qty, price: price });
			}

			createOrderBtn.disabled = true;

			try {
				const isEdit = createdOrderId.value !== "";
				const endpoint = isEdit 
					? `/parts/api/orders/${createdOrderId.value}/update`
					: "/parts/api/orders/create";
				const method = isEdit ? "PUT" : "POST";

				const res = await fetch(endpoint, {
					method: method,
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ vendor_id: vendorId, items }),
				});
				const data = await res.json();

				if (!data.ok) {
					showError(data.error || (isEdit ? "Update order failed." : "Create order failed."));
					createOrderBtn.disabled = false;
					return;
				}

				if (!isEdit) {
					createdOrderId.value = data.order_id;
				}
				orderCreatedBox.classList.remove("d-none");

				vendorSelect.disabled = true;
				partSearch.disabled = true;

				rows.forEach((tr) => {
					const q = tr.querySelector(".qty-input");
					const p = tr.querySelector(".price-input");
					if (q) q.disabled = true;
					if (p) p.disabled = true;
					const rm = tr.querySelector(".remove-item-btn");
					if (rm) rm.disabled = true;
				});

			} catch (e) {
				showError("Network error while " + (createdOrderId.value ? "updating" : "creating") + " order.");
				createOrderBtn.disabled = false;
			}
		}

		async function receiveOrderAjax() {
			clearError();

			const oid = createdOrderId.value || "";
			if (!oid) { showError("Order id missing."); return; }

			receiveBtn.disabled = true;

			try {
				const res = await fetch(`/parts/api/orders/${encodeURIComponent(oid)}/receive`, {
					method: "POST",
				});
				const data = await res.json();

				if (!data.ok) {
					showError(data.error || "Receive failed.");
					receiveBtn.disabled = false;
					return;
				}

				const modalEl = document.getElementById("orderModal");
				const modal = window.bootstrap?.Modal?.getInstance(modalEl);
				if (modal) modal.hide();

				location.reload();
			} catch (e) {
				showError("Network error while receiving order.");
				receiveBtn.disabled = false;
			}
		}

		createOrderBtn.addEventListener("click", createOrderAjax);
		receiveBtn.addEventListener("click", receiveOrderAjax);

		// Load order data when Edit button clicked
		document.addEventListener("click", async function (e) {
			const btn = e.target.closest(".editOrderBtn");
			if (!btn) return;
			
			const orderId = btn.getAttribute("data-order-id");
			if (!orderId) return;

			try {
				const res = await fetch(`/parts/api/orders/${encodeURIComponent(orderId)}`);
				if (!res.ok) {
					showError("Failed to load order");
					return;
				}

				const data = await res.json();
				if (!data.ok || !data.order) {
					showError("Order not found");
					return;
				}

				const order = data.order;			
			// Prevent editing received orders
			if (order.status === "received") {
				showError("Cannot edit received orders. Click 'Unreceive' first to modify or delete.");
				return;
			}
							// Set vendor
				vendorSelect.value = order.vendor_id || "";
				// Clear current items
				orderItems = [];
				// Load items from order
				if (Array.isArray(order.items)) {
					orderItems = order.items.map(item => ({
						part_id: item.part_id,
						part_number: item.part_number,
						description: item.description,
						quantity: item.quantity,
						price: item.price,
						core_has_charge: item.core_has_charge || false,
						core_cost: item.core_cost || 0
					}));
				}
				// Render items
				renderOrderItems();
				// Mark as editing
				createdOrderId.value = orderId;
				orderCreatedBox.classList.add("d-none");
				createOrderBtn.textContent = "Update order";
			} catch (err) {
				showError("Network error while loading order");
			}
		});

		// Reset form when modal is closed
		const orderModal = document.getElementById("orderModal");
		orderModal?.addEventListener("hidden.bs.modal", function () {
			vendorSelect.value = "";
			partSearch.value = "";
			partSearch.disabled = true;
			dropdown.style.display = "none";
			orderItems = [];
			renderOrderItems();
			createdOrderId.value = "";
			orderCreatedBox.classList.add("d-none");
			orderAlert.classList.add("d-none");
			createOrderBtn.textContent = "Create order";
		});

		const partHistoryModal = document.getElementById("partHistoryModal");
		const partHistoryMeta = document.getElementById("partHistoryMeta");
		const partHistoryOrdersBody = document.getElementById("partHistoryOrdersBody");
		const partHistoryWorkOrdersBody = document.getElementById("partHistoryWorkOrdersBody");

		function formatDateTime(v) {
			if (!v) return "-";
			const d = new Date(v);
			if (Number.isNaN(d.getTime())) return "-";
			return d.toLocaleString();
		}

		function money(n) {
			const x = Number(n || 0);
			return Number.isFinite(x) ? x.toFixed(2) : "0.00";
		}

		async function loadPartHistory(partId) {
			if (!partHistoryMeta || !partHistoryOrdersBody || !partHistoryWorkOrdersBody) return;

			partHistoryMeta.textContent = "Loading...";
			partHistoryOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">Loading...</td></tr>`;
			partHistoryWorkOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">Loading...</td></tr>`;

			try {
				const res = await fetch(`/parts/api/${encodeURIComponent(partId)}/history`, {
					method: "GET",
					headers: { "Accept": "application/json" }
				});
				const data = await res.json();

				if (!res.ok || !data.ok) {
					partHistoryMeta.textContent = data?.error || "Failed to load part history";
					partHistoryOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">No data.</td></tr>`;
					partHistoryWorkOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">No data.</td></tr>`;
					return;
				}

				const part = data.part || {};
				const orders = Array.isArray(data.orders) ? data.orders : [];
				const workOrders = Array.isArray(data.work_orders) ? data.work_orders : [];

				partHistoryMeta.textContent = `${part.part_number || ""}${part.description ? ` — ${part.description}` : ""} | Orders: ${orders.length}, Work Orders: ${workOrders.length}`;

				if (orders.length === 0) {
					partHistoryOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">No orders found for this part.</td></tr>`;
				} else {
				partHistoryOrdersBody.innerHTML = orders.map((row) => {
					const orderNum = row.order_number || (row.order_id ? `#${row.order_id.slice(-6)}` : "-");
					return `
					<tr>
						<td><span class="badge bg-secondary">${orderNum}</span></td>
						<td>${escapeHtml(row.status || "-")}</td>
						<td>${escapeHtml(row.vendor || "-")}</td>
						<td class="text-end">${Number(row.quantity || 0)}</td>
						<td class="text-end">$${money(row.price)}</td>
						<td class="small">${escapeHtml(formatDateTime(row.created_at))}</td>
						<td class="small">${escapeHtml(formatDateTime(row.received_at))}</td>
					</tr>
				`;
				}).join("");
				}

				if (workOrders.length === 0) {
					partHistoryWorkOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">No work orders found for this part.</td></tr>`;
				} else {
				partHistoryWorkOrdersBody.innerHTML = workOrders.map((row) => {
					const woNum = row.wo_number || (row.work_order_id ? `#${row.work_order_id.slice(-6)}` : "-");
					return `
					<tr>
						<td><span class="badge bg-secondary">${woNum}</span></td>
						<td>${escapeHtml(row.status || "-")}</td>
						<td>${escapeHtml(row.customer || "-")}</td>
						<td>${escapeHtml(row.unit || "-")}</td>
						<td class="text-end">${Number(row.used_qty || 0)}</td>
						<td class="text-end">$${money(row.grand_total)}</td>
						<td class="small">${escapeHtml(formatDateTime(row.created_at))}</td>
					</tr>
				`;
				}).join("");
				}
			} catch (err) {
				partHistoryMeta.textContent = "Network error while loading history";
				partHistoryOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">No data.</td></tr>`;
				partHistoryWorkOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">No data.</td></tr>`;
			}
		}

		document.addEventListener("click", function (e) {
			const btn = e.target.closest(".partHistoryBtn");
			if (!btn) return;
			const partId = btn.getAttribute("data-part-id");
			if (!partId) return;
			loadPartHistory(partId);
		});

		partHistoryModal?.addEventListener("hidden.bs.modal", function () {
			if (partHistoryMeta) partHistoryMeta.textContent = "Loading...";
			if (partHistoryOrdersBody) partHistoryOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">No data.</td></tr>`;
			if (partHistoryWorkOrdersBody) partHistoryWorkOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">No data.</td></tr>`;
		});

		document.addEventListener("show.bs.modal", function (e) {
			if (!e.target || e.target.id !== "orderModal") return;

			clearError();

			vendorSelect.disabled = false;
			vendorSelect.value = "";
			partSearch.disabled = true;
			partSearch.value = "";
			hideDropdown();

			createdOrderId.value = "";
			orderCreatedBox.classList.add("d-none");

			createOrderBtn.disabled = false;
			receiveBtn.disabled = false;

			itemsBody.innerHTML = `
				<tr id="emptyOrderRow">
					<td colspan="5" class="text-muted">No items added.</td>
				</tr>
			`;
			
			orderTotalAmount.textContent = "$0.00";
		});

		// ---- Order List Management (Receive, Unreceive, Delete from Orders tab) ----
		document.addEventListener("click", async function (e) {
			// Receive order button
			const receiveBtn = e.target.closest(".receiveOrderBtn");
			if (receiveBtn) {
				const orderId = receiveBtn.getAttribute("data-order-id");
				if (!orderId) return;

				if (confirm("Mark this order as received?")) {
					try {
						const res = await fetch(`/parts/api/orders/${encodeURIComponent(orderId)}/receive`, {
							method: "POST"
						});
						const data = await res.json();

						if (data.ok) {
							alert(`Order received! ${data.updated_parts} parts updated.`);
							location.reload();
						} else {
							alert("Error: " + (data.error || "Failed to receive order"));
						}
					} catch (err) {
						alert("Network error while receiving order");
					}
				}
				return;
			}

			// Unreceive order button
			const unreceiveBtn = e.target.closest(".unreceiveOrderBtn");
			if (unreceiveBtn) {
				const orderId = unreceiveBtn.getAttribute("data-order-id");
				if (!orderId) return;

				if (confirm("Unreceive this order? Items will be removed from inventory.")) {
					try {
						const res = await fetch(`/parts/api/orders/${encodeURIComponent(orderId)}/unreceive`, {
							method: "POST"
						});
						const data = await res.json();

						if (data.ok) {
							alert(`Order unreceived! ${data.updated_parts} parts removed from inventory.`);
							location.reload();
						} else {
							alert("Error: " + (data.error || "Failed to unreceive order"));
						}
					} catch (err) {
						alert("Network error while unreceiving order");
					}
				}
				return;
			}

			// Delete order button
			const deleteBtn = e.target.closest(".deleteOrderBtn");
			if (deleteBtn) {
				const orderId = deleteBtn.getAttribute("data-order-id");
				if (!orderId) return;

				if (confirm("Delete this order? If received, items will be removed from inventory.")) {
					try {
						const res = await fetch(`/parts/api/orders/${encodeURIComponent(orderId)}`, {
							method: "DELETE"
						});
						const data = await res.json();

						if (data.ok) {
							alert("Order deleted successfully");
							location.reload();
						} else {
							alert("Error: " + (data.error || "Failed to delete order"));
						}
					} catch (err) {
						alert("Network error while deleting order");
					}
				}
				return;
			}
		});

	// ---- Edit Part Modal Logic ----
	const createPartModal = document.getElementById('createPartModal');
	if (createPartModal) {
		const editingPartId = document.getElementById('editingPartId');
		const modalTitle = document.getElementById('createPartModalLabel');
		const partNumberInput = document.querySelector('input[name="part_number"]');
		const descriptionInput = document.querySelector('input[name="description"]');
		const referenceInput = document.querySelector('input[name="reference"]');
		const vendorSelectParts = document.querySelector('select[name="vendor_id"]');
		const categorySelect = document.querySelector('select[name="category_id"]');
		const locationSelect = document.querySelector('select[name="location_id"]');
		const inStockInput = document.querySelector('input[name="in_stock"]');
		const averageCostInput = document.querySelector('input[name="average_cost"]');
		const coreCheckbox = document.getElementById('coreChargeToggle');
		const coreCostInput = document.querySelector('input[name="core_cost"]');
		const miscCheckbox = document.getElementById('miscChargeToggle');
		const miscBodyParts = document.getElementById('miscChargesBody');
		const form = document.querySelector('form[action*="/parts/create"]');

		// Handle Edit Part buttons
		document.addEventListener('click', function(e) {
			const btn = e.target.closest('.editPartBtn');
			if (!btn) return;

			const partId = btn.getAttribute('data-part-id');
			if (!partId) return;

			// Fetch part data
			fetch(`/parts/api/${encodeURIComponent(partId)}`, {
				method: 'GET',
				headers: { 'Accept': 'application/json' }
			})
			.then(res => res.json())
			.then(data => {
				if (!data.ok || !data.item) {
					alert('Failed to load part data');
					return;
				}

				const item = data.item;

				// Set editing mode
				editingPartId.value = partId;
				modalTitle.textContent = 'Edit part: ' + item.part_number;

				// Fill form with data
				partNumberInput.value = item.part_number;
				descriptionInput.value = item.description;
				referenceInput.value = item.reference;
				vendorSelectParts.value = item.vendor_id;
				categorySelect.value = item.category_id;
				locationSelect.value = item.location_id;
				inStockInput.value = item.in_stock;
				averageCostInput.value = item.average_cost;

				// Core charge
				coreCheckbox.checked = item.core_has_charge;
				coreCostInput.value = item.core_cost;
				document.getElementById('coreCostGroup').style.display = item.core_has_charge ? '' : 'none';

				// Misc charges
				miscCheckbox.checked = item.misc_has_charge;
				if (miscBodyParts) {
					miscBodyParts.innerHTML = '';
					if (item.misc_charges && item.misc_charges.length > 0) {
						item.misc_charges.forEach((charge, idx) => {
							const tr = document.createElement('tr');
							tr.dataset.index = String(idx);
							tr.innerHTML = `
								<td>
									<input class="form-control form-control-sm misc-desc" name="misc_charges[${idx}][description]" value="${charge.description || ''}" maxlength="200" />
								</td>
								<td class="text-end">
									<input class="form-control form-control-sm text-end misc-price" name="misc_charges[${idx}][price]" type="number" min="0" step="0.01" value="${charge.price || ''}" />
								</td>
								<td class="text-end">
									<button type="button" class="btn btn-sm btn-outline-danger remove-misc-btn">Remove</button>
								</td>
							`;
							miscBodyParts.appendChild(tr);
						});
					}
				}

				document.getElementById('miscChargesGroup').style.display = item.misc_has_charge ? '' : 'none';
			})
			.catch(err => {
				console.error('Error loading part:', err);
				alert('Error loading part data');
			});
		});

		// Handle form submission (both create and edit)
		if (form) {
			form.addEventListener('submit', async function(e) {
				// If we're in edit mode, use AJAX instead of form submission
				if (editingPartId && editingPartId.value) {
					e.preventDefault();

					// Gather form data
					const partId = editingPartId.value;
					const formData = {
						part_number: partNumberInput.value.trim(),
						description: descriptionInput.value.trim(),
						reference: referenceInput.value.trim(),
						vendor_id: vendorSelectParts.value,
						category_id: categorySelect.value,
						location_id: locationSelect.value,
						in_stock: parseInt(inStockInput.value || '0'),
						average_cost: parseFloat(averageCostInput.value || '0'),
						core_has_charge: coreCheckbox.checked,
						core_cost: parseFloat(coreCostInput.value || '0'),
						misc_has_charge: miscCheckbox.checked,
						misc_charges: []
					};

					// Gather misc charges
					if (miscBodyParts) {
						const rows = miscBodyParts.querySelectorAll('tr');
						rows.forEach(tr => {
							const desc = tr.querySelector('.misc-desc').value.trim();
							const price = parseFloat(tr.querySelector('.misc-price').value || '0');
							if (desc && price >= 0) {
								formData.misc_charges.push({ description: desc, price: price });
							}
						});
					}

					// Send AJAX request
					try {
						const res = await fetch(`/parts/api/${encodeURIComponent(partId)}/update`, {
							method: 'POST',
							headers: { 'Content-Type': 'application/json' },
							body: JSON.stringify(formData)
						});
						const result = await res.json();

						if (result.ok) {
							// Close modal and reload page
							const modal = bootstrap.Modal.getInstance(createPartModal);
							if (modal) modal.hide();
							location.reload();
						} else {
							alert('Update failed: ' + (result.error || 'Unknown error'));
						}
					} catch (err) {
						console.error('Error updating part:', err);
						alert('Network error while updating part');
					}

					return false;
				}
				// Otherwise, use default form submission for create
			});
		}

		// Reset form when modal is hidden
		createPartModal.addEventListener('hidden.bs.modal', function() {
			editingPartId.value = '';
			modalTitle.textContent = 'Create new part';
			form.reset();
			if (document.getElementById('coreCostGroup')) {
				document.getElementById('coreCostGroup').style.display = 'none';
			}
			if (document.getElementById('miscChargesGroup')) {
				document.getElementById('miscChargesGroup').style.display = 'none';
			}
		});
	}

	});
})();
