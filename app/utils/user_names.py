"""
Живые имена пользователей для отображения.

Имена людей в shop-базах — снимки на момент записи:
  * work_orders.labors[].labor.assigned_mechanics[].name (+ блочное зеркало
    labors[].assigned_mechanics[].name);
  * work_orders.mechanic_done_marks[].name;
  * wo_time_logs.user_name;
  * calendar_events.mechanics[].name и legacy mechanic_name.
После переименования пользователя (Settings → Users) снимки устаревают,
поэтому экраны берут имя по id из master.users (load_user_names +
display_name_for / relabel_people), а снимок — только запас для удалённых
пользователей и легаси-записей без id.

refresh_user_name_snapshots переписывает сами снимки (после переименования
и в бэкфилле app/scripts/backfill_user_name_snapshots.py) — для «сырых»
читателей (AI-ассистент, старые сборки мобильного приложения). Корректность
экранов от него не зависит.
"""
from __future__ import annotations

import logging

from bson import ObjectId
from flask import current_app, g, has_app_context, has_request_context, request

from app.extensions import get_master_db
from app.utils.tenant import oid, shop_db_name, tenant_id_variants

FORMER_USER_LABEL = "Former user"
INACTIVE_SUFFIX = " (inactive)"

_USER_PROJECTION = {"first_name": 1, "last_name": 1, "name": 1, "email": 1, "role": 1, "is_active": 1}
_MEMO_ATTR = "_user_names_memo"

# Счётчики refresh_user_name_snapshots: документы, где снимок изменён
# (в dry_run — где был бы изменён), по форме снимка.
SNAPSHOT_COUNTER_KEYS = (
    "work_orders",           # labors[].labor.assigned_mechanics[].name
    "work_order_mirrors",    # labors[].assigned_mechanics[].name
    "done_marks",            # mechanic_done_marks[].name
    "time_logs",             # wo_time_logs.user_name
    "calendar_mechanics",    # calendar_events.mechanics[].name
    "calendar_mechanic_name",  # calendar_events.mechanic_name (legacy первый механик)
)

_log = logging.getLogger(__name__)


def _log_exception(message: str, *args) -> None:
    """current_app.logger, а вне app context (голый вызов из скрипта) — модульный логгер."""
    if has_app_context():
        current_app.logger.exception(message, *args)
    else:
        _log.exception(message, *args)


# ── форматирование ───────────────────────────────────────────

def user_display_name(u: dict | None) -> str:
    """Имя пользователя для отображения: «first last», иначе name, иначе
    email, иначе ''. То же правило, что у get_assignable_mechanics и у
    писателей снимков (назначения, таймеры, отметки Done)."""
    u = u or {}
    first = str(u.get("first_name") or "").strip()
    last = str(u.get("last_name") or "").strip()
    full = f"{first} {last}".strip()
    return full or str(u.get("name") or "").strip() or str(u.get("email") or "").strip()


def tenant_id_values(raw) -> list:
    """Все представления tenant_id (как есть, строка, ObjectId) — для $in:
    исторически tenant_id хранился то строкой, то ObjectId. Список, а не
    set: странное (нехешируемое) значение не должно ронять вызывающего."""
    if raw is None or raw == "":
        return []
    out: list = []
    for value in (raw, str(raw), oid(raw)):
        if value is not None and value != "" and value not in out:
            out.append(value)
    return out


def tenant_values_for_shop(shop: dict | None) -> list | None:
    """Варианты tenant_id магазина; None — у документа нет tenant_id (тогда
    load_user_names берёт тенант сессии). Магазин из get_shop_db уже
    проверен на принадлежность тенанту сессии, а в отличие от сессии его
    tenant_id доступен и вне запроса."""
    values = tenant_id_values((shop or {}).get("tenant_id"))
    return values or None


def _key(user_id) -> str:
    """Нормализованный ключ id: hex ObjectId; не-ObjectId — строка как есть."""
    parsed = oid(user_id)
    if parsed:
        return str(parsed)
    return str(user_id or "").strip()


def _tenant_memo_key(tenant_values: list) -> tuple:
    return tuple(sorted({str(v) for v in tenant_values}))


def _request_memo(tenant_values: list) -> dict | None:
    """Кэш {str_id: info | None} на время ТЕКУЩЕГО запроса (flask.g) или None
    вне запроса. Привязан к объекту запроса: при внешнем app_context (тесты)
    g переживает запрос, а кэш — нет."""
    if not has_request_context():
        return None
    current = request._get_current_object()
    memo = getattr(g, _MEMO_ATTR, None)
    if not memo or memo.get("request") is not current:
        memo = {"request": current, "by_tenant": {}}
        setattr(g, _MEMO_ATTR, memo)
    return memo["by_tenant"].setdefault(_tenant_memo_key(tenant_values), {})


