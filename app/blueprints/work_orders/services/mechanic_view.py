"""
Механик-режим: сборка ответов без денег.

Пользователь без work_orders.view_costs («механик») не должен видеть цены,
стоимости и тоталы нигде — эти хелперы вычищают денежные поля из ответов
списка/деталей WO и поиска запчастей, а mechanic_wo_payload собирает
детали WO в «безденежной» форме для веб-страницы механика и мобильного
приложения.
"""
from __future__ import annotations

import logging

from bson import ObjectId
from flask import current_app, has_app_context

from app.blueprints.work_orders.services import edit_views as ev
from app.blueprints.work_orders.services.common import i32, oid, part_unit_payload
from app.blueprints.work_orders.services.lookups import customer_label, unit_label
from app.blueprints.work_orders.services.mechanic_done import done_user_ids
from app.blueprints.work_orders.services.time_tracking import summarize_wo_time

_log = logging.getLogger(__name__)

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


def _labors_with_ids(labors) -> tuple[list, bool]:
    """Копия labors, где у каждого dict-блока свой labor_id: пустой —
    выдаётся, повтор уже встреченного id (легаси-данные) — перевыдаётся
    (первое вхождение сохраняет id и тайм-логи). (labors, изменилось ли)."""
    out = []
    seen: set[str] = set()
    changed = False
    for block in labors or []:
        if not isinstance(block, dict):
            out.append(block)
            continue
        lid = str(block.get("labor_id") or "").strip()
        if not lid or lid in seen:
            block = {**block, "labor_id": str(ObjectId())}
            lid = block["labor_id"]
            changed = True
        seen.add(lid)
        out.append(block)
    return out, changed


def ensure_wo_labor_ids(shop_db, wo: dict) -> dict:
    """
    Ленивая генерация labor_id на легаси WO: проставляет отсутствующие id,
    перевыдаёт повторы одного id внутри WO и персистит это CAS-записью по
    labors_rev (+1). Возвращает документ, который реально лежит в базе после
    записи (или свежий, если записать не удалось) — из НЕГО строятся и ответ
    клиенту, и edit view. Входной dict не мутируется. Никогда не бросает.
    """
    if not isinstance(wo, dict) or not wo.get("_id"):
        return wo
    current = wo
    for _attempt in range(ev.MAX_CAS_ATTEMPTS):
        labors, changed = _labors_with_ids(current.get("labors"))
        if not changed:
            return current
        rev = ev.labors_rev(current)
        try:
            res = shop_db.work_orders.update_one(
                {"_id": current["_id"], "shop_id": current.get("shop_id"), **ev.rev_filter(rev)},
                ev.bump_rev({"$set": {"labors": labors}}),
            )
            if res.matched_count:
                return {**current, "labors": labors, "labors_rev": rev + 1}
            fresh = shop_db.work_orders.find_one({"_id": current["_id"], "shop_id": current.get("shop_id")})
        except Exception:
            _log_exception("ensure_wo_labor_ids failed (wo %s)", current.get("_id"))
            return current
        if not fresh:
            return current
        current = fresh
    _log_warning("ensure_wo_labor_ids: labors_rev kept changing (wo %s), ids not persisted", current.get("_id"))
    return current


def _log_exception(message: str, *args) -> None:
    if has_app_context():
        current_app.logger.exception(message, *args)
    else:
        _log.exception(message, *args)


def _log_warning(message: str, *args) -> None:
    if has_app_context():
        current_app.logger.warning(message, *args)
    else:
        _log.warning(message, *args)


def wo_labors_rev(shop_db, shop_id, work_order_id) -> int:
    """Текущая ревизия labors_rev WO (0 — нет WO/поля): ответы таймеров и
    правок тайм-логов отдают её после всех своих записей, чтобы клиент не
    принял собственное действие за чужую правку."""
    wo_id = oid(work_order_id)
    if not wo_id:
        return 0
    doc = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop_id}, {"labors_rev": 1})
    return ev.labors_rev(doc)


def mechanic_rev_payload(wo: dict, user_id, *, mechanic: bool) -> dict:
    """
    Дешёвый опрос «изменился ли WO» (rev-эндпоинты механика и приложения).
    Механику — без статуса: paid ему не отдаётся нигде, вместо статуса
    locked (paid / смета / подтверждён менеджером) и его личный Done;
    остальным — статус и manager_confirmed.
    """
    status = str(wo.get("status") or "open").strip().lower()
    out = {"ok": True, "labors_rev": ev.labors_rev(wo)}
    if mechanic:
        out["locked"] = status in ("paid", "estimate") or bool(wo.get("manager_confirmed"))
        out["mechanic_done"] = bool(wo.get("mechanic_done")) or oid(user_id) in done_user_ids(wo)
    else:
        out["status"] = status
        out["manager_confirmed"] = bool(wo.get("manager_confirmed"))
    return out


