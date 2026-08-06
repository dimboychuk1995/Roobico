"""Парт-ордера, привязанные к work order (parts_orders.work_order_id).

Заказ создаётся из страницы WO (кнопка у юнита) обычным
/parts/api/orders/create с полем work_order_id и живёт в общем списке
Parts Orders как любой другой заказ. Здесь — payload для блока внутри WO:
статусы заказов + сверка «какие позиции заказа реально стоят в этом WO».

Сверка использования повторяет логику parts_api_history: строка запчасти WO
может ссылаться на парт ObjectId-ом, строкой или (легаси/one-time) только
part_number-ом — считаем по id, фолбэк по номеру.
"""
from __future__ import annotations


def _wo_parts_usage(wo: dict) -> tuple[dict, dict]:
    """(qty по str(part_id), qty по part_number.lower()) из labors[].parts[]."""
    by_id: dict[str, int] = {}
    by_pn: dict[str, int] = {}
    for block in wo.get("labors") or []:
        if not isinstance(block, dict):
            continue
        for p in block.get("parts") or []:
            if not isinstance(p, dict):
                continue
            try:
                qty = int(p.get("qty") or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            pid = p.get("part_id")
            if pid:
                key = str(pid)
                by_id[key] = by_id.get(key, 0) + qty
            pn = str(p.get("part_number") or "").strip().lower()
            if pn:
                by_pn[pn] = by_pn.get(pn, 0) + qty
    return by_id, by_pn


def linked_parts_orders_payload(shop_db, shop, wo, fmt_date=None, show_usage=True) -> list[dict]:
    """Список привязанных к WO заказов со статусами и сверкой использования.

    show_usage=False — WO ещё не принят (estimate или страница создания):
    сверка «использовано/не использовано» не имеет смысла, пока состав работ
    не финален — позиции отдаются нейтрально (usage=""), unused пустой.
    """
    from app.blueprints.parts.routes import _parts_order_amounts

    orders = list(
        shop_db.parts_orders.find({
            "shop_id": shop["_id"],
            "work_order_id": wo["_id"],
            "is_active": {"$ne": False},
            "is_return": {"$ne": True},
        }).sort([("created_at", -1)])
    )
    if not orders:
        return []

    vendor_ids = [o["vendor_id"] for o in orders if o.get("vendor_id")]
    vendor_names = {}
    if vendor_ids:
        for v in shop_db.vendors.find({"_id": {"$in": vendor_ids}}, {"name": 1}):
            vendor_names[v["_id"]] = str(v.get("name") or "-")

    used_by_id, used_by_pn = _wo_parts_usage(wo)

    out = []
    for order in orders:
        amounts = _parts_order_amounts(order)
        total = float(amounts.get("total_amount") or 0.0)
        paid = float(order.get("paid_amount") or 0.0)

        items = []
        unused = []
        for it in order.get("items") or []:
            ordered_qty = int(it.get("quantity") or 0)
            pid_key = str(it.get("part_id")) if it.get("part_id") else ""
            pn_key = str(it.get("part_number") or "").strip().lower()
            used_qty = used_by_id.get(pid_key)
            if used_qty is None:
                used_qty = used_by_pn.get(pn_key, 0)
            if not show_usage:
                usage = ""
            elif used_qty >= ordered_qty and ordered_qty > 0:
                usage = "used"
            elif used_qty > 0:
                usage = "partial"
            else:
                usage = "unused"
            if show_usage and usage != "used":
                unused.append({
                    "part_number": it.get("part_number") or "-",
                    "ordered": ordered_qty,
                    "used": int(used_qty),
                })
            items.append({
                "part_number": it.get("part_number") or "-",
                "description": it.get("description") or "",
                "quantity": ordered_qty,
                "used_qty": int(used_qty),
                "usage": usage,
            })

        order_date = order.get("order_date") or order.get("created_at")
        out.append({
            "id": str(order["_id"]),
            "order_number": order.get("order_number"),
            "vendor": vendor_names.get(order.get("vendor_id")) or "-",
            "status": str(order.get("status") or "ordered"),
            "payment_status": str(order.get("payment_status") or "unpaid"),
            "vendor_bill": str(order.get("vendor_bill") or ""),
            "total_amount": round(total, 2),
            "paid_amount": round(paid, 2),
            "remaining_balance": round(max(0.0, total - paid), 2),
            "order_date_label": fmt_date(order_date) if fmt_date else "",
            "items": items,
            "unused": unused,
        })
    return out
