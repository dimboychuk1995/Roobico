"""
Механик-режим: сборка и merge сохранений WO.

Механик присылает только описание работ и парты (part_id/part_number + qty);
клиентские цены/часы/ставки/статусы игнорируются. Цены проставляет сервер:
cost = average_cost из каталога, price = suggest_part_price (те же
прайсинг-правила, что в веб-автозаполнении). При редактировании существующего
WO блоки матчатся по стабильному labor_id, чтобы не затереть менеджерские
поля (hours, rate, назначения, подправленные цены).
"""
from __future__ import annotations

import json

from app.blueprints.work_orders.services.common import i32, round2
from app.blueprints.work_orders.services.mobile_editor import suggest_part_price


def parse_mileage(value):
    """Пробег из формы: None, если не передан/мусор — тогда юнит не трогаем."""
    if value is None or str(value).strip() == "":
        return None
    try:
        n = int(float(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def mechanic_done_fields(data: dict, user_id, now) -> dict:
    """
    $set-поля флага «механик закончил работу» из mechanic_state сохранения.

    Работа механика всегда остаётся in_progress до утверждения менеджером;
    mechanic_state = "done" лишь помечает для менеджера, что механик закончил.
    Любое другое сохранение механика сбрасывает флаг (он снова в работе).
    """
    state = str((data or {}).get("mechanic_state") or "").strip().lower()
    if state == "done":
        return {"mechanic_done": True, "mechanic_done_at": now, "mechanic_done_by": user_id}
    return {"mechanic_done": False, "mechanic_done_at": None, "mechanic_done_by": None}


def _resolve_part_doc(shop_db, shop_id, part_id_raw, part_number):
    from app.blueprints.work_orders.services.common import oid

    part_id = oid(part_id_raw)
    if part_id:
        doc = shop_db.parts.find_one({"_id": part_id, "shop_id": shop_id, "is_active": True})
        if doc:
            return doc
    pn = str(part_number or "").strip()
    if pn:
        return shop_db.parts.find_one(
            {"part_number": pn, "shop_id": shop_id, "is_active": True}
        )
    return None


def _catalog_misc_items(part_doc: dict) -> list[dict]:
    """Misc-чарджи запчасти из каталога — в формате misc-items JSON WO."""
    if not part_doc.get("misc_has_charge"):
        return []
    items = []
    for ch in part_doc.get("misc_charges") or []:
        if not isinstance(ch, dict):
            continue
        description = str(ch.get("description") or "").strip()
        price = round2(ch.get("price") or 0)
        if not description or price <= 0:
            continue
        items.append({
            "description": description,
            "price": price,
            "taxable": ch.get("taxable") is not False,
        })
    return items


def _attach_catalog_misc_items(parts: list[dict]) -> None:
    """
    Misc-чарджи каталога с новых строк механика → JSON-список на ПЕРВОЙ
    строке партов блока — тот же формат и место, куда пишет веб-автозаполнение
    менеджера (quantity = qty строки, partIndex — индекс строки-источника).
    Уже сохранённые в JSON позиции (ручные и от других строк) не трогаем.
    """
    new_items = []
    for idx, p in enumerate(parts):
        items = p.pop("_catalog_misc_items", None) or []
        qty = i32(p.get("qty")) or 0
        if qty <= 0:
            continue
        for it in items:
            new_items.append({**it, "quantity": qty, "partIndex": idx})

    if not parts or not new_items:
        return

    existing = []
    raw = str(parts[0].get("misc_charge_description") or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                existing = parsed
        except ValueError:
            existing = []
    parts[0]["misc_charge_description"] = json.dumps(existing + new_items)


def _default_rate_code(shop_db, shop_id) -> str:
    """
    Ставка для работ механика без пресета: "standard", если есть, иначе
    первая активная ставка магазина. Без ставки labor total был бы $0.
    """
    from app.blueprints.work_orders.services.lookups import get_labor_rates

    rates = get_labor_rates(shop_db, shop_id)
    if not rates:
        return ""
    for r in rates:
        if str(r.get("code") or "").strip().lower() == "standard":
            return str(r.get("code") or "")
    return str(rates[0].get("code") or "")


def _autofill_part(shop_db, shop_id, customer_id, raw_part: dict) -> dict:
    """Новая строка запчасти от механика: qty от клиента, деньги — с сервера."""
    qty = i32(raw_part.get("qty")) or 0
    part_doc = _resolve_part_doc(
        shop_db, shop_id, raw_part.get("part_id"), raw_part.get("part_number")
    )
    if part_doc:
        core_charge = (
            round2(part_doc.get("core_cost") or 0)
            if part_doc.get("core_has_charge")
            else 0.0
        )
        return {
            "part_id": str(part_doc["_id"]),
            "part_number": str(part_doc.get("part_number") or "").strip(),
            "description": (
                str(raw_part.get("description") or "").strip()
                or str(part_doc.get("description") or "").strip()
            ),
            "qty": qty,
            "cost": round2(part_doc.get("average_cost") or 0),
            "price": suggest_part_price(shop_db, shop_id, customer_id, part_doc),
            "core_charge": core_charge,
            "misc_charge": 0,
            "misc_charge_description": "",
            "one_time_part": False,
            # Временный ключ: misc-чарджи каталога, забирает
            # _attach_catalog_misc_items при сборке блока.
            "_catalog_misc_items": _catalog_misc_items(part_doc),
        }
    # Не нашли в каталоге — ручная строка без цен, менеджер заполнит.
    return {
        "part_id": "",
        "part_number": str(raw_part.get("part_number") or "").strip(),
        "description": str(raw_part.get("description") or "").strip(),
        "qty": qty,
        "cost": 0,
        "price": 0,
        "core_charge": 0,
        "misc_charge": 0,
        "misc_charge_description": "",
        "one_time_part": True,
    }


def _stored_part_key(p: dict) -> str:
    part_id = str(p.get("part_id") or "").strip()
    if part_id:
        return f"id:{part_id}"
    return f"pn:{str(p.get('part_number') or '').strip().lower()}"


def _stored_block_to_payload(block: dict, block_totals: dict) -> dict:
    """Существующий блок WO -> payload-форма compute_labors_and_totals."""
    labor_src = block.get("labor") if isinstance(block.get("labor"), dict) else {}
    labor_base = round2((block_totals or {}).get("labor") or 0)
    return {
        "labor_id": str(block.get("labor_id") or ""),
        "description": str(labor_src.get("description") or "").strip(),
        "hours": str(labor_src.get("hours") or "").strip(),
        "rate_code": str(labor_src.get("rate_code") or "").strip(),
        # Фиксируем сохранённую labor base как ручной total, чтобы merge
        # не пересчитал сумму по текущей (возможно изменённой) ставке.
        "labor_total": labor_base if labor_base > 0 else None,
        "issue_description": str(labor_src.get("issue_description") or "").strip(),
        "hours_source": str(labor_src.get("hours_source") or "").strip(),
        "assigned_mechanics": labor_src.get("assigned_mechanics") or [],
        "parts": [dict(p) for p in (block.get("parts") or []) if isinstance(p, dict)],
    }


def _preset_defaults(shop_db, shop_id, preset_id_raw) -> dict:
    """
    Часы/ставка пресета для новой работы механика: часы биллинга фиксированы
    пресетом и не зависят от фактически затреканного времени.
    """
    from app.blueprints.work_orders.services.common import oid

    pid = oid(preset_id_raw)
    if not pid:
        return {}
    doc = shop_db.wo_presets.find_one(
        {"_id": pid, "shop_id": shop_id, "is_active": True},
        {"labor_hours": 1, "labor_rate_code": 1},
    )
    if not doc:
        return {}

    out: dict = {}
    try:
        hours = float(doc.get("labor_hours") or 0)
    except (TypeError, ValueError):
        hours = 0.0
    if hours > 0:
        out["hours"] = str(doc.get("labor_hours"))
        out["hours_source"] = "preset"
    rate_code = str(doc.get("labor_rate_code") or "").strip()
    if rate_code:
        out["rate_code"] = rate_code
    return out


def build_mechanic_labors_payload(shop_db, shop, customer_id, payload_labors) -> list[dict]:
    """Создание WO механиком: все парты автозаполняются сервером."""
    default_rate_code = _default_rate_code(shop_db, shop["_id"])
    out = []
    for raw in payload_labors or []:
        if not isinstance(raw, dict):
            continue
        parts = [
            _autofill_part(shop_db, shop["_id"], customer_id, p)
            for p in (raw.get("parts") or [])
            if isinstance(p, dict) and (
                str(p.get("part_id") or "").strip()
                or str(p.get("part_number") or "").strip()
                or str(p.get("description") or "").strip()
            )
        ]
        _attach_catalog_misc_items(parts)
        # Работа из пресета: часы и ставка — из пресета (клиентские значения
        # механика игнорируются, как и остальные денежные поля). Без пресета
        # ставка — дефолтная ставка магазина, чтобы затреканные часы
        # биллинговались, а не давали $0.
        preset = _preset_defaults(shop_db, shop["_id"], raw.get("preset_id"))
        out.append({
            "labor_id": str(raw.get("labor_id") or ""),
            "description": str(raw.get("description") or "").strip(),
            "hours": preset.get("hours", ""),
            "rate_code": preset.get("rate_code") or default_rate_code,
            "labor_total": None,
            "issue_description": str(raw.get("issue_description") or "").strip(),
            "hours_source": preset.get("hours_source", ""),
            "assigned_mechanics": [],
            "parts": parts,
        })
    return out


def merge_mechanic_edit(shop_db, shop, existing_wo: dict, payload_labors) -> list[dict]:
    """
    Редактирование WO механиком: вернуть payload для compute_labors_and_totals.

    Правила: блоки матчатся по labor_id; у совпавших сохраняются hours/rate/
    назначения/менеджерские цены партов (qty и описание обновляются), новые
    парты автоценятся; блоки без labor_id — новые строки; блоки, отсутствующие
    в payload, удаляются (клиент всегда шлёт полный список работ, инвентарь
    возвращает adjust_inventory_for_part_changes, тайм-логи остаются и видны
    менеджеру как «время на удалённых строках»).
    """
    customer_id = existing_wo.get("customer_id")
    totals_doc = existing_wo.get("totals") if isinstance(existing_wo.get("totals"), dict) else {}
    totals_blocks = totals_doc.get("labors") if isinstance(totals_doc.get("labors"), list) else []

    existing_by_id: dict[str, dict] = {}
    for i, block in enumerate(existing_wo.get("labors") or []):
        if not isinstance(block, dict):
            continue
        labor_id = str(block.get("labor_id") or "").strip()
        block_totals = totals_blocks[i] if i < len(totals_blocks) and isinstance(totals_blocks[i], dict) else {}
        if labor_id:
            existing_by_id[labor_id] = _stored_block_to_payload(block, block_totals)

    out: list[dict] = []

    for raw in payload_labors or []:
        if not isinstance(raw, dict):
            continue
        labor_id = str(raw.get("labor_id") or "").strip()
        base = existing_by_id.get(labor_id)

        if base is None:
            # Новая строка от механика.
            out.append(build_mechanic_labors_payload(shop_db, shop, customer_id, [raw])[0])
            continue

        existing_parts_by_key = {_stored_part_key(p): p for p in base["parts"]}

        merged_parts = []
        for p in raw.get("parts") or []:
            if not isinstance(p, dict):
                continue
            if not (
                str(p.get("part_id") or "").strip()
                or str(p.get("part_number") or "").strip()
                or str(p.get("description") or "").strip()
            ):
                continue
            key = _stored_part_key(p)
            existing_part = existing_parts_by_key.get(key)
            if existing_part is not None:
                merged = dict(existing_part)
                merged["qty"] = i32(p.get("qty")) or 0
                incoming_desc = str(p.get("description") or "").strip()
                if incoming_desc:
                    merged["description"] = incoming_desc
                merged_parts.append(merged)
            else:
                merged_parts.append(_autofill_part(shop_db, shop["_id"], customer_id, p))

        # Misc-чарджи каталога с новых строк — в JSON первой строки блока.
        _attach_catalog_misc_items(merged_parts)

        out.append({
            **base,
            "description": str(raw.get("description") or "").strip(),
            "issue_description": str(raw.get("issue_description") or "").strip(),
            "parts": merged_parts,
        })

    return out


def refresh_time_derived_fields(shop_db, shop, work_order_id, mechanics_by_id=None):
    """
    Пересчёт полей WO, производных от тайм-логов (вызывается после остановки
    таймера, в т.ч. auto_switch):
      - assigned_mechanics — кто фактически работал, пропорционально времени;
      - часы работы — сумма завершённого времени всех механиков строки.
        Исключения: работы из пресета (часы фиксированы пресетом) и часы,
        введённые менеджером вручную (нет метки hours_source="tracked");
      - totals — пересчитываются с зафиксированной налоговой ставкой WO.
    Возвращает True, если WO обновлён.
    """
    from app.blueprints.work_orders.services import time_tracking
    from app.blueprints.work_orders.services.common import oid, utcnow
    from app.blueprints.work_orders.services.mobile_editor import compute_labors_and_totals
    from app.blueprints.work_orders.services.totals import (
        _apply_sales_tax_to_totals,
        _get_shop_sales_tax_context,
        _is_customer_taxable,
        align_totals_with_labors,
        normalize_totals_payload,
    )

    wo_id = oid(work_order_id)
    if not wo_id:
        return False
    wo = shop_db.work_orders.find_one(
        {"_id": wo_id, "shop_id": shop["_id"], "is_active": True}
    )
    if not wo:
        return False

    assignments = time_tracking.time_based_assignments(shop_db, shop["_id"], wo_id)
    summary = time_tracking.summarize_wo_time(shop_db, shop["_id"], wo_id)
    if not assignments and not summary:
        return False

    totals_doc = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    totals_blocks = totals_doc.get("labors") if isinstance(totals_doc.get("labors"), list) else []

    changed = False
    payload = []
    for i, block in enumerate(wo.get("labors") or []):
        if not isinstance(block, dict):
            continue
        block_totals = (
            totals_blocks[i]
            if i < len(totals_blocks) and isinstance(totals_blocks[i], dict)
            else {}
        )
        base = _stored_block_to_payload(block, block_totals)
        labor_id = base["labor_id"]

        assigned = assignments.get(labor_id)
        if assigned is not None:
            for a in assigned:
                m = (mechanics_by_id or {}).get(str(a["user_id"]))
                if m:
                    a["name"] = m.get("name") or a["name"]
                    a["role"] = m.get("role") or ""
            if assigned != base.get("assigned_mechanics"):
                base["assigned_mechanics"] = assigned
                changed = True

        seconds = int((summary.get(labor_id) or {}).get("completed_seconds") or 0)
        source = base.get("hours_source") or ""
        if seconds > 0 and source != "preset" and (not base.get("hours") or source == "tracked"):
            new_hours = str(round2(seconds / 3600.0))
            if new_hours != base.get("hours"):
                base["hours"] = new_hours
                # Сумма должна пересчитаться из новых часов, а не из
                # зафиксированной прежней labor base.
                base["labor_total"] = None
                base["hours_source"] = "tracked"
                changed = True

        payload.append(base)

    if not changed:
        return False

    labors, totals_raw = compute_labors_and_totals(shop_db, shop, payload)
    totals = align_totals_with_labors(normalize_totals_payload(totals_raw), labors)

    locked_tax_rate = totals_doc.get("sales_tax_rate")
    if locked_tax_rate is None:
        locked_tax_rate = _get_shop_sales_tax_context(shop, shop_db).get("rate") or 0
    is_taxable = totals_doc.get("is_taxable")
    if is_taxable is None:
        is_taxable = _is_customer_taxable(shop_db, wo.get("customer_id"))
    totals = _apply_sales_tax_to_totals(totals, locked_tax_rate, bool(is_taxable))

    shop_db.work_orders.update_one(
        {"_id": wo_id, "shop_id": shop["_id"]},
        {"$set": {"labors": labors, "totals": totals, "updated_at": utcnow()}},
    )
    return True
