"""
Ревизия редактируемого состояния WO и edit views — опора трёхстороннего
merge сохранений (labor_merge.py).

Ревизия (labors_rev)
--------------------
work_orders.labors_rev (int; нет поля == 0) — ревизия редактируемого
состояния WO: массив labors И mechanic_done_marks/mechanic_done. Любая
запись, которая их меняет, делает $inc labors_rev и фильтрует по ревизии,
прочитанной в том же цикле (compare-and-swap).

    labors_rev(wo) -> int
    rev_filter(rev) -> dict          {"labors_rev": rev}, при rev == 0 —
                                     {"labors_rev": {"$in": [None, 0]}}
                                     (None матчит и отсутствующее поле)
    with_rev(projection) -> projection, гарантированно включающая labors_rev
                                     (None — весь документ, как в find_one)
    bump_rev(update) -> update с {"$inc": {"labors_rev": 1}}
    ConcurrentUpdateError            CAS не прошёл MAX_CAS_ATTEMPTS раз подряд
    CONCURRENT_UPDATE_RESPONSE       тело 409 для сохранений

Сессия устройства
-----------------
    edit_session_id() -> str         session["wo_edit_sid"] (создаётся при
                                     первом вызове). У каждого браузера и
                                     установки приложения своя cookie-сессия —
                                     sid разделяет устройства под одним логином.

Edit views — коллекция wo_edit_views (shop-база)
------------------------------------------------
{_id, shop_id, work_order_id, user_id (str), sid, channel: office |
 mechanic_web | mobile, kind: load | sent | merged, protocol: 0 | 1,
 labors: [компактные строки], wo_fields? {customer_id, unit_id,
 work_order_date, is_taxable, mileage}, minted? [labor_id] (sent),
 labors_rev, created_at}. Индексы и TTL 14 дней —
 extensions.ensure_wo_edit_views_indexes.

    work_rows_from_wo(wo) -> list[row]   строки WO в рабочей форме (current
                                         для merge_labors): _stored_block_to_payload
                                         + client_key + minted блока
    compact_row(row) / compact_rows(rows) / compact_rows_from_wo(wo)
                                         компактная JSON-safe строка: только поля
                                         merge, misc поднят в misc_items
    wo_fields_of(wo) -> dict             поля WO уровня документа (офисная вьюха)
    running_labor_ids(shop_db, shop_id, work_order_id) -> set[str]
    record_view(shop_db, shop_id, wo, *, user_id, sid, channel, kind="load",
                protocol=0, rows=None, minted=None, wo_fields=None,
                labors_rev=None, can_edit=True, now=None) -> str
        Записывает вьюху; возвращает её id или "" (нет права — can_edit,
        оплаченный WO, ошибка). Никогда не бросает. rows=None — строки
        берутся из wo (должен быть документом ПОСЛЕ ensure_wo_labor_ids);
        rows=MergeResult.belief для sent-вьюхи (+ minted=MergeResult.minted).

Выбор base — find_base
----------------------
    find_base(shop_db, shop_id, work_order_id, *, data, channel, user_id,
              sid, request_started_at, mine_rows, is_mechanic) -> BaseChoice
  1. Новый клиент (merge_protocol: 1 или ключ view_id): вьюха с этим _id
     того же магазина, WO и пользователя. Не нашлась/истекла/пустой id →
     rows=None (консервативно) + заметка UNRESOLVED_BASE_NOTE +
     force_foreign. Легаси-поиска для нового клиента нет никогда.
  2. Легаси office / mechanic_web → rows=None (консервативно).
  3. Легаси mobile, механик — правило «подхвачен ли рефетч»
     (choose_adopted_view: по id в mine, без часов; первая load после sent —
     её рефетч, новой формы не доказывает) + защита от пустой формы
     (failed_load).
  4. Легаси mobile, менеджер — консенсус load-вьюх (consensus_base).
Правила 3–4 — чистые функции над списком вьюх (тестируются без БД);
выборка — вьюхи (shop, wo, user, sid, channel "mobile", protocol 0) с
created_at < request_started_at. Общий принцип: форма держит только id
своей load-вьюхи, и вьюха, в которой нет какого-то id из mine, не может
быть её основой.

BaseChoice.ctx_kwargs() — готовые поля MergeContext (legacy, base_minted,
extra_notes, force_foreign).
"""
from __future__ import annotations

import copy
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from flask import current_app, has_app_context, session

from app.blueprints.work_orders.services.common import as_bool, f64, oid, part_unit_key, round2
from app.blueprints.work_orders.services.labor_merge import (
    ALL_PART_FIELDS,
    MONEY_FIELDS,
    TEXT_FIELDS,
    lift_misc,
    match_parts,
    norm_part_field,
    norm_qty,
    norm_row_field,
    norm_text,
)
from app.blueprints.work_orders.services.mechanic_editor import _stored_block_to_payload

