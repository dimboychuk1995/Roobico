"""
Флаг «механик закончил» (mechanic_done) при нескольких механиках на одном WO.

Каждый механик отмечает Done только за себя: отметки лежат в
mechanic_done_marks = [{user_id, name, at}]. WO-уровневый mechanic_done
(бейдж «Mechanic done» у менеджера, выход из группы In Work, пуш
«finished») ставится ТОЛЬКО когда Done поставили все механики, работавшие
по WO. «Работавшие» = назначенные на строки (assigned_mechanics, их
переписывают таймеры) + владельцы тайм-логов по WO + сам сохраняющий;
учитываются только активные пользователи-механики магазина, чтобы
уволенный механик или офисный юзер с таймером не блокировали Done навсегда.

Обычное сохранение механика (автосейв) и старт его таймера снимают
только ЕГО отметку. Менеджерское «Save In Progress» / статус in_progress
снимает все отметки — WO возвращается в In Work до нового Done от каждого.
"""
from __future__ import annotations

from bson import ObjectId

from app.blueprints.work_orders.services.common import oid
from app.extensions import get_master_db
from app.utils.push_notifications import MECHANIC_ROLES

DONE_CLEAR_FIELDS = {
    "mechanic_done": False,
    "mechanic_done_at": None,
    "mechanic_done_by": None,
    "mechanic_done_marks": [],
}


def clear_mechanic_done_fields() -> dict:
    """$set-поля менеджерского сброса: WO снова «в работе» у всех механиков."""
    return dict(DONE_CLEAR_FIELDS)


def done_marks(wo: dict | None) -> list[dict]:
    marks = (wo or {}).get("mechanic_done_marks")
    if not isinstance(marks, list):
        return []
    return [m for m in marks if isinstance(m, dict) and oid(m.get("user_id"))]


def done_user_ids(wo: dict | None) -> set[ObjectId]:
    return {oid(m.get("user_id")) for m in done_marks(wo)}


def wo_worker_ids(shop_db, wo: dict | None) -> set[ObjectId]:
    """Кто фактически работал по WO: назначенные на строки + тайм-логи."""
    ids: set[ObjectId] = set()
    if not wo:
        return ids
    for block in wo.get("labors") or []:
        if not isinstance(block, dict):
            continue
        labor = block.get("labor") if isinstance(block.get("labor"), dict) else {}
        for a in labor.get("assigned_mechanics") or block.get("assigned_mechanics") or []:
            uid = oid((a or {}).get("user_id")) if isinstance(a, dict) else None
            if uid:
                ids.add(uid)
    if wo.get("_id"):
        for log in shop_db.wo_time_logs.find(
            {"shop_id": wo.get("shop_id"), "work_order_id": wo["_id"]}, {"user_id": 1}
        ):
            uid = oid(log.get("user_id"))
            if uid:
                ids.add(uid)
    return ids


def _active_mechanics(user_ids: set[ObjectId]) -> dict[ObjectId, dict]:
    if not user_ids:
        return {}
    rows = get_master_db().users.find(
        {"_id": {"$in": list(user_ids)}, "is_active": True, "role": {"$in": list(MECHANIC_ROLES)}},
        {"first_name": 1, "last_name": 1, "name": 1, "email": 1},
    )
    return {u["_id"]: u for u in rows}


def _display_name(user: dict | None) -> str:
    user = user or {}
    full = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p).strip()
    return full or str(user.get("name") or "").strip() or str(user.get("email") or "").strip() or "Mechanic"


def mechanic_done_fields(shop_db, shop, wo: dict | None, data: dict, user_id, now) -> dict:
    """
    $set-поля по mechanic_state сохранения механика.

    "done" — добавить отметку текущего механика; любое другое сохранение —
    снять её (он снова в работе). mechanic_done = True только если у каждого
    работавшего активного механика есть отметка. Статус WO не трогается:
    работа механика остаётся in_progress до утверждения менеджером.
    """
    state = str((data or {}).get("mechanic_state") or "").strip().lower()
    user_id = oid(user_id)

    marks = [m for m in done_marks(wo) if oid(m.get("user_id")) != user_id]
    workers = wo_worker_ids(shop_db, wo)
    if user_id:
        workers.add(user_id)
    active = _active_mechanics(workers | {oid(m.get("user_id")) for m in marks})

    if state == "done" and user_id:
        marks.append({"user_id": user_id, "name": _display_name(active.get(user_id)), "at": now})

    # Отметки уволенных/неактивных не считаем; работавшие — только активные механики.
    marks = [m for m in marks if oid(m.get("user_id")) in active]
    workers = {w for w in workers if w in active}
    done_ids = {oid(m.get("user_id")) for m in marks}
    all_done = bool(done_ids) and workers <= done_ids

    return {
        "mechanic_done": all_done,
        "mechanic_done_at": now if all_done else None,
        "mechanic_done_by": user_id if all_done else None,
        "mechanic_done_marks": marks,
    }


def release_mechanic_done_mark(shop_db, shop, wo_id, user_id, now):
    """
    Механик снова взялся за работу (старт таймера): снять его отметку Done;
    WO-уровневый флаг сбрасывается — появился незакончивший работник.
    Возвращает True, если документ изменился.
    """
    res = shop_db.work_orders.update_one(
        {
            "_id": wo_id,
            "shop_id": shop["_id"],
            "$or": [{"mechanic_done": True}, {"mechanic_done_marks.user_id": user_id}],
        },
        {
            "$set": {"mechanic_done": False, "mechanic_done_at": None, "mechanic_done_by": None, "updated_at": now},
            "$pull": {"mechanic_done_marks": {"user_id": user_id}},
        },
    )
    return res.modified_count > 0


def done_mechanic_names(wo: dict | None) -> list[str]:
    names: list[str] = []
    for m in done_marks(wo):
        nm = str(m.get("name") or "").strip()
        if nm and nm not in names:
            names.append(nm)
    return names


def personalize_wo_list_item(item: dict, user_id) -> dict:
    """
    Для механика «Done» в списке/деталях — его собственная отметка (или
    WO целиком done). Служебный список id из ответа убирается.
    """
    ids = item.pop("mechanic_done_user_ids", None) or []
    item["mechanic_done"] = bool(item.get("mechanic_done")) or str(user_id or "") in ids
    return item