def mechanic_labors_payload(shop_db, shop, wo: dict, user_id) -> tuple[list[dict], dict | None]:
    """Строки WO в безденежной форме механика (labor_id, описания, запчасти,
    бакеты времени) + идущий таймер пользователя на этом WO. Та же форма —
    в деталях и в ответе сохранения механика (клиент подхватывает её без
    перечитывания)."""
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
                # Единица измерения — не деньги, механику показываем («2 gal»)
                **part_unit_payload(p.get("unit")),
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
    return labors_out, my_running_timer


def mechanic_wo_payload(shop_db, shop, wo: dict, user_id, *, view: dict | None = None) -> dict:
    """
    Детали WO для механика: без hours/rate/цен/тоталов, с labor_id и
    сводкой времени по каждой строке, плюс labors_rev.

    Сначала ensure_wo_labor_ids (CAS) — ответ строится из документа, который
    лежит в базе. view — записать load-вьюху (edit view) этого же документа:
    {"channel": "mechanic_web" | "mobile", "sid": ..., "protocol": 0 | 1,
    "can_edit": bool}; её id уходит в ответ как view_id ("" — не записана).
    """
    from app.blueprints.work_orders.services.common import format_preferred_date_label
    from app.utils.contacts import get_main_contact_email

    wo = ensure_wo_labor_ids(shop_db, wo)

    # Без фильтра is_active: WO деактивированного клиента должен открываться,
    # клиент помечается "(inactive)" в лейбле.
    customer = shop_db.customers.find_one({"_id": wo.get("customer_id")})
    unit = shop_db.units.find_one({"_id": wo.get("unit_id"), "is_active": True})

    # Записи календаря, привязанные к WO (план: когда, кто, пресеты, заметка).
    # Денег в них нет — отдаём механику как есть.
    from app.blueprints.calendar.services.work_orders import linked_appointments_for_wo

    appointments = linked_appointments_for_wo(shop_db, shop, wo["_id"])

    labors_out, my_running_timer = mechanic_labors_payload(shop_db, shop, wo, user_id)

    view_id = ""
    if view is not None:
        view_id = ev.record_view(
            shop_db, shop["_id"], wo,
            user_id=user_id, sid=view.get("sid") or "", channel=view.get("channel") or "",
            kind="load", protocol=view.get("protocol") or 0, can_edit=bool(view.get("can_edit")),
        )

    return {
        "ok": True,
        "id": str(wo["_id"]),
        "wo_number": wo.get("wo_number"),
        "status": (wo.get("status") or "open").strip().lower(),
        "manager_confirmed": bool(wo.get("manager_confirmed")),
        "customer": {
            "id": str(wo.get("customer_id") or ""),
            "label": (
                customer_label(customer)
                + (" (inactive)" if customer.get("is_active") is False else "")
            ) if customer else "-",
            "inactive": bool(customer) and customer.get("is_active") is False,
        },
        # Email клиента нужен механику для «Send for approval» (авторизация).
        "customer_email": get_main_contact_email(customer, entity_type="customer") if customer else "",
        # Для механика «Done» — его собственная отметка (или WO целиком done).
        "mechanic_done": bool(wo.get("mechanic_done")) or oid(user_id) in done_user_ids(wo),
        "unit": {
            "id": str(wo.get("unit_id") or ""),
            "label": unit_label(unit) if unit else "-",
            "vin": str((unit or {}).get("vin") or ""),
            "unit_number": str((unit or {}).get("unit_number") or ""),
            "mileage": (unit or {}).get("mileage"),
        },
        # Пробег, записанный на самом WO (снимок на момент работы).
        "wo_mileage": wo.get("mileage"),
        "date": format_preferred_date_label(wo.get("work_order_date"), wo.get("created_at")),
        "labors": labors_out,
        "appointments": appointments,
        "my_running_timer": my_running_timer,
        # Ревизия редактируемого состояния и edit view, с которой клиент
        # начинает правку (merge_protocol 1 шлёт её обратно как view_id).
        "labors_rev": ev.labors_rev(wo),
        "view_id": view_id,
    }


