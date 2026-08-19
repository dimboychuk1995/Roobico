"""
Живое состояние WO для менеджерской страницы деталей.

Страница деталей опрашивает /live_state раз в несколько секунд и без
перезагрузки показывает, что делают механики: идущие таймеры и накопленное
время (wo_time_logs), статус WO и флаг mechanic_done. Маркер изменения
документа — updated_at: если он сдвинулся, страница перечитывает WO целиком,
потому что сохранения механика переписывают labors/totals массивом и мержить
их в открытую форму на клиенте небезопасно.
"""
from __future__ import annotations

from app.blueprints.work_orders.services.time_tracking import _fmt_iso, summarize_wo_time


def work_orders_live_signature(shop_db, shop_id):
    """Дешёвая сигнатура «в магазине что-то изменилось» для страницы списка WO.

    Складывается из последних таймстемпов work_orders (updated_at покрывает
    сохранения/статусы/оплаты/подтверждения/удаления, created_at — создания,
    где updated_at ещё не проставлен) и wo_time_logs (updated_at пишется и на
    старте, и на стопе таймера — список показывает «кто работает сейчас»).
    Сдвиг любого компонента => страница перечитывает список целиком.
    """
    def _max_ts(coll, field, query):
        doc = coll.find_one(query, {field: 1}, sort=[(field, -1)])
        return _fmt_iso((doc or {}).get(field))

    return "|".join([
        _max_ts(shop_db.work_orders, "updated_at", {"shop_id": shop_id}),
        _max_ts(shop_db.work_orders, "created_at", {"shop_id": shop_id, "is_active": True}),
        _max_ts(shop_db.wo_time_logs, "updated_at", {"shop_id": shop_id}),
    ])


def build_live_state(shop_db, shop, wo, viewer_user_id=None):
    """Payload для polling'а: статус + mechanic_done + сводка времени.

    updated_by_me позволяет клиенту не перезагружать страницу после
    собственного сохранения (в т.ч. из другой вкладки того же юзера).
    """
    return {
        "status": (wo.get("status") or "open").strip().lower(),
        "mechanic_done": bool(wo.get("mechanic_done")),
        "updated_at": _fmt_iso(wo.get("updated_at")),
        "updated_by_me": bool(
            viewer_user_id is not None and wo.get("updated_by") == viewer_user_id
        ),
        "time_summary": summarize_wo_time(shop_db, shop["_id"], wo["_id"]),
    }
