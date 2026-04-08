
(function () {
	"use strict";
	const APP_TIMEZONE = document.body?.dataset?.appTimezone || "UTC";

	function initPartsPage() {
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

			function buildMiscRow(idx, desc, price, taxable) {
				const tr = document.createElement("tr");
				tr.dataset.index = String(idx);
				const isChecked = taxable !== false;
				tr.innerHTML = `
					<td>
						<input class="form-control form-control-sm misc-desc" name="misc_charges[${idx}][description]" value="${desc || ""}" maxlength="200" />
					</td>
					<td class="text-end">
						<input class="form-control form-control-sm text-end misc-price" name="misc_charges[${idx}][price]" type="number" min="0" step="0.01" value="${price || ""}" />
					</td>
					<td class="text-center">
						<input class="form-check-input misc-taxable" name="misc_charges[${idx}][taxable]" type="checkbox" value="1" ${isChecked ? "checked" : ""}>
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
					const taxable = tr.querySelector(".misc-taxable");
					if (desc) desc.name = `misc_charges[${idx}][description]`;
					if (price) price.name = `misc_charges[${idx}][price]`;
					if (taxable) taxable.name = `misc_charges[${idx}][taxable]`;
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
		const vendorSearchInput = document.getElementById("order_vendor_search");
		const vendorDropdown = document.getElementById("orderVendorDropdown");
		const partSearch = document.getElementById("partSearch");
		const dropdown = document.getElementById("partSearchDropdown");
		const itemsBody = document.getElementById("orderItemsBody");

		const createOrderBtn = document.getElementById("createOrderBtn");
		const receiveBtn = document.getElementById("receiveBtn");
		const createdOrderId = document.getElementById("createdOrderId");
		const orderCreatedBox = document.getElementById("orderCreatedBox");
		const orderAlert = document.getElementById("orderAlert");
		const orderTotalAmount = document.getElementById("orderTotalAmount");
		const orderDateInput = document.getElementById("orderDateInput");
		const orderDatesBlock = document.getElementById("orderDatesBlock");
		const orderMetaCreated = document.getElementById("orderMetaCreated");
		const orderMetaReceived = document.getElementById("orderMetaReceived");
		const orderMetaPaymentsBody = document.getElementById("orderMetaPaymentsBody");
		const payOrderModalBtn = document.getElementById("payOrderModalBtn");
		const receiveOrderModalBtn = document.getElementById("receiveOrderModalBtn");
		const unreceiveOrderModalBtn = document.getElementById("unreceiveOrderModalBtn");
		const nonInventoryBody = document.getElementById("nonInventoryBody");
		const orderModal = document.getElementById("orderModal");

		let orderItems = [];
		let currentOrderStatus = null;
		let currentVendorBill = "";

		const canUseOrderComposer = !!(
			vendorSelect && vendorSearchInput && vendorDropdown && partSearch && dropdown && itemsBody &&
			createOrderBtn && createdOrderId && orderCreatedBox && orderAlert && orderTotalAmount && nonInventoryBody
		);

		if (canUseOrderComposer) {
			try {

			const vendorOptions = Array.from(vendorSelect.options)
				.filter((opt) => opt.value)
				.map((opt) => ({ id: String(opt.value), label: String(opt.textContent || "").trim() }));

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

			const nonInventoryRows = Array.from(nonInventoryBody.querySelectorAll("tr"));
			nonInventoryRows.forEach((tr) => {
				const amount = parseFloat((tr.querySelector(".non-inv-amount")?.value || "0"));
				if (Number.isFinite(amount) && amount > 0) {
					total += amount;
				}
			});
			
			orderTotalAmount.textContent = "$" + total.toFixed(2);
		}

		function nonInventoryRowHasData(tr) {
			if (!tr) return false;
			const type = String(tr.querySelector(".non-inv-type")?.value || "").trim();
			const desc = String(tr.querySelector(".non-inv-desc")?.value || "").trim();
			const amount = parseFloat(tr.querySelector(".non-inv-amount")?.value || "0");
			return !!type || !!desc || (Number.isFinite(amount) && amount > 0);
		}

		function appendNonInventoryRow(type, description, amount, disabled) {
			const tr = document.createElement("tr");
			tr.innerHTML = `
				<td>
					<select class="form-select form-select-sm non-inv-type" ${disabled ? "disabled" : ""}>
						<option value="">-- Select type --</option>
						<option value="shop_supply" ${type === "shop_supply" ? "selected" : ""}>shop supply</option>
						<option value="tools" ${type === "tools" ? "selected" : ""}>tools</option>
						<option value="utilities" ${type === "utilities" ? "selected" : ""}>utilities</option>
						<option value="payment_to_another_service" ${type === "payment_to_another_service" ? "selected" : ""}>payment to another service</option>
					</select>
				</td>
				<td>
					<input type="text" class="form-control form-control-sm non-inv-desc" maxlength="200" placeholder="e.g. bolts, shop supplies, tool" value="${escapeHtml(description || "")}" ${disabled ? "disabled" : ""}>
				</td>
				<td class="text-end">
					<input type="number" class="form-control form-control-sm text-end non-inv-amount" min="0" step="0.01" value="${Number(amount || 0) > 0 ? Number(amount).toFixed(2) : ""}" ${disabled ? "disabled" : ""}>
				</td>
				<td class="text-end">
					<button type="button" class="btn btn-sm btn-outline-danger non-inv-remove-btn" ${disabled ? "disabled" : ""}>Remove</button>
				</td>
			`;
			nonInventoryBody.appendChild(tr);
			return tr;
		}

		function ensureTrailingNonInventoryRow(disabled) {
			const rows = Array.from(nonInventoryBody.querySelectorAll("tr"));
			if (rows.length === 0) {
				appendNonInventoryRow("", "", 0, !!disabled);
				return;
			}
			const last = rows[rows.length - 1];
			if (nonInventoryRowHasData(last) && !disabled) {
				appendNonInventoryRow("", "", 0, false);
			}
		}

		function renderNonInventoryRows(lines, disabled) {
			nonInventoryBody.innerHTML = "";
			const source = Array.isArray(lines) ? lines : [];
			source.forEach((line) => {
				if (!line || typeof line !== "object") return;
				appendNonInventoryRow(line.type || "", line.description || "", line.amount || 0, !!disabled);
			});
			ensureTrailingNonInventoryRow(!!disabled);
			calculateOrderTotal();
		}

		function collectNonInventoryAmounts() {
			const rows = Array.from(nonInventoryBody.querySelectorAll("tr"));
			const lines = [];
			for (const tr of rows) {
				const type = String(tr.querySelector(".non-inv-type")?.value || "").trim();
				const description = String(tr.querySelector(".non-inv-desc")?.value || "").trim();
				const rawAmount = String(tr.querySelector(".non-inv-amount")?.value || "").trim();
				const amount = parseFloat(rawAmount || "0");

				if (!type && !description && !rawAmount) {
					continue;
				}

				if (!type) {
					return { lines: [], error: "Select non inventory type." };
				}

				if (!description) {
					return { lines: [], error: "Non inventory description is required." };
				}

				if (!Number.isFinite(amount) || amount <= 0) {
					return { lines: [], error: "Non inventory amount must be greater than 0." };
				}

				lines.push({ type, description, amount: Number(amount.toFixed(2)) });
			}

			return { lines, error: null };
		}

		function hideDropdown() {
			dropdown.style.display = "none";
			dropdown.innerHTML = "";
		}

		function hideVendorDropdown() {
			vendorDropdown.style.display = "none";
			vendorDropdown.innerHTML = "";
		}

		function formatMoney(value) {
			const x = Number(value || 0);
			return `$${Number.isFinite(x) ? x.toFixed(2) : "0.00"}`;
		}

		function setDateInputLocked(input, isLocked) {
			if (!input) return;
			const locked = !!isLocked;

			input.disabled = locked;
			input.readOnly = locked;

			const fp = input._flatpickr;
			if (!fp) return;

			if (fp.input) {
				fp.input.disabled = locked;
				fp.input.readOnly = locked;
			}
			if (fp.altInput) {
				fp.altInput.disabled = locked;
				fp.altInput.readOnly = locked;
			}

			fp.set("clickOpens", !locked);
			fp.set("allowInput", !locked);
		}

		function getBootstrapModalClass() {
			if (window.bootstrap && window.bootstrap.Modal) return window.bootstrap.Modal;
			if (typeof bootstrap !== "undefined" && bootstrap.Modal) return bootstrap.Modal;
			return null;
		}

		const partsOrderPaymentModalEl = document.getElementById("partsOrderPaymentModal");
		const partsOrderPaymentOrderIdInput = document.getElementById("partsOrderPaymentOrderId");
		const partsOrderPaymentOrderMeta = document.getElementById("partsOrderPaymentOrderMeta");
		const partsOrderPaymentInvoiceTotal = document.getElementById("partsOrderPaymentInvoiceTotal");
		const partsOrderPaymentAlreadyPaid = document.getElementById("partsOrderPaymentAlreadyPaid");
		const partsOrderPaymentRemainingBalance = document.getElementById("partsOrderPaymentRemainingBalance");
		const partsOrderPaymentAmountInput = document.getElementById("partsOrderPaymentAmountInput");
		const partsOrderPaymentMethodInput = document.getElementById("partsOrderPaymentMethodInput");
		const partsOrderPaymentDateInput = document.getElementById("partsOrderPaymentDateInput");
		const partsOrderPaymentNotesInput = document.getElementById("partsOrderPaymentNotesInput");
		const partsOrderPaymentSubmitBtn = document.getElementById("partsOrderPaymentSubmitBtn");

		function formatDateTime(v) {
			if (!v) return "-";
			const raw = String(v).trim();
			const dateOnly = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
			if (dateOnly) {
				return `${dateOnly[2]}/${dateOnly[3]}/${dateOnly[1]}`;
			}
			const d = new Date(raw);
			if (Number.isNaN(d.getTime())) return "-";
			return new Intl.DateTimeFormat("en-US", {
				timeZone: APP_TIMEZONE,
				month: "2-digit",
				day: "2-digit",
				year: "numeric",
			}).format(d);
		}

		async function renderOrderTimeline(orderId, orderData) {
			if (!orderDatesBlock || !orderMetaPaymentsBody || !orderId) return;

			let summary = null;
			try {
				summary = await loadPartsOrderPaymentSummary(orderId);
			} catch (err) {
				orderDatesBlock.classList.remove("d-none");
				orderMetaPaymentsBody.innerHTML = `<tr><td colspan="5" class="text-danger">Failed to load payment data.</td></tr>`;
				return;
			}

			orderDatesBlock.classList.remove("d-none");
			if (orderMetaCreated) {
				orderMetaCreated.textContent = formatDateTime((orderData && orderData.created_at) || summary.created_at);
			}
			if (orderMetaReceived) {
				orderMetaReceived.textContent = formatDateTime((orderData && orderData.received_at) || summary.received_at);
			}
			if (payOrderModalBtn) {
				const isPaid = String(summary.payment_status || "").toLowerCase() === "paid"
					|| Number(summary.remaining_balance || 0) <= 0.01;
				payOrderModalBtn.style.display = (!isPaid && orderId) ? "inline-flex" : "none";
			}

			const payments = Array.isArray(summary.payments) ? summary.payments : [];
			if (payments.length === 0) {
				orderMetaPaymentsBody.innerHTML = `<tr><td colspan="5" class="text-muted">No payments.</td></tr>`;
				return;
			}

			orderMetaPaymentsBody.innerHTML = payments.map((p) => {
				const pid = String(p.id || "");
				const dateLabel = formatDateTime(p.payment_date || p.created_at);
				const amount = Number(p.amount || 0);
				const method = String(p.payment_method || "cash");
				const notes = String(p.notes || "");
				return `
					<tr>
						<td>${escapeHtml(dateLabel)}</td>
						<td class="text-end fw-semibold">$${Number.isFinite(amount) ? amount.toFixed(2) : "0.00"}</td>
						<td>${escapeHtml(method)}</td>
						<td>${notes ? escapeHtml(notes) : "-"}</td>
						<td class="text-end"><button type="button" class="btn btn-sm btn-outline-danger js-delete-order-payment-inline" data-order-id="${escapeHtml(orderId)}" data-payment-id="${escapeHtml(pid)}">Delete</button></td>
					</tr>
				`;
			}).join("");
		}

		function isPartsPageAlive() {
			const partsPageRootMarker = document.getElementById("orderModal") || document.getElementById("createPartModal");
			return !!(partsPageRootMarker && document.body && document.body.contains(partsPageRootMarker));
		}

		async function loadPartsOrderPaymentSummary(orderId) {
			const res = await fetch(`/parts/api/orders/${encodeURIComponent(orderId)}/payments`, {
				method: "GET",
				headers: { "Accept": "application/json" },
			});
			const data = await res.json();
			if (!res.ok || !data || !data.ok) {
				throw new Error((data && (data.error || data.message)) || "Failed to load payment summary");
			}
			return data;
		}

		async function openPartsOrderPaymentModal(orderId) {
			if (!orderId) return;

			const data = await loadPartsOrderPaymentSummary(orderId);
			partsOrderPaymentOrderIdInput.value = orderId;
			partsOrderPaymentOrderMeta.textContent = `Order #${data.order_number || "-"}`;
			partsOrderPaymentInvoiceTotal.textContent = formatMoney(data.grand_total || 0);
			partsOrderPaymentAlreadyPaid.textContent = formatMoney(data.paid_amount || 0);
			partsOrderPaymentRemainingBalance.textContent = formatMoney(data.remaining_balance || 0);
			partsOrderPaymentAmountInput.value = (Number(data.remaining_balance || 0) > 0)
				? Number(data.remaining_balance).toFixed(2)
				: "";
			partsOrderPaymentMethodInput.value = "cash";
			const isReceivedOrder = String(data.order_status || "").toLowerCase() === "received";
			if (partsOrderPaymentDateInput) {
				partsOrderPaymentDateInput.value = partsOrderPaymentDateInput.defaultValue || partsOrderPaymentDateInput.value || "";
				if (partsOrderPaymentDateInput._flatpickr) { partsOrderPaymentDateInput._flatpickr.setDate(partsOrderPaymentDateInput.value || null, false, "Y-m-d"); }
				setDateInputLocked(partsOrderPaymentDateInput, isReceivedOrder);
			}
			partsOrderPaymentNotesInput.value = "";

			if (!partsOrderPaymentModalEl) return;
			const modal = bootstrap.Modal.getOrCreateInstance(partsOrderPaymentModalEl);
			modal.show();
		}

		async function loadOrderIntoModal(orderId) {
			if (!orderId) return;

			const res = await fetch(`/parts/api/orders/${encodeURIComponent(orderId)}`, {
				method: "GET",
				headers: { "Accept": "application/json" },
			});
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
			applyOrderToModal(order, orderId);
		}

		async function openOrderEditModal(orderId) {
			if (!orderId) return;
			clearError();
			await loadOrderIntoModal(orderId);
			const modalEl = document.getElementById("orderModal");
			const ModalClass = getBootstrapModalClass();
			if (modalEl && ModalClass) {
				ModalClass.getOrCreateInstance(modalEl).show();
			}
		}

		function applyOrderToModal(order, orderId) {
			if (!order || typeof order !== "object") return;
			currentOrderStatus = String(order.status || "").trim().toLowerCase();
			currentVendorBill = String(order.vendor_bill || "").trim();
			const isReceived = currentOrderStatus === "received";
			if (receiveOrderModalBtn) receiveOrderModalBtn.style.display = isReceived ? "none" : "block";
			if (unreceiveOrderModalBtn) unreceiveOrderModalBtn.style.display = isReceived ? "block" : "none";
			if (payOrderModalBtn) {
				const paymentSummary = (order && typeof order.payment_summary === "object") ? order.payment_summary : null;
				const paymentStatus = String(
					(paymentSummary && paymentSummary.payment_status)
					|| order.payment_status
					|| ""
				).toLowerCase();
				const remaining = Number((paymentSummary && paymentSummary.remaining_balance) || order.remaining_balance || 0);
				const isPaid = paymentStatus === "paid" || remaining <= 0.01;
				payOrderModalBtn.style.display = (!isPaid && orderId) ? "inline-flex" : "none";
			}

			if (isReceived) {
				vendorSelect.disabled = true;
				vendorSearchInput.disabled = true;
				partSearch.disabled = true;
				createOrderBtn.disabled = true;
			} else {
				vendorSelect.disabled = false;
				vendorSearchInput.disabled = false;
				partSearch.disabled = !vendorSelect.value;
				createOrderBtn.disabled = false;
			}

			vendorSelect.value = order.vendor_id || "";
			syncVendorSearchFromSelect();
			orderItems = [];
			const rawItems = Array.isArray(order.items)
				? order.items
				: (Array.isArray(order.parts) ? order.parts : []);
			if (rawItems.length > 0) {
				orderItems = rawItems.map(item => ({
					part_id: item.part_id,
					part_number: item.part_number,
					description: item.description,
					quantity: item.quantity ?? item.qty ?? 0,
					price: item.price ?? item.cost ?? 0,
					core_has_charge: item.core_has_charge || false,
					core_cost: item.core_cost || 0
				}));
			}

			const nonInventoryLines = Array.isArray(order.non_inventory_amounts)
				? order.non_inventory_amounts
				: [];
			renderOrderItems();
			renderNonInventoryRows(nonInventoryLines, isReceived);
			if (orderDateInput) {
				orderDateInput.value = String(order.order_date || orderDateInput.defaultValue || "").trim();
				if (orderDateInput._flatpickr) { orderDateInput._flatpickr.setDate(orderDateInput.value || null, false, "Y-m-d"); }
				setDateInputLocked(orderDateInput, isReceived);
			}
			createdOrderId.value = orderId;
			orderCreatedBox.classList.add("d-none");
			createOrderBtn.textContent = "Save";
			renderOrderTimeline(orderId, order);
		}

		function parseJsonAttr(raw) {
			if (!raw) return null;
			const decodeHtml = function (s) {
				const el = document.createElement("textarea");
				el.innerHTML = String(s || "");
				return el.value;
			};
			try {
				return JSON.parse(raw);
			} catch (e) {
				try {
					const d1 = decodeHtml(raw);
					return JSON.parse(d1);
				} catch (e2) {
					try {
						const d2 = decodeHtml(decodeHtml(raw));
						return JSON.parse(d2);
					} catch (e3) {
						return null;
					}
				}
			}
		}

		document.addEventListener("click", async function (e) {
			if (!isPartsPageAlive()) return;
			const inlineDeleteBtn = e.target.closest(".js-delete-order-payment-inline");
			if (inlineDeleteBtn) {
				const paymentId = String(inlineDeleteBtn.getAttribute("data-payment-id") || "").trim();
				const orderId = String(inlineDeleteBtn.getAttribute("data-order-id") || "").trim();
				if (!paymentId || !orderId) return;
				if (!await appConfirm("Delete this payment?")) return; // inline delete

				const originalText = inlineDeleteBtn.textContent;
				inlineDeleteBtn.disabled = true;
				inlineDeleteBtn.textContent = "Deleting...";

				try {
					const res = await fetch(`/parts/api/payments/${encodeURIComponent(paymentId)}/delete`, {
						method: "POST",
						headers: { "Accept": "application/json" },
					});
					const data = await res.json();
					if (!res.ok || !data || !data.ok) {
						throw new Error((data && (data.message || data.error)) || "Failed to delete payment");
					}
					await loadOrderIntoModal(orderId);
				} catch (err) {
					appAlert(err.message || "Failed to delete payment", 'error');
					inlineDeleteBtn.disabled = false;
					inlineDeleteBtn.textContent = originalText;
				}
				return;
			}

			const btn = e.target.closest(".js-order-payment");
			if (!btn) return;

			const rowPaymentStatus = String(btn.getAttribute("data-payment-status") || "").trim().toLowerCase();
			if (rowPaymentStatus === "paid") return;

			const orderId = String(btn.getAttribute("data-order-id") || "").trim();
			if (!orderId) return;

			try {
				await openPartsOrderPaymentModal(orderId);
			} catch (err) {
				appAlert(err.message || "Failed to open payment modal", 'error');
			}
		});

		payOrderModalBtn?.addEventListener("click", async function () {
			const orderId = String(createdOrderId?.value || "").trim();
			if (!orderId) {
				appAlert("Create order first.", 'warning');
				return;
			}

			try {
				await openPartsOrderPaymentModal(orderId);
			} catch (err) {
				appAlert(err.message || "Failed to open payment modal", 'error');
			}
		});

		partsOrderPaymentSubmitBtn?.addEventListener("click", async function () {
			const orderId = String(partsOrderPaymentOrderIdInput?.value || "").trim();
			if (!orderId) return;

			const amount = parseFloat(partsOrderPaymentAmountInput?.value || "0");
			if (!(amount > 0)) {
				appAlert("Enter valid payment amount.", 'warning');
				return;
			}

			const method = String(partsOrderPaymentMethodInput?.value || "cash").trim() || "cash";
			const paymentDate = String(partsOrderPaymentDateInput?.value || "").trim();
			const notes = String(partsOrderPaymentNotesInput?.value || "").trim();

			if (!paymentDate) {
				appAlert("Select payment date.", 'warning');
				return;
			}

			const originalText = partsOrderPaymentSubmitBtn.textContent;
			partsOrderPaymentSubmitBtn.disabled = true;
			partsOrderPaymentSubmitBtn.textContent = "Saving...";

			try {
				const res = await fetch(`/parts/api/orders/${encodeURIComponent(orderId)}/payment`, {
					method: "POST",
					headers: { "Content-Type": "application/json", "Accept": "application/json" },
					body: JSON.stringify({ amount, payment_method: method, payment_date: paymentDate, notes }),
				});
				const data = await res.json();
				if (!res.ok || !data || !data.ok) {
					throw new Error((data && (data.message || data.error)) || "Failed to save payment");
				}

				const openedOverOrderModal = !!(orderModal && orderModal.classList.contains("show"));
				const modal = bootstrap.Modal.getInstance(partsOrderPaymentModalEl);
				if (modal) modal.hide();

				if (openedOverOrderModal) {
					await loadOrderIntoModal(orderId);
				} else {
					location.reload();
				}
			} catch (err) {
				appAlert(err.message || "Failed to save payment", 'error');
			} finally {
				partsOrderPaymentSubmitBtn.disabled = false;
				partsOrderPaymentSubmitBtn.textContent = originalText;
			}
		});

		document.addEventListener("click", async function (e) {
			if (!isPartsPageAlive()) return;

			const btn = e.target.closest(".js-delete-parts-payment");
			if (!btn) return;

			const paymentId = String(btn.getAttribute("data-payment-id") || "").trim();
			if (!paymentId) return;

			if (!await appConfirm("Delete this payment?")) return;

			const originalText = btn.textContent;
			btn.disabled = true;
			btn.textContent = "Deleting...";

			try {
				const res = await fetch(`/parts/api/payments/${encodeURIComponent(paymentId)}/delete`, {
					method: "POST",
					headers: { "Accept": "application/json" },
				});
				const data = await res.json();
				if (!res.ok || !data || !data.ok) {
					throw new Error((data && (data.message || data.error)) || "Failed to delete payment");
				}

				location.reload();
			} catch (err) {
				appAlert(err.message || "Failed to delete payment", 'error');
				btn.disabled = false;
				btn.textContent = originalText;
			}
		});

		function showVendorDropdown() {
			vendorDropdown.style.display = "block";
		}

		function getVendorLabelById(vendorId) {
			const option = vendorSelect.querySelector(`option[value="${vendorId}"]`);
			if (!option) return "";
			return String(option.textContent || "").trim();
		}

		function syncVendorSearchFromSelect() {
			const vendorId = String(vendorSelect.value || "").trim();
			if (!vendorId) {
				vendorSearchInput.value = "";
				vendorSearchInput.placeholder = "Search vendor...";
				return;
			}
			vendorSearchInput.value = getVendorLabelById(vendorId);
		}

		function renderVendorDropdown(filterText) {
			const q = String(filterText || "").trim().toLowerCase();
			const visible = q
				? vendorOptions.filter((v) => v.label.toLowerCase().includes(q))
				: vendorOptions;

			vendorDropdown.innerHTML = "";
			if (visible.length === 0) {
				vendorDropdown.innerHTML = `<div class="list-group-item text-muted">No vendors found</div>`;
				showVendorDropdown();
				return;
			}

			visible.forEach((vendor) => {
				const btn = document.createElement("button");
				btn.type = "button";
				btn.className = "list-group-item list-group-item-action";
				btn.textContent = vendor.label;
				btn.addEventListener("click", function () {
					vendorSelect.value = vendor.id;
					vendorSelect.dispatchEvent(new Event("change", { bubbles: true }));
					syncVendorSearchFromSelect();
					hideVendorDropdown();
				});
				vendorDropdown.appendChild(btn);
			});

			showVendorDropdown();
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

			// Prevent adding items if order is received
			if (vendorSelect.disabled) {
				return;
			}

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
			const isReceived = currentOrderStatus === "received";
			
			orderItems.forEach(item => {
				const coreIndicator = item.core_has_charge && item.core_cost > 0
					? `<span class="badge bg-warning text-dark ms-1" title="Core charge: $${Number(item.core_cost).toFixed(2)} per unit">Core</span>`
					: '';
				
				const tr = document.createElement("tr");
				tr.setAttribute("data-part-id", item.part_id);
				tr.setAttribute("data-core-has-charge", item.core_has_charge ? "true" : "false");
				tr.setAttribute("data-core-cost", item.core_cost || "0");
				
				const removeBtn = isReceived
					? `<button type="button" class="btn btn-sm btn-outline-danger remove-item-btn" disabled title="Unreceive order to delete items">Remove</button>`
					: `<button type="button" class="btn btn-sm btn-outline-danger remove-item-btn">Remove</button>`;
				
				tr.innerHTML = `
					<td class="fw-semibold">${escapeHtml(item.part_number)}${coreIndicator}</td>
					<td class="text-muted">${escapeHtml(item.description) || "-"}</td>
					<td class="text-end">
						<input class="form-control form-control-sm text-end qty-input" type="number" min="1" step="1" value="${item.quantity}" required ${isReceived ? 'disabled' : ''}>
					</td>
					<td class="text-end">
						<input class="form-control form-control-sm text-end price-input" type="number" min="0" step="0.01" value="${item.price}" required ${isReceived ? 'disabled' : ''}>
					</td>
					<td class="text-end">
						${removeBtn}
					</td>
				`;
				itemsBody.appendChild(tr);
			});
			calculateOrderTotal();
		}

		vendorSelect.addEventListener("change", function () {
			clearError();
			// Don't allow vendor change if order is received
			if (vendorSelect.disabled) {
				return;
			}
			syncVendorSearchFromSelect();
			partSearch.disabled = !vendorSelect.value;
			partSearch.value = "";
			hideDropdown();
			if (vendorSelect.value) partSearch.focus();
		});

		vendorSearchInput.addEventListener("focus", function () {
			if (vendorSearchInput.disabled) return;
			renderVendorDropdown(vendorSearchInput.value);
		});

		vendorSearchInput.addEventListener("input", function () {
			if (vendorSearchInput.disabled) return;
			renderVendorDropdown(vendorSearchInput.value);
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
			if (!isPartsPageAlive()) return;
			if (!dropdown.contains(e.target) && e.target !== partSearch) hideDropdown();
			if (!vendorDropdown.contains(e.target) && e.target !== vendorSearchInput) hideVendorDropdown();
		});

		itemsBody.addEventListener("click", function (e) {
			const btn = e.target.closest(".remove-item-btn");
			if (!btn) return;
			
			// Prevent deletion if received
			if (btn.disabled || vendorSelect.disabled) {
				return;
			}
			
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

		nonInventoryBody.addEventListener("input", function (e) {
			if (e.target.classList.contains("non-inv-desc") || e.target.classList.contains("non-inv-amount")) {
				ensureTrailingNonInventoryRow(vendorSelect.disabled);
				calculateOrderTotal();
			}
		});

		nonInventoryBody.addEventListener("click", function (e) {
			const btn = e.target.closest(".non-inv-remove-btn");
			if (!btn) return;
			if (btn.disabled || vendorSelect.disabled) return;

			const tr = btn.closest("tr");
			if (tr) tr.remove();
			ensureTrailingNonInventoryRow(false);
			calculateOrderTotal();
		});

		async function createOrderAjax() {
			clearError();

			const vendorId = vendorSelect.value || "";
			const orderDate = String(orderDateInput?.value || "").trim();
			if (!vendorId) { showError("Select vendor."); return; }
			if (!orderDate) { showError("Select order date."); return; }

			const rows = Array.from(itemsBody.querySelectorAll("tr[data-part-id]"));

			const nonInventoryPayload = collectNonInventoryAmounts();
			if (nonInventoryPayload.error) { showError(nonInventoryPayload.error); return; }

			if (rows.length === 0 && nonInventoryPayload.lines.length === 0) {
				showError("Add at least one item or non inventory amount.");
				return;
			}

			// Check if order is received
			if (vendorSelect.disabled) {
				showError("Cannot update received orders. Click 'Unreceive' first.");
				return;
			}

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
					body: JSON.stringify({ vendor_id: vendorId, order_date: orderDate, items, non_inventory_amounts: nonInventoryPayload.lines }),
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

				Array.from(nonInventoryBody.querySelectorAll("input,button")).forEach((el) => {
					el.disabled = true;
				});

			} catch (e) {
				showError("Network error while " + (createdOrderId.value ? "updating" : "creating") + " order.");
				createOrderBtn.disabled = false;
			}
		}

		async function askVendorBill(defaultValue) {
			if (typeof Swal === 'undefined') {
				const value = window.prompt("Vendor Bill (invoice number). Leave blank if none.", String(defaultValue || "").trim());
				if (value === null) return null;
				return String(value || "").trim();
			}
			const noAnim = { popup: '', backdrop: '' };
			const result = await Swal.fire({
				title: 'Vendor Bill',
				text: 'Enter invoice number from the vendor (leave blank if none).',
				input: 'text',
				inputValue: String(defaultValue || "").trim(),
				inputPlaceholder: 'e.g. INV-12345',
				showCancelButton: true,
				confirmButtonText: 'Receive Order',
				cancelButtonText: 'Cancel',
				confirmButtonColor: '#1f6b43',
				cancelButtonColor: '#6c757d',
				showClass: noAnim,
				hideClass: noAnim,
			});
			if (!result.isConfirmed) return null;
			return String(result.value || "").trim();
		}

		async function receiveOrderWithVendorBill(orderId, vendorBill) {
			const res = await fetch(`/parts/api/orders/${encodeURIComponent(orderId)}/receive`, {
				method: "POST",
				headers: { "Content-Type": "application/json", "Accept": "application/json" },
				body: JSON.stringify({ vendor_bill: String(vendorBill || "").trim() }),
			});
			const data = await res.json();
			if (!res.ok || !data || !data.ok) {
				throw new Error((data && (data.error || data.message)) || "Receive failed.");
			}
			return data;
		}

		async function receiveOrderAjax() {
			clearError();

			const oid = createdOrderId.value || "";
			if (!oid) { showError("Order id missing."); return; }
			const vendorBill = await askVendorBill(currentVendorBill);
			if (vendorBill === null) return;

			receiveBtn.disabled = true;

			try {
				await receiveOrderWithVendorBill(oid, vendorBill);
				currentVendorBill = vendorBill;

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
		if (receiveBtn) {
			receiveBtn.addEventListener("click", receiveOrderAjax);
		}

		// Receive order button in modal
		if (receiveOrderModalBtn) {
			receiveOrderModalBtn.addEventListener("click", async function () {
				const orderId = createdOrderId.value;
				if (!orderId) return;
				const vendorBill = await askVendorBill(currentVendorBill);
				if (vendorBill === null) return;

				try {
					const data = await receiveOrderWithVendorBill(orderId, vendorBill);
					currentVendorBill = vendorBill;
					appAlert(`Order received! ${data.updated_parts} parts updated.`, 'success');
					const modalEl = document.getElementById("orderModal");
					const modal = window.bootstrap?.Modal?.getInstance(modalEl);
					if (modal) modal.hide();
					location.reload();
				} catch (err) {
					appAlert(err.message || "Network error while receiving order", 'error');
				}
			});
		}

		// Unreceive order button in modal
		if (unreceiveOrderModalBtn) {
			unreceiveOrderModalBtn.addEventListener("click", async function () {
				const orderId = createdOrderId.value;
				if (!orderId) return;

				if (await appConfirm("Unreceive this order? Items will be removed from inventory.")) {
					try {
						const res = await fetch(`/parts/api/orders/${encodeURIComponent(orderId)}/unreceive`, {
							method: "POST"
						});
						const data = await res.json();

						if (data.ok) {
							appAlert(`Order unreceived! ${data.updated_parts} parts removed from inventory.`, 'success');
							const modalEl = document.getElementById("orderModal");
							const modal = window.bootstrap?.Modal?.getInstance(modalEl);
							if (modal) modal.hide();
							location.reload();
						} else {
							appAlert("Error: " + (data.error || "Failed to unreceive order"), 'error');
						}
					} catch (err) {
						appAlert("Network error while unreceiving order", 'error');
					}
				}
			});
		}

		// Reset form when modal is closed
		orderModal?.addEventListener("hidden.bs.modal", function () {
			vendorSelect.value = "";
			vendorSelect.disabled = false;
			vendorSearchInput.value = "";
			vendorSearchInput.disabled = false;
			if (orderDateInput) {
				orderDateInput.value = orderDateInput.defaultValue || "";
				setDateInputLocked(orderDateInput, false);
			}
			hideVendorDropdown();
			partSearch.value = "";
			partSearch.disabled = true;
			dropdown.style.display = "none";
			orderItems = [];
			renderOrderItems();
			renderNonInventoryRows([], false);
			createdOrderId.value = "";
			orderCreatedBox.classList.add("d-none");
			orderAlert.classList.add("d-none");
			createOrderBtn.textContent = "Create order";
			createOrderBtn.disabled = false;
			if (receiveOrderModalBtn) receiveOrderModalBtn.style.display = "none";
			if (unreceiveOrderModalBtn) unreceiveOrderModalBtn.style.display = "none";
			if (payOrderModalBtn) payOrderModalBtn.style.display = "none";
			currentVendorBill = "";
			if (receiveBtn) receiveBtn.disabled = false;
			if (orderDatesBlock) orderDatesBlock.classList.add("d-none");
			if (orderMetaCreated) orderMetaCreated.textContent = "-";
			if (orderMetaReceived) orderMetaReceived.textContent = "-";
			if (orderMetaPaymentsBody) orderMetaPaymentsBody.innerHTML = `<tr><td colspan="5" class="text-muted">No payments.</td></tr>`;
		});

		partsOrderPaymentModalEl?.addEventListener("shown.bs.modal", function () {
			if (!(orderModal && orderModal.classList.contains("show"))) return;

			const backdrops = document.querySelectorAll(".modal-backdrop.show");
			if (backdrops.length > 0) {
				const topBackdrop = backdrops[backdrops.length - 1];
				topBackdrop.style.zIndex = "1080";
			}
			if (partsOrderPaymentModalEl) {
				partsOrderPaymentModalEl.style.zIndex = "1090";
			}
		});

		partsOrderPaymentModalEl?.addEventListener("hidden.bs.modal", function () {
			if (partsOrderPaymentModalEl) {
				partsOrderPaymentModalEl.style.zIndex = "";
			}
			if (orderModal && orderModal.classList.contains("show")) {
				document.body.classList.add("modal-open");
			}
		});

		const partHistoryModal = document.getElementById("partHistoryModal");
		const partHistoryMeta = document.getElementById("partHistoryMeta");
		const partHistoryOrdersBody = document.getElementById("partHistoryOrdersBody");
		const partHistoryWorkOrdersBody = document.getElementById("partHistoryWorkOrdersBody");

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
					<tr style="cursor: pointer;" class="workOrderHistoryRow" data-wo-id="${row.work_order_id}">
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
			if (!isPartsPageAlive()) return;
			const btn = e.target.closest(".partHistoryBtn");
			if (!btn) return;
			const partId = btn.getAttribute("data-part-id");
			if (!partId) return;
			loadPartHistory(partId);
		});

		document.addEventListener("click", function (e) {
			if (!isPartsPageAlive()) return;
			const woRow = e.target.closest(".workOrderHistoryRow");
			if (!woRow) return;
			const woId = woRow.getAttribute("data-wo-id");
			if (!woId) return;
			window.open(`/work_orders/details?work_order_id=${woId}`, "_blank");
		});

		partHistoryModal?.addEventListener("hidden.bs.modal", function () {
			if (partHistoryMeta) partHistoryMeta.textContent = "Loading...";
			if (partHistoryOrdersBody) partHistoryOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">No data.</td></tr>`;
			if (partHistoryWorkOrdersBody) partHistoryWorkOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">No data.</td></tr>`;
		});

		partHistoryModal?.addEventListener("show.bs.modal", function (e) {
			const trigger = e.relatedTarget;
			const partId = String(trigger?.getAttribute("data-part-id") || "").trim();
			if (!partId) return;
			loadPartHistory(partId);
		});

		orderModal?.addEventListener("show.bs.modal", function (e) {
			if (!isPartsPageAlive()) return;
			const trigger = e.relatedTarget;
			const editBtn = trigger ? trigger.closest(".editOrderBtn") : null;
			if (!editBtn) return;
			const orderId = String(editBtn.getAttribute("data-order-id") || "").trim();
			if (!orderId) return;
			const inlineOrder = parseJsonAttr(editBtn.getAttribute("data-order-json"));
			if (inlineOrder && typeof inlineOrder === "object") {
				applyOrderToModal(inlineOrder, orderId);
				return;
			}
			loadOrderIntoModal(orderId).catch(function () {
				showError("Network error while loading order");
			});
		});

		// ---- Order List Management (Receive from Status, Delete from Orders tab) ----
		document.addEventListener("click", async function (e) {
			if (!isPartsPageAlive()) return;
			// Receive order by clicking on status button
			const receiveStatusBtn = e.target.closest(".receiveStatusBtn");
			if (receiveStatusBtn) {
				const orderId = receiveStatusBtn.getAttribute("data-order-id");
				if (!orderId) return;

				const vendorBillDefault = String(receiveStatusBtn.getAttribute("data-vendor-bill") || "").trim();
				const vendorBill = await askVendorBill(vendorBillDefault);
				if (vendorBill === null) return;

				try {
					const data = await receiveOrderWithVendorBill(orderId, vendorBill);
					appAlert(`Order received! ${data.updated_parts} parts updated.`, 'success');
					location.reload();
				} catch (err) {
					appAlert(err.message || "Network error while receiving order", 'error');
				}
				return;
			}

			// Delete order button
			const deleteBtn = e.target.closest(".deleteOrderBtn");
			if (deleteBtn) {
				const orderId = deleteBtn.getAttribute("data-order-id");
				if (!orderId) return;

				if (await appConfirm("Delete this order? If received, items will be removed from inventory.")) {
					try {
						const res = await fetch(`/parts/api/orders/${encodeURIComponent(orderId)}`, {
							method: "DELETE"
						});
						const data = await res.json();

						if (data.ok) {
							appAlert("Order deleted successfully", 'success');
							location.reload();
						} else {
							appAlert("Error: " + (data.error || "Failed to delete order"), 'error');
						}
					} catch (err) {
						appAlert("Network error while deleting order", 'error');
					}
				}
				return;
			}
		});
			} catch (err) {
				console.error("Parts order composer init failed:", err);
			}
		}

		// Auto-open order modal if ?open_order=<id> is in URL
		(function checkUrlOpenOrder() {
			var params = new URLSearchParams(window.location.search);
			var openOrderId = (params.get("open_order") || "").trim();
			if (!openOrderId) return;
			// Clean URL without reloading
			params.delete("open_order");
			var cleanUrl = window.location.pathname + (params.toString() ? "?" + params.toString() : "") + window.location.hash;
			window.history.replaceState({}, "", cleanUrl);
			// Create a virtual trigger button that mimics an editOrderBtn
			var modalEl = document.getElementById("orderModal");
			if (!modalEl) return;
			var virtualBtn = document.createElement("button");
			virtualBtn.classList.add("editOrderBtn");
			virtualBtn.setAttribute("data-order-id", openOrderId);
			var ModalClass = (window.bootstrap && window.bootstrap.Modal) ? window.bootstrap.Modal : null;
			if (ModalClass) {
				ModalClass.getOrCreateInstance(modalEl).show(virtualBtn);
			}
		})();

	// ---- Edit Part Modal Logic ----
	const createPartModal = document.getElementById('createPartModal');
	if (createPartModal) {
		const editingPartId = document.getElementById('editingPartId');
		const modalTitle = document.getElementById('createPartModalLabel');
		const partNumberInput = createPartModal.querySelector('input[name="part_number"]');
		const descriptionInput = createPartModal.querySelector('input[name="description"]');
		const referenceInput = createPartModal.querySelector('input[name="reference"]');
		const vendorSelectParts = createPartModal.querySelector('select[name="vendor_id"]');
		const categorySelect = createPartModal.querySelector('select[name="category_id"]');
		const locationSelect = createPartModal.querySelector('select[name="location_id"]');
		const inStockGroup = document.getElementById('inStockGroup');
		const inStockInput = createPartModal.querySelector('input[name="in_stock"]');
		const averageCostInput = createPartModal.querySelector('input[name="average_cost"]');
		const doNotTrackInventoryCheckbox = document.getElementById('doNotTrackInventoryToggle');
		const coreCheckbox = document.getElementById('coreChargeToggle');
		const coreCostInput = createPartModal.querySelector('input[name="core_cost"]');
		const coreCostGroup = document.getElementById('coreCostGroup');
		const miscCheckbox = document.getElementById('miscChargeToggle');
		const miscBodyParts = document.getElementById('miscChargesBody');
		const sellingPriceCheckbox = document.getElementById('sellingPriceToggle');
		const sellingPriceInput = document.getElementById('sellingPriceInput');
		const sellingPriceGroup = document.getElementById('sellingPriceGroup');
		const form = createPartModal.querySelector('form[action*="/parts/create"]');
		function parsePartJsonAttr(raw) {
			if (!raw) return null;
			const decodeHtml = function (s) {
				const el = document.createElement('textarea');
				el.innerHTML = String(s || '');
				return el.value;
			};
			try {
				return JSON.parse(raw);
			} catch (e) {
				try {
					return JSON.parse(decodeHtml(raw));
				} catch (e2) {
					try {
						return JSON.parse(decodeHtml(decodeHtml(raw)));
					} catch (e3) {
						return null;
					}
				}
			}
		}
		function boolFromAttr(v) {
			return String(v || '').trim() === '1' || String(v || '').trim().toLowerCase() === 'true';
		}

		function applyPartToModal(item, partId) {
			if (!item || typeof item !== 'object') return;
			editingPartId.value = partId || '';
			modalTitle.textContent = 'Edit part: ' + (item.part_number || '');

			partNumberInput.value = item.part_number || '';
			descriptionInput.value = item.description || '';
			referenceInput.value = item.reference || '';
			vendorSelectParts.value = item.vendor_id || '';
			categorySelect.value = item.category_id || '';
			locationSelect.value = item.location_id || '';
			inStockInput.value = item.in_stock ?? 0;
			averageCostInput.value = item.average_cost ?? 0;

			if (doNotTrackInventoryCheckbox) {
				doNotTrackInventoryCheckbox.checked = !!item.do_not_track_inventory;
			}
			syncInStockVisibility();

			if (coreCheckbox) coreCheckbox.checked = !!item.core_has_charge;
			if (coreCostInput) coreCostInput.value = item.core_cost ?? 0;
			syncInStockVisibility();

			miscCheckbox.checked = !!item.misc_has_charge;
			if (miscBodyParts) {
				miscBodyParts.innerHTML = '';
				if (item.misc_charges && item.misc_charges.length > 0) {
					item.misc_charges.forEach((charge, idx) => {
						miscBodyParts.appendChild(buildMiscRow(idx, charge.description, charge.price, charge.taxable));
					});
				}
			}

			document.getElementById('miscChargesGroup').style.display = item.misc_has_charge ? '' : 'none';

			if (sellingPriceCheckbox) sellingPriceCheckbox.checked = !!item.has_selling_price;
			if (sellingPriceInput) sellingPriceInput.value = item.selling_price ?? 0;
			syncSellingPriceVisibility();
		}

		async function loadPartIntoEditModal(partId) {
			if (!partId) return;

			const res = await fetch(`/parts/api/${encodeURIComponent(partId)}`, {
				method: 'GET',
				headers: { 'Accept': 'application/json' }
			});
			const data = await res.json();
			if (!res.ok || !data.ok || !data.item) {
				throw new Error((data && (data.error || data.message)) || 'Failed to load part data');
			}

			const item = data.item;
			applyPartToModal(item, partId);
		}

		async function openPartEditModal(partId, inlineData) {
			if (!partId) return;
			if (inlineData && typeof inlineData === 'object') {
				applyPartToModal(inlineData, partId);
			} else {
				await loadPartIntoEditModal(partId);
			}
			const ModalClass = getBootstrapModalClass();
			if (ModalClass) {
				ModalClass.getOrCreateInstance(createPartModal).show();
			}
		}

		function syncSellingPriceVisibility() {
			const show = !!(sellingPriceCheckbox && sellingPriceCheckbox.checked);
			if (sellingPriceGroup) sellingPriceGroup.style.display = show ? '' : 'none';
			if (sellingPriceInput) {
				sellingPriceInput.disabled = !show;
				if (!show) sellingPriceInput.value = '0';
				if (show && String(sellingPriceInput.value || '').trim() === '') sellingPriceInput.value = '0';
			}
		}

		function syncInStockVisibility() {
			const hide = !!(doNotTrackInventoryCheckbox && doNotTrackInventoryCheckbox.checked);
			if (inStockGroup) inStockGroup.style.display = hide ? 'none' : '';
			if (inStockInput) {
				inStockInput.disabled = hide;
				if (hide) inStockInput.value = '';
				if (!hide && String(inStockInput.value || '').trim() === '') inStockInput.value = '0';
			}

			if (coreCheckbox) {
				coreCheckbox.disabled = hide;
				if (hide) coreCheckbox.checked = false;
			}
			if (coreCostInput) {
				coreCostInput.disabled = hide || !(coreCheckbox && coreCheckbox.checked);
				if (hide) coreCostInput.value = '0';
			}
			if (coreCostGroup) {
				coreCostGroup.style.display = hide ? 'none' : ((coreCheckbox && coreCheckbox.checked) ? '' : 'none');
			}
		}

		doNotTrackInventoryCheckbox?.addEventListener('change', syncInStockVisibility);
		coreCheckbox?.addEventListener('change', syncInStockVisibility);
		sellingPriceCheckbox?.addEventListener('change', syncSellingPriceVisibility);

		createPartModal.addEventListener('show.bs.modal', function(e) {
			const trigger = e.relatedTarget;
			const editBtn = trigger ? trigger.closest('.editPartBtn') : null;
			if (!editBtn) return;
			const partId = String(editBtn.getAttribute('data-part-id') || '').trim();
			if (!partId) return;
			const inlineData = parsePartJsonAttr(editBtn.getAttribute('data-part-json'));
			if (inlineData && typeof inlineData === 'object') {
				applyPartToModal(inlineData, partId);
				return;
			}
			loadPartIntoEditModal(partId).catch(err => {
				console.error('Error loading part:', err);
				appAlert('Error loading part data', 'error');
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
						do_not_track_inventory: !!(doNotTrackInventoryCheckbox && doNotTrackInventoryCheckbox.checked),
						has_selling_price: !!(sellingPriceCheckbox && sellingPriceCheckbox.checked),
						selling_price: parseFloat(sellingPriceInput?.value || '0'),
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
							const taxableEl = tr.querySelector('.misc-taxable');
							if (desc && price >= 0) {
								formData.misc_charges.push({ description: desc, price: price, taxable: !!(taxableEl && taxableEl.checked) });
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
							appAlert('Update failed: ' + (result.error || 'Unknown error'), 'error');
						}
					} catch (err) {
						console.error('Error updating part:', err);
						appAlert('Network error while updating part', 'error');
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
			syncInStockVisibility();
			syncSellingPriceVisibility();
			if (coreCostGroup) coreCostGroup.style.display = 'none';
			if (document.getElementById('miscChargesGroup')) {
				document.getElementById('miscChargesGroup').style.display = 'none';
			}
		});

		syncInStockVisibility();
		syncSellingPriceVisibility();
	}
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", initPartsPage, { once: true });
	} else {
		initPartsPage();
	}
})();
