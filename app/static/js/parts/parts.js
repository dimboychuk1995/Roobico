
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
		let scannedInvoiceFile = null;
		let scannedVendorData = null;

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
		let _partsPaymentPendingAttId = "";

		function _genTempId() {
			var h = ''; for (var i = 0; i < 24; i++) h += Math.floor(Math.random() * 16).toString(16); return h;
		}

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
						<td class="text-end">
							<button type="button" class="btn btn-sm btn-outline-secondary js-open-att-modal me-1" data-entity-type="parts_order_payment" data-entity-id="${escapeHtml(pid)}" data-bs-toggle="modal" data-bs-target="#attachmentsModal" title="Attachments"><i class="bi bi-paperclip me-1"></i>Attachments</button>
							<button type="button" class="btn btn-sm btn-outline-danger js-delete-order-payment-inline" data-order-id="${escapeHtml(orderId)}" data-payment-id="${escapeHtml(pid)}">Delete</button>
						</td>
					</tr>
				`;
			}).join("");

			// Re-init sorting for refreshed payments table
			var payTbl = orderMetaPaymentsBody && orderMetaPaymentsBody.closest("table");
			if (payTbl && window.TableSort) window.TableSort.refresh(payTbl);
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

			// Init attachment block with temp ID
			_partsPaymentPendingAttId = _genTempId();
			var attWrap = document.getElementById("partsPaymentAttBlock");
			var attEl = attWrap ? attWrap.querySelector(".attachments-block") : null;
			if (attEl) {
				attEl.dataset.entityId = _partsPaymentPendingAttId;
				if (attEl._attBlock) {
					attEl._attBlock.setEntityId(_partsPaymentPendingAttId);
					attEl._attBlock.items = [];
					attEl._attBlock.render();
				} else if (typeof window.AttachmentsInit === "function") {
					window.AttachmentsInit();
				}
			}

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

			// Show attachments block when editing order
			var attGroup = document.getElementById('orderAttachmentsGroup');
			if (attGroup) {
				if (orderId) {
					attGroup.classList.remove('d-none');
					var attBlock = attGroup.querySelector('.attachments-block');
					if (attBlock) {
						attBlock.setAttribute('data-entity-id', orderId);
						var ctrl = window.AttachmentsGetBlock ? window.AttachmentsGetBlock(attBlock) : null;
						if (ctrl) { ctrl.setEntityId(orderId); ctrl.load(); }
						else if (window.AttachmentsInit) window.AttachmentsInit();
					}
				} else {
					attGroup.classList.add('d-none');
				}
			}
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
					body: JSON.stringify({ amount, payment_method: method, payment_date: paymentDate, notes, pending_attachment_id: _partsPaymentPendingAttId || "" }),
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

					// Auto-attach scanned invoice file
					if (scannedInvoiceFile) {
						const attachFD = new FormData();
						attachFD.append("entity_type", "parts_order");
						attachFD.append("entity_id", data.order_id);
						attachFD.append("files", scannedInvoiceFile);
						fetch("/attachments/api/upload", {
							method: "POST",
							body: attachFD,
						}).catch(() => {});
						scannedInvoiceFile = null;
					}
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
			// Temporarily hide the Bootstrap modal so its focus trap doesn't block SweetAlert input
			const openModal = document.querySelector('.modal.show');
			if (openModal) openModal.style.display = 'none';
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
				confirmButtonColor: '#1a7a42',
				cancelButtonColor: '#6c757d',
				showClass: noAnim,
				hideClass: noAnim,
			});
			if (openModal) openModal.style.display = '';
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

			// Reset scan UI
			const scanProgress = document.getElementById("invoiceScanProgress");
			const scanResult = document.getElementById("invoiceScanResult");
			if (scanProgress) scanProgress.classList.add("d-none");
			if (scanResult) scanResult.classList.add("d-none");
			const fileInput = document.getElementById("invoiceFileInput");
			if (fileInput) fileInput.value = "";
			scannedInvoiceFile = null;
			scannedVendorData = null;
		});

		// ── Invoice AI Scan ──────────────────────────────────────────────────
		const invoiceFileInput = document.getElementById("invoiceFileInput");
		const scanProgress = document.getElementById("invoiceScanProgress");
		const scanResult = document.getElementById("invoiceScanResult");
		const scanDetails = document.getElementById("invoiceScanDetails");
		const dismissScanBtn = document.getElementById("dismissScanResult");

		if (invoiceFileInput) {
			invoiceFileInput.addEventListener("change", async function () {
				const file = this.files?.[0];
				if (!file) return;

				// Show progress
				scanProgress?.classList.remove("d-none");
				scanResult?.classList.add("d-none");
				clearError();

				// Keep file for attachment later
				scannedInvoiceFile = file;

				const formData = new FormData();
				formData.append("invoice", file);

				try {
					const resp = await fetch("/parts/api/orders/parse-invoice", {
						method: "POST",
						body: formData,
					});
					const data = await resp.json();

					scanProgress?.classList.add("d-none");

					if (!data.ok) {
						showError(data.error || "Failed to scan invoice.");
						return;
					}

					// Apply vendor match
					if (data.vendor_match && !vendorSelect.disabled) {
						vendorSelect.value = data.vendor_match.vendor_id;
						vendorSearchInput.value = data.vendor_match.vendor_name;
						vendorSelect.dispatchEvent(new Event("change"));
						partSearch.disabled = false;
					}

					// Save scanned vendor data for modal pre-fill
					scannedVendorData = {
						name: data.vendor_name || "",
						address: data.vendor_address || "",
						phone: data.vendor_phone || "",
						email: data.vendor_email || "",
						website: data.vendor_website || "",
						contact_first_name: data.vendor_contact_first_name || "",
						contact_last_name: data.vendor_contact_last_name || "",
					};

					// Build result summary
					const matched = data.items.filter(i => i.matched_part);
					const unmatched = data.items.filter(i => !i.matched_part);

					let details = "";

					// ── Vendor section ───────────────────────────────────
					if (data.vendor_match) {
						details += `<div class="mb-2"><i class="bi bi-check-circle text-success me-1"></i><strong>Vendor:</strong> ${escapeHtml(data.vendor_match.vendor_name)} — matched</div>`;
					} else {
						details += `<div class="mb-2 p-2 border rounded bg-light">`;
						details += `<div class="d-flex align-items-center gap-2 flex-wrap">`;
						details += `<i class="bi bi-exclamation-triangle text-warning me-1"></i>`;
						details += `<strong>Vendor not found:</strong> ${escapeHtml(data.vendor_name)}`;
						details += `<button type="button" class="btn btn-sm btn-outline-success" id="openScanVendorModalBtn" data-vendor-name="${escapeHtml(data.vendor_name)}"><i class="bi bi-plus-lg me-1"></i>Create Vendor</button>`;
						details += `</div></div>`;
					}

					details += `<div class="mb-2"><strong>Invoice #:</strong> ${escapeHtml(data.invoice_number)} &nbsp; <strong>Date:</strong> ${escapeHtml(data.invoice_date)}</div>`;
					details += `<div class="mb-2"><strong>Items:</strong> ${data.items.length} found — ${matched.length} matched, ${unmatched.length} new</div>`;

					// ── Matched items (editable) ─────────────────────────
					if (matched.length > 0) {
						details += `<div class="mt-2"><strong>Matched parts:</strong></div>`;
						details += `<div class="table-responsive mt-1"><table class="table table-sm table-bordered mb-0"><thead><tr>`;
						details += `<th>Part #</th><th>Description</th><th style="width:80px;">Qty</th><th style="width:110px;">Price</th><th style="width:100px;">Action</th>`;
						details += `</tr></thead><tbody>`;
						for (const item of matched) {
							const mp = item.matched_part;
							details += `<tr data-scan-matched="1" data-scan-part-id="${escapeHtml(mp.part_id)}">
								<td><strong>${escapeHtml(mp.part_number)}</strong></td>
								<td class="text-muted">${escapeHtml(mp.description || item.description)}</td>
								<td><input type="number" class="form-control form-control-sm scan-match-qty" min="1" step="1" value="${item.quantity}"></td>
								<td><input type="number" class="form-control form-control-sm scan-match-price" min="0" step="0.01" value="${Number(item.price).toFixed(2)}"></td>
								<td><button type="button" class="btn btn-sm btn-primary add-scanned-matched-btn"><i class="bi bi-plus-lg me-1"></i>Add</button></td>
							</tr>`;
						}
						details += `</tbody></table></div>`;
						if (matched.length > 1) {
							details += `<div class="mt-1"><button type="button" class="btn btn-sm btn-primary" id="addAllMatchedBtn"><i class="bi bi-plus-lg me-1"></i>Add All Matched</button></div>`;
						}
					}

					// ── Unmatched items (editable) ───────────────────────
					if (unmatched.length > 0) {
						details += `<div class="mt-3"><strong>New parts (not in database):</strong></div>`;
						details += `<div class="table-responsive mt-1"><table class="table table-sm table-bordered mb-0"><thead><tr>`;
						details += `<th style="min-width:140px;">Part #</th><th style="min-width:200px;">Description</th><th style="width:80px;">Qty</th><th style="width:110px;">Price</th><th style="width:130px;">Action</th>`;
						details += `</tr></thead><tbody>`;
						for (const item of unmatched) {
							details += `<tr data-scan-unmatched="1">
								<td><input type="text" class="form-control form-control-sm scan-new-pn" value="${escapeHtml(item.part_number)}" placeholder="Part number"></td>
								<td><input type="text" class="form-control form-control-sm scan-new-desc" value="${escapeHtml(item.description)}" placeholder="Description"></td>
								<td><input type="number" class="form-control form-control-sm scan-new-qty" min="1" step="1" value="${item.quantity}"></td>
								<td><input type="number" class="form-control form-control-sm scan-new-price" min="0" step="0.01" value="${Number(item.price).toFixed(2)}"></td>
								<td><button type="button" class="btn btn-sm btn-outline-primary create-scanned-part-btn"><i class="bi bi-plus-lg me-1"></i>Create & Add</button></td>
							</tr>`;
						}
						details += `</tbody></table></div>`;
						if (unmatched.length > 1) {
							details += `<div class="mt-1"><button type="button" class="btn btn-sm btn-outline-primary" id="createAllUnmatchedBtn"><i class="bi bi-plus-lg me-1"></i>Create & Add All</button></div>`;
						}
					}

					if (scanDetails) scanDetails.innerHTML = details;
					scanResult?.classList.remove("d-none");

				} catch (err) {
					scanProgress?.classList.add("d-none");
					showError("Network error during invoice scan.");
					console.error(err);
				}

				// Reset file input so the same file can be re-selected
				this.value = "";
			});
		}

		dismissScanBtn?.addEventListener("click", () => {
			scanResult?.classList.add("d-none");
		});

		// ── Create Vendor from scan (modal) ──────────────────────────────
		const scanVendorModalEl = document.getElementById("scanVendorModal");
		const scanVendorModal = scanVendorModalEl ? new bootstrap.Modal(scanVendorModalEl) : null;
		const scanVendorNameInput = document.getElementById("scanVendorNameInput");
		const scanVendorWebsite = document.getElementById("scanVendorWebsite");
		const scanVendorAddress = document.getElementById("scanVendorAddress");
		const scanVendorNotesInput = document.getElementById("scanVendorNotesInput");
		const scanVendorContactsForm = document.getElementById("scanVendorContactsForm");
		const scanVendorSubmitBtn = document.getElementById("scanVendorSubmitBtn");
		const scanVendorAlert = document.getElementById("scanVendorAlert");

		// Open vendor modal from scan results
		document.addEventListener("click", function (e) {
			const btn = e.target.closest("#openScanVendorModalBtn");
			if (!btn || !scanVendorModal) return;

			// Pre-fill all fields from scanned data
			const vd = scannedVendorData || {};
			if (scanVendorNameInput) scanVendorNameInput.value = vd.name || btn.dataset.vendorName || "";
			if (scanVendorWebsite) scanVendorWebsite.value = vd.website || "";
			if (scanVendorAddress) scanVendorAddress.value = vd.address || "";
			if (scanVendorNotesInput) scanVendorNotesInput.value = "";
			if (scanVendorAlert) scanVendorAlert.classList.add("d-none");
			if (scanVendorSubmitBtn) {
				scanVendorSubmitBtn.disabled = false;
				scanVendorSubmitBtn.textContent = "Create Vendor";
			}

			// Pre-fill contacts from scanned data
			const hasContact = vd.contact_first_name || vd.contact_last_name || vd.phone || vd.email;
			if (scanVendorContactsForm && window.RoobicoContacts) {
				if (hasContact) {
					window.RoobicoContacts.setContacts(scanVendorContactsForm, [{
						first_name: vd.contact_first_name || "",
						last_name: vd.contact_last_name || "",
						phone: vd.phone || "",
						email: vd.email || "",
						is_main: true,
					}]);
				} else {
					window.RoobicoContacts.setContacts(scanVendorContactsForm, [{ is_main: true }]);
				}
			}

			scanVendorModal.show();
		});

		// Submit vendor creation from modal
		if (scanVendorSubmitBtn) {
			scanVendorSubmitBtn.addEventListener("click", function () {
				const name = (scanVendorNameInput?.value || "").trim();
				if (!name) {
					if (scanVendorAlert) {
						scanVendorAlert.textContent = "Vendor name is required.";
						scanVendorAlert.classList.remove("d-none");
					}
					return;
				}

				if (scanVendorAlert) scanVendorAlert.classList.add("d-none");

				// Collect contacts
				let contacts = [];
				if (scanVendorContactsForm && window.RoobicoContacts) {
					contacts = window.RoobicoContacts.getContacts(scanVendorContactsForm);
				}

				scanVendorSubmitBtn.disabled = true;
				scanVendorSubmitBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Creating...`;

				fetch("/vendors/api/create", {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({
						name: name,
						website: (scanVendorWebsite?.value || "").trim(),
						address: (scanVendorAddress?.value || "").trim(),
						notes: (scanVendorNotesInput?.value || "").trim(),
						contacts: contacts,
					}),
				})
				.then(r => r.json())
				.then(result => {
					if (result.ok && result.vendor_id) {
						// Add to vendor select dropdown
						const opt = document.createElement("option");
						opt.value = result.vendor_id;
						opt.textContent = result.vendor_name;
						vendorSelect.appendChild(opt);

						// Select the new vendor
						vendorSelect.value = result.vendor_id;
						vendorSearchInput.value = result.vendor_name;
						vendorSelect.dispatchEvent(new Event("change"));
						partSearch.disabled = false;

						// Close modal
						scanVendorModal.hide();

						// Update scan result UI
						const vendorSection = scanResult?.querySelector(".bg-light");
						if (vendorSection) {
							vendorSection.innerHTML = `<i class="bi bi-check-circle text-success me-1"></i><strong>Vendor:</strong> ${escapeHtml(result.vendor_name)} — created & selected`;
							vendorSection.classList.remove("bg-light");
						}
					} else {
						scanVendorSubmitBtn.disabled = false;
						scanVendorSubmitBtn.textContent = "Create Vendor";
						if (scanVendorAlert) {
							scanVendorAlert.textContent = result.error || "Failed to create vendor.";
							scanVendorAlert.classList.remove("d-none");
						}
					}
				})
				.catch(() => {
					scanVendorSubmitBtn.disabled = false;
					scanVendorSubmitBtn.textContent = "Create Vendor";
					if (scanVendorAlert) {
						scanVendorAlert.textContent = "Network error creating vendor.";
						scanVendorAlert.classList.remove("d-none");
					}
				});
			});
		}

		// Ensure z-index stacking when scan vendor modal opens over order modal
		scanVendorModalEl?.addEventListener("shown.bs.modal", function () {
			const backdrops = document.querySelectorAll(".modal-backdrop.show");
			if (backdrops.length > 1) {
				backdrops[backdrops.length - 1].style.zIndex = "1070";
			}
			scanVendorModalEl.style.zIndex = "1075";
		});

		scanVendorModalEl?.addEventListener("hidden.bs.modal", function () {
			scanVendorModalEl.style.zIndex = "";
			if (orderModal && orderModal.classList.contains("show")) {
				document.body.classList.add("modal-open");
			}
		});

		// ── Add single matched part from scan ────────────────────────────
		document.addEventListener("click", function (e) {
			const btn = e.target.closest(".add-scanned-matched-btn");
			if (!btn) return;

			const tr = btn.closest("tr[data-scan-matched]");
			if (!tr) return;

			const partId = tr.dataset.scanPartId;
			const partNumber = tr.querySelector("td strong")?.textContent || "";
			const desc = tr.querySelector("td.text-muted")?.textContent || "";
			const qty = parseInt(tr.querySelector(".scan-match-qty")?.value || "1", 10);
			const price = parseFloat(tr.querySelector(".scan-match-price")?.value || "0");

			addOrIncrementItem({
				id: partId,
				part_number: partNumber,
				description: desc,
				average_cost: price,
				core_has_charge: false,
				core_cost: 0,
			});
			const row = itemsBody.querySelector(`tr[data-part-id="${partId}"]`);
			if (row) {
				const qtyInput = row.querySelector(".qty-input");
				if (qtyInput) qtyInput.value = String(qty);
				const priceInput = row.querySelector(".price-input");
				if (priceInput) priceInput.value = Number(price).toFixed(2);
			}
			calculateOrderTotal();

			btn.innerHTML = `<i class="bi bi-check-lg"></i> Added`;
			btn.classList.remove("btn-primary");
			btn.classList.add("btn-success");
			btn.disabled = true;
		});

		// ── Add all matched parts ────────────────────────────────────────
		document.addEventListener("click", function (e) {
			const btn = e.target.closest("#addAllMatchedBtn");
			if (!btn) return;

			const rows = document.querySelectorAll("tr[data-scan-matched]");
			rows.forEach(tr => {
				const addBtn = tr.querySelector(".add-scanned-matched-btn");
				if (addBtn && !addBtn.disabled) addBtn.click();
			});
			btn.innerHTML = `<i class="bi bi-check-lg me-1"></i>All Added`;
			btn.classList.remove("btn-primary");
			btn.classList.add("btn-success");
			btn.disabled = true;
		});

		// ── Create Part from scan (modal) ────────────────────────────────
		const scanPartModalEl = document.getElementById("scanPartModal");
		const scanPartModal = scanPartModalEl ? new bootstrap.Modal(scanPartModalEl) : null;
		const scanPartNumber = document.getElementById("scanPartNumber");
		const scanPartReference = document.getElementById("scanPartReference");
		const scanPartDescription = document.getElementById("scanPartDescription");
		const scanPartAverageCost = document.getElementById("scanPartAverageCost");
		const scanPartVendor = document.getElementById("scanPartVendor");
		const scanPartInStock = document.getElementById("scanPartInStock");
		const scanPartCategory = document.getElementById("scanPartCategory");
		const scanPartLocation = document.getElementById("scanPartLocation");
		const scanPartSellingPriceToggle = document.getElementById("scanPartSellingPriceToggle");
		const scanPartSellingPriceGroup = document.getElementById("scanPartSellingPriceGroup");
		const scanPartSellingPrice = document.getElementById("scanPartSellingPrice");
		const scanPartDoNotTrack = document.getElementById("scanPartDoNotTrack");
		const scanPartCoreChargeToggle = document.getElementById("scanPartCoreChargeToggle");
		const scanPartCoreCostGroup = document.getElementById("scanPartCoreCostGroup");
		const scanPartCoreCost = document.getElementById("scanPartCoreCost");
		const scanPartSubmitBtn = document.getElementById("scanPartSubmitBtn");
		const scanPartAlert = document.getElementById("scanPartAlert");

		// Toggles for conditional fields
		scanPartSellingPriceToggle?.addEventListener("change", function () {
			scanPartSellingPriceGroup?.classList.toggle("d-none", !this.checked);
		});
		scanPartCoreChargeToggle?.addEventListener("change", function () {
			scanPartCoreCostGroup?.classList.toggle("d-none", !this.checked);
		});

		// Track which table row triggered the modal
		let scanPartSourceRow = null;
		let scanPartSourceQty = 1;

		// z-index stacking for scanPartModal over orderModal
		scanPartModalEl?.addEventListener("shown.bs.modal", function () {
			const backdrops = document.querySelectorAll(".modal-backdrop.show");
			if (backdrops.length > 0) {
				backdrops[backdrops.length - 1].style.zIndex = "1075";
			}
			scanPartModalEl.style.zIndex = "1080";
		});

		// ── Create & Add single unmatched part from scan ─────────────────
		document.addEventListener("click", function (e) {
			const btn = e.target.closest(".create-scanned-part-btn");
			if (!btn || !scanPartModal) return;

			const tr = btn.closest("tr[data-scan-unmatched]");
			if (!tr) return;

			const pn = (tr.querySelector(".scan-new-pn")?.value || "").trim();
			const desc = (tr.querySelector(".scan-new-desc")?.value || "").trim();
			const qty = parseInt(tr.querySelector(".scan-new-qty")?.value || "1", 10);
			const price = parseFloat(tr.querySelector(".scan-new-price")?.value || "0");

			// Pre-fill modal
			scanPartSourceRow = tr;
			scanPartSourceQty = qty;
			if (scanPartNumber) scanPartNumber.value = pn;
			if (scanPartReference) scanPartReference.value = "";
			if (scanPartDescription) scanPartDescription.value = desc;
			if (scanPartAverageCost) scanPartAverageCost.value = Number(price).toFixed(2);
			if (scanPartVendor) scanPartVendor.value = vendorSelect.value || "";
			if (scanPartInStock) scanPartInStock.value = "0";
			if (scanPartCategory) scanPartCategory.value = "";
			if (scanPartLocation) scanPartLocation.value = "";
			if (scanPartSellingPriceToggle) { scanPartSellingPriceToggle.checked = false; }
			scanPartSellingPriceGroup?.classList.add("d-none");
			if (scanPartSellingPrice) scanPartSellingPrice.value = "0";
			if (scanPartDoNotTrack) scanPartDoNotTrack.checked = false;
			if (scanPartCoreChargeToggle) { scanPartCoreChargeToggle.checked = false; }
			scanPartCoreCostGroup?.classList.add("d-none");
			if (scanPartCoreCost) scanPartCoreCost.value = "0";
			if (scanPartAlert) scanPartAlert.classList.add("d-none");
			if (scanPartSubmitBtn) {
				scanPartSubmitBtn.disabled = false;
				scanPartSubmitBtn.textContent = "Create Part";
			}

			scanPartModal.show();
		});

		// Submit part creation from modal
		scanPartSubmitBtn?.addEventListener("click", function () {
			const pn = (scanPartNumber?.value || "").trim();
			if (!pn) {
				if (scanPartAlert) {
					scanPartAlert.textContent = "Part number is required.";
					scanPartAlert.classList.remove("d-none");
				}
				return;
			}

			const desc = (scanPartDescription?.value || "").trim();
			const price = parseFloat(scanPartAverageCost?.value || "0");
			const hasSelling = scanPartSellingPriceToggle?.checked || false;
			const coreCharge = scanPartCoreChargeToggle?.checked || false;

			const payload = {
				part_number: pn,
				reference: (scanPartReference?.value || "").trim(),
				description: desc,
				average_cost: price,
				vendor_id: scanPartVendor?.value || "",
				in_stock: parseInt(scanPartInStock?.value || "0", 10),
				category_id: scanPartCategory?.value || "",
				location_id: scanPartLocation?.value || "",
				has_selling_price: hasSelling,
				selling_price: hasSelling ? parseFloat(scanPartSellingPrice?.value || "0") : 0,
				do_not_track_inventory: scanPartDoNotTrack?.checked || false,
				core_has_charge: coreCharge,
				core_cost: coreCharge ? parseFloat(scanPartCoreCost?.value || "0") : 0,
			};

			scanPartSubmitBtn.disabled = true;
			scanPartSubmitBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Creating...`;

			fetch("/parts/api/create", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(payload),
			})
			.then(r => r.json())
			.then(result => {
				if (result.ok && result.part_id) {
					scanPartModal.hide();

					// Mark source row as created
					const tr = scanPartSourceRow;
					if (tr) {
						const btn = tr.querySelector(".create-scanned-part-btn");
						if (btn) {
							btn.innerHTML = `<i class="bi bi-check-lg me-1"></i>Created`;
							btn.classList.remove("btn-outline-primary");
							btn.classList.add("btn-success");
							btn.disabled = true;
						}
						tr.querySelectorAll("input").forEach(inp => inp.disabled = true);
					}

					// Add to order items
					addOrIncrementItem({
						id: result.part_id,
						part_number: pn,
						description: desc,
						average_cost: price,
						core_has_charge: coreCharge,
						core_cost: coreCharge ? parseFloat(scanPartCoreCost?.value || "0") : 0,
					});
					const row = itemsBody.querySelector(`tr[data-part-id="${result.part_id}"]`);
					if (row) {
						const qtyInput = row.querySelector(".qty-input");
						if (qtyInput && scanPartSourceQty > 1) qtyInput.value = String(scanPartSourceQty);
						const priceInput = row.querySelector(".price-input");
						if (priceInput) priceInput.value = Number(price).toFixed(2);
					}
					calculateOrderTotal();
					scanPartSourceRow = null;
				} else {
					scanPartSubmitBtn.disabled = false;
					scanPartSubmitBtn.textContent = "Create Part";
					if (scanPartAlert) {
						scanPartAlert.textContent = result.error || "Failed to create part.";
						scanPartAlert.classList.remove("d-none");
					}
				}
			})
			.catch(() => {
				scanPartSubmitBtn.disabled = false;
				scanPartSubmitBtn.textContent = "Create Part";
				if (scanPartAlert) {
					scanPartAlert.textContent = "Network error creating part.";
					scanPartAlert.classList.remove("d-none");
				}
			});
		});

		// ── Create & Add all unmatched parts (queue through modal) ──────
		let scanPartQueue = [];
		let scanPartQueueBtn = null;

		function openNextQueuedPart() {
			if (scanPartQueue.length === 0) {
				if (scanPartQueueBtn) {
					scanPartQueueBtn.innerHTML = `<i class="bi bi-check-lg me-1"></i>All Created`;
					scanPartQueueBtn.classList.remove("btn-outline-primary");
					scanPartQueueBtn.classList.add("btn-success");
					scanPartQueueBtn = null;
				}
				return;
			}
			const nextBtn = scanPartQueue.shift();
			nextBtn.click();
		}

		// After modal hides, open next queued part
		scanPartModalEl?.addEventListener("hidden.bs.modal", function () {
			if (scanPartQueue.length > 0) {
				setTimeout(openNextQueuedPart, 200);
			}
		});

		document.addEventListener("click", function (e) {
			const btn = e.target.closest("#createAllUnmatchedBtn");
			if (!btn) return;

			const rows = document.querySelectorAll("tr[data-scan-unmatched]");
			const pending = [];
			rows.forEach(tr => {
				const createBtn = tr.querySelector(".create-scanned-part-btn");
				if (createBtn && !createBtn.disabled) pending.push(createBtn);
			});

			if (pending.length === 0) return;

			btn.disabled = true;
			btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Creating ${pending.length}...`;
			scanPartQueueBtn = btn;
			scanPartQueue = pending.slice(1);
			pending[0].click();
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
		const partHistoryDateFrom = document.getElementById("partHistoryDateFrom");
		const partHistoryDateTo = document.getElementById("partHistoryDateTo");
		const partHistoryDatePreset = document.getElementById("partHistoryDatePreset");
		const partHistoryFilterRow = document.getElementById("partHistoryFilterRow");

		// Cache raw history data for client-side date filtering
		let _historyOrders = [];
		let _historyWorkOrders = [];
		let _historyPart = {};
		let _historyPartId = "";
		let _ordersCurrentPage = 1;
		let _woCurrentPage = 1;

		function money(n) {
			const x = Number(n || 0);
			return Number.isFinite(x) ? x.toFixed(2) : "0.00";
		}

		function _getHistoryDateVal(el) {
			if (!el) return "";
			if (el._flatpickr) {
				var d = el._flatpickr.selectedDates;
				return d && d.length ? el._flatpickr.formatDate(d[0], "Y-m-d") : "";
			}
			return el.value || "";
		}

		function _renderHistoryTables(orders, workOrders, ordersPg, woPg) {
			partHistoryMeta.textContent = `${_historyPart.part_number || ""}${_historyPart.description ? ` — ${_historyPart.description}` : ""} | Orders: ${(ordersPg && ordersPg.total) || orders.length}, Work Orders: ${(woPg && woPg.total) || workOrders.length}`;

			if (orders.length === 0) {
				partHistoryOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">No orders found for this part.</td></tr>`;
			} else {
				partHistoryOrdersBody.innerHTML = orders.map(function (row) {
					var orderNum = row.order_number || (row.order_id ? "#" + row.order_id.slice(-6) : "-");
					return '<tr>' +
						'<td><span class="badge bg-secondary">' + orderNum + '</span></td>' +
						'<td>' + escapeHtml(row.status || "-") + '</td>' +
						'<td>' + escapeHtml(row.vendor || "-") + '</td>' +
						'<td class="text-end">' + Number(row.quantity || 0) + '</td>' +
						'<td class="text-end">$' + money(row.price) + '</td>' +
						'<td class="small">' + escapeHtml(formatDateTime(row.created_at)) + '</td>' +
						'<td class="small">' + escapeHtml(formatDateTime(row.received_at)) + '</td>' +
						'</tr>';
				}).join("");
			}

			// Orders pagination
			var ordersPagEl = document.getElementById("partHistoryOrdersPagination");
			if (ordersPagEl && ordersPg) {
				if (ordersPg.pages > 1) {
					var prevDisabled = !ordersPg.has_prev ? " disabled" : "";
					var nextDisabled = !ordersPg.has_next ? " disabled" : "";
					ordersPagEl.innerHTML =
						'<div class="wo-pagination-row mt-2">' +
						'<div class="small text-muted wo-pagination-meta">Page ' + ordersPg.page + ' of ' + ordersPg.pages + ' &middot; ' + ordersPg.total + ' total</div>' +
						'<div class="wo-pagination-actions"><div class="btn-group btn-group-sm" role="group" aria-label="Orders pagination">' +
						'<button type="button" class="btn btn-outline-secondary js-history-orders-page' + prevDisabled + '" data-page="' + ordersPg.prev_page + '"' + (prevDisabled ? ' tabindex="-1"' : '') + '>Prev</button>' +
						'<button type="button" class="btn btn-outline-secondary js-history-orders-page' + nextDisabled + '" data-page="' + ordersPg.next_page + '"' + (nextDisabled ? ' tabindex="-1"' : '') + '>Next</button>' +
						'</div></div></div>';
				} else if (ordersPg.total) {
					ordersPagEl.innerHTML = '<div class="mt-2"><div class="small text-muted">' + ordersPg.total + ' total</div></div>';
				} else {
					ordersPagEl.innerHTML = '';
				}
			}

			if (workOrders.length === 0) {
				partHistoryWorkOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">No work orders found for this part.</td></tr>`;
			} else {
				partHistoryWorkOrdersBody.innerHTML = workOrders.map(function (row) {
					var woNum = row.wo_number || (row.work_order_id ? "#" + row.work_order_id.slice(-6) : "-");
					return '<tr style="cursor: pointer;" class="workOrderHistoryRow" data-wo-id="' + row.work_order_id + '">' +
						'<td><span class="badge bg-secondary">' + woNum + '</span></td>' +
						'<td>' + escapeHtml(row.status || "-") + '</td>' +
						'<td>' + escapeHtml(row.customer || "-") + '</td>' +
						'<td>' + escapeHtml(row.unit || "-") + '</td>' +
						'<td class="text-end">' + Number(row.used_qty || 0) + '</td>' +
						'<td class="text-end">$' + money(row.grand_total) + '</td>' +
						'<td class="small">' + escapeHtml(formatDateTime(row.created_at)) + '</td>' +
						'</tr>';
				}).join("");
			}

			// Work orders pagination
			var woPagEl = document.getElementById("partHistoryWoPagination");
			if (woPagEl && woPg) {
				if (woPg.pages > 1) {
					var wPrevDisabled = !woPg.has_prev ? " disabled" : "";
					var wNextDisabled = !woPg.has_next ? " disabled" : "";
					woPagEl.innerHTML =
						'<div class="wo-pagination-row mt-2">' +
						'<div class="small text-muted wo-pagination-meta">Page ' + woPg.page + ' of ' + woPg.pages + ' &middot; ' + woPg.total + ' total</div>' +
						'<div class="wo-pagination-actions"><div class="btn-group btn-group-sm" role="group" aria-label="Work orders pagination">' +
						'<button type="button" class="btn btn-outline-secondary js-history-wo-page' + wPrevDisabled + '" data-page="' + woPg.prev_page + '"' + (wPrevDisabled ? ' tabindex="-1"' : '') + '>Prev</button>' +
						'<button type="button" class="btn btn-outline-secondary js-history-wo-page' + wNextDisabled + '" data-page="' + woPg.next_page + '"' + (wNextDisabled ? ' tabindex="-1"' : '') + '>Next</button>' +
						'</div></div></div>';
				} else if (woPg.total) {
					woPagEl.innerHTML = '<div class="mt-2"><div class="small text-muted">' + woPg.total + ' total</div></div>';
				} else {
					woPagEl.innerHTML = '';
				}
			}

			if (window.TableSort) {
				var oTbl = partHistoryOrdersBody && partHistoryOrdersBody.closest("table");
				var wTbl = partHistoryWorkOrdersBody && partHistoryWorkOrdersBody.closest("table");
				if (oTbl) window.TableSort.refresh(oTbl);
				if (wTbl) window.TableSort.refresh(wTbl);
			}
		}

		async function loadPartHistory(partId, ordersPage, woPage) {
			if (!partHistoryMeta || !partHistoryOrdersBody || !partHistoryWorkOrdersBody) return;

			if (partId) _historyPartId = partId;
			if (typeof ordersPage === "number" && ordersPage >= 1) _ordersCurrentPage = ordersPage;
			if (typeof woPage === "number" && woPage >= 1) _woCurrentPage = woPage;

			partHistoryMeta.textContent = "Loading...";
			partHistoryOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">Loading...</td></tr>`;
			partHistoryWorkOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">Loading...</td></tr>`;

			try {
				var apiParams = new URLSearchParams();
				apiParams.set("orders_page", String(_ordersCurrentPage));
				apiParams.set("wo_page", String(_woCurrentPage));

				var dateFrom = _getHistoryDateVal(partHistoryDateFrom);
				var dateTo = _getHistoryDateVal(partHistoryDateTo);
				var datePreset = partHistoryDatePreset ? partHistoryDatePreset.value : "";
				if (datePreset) apiParams.set("date_preset", datePreset);
				if (dateFrom) apiParams.set("date_from", dateFrom);
				if (dateTo) apiParams.set("date_to", dateTo);

				const res = await fetch(`/parts/api/${encodeURIComponent(_historyPartId)}/history?${apiParams.toString()}`, {
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

				_historyPart = data.part || {};
				_historyOrders = Array.isArray(data.orders) ? data.orders : [];
				_historyWorkOrders = Array.isArray(data.work_orders) ? data.work_orders : [];

				_renderHistoryTables(_historyOrders, _historyWorkOrders, data.orders_pagination || {}, data.wo_pagination || {});
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

		// Pagination click handlers for history tables
		document.addEventListener("click", function (e) {
			if (!isPartsPageAlive()) return;
			var btn = e.target.closest(".js-history-orders-page");
			if (btn && !btn.classList.contains("disabled")) {
				var page = parseInt(btn.dataset.page, 10);
				if (page >= 1) loadPartHistory(null, page, undefined);
				return;
			}
			var woBtn = e.target.closest(".js-history-wo-page");
			if (woBtn && !woBtn.classList.contains("disabled")) {
				var woPage = parseInt(woBtn.dataset.page, 10);
				if (woPage >= 1) loadPartHistory(null, undefined, woPage);
			}
		});

		partHistoryModal?.addEventListener("hidden.bs.modal", function () {
			if (partHistoryMeta) partHistoryMeta.textContent = "Loading...";
			if (partHistoryOrdersBody) partHistoryOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">No data.</td></tr>`;
			if (partHistoryWorkOrdersBody) partHistoryWorkOrdersBody.innerHTML = `<tr><td colspan="7" class="text-muted">No data.</td></tr>`;
			_historyOrders = [];
			_historyWorkOrders = [];
			_historyPart = {};
			_historyPartId = "";
			_ordersCurrentPage = 1;
			_woCurrentPage = 1;
			var ordersPagEl = document.getElementById("partHistoryOrdersPagination");
			var woPagEl = document.getElementById("partHistoryWoPagination");
			if (ordersPagEl) ordersPagEl.innerHTML = '';
			if (woPagEl) woPagEl.innerHTML = '';
			if (partHistoryDateFrom && partHistoryDateFrom._flatpickr) partHistoryDateFrom._flatpickr.clear();
			if (partHistoryDateTo && partHistoryDateTo._flatpickr) partHistoryDateTo._flatpickr.clear();
			if (partHistoryDatePreset) partHistoryDatePreset.value = "all_time";
		});

		partHistoryModal?.addEventListener("shown.bs.modal", function () {
			if (typeof window.initDatePickers === "function") {
				window.initDatePickers(partHistoryModal);
			}
		});

		// Date filter change handlers — re-fetch from server with page reset
		function _onHistoryDateChange() {
			if (_historyPartId) {
				_ordersCurrentPage = 1;
				_woCurrentPage = 1;
				loadPartHistory(null, 1, 1);
			}
		}

		if (partHistoryDatePreset) {
			partHistoryDatePreset.addEventListener("change", function () {
				if (typeof window.applyDatePresetToForm === "function") {
					window.applyDatePresetToForm(partHistoryFilterRow, partHistoryDatePreset.value);
				}
				_onHistoryDateChange();
			});
		}

		if (partHistoryDateFrom) {
			partHistoryDateFrom.addEventListener("change", function () {
				if (partHistoryDatePreset && partHistoryDatePreset.value !== "custom") {
					partHistoryDatePreset.value = "custom";
				}
				_onHistoryDateChange();
			});
		}
		if (partHistoryDateTo) {
			partHistoryDateTo.addEventListener("change", function () {
				if (partHistoryDatePreset && partHistoryDatePreset.value !== "custom") {
					partHistoryDatePreset.value = "custom";
				}
				_onHistoryDateChange();
			});
		}

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
			if (!editBtn) {
				// Creating new order — hide attachments
				var attGroup = document.getElementById('orderAttachmentsGroup');
				if (attGroup) attGroup.classList.add('d-none');
				return;
			}
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

			// Show attachments block when editing
			var attGroup = document.getElementById('partAttachmentsGroup');
			if (attGroup) {
				if (partId) {
					attGroup.style.display = '';
					var attBlock = attGroup.querySelector('.attachments-block');
					if (attBlock) {
						attBlock.setAttribute('data-entity-id', partId);
						var ctrl = window.AttachmentsGetBlock ? window.AttachmentsGetBlock(attBlock) : null;
						if (ctrl) { ctrl.setEntityId(partId); ctrl.load(); }
						else if (window.AttachmentsInit) window.AttachmentsInit();
					}
				} else {
					attGroup.style.display = 'none';
				}
			}
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
			if (!editBtn) {
				// Creating new part — hide attachments
				var attGroup = document.getElementById('partAttachmentsGroup');
				if (attGroup) attGroup.style.display = 'none';
				return;
			}
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