def _info(u: dict) -> dict:
    return {
        "name": user_display_name(u),
        "is_active": bool(u.get("is_active")),
        "role": str(u.get("role") or "").strip(),
    }


# ── резолвер ─────────────────────────────────────────────────

def load_user_names(user_ids, tenant_values=None) -> dict[str, dict]:
    """
    {str_id: {"name", "is_active", "role"}} для пользователей тенанта.

    Один запрос к master.users по _id $in С фильтром tenant_id (изоляция
    тенантов: чужой id не резолвится). БЕЗ фильтра is_active/role — история
    должна показывать и уволенных, и бывших механиков. id — строки или
    ObjectId вперемешку; не-ObjectId (легаси "u1", "uattend:5", "") молча
    пропускаются. tenant_values=None — тенант сессии (вне запроса — ничего
    не резолвится). В запросе результат кэшируется в flask.g. Ошибка базы —
    в лог, вызывающий получает то, что успели найти (и падает на снимки).
    """
    keys: list[str] = []
    seen: set[str] = set()
    for raw in user_ids or ():
        parsed = oid(raw)
        if not parsed:
            continue
        key = str(parsed)
        if key not in seen:
            seen.add(key)
            keys.append(key)
    if not keys:
        return {}

    if tenant_values is None:
        tenant_values = tenant_id_variants() if has_request_context() else []
    tenant_values = [v for v in (tenant_values or []) if v is not None and v != ""]
    if not tenant_values:
        return {}

    memo = _request_memo(tenant_values)
    if memo is None:
        memo = {}
    missing = [k for k in keys if k not in memo]
    if missing:
        try:
            rows = get_master_db().users.find(
                {"_id": {"$in": [ObjectId(k) for k in missing]}, "tenant_id": {"$in": tenant_values}},
                _USER_PROJECTION,
            )
            found = {str(u["_id"]): _info(u) for u in rows}
        except Exception:
            _log_exception("User names: failed to load %d user(s)", len(missing))
            return {k: dict(memo[k]) for k in keys if memo.get(k)}
        for k in missing:
            memo[k] = found.get(k)
    return {k: dict(memo[k]) for k in keys if memo.get(k)}


def display_name_for(names: dict, user_id, snapshot="", *, mark_inactive: bool = False,
                     default: str = FORMER_USER_LABEL) -> str:
    """Подпись человека: живое имя по id (с " (inactive)" для неактивного,
    если mark_inactive), иначе сохранённый снимок, иначе default."""
    info = (names or {}).get(_key(user_id)) if user_id else None
    if info and info.get("name"):
        label = info["name"]
        if mark_inactive and not info.get("is_active", True):
            label += INACTIVE_SUFFIX
        return label
    return str(snapshot or "").strip() or default


def relabel_people(items, names: dict, id_keys=("user_id", "id"), name_key: str = "name", *,
                   mark_inactive: bool = False, default: str | None = None) -> int:
    """
    На месте заменить снимок имени живым в списке dict'ов (assigned_mechanics,
    running_users, бригада записи календаря). id — первый непустой из id_keys.
    Неизвестный id (удалённый пользователь, чужой тенант) — снимок остаётся;
    default (если задан) ставится только когда снимок пуст. Возвращает число
    изменённых элементов.
    """
    changed = 0
    for item in items or ():
        if not isinstance(item, dict):
            continue
        uid = next((item.get(k) for k in id_keys if item.get(k)), None)
        if not uid:
            continue
        current = item.get(name_key)
        info = (names or {}).get(_key(uid))
        if info and info.get("name"):
            label = info["name"]
            if mark_inactive and not info.get("is_active", True):
                label += INACTIVE_SUFFIX
        elif default is not None and not str(current or "").strip():
            label = default
        else:
            continue
        if label != current:
            item[name_key] = label
            changed += 1
    return changed


# ── переименование: снимки в shop-базах ─────────────────────

