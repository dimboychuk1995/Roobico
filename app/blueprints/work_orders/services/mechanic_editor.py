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
        "assigned_mechanics": labor_src.get("assigned_mechanics") or [],
        "parts": [dict(p) for p in (block.get("parts") or []) if isinstance(p, dict)],
    }


def build_mechanic_labors_payload(shop_db, shop, customer_id, payload_labors) -> list[dict]:
    """Создание WO механиком: все парты автозаполняются сервером."""
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
        out.append({
            "labor_id": str(raw.get("labor_id") or ""),
            "description": str(raw.get("description") or "").strip(),
            "hours": "",
            "rate_code": "",
            "labor_total": None,
            "issue_description": str(raw.get("issue_description") or "").strip(),
            "assigned_mechanics": [],
            "parts": parts,
        })
    return out


def merge_mechanic_edit(shop_db, shop, existing_wo: dict, payload_labors) -> list[dict]:
    """
    Редактирование WO механиком: вернуть payload для compute_labors_and_totals.

    Правила: блоки матчатся по labor_id; у совпавших сохраняются hours/rate/
    назначения/менеджерские цены партов (qty и описание обновляются), новые
    парты автоценятся; блоки без labor_id — новые строки; существующие блоки,
    отсутствующие в payload, сохраняются (удаление строк — только менеджер).
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

    seen_ids: set[str] = set()
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

        seen_ids.add(labor_id)
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

        out.append({
            **base,
            "description": str(raw.get("description") or "").strip(),
            "issue_description": str(raw.get("issue_description") or "").strip(),
            "parts": merged_parts,
        })

    # Блоки, которые механик не прислал, сохраняем как есть.
    for labor_id, base in existing_by_id.items():
        if labor_id not in seen_ids:
            out.append(base)

    return out