_log = logging.getLogger(__name__)

COLLECTION = "wo_edit_views"
SESSION_SID_KEY = "wo_edit_sid"
CHANNELS = ("office", "mechanic_web", "mobile")
KINDS = ("load", "sent", "merged")

MAX_CAS_ATTEMPTS = 5
CONCURRENT_UPDATE_RESPONSE = {
    "ok": False,
    "error": "concurrent_update",
    "message": "This work order is being updated by someone else right now. Please try again.",
}
UNRESOLVED_BASE_NOTE = (
    "Couldn't confirm what you started from, so nothing was removed. Please review the work order."
)

# Сколько форма правки старого приложения менеджера может простоять
# открытой: кандидаты консенсуса без sent-вьюхи и load-вьюхи «другой формы»
# до неё (ревью D1-02 / D1-V1). Больше кандидатов — меньше строк base (по
# отсутствию удаляется только то, что было во всех), поэтому окно широкое.
MANAGER_FORM_WINDOW = timedelta(hours=24)
LEGACY_VIEWS_LIMIT = 100


def _log_exception(message: str, *args) -> None:
    """current_app.logger, а вне app context — модульный логгер."""
    if has_app_context():
        current_app.logger.exception(message, *args)
    else:
        _log.exception(message, *args)


def _log_warning(message: str, *args) -> None:
    if has_app_context():
        current_app.logger.warning(message, *args)
    else:
        _log.warning(message, *args)