def _snapshot_operations(shop_ids: list, ids: list, name: str):
    """(счётчик, коллекция, фильтр, update, array_filters) для каждой формы
    снимка. Фильтр берёт только документы, где имя ещё отличается, — повторный
    прогон ничего не меняет. array_filters с $elemMatch по массиву: документы,
    где у части строк нет assigned_mechanics (или там null), не роняют апдейт."""
    shop_filter = {"$in": shop_ids}
    stale = {"user_id": {"$in": ids}, "name": {"$ne": name}}
    return (
        (
            "work_orders", "work_orders",
            {"shop_id": shop_filter, "labors": {"$elemMatch": {"labor.assigned_mechanics": {"$elemMatch": stale}}}},
            {"$set": {"labors.$[b].labor.assigned_mechanics.$[m].name": name}},
            [{"b.labor.assigned_mechanics": {"$elemMatch": {"user_id": {"$in": ids}}}}, {"m.user_id": {"$in": ids}}],
        ),
        (
            "work_order_mirrors", "work_orders",
            {"shop_id": shop_filter, "labors": {"$elemMatch": {"assigned_mechanics": {"$elemMatch": stale}}}},
            {"$set": {"labors.$[b].assigned_mechanics.$[m].name": name}},
            [{"b.assigned_mechanics": {"$elemMatch": {"user_id": {"$in": ids}}}}, {"m.user_id": {"$in": ids}}],
        ),
        (
            "done_marks", "work_orders",
            {"shop_id": shop_filter, "mechanic_done_marks": {"$elemMatch": stale}},
            {"$set": {"mechanic_done_marks.$[m].name": name}},
            [{"m.user_id": {"$in": ids}}],
        ),
        (
            "time_logs", "wo_time_logs",
            {"shop_id": shop_filter, "user_id": {"$in": ids}, "user_name": {"$ne": name}},
            {"$set": {"user_name": name}},
            None,
        ),
        (
            "calendar_mechanics", "calendar_events",
            {"shop_id": shop_filter, "mechanics": {"$elemMatch": {"id": {"$in": ids}, "name": {"$ne": name}}}},
            {"$set": {"mechanics.$[m].name": name}},
            [{"m.id": {"$in": ids}}],
        ),
        (
            "calendar_mechanic_name", "calendar_events",
            {"shop_id": shop_filter, "mechanic_id": {"$in": ids}, "mechanic_name": {"$ne": name}},
            {"$set": {"mechanic_name": name}},
            None,
        ),
    )


def refresh_user_name_snapshots(master_db, tenant_id, user_id, new_name, *, dry_run: bool = False) -> dict:
    """
    Переписать снимки имени пользователя во всех shop-базах его тенанта на
    new_name (формы — SNAPSHOT_COUNTER_KEYS). id в данных бывают и ObjectId, и
    строкой — матчатся оба. updated_at, labors_rev и Activity Journal НЕ
    трогаются: это не правка документа (живой опрос страниц WO не должен
    перезагружаться, CAS сохранений не должен спотыкаться). Работает и вне
    запроса (скрипты): shop-базы — по master.shops тенанта через клиент
    master_db, не через get_shop_db (тот привязан к сессии). Пользователь
    обязан принадлежать тенанту, иначе ничего не делается. Идемпотентен;
    ошибки — в лог и в счётчик errors, наружу не бросает.

    Возвращает {"shops", "errors", <SNAPSHOT_COUNTER_KEYS>}: число shop-баз и
    документов, где снимок изменён (dry_run=True — где был бы изменён).
    """
    counters = {"shops": 0, "errors": 0, **{key: 0 for key in SNAPSHOT_COUNTER_KEYS}}
    name = str(new_name or "").strip()
    uid = oid(user_id)
    tenant_values = tenant_id_values(tenant_id)
    if not name or not uid or not tenant_values or master_db is None:
        return counters
    ids = [uid, str(uid)]

    try:
        if not master_db.users.find_one({"_id": uid, "tenant_id": {"$in": tenant_values}}, {"_id": 1}):
            return counters
        shops = list(master_db.shops.find(
            {"tenant_id": {"$in": tenant_values}},
            {"db_name": 1, "database": 1, "db": 1, "mongo_db": 1, "shop_db": 1},
        ))
    except Exception:
        counters["errors"] += 1
        _log_exception("User names: failed to list shops of tenant %s for user %s", tenant_id, uid)
        return counters

    for shop in shops:
        db_name = shop_db_name(shop)
        if not db_name:
            continue
        try:
            shop_db = master_db.client[db_name]
        except Exception:
            counters["errors"] += 1
            _log_exception("User names: bad shop database name %r of shop %s", db_name, shop.get("_id"))
            continue
        counters["shops"] += 1
        shop_ids = [shop["_id"], str(shop["_id"])]
        for key, collection, query, update, array_filters in _snapshot_operations(shop_ids, ids, name):
            try:
                if dry_run:
                    counters[key] += shop_db[collection].count_documents(query)
                else:
                    res = shop_db[collection].update_many(query, update, array_filters=array_filters)
                    counters[key] += res.modified_count
            except Exception:
                counters["errors"] += 1
                _log_exception(
                    "User names: failed to refresh %s snapshots in %s for user %s", key, db_name, uid,
                )
    return counters
