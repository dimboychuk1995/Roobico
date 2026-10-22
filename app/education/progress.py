"""Прогресс пользователя в Education Center.

master.education_progress — один документ на юзера:
  {user_id: ObjectId, tenant_id: ObjectId|None,
   completed: {"<module>/<lesson>": datetime}, last_viewed: "<module>/<lesson>",
   updated_at: datetime}

Прогресс личный (не на тенанта): владелец и механик учатся разным вещам.
"""
from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId

from app.extensions import get_master_db
from . import catalog


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uid(user_id) -> ObjectId | None:
    try:
        return ObjectId(str(user_id))
    except Exception:
        return None


def get_progress(user_id) -> dict:
    uid = _uid(user_id)
    if uid is None:
        return {"completed": {}, "last_viewed": None}
    doc = get_master_db().education_progress.find_one({"user_id": uid}) or {}
    completed = doc.get("completed") if isinstance(doc.get("completed"), dict) else {}
    # Уроки, удалённые из каталога, не считаем.
    completed = {k: v for k, v in completed.items() if catalog.get_lesson_by_key(k)}
    return {"completed": completed, "last_viewed": doc.get("last_viewed")}


def set_completed(user_id, tenant_id, lesson_key: str, completed: bool) -> dict:
    uid = _uid(user_id)
    if uid is None or not catalog.get_lesson_by_key(lesson_key):
        raise ValueError("unknown lesson")
    col = get_master_db().education_progress
    update: dict = {"$set": {"updated_at": _utcnow(), "tenant_id": _uid(tenant_id)}}
    if completed:
        update["$set"][f"completed.{lesson_key.replace('.', '_')}"] = _utcnow()
    else:
        update["$unset"] = {f"completed.{lesson_key.replace('.', '_')}": ""}
    col.update_one({"user_id": uid}, update, upsert=True)
    return summary(user_id)


def mark_viewed(user_id, tenant_id, lesson_key: str) -> None:
    uid = _uid(user_id)
    if uid is None:
        return
    get_master_db().education_progress.update_one(
        {"user_id": uid},
        {"$set": {"last_viewed": lesson_key, "updated_at": _utcnow(), "tenant_id": _uid(tenant_id)}},
        upsert=True,
    )


def reset(user_id) -> None:
    uid = _uid(user_id)
    if uid is None:
        return
    get_master_db().education_progress.update_one(
        {"user_id": uid},
        {"$set": {"completed": {}, "updated_at": _utcnow()}, "$unset": {"last_viewed": ""}},
    )


def summary(user_id) -> dict:
    """Сводка для страниц и API: общий процент, по модулям, «продолжить»."""
    progress = get_progress(user_id)
    completed = progress["completed"]
    total = catalog.TOTAL_LESSONS
    done = len(completed)

    modules = {}
    for m in catalog.MODULES:
        m_done = sum(1 for l in m.lessons if l.key in completed)
        modules[m.id] = {
            "done": m_done,
            "total": len(m.lessons),
            "percent": int(round(100 * m_done / len(m.lessons))) if m.lessons else 0,
        }

    # «Продолжить»: последний открытый незавершённый урок, иначе первый
    # незавершённый по порядку, иначе None (всё пройдено).
    continue_lesson = None
    last = progress.get("last_viewed")
    if last and last not in completed and catalog.get_lesson_by_key(last):
        continue_lesson = catalog.get_lesson_by_key(last)
    if continue_lesson is None:
        for l in catalog.ALL_LESSONS:
            if l.key not in completed:
                continue_lesson = l
                break

    return {
        "done": done,
        "total": total,
        "percent": int(round(100 * done / total)) if total else 0,
        "modules": modules,
        "completed": sorted(completed.keys()),
        "continue": {"key": continue_lesson.key, "title": continue_lesson.title,
                     "url": continue_lesson.url} if continue_lesson else None,
    }
