"""
Сохранение WO из офиса (веб-редактор work_order_details.js): compare-and-swap
по labors_rev + трёхсторонний merge строк (labor_merge.merge_labors,
OFFICE_SPEC) + 3-way полей уровня WO.

Раньше офисный Save писал весь массив labors из формы: работы и запчасти,
которые механики добавили, пока форма была открыта, удалялись, их правки qty
откатывались, назначения бывших механиков пропадали, а устаревшая вкладка
после transfer возвращала WO старому клиенту.

Одно сохранение:
1. Роут фиксирует request_started_at и sid устройства в начале хендлера.
2. Цикл ≤ MAX_CAS_ATTEMPTS (concurrent_save.cas_update): чтение WO → guard'ы
   (paid, смета с платежами) → строки формы в хранимой форме (тот же конвейер,
   что раньше: apply_assignments_to_labors) → base (edit view вкладки, или
   None — консервативно: старая вкладка без view_id ничего не удаляет по
   отсутствию) → merge → пересборка блоков → totals → $set + $inc labors_rev
   с фильтром {_id, shop_id, is_active, status прочитанный, labors_rev}.
3. Totals:
   - быстрый путь (merge не принёс чужих строк/запчастей/денег) — ровно
     прежний код: тоталы клиента → align → налог по зафиксированной ставке;
     для строк, где tracked-правило вернуло часы таймеров, база клиента
     заменяется базой строки (как прежнее правило);
   - merged-путь — тоталы собирает сервер: база строки — клиентская, если
     деньги строки взяты из формы, иначе из строки; misc_taxable_total —
     явно (misc_taxable_total_for_labors); shop supply — значение клиента,
     если он ручной (shop_supply_overridden; старый JS — по расхождению с
     процентом > 0.005), иначе процент страницы × сумма баз.
4. Поля уровня WO (клиент, юнит, дата, налог, пробег) — 3-way по полям
   load-вьюхи: значение формы применяется, только если пользователь его
   менял; иначе остаётся текущее (устаревшая вкладка не отменит transfer и
   не сдвинет дату). Без base — как раньше (форма побеждает).
5. После записи — sent view (belief вкладки) и merged view, затем склад,
   отдельная запись «бухгалтерии» склада (без $inc labors_rev), одометр,
   коры, журнал, назначения по таймерам, календарь. Ошибка любого —
   logger.exception + warnings в ответе; WO уже записан, ответ 200.
   Ответ несёт итоговую labors_rev (перечитана после всех записей).

Старая вкладка (без merge-протокола) сохраняется консервативно, но если
merge оставил бы строку или запчасть, которой нет в её форме, Save
отклоняется целиком (200 page_outdated, ничего не пишется, склад не
двигается): нельзя ни удалить (мог добавить другой), ни оставить (могла
удалить сама вкладка — двойной счёт).

Механик (механик-режим) на этом эндпоинте сохраняет как на своей странице
(concurrent_save.save_mechanic_edit, MECHANIC_SPEC): деньги/назначения из
формы игнорируются, подтверждённый менеджером WO закрыт. Строки офисной формы
переводятся в форму механика — office_rows_as_mechanic_rows.

Записи «сбоку» (Describe Issue, правка тайм-лога) пишут в WO мимо Save;
вкладка получает производную вьюху (edit_views.derive_view_with_rows) —
свою же запись следующий Save не принимает за чужую.

Публичное API
-------------
    save_office_edit(shop_db, shop, wo_id, data, *, user_id, sid,
                     request_started_at, initial_wo=None) -> (payload, status)
    office_mechanic_guard() -> guard(wo)
    office_rows_as_mechanic_rows(rows) -> list[dict]
    office_view_wo_fields(shop_db, shop, wo, *, supply_pct=None) -> dict
    save_labor_issue_description(shop_db, shop, wo_id, labor_index, data, *,
                                 user_id) -> (payload, status)
    derive_view_after_time_log(shop_db, shop_id, wo_id, view_id, *, user_id,
                               labor_id, before_wo) -> str
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from flask import current_app
from pymongo import ReturnDocument

from app.blueprints.work_orders.services import edit_views as ev
from app.blueprints.work_orders.services.common import as_bool, f64, oid, round2, utcnow
from app.blueprints.work_orders.services.concurrent_save import (
    CORES_WARNING,
    FOLLOW_UP_WARNING,
    INVENTORY_WARNING,
    MILEAGE_WARNING,
    WO_CONFIRMED_ERROR,
    CasPlan,
    SaveRejected,
    _merge_audit_details,
    cas_update,
)
from app.blueprints.work_orders.services.inventory import (
    adjust_inventory_for_part_changes,
    deduct_parts_from_inventory,
    restore_parts_to_inventory,
    sync_work_order_cores,
)
from app.blueprints.work_orders.services.labor_merge import (
    OFFICE_SPEC,
    MergeContext,
    merge_labors,
    norm_assigned,
    norm_text,
)
from app.blueprints.work_orders.services.lookups import (
    apply_assignments_to_labors,
    get_assignable_mechanics,
    get_labor_rates,
    locked_shop_supply_pct,
)
from app.blueprints.work_orders.services.mechanic_done import clear_mechanic_done_fields
from app.blueprints.work_orders.services.mechanic_editor import parse_mileage
from app.blueprints.work_orders.services.mechanic_view import wo_labors_rev
from app.blueprints.work_orders.services.payments import _sum_active_work_order_payments
from app.blueprints.work_orders.services.totals import (
    _apply_sales_tax_to_totals,
    _get_shop_sales_tax_context,
    _is_customer_taxable,
    align_totals_with_labors,
    misc_taxable_total_for_labors,
    normalize_parts_payload,
    normalize_totals_payload,
)
from app.utils import audit_log
from app.utils.display_datetime import shop_date_input_value, shop_local_date_to_utc

CHANNEL = "office"

# Ключи плоской строки офисной формы (serializeBlocks) → поле рабочей строки merge.
_FLAT_TEXT_KEYS = (
    ("labor_description", "description"),
    ("issue_description", "issue_description"),
)
_FLAT_MONEY_KEYS = (("labor_hours", "hours"), ("labor_rate_code", "rate_code"))

_ESTIMATE_PAYMENTS_ERROR = {
    "ok": False,
    "error": "wo_has_payments_cannot_become_estimate",
    "message": "This work order has recorded payments — an estimate cannot have payments. Delete the payments first.",
}
_CALENDAR_WARNING = FOLLOW_UP_WARNING

# Старая вкладка (JS до merge-протокола: без view_id) не говорит, что она
# видела. Строку или запчасть, которой нет в её форме, сервер не может ни
# удалить (её мог добавить другой), ни молча оставить (её могла удалить
# сама вкладка — осталась бы в инвойсе, склад не вернулся бы). Такой Save
# не пишется вовсе: ответ 200 ok:false, старый JS показывает message.
PAGE_OUTDATED_RESPONSE = {
    "ok": False,
    "error": "page_outdated",
    "message": "This page was opened before Roobico was updated. Reload the page and make your change again.",
}
_UNIT_MISMATCH_MESSAGE = "The selected unit belongs to a different customer. Select a unit of this customer."
_CUSTOMER_MOVED_MESSAGE = (
    "This work order was moved to another customer by another user. Reload the page to see the change."
)


# ───────────────────────── поля уровня WO ─────────────────────────


def office_view_wo_fields(shop_db, shop, wo: dict, *, supply_pct=None) -> dict:
    """
    Поля уровня WO, которые показывает страница деталей, — для load/merged
    вьюхи офиса (база 3-way полей WO следующего Save этой вкладки):
    edit_views.wo_fields_of + work_order_date_input (дата в поле страницы),
    is_taxable (флаг налога страницы: из totals, нет ключа — False, как
    normalize_totals_payload) и shop_supply_pct (зафиксированный процент
    shop supply страницы).
    """
    wo = wo if isinstance(wo, dict) else {}
    totals = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    fields = ev.wo_fields_of(wo)
    fields["work_order_date_input"] = shop_date_input_value(
        wo.get("work_order_date") or wo.get("created_at"), default_today=True
    )
    fields["is_taxable"] = bool(totals.get("is_taxable"))
    if supply_pct is None:
        supply_pct = locked_shop_supply_pct(shop_db, shop["_id"], totals)
    fields["shop_supply_pct"] = float(supply_pct or 0)
    return fields


def _client_wo_fields(data: dict, raw_totals: dict) -> dict:
    """Поля уровня WO из запроса (None — клиент поле не прислал)."""
    return {
        "customer_id": oid(data.get("customer_id")) if data.get("customer_id") else None,
        "unit_id": oid(data.get("unit_id")) if data.get("unit_id") else None,
        "work_order_date": norm_text(data.get("work_order_date")),
        "mileage": parse_mileage(data.get("unit_mileage")),
        "is_taxable": bool(raw_totals.get("is_taxable")) if "is_taxable" in raw_totals else None,
    }


@dataclass
class _WoLevel:
    customer_id: object = None      # записать (ObjectId) или None — оставить текущий
    unit_id: object = None
    write_date: bool = True
    work_order_date: object = None
    mileage: int | None = None      # записать снимок пробега или None — не трогать
    is_taxable: bool = False        # итоговый флаг налога
    notes: list = field(default_factory=list)
    foreign: bool = False           # вкладка показывает устаревшее значение — перечитать
    # Клиента WO сменил другой пользователь (transfer), а вкладка показывает
    # прежнего: юнит прежнего клиента из этой вкладки — уже чужой.
    customer_moved: bool = False


def _merge_wo_fields(shop_db, wo: dict, client: dict, base) -> _WoLevel:
    """3-way полей уровня WO: значение формы применяется, только если оно
    отличается от того, что показала страница (base); иначе остаётся текущее.
    Без base (старая вкладка, вьюха не нашлась) — форма побеждает, как раньше."""
    base = base if isinstance(base, dict) else None
    out = _WoLevel()

    def _pick_id(key: str, label: str):
        mine = client.get(key)
        if mine is None:
            return None
        cur = norm_text(wo.get(key))
        if base is None or key not in base:
            return mine
        b = norm_text(base.get(key))
        if str(mine) == b:
            if cur != str(mine):
                out.foreign = True
                out.notes.append(f"The {label} was changed by another user.")
                if key == "customer_id":
                    out.customer_moved = True
            return None
        if cur != b and cur != str(mine):
            out.notes.append(f"The {label} was also changed by another user; your choice was kept.")
        return mine

    out.customer_id = _pick_id("customer_id", "customer")
    out.unit_id = _pick_id("unit_id", "unit")

    date_raw = client.get("work_order_date") or ""
    if base is not None and "work_order_date_input" in base and date_raw == norm_text(base.get("work_order_date_input")):
        out.write_date = False
        shown_now = shop_date_input_value(wo.get("work_order_date") or wo.get("created_at"), default_today=True)
        if shown_now != date_raw:
            out.foreign = True
            out.notes.append("The work order date was changed by another user.")
    else:
        # Как раньше: пустая дата = сегодня.
        out.work_order_date = shop_local_date_to_utc(date_raw, default_today=True)

    mileage = client.get("mileage")
    out.mileage = mileage
    # Сменили юнит — снимок пробега принадлежит новому юниту, пишется как есть.
    if mileage is not None and out.unit_id is None and base is not None and "mileage" in base:
        if parse_mileage(base.get("mileage")) == mileage:
            out.mileage = None
            if parse_mileage(wo.get("mileage")) != mileage:
                out.foreign = True
                out.notes.append("The mileage was changed by another user.")

    totals = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    mine_tax = client.get("is_taxable")
    if mine_tax is None:
        if "is_taxable" in totals:
            out.is_taxable = bool(totals.get("is_taxable"))
        else:
            out.is_taxable = _is_customer_taxable(shop_db, wo.get("customer_id"))
    elif base is None or "is_taxable" not in base or mine_tax != bool(base.get("is_taxable")):
        out.is_taxable = mine_tax
    else:
        out.is_taxable = bool(totals.get("is_taxable"))
        if out.is_taxable != mine_tax:
            out.foreign = True
            out.notes.append("The sales tax setting was changed by another user.")
    return out


def _belief_wo_fields(shop_db, shop, written: dict, client: dict, base) -> dict:
    """Что показывает вкладка после своего Save (base её следующего Save, если
    она не перечитывает WO): записанное состояние, поверх — значения формы."""
    fields = office_view_wo_fields(
        shop_db, shop, written,
        supply_pct=(base or {}).get("shop_supply_pct") if isinstance(base, dict) else None,
    )
    if client.get("customer_id") is not None:
        fields["customer_id"] = str(client["customer_id"])
    if client.get("unit_id") is not None:
        fields["unit_id"] = str(client["unit_id"])
    if client.get("work_order_date"):
        fields["work_order_date_input"] = client["work_order_date"]
    if client.get("is_taxable") is not None:
        fields["is_taxable"] = bool(client["is_taxable"])
    if client.get("mileage") is not None:
        fields["mileage"] = client["mileage"]
    return fields


def _validate_wo_level(shop_db, shop_id, wo: dict, level: _WoLevel) -> None:
    """
    Применяемые клиент/юнит существуют (ответы — как раньше), а итоговая
    пара (клиент, юнит) согласована. 3-way решает клиента и юнит по
    отдельности, поэтому пара проверяется всегда, когда пишется хотя бы
    одно из двух: смена клиента со старым юнитом (гонка селектов, API)
    отклоняется, как раньше; юнит прежнего клиента из устаревшей вкладки
    после transfer — тоже, с понятным текстом (прежнего клиента не
    применяем: строки уже переоценены под нового).
    """
    if level.customer_id:
        if not shop_db.customers.find_one(
            {"_id": level.customer_id, "shop_id": shop_id, "is_active": True}, {"_id": 1}
        ):
            raise SaveRejected({"ok": False, "error": "customer_not_found"}, 200)
    unit_doc = None
    if level.unit_id:
        unit_doc = shop_db.units.find_one(
            {"_id": level.unit_id, "shop_id": shop_id, "is_active": True}, {"_id": 1, "customer_id": 1}
        )
        if not unit_doc:
            raise SaveRejected({"ok": False, "error": "unit_not_found"}, 200)
    if not (level.customer_id or level.unit_id):
        return
    customer_id = level.customer_id or wo.get("customer_id")
    if unit_doc is None and wo.get("unit_id"):
        # Юнит остаётся текущим — сверяем его с итоговым клиентом. Активность
        # не проверяем: текущий юнит не применяется заново, а неприменённые
        # значения сохранению не мешают.
        unit_doc = shop_db.units.find_one({"_id": wo["unit_id"], "shop_id": shop_id}, {"_id": 1, "customer_id": 1})
    if unit_doc and customer_id and unit_doc.get("customer_id") != customer_id:
        moved = level.customer_moved and not level.customer_id
        raise SaveRejected({
            "ok": False,
            "error": "unit_customer_mismatch",
            "message": _CUSTOMER_MOVED_MESSAGE if moved else _UNIT_MISMATCH_MESSAGE,
        }, 200)


# ───────────────────────── строки формы → mine / пересборка ─────────────────────────


def _stored_assigned_by_labor_id(wo: dict, base_rows=None) -> dict:
    """
    {labor_id: назначения строки, которые форма может оставить без права
    назначать} — для normalize_assigned_mechanics (бывший механик, уже не
    назначаемый, остаётся, только если он уже стоит на строке).

    Это назначения строки в базе плюс назначения той же строки в base
    вкладки (компактная строка вьюхи), которых в базе уже нет. Иначе
    бывший механик, которого вкладка видела, а другой менеджер тем временем
    снял, выпал бы из mine: mine разошёлся бы с base, и 3-way принял бы
    нетронутую строку за правку этой вкладки — откатил бы чужое
    переназначение на усечённое (payroll).
    """
    out = {}
    for block in wo.get("labors") or []:
        if not isinstance(block, dict):
            continue
        lid = norm_text(block.get("labor_id"))
        if not lid or lid in out:
            continue
        labor = block.get("labor") if isinstance(block.get("labor"), dict) else {}
        out[lid] = list(labor.get("assigned_mechanics") or block.get("assigned_mechanics") or [])
    for row in base_rows or []:
        if not isinstance(row, dict):
            continue
        lid = norm_text(row.get("labor_id"))
        if not lid:
            continue
        current = out.get(lid) or []
        on_row = {norm_text(a.get("user_id") or a.get("id")) for a in current if isinstance(a, dict)}
        seen_by_tab = [
            a for a in (row.get("assigned_mechanics") or [])
            if isinstance(a, dict) and norm_text(a.get("user_id")) and norm_text(a.get("user_id")) not in on_row
        ]
        if seen_by_tab:
            # Текущие — первыми: имя/роль из базы приоритетнее снимка вьюхи.
            out[lid] = list(current) + seen_by_tab
    return out


def _sent(raw: dict, flat_key: str, labor_key: str) -> bool:
    nested = raw.get("labor") if isinstance(raw.get("labor"), dict) else None
    if nested is not None:
        return labor_key in nested
    return flat_key in raw


def _office_mine(payload_rows: list, client_totals: dict, mechanics_by_id: dict, wo: dict, base_rows=None):
    """
    Строки формы → (mine для merge_labors, шаблоны хранимых блоков).

    Шаблон — блок ровно в том виде, в каком его раньше писал офисный Save
    (apply_assignments_to_labors: labor-словарь, нормализованные назначения
    и запчасти, labor_full_total формы); после merge в нём меняются только
    поля, которые merge решил иначе, — Save без конфликтов пишет то же, что
    раньше. labor_total рабочей строки — база строки из тоталов клиента.
    Поле, которого строка не прислала, merge считает «не менялось».
    base_rows — строки base вкладки (бывшие механики, которых она видела,
    см. _stored_assigned_by_labor_id).
    """
    processed = apply_assignments_to_labors(
        copy.deepcopy(payload_rows), mechanics_by_id,
        stored_assigned=_stored_assigned_by_labor_id(wo, base_rows),
    )
    client_blocks = client_totals.get("labors") if isinstance(client_totals.get("labors"), list) else []
    raw_rows = [r for r in payload_rows if isinstance(r, dict)]
    mine, templates = [], []
    for i, block in enumerate(processed):
        raw = raw_rows[i] if i < len(raw_rows) else {}
        hours_edited = as_bool(block.pop("hours_edited", False))
        block.pop("hours_source", None)        # клиентское значение не авторитетно
        block.pop("minted", None)              # метку ставит только сервер
        labor = block.get("labor") if isinstance(block.get("labor"), dict) else None
        if labor is not None:
            labor.pop("hours_edited", None)
            labor.pop("hours_source", None)
            if hours_edited:
                labor["hours_source"] = ""
        templates.append(block)

        row = {
            "labor_id": norm_text(block.get("labor_id")),
            "hours_edited": hours_edited,
            "parts": copy.deepcopy(block.get("parts") or []),
        }
        ck = norm_text(block.get("client_key"))
        if ck:
            row["client_key"] = ck
        if labor is not None:
            for flat_key, key in _FLAT_TEXT_KEYS:
                if _sent(raw, flat_key, key):
                    row[key] = labor.get(key)
            money_sent = False
            for flat_key, key in _FLAT_MONEY_KEYS:
                if _sent(raw, flat_key, key):
                    row[key] = labor.get(key) or ""
                    money_sent = True
            if money_sent:
                bt = client_blocks[i] if i < len(client_blocks) and isinstance(client_blocks[i], dict) else {}
                row["labor_total"] = round2(bt.get("labor") if bt.get("labor") is not None else bt.get("labor_total"))
            if _sent(raw, "assigned_mechanics", "assigned_mechanics"):
                row["assigned_mechanics"] = copy.deepcopy(labor.get("assigned_mechanics") or [])
        mine.append(row)
    return mine, templates


def _billable_row(row) -> bool:
    """В строке (рабочая форма merge) есть что выставить в счёт: часы, база
    работы или запчасти."""
    if not isinstance(row, dict):
        return False
    hours = f64(norm_text(row.get("hours"))) or 0.0
    total = f64(row.get("labor_total")) or 0.0
    return hours > 0 or total > 0 or any(isinstance(p, dict) for p in (row.get("parts") or []))


def _keep_raw(template_value, merged_value):
    """Текст хранится как прислала форма; меняем, только если merge дал другое."""
    if template_value is not None and norm_text(template_value) == norm_text(merged_value):
        return template_value
    return norm_text(merged_value)


def _stored_assignments(merged, template) -> list:
    if norm_assigned(merged) == norm_assigned(template):
        return copy.deepcopy(template or [])
    out = []
    for a in merged if isinstance(merged, list) else []:
        if not isinstance(a, dict):
            continue
        a = dict(a)
        uid = oid(a.get("user_id") or a.get("id"))
        if uid:
            a["user_id"] = uid
        out.append(a)
    return out


def _office_block(row: dict, template: dict) -> dict:
    """Строка результата merge → хранимый блок (по шаблону строки формы)."""
    block = copy.deepcopy(template)
    labor = block.get("labor") if isinstance(block.get("labor"), dict) else None
    if labor is None:
        labor = {}
        block["labor"] = labor
    block["labor_id"] = norm_text(row.get("labor_id"))
    labor["description"] = norm_text(row.get("description"))
    issue = row.get("issue_description")
    labor["issue_description"] = _keep_raw(labor.get("issue_description"), issue)
    if "issue_description" in block:
        block["issue_description"] = _keep_raw(block.get("issue_description"), issue)
    labor["hours"] = str(row.get("hours") or "").strip()
    labor["rate_code"] = str(row.get("rate_code") or "").strip()
    assigned = _stored_assignments(row.get("assigned_mechanics"), labor.get("assigned_mechanics"))
    labor["assigned_mechanics"] = assigned
    block["assigned_mechanics"] = assigned
    source = norm_text(row.get("hours_source"))
    if source:
        labor["hours_source"] = source
    elif "hours_source" in labor:
        labor["hours_source"] = ""
    block["parts"] = normalize_parts_payload(row.get("parts") or [])
    block.pop("client_key", None)
    block.pop("minted", None)
    ck = norm_text(row.get("client_key"))
    if ck:
        block["client_key"] = ck
    if isinstance(row.get("minted"), dict):
        block["minted"] = dict(row["minted"])
    return block


def _rebuild_blocks(result, templates: list, current_blocks: list) -> list:
    blocks = []
    for row, origin in zip(result.rows, result.origins):
        mi = origin.get("mine_index")
        if mi is None:
            # Строка, которой вкладка не знает (добавлена/изменена другими), — как лежит.
            blocks.append(copy.deepcopy(current_blocks[origin["current_index"]]))
        else:
            blocks.append(_office_block(row, templates[mi]))
    return blocks


# ───────────────────────── тоталы ─────────────────────────


def _fast_path(result, mine_count: int) -> bool:
    """Merge не принёс чужих строк/запчастей/денег: итог — строки формы в её порядке."""
    if result.foreign_structure or len(result.rows) != mine_count:
        return False
    return all(o.get("mine_index") == i for i, o in enumerate(result.origins))


def _stored_block_totals(wo: dict) -> list:
    """Сохранённые totals-блоки, выровненные со строками current (dict-блоки labors)."""
    totals = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    blocks = totals.get("labors") if isinstance(totals.get("labors"), list) else []
    out = []
    for pos, block in enumerate(wo.get("labors") or []):
        if isinstance(block, dict):
            bt = blocks[pos] if pos < len(blocks) else None
            out.append(bt if isinstance(bt, dict) else None)
    return out


def _fast_totals(raw_totals: dict, result, labors: list, stored_block_totals: list) -> dict:
    """Прежний путь: тоталы формы; база tracked-строк — из строки (часы таймеров)."""
    totals = normalize_totals_payload(raw_totals)
    client_blocks = totals["labors"]
    tracked = set(result.tracked_adjusted)
    for k, (row, origin) in enumerate(zip(result.rows, result.origins)):
        if norm_text(row.get("labor_id")) not in tracked or k >= len(client_blocks):
            continue
        ci = origin.get("current_index")
        if ci is None or ci >= len(stored_block_totals) or stored_block_totals[ci] is None:
            continue
        client_blocks[k]["labor"] = round2(row.get("labor_total") or 0)
    return align_totals_with_labors(totals, labors)


def _server_labor_base(row: dict, rates: dict) -> float:
    """База строки, как её считает сервер (compute_labors_and_totals)."""
    manual = f64(row.get("labor_total"))
    if manual is not None and manual > 0:
        return round2(manual)
    hours = f64(norm_text(row.get("hours"))) or 0.0
    return round2(hours * float(rates.get(norm_text(row.get("rate_code"))) or 0))


def _supply_overridden(data: dict, client: dict, supply_pct: float) -> bool:
    if "shop_supply_overridden" in data:
        return as_bool(data.get("shop_supply_overridden"))
    # Старый JS флага не шлёт: ручное значение — расхождение с процентом страницы.
    expected = round2(round2(client.get("labor")) * (supply_pct / 100.0)) if supply_pct > 0 else 0.0
    return abs(round2(client.get("shop_supply_total")) - expected) > 0.005


def _merged_totals(raw_totals: dict, data: dict, result, labors: list, *, rates: dict, supply_pct: float) -> dict:
    """Тоталы собирает сервер: в Save влились чужие строки/запчасти/деньги."""
    client = normalize_totals_payload(raw_totals)
    client_blocks = client["labors"]
    tracked = set(result.tracked_adjusted)
    bases = []
    for row, origin in zip(result.rows, result.origins):
        mi = origin.get("mine_index")
        if (
            mi is not None and origin.get("money_from_mine")
            and norm_text(row.get("labor_id")) not in tracked and mi < len(client_blocks)
        ):
            bases.append(round2(client_blocks[mi].get("labor")))
        else:
            bases.append(_server_labor_base(row, rates))
    labor_sum = round2(sum(bases))
    if _supply_overridden(data, client, supply_pct):
        supply = round2(client.get("shop_supply_total"))
    else:
        supply = round2(labor_sum * (supply_pct / 100.0)) if supply_pct > 0 else 0.0
    totals_input = {
        k: v for k, v in client.items() if k not in ("parts_taxable_total", "grand_total", "sales_tax_total")
    }
    totals_input["labors"] = [{"labor": b} for b in bases]
    # ЯВНО: align/normalize превращают отсутствующий ключ в 0 (налог с misc пропал бы).
    totals_input["misc_taxable_total"] = misc_taxable_total_for_labors(labors)
    totals_input["shop_supply_total"] = supply
    return align_totals_with_labors(totals_input, labors)


def _refresh_full_totals(labors: list, result, raw_totals: dict, totals: dict) -> None:
    """merged-путь: labor_full_total строк формы, чьи деньги изменились, — из
    итоговых тоталов (без supply, как считает страница); остальным — как прислано."""
    client_blocks = normalize_totals_payload(raw_totals)["labors"]
    aligned = totals.get("labors") if isinstance(totals.get("labors"), list) else []
    for k, (block, origin) in enumerate(zip(labors, result.origins)):
        mi = origin.get("mine_index")
        if mi is None or k >= len(aligned) or not isinstance(block.get("labor"), dict):
            continue
        ab = aligned[k]
        cb = client_blocks[mi] if mi < len(client_blocks) else None
        keys = ("labor", "parts", "core_total", "misc_total")
        if cb is not None and all(round2(ab.get(x)) == round2(cb.get(x)) for x in keys):
            continue
        block["labor"]["labor_full_total"] = round2(round2(ab.get("labor")) + round2(ab.get("parts_total")))


def _locked_tax_rate(shop_db, shop, wo: dict):
    existing = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    rate = existing.get("sales_tax_rate")
    if rate is None:
        # Легаси-WO без зафиксированной ставки — текущая ставка магазина.
        rate = _get_shop_sales_tax_context(shop, shop_db).get("rate") or 0
    return rate


# ───────────────────────── сохранение ─────────────────────────


def save_office_edit(shop_db, shop, wo_id, data, *, user_id, sid: str, request_started_at, initial_wo=None):
    """
    Офисный Save (пользователь не в механик-режиме): merge строк, 3-way полей
    WO, тоталы, CAS, побочные эффекты после записи. Ответ — прежние ключи +
    view_id, merged_view_id, labors_rev (итоговая), client_keys,
    merged_foreign, merge_notes, warnings. Возвращает (payload, http_status).
    """
    data = data if isinstance(data, dict) else {}
    payload_rows = data.get("labors", data.get("blocks"))
    if not isinstance(payload_rows, list):
        return {"ok": False, "error": "labors_required"}, 200
    raw_totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
    client_totals = normalize_totals_payload(raw_totals)
    client_fields = _client_wo_fields(data, raw_totals)
    save_status = norm_text(data.get("save_status")).lower()
    protocol = 1 if ev.is_new_client(data) else 0
    shop_id = shop["_id"]
    uid = str(user_id or "")
    mechanics_by_id = {m["id"]: m for m in get_assignable_mechanics(shop)}
    rates = {r["code"]: r["hourly_rate"] for r in get_labor_rates(shop_db, shop_id)}
    memo: dict = {}

    def prepare(wo: dict) -> CasPlan:
        status = wo.get("status") or "open"
        if status == "paid":
            raise SaveRejected({"ok": False, "error": "paid_cannot_edit"}, 200)
        # Смета живёт без движения склада: остаётся сметой, пока её явно не
        # конвертировали; WO → смета — только без платежей.
        was_estimate = status == "estimate"
        to_estimate = save_status == "estimate" and not was_estimate
        if to_estimate and _sum_active_work_order_payments(shop_db, wo["_id"]) > 0:
            raise SaveRejected(dict(_ESTIMATE_PAYMENTS_ERROR), 200)
        from_estimate = was_estimate and save_status in ("open", "in_progress")
        stays_estimate = was_estimate and not from_estimate

        if "choice" not in memo:
            memo["choice"] = ev.find_base(
                shop_db, shop_id, wo["_id"], data=data, channel=CHANNEL, user_id=uid, sid=sid,
                request_started_at=request_started_at, mine_rows=payload_rows, is_mechanic=False,
            )
        choice = memo["choice"]
        level = _merge_wo_fields(shop_db, wo, client_fields, choice.wo_fields)
        _validate_wo_level(shop_db, shop_id, wo, level)

        mine, templates = _office_mine(payload_rows, client_totals, mechanics_by_id, wo, choice.rows)
        now = utcnow()
        ctx = {
            **choice.ctx_kwargs(),
            "user_id": uid,
            "sid": sid,
            "now": now,
            "running_labor_ids": ev.running_labor_ids(shop_db, shop_id, wo["_id"]),
            "rates": rates,
        }
        ctx["extra_notes"] = tuple(ctx.get("extra_notes") or ()) + tuple(level.notes)
        ctx["force_foreign"] = bool(ctx.get("force_foreign")) or level.foreign
        result = merge_labors(choice.rows, ev.work_rows_from_wo(wo), mine, OFFICE_SPEC, ctx=MergeContext(**ctx))
        if not protocol:
            # Тихо оставленные строки (пустые; дубль labor_id легаси-WO) в счёт
            # не идут, только если в них правда нечего выставить.
            kept_jobs = len(result.stats.get("kept_ids") or []) + sum(
                1 for row, origin in zip(result.rows, result.origins)
                if origin.get("kind") == "kept_silent" and _billable_row(row)
            )
            kept_parts = int(result.stats.get("parts_kept") or 0)
            if kept_jobs or kept_parts:
                # Старая вкладка не прислала строку/запчасть, которая есть в
                # WO: удалила ли её вкладка или добавил кто-то другой — не
                # узнать. Оставить — двойной счёт и склад не вернётся;
                # удалить — потерять чужую работу. Не пишем ничего.
                current_app.logger.warning(
                    "WO %s: save from a page opened before the merge protocol refused (page_outdated): "
                    "it would keep %d job(s) and %d part(s) the page did not send",
                    wo["_id"], kept_jobs, kept_parts,
                )
                raise SaveRejected(dict(PAGE_OUTDATED_RESPONSE), 200)

        current_blocks = [b for b in (wo.get("labors") or []) if isinstance(b, dict)]
        labors = _rebuild_blocks(result, templates, current_blocks)
        fast = _fast_path(result, len(mine))
        if fast:
            totals = _fast_totals(raw_totals, result, labors, _stored_block_totals(wo))
        else:
            supply_pct = (choice.wo_fields or {}).get("shop_supply_pct") if isinstance(choice.wo_fields, dict) else None
            if supply_pct is None:
                supply_pct = locked_shop_supply_pct(shop_db, shop_id, wo.get("totals"))
            totals = _merged_totals(raw_totals, data, result, labors, rates=rates, supply_pct=float(supply_pct or 0))
            _refresh_full_totals(labors, result, raw_totals, totals)
        totals = _apply_sales_tax_to_totals(totals, _locked_tax_rate(shop_db, shop, wo), level.is_taxable)

        set_fields = {
            "labors": labors,
            "totals": totals,
            "inventory_adjusted_at": now,
            "updated_at": now,
            "updated_by": user_id,
        }
        if level.write_date:
            set_fields["work_order_date"] = level.work_order_date
        if level.mileage is not None:
            set_fields["mileage"] = level.mileage
        if level.customer_id:
            set_fields["customer_id"] = level.customer_id
        if level.unit_id:
            set_fields["unit_id"] = level.unit_id
        if to_estimate:
            set_fields["inventory_deducted"] = False
            set_fields["inventory_deductions"] = []
        if save_status in ("open", "in_progress"):
            set_fields["status"] = save_status
        elif to_estimate:
            set_fields["status"] = "estimate"
        if save_status == "in_progress":
            # Менеджерское «Save In Progress» возвращает WO в работу: отметки
            # Done всех механиков снимаются, WO снова в группе In Work.
            set_fields.update(clear_mechanic_done_fields())
        unset = {"blocks": "", "labor_total": "", "parts_total": "", "grand_total": ""}
        if level.unit_id and level.unit_id != wo.get("unit_id") and level.mileage is None:
            # Юнит сменили, новый пробег не ввели — снимок чужого юнита не оставляем.
            unset["mileage"] = ""
        return CasPlan(
            update={"$set": set_fields, "$unset": unset},
            # Статус — в фильтре как прочитан: решения про склад (смета или
            # нет) приняты по нему; paid между чтением и записью не пройдёт.
            cas_filter={"is_active": True, "status": wo.get("status")},
            extra={
                "set_fields": set_fields, "unset": unset, "result": result, "choice": choice, "level": level,
                "to_estimate": to_estimate, "from_estimate": from_estimate, "stays_estimate": stays_estimate,
                "fast": fast,
            },
        )

    try:
        outcome = cas_update(shop_db, shop_id, wo_id, prepare, initial_wo=initial_wo)
    except SaveRejected as rejected:
        return rejected.payload, rejected.status
    except ev.ConcurrentUpdateError:
        current_app.logger.warning("WO %s: office save gave up after %d CAS attempts", wo_id, ev.MAX_CAS_ATTEMPTS)
        return dict(ev.CONCURRENT_UPDATE_RESPONSE), 409
    return _finish_office_save(
        shop_db, shop, outcome, client_fields=client_fields, mechanics_by_id=mechanics_by_id,
        user_id=user_id, sid=sid, protocol=protocol,
    )


def _finish_office_save(shop_db, shop, outcome, *, client_fields, mechanics_by_id, user_id, sid, protocol):
    from app.blueprints.calendar.services.work_orders import (
        refresh_linked_appointments_identity,
        sync_linked_appointments_status,
    )
    from app.blueprints.work_orders.services import time_tracking

    wo = outcome.wo
    x = outcome.plan.extra
    set_fields, result, choice, level = x["set_fields"], x["result"], x["choice"], x["level"]
    shop_id = shop["_id"]
    wo_id = wo["_id"]
    uid = str(user_id or "")
    written = {**wo, **set_fields, "labors_rev": outcome.labors_rev}
    for key in x["unset"]:
        written.pop(key, None)
    merged_foreign = bool(result.foreign_any)

    # Вьюхи — сразу после записи (до побочных эффектов). Старая вкладка без
    # view_id сохраняется консервативно — её вьюх никто не читает.
    view_id = merged_view_id = ""
    if protocol:
        view_id = ev.record_view(
            shop_db, shop_id, written, user_id=uid, sid=sid, channel=CHANNEL, kind="sent", protocol=1,
            rows=result.belief, minted=result.minted, labors_rev=outcome.labors_rev,
            wo_fields=_belief_wo_fields(shop_db, shop, written, client_fields, choice.wo_fields),
        )
        merged_view_id = view_id
        if merged_foreign:
            merged_view_id = ev.record_view(
                shop_db, shop_id, written, user_id=uid, sid=sid, channel=CHANNEL, kind="merged", protocol=1,
                labors_rev=outcome.labors_rev, wo_fields=office_view_wo_fields(shop_db, shop, written),
            )

    warnings: list[str] = []

    def _warn(message: str):
        if message not in warnings:
            warnings.append(message)

    old_labors = wo.get("labors") or []
    new_labors = set_fields["labors"]
    ref = {"kind": "work_order", "id": wo_id, "label": str(wo.get("wo_number") or "")}
    inventory = {"adjusted": [], "errors": []}
    try:
        if x["to_estimate"]:
            # WO → смета: на склад возвращается всё, что было в WO.
            restore = restore_parts_to_inventory(shop_db, old_labors, user_id, ref=ref)
            inventory = {"adjusted": restore.get("restored") or [], "errors": restore.get("errors") or []}
        elif x["from_estimate"]:
            # Конверсия сметы: списываются ВСЕ парты, как при создании WO.
            deducted = deduct_parts_from_inventory(
                shop_db, new_labors, user_id, ref={"kind": "work_order", "label": str(wo.get("wo_number") or "")},
            )
            inventory = {
                "adjusted": deducted.get("deducted") or [],
                "errors": deducted.get("errors") or [],
                "deducted": deducted.get("deducted") or [],
            }
        elif not x["stays_estimate"]:
            inventory = adjust_inventory_for_part_changes(shop_db, old_labors, new_labors, user_id, ref=ref) or inventory
    except Exception:
        current_app.logger.exception("WO %s saved, but the inventory adjustment failed", wo_id)
        _warn(INVENTORY_WARNING)

    # «Бухгалтерия» склада на WO зависит от результатов склада — отдельной
    # записью по _id; labors_rev она не двигает (строки работ не меняются).
    follow: dict = {}
    if x["from_estimate"]:
        follow["$set"] = {
            "inventory_deducted": len(inventory.get("deducted") or []) > 0,
            "inventory_deductions": inventory.get("deducted") or [],
        }
    adjusted_count = len(inventory.get("adjusted") or [])
    if adjusted_count:
        follow["$inc"] = {"inventory_adjustment_count": adjusted_count}
    if follow:
        try:
            shop_db.work_orders.update_one({"_id": wo_id, "shop_id": shop_id}, follow)
        except Exception:
            current_app.logger.exception("WO %s saved, but the inventory bookkeeping update failed", wo_id)
            _warn(INVENTORY_WARNING)

    # Одометр юнита — только вперёд и только если пробег на WO поменяли:
    # открыли старый WO и нажали Save — юнит не откатывается.
    if level.mileage is not None:
        unit_id = level.unit_id or wo.get("unit_id")
        if unit_id and level.mileage != wo.get("mileage"):
            try:
                unit_doc = shop_db.units.find_one({"_id": unit_id, "shop_id": shop_id}, {"mileage": 1}) or {}
                current_unit_mileage = parse_mileage(unit_doc.get("mileage"))
                if current_unit_mileage is None or level.mileage >= current_unit_mileage:
                    shop_db.units.update_one(
                        {"_id": unit_id, "shop_id": shop_id, "is_active": True},
                        {"$set": {"mileage": level.mileage, "updated_at": set_fields["updated_at"],
                                  "updated_by": user_id}},
                    )
            except Exception:
                current_app.logger.exception("WO %s saved, but the unit odometer update failed", wo_id)
                _warn(MILEAGE_WARNING)

    core_sync = {"changes": [], "errors": []}
    try:
        if x["to_estimate"]:
            core_sync = sync_work_order_cores(shop_db, shop, old_labors, [], user_id)
        elif x["from_estimate"]:
            core_sync = sync_work_order_cores(shop_db, shop, [], new_labors, user_id)
        elif not x["stays_estimate"]:
            core_sync = sync_work_order_cores(shop_db, shop, old_labors, new_labors, user_id)
    except Exception:
        current_app.logger.exception("WO %s saved, but the core sync failed", wo_id)
        _warn(CORES_WARNING)

    details = {"saved_status": set_fields.get("status") or wo.get("status")}
    details.update(_merge_audit_details(result, choice, outcome.attempts) or {})
    audit_log.log_event("update", "work_order", wo_id, label=audit_log.wo_label(wo),
                        changes=audit_log.wo_changes(wo, set_fields), details=details)

    # Строки с затреканным временем: assigned = кто фактически работал,
    # пропорционально времени — ручной ввод их не перетирает.
    try:
        time_tracking.sync_labor_assignments_from_time(shop_db, shop, wo_id, mechanics_by_id=mechanics_by_id)
    except Exception:
        current_app.logger.exception("WO %s saved, but the time-based assignment sync failed", wo_id)
        _warn(FOLLOW_UP_WARNING)
    # Привязанные записи календаря: статус по сохранённому WO, клиент/юнит —
    # если их поменяли.
    try:
        if level.customer_id or level.unit_id:
            refresh_linked_appointments_identity(shop_db, shop, wo_id)
        sync_linked_appointments_status(shop_db, shop, wo_id)
    except Exception:
        current_app.logger.exception("WO %s saved, but the calendar sync failed", wo_id)
        _warn(_CALENDAR_WARNING)

    return {
        "ok": True,
        "status": set_fields.get("status") or (wo.get("status") or "open"),
        # Порядок — как у присланных строк: веб-форма подхватывает id, выданные
        # сервером новым строкам (повторный Save не плодит новые id).
        "labor_ids": list(result.labor_ids),
        "inventory_adjusted": len(inventory.get("adjusted") or []) > 0,
        "inventory_changes": inventory.get("adjusted") or [],
        "inventory_warnings": list(inventory.get("errors") or []),
        "cores_synced": len(core_sync.get("changes") or []) > 0,
        "core_changes": core_sync.get("changes") or [],
        "core_sync_errors": core_sync.get("errors") or [],
        "view_id": view_id,
        "merged_view_id": merged_view_id,
        # Итоговая ревизия — после синка назначений (он тоже двигает её).
        "labors_rev": wo_labors_rev(shop_db, shop_id, wo_id),
        "client_keys": dict(result.client_keys),
        "merged_foreign": merged_foreign,
        "merge_notes": list(result.notes),
        "warnings": warnings,
    }, 200


# ───────────────────────── механик на офисном эндпоинте ─────────────────────────


def office_mechanic_guard():
    """Guard механика на офисном Save: прежние ответы офиса (200) + замок
    подтверждённого менеджером WO (как на эндпоинтах механика)."""

    def guard(wo: dict):
        status = wo.get("status") or "open"
        if status == "paid":
            return {"ok": False, "error": "paid_cannot_edit"}, 200
        # Сметы — офисный документ: механик их не редактирует.
        if status == "estimate":
            return {"ok": False, "error": "estimate_locked_for_mechanics"}, 200
        if wo.get("manager_confirmed"):
            return dict(WO_CONFIRMED_ERROR), 403
        return None

    return guard


def office_rows_as_mechanic_rows(rows) -> list[dict]:
    """Строки офисной формы (плоские или с labor-словарём) → строки механика:
    только тексты работ и состав/qty/описания запчастей (деньги, часы и
    назначения механик не правит)."""
    out = []
    for raw in rows if isinstance(rows, list) else []:
        if not isinstance(raw, dict):
            continue
        labor = raw.get("labor") if isinstance(raw.get("labor"), dict) else {}
        row = {"labor_id": norm_text(raw.get("labor_id"))}
        description = raw.get("labor_description", labor.get("description", raw.get("description")))
        if description is not None:
            row["description"] = norm_text(description)
        issue = raw.get("issue_description", labor.get("issue_description"))
        if issue is not None:
            row["issue_description"] = norm_text(issue)
        ck = norm_text(raw.get("client_key"))
        if ck:
            row["client_key"] = ck
        row["parts"] = [
            {
                "part_id": norm_text(p.get("part_id")),
                "part_number": norm_text(p.get("part_number")),
                "description": norm_text(p.get("description")),
                "qty": p.get("qty"),
            }
            for p in (raw.get("parts") or [])
            if isinstance(p, dict)
        ]
        out.append(row)
    return out


# ───────────────────────── Describe Issue одной строки ─────────────────────────


def save_labor_issue_description(shop_db, shop, wo_id, labor_index: int, data, *, user_id):
    """
    Текст «Describe issue» одной строки WO. Возвращает (payload, status).

    Новый JS шлёт labor_id строки: запись по labor_id (array_filters) + $inc
    labors_rev; строки уже нет — 404 labor_not_found. Пустой labor_id (строку
    ещё не сохранили) — в базу не пишем: текст уйдёт ближайшим Save формы.
    Старый JS (только индекс в URL) — позиционная запись CAS по ревизии
    прочитанного массива, никогда за его концом. В ответе — итоговая labors_rev.

    view_id в теле — текущая база вкладки (editViewId). Текст пишется в WO
    сразу, а форма не перечитывается: без новой базы следующий Save вкладки
    принял бы свой же текст за правку (откатил бы более новый текст механика,
    воскресил бы удалённую механиком работу, не дал бы удалить строку). После
    записи из этой вьюхи выводится новая — с этим текстом у строки
    (derive_view_with_rows); ответ несёт её view_id ("" — вьюха не нашлась,
    ничего не выведено; JS тогда оставляет прежнюю).
    """
    data = data if isinstance(data, dict) else {}
    text = str(data.get("text") or "").strip()
    if len(text) > 4000:
        text = text[:4000]
    shop_id = shop["_id"]
    base_filter = {"_id": wo_id, "shop_id": shop_id, "is_active": True}
    wo = shop_db.work_orders.find_one(base_filter, ev.with_rev({"labors": 1, "wo_number": 1, "status": 1}))
    if not wo:
        return {"ok": False, "error": "Work order not found"}, 404
    now = utcnow()

    if "labor_id" in data:
        labor_id = norm_text(data.get("labor_id"))
        if not labor_id:
            return {"ok": True, "saved": False, "issue_description": text, "labors_rev": ev.labors_rev(wo)}, 200
        doc = shop_db.work_orders.find_one_and_update(
            {**base_filter, "labors.labor_id": labor_id},
            ev.bump_rev({"$set": {
                "labors.$[b].labor.issue_description": text,
                "labors.$[b].issue_description": text,
                "updated_at": now,
                "updated_by": user_id,
            }}),
            array_filters=[{"b.labor_id": labor_id}],
            projection=ev.with_rev({"labors.labor_id": 1}),
            return_document=ReturnDocument.AFTER,
        )
        if not doc:
            return {
                "ok": False, "error": "labor_not_found",
                "message": "This job is no longer on the work order. Reload the page.",
            }, 404
        ids = [norm_text(b.get("labor_id")) for b in (doc.get("labors") or []) if isinstance(b, dict)]
        position = ids.index(labor_id) if labor_id in ids else None
        audit_log.log_event(
            "update", "work_order", wo_id, label=audit_log.wo_label(wo),
            summary=f"{audit_log.wo_label(wo)}: issue description saved for job"
                    + (f" #{position + 1}" if position is not None else ""),
            details={"labor_id": labor_id, "labor_index": position},
        )
        rev = ev.labors_rev(doc)
        return {
            "ok": True, "saved": True, "issue_description": text, "labors_rev": rev,
            "view_id": _derive_issue_view(shop_db, shop_id, wo_id, data, user_id=user_id,
                                          labor_id=labor_id, text=text, labors_rev=rev),
        }, 200

    for _attempt in range(ev.MAX_CAS_ATTEMPTS):
        labors = wo.get("labors") or []
        if labor_index < 0 or labor_index >= len(labors) or not isinstance(labors[labor_index], dict):
            return {"ok": False, "error": "labor_index out of range"}, 400
        rev = ev.labors_rev(wo)
        res = shop_db.work_orders.update_one(
            {**base_filter, **ev.rev_filter(rev)},
            ev.bump_rev({"$set": {
                f"labors.{labor_index}.labor.issue_description": text,
                f"labors.{labor_index}.issue_description": text,
                "updated_at": now,
                "updated_by": user_id,
            }}),
        )
        if res.matched_count:
            audit_log.log_event(
                "update", "work_order", wo_id, label=audit_log.wo_label(wo),
                summary=f"{audit_log.wo_label(wo)}: issue description saved for job #{labor_index + 1}",
                details={"labor_index": labor_index},
            )
            payload = {"ok": True, "saved": True, "issue_description": text, "labors_rev": rev + 1}
            if "view_id" in data:
                payload["view_id"] = _derive_issue_view(
                    shop_db, shop_id, wo_id, data, user_id=user_id,
                    labor_id=norm_text(labors[labor_index].get("labor_id")), text=text, labors_rev=rev + 1,
                )
            return payload, 200
        wo = shop_db.work_orders.find_one(base_filter, ev.with_rev({"labors": 1, "wo_number": 1, "status": 1}))
        if not wo:
            return {"ok": False, "error": "Work order not found"}, 404
    current_app.logger.warning("WO %s: issue description gave up after %d CAS attempts", wo_id, ev.MAX_CAS_ATTEMPTS)
    return dict(ev.CONCURRENT_UPDATE_RESPONSE), 409


# ───────────────────────── база вкладки после записи «сбоку» ─────────────────────────


def _derive_issue_view(shop_db, shop_id, wo_id, data: dict, *, user_id, labor_id: str, text: str, labors_rev) -> str:
    """Вьюха вкладки после её Describe Issue: та же база, у строки — записанный текст."""
    if not norm_text(labor_id):
        return ""
    return ev.derive_view_with_rows(
        shop_db, shop_id, wo_id, (data or {}).get("view_id"),
        user_id=str(user_id or ""), channel=CHANNEL,
        row_fields={labor_id: {"issue_description": text}}, labors_rev=labors_rev,
    )


# Поля строки, которые пересчитывает правка тайм-лога (refresh_time_derived_fields)
# и которые merge сравнивает, решая «строку менял кто-то другой».
_TIME_LOG_ROW_FIELDS = ("hours", "hours_source", "labor_total")
TIME_LOG_ROW_PROJECTION = {"labors": 1, "totals": 1}


def _compact_row_of(wo, labor_id: str):
    lid = norm_text(labor_id)
    if not lid or not isinstance(wo, dict):
        return None
    for row in ev.compact_rows_from_wo(wo):
        if row.get("labor_id") == lid:
            return row
    return None


def derive_view_after_time_log(shop_db, shop_id, wo_id, view_id, *, user_id, labor_id, before_wo) -> str:
    """
    Вьюха вкладки после её правки/удаления тайм-лога строки labor_id.

    Пересчёт часов (refresh_time_derived_fields) пишет в WO сразу, форма
    вкладки не перечитывается (в режиме правки — чтобы не стереть её
    правки). Без новой базы следующий Save принял бы свежие tracked-часы за
    правку другого пользователя и, например, не дал бы удалить эту строку.
    Производная вьюха — та же база, у строки — те из полей hours /
    hours_source / labor_total, которые пересчёт реально изменил (before_wo
    — документ до пересчёта, TIME_LOG_ROW_PROJECTION + labors_rev). Только
    изменённые: значение, которое до этого поменял кто-то другой (ручные
    часы другого менеджера), должно остаться в базе чужим — иначе Save
    вкладки молча откатил бы его к значению формы. assigned_mechanics не
    переносится: форма показывает прежнее назначение, и свежее в базе
    выдало бы нетронутую форму за переназначение (назначения строк с
    таймерами всё равно пересчитывает синк после Save).
    Возвращает id новой вьюхи или "" (не просили / вьюха не нашлась / WO нет).
    """
    if not norm_text(view_id):
        return ""
    after_wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop_id}, ev.with_rev(TIME_LOG_ROW_PROJECTION))
    if not after_wo:
        return ""
    lid = norm_text(labor_id)
    before = _compact_row_of(before_wo, lid) or {}
    after = _compact_row_of(after_wo, lid)
    patch = {}
    if after is not None:
        patch = {f: after.get(f) for f in _TIME_LOG_ROW_FIELDS if after.get(f) != before.get(f)}
    return ev.derive_view_with_rows(
        shop_db, shop_id, wo_id, view_id,
        user_id=str(user_id or ""), channel=CHANNEL,
        row_fields={lid: patch} if patch else None, labors_rev=ev.labors_rev(after_wo),
    )


__all__ = [
    "CHANNEL",
    "PAGE_OUTDATED_RESPONSE",
    "TIME_LOG_ROW_PROJECTION",
    "derive_view_after_time_log",
    "office_mechanic_guard",
    "office_rows_as_mechanic_rows",
    "office_view_wo_fields",
    "save_labor_issue_description",
    "save_office_edit",
]