def _utc(v) -> datetime | None:
    if isinstance(v, datetime):
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)
    if isinstance(v, str) and v.strip():
        try:
            return _utc(datetime.fromisoformat(v.strip().replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ───────────────────────── ревизия и CAS ─────────────────────────

class ConcurrentUpdateError(Exception):
    """Запись WO по ревизии не удалась MAX_CAS_ATTEMPTS раз подряд."""


def labors_rev(wo) -> int:
    """Ревизия редактируемого состояния WO (нет поля / мусор → 0)."""
    if not isinstance(wo, dict):
        return 0
    v = wo.get("labors_rev")
    if isinstance(v, bool) or v is None:
        return 0
    if isinstance(v, int):
        return v
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# Внутренний алиас: у record_view есть параметр labors_rev.
_rev_of = labors_rev


def rev_filter(rev) -> dict:
    """Условие CAS по ревизии. Без ревизии (0) — поле отсутствует, null или 0."""
    rev = labors_rev({"labors_rev": rev})
    if rev:
        return {"labors_rev": rev}
    return {"labors_rev": {"$in": [None, 0]}}


def with_rev(projection=None):
    """Проекция чтения, которое кормит rev_filter/labors_rev: labors_rev
    обязан в неё попасть (иначе 0 и CAS никогда не совпадёт)."""
    if projection is None:
        return None
    if isinstance(projection, (list, tuple)):
        fields = list(projection)
        if "labors_rev" not in fields:
            fields.append("labors_rev")
        return fields
    if isinstance(projection, dict):
        out = dict(projection)
        inclusion = any(k != "_id" and v not in (0, False) for k, v in out.items()) or (
            set(out) == {"_id"} and out["_id"] not in (0, False)
        )
        if inclusion:
            out["labors_rev"] = 1
        else:
            out.pop("labors_rev", None)  # проекция-исключение: labors_rev не исключаем
        return out
    return projection


def bump_rev(update: dict) -> dict:
    """update-документ с $inc labors_rev 1 (прочие $inc сохраняются)."""
    out = dict(update or {})
    inc = dict(out.get("$inc") or {})
    inc["labors_rev"] = 1
    out["$inc"] = inc
    return out


# ───────────────────────── сессия устройства ─────────────────────────

def edit_session_id() -> str:
    """Id сессии устройства для edit views: создаётся один раз на cookie-сессию."""
    sid = session.get(SESSION_SID_KEY)
    if not isinstance(sid, str) or not sid.strip():
        sid = secrets.token_hex(8)
        session[SESSION_SID_KEY] = sid
        session.modified = True
    return sid


# ───────────────────────── компактные строки ─────────────────────────

def work_rows_from_wo(wo: dict) -> list[dict]:
    """Строки WO в рабочей форме merge (current): база закреплена в
    labor_total, client_key и метка minted блока — переносятся."""
    wo = wo if isinstance(wo, dict) else {}
    totals_doc = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    totals_blocks = totals_doc.get("labors") if isinstance(totals_doc.get("labors"), list) else []
    out = []
    for i, block in enumerate(wo.get("labors") or []):
        if not isinstance(block, dict):
            continue
        block_totals = totals_blocks[i] if i < len(totals_blocks) and isinstance(totals_blocks[i], dict) else {}
        row = _stored_block_to_payload(block, block_totals)
        row["assigned_mechanics"] = copy.deepcopy(row.get("assigned_mechanics") or [])
        ck = norm_text(block.get("client_key"))
        if ck:
            row["client_key"] = ck
        if isinstance(block.get("minted"), dict):
            row["minted"] = dict(block["minted"])
        out.append(row)
    return out


def _json_safe(v):
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, datetime):
        return (_utc(v) or v).isoformat()
    return v


def _compact_part(p: dict) -> dict:
    unit_raw = p.get("unit") if p.get("unit") is not None else p.get("unit_of_measure")
    core_raw = p.get("core_charge") if p.get("core_charge") is not None else p.get("core_cost")
    return {
        "part_id": norm_text(p.get("part_id")),
        "part_number": norm_text(p.get("part_number")),
        "description": norm_text(p.get("description")),
        "qty": norm_qty(p.get("qty")),
        "unit": part_unit_key(unit_raw),
        "cost": round2(p.get("cost")),
        "price": round2(p.get("price")),
        "core_charge": round2(core_raw),
        "misc_charge": round2(p.get("misc_charge")),
        # JSON misc-позиций первой строки уже поднят в misc_items; здесь —
        # только легаси-текст отдельной запчасти.
        "misc_charge_description": norm_text(p.get("misc_charge_description")),
        "one_time_part": as_bool(p.get("one_time_part")),
    }


def _compact_assigned(v) -> list[dict]:
    out = []
    for a in v if isinstance(v, list) else []:
        if not isinstance(a, dict):
            continue
        uid = norm_text(a.get("user_id") or a.get("id"))
        if not uid:
            continue
        # name/role сравнениями не используются — нужны только чтобы
        # восстановленная из base строка не потеряла подпись назначения.
        out.append({
            "user_id": uid,
            "percent": round2(a.get("percent")),
            "name": norm_text(a.get("name")),
            "role": norm_text(a.get("role")),
        })
    return out


def _compact_minted(mk):
    if not isinstance(mk, dict):
        return None
    at = mk.get("at")
    at_utc = _utc(at)
    return {
        "by": norm_text(mk.get("by")),
        "sid": norm_text(mk.get("sid")),
        "ck": norm_text(mk.get("ck")),
        "at": at_utc.isoformat() if at_utc is not None else norm_text(at),
    }


def compact_row(row: dict) -> dict:
    """Рабочая строка (или belief/компактная) → компактная строка вьюхи."""
    row = row if isinstance(row, dict) else {}
    items, parts = lift_misc(row)
    total = f64(row.get("labor_total"))
    return {
        "labor_id": norm_text(row.get("labor_id")),
        "client_key": norm_text(row.get("client_key")),
        "minted": _compact_minted(row.get("minted")),
        "description": norm_text(row.get("description")),
        "issue_description": norm_text(row.get("issue_description")),
        "hours": norm_text(row.get("hours")),
        "rate_code": norm_text(row.get("rate_code")),
        "labor_total": round2(total) if total is not None and total == total else None,
        "hours_source": norm_text(row.get("hours_source")),
        "assigned_mechanics": _compact_assigned(row.get("assigned_mechanics")),
        "misc_items": _json_safe(items) if items is not None else None,
        "parts": [_compact_part(p) for p in parts],
    }


def compact_rows(rows) -> list[dict]:
    return [compact_row(r) for r in (rows or []) if isinstance(r, dict)]


def compact_rows_from_wo(wo: dict) -> list[dict]:
    return compact_rows(work_rows_from_wo(wo))


def wo_fields_of(wo: dict) -> dict:
    """Поля уровня документа, которые офис шлёт целиком (3-way на уровне WO)."""
    wo = wo if isinstance(wo, dict) else {}
    totals = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    return {
        "customer_id": norm_text(wo.get("customer_id")),
        "unit_id": norm_text(wo.get("unit_id")),
        "work_order_date": wo.get("work_order_date"),
        "is_taxable": bool(totals.get("is_taxable")) if "is_taxable" in totals else None,
        "mileage": wo.get("mileage"),
    }


def running_labor_ids(shop_db, shop_id, work_order_id) -> set[str]:
    """labor_id, на которых сейчас идёт таймер (такие строки merge не удаляет)."""
    wo_oid = oid(work_order_id)
    if shop_db is None or not wo_oid:
        return set()
    out = set()
    for log in shop_db.wo_time_logs.find(
        {"shop_id": shop_id, "work_order_id": wo_oid, "stopped_at": None},
        {"labor_id": 1},
    ):
        lid = norm_text(log.get("labor_id"))
        if lid:
            out.add(lid)
    return out


# ───────────────────────── запись вьюхи ─────────────────────────

def record_view(
    shop_db,
    shop_id,
    wo: dict,
    *,
    user_id,
    sid: str,
    channel: str,
    kind: str = "load",
    protocol: int = 0,
    rows=None,
    minted=None,
    wo_fields: dict | None = None,
    labors_rev: int | None = None,
    can_edit: bool = True,
    now: datetime | None = None,
    allow_paid: bool = False,
) -> str:
    """Записать edit view; id вьюхи или "" (нет права, paid, ошибка).

    can_edit — проверка вызывающего: офис — work_orders.edit, механик-режим —
    work_orders.create, менеджер в приложении — work_orders.edit. Объём
    ограничен TTL, во вьюхе только поля merge. Никогда не бросает.
    allow_paid — записать и для оплаченного WO (только рендер страницы
    деталей офиса: после Unpaid / удаления платежа без перезагрузки у
    вкладки должна быть база для Save; сохранения paid-WO отклоняют guard'ы
    и CAS-фильтр, так что вьюха используется, только когда WO ушёл из paid).
    """
    try:
        if not can_edit or not isinstance(wo, dict) or not wo.get("_id"):
            return ""
        if not allow_paid and norm_text(wo.get("status")).lower() == "paid":
            return ""
        if channel not in CHANNELS or kind not in KINDS:
            raise ValueError(f"bad edit view channel/kind: {channel!r}/{kind!r}")
        compact = compact_rows(rows) if rows is not None else compact_rows_from_wo(wo)
        doc: dict[str, Any] = {
            "shop_id": shop_id,
            "work_order_id": wo["_id"],
            "user_id": norm_text(user_id),
            "sid": norm_text(sid),
            "channel": channel,
            "kind": kind,
            "protocol": 1 if protocol else 0,
            "labors": compact,
            "labors_rev": labors_rev if labors_rev is not None else _rev_of(wo),
            "created_at": now or _now(),
        }
        if kind == "sent":
            doc["minted"] = [norm_text(x) for x in (minted or []) if norm_text(x)]
        if wo_fields is not None:
            doc["wo_fields"] = dict(wo_fields)
        res = shop_db[COLLECTION].insert_one(doc)
        return str(res.inserted_id)
    except Exception:
        _log_exception("record_view failed (wo %s, channel %s, kind %s)", (wo or {}).get("_id"), channel, kind)
        return ""


# ───────────────────────── выбор base ─────────────────────────

@dataclass
class BaseChoice:
    """Результат find_base.

    rows       base для merge_labors (компактные строки) или None —
               консервативный merge (по отсутствию ничего не удаляется);
    legacy     запрос старого клиента (MergeContext.legacy);
    reason     почему так (журнал, тесты): view | view_unresolved |
               legacy_conservative | adopted_sent | adopted_sent_minted_gone |
               adopted_load | no_views | failed_load | consensus |
               no_candidates | lookup_failed;
    view_id    вьюха-основа ("" — синтетическая/нет);
    kind       load | sent | merged | consensus | "";
    minted     id из minted sent-вьюхи-основы (MergeContext.base_minted;
               без строк, которых уже нет в load после неё, кроме её
               первой load — рефетча — adopted_sent_minted_gone);
    wo_fields  поля WO уровня документа из вьюхи (офис) или None;
    note       заметка клиенту (MergeContext.extra_notes);
    force_foreign  клиенту обязательно перечитать WO.
    """

    rows: list | None
    legacy: bool
    reason: str
    view_id: str = ""
    kind: str = ""
    minted: frozenset = frozenset()
    wo_fields: dict | None = None
    labors_rev: int | None = None
    note: str = ""
    force_foreign: bool = False

    def ctx_kwargs(self) -> dict:
        return {
            "legacy": self.legacy,
            "base_minted": self.minted,
            "extra_notes": (self.note,) if self.note else (),
            "force_foreign": self.force_foreign,
        }


def is_new_client(data) -> bool:
    """Новый клиент (merge_protocol 1) называет свою вьюху сам."""
    if not isinstance(data, dict):
        return False
    if "view_id" in data:
        return True
    mp = data.get("merge_protocol")
    if mp is None:
        return False
    try:
        return int(mp) >= 1
    except (TypeError, ValueError):
        return as_bool(mp)


def _sorted_views(views, before) -> list[tuple[datetime, dict]]:
    """load/sent-вьюхи раньше before, по времени (старые первыми)."""
    before = _utc(before)
    out = []
    for v in views or []:
        if not isinstance(v, dict) or v.get("kind") not in ("load", "sent"):
            continue
        ts = _utc(v.get("created_at"))
        if ts is None or (before is not None and ts >= before):
            continue
        out.append((ts, v))
    out.sort(key=lambda x: x[0])
    return out


def _mine_ids(mine_rows) -> set[str]:
    return {
        norm_text(r.get("labor_id"))
        for r in (mine_rows or [])
        if isinstance(r, dict) and norm_text(r.get("labor_id"))
    }


def _view_ids(view) -> set[str]:
    """labor_id строк вьюхи."""
    rows = (view or {}).get("labors") if isinstance(view, dict) else None
    return {
        norm_text(r.get("labor_id"))
        for r in (rows if isinstance(rows, list) else [])
        if isinstance(r, dict) and norm_text(r.get("labor_id"))
    }


def _minted_of(view) -> frozenset:
    return frozenset(norm_text(x) for x in ((view or {}).get("minted") or []) if norm_text(x))


def _adopt(views, mine_rows, request_started_at) -> tuple[dict | None, str, frozenset]:
    """(вьюха-основа, причина, base_minted) — правило choose_adopted_view."""
    vs = _sorted_views(views, request_started_at)
    if not vs:
        return None, "no_views", frozenset()
    sents = [(ts, v) for ts, v in vs if v.get("kind") == "sent"]
    s_ts, s_view = sents[-1] if sents else (None, None)
    loads = [v for ts, v in vs if v.get("kind") == "load" and (s_ts is None or ts > s_ts)]
    s_minted = _minted_of(s_view)
    if s_view is None or not s_minted:
        # Без новых строк в последнем сохранении старое приложение WO не
        # перечитывает: каждая load после него — новое открытие формы.
        if loads:
            return loads[-1], "adopted_load", frozenset()
        return s_view, "adopted_sent", s_minted
    mine_ids = _mine_ids(mine_rows)
    # id, которых приложение с состоянием S знать не может: id новых строк S
    # (minted) оно узнаёт только из load после S, чужие строки — тоже.
    learned = mine_ids - (_view_ids(s_view) - s_minted)
    if learned:
        # Приложение держит load после S: новейшую, в которой есть ВСЕ id mine
        # (id, которых в load нет, приложение из неё взять не могло).
        for view in reversed(loads):
            if mine_ids <= _view_ids(view):
                return view, "adopted_load", frozenset()
        return s_view, "adopted_sent", s_minted
    # Доказательства нет — base S, сколько бы времени ни прошло (ревью
    # D1-01 / D4-03: медленный рефетч, выброшенный приложением, через 15 с
    # считался новым открытием формы, и его чужие строки удалялись). Для
    # строк это безопасно: строк, которых нет в S, по отсутствию не удалить.
    # Но строки S, которых нет в load, открывшей форму заново после S (их
    # удалил кто-то другой), к id-less строкам mine не привязываем: в новой
    # форме могли добавить такую же работу — её надо создать, а не выбросить
    # как «удалённую другим». Первая load после S — рефетч самого S (старое
    # приложение делает его сразу после сохранения с id-less строками):
    # подхвачен он или выброшен, не узнать, поэтому открытия формы он не
    # доказывает (ревью F1-RR-02: механик печатал сквозь рефетчи, офис
    # удалил его новую работу — рефетч без неё «доказывал» новую форму, и
    # следующий автосейв создавал работу заново). Доказывает следующая load.
    gone = frozenset(x for x in s_minted if any(x not in _view_ids(v) for v in loads[1:]))
    if gone:
        return s_view, "adopted_sent_minted_gone", s_minted - gone
    return s_view, "adopted_sent", s_minted


def choose_adopted_view(views, mine_rows, request_started_at) -> tuple[dict | None, str]:
    """Легаси-приложение механика: какую вьюху клиент реально держит.

    Каждый GET механика — открытие формы или рефетч после автосейва (тот
    приходит один раз, если в сохранении были строки без id, и выбрасывается,
    если пользователь печатал). S — новейшая sent-вьюха, Ls — load-вьюхи
    после S (все, если S нет):
      - S нет или S.minted пуст (рефетча не было) — новейшая L (иначе S);
      - в mine есть id, которых приложение с состоянием S знать не могло
        (S.minted и чужие строки, появившиеся после S) — приложение
        подхватило L: новейшая L, содержащая все id mine (нет такой — S);
      - иначе — S (часы не смотрим: медленный рефетч остаётся рефетчем).
    Нет вьюх — (None, "no_views"). base_minted для S — choose_base_from_views:
    S.minted без строк, которых нет в load после S, кроме первой (первая —
    рефетч самого S, новой формы он не доказывает). Известная цена: механик
    подхватил этот рефетч (работы, удалённой другим, уже нет) и тут же, не
    открывая форму заново, добавил такую же работу — она снова считается
    удалённой другим; после нового открытия формы — создаётся.
    """
    view, reason, _minted = _adopt(views, mine_rows, request_started_at)
    return view, reason


def failed_load(base_rows, mine_rows) -> bool:
    """Форма открылась пустой (GET упал): в mine нет ни одного labor_id, а в
    base строки есть — такая base стёрла бы WO."""
    if not base_rows:
        return False
    return not _mine_ids(mine_rows)


def _as_index(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v == v and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _consensus_row(copies: list[dict], mine_row: dict | None) -> dict:
    """Синтетическая строка base из копий одной строки во всех кандидатах.

    Запчасти — только присутствующие во всех копиях; содержимое — из самой
    старой копии. Поле, значение которого у клиента совпадает со значением
    хотя бы в одной копии, берётся из mine: клиент его не правил (он видел
    одну из этих версий), и старая копия не превратит его в «правку».
    """
    oldest = copies[0]
    out = copy.deepcopy(oldest)
    items, parts = lift_misc(oldest)
    others = [lift_misc(c)[1] for c in copies[1:]]
    matches = [match_parts(parts, op) for op in others]
    keep = [i for i in range(len(parts)) if all(i in mt for mt in matches)]
    new_index = {old: new for new, old in enumerate(keep)}
    out_parts = [copy.deepcopy(parts[i]) for i in keep]

    if items is not None:
        kept_items = []
        for it in items:
            it = dict(it)
            if it.get("manual") is not True:
                pi = _as_index(it.get("partIndex"))
                if pi is not None and 0 <= pi < len(parts):
                    if pi not in new_index:
                        continue  # запчасть-владелец есть не во всех копиях
                    if new_index[pi] != pi:
                        it["partIndex"] = new_index[pi]
            kept_items.append(it)
        out["misc_items"] = kept_items
    else:
        out["misc_items"] = None
    out["parts"] = out_parts

    if isinstance(mine_row, dict):
        for f in TEXT_FIELDS + MONEY_FIELDS:
            if f in mine_row:
                mv = norm_row_field(f, mine_row.get(f))
                if any(norm_row_field(f, c.get(f)) == mv for c in copies):
                    out[f] = copy.deepcopy(mine_row.get(f))
        _m_items, m_parts = lift_misc(mine_row)
        for si, mi in match_parts(out_parts, m_parts).items():
            oi = keep[si]
            versions = [parts[oi]] + [others[k][matches[k][oi]] for k in range(len(others))]
            mp = m_parts[mi]
            for f in ALL_PART_FIELDS:
                if f not in mp:
                    continue
                mv = norm_part_field(f, mp.get(f))
                if any(norm_part_field(f, vp.get(f)) == mv for vp in versions):
                    out_parts[si][f] = copy.deepcopy(mp.get(f))
    return out


def consensus_base(views, request_started_at, mine_rows=None, *, window=MANAGER_FORM_WINDOW):
    """Легаси-приложение менеджера: экран деталей и форма ходят в один GET,
    пуш открывает детали поверх открытой формы, а форм одного WO в стеке
    может быть две — какая load-вьюха «формы», не узнать. Синтетическая base
    — строки, которые есть во ВСЕХ кандидатах (см. _consensus_row): форма
    держит одну из них, так что по отсутствию удаляется только то, что она
    точно видела. Кандидат — load-вьюха, в которой есть все labor_id mine
    (своих id в чужой load форма не держит), и:
      - без sent-вьюхи — за последние window (ревью D1-02: при окне 30 мин
        старая форма выпадала, и base становилась одна вьюха экрана деталей
        после пуша — работа механика, появившаяся между ними, удалялась);
      - с sent-вьюхой S — load после S, а также load до S за window, если
        форма, сохранившая S, знала строки, которых в этой load нет (это
        другая, более старая форма — она могла остаться открытой под новой;
        ревью D1-V1). Load формы, сохранившей S, в кандидаты не попадает:
        иначе удалить строку, добавленную до S, было бы нельзя;
      - с sent-вьюхой S и не больше чем одной load после S — все load до S
        за window (ревью F1-RR-03). Форма, сохранившая S, после Save
        закрывается на экран деталей, а он перечитывает WO (одна load);
        новая форма — ещё одна load (её открытие). Значит, сохраняет форма,
        открытая до S, — устаревшая, оставшаяся под новой: её load и load
        новой формы держат одни и те же id (работу добавили в новой форме
        или пока она была открыта), различить их нельзя, и без этого правила
        Save устаревшей формы удалял бы такую работу. Цена: не дошёл GET
        экрана деталей после Save — первое удаление строки, которой нет в
        старых load, не проходит (строка остаётся, со следующего Save — да).
        Остаток: между Save новой формы и Save устаревшей экран деталей
        перечитан больше одного раза (pull-to-refresh, ещё пуш) — правило
        D1-V1 выше, работа, добавленная в новой форме, удаляется.
    Нет кандидатов → (None, "no_candidates").
    """
    vs = _sorted_views(views, request_started_at)
    mine_ids = _mine_ids(mine_rows)
    start = _utc(request_started_at)

    def recent(ts) -> bool:
        return start is None or ts >= start - window

    loads = [(ts, v) for ts, v in vs if v.get("kind") == "load" and mine_ids <= _view_ids(v)]
    sents = [(ts, v) for ts, v in vs if v.get("kind") == "sent"]
    if sents:
        s_ts, s_view = sents[-1]
        saver_ids = _view_ids(s_view) - _minted_of(s_view)
        # Форма, сохранившая S, закрывается (router.back()) на экран деталей,
        # и он перечитывает WO — одна load; новая форма после S — ещё одна
        # (её открытие). Load после S не больше одной — сохраняет форма,
        # открытая до S: кандидаты — все load до S за window.
        stale_form = sum(1 for ts, v in vs if v.get("kind") == "load" and ts > s_ts) <= 1
        cands = [
            v for ts, v in loads
            if ts > s_ts or (recent(ts) and (stale_form or not saver_ids <= _view_ids(v)))
        ]
    else:
        cands = [v for ts, v in loads if recent(ts)]
    if not cands:
        return None, "no_candidates"

    by_id = []
    for v in cands:
        rows = {}
        for r in v.get("labors") or []:
            lid = norm_text(r.get("labor_id")) if isinstance(r, dict) else ""
            if lid:
                rows.setdefault(lid, r)
        by_id.append(rows)
    common = set(by_id[0])
    for rows in by_id[1:]:
        common &= set(rows)
    mine_by_id = {
        norm_text(r.get("labor_id")): r
        for r in (mine_rows or [])
        if isinstance(r, dict) and norm_text(r.get("labor_id"))
    }
    out, seen = [], set()
    for r in cands[0].get("labors") or []:
        lid = norm_text(r.get("labor_id")) if isinstance(r, dict) else ""
        if not lid or lid not in common or lid in seen:
            continue
        seen.add(lid)
        out.append(_consensus_row([rows[lid] for rows in by_id], mine_by_id.get(lid)))
    return out, "consensus"


def choose_base_from_views(views, *, channel: str, is_mechanic: bool, mine_rows, request_started_at) -> BaseChoice:
    """Base легаси-клиента по его вьюхам (правила 2–4 find_base, без БД)."""
    if channel != "mobile":
        return BaseChoice(rows=None, legacy=True, reason="legacy_conservative")
    if is_mechanic:
        view, reason, minted = _adopt(views, mine_rows, request_started_at)
        if view is None:
            return BaseChoice(rows=None, legacy=True, reason=reason)
        rows = list(view.get("labors") or [])
        if failed_load(rows, mine_rows):
            return BaseChoice(rows=None, legacy=True, reason="failed_load")
        kind = norm_text(view.get("kind"))
        if kind != "sent":
            minted = frozenset()
        return BaseChoice(
            rows=rows, legacy=True, reason=reason, view_id=str(view.get("_id") or ""),
            kind=kind, minted=minted, labors_rev=view.get("labors_rev"),
        )
    rows, reason = consensus_base(views, request_started_at, mine_rows=mine_rows)
    if rows is None:
        return BaseChoice(rows=None, legacy=True, reason=reason)
    if failed_load(rows, mine_rows):
        return BaseChoice(rows=None, legacy=True, reason="failed_load")
    return BaseChoice(rows=rows, legacy=True, reason=reason, kind="consensus")


def find_base(
    shop_db,
    shop_id,
    work_order_id,
    *,
    data,
    channel: str,
    user_id,
    sid: str,
    request_started_at,
    mine_rows,
    is_mechanic: bool,
) -> BaseChoice:
    """Base для merge сохранения — см. докстринг модуля. Не бросает."""
    wo_oid = oid(work_order_id)
    if is_new_client(data):
        view = None
        view_oid = oid((data or {}).get("view_id"))
        if view_oid and wo_oid and shop_db is not None:
            try:
                view = shop_db[COLLECTION].find_one({
                    "_id": view_oid,
                    "shop_id": shop_id,
                    "work_order_id": wo_oid,
                    "user_id": norm_text(user_id),
                })
            except Exception:
                _log_exception("edit view lookup failed (wo %s)", work_order_id)
                view = None
        if view is None or not isinstance(view.get("labors"), list):
            _log_warning(
                "edit view %r not resolved for wo %s / user %s: conservative merge",
                (data or {}).get("view_id"), work_order_id, user_id,
            )
            return BaseChoice(
                rows=None, legacy=False, reason="view_unresolved",
                note=UNRESOLVED_BASE_NOTE, force_foreign=True,
            )
        kind = norm_text(view.get("kind"))
        minted = frozenset(
            norm_text(x) for x in (view.get("minted") or []) if norm_text(x)
        ) if kind == "sent" else frozenset()
        return BaseChoice(
            rows=list(view["labors"]), legacy=False, reason="view",
            view_id=str(view["_id"]), kind=kind, minted=minted,
            wo_fields=view.get("wo_fields"), labors_rev=view.get("labors_rev"),
        )

    if channel != "mobile":
        return BaseChoice(rows=None, legacy=True, reason="legacy_conservative")
    if shop_db is None or not wo_oid:
        return BaseChoice(rows=None, legacy=True, reason="no_views")
    query: dict[str, Any] = {
        "shop_id": shop_id,
        "work_order_id": wo_oid,
        "user_id": norm_text(user_id),
        "sid": norm_text(sid),
        "channel": channel,
        "protocol": 0,
    }
    started = _utc(request_started_at)
    if started is not None:
        query["created_at"] = {"$lt": started}
    try:
        views = list(
            shop_db[COLLECTION].find(query).sort("created_at", -1).limit(LEGACY_VIEWS_LIMIT)
        )
    except Exception:
        _log_exception("legacy edit views lookup failed (wo %s)", work_order_id)
        return BaseChoice(rows=None, legacy=True, reason="lookup_failed")
    return choose_base_from_views(
        views, channel=channel, is_mechanic=is_mechanic,
        mine_rows=mine_rows, request_started_at=request_started_at,
    )


__all__ = [
    "BaseChoice",
    "CHANNELS",
    "COLLECTION",
    "CONCURRENT_UPDATE_RESPONSE",
    "ConcurrentUpdateError",
    "KINDS",
    "MANAGER_FORM_WINDOW",
    "MAX_CAS_ATTEMPTS",
    "UNRESOLVED_BASE_NOTE",
    "bump_rev",
    "choose_adopted_view",
    "choose_base_from_views",
    "compact_row",
    "compact_rows",
    "compact_rows_from_wo",
    "consensus_base",
    "edit_session_id",
    "failed_load",
    "find_base",
    "is_new_client",
    "labors_rev",
    "record_view",
    "rev_filter",
    "running_labor_ids",
    "with_rev",
    "wo_fields_of",
    "work_rows_from_wo",
]


# ───────────────────────── производная вьюха ─────────────────────────

def derive_view_with_rows(
    shop_db,
    shop_id,
    work_order_id,
    view_id,
    *,
    user_id,
    channel: str,
    row_fields: dict | None = None,
    labors_rev: int | None = None,
    now: datetime | None = None,
) -> str:
    """Новая вьюха из вьюхи клиента после его записи «сбоку» (не Save формы).

    Describe Issue и правка тайм-лога пишут в WO сразу; форма вкладки при
    этом не перечитывается, и база её следующего Save (вьюха view_id)
    выдавала бы эту собственную запись за чужую правку: Save откатывал бы
    более новый текст механика, воскрешал удалённую механиком работу или
    отказывался удалять строку («изменена другим»). Производная вьюха —
    копия view_id (тот же магазин, WO, пользователь и канал), у строк с
    labor_id из row_fields ({labor_id: {поле компактной строки: значение}})
    поля заменены; created_at — сейчас, labors_rev — ревизия после записи
    (None — как у исходной). Возвращает id новой вьюхи или "" (вьюха не
    нашлась / пустой id / ошибка — ничего не создаётся). Никогда не бросает.
    """
    try:
        view_oid = oid(view_id)
        wo_oid = oid(work_order_id)
        if shop_db is None or not view_oid or not wo_oid or channel not in CHANNELS:
            return ""
        src = shop_db[COLLECTION].find_one({
            "_id": view_oid,
            "shop_id": shop_id,
            "work_order_id": wo_oid,
            "user_id": norm_text(user_id),
            "channel": channel,
        })
        if not src or not isinstance(src.get("labors"), list):
            _log_warning(
                "edit view %r not resolved for wo %s / user %s: no derived view",
                view_id, work_order_id, user_id,
            )
            return ""
        patches = {
            norm_text(lid): fields
            for lid, fields in (row_fields or {}).items()
            if norm_text(lid) and isinstance(fields, dict)
        }
        rows = []
        for row in src["labors"]:
            row = copy.deepcopy(row)
            if isinstance(row, dict):
                fields = patches.get(norm_text(row.get("labor_id")))
                if fields:
                    row.update(_json_safe(copy.deepcopy(fields)))
            rows.append(row)
        doc = {k: copy.deepcopy(v) for k, v in src.items() if k != "_id"}
        doc["labors"] = rows
        doc["created_at"] = now or _now()
        doc["derived_from"] = view_oid
        if labors_rev is not None:
            doc["labors_rev"] = labors_rev
        res = shop_db[COLLECTION].insert_one(doc)
        return str(res.inserted_id)
    except Exception:
        _log_exception("derive_view_with_rows failed (wo %s, view %r)", work_order_id, view_id)
        return ""
