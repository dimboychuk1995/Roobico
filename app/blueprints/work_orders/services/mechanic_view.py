"""
Механик-режим: сборка ответов без денег.

Пользователь без work_orders.view_costs («механик») не должен видеть цены,
стоимости и тоталы нигде — эти хелперы вычищают денежные поля из ответов
списка/деталей WO и поиска запчастей, а mechanic_wo_payload собирает
детали WO в «безденежной» форме для веб-страницы механика и мобильного
приложения.
"""
from __future__ import annotations

from bson import ObjectId

from app.blueprints.work_orders.services.common import i32
from app.blueprints.work_orders.services.lookups import customer_label, unit_label
from app.blueprints.work_orders.services.time_tracking import summarize_wo_time

# Денежные ключи в элементах поиска запчастей (/work_orders/api/parts/search).
PART_SEARCH_MONEY_KEYS = (
    "average_cost",
    "selling_price",
    "has_selling_price",
    "core_cost",
    "core_has_charge",
    "misc_has_charge",
    "misc_charges",
)

# Денежные ключи в строках списка WO (get_work_orders_list).
WO_LIST_MONEY_KEYS = (
    "labor_total",
    "parts_total",
    "sales_tax_total",
    "grand_total",
    "paid_amount",
    "balance",
)


def strip_part_search_item(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in PART_SEARCH_MONEY_KEYS}


def strip_wo_list_item(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in WO_LIST_MONEY_KEYS}


def ensure_wo_labor_ids(shop_db, wo: dict) -> dict:
    """
    Ленивая генерация labor_id на легаси WO: проставляет отсутствующие id
    и один раз персистит их, чтобы таймеры работали без прогона бэкфилла.
    """
    labors = wo.get("labors") or []
    changed = False
    for block in labors:
        if isinstance(block, dict) and not str(block.get("labor_id") or "").strip():
            block["labor_id"] = str(ObjectId())
            changed = True
    if changed:
        shop_db.work_orders.update_one(
            {"_id": wo["_id"], "shop_id": wo.get("shop_id")},
            {"$set": {"labors": labors}},
        )
    return wo


def mechanic_wo_payload(shop_db, shop, wo: dict, user_id) -> dict:
    """
    Детали WO для механика: без hours/rate/цен/тоталов, с labor_id и
    сводкой времени по каждой строке.
    """
    from app.blueprints.work_orders.services.common import format_preferred_date_label
    from app.utils.contacts import get_main_contact_email

    wo = ensure_wo_labor_ids(shop_db, wo)

    customer = shop_db.customers.find_one({"_id": wo.get("customer_id"), "is_active": True})
    unit = shop_db.units.find_one({"_id": wo.get("unit_id"), "is_active": True})

    time_summary = summarize_wo_time(shop_db, shop["_id"], wo["_id"], user_id=user_id)

    labors_out = []
    my_running_timer = None
    for block in wo.get("labors") or []:
        if not isinstance(block, dict):
            continue
        labor_src = block.get("labor") if isinstance(block.get("labor"), dict) else {}
        labor_id = str(block.get("labor_id") or "")

        parts_out = []
        for p in block.get("parts") or []:
            if not isinstance(p, dict):
                continue
            parts_out.append({
                "part_id": str(p.get("part_id") or ""),
                "part_number": str(p.get("part_number") or "").strip(),
                "description": str(p.get("description") or "").strip(),
                "qty": i32(p.get("qty")) or 0,
                "one_time_part": bool(p.get("one_time_part")),
            })

        time_bucket = time_summary.get(labor_id) or {
            "total_seconds": 0,
            "completed_seconds": 0,
            "my_seconds": 0,
            "my_running": False,
            "my_started_at": "",
            "running_users": [],
            "users": {},
        }
        if time_bucket.get("my_running"):
            my_running_timer = {
                "work_order_id": str(wo["_id"]),
                "wo_number": wo.get("wo_number"),
                "labor_id": labor_id,
                "started_at": time_bucket.get("my_started_at") or "",
            }

        labors_out.append({
            "labor_id": labor_id,
            "description": str(labor_src.get("description") or "").strip(),
            "issue_description": str(labor_src.get("issue_description") or "").strip(),
            "parts": parts_out,
            "time": {
                "total_seconds": time_bucket.get("total_seconds") or 0,
                "completed_seconds": time_bucket.get("completed_seconds") or 0,
                "my_seconds": time_bucket.get("my_seconds") or 0,
                "my_running": bool(time_bucket.get("my_running")),
                "my_started_at": time_bucket.get("my_started_at") or "",
                "running_users": time_bucket.get("running_users") or [],
            },
        })

    return {
        "ok": True,
        "id": str(wo["_id"]),
        "wo_number": wo.get("wo_number"),
        "status": (wo.get("status") or "open").strip().lower(),
        "manager_confirmed": bool(wo.get("manager_confirmed")),
        "customer": {
            "id": str(wo.get("customer_id") or ""),
            "label": customer_label(customer) if customer else "-",
        },
        # Email клиента нужен механику для «Send for approval» (авторизация).
        "customer_email": get_main_contact_email(customer, entity_type="customer") if customer else "",
        "mechanic_done": bool(wo.get("mechanic_done")),
        "unit": {
            "id": str(wo.get("unit_id") or ""),
            "label": unit_label(unit) if unit else "-",
            "vin": str((unit or {}).get("vin") or ""),
            "unit_number": str((unit or {}).get("unit_number") or ""),
            "mileage": (unit or {}).get("mileage"),
        },
        "date": format_preferred_date_label(wo.get("work_order_date"), wo.get("created_at")),
        "labors": labors_out,
        "my_running_timer": my_running_timer,
    }
