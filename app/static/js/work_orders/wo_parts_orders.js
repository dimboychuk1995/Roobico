// Парт-ордера, привязанные к work order: блок в секции Customer & Unit.
// Создание заказа (через /parts/api/orders/create с work_order_id),
// receive/pay через существующие API, сверка использования позиций в WO.
(function () {
  "use strict";

  var toggleBtn = document.getElementById("woPartsOrdersToggle");
  var listEl = document.getElementById("woPartsOrdersList");
  if (!toggleBtn || !listEl) return; // WO не создан или нет прав view_costs

  function readJson(id, fallback) {
    var el = document.getElementById(id);
    if (!el) return fallback;
    try { return JSON.parse(el.textContent); } catch (e) { return fallback; }
  }
  var createdInfo = readJson("workOrderCreatedData", {}) || {};
  var workOrderId = String(createdInfo.id || "");
  // До сохранения WO заказы висят на pending-id (перепривяжутся при создании)
  var pendingWoId = String(toggleBtn.getAttribute("data-pending-wo-id") || "");
  var linkId = workOrderId || pendingWoId;
  if (!linkId) return;

  var alertFn = window.appAlert || function (m) { window.alert(m); };
  function money(v) { return "$" + (Math.round((v || 0) * 100) / 100).toFixed(2); }

  function fetchJson(url, options) {
    return fetch(url, Object.assign({ headers: { "Accept": "application/json" } }, options || {}))
      .then(function (res) {
        return res.text().then(function (text) {
          var data = null;
          try { data = JSON.parse(text); } catch (e) { /* not JSON */ }
          if (!data) throw new Error("Request failed (" + res.status + ")");
          return data;
        });
      });
  }

  // ── список привязанных заказов ───────────────────────────────────
  function statusBadge(order) {
    var s = document.createElement("span");
    if (order.status === "received") {
      s.className = "badge text-bg-success";
      s.textContent = "received";
    } else {
      s.className = "badge text-bg-warning";
      s.textContent = order.status || "ordered";
    }
    return s;
  }

  function payBadge(order) {
    var s = document.createElement("span");
    if (order.payment_status === "paid") {
      s.className = "badge text-bg-success";
      s.textContent = "paid";
    } else if (order.payment_status === "partial") {
      s.className = "badge text-bg-warning";
      s.textContent = "partial";
    } else {
      s.className = "badge text-bg-secondary";
      s.textContent = order.payment_status || "unpaid";
    }
    return s;
  }

  function usageChip(item) {
    var chip = document.createElement("span");
    if (item.usage === "used") {
      chip.className = "badge text-bg-success";
      chip.textContent = item.part_number + " × " + item.quantity;
      chip.title = "All " + item.quantity + " used in this work order";
    } else if (item.usage === "partial") {
      chip.className = "badge text-bg-warning";
      chip.textContent = item.part_number + " " + item.used_qty + "/" + item.quantity;
      chip.title = "Only " + item.used_qty + " of " + item.quantity + " used in this work order";
    } else {
      chip.className = "badge text-bg-danger";
      chip.textContent = item.part_number + " × " + item.quantity;
      chip.title = "Not used in this work order";
    }
    return chip;
  }

  function render(orders) {
    listEl.textContent = "";
    var countBadge = document.getElementById("woPartsOrdersCount");
    if (countBadge) {
      countBadge.textContent = String(orders.length);
      countBadge.style.display = orders.length ? "" : "none";
    }
    if (!orders.length) {
      var empty = document.createElement("div");
      empty.className = "text-muted small";
      empty.textContent = "No parts orders for this work order yet.";
      listEl.appendChild(empty);
      return;
    }

    orders.forEach(function (order) {
      var card = document.createElement("div");
      card.className = "border rounded p-2 mb-2";

      var head = document.createElement("div");
      head.className = "d-flex align-items-center flex-wrap gap-2";

      var num = document.createElement("span");
      num.className = "fw-semibold";
      num.textContent = "#" + order.order_number;
      head.appendChild(num);

      var vendor = document.createElement("span");
      vendor.className = "text-muted";
      vendor.textContent = order.vendor;
      head.appendChild(vendor);

      head.appendChild(statusBadge(order));
      head.appendChild(payBadge(order));

      var total = document.createElement("span");
      total.className = "ms-auto fw-semibold";
      total.textContent = money(order.total_amount);
      if (order.remaining_balance > 0) {
        total.textContent += " · balance " + money(order.remaining_balance);
      }
      head.appendChild(total);

      // Действия: receive для ordered, pay для неоплаченных
      var actions = document.createElement("span");
      actions.className = "d-inline-flex gap-1";
      if (order.status !== "received") {
        var rcv = document.createElement("button");
        rcv.type = "button";
        rcv.className = "btn btn-sm btn-outline-warning";
        rcv.textContent = "Receive";
        rcv.addEventListener("click", function () { receiveOrder(order); });
        actions.appendChild(rcv);
      }
      if (order.payment_status !== "paid") {
        var pay = document.createElement("button");
        pay.type = "button";
        pay.className = "btn btn-sm btn-outline-success";
        pay.textContent = "Pay";
        pay.addEventListener("click", function () { openPayModal(order); });
        actions.appendChild(pay);
      }
      head.appendChild(actions);
      card.appendChild(head);

      if (order.items.length) {
        var chips = document.createElement("div");
        chips.className = "d-flex flex-wrap gap-1 mt-2";
        order.items.forEach(function (item) { chips.appendChild(usageChip(item)); });
        card.appendChild(chips);
      }

      if (order.unused.length) {
        var warn = document.createElement("div");
        warn.className = "alert alert-warning py-1 px-2 small mt-2 mb-0";
        warn.textContent = "Not used in this work order: " + order.unused.map(function (u) {
          return u.part_number + (u.used ? " (" + u.used + "/" + u.ordered + ")" : " × " + u.ordered);
        }).join(", ");
        card.appendChild(warn);
      }

      listEl.appendChild(card);
    });
  }

  function reload() {
    fetchJson("/work_orders/api/work_orders/" + encodeURIComponent(linkId) + "/parts_orders")
      .then(function (data) {
        if (data.ok) render(data.orders || []);
      })
      .catch(function () { /* блок не критичен для страницы */ });
  }

  // ── receive / pay ────────────────────────────────────────────────
  function receiveOrder(order) {
    var confirmFn = window.appConfirm || function (m) { return Promise.resolve(window.confirm(m)); };
    confirmFn("Receive order #" + order.order_number + "? Stock will be updated.", {
      confirmText: "Receive", icon: "question",
    }).then(function (yes) {
      if (!yes) return;
      fetchJson("/parts/api/orders/" + encodeURIComponent(order.id) + "/receive", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({ vendor_bill: order.vendor_bill || "" }),
      }).then(function (data) {
        if (!data.ok) { alertFn(data.error || "Failed to receive order", "error"); return; }
        alertFn("Order received — stock updated.", "success");
        reload();
      }).catch(function (e) { alertFn(e.message, "error"); });
    });
  }

  function openPayModal(order) {
    document.getElementById("woPoPayOrderId").value = order.id;
    document.getElementById("woPoPayMeta").textContent = "· order #" + order.order_number;
    document.getElementById("woPoPayAmount").value = (order.remaining_balance || 0).toFixed(2);
    document.getElementById("woPoPayNotes").value = "";
    bootstrap.Modal.getOrCreateInstance(document.getElementById("woPoPayModal")).show();
  }

  var paySubmit = document.getElementById("woPoPaySubmit");
  if (paySubmit) {
    paySubmit.addEventListener("click", function () {
      var orderId = document.getElementById("woPoPayOrderId").value;
      var amount = parseFloat(document.getElementById("woPoPayAmount").value) || 0;
      if (!orderId || amount <= 0) { alertFn("Enter a payment amount.", "warning"); return; }
      paySubmit.disabled = true;
      fetchJson("/parts/api/orders/" + encodeURIComponent(orderId) + "/payment", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({
          amount: amount,
          payment_method: document.getElementById("woPoPayMethod").value,
          notes: document.getElementById("woPoPayNotes").value || "",
        }),
      }).then(function (data) {
        if (!data.ok) {
          alertFn(data.message || data.error || "Failed to record payment", "error");
          return;
        }
        bootstrap.Modal.getOrCreateInstance(document.getElementById("woPoPayModal")).hide();
        alertFn("Payment recorded.", "success");
        reload();
      }).catch(function (e) { alertFn(e.message, "error"); })
        .finally(function () { paySubmit.disabled = false; });
    });
  }

  // ── создание заказа ──────────────────────────────────────────────
  var orderItems = [];
  var vendorsLoaded = false;

  function renderOrderItems() {
    var host = document.getElementById("woPoItems");
    host.textContent = "";
    if (!orderItems.length) {
      var hint = document.createElement("div");
      hint.className = "text-muted small mb-1";
      hint.textContent = "No items yet — search below to add parts.";
      host.appendChild(hint);
      return;
    }
    orderItems.forEach(function (item, idx) {
      var row = document.createElement("div");
      row.className = "d-flex align-items-center gap-2 mb-1";

      var name = document.createElement("div");
      name.className = "flex-grow-1 small";
      name.textContent = item.part_number + (item.description ? " — " + item.description : "");
      row.appendChild(name);

      var qty = document.createElement("input");
      qty.type = "number"; qty.min = "1"; qty.step = "1"; qty.value = item.quantity;
      qty.className = "form-control form-control-sm";
      qty.style.maxWidth = "80px";
      qty.addEventListener("change", function () {
        item.quantity = Math.max(1, parseInt(qty.value, 10) || 1);
        qty.value = item.quantity;
      });
      row.appendChild(qty);

      var price = document.createElement("input");
      price.type = "number"; price.min = "0"; price.step = "0.01"; price.value = item.price;
      price.className = "form-control form-control-sm";
      price.style.maxWidth = "110px";
      price.addEventListener("change", function () {
        item.price = Math.max(0, parseFloat(price.value) || 0);
        price.value = item.price;
      });
      row.appendChild(price);

      var del = document.createElement("button");
      del.type = "button";
      del.className = "btn btn-sm btn-outline-danger";
      del.innerHTML = "&times;";
      del.addEventListener("click", function () {
        orderItems.splice(idx, 1);
        renderOrderItems();
      });
      row.appendChild(del);

      host.appendChild(row);
    });
  }

  var poModalEl = document.getElementById("woPartsOrderModal");
  if (poModalEl) {
    poModalEl.addEventListener("show.bs.modal", function () {
      orderItems = [];
      renderOrderItems();
      if (!vendorsLoaded) {
        fetchJson("/work_orders/api/vendors-lookup").then(function (data) {
          if (!data.ok) return;
          var sel = document.getElementById("woPoVendor");
          (data.vendors || []).forEach(function (v) {
            var opt = document.createElement("option");
            opt.value = v.id;
            opt.textContent = v.name;
            sel.appendChild(opt);
          });
          vendorsLoaded = true;
        }).catch(function () { /* поиск вендоров не критичен до сабмита */ });
      }
    });
  }

  // Поиск партов — тем же API, что и строки лейборов
  var searchInput = document.getElementById("woPoPartSearch");
  var resultsBox = document.getElementById("woPoPartResults");
  var searchTimer = null;
  if (searchInput && resultsBox) {
    searchInput.addEventListener("input", function () {
      clearTimeout(searchTimer);
      var q = searchInput.value.trim();
      if (q.length < 3) { resultsBox.classList.add("d-none"); return; }
      searchTimer = setTimeout(function () {
        fetchJson("/work_orders/api/parts/search?q=" + encodeURIComponent(q) + "&limit=15")
          .then(function (data) {
            var parts = (data && data.items) || [];
            resultsBox.textContent = "";
            parts.forEach(function (p) {
              var a = document.createElement("button");
              a.type = "button";
              a.className = "list-group-item list-group-item-action py-1 small";
              a.textContent = (p.part_number || "-") + (p.description ? " — " + p.description : "");
              a.addEventListener("click", function () {
                orderItems.push({
                  part_id: p.id || "",
                  part_number: p.part_number || "-",
                  description: p.description || "",
                  quantity: 1,
                  price: parseFloat(p.average_cost != null ? p.average_cost : 0) || 0,
                });
                renderOrderItems();
                searchInput.value = "";
                resultsBox.classList.add("d-none");
              });
              resultsBox.appendChild(a);
            });
            resultsBox.classList.toggle("d-none", parts.length === 0);
          })
          .catch(function () { resultsBox.classList.add("d-none"); });
      }, 250);
    });
    document.addEventListener("click", function (e) {
      if (!resultsBox.contains(e.target) && e.target !== searchInput) {
        resultsBox.classList.add("d-none");
      }
    });
  }

  // ── скан инвойса вендора (тот же AI-парсер, что на странице Parts) ──
  var scanInput = document.getElementById("woPoScanInput");
  if (scanInput) {
    scanInput.addEventListener("change", function () {
      var file = scanInput.files && scanInput.files[0];
      if (!file) return;
      var status = document.getElementById("woPoScanStatus");
      var warn = document.getElementById("woPoScanWarn");
      if (warn) warn.classList.add("d-none");
      if (status) status.textContent = "Scanning invoice…";

      var fd = new FormData();
      fd.append("invoice", file);
      fetchJson("/parts/api/orders/parse-invoice", { method: "POST", body: fd })
        .then(function (data) {
          if (!data.ok) {
            if (status) status.textContent = "";
            alertFn(data.error || "Failed to scan the invoice", "error");
            return;
          }
          // Вендор: подставляем, если распознан и есть в базе
          var sel = document.getElementById("woPoVendor");
          if (data.vendor_match && sel) {
            var vid = data.vendor_match.vendor_id;
            if (!sel.querySelector('option[value="' + vid + '"]')) {
              var opt = document.createElement("option");
              opt.value = vid;
              opt.textContent = data.vendor_match.vendor_name || "Vendor";
              sel.appendChild(opt);
            }
            sel.value = vid;
          }
          // Позиции: совпавшие с каталогом — в заказ, остальные — в предупреждение
          var unmatched = [];
          (data.items || []).forEach(function (item) {
            var m = item.matched_part;
            if (m && m.part_id) {
              orderItems.push({
                part_id: m.part_id,
                part_number: m.part_number || item.part_number || "-",
                description: m.description || item.description || "",
                quantity: Math.max(1, parseInt(item.quantity, 10) || 1),
                price: (item.price != null && item.price !== "")
                  ? (parseFloat(item.price) || 0)
                  : (parseFloat(m.average_cost) || 0),
              });
            } else if (item.part_number || item.description) {
              unmatched.push(item.part_number || item.description);
            }
          });
          renderOrderItems();
          if (status) {
            status.textContent = data.vendor_match
              ? "Scanned — vendor and items filled in."
              : "Scanned — pick the vendor manually.";
          }
          if (unmatched.length && warn) {
            warn.textContent = "Not in the parts catalog (add them on the Parts page first): "
              + unmatched.join(", ");
            warn.classList.remove("d-none");
          }
        })
        .catch(function (e) {
          if (status) status.textContent = "";
          alertFn(e.message || "Failed to scan the invoice", "error");
        })
        .finally(function () { scanInput.value = ""; });
    });
  }

  var poSubmit = document.getElementById("woPoSubmit");
  if (poSubmit) {
    poSubmit.addEventListener("click", function () {
      var vendorId = document.getElementById("woPoVendor").value;
      if (!vendorId) { alertFn("Select a vendor.", "warning"); return; }
      if (!orderItems.length) { alertFn("Add at least one item.", "warning"); return; }
      poSubmit.disabled = true;
      var payload = {
        vendor_id: vendorId,
        order_date: document.getElementById("woPoDate").value || "",
        items: orderItems.map(function (i) {
          return { part_id: i.part_id, quantity: i.quantity, price: i.price };
        }),
      };
      if (workOrderId) payload.work_order_id = workOrderId;
      else payload.pending_work_order_id = pendingWoId;
      fetchJson("/parts/api/orders/create", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify(payload),
      }).then(function (data) {
        if (!data.ok) { alertFn(data.error || "Failed to create order", "error"); return; }
        bootstrap.Modal.getOrCreateInstance(poModalEl).hide();
        alertFn("Parts order created and linked to this work order.", "success");
        var collapseEl = document.getElementById("woPartsOrdersBlock");
        if (collapseEl && window.bootstrap) {
          bootstrap.Collapse.getOrCreateInstance(collapseEl, { toggle: false }).show();
        }
        reload();
      }).catch(function (e) { alertFn(e.message, "error"); })
        .finally(function () { poSubmit.disabled = false; });
    });
  }

  reload();
})();
