"""
Сохранения WO из механик-режима и мобильного редактора: compare-and-swap по
labors_rev + трёхсторонний merge строк (labor_merge.py) + edit views
(edit_views.py).

Раньше каждое сохранение писало весь массив labors из копии клиента без
проверки версии: устаревший автосейв одного механика удалял работы другого
(пинг-понг телефонов), а менеджерский Save с телефона стирал назначения.
Теперь одно сохранение — это:

1. Роут фиксирует request_started_at и sid устройства в начале хендлера.
2. Цикл ≤ MAX_CAS_ATTEMPTS (cas_update): чтение WO → ВСЕ guard'ы этой
   попытки (paid / смета / подтверждён / payload / пробег) → base (find_base,
   один раз на запрос: выбор детерминирован по request_started_at) → merge →
   totals → $set + $inc labors_rev с фильтром {_id, shop_id, guard'ы,
   labors_rev}. Не совпало — кто-то записал раньше: перечитать, посчитать
   заново.
3. Сразу после записи — sent view (belief клиента) и, если merge принёс
   новому клиенту чужие правки, merged view записанного состояния.
4. Побочные эффекты строго после записи, от old = labors прочитанного
   документа и new = записанных: склад, коры, одометр, журнал, пуш,
   календарь. Ошибка любого — logger.exception + warnings в ответе; WO уже
   записан, ответ 200. (Отдельная запись «бухгалтерии» склада не нужна:
   у механика/мобилки от результатов склада в WO ничего не зависит.)
5. Попытки кончились — 409 concurrent_update.

API
---
    SaveRejected(payload, status)       ответ guard'а (исключение внутри prepare)
    CasPlan(update, cas_filter, extra)  что пишет одна попытка
    cas_update(shop_db, shop_id, wo_id, prepare, *, initial_wo=None) -> CasOutcome
        prepare(wo) -> CasPlan (или raise SaveRejected); ConcurrentUpdateError
        при исчерпании попыток
    MECHANIC_CAS_FILTER / MOBILE_MANAGER_CAS_FILTER / NON_MECHANIC_CAS_FILTER
    mechanic_web_guard(data, mechanic_mode=...), mobile_edit_guard(data, is_mechanic=...)
    save_mechanic_edit(...) -> (payload, http_status)
    save_mobile_manager_edit(...) -> (payload, http_status)

Ключи ответа сохранения (дополняют прежние): view_id, merged_view_id,
labors_rev (итоговая), labor_ids (1:1 со строками запроса), client_keys,
merged_foreign, merge_notes, warnings; сохранения механика ещё labors — ровно
в форме mechanic_wo_payload (с бакетами time), чтобы новый клиент подхватил
результат без перечитывания.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from flask import current_app

from app.blueprints.work_orders.services import edit_views as ev
from app.blueprints.work_orders.services.common import utcnow
from app.blueprints.work_orders.services.inventory import (
    adjust_inventory_for_part_changes,
    sync_work_order_cores,
)
from app.blueprints.work_orders.services.mechanic_done import (
    clear_mechanic_done_fields,
    mechanic_done_fields,
)
from app.blueprints.work_orders.services.mechanic_editor import (
    MILEAGE_REQUIRED_ERROR,
    mechanic_save_date_fields,
    merge_mechanic_edit,
    odometer_moves_forward,
    parse_mileage,
)
from app.blueprints.work_orders.services.mechanic_view import mechanic_labors_payload
from app.blueprints.work_orders.services.mobile_editor import (
    compute_labors_and_totals,
    merge_mobile_manager_edit,
)
from app.blueprints.work_orders.services.totals import (
    _apply_sales_tax_to_totals,
    _get_shop_sales_tax_context,
    _is_customer_taxable,
    align_totals_with_labors,
    normalize_totals_payload,
)
from app.utils import audit_log

# CAS-фильтры (кроме _id, shop_id и ревизии) — те же условия, что проверяют
# guard'ы на чтении: WO, оплаченный / превращённый в смету / подтверждённый
# менеджером между чтением и записью, сохранение не перезапишет.
MECHANIC_CAS_FILTER = {
    "is_active": True,
    "status": {"$nin": ["paid", "estimate"]},
    "manager_confirmed": {"$ne": True},
}
MOBILE_MANAGER_CAS_FILTER = {"is_active": True, "status": {"$nin": ["paid", "estimate"]}}
NON_MECHANIC_CAS_FILTER = {"is_active": True, "status": {"$ne": "paid"}}

WO_CONFIRMED_ERROR = {
    "ok": False,
    "error": "wo_confirmed",
    "message": "This work order was confirmed by a manager and is locked.",
}

INVENTORY_WARNING = "Saved, but stock levels could not be updated. Please check this work order's parts in inventory."
CORES_WARNING = "Saved, but core charges could not be synced. Please check cores for this work order."
MILEAGE_WARNING = "Saved, but the unit mileage could not be updated."
FOLLOW_UP_WARNING = "Saved, but notifications or the calendar could not be updated."


class SaveRejected(Exception):
    """Guard отклонил сохранение: готовый ответ (payload, http status)."""

    def __init__(self, payload: dict, status: int):
        super().__init__(str((payload or {}).get("error") or "rejected"))
        self.payload = payload
        self.status = status


@dataclass
class CasPlan:
    update: dict                    # update-документ без $inc labors_rev (его добавит cas_update)
    cas_filter: dict                # guard'ы попытки
    extra: dict = field(default_factory=dict)


@dataclass
class CasOutcome:
    wo: dict                        # документ, на котором прошёл CAS (состояние ДО записи)
    plan: CasPlan
    labors_rev: int                 # ревизия после записи
    attempts: int


def cas_update(shop_db, shop_id, wo_id, prepare: Callable[[dict], CasPlan], *, initial_wo=None) -> CasOutcome:
    """Цикл compare-and-swap по labors_rev: каждая попытка читает WO заново
    (первая может взять initial_wo — документ, только что прочитанный
    роутом) и заново строит запись через prepare. Нет активного WO —
    SaveRejected 404; ревизия всё время двигается — ConcurrentUpdateError."""
    base_filter = {"_id": wo_id, "shop_id": shop_id}
    for attempt in range(ev.MAX_CAS_ATTEMPTS):
        if attempt == 0 and initial_wo is not None:
            wo = initial_wo
        else:
            wo = shop_db.work_orders.find_one({**base_filter, "is_active": True})
        if not wo:
            raise SaveRejected({"ok": False, "error": "work_order_not_found"}, 404)
        plan = prepare(wo)
        rev = ev.labors_rev(wo)
        res = shop_db.work_orders.update_one(
            {**base_filter, **plan.cas_filter, **ev.rev_filter(rev)},
            ev.bump_rev(plan.update),
        )
        if res.matched_count:
            return CasOutcome(wo=wo, plan=plan, labors_rev=rev + 1, attempts=attempt + 1)
    raise ev.ConcurrentUpdateError(f"work order {wo_id}: labors_rev kept changing")


# ───────────────────────── guard'ы эндпоинтов ─────────────────────────


def _status_of(wo: dict) -> str:
    return wo.get("status") or "open"


def mechanic_web_guard(data: dict, *, mechanic_mode: bool):
    """Guard веб-эндпоинта механика (порядок проверок — как раньше)."""
    labors = (data or {}).get("labors")

    def guard(wo: dict):
        if _status_of(wo) == "paid":
            return {"ok": False, "error": "paid_cannot_edit"}, 400
        # Сметы механик не редактирует.
        if _status_of(wo) == "estimate" and mechanic_mode:
            return {"ok": False, "error": "estimate_locked_for_mechanics"}, 400
        # Подтверждённый менеджером WO механик редактировать не может.
        if wo.get("manager_confirmed") and mechanic_mode:
            return dict(WO_CONFIRMED_ERROR), 403
        if not isinstance(labors, list) or not labors:
            return {"ok": False, "error": "labors_required"}, 400
        # Пробег обязателен для механика — до любых побочных эффектов.
        if (
            mechanic_mode and wo.get("unit_id") and "unit_mileage" in data
            and parse_mileage(data.get("unit_mileage")) is None
        ):
            return dict(MILEAGE_REQUIRED_ERROR), 400
        return None

    return guard


def mobile_edit_guard(data: dict, *, is_mechanic: bool):
    """Guard мобильного редактора (механик и менеджер; порядок — как раньше)."""
    labors = (data or {}).get("labors")

    def guard(wo: dict):
        if _status_of(wo) == "paid":
            return {"ok": False, "error": "paid_cannot_edit",
                    "message": "Paid work orders cannot be edited."}, 400
        # Сметы редактируются в веб-интерфейсе: мобильный save форсит
        # open/in_progress и молча конвертировал бы смету со списанием склада.
        if _status_of(wo) == "estimate":
            return {"ok": False, "error": "estimate_web_only",
                    "message": "Estimates are edited in the web interface."}, 400
        # Подтверждённый менеджером WO механик редактировать не может
        # (менеджер может снять подтверждение — тогда правка снова доступна).
        if wo.get("manager_confirmed") and is_mechanic:
            return dict(WO_CONFIRMED_ERROR), 403
        if not isinstance(labors, list) or not labors:
            return {"ok": False, "error": "labors_required",
                    "message": "At least one labor is required."}, 400
        # Старые сборки приложения поле пробега не шлют вовсе (ключа нет) —
        # их не ломаем; новые шлют всегда, пустое/мусор отклоняем.
        if (
            is_mechanic and wo.get("unit_id") and "unit_mileage" in data
            and parse_mileage(data.get("unit_mileage")) is None
        ):
            return dict(MILEAGE_REQUIRED_ERROR), 400
        return None

    return guard


# ───────────────────────── общие куски попытки ─────────────────────────


def _labors_and_totals(shop_db, shop, wo, rows, *, data, taxable_from_request: bool):
    """compute_labors_and_totals → align → налог по ставке, зафиксированной на WO."""
    labors, totals_raw = compute_labors_and_totals(shop_db, shop, rows)
    totals = align_totals_with_labors(normalize_totals_payload(totals_raw), labors)
    existing = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    rate = existing.get("sales_tax_rate")
    if rate is None:
        rate = _get_shop_sales_tax_context(shop, shop_db).get("rate") or 0
    if taxable_from_request:
        # Мобильный редактор: флаг из запроса → сохранённый → клиент.
        if isinstance(data, dict) and "is_taxable" in data:
            is_taxable = bool(data.get("is_taxable"))
        elif "is_taxable" in existing:
            is_taxable = bool(existing.get("is_taxable"))
        else:
            is_taxable = _is_customer_taxable(shop_db, wo.get("customer_id"))
    else:
        is_taxable = existing.get("is_taxable")
        if is_taxable is None:
            is_taxable = _is_customer_taxable(shop_db, wo.get("customer_id"))
    totals = _apply_sales_tax_to_totals(totals, rate, bool(is_taxable))
    return labors, totals


def _mileage_fields(shop_db, shop_id, wo, data, set_fields) -> tuple:
    """Снимок пробега на WO (+ одометр юнита ДО этого сохранения)."""
    unit_mileage = parse_mileage((data or {}).get("unit_mileage"))
    prior_unit_mileage = None
    if unit_mileage is not None:
        unit_doc = shop_db.units.find_one({"_id": wo.get("unit_id"), "shop_id": shop_id}, {"mileage": 1})
        prior_unit_mileage = (unit_doc or {}).get("mileage")
        set_fields["mileage"] = unit_mileage
    return unit_mileage, prior_unit_mileage


def _merge_ctx(shop_db, shop_id, wo, choice, *, user_id: str, sid: str, now) -> dict:
    return {
        "user_id": user_id,
        "sid": sid,
        "now": now,
        "running_labor_ids": ev.running_labor_ids(shop_db, shop_id, wo["_id"]),
        **choice.ctx_kwargs(),
    }


# ───────────────────────── после записи ─────────────────────────


def _record_save_views(shop_db, shop_id, written: dict, result, *, user_id: str, sid: str, channel: str, protocol: int):
    """sent view (belief) + merged view (новому клиенту, если merge принёс
    чужое). Легаси office/mechanic_web всегда сохраняются консервативно —
    их вьюхи никто не читает, не пишем."""
    if channel != "mobile" and not protocol:
        return "", ""
    view_id = ev.record_view(
        shop_db, shop_id, written, user_id=user_id, sid=sid, channel=channel,
        kind="sent", protocol=protocol, rows=result.belief, minted=result.minted,
        labors_rev=written.get("labors_rev"),
    )
    merged_view_id = view_id
    if protocol and result.foreign_any:
        # Клиент подхватит записанное состояние — его следующая base.
        merged_view_id = ev.record_view(
            shop_db, shop_id, written, user_id=user_id, sid=sid, channel=channel,
            kind="merged", protocol=protocol, labors_rev=written.get("labors_rev"),
        )
    return view_id, merged_view_id


def _side_effects(shop_db, shop, wo, set_fields, user_id, *, unit_mileage, prior_unit_mileage, warnings: list):
    """Склад, коры и одометр — от old = labors прочитанного WO, new = записанных."""
    old_labors = wo.get("labors") or []
    new_labors = set_fields.get("labors") or []
    ref = {"kind": "work_order", "id": wo["_id"], "label": str(wo.get("wo_number") or "")}

    inventory = {"errors": []}
    try:
        inventory = adjust_inventory_for_part_changes(shop_db, old_labors, new_labors, user_id, ref=ref) or inventory
    except Exception:
        current_app.logger.exception("WO %s saved, but the inventory adjustment failed", wo["_id"])
        warnings.append(INVENTORY_WARNING)

    core_sync = {"errors": []}
    try:
        core_sync = sync_work_order_cores(shop_db, shop, old_labors, new_labors, user_id) or core_sync
    except Exception:
        current_app.logger.exception("WO %s saved, but the core sync failed", wo["_id"])
        warnings.append(CORES_WARNING)

    # Одометр юнита — только вперёд (меньшее значение остаётся снимком на WO).
    if unit_mileage is not None and odometer_moves_forward(prior_unit_mileage, unit_mileage):
        try:
            shop_db.units.update_one(
                {"_id": wo.get("unit_id"), "shop_id": shop["_id"], "is_active": True},
                {"$set": {"mileage": unit_mileage, "updated_at": set_fields.get("updated_at") or utcnow(),
                          "updated_by": user_id}},
            )
        except Exception:
            current_app.logger.exception("WO %s saved, but the unit odometer update failed", wo["_id"])
            warnings.append(MILEAGE_WARNING)
    return inventory, core_sync


def _merge_audit_details(result, choice, attempts: int) -> dict | None:
    """Что merge сделал со строками — в details события журнала (разбор
    «куда делась работа»). Обычное сохранение без сюрпризов — без деталей."""
    stats = result.stats or {}
    details: dict = {}
    for key in ("kept_ids", "skipped_ids", "resurrected_ids", "deleted_ids"):
        if stats.get(key):
            details["merge_" + key.replace("_ids", "_labor_ids")] = [str(x) for x in stats[key]]
    if result.notes:
        details["merge_notes"] = list(result.notes)
    if details or attempts > 1:
        details["merge_base"] = choice.reason
        details["cas_attempts"] = attempts
    return details or None


def _finish_save(shop_db, shop, outcome: CasOutcome, *, user_id, sid: str, channel: str, protocol: int,
                 audit_suffix: str, include_grand_total: bool, hide_money: bool, mechanic_labors: bool):
    from app.blueprints.calendar.services.work_orders import sync_linked_appointments_status
    from app.blueprints.work_orders.services.push_events import notify_after_mechanic_save

    wo = outcome.wo
    plan = outcome.plan
    set_fields = plan.extra["set_fields"]
    result = plan.extra["result"]
    choice = plan.extra["choice"]
    wo_id = wo["_id"]
    written = {**wo, **set_fields, "labors_rev": outcome.labors_rev}

    view_id, merged_view_id = _record_save_views(
        shop_db, shop["_id"], written, result,
        user_id=str(user_id or ""), sid=sid, channel=channel, protocol=protocol,
    )

    warnings: list[str] = []
    inventory, core_sync = _side_effects(
        shop_db, shop, wo, set_fields, user_id,
        unit_mileage=plan.extra.get("unit_mileage"),
        prior_unit_mileage=plan.extra.get("prior_unit_mileage"),
        warnings=warnings,
    )

    done_now = bool(set_fields.get("mechanic_done")) and not bool(wo.get("mechanic_done"))
    audit_log.log_event(
        "done" if done_now else "update", "work_order", wo_id, label=audit_log.wo_label(wo),
        summary=f"{audit_log.wo_label(wo)} {'marked Done' if done_now else 'saved'} {audit_suffix}",
        changes=audit_log.wo_changes(wo, set_fields),
        details=_merge_audit_details(result, choice, outcome.attempts),
    )

    try:
        notify_after_mechanic_save(shop, wo, set_fields, wo_id, wo.get("wo_number"), user_id)
    except Exception:
        current_app.logger.exception("WO %s saved, but the push notification failed", wo_id)
        warnings.append(FOLLOW_UP_WARNING)
    try:
        # Записи календаря, привязанные к WO, следуют за работой (In Progress / Completed).
        sync_linked_appointments_status(shop_db, shop, wo_id)
    except Exception:
        current_app.logger.exception("WO %s saved, but the calendar sync failed", wo_id)
        if FOLLOW_UP_WARNING not in warnings:
            warnings.append(FOLLOW_UP_WARNING)

    resp = {
        "ok": True,
        "id": str(wo_id),
        "status": set_fields.get("status") or _status_of(wo),
    }
    if include_grand_total:
        # Механик-режим — без денег.
        resp["grand_total"] = None if hide_money else set_fields["totals"].get("grand_total")
    resp.update({
        "inventory_warnings": inventory.get("errors") or [],
        "core_sync_errors": core_sync.get("errors") or [],
        "view_id": view_id,
        "merged_view_id": merged_view_id,
        "labors_rev": outcome.labors_rev,
        "labor_ids": list(result.labor_ids),
        "client_keys": dict(result.client_keys),
        "merged_foreign": bool(result.foreign_any),
        "merge_notes": list(result.notes),
        "warnings": warnings,
    })
    if mechanic_labors:
        try:
            resp["labors"] = mechanic_labors_payload(shop_db, shop, written, user_id)[0]
        except Exception:
            # Без строк новый клиент просто перечитает WO.
            current_app.logger.exception("WO %s saved, but the mechanic labors payload failed", wo_id)
            resp["merged_foreign"] = True
    return resp, 200


def _run(shop_db, shop, wo_id, prepare, *, initial_wo, finish):
    try:
        outcome = cas_update(shop_db, shop["_id"], wo_id, prepare, initial_wo=initial_wo)
    except SaveRejected as rejected:
        return rejected.payload, rejected.status
    except ev.ConcurrentUpdateError:
        current_app.logger.warning("WO %s: save gave up after %d CAS attempts", wo_id, ev.MAX_CAS_ATTEMPTS)
        return dict(ev.CONCURRENT_UPDATE_RESPONSE), 409
    return finish(outcome)


# ───────────────────────── сохранение механика ─────────────────────────


def save_mechanic_edit(
    shop_db, shop, wo_id, data, *,
    user_id, sid: str, request_started_at, channel: str, mechanic_mode: bool,
    guard, cas_filter: dict, status: str | None, taxable_from_request: bool,
    audit_suffix: str, include_grand_total: bool = False, initial_wo: dict | None = None,
):
    """
    Сохранение по MECHANIC_SPEC: веб-страница механика (channel
    "mechanic_web", доступна и менеджеру) и мобильное приложение механика
    (channel "mobile"). Деньги клиента игнорируются; hours/ставки/назначения/
    цены — из текущего WO.

    mechanic_mode — пользователь без цен: дата WO двигается по реальной
    правке, в ответе нет денег. guard(wo) -> (payload, status) | None —
    проверки эндпоинта, повторяются на каждой попытке; cas_filter — те же
    условия в фильтре записи. status — что писать в WO (None — не трогать).
    Возвращает (payload, http_status).
    """
    data = data if isinstance(data, dict) else {}
    labors_payload = data.get("labors")
    protocol = 1 if ev.is_new_client(data) else 0
    shop_id = shop["_id"]
    uid = str(user_id or "")
    memo: dict = {}

    def prepare(wo: dict) -> CasPlan:
        rejected = guard(wo)
        if rejected is not None:
            raise SaveRejected(*rejected)
        if "choice" not in memo:
            memo["choice"] = ev.find_base(
                shop_db, shop_id, wo["_id"], data=data, channel=channel, user_id=uid, sid=sid,
                request_started_at=request_started_at, mine_rows=labors_payload, is_mechanic=True,
            )
        choice = memo["choice"]
        now = utcnow()
        result = merge_mechanic_edit(
            shop_db, shop, wo, labors_payload, base=choice.rows,
            is_mechanic_web=(channel == "mechanic_web"),
            **_merge_ctx(shop_db, shop_id, wo, choice, user_id=uid, sid=sid, now=now),
        )
        labors, totals = _labors_and_totals(
            shop_db, shop, wo, result.rows, data=data, taxable_from_request=taxable_from_request,
        )
        set_fields = {"labors": labors, "totals": totals}
        if status:
            set_fields["status"] = status
        set_fields.update({"inventory_adjusted_at": now, "updated_at": now, "updated_by": user_id})
        set_fields.update(mechanic_done_fields(shop_db, shop, wo, data, user_id, now))
        unit_mileage, prior_unit_mileage = _mileage_fields(shop_db, shop_id, wo, data, set_fields)
        if mechanic_mode:
            # Дата WO (дата инвойса) = день последнего реального сохранения
            # механиком — см. mechanic_save_date_fields.
            set_fields.update(mechanic_save_date_fields(
                wo, labors, data, set_fields, user_id, now, unit_mileage=prior_unit_mileage,
            ))
        return CasPlan(
            update={"$set": set_fields},
            cas_filter=dict(cas_filter),
            extra={
                "set_fields": set_fields, "result": result, "choice": choice,
                "unit_mileage": unit_mileage, "prior_unit_mileage": prior_unit_mileage,
            },
        )

    def finish(outcome: CasOutcome):
        return _finish_save(
            shop_db, shop, outcome, user_id=user_id, sid=sid, channel=channel, protocol=protocol,
            audit_suffix=audit_suffix, include_grand_total=include_grand_total,
            hide_money=mechanic_mode, mechanic_labors=True,
        )

    return _run(shop_db, shop, wo_id, prepare, initial_wo=initial_wo, finish=finish)


# ───────────────────────── мобильный редактор менеджера ─────────────────────────


def save_mobile_manager_edit(
    shop_db, shop, wo_id, data, *,
    user_id, sid: str, request_started_at, guard, status: str | None, initial_wo: dict | None = None,
):
    """
    Сохранение менеджера из мобильного приложения (MOBILE_MANAGER_SPEC):
    тексты, денежная группа и деньги запчастей — 3-way; назначения
    механиков, hours_source и единицы существующих запчастей — из текущего
    WO. «Save In Progress» снимает отметки Done всех механиков (как в
    веб-редакторе). Возвращает (payload, http_status).
    """
    data = data if isinstance(data, dict) else {}
    labors_payload = data.get("labors")
    protocol = 1 if ev.is_new_client(data) else 0
    shop_id = shop["_id"]
    uid = str(user_id or "")
    memo: dict = {}

    def prepare(wo: dict) -> CasPlan:
        rejected = guard(wo)
        if rejected is not None:
            raise SaveRejected(*rejected)
        if "choice" not in memo:
            memo["choice"] = ev.find_base(
                shop_db, shop_id, wo["_id"], data=data, channel="mobile", user_id=uid, sid=sid,
                request_started_at=request_started_at, mine_rows=labors_payload, is_mechanic=False,
            )
        choice = memo["choice"]
        now = utcnow()
        result = merge_mobile_manager_edit(
            shop_db, shop, wo, labors_payload, base=choice.rows,
            **_merge_ctx(shop_db, shop_id, wo, choice, user_id=uid, sid=sid, now=now),
        )
        labors, totals = _labors_and_totals(shop_db, shop, wo, result.rows, data=data, taxable_from_request=True)
        set_fields = {
            "labors": labors,
            "totals": totals,
            "inventory_adjusted_at": now,
            "updated_at": now,
            "updated_by": user_id,
        }
        if status in ("open", "in_progress"):
            set_fields["status"] = status
        if status == "in_progress":
            # Менеджерское «Save In Progress»: отметки Done механиков снимаются,
            # WO снова в группе In Work (как в веб-редакторе).
            set_fields.update(clear_mechanic_done_fields())
        unit_mileage, prior_unit_mileage = _mileage_fields(shop_db, shop_id, wo, data, set_fields)
        return CasPlan(
            update={"$set": set_fields},
            cas_filter=dict(MOBILE_MANAGER_CAS_FILTER),
            extra={
                "set_fields": set_fields, "result": result, "choice": choice,
                "unit_mileage": unit_mileage, "prior_unit_mileage": prior_unit_mileage,
            },
        )

    def finish(outcome: CasOutcome):
        return _finish_save(
            shop_db, shop, outcome, user_id=user_id, sid=sid, channel="mobile", protocol=protocol,
            audit_suffix="(mobile)", include_grand_total=True, hide_money=False, mechanic_labors=False,
        )

    return _run(shop_db, shop, wo_id, prepare, initial_wo=initial_wo, finish=finish)


__all__ = [
    "CORES_WARNING",
    "CasOutcome",
    "CasPlan",
    "FOLLOW_UP_WARNING",
    "INVENTORY_WARNING",
    "MECHANIC_CAS_FILTER",
    "MILEAGE_WARNING",
    "MOBILE_MANAGER_CAS_FILTER",
    "NON_MECHANIC_CAS_FILTER",
    "SaveRejected",
    "WO_CONFIRMED_ERROR",
    "cas_update",
    "mechanic_web_guard",
    "mobile_edit_guard",
    "save_mechanic_edit",
    "save_mobile_manager_edit",
]
