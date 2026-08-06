// Парт-ордера, привязанные к work order: блок в секции Customer & Unit.
// Создание/редактирование/receive/pay идут через ОБЩУЮ модалку заказа
// (components/parts_order_modal.html + js/parts/parts.js — та же, что на
// странице Parts). Здесь только список привязанных заказов со сверкой
// использования позиций в WO; кнопки списка переиспользуют обработчики
// parts.js (editOrderBtn / receiveStatusBtn / js-order-payment), а после
// каждой мутации parts.js шлёт "roobico:wo-parts-orders-changed".
(function () {
  "use strict";

  var toggleBtn = document.getElementById("woPartsOrdersToggle");
  var listEl = document.getElementById("woPartsOrdersList");
  if (!toggleBtn || !listEl) return; // нет прав view_costs

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
    if (!item.usage) {
      // Сверка спрятана: WO ещё не принят (estimate / не сохранён) — все
      // позиции нейтральные, без «не использовано».
      chip.className = "badge text-bg-secondary";
      chip.textContent = item.part_number + " × " + item.quantity;
      chip.title = "Usage is checked after the work order is accepted";
    } else if (item.usage === "used") {
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

      // Номер — открывает общую модалку заказа (обработчик в parts.js).
      var num = document.createElement("button");
      num.type = "button";
      num.className = "btn btn-link p-0 fw-semibold text-decoration-none editOrderBtn";
      num.setAttribute("data-order-id", order.id);
      num.setAttribute("data-bs-toggle", "modal");
      num.setAttribute("data-bs-target", "#orderModal");
      num.textContent = "#" + order.order_number;
      num.title = "Open this parts order";
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

      // Действия — те же классы, что в таблице Parts Orders: обработчики
      // (receive c локациями, платёж) живут в parts.js.
      var actions = document.createElement("span");
      actions.className = "d-inline-flex gap-1";

      var open = document.createElement("button");
      open.type = "button";
      open.className = "btn btn-sm btn-outline-secondary editOrderBtn";
      open.setAttribute("data-order-id", order.id);
      open.setAttribute("data-bs-toggle", "modal");
      open.setAttribute("data-bs-target", "#orderModal");
      open.textContent = "Open";
      actions.appendChild(open);

      if (order.status !== "received") {
        var rcv = document.createElement("button");
        rcv.type = "button";
        rcv.className = "btn btn-sm btn-outline-warning receiveStatusBtn";
        rcv.setAttribute("data-order-id", order.id);
        rcv.setAttribute("data-vendor-bill", order.vendor_bill || "");
        rcv.textContent = "Receive";
        actions.appendChild(rcv);
      }
      if (order.payment_status !== "paid") {
        var pay = document.createElement("button");
        pay.type = "button";
        pay.className = "btn btn-sm btn-outline-success js-order-payment";
        pay.setAttribute("data-order-id", order.id);
        pay.setAttribute("data-payment-status", order.payment_status || "unpaid");
        pay.textContent = "Pay";
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

  // parts.js шлёт после create/receive/unreceive/pay/delete в WO-контексте.
  window.addEventListener("roobico:wo-parts-orders-changed", function () {
    var collapseEl = document.getElementById("woPartsOrdersBlock");
    if (collapseEl && window.bootstrap) {
      bootstrap.Collapse.getOrCreateInstance(collapseEl, { toggle: false }).show();
    }
    reload();
  });

  reload();
})();