def find_active_unit_work_order(shop_db, shop, unit_id) -> dict | None:
    """
    WO юнита, над которым сейчас работают: статус in_progress, активный, не
    подтверждён менеджером (подтверждённый для механика закрыт — туда
    отправлять бессмысленно). Если таких несколько, берём тот, где идёт
    таймер, иначе самый свежий по updated_at.

    Зачем: второй механик сканирует VIN (или выбирает клиента и юнит
    руками) того же грузовика — ему должен открыться WO коллеги, а не
    завестись дубль. None — юнит свободен, можно создавать.
    """
    uid = oid(unit_id)
    if not uid:
        return None

    rows = list(
        shop_db.work_orders.find(
            {
                "shop_id": shop["_id"],
                "unit_id": uid,
                "is_active": True,
                "status": "in_progress",
                "manager_confirmed": {"$ne": True},
            },
            {"wo_number": 1, "status": 1, "mechanic_done": 1, "customer_id": 1,
             "updated_at": 1, "created_at": 1},
        ).sort([("updated_at", -1), ("created_at", -1)])
    )
    if not rows:
        return None

    running: dict = {}
    for log in shop_db.wo_time_logs.find(
        {
            "shop_id": shop["_id"],
            "work_order_id": {"$in": [r["_id"] for r in rows]},
            "stopped_at": None,
        },
        {"work_order_id": 1, "user_id": 1, "user_name": 1},
    ):
        running.setdefault(log.get("work_order_id"), []).append(
            (str(log.get("user_id") or "").strip(), str(log.get("user_name") or "").strip())
        )

    chosen = next((r for r in rows if running.get(r["_id"])), rows[0])
    names = _working_now_labels(running.get(chosen["_id"], []), shop)
    return {
        "id": str(chosen["_id"]),
        "wo_number": chosen.get("wo_number"),
        "status": str(chosen.get("status") or "in_progress"),
        "mechanic_done": bool(chosen.get("mechanic_done")),
        "customer_id": str(chosen.get("customer_id") or ""),
        "working_now": names,
    }


def _working_now_labels(people: list[tuple[str, str]], shop) -> list[str]:
    """
    Подписи «кто сейчас работает» по открытым таймерам [(user_id, снимок)]:
    живое имя по user_id (снимок user_name в логе устаревает после
    переименования, он — лишь запас), один человек — одна подпись (дубли
    открытых логов после гонки start), пустые отбрасываются.
    """
    from app.utils.user_names import display_name_for, load_user_names, tenant_values_for_shop

    live = load_user_names([uid for uid, _snapshot in people if uid], tenant_values=tenant_values_for_shop(shop))
    labels: list[str] = []
    seen_ids: set[str] = set()
    for uid, snapshot in people:
        if uid:
            if uid in seen_ids:
                continue
            seen_ids.add(uid)
        label = display_name_for(live, uid, snapshot, default="")
        if label and label not in labels:
            labels.append(label)
    return labels


def active_unit_work_order_message(payload: dict | None) -> str:
    """Текст для тоста/алерта: «WO #N уже в работе по этому юниту (Иван работает)»."""
    if not payload:
        return ""
    names = payload.get("working_now") or []
    who = f" ({', '.join(names)} working on it right now)" if names else ""
    return f"Work order #{payload.get('wo_number')} is already in progress for this unit{who}."


def mechanic_unit_history(shop_db, shop, unit: dict, exclude_wo_id=None, limit: int = 30) -> dict:
    """История юнита без денег: прошлые WO с работами и запчастями.

    Статус paid наружу не отдаём — для механика оплаченный WO выглядит
    как завершённый (completed); цен/тоталов в ответе нет вообще.
    """
    from app.blueprints.work_orders.services.common import format_preferred_date_label

    query = {"shop_id": shop["_id"], "unit_id": unit["_id"], "is_active": True}
    if exclude_wo_id:
        query["_id"] = {"$ne": exclude_wo_id}

    rows = (
        shop_db.work_orders.find(
            query,
            {"wo_number": 1, "status": 1, "work_order_date": 1, "created_at": 1, "labors": 1},
        )
        .sort([("created_at", -1)])
        .limit(limit)
    )

    items = []
    for wo in rows:
        labors_out = []
        for block in wo.get("labors") or []:
            if not isinstance(block, dict):
                continue
            labor_src = block.get("labor") if isinstance(block.get("labor"), dict) else {}

            parts_out = []
            for p in block.get("parts") or []:
                if not isinstance(p, dict):
                    continue
                parts_out.append({
                    "part_number": str(p.get("part_number") or "").strip(),
                    "description": str(p.get("description") or "").strip(),
                    "qty": i32(p.get("qty")) or 0,
                    **part_unit_payload(p.get("unit")),
                })

            description = str(labor_src.get("description") or "").strip()
            if not description and not parts_out:
                continue
            labors_out.append({"description": description, "parts": parts_out})

        status = str(wo.get("status") or "open").strip().lower()
        if status == "paid":
            status = "completed"

        items.append({
            "id": str(wo["_id"]),
            "wo_number": wo.get("wo_number"),
            "status": status,
            "date": format_preferred_date_label(wo.get("work_order_date"), wo.get("created_at")),
            "labors": labors_out,
        })

    return {"unit_label": unit_label(unit), "items": items}
