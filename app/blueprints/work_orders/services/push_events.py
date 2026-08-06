"""Push-события уровня WO: механик взял работу / механик закончил.

События шлются офисным пользователям магазина (все активные не-механики),
когда МЕХАНИК (роль mechanic/senior_mechanic) переводит WO в работу или
помечает его законченным. Лейбор-уровень (старт/стоп таймера на строке) не
уведомляет — только переходы состояния самого WO:

  taken    — WO стал «в работе у механика»: статус перешёл в in_progress
             (старт таймера / сохранение механика) или сброшен mechanic_done
             (механик вернулся к работе после done);
  finished — mechanic_done перешёл в True (кнопка Done в сохранении).

Вызовы безопасны: любые ошибки логируются и не роняют исходный запрос.
"""
from __future__ import annotations

from flask import current_app

from app.extensions import get_master_db
from app.utils.push_notifications import (
    MECHANIC_ROLES,
    office_user_ids_for_shop,
    send_push_to_users,
)


def _actor_if_mechanic(master_db, actor_user_id):
    if actor_user_id is None:
        return None
    user = master_db.users.find_one(
        {"_id": actor_user_id},
        {"role": 1, "name": 1, "first_name": 1, "last_name": 1, "email": 1},
    )
    if not user or str(user.get("role") or "").strip().lower() not in MECHANIC_ROLES:
        return None
    return user


def _display_name(user: dict) -> str:
    name = str(user.get("name") or "").strip()
    if not name:
        name = " ".join(
            p for p in (user.get("first_name"), user.get("last_name")) if p
        ).strip()
    return name or str(user.get("email") or "Mechanic")


def notify_wo_event(shop, wo_id, wo_number, actor_user_id, event: str) -> None:
    """Уведомить офис о WO-событии механика. event: "taken" | "finished"."""
    try:
        master_db = get_master_db()
        actor = _actor_if_mechanic(master_db, actor_user_id)
        if not actor:
            return  # событие не от механика — офису не о чем сообщать

        recipients = office_user_ids_for_shop(master_db, shop, exclude_user_id=actor_user_id)
        if not recipients:
            return

        mechanic = _display_name(actor)
        wo_label = f"WO #{wo_number}" if wo_number else "Work order"
        shop_name = str(shop.get("name") or "").strip()
        suffix = f" · {shop_name}" if shop_name else ""

        if event == "finished":
            title = f"{wo_label} finished"
            body = f"{mechanic} finished {wo_label} — ready for review{suffix}"
        else:
            title = f"{wo_label} in work"
            body = f"{mechanic} started working on {wo_label}{suffix}"

        send_push_to_users(
            master_db,
            recipients,
            title,
            body,
            {
                "type": "wo_finished" if event == "finished" else "wo_taken",
                "work_order_id": str(wo_id or ""),
                "shop_id": str(shop.get("_id") or ""),
            },
        )
    except Exception:
        current_app.logger.exception("Failed to send WO push notification (%s)", event)


def notify_after_mechanic_save(shop, wo_before, set_fields: dict, wo_id, wo_number, actor_user_id) -> None:
    """Определить и отправить событие после сохранения WO механиком.

    wo_before — документ до апдейта (None для только что созданного WO),
    set_fields — что записали ($set / вставленный документ).
    """
    was_done = bool((wo_before or {}).get("mechanic_done"))
    was_in_progress = ((wo_before or {}).get("status") or "") == "in_progress"
    now_done = bool(set_fields.get("mechanic_done"))
    new_status = set_fields.get("status") or (wo_before or {}).get("status") or ""

    if now_done and not was_done:
        notify_wo_event(shop, wo_id, wo_number, actor_user_id, "finished")
    elif not now_done and new_status == "in_progress" and (not was_in_progress or was_done):
        notify_wo_event(shop, wo_id, wo_number, actor_user_id, "taken")
