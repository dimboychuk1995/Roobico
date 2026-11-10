"""
Трёхстороннее слияние строк работ WO (labors): base × current × mine.

Раньше каждый путь сохранения писал ВЕСЬ массив labors из копии клиента,
и строки, которых не было в payload, удалялись — так пропадала работа
второго механика (пинг-понг автосейвов двух телефонов, устаревшая форма
офиса). Теперь сохранение — merge трёх версий:

  base     то, с чего начинал этот клиент (edit view, см. edit_views.py),
           или None — базы нет: консервативный режим, по отсутствию строки
           ничего не удаляется;
  current  строки WO в базе сейчас (чтение внутри CAS-цикла);
  mine     строки из запроса клиента.

Модуль чистый: без БД и без Flask-контекста. Всё, что зависит от магазина
(автоцены механика, сборка новой строки, генерация id), приходит
колбэками в MergeContext — адаптеры каналов подставляют
build_mechanic_labors_payload / _autofill_part и т.п.

Форма строк
-----------
Рабочая строка (work-form) — то, что отдаёт
mechanic_editor._stored_block_to_payload (labor base закреплена в
labor_total), плюс:
  client_key    ключ новой строки, сгенерированный клиентом (uuid);
  minted        {"by": str(user_id), "sid": sid, "at": datetime, "ck": client_key}
                — «эту строку создал такой-то запрос такого-то устройства».
                Пишется ядром в каждую новую строку, хранится в документе WO
                на уровне блока (рядом с labor_id) и привязывает id-less
                строки легаси-клиентов к уже созданным (ретрай, наложение
                запросов, неподхваченный рефетч);
  misc_items    (опц.) уже поднятый список misc-позиций блока (компактная
                строка edit view); если ключа нет — список поднимается из
                JSON в parts[0].misc_charge_description;
  hours_edited  (опц., только mine офиса) — менеджер правил часы руками;
  прочие ключи адаптеров переносятся, ядро их не читает.
Компактная строка edit view (edit_views.compact_row) — та же форма с
misc_items и строковыми id; ядро принимает обе.

Misc-позиции блока живут JSON-списком в parts[0].misc_charge_description.
Перед merge список поднимается на уровень блока, auto-позиции
(partIndex) привязываются к идентичности своей запчасти; после merge
список пишется на первую запчасть итогового списка с пересчитанными
partIndex. Список не изменился — пишется исходная строка байт-в-байт.

API
---
merge_labors(base, current, mine, spec, *, ctx) -> MergeResult
    base: list[row] | None   current: list[row]   mine: list[row]
    spec: MECHANIC_SPEC | MOBILE_MANAGER_SPEC | OFFICE_SPEC (FieldSpec)
    ctx:  MergeContext (или dict с теми же ключами)

MergeContext:
    user_id, sid          кто сохраняет (метки minted, привязка легаси-строк);
    now                   datetime UTC (метка minted.at);
    legacy                запрос без merge_protocol/view_id (старый клиент);
    running_labor_ids     labor_id, на которых сейчас идёт таймер (не удалять);
    rates                 {rate_code: hourly_rate} — пересчёт базы по tracked-правилу;
    new_block(raw)->row   новая строка (механик: build_mechanic_labors_payload
                          с сохранением labor_id/client_key); raw — копия строки
                          mine с уже выставленным итоговым labor_id;
    new_part(raw)->part   новая запчасть в существующей строке (механик:
                          _autofill_part); None — запчасть отбросить. Ключ
                          "_catalog_misc_items" в результате превращается в
                          auto misc-позиции (как _attach_catalog_misc_items);
    mint_id()->str        генерация labor_id (по умолчанию str(ObjectId()));
    is_mechanic_web       канал веб-страницы механика: старый JS шлёт qty ≥ 1,
                          поэтому qty 1 против base 0 = «не менял». Ядро
                          применяет квирк только при legacy=True;
    base_minted           BaseChoice.minted — id, выданные id-less строкам
                          клиента в его base-вьюхе (sent): только такие строки
                          base можно привязать к id-less строкам mine;
    extra_notes,
    force_foreign         из BaseChoice (вьюха нового клиента не нашлась).

MergeResult:
    rows              итоговые строки (work-form): строки mine в порядке mine,
                      затем строки current, которых клиент не знает, в порядке
                      current; пустые строки, которые легаси-клиент знает, но
                      не шлёт, — на своих местах (индексы клиента не съезжают).
    origins           выровнено с rows: {"kind", "mine_index", "current_index",
                      "money_from_mine"}; kind ∈ merged | client_key | mapped |
                      adopted | resurrected | new | unknown | kept | kept_silent.
                      money_from_mine — денежная группа строки взята из mine
                      (не tracked-правилом): офисный merged-путь берёт базу
                      клиента только для таких строк.
    labor_ids         выровнено 1:1 с mine (включая пропущенные строки; "" для
                      не-dict элементов).
    client_keys       {client_key: labor_id} для всех строк mine с client_key.
    minted            итоговые id всех id-less строк mine по порядку
                      (новые или привязанные) — в sent view.
    belief            «что теперь думает клиент» — следующая base (sent view):
                      строки mine с итоговыми id, редактируемые значения —
                      как у клиента, нередактируемые — из итога/base, но
                      никогда из mine для полей, которые клиент не шлёт.
    notes             ≤ 6 коротких английских фраз (без денег).
    foreign_any       клиенту нужно перечитать WO.
    foreign_structure строки/запчасти сохранены/пропущены/восстановлены,
                      misc ≠ mine или деньги строки/запчасти взяты не из mine
                      (кроме tracked-правила) — офису нельзя брать тоталы
                      клиента как есть (быстрый путь только при False).
    tracked_adjusted  labor_id, где tracked/preset-правило заменило часы/базу
                      mine (офисный быстрый путь подставляет labor_total строки
                      вместо базы клиента — как старое правило routes.py).
    stats             счётчики и списки id (журнал, тесты).

Значения на выходе: hours / rate_code / labor_total — как пришли из mine
или current (без приведения типов); пересчитанная tracked-правилом база —
float; labor_total None = «посчитать из hours × ставка». Строки и
запчасти, восстановленные из компактной base, несут part_id строкой
(normalize_parts_payload / compute_labors_and_totals приведут к ObjectId);
user_id назначений ядро возвращает в ObjectId само. Временный ключ
_catalog_misc_items в итог не попадает.

Советы адаптерам: id-less строкам current id перед merge НЕ выдавать
(ядро само сопоставит их с неизвестными id mine); compute_labors_and_totals
и офисная пересборка должны переносить client_key и minted.
"""
from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from bson import ObjectId

from app.blueprints.work_orders.services.common import as_bool, f64, part_unit_key, round2

log = logging.getLogger(__name__)

TEXT_FIELDS = ("description", "issue_description")
MONEY_FIELDS = ("hours", "rate_code", "labor_total")
ALL_PART_FIELDS = (
    "qty", "description", "cost", "price", "core_charge", "misc_charge",
    "misc_charge_description", "one_time_part", "unit",
)
_PART_MONEY_FIELDS = ("cost", "price", "core_charge", "misc_charge")
# Поля запчасти, от которых зависят деньги блока: взяты не из mine →
# офис не может взять тоталы клиента как есть.
_PART_TOTALS_FIELDS = ("qty", "cost", "price", "core_charge", "misc_charge", "misc_charge_description")

MAX_NOTES = 6
# Без base привязываем id-less строку легаси-клиента только к свежей
# метке minted (ретрай / двойной клик), не к строке недельной давности.
LEGACY_MAP_WINDOW = timedelta(minutes=10)
_LABEL_MAX = 40


# ───────────────────────── спецификации каналов ─────────────────────────

@dataclass(frozen=True)
class FieldSpec:
    """Что клиент канала редактирует.

    money_mode: "" — деньги не его (механик); "office" — группа (hours,
    rate_code), labor_total выводит клиент; "mobile_manager" — группа
    (hours, rate_code, labor_total).
    blank_rows_not_sent — легаси-клиенты не шлют строки без описания и
    запчастей (их отсутствие — не удаление).
    keep_part_description_when_empty — пустое описание запчасти от клиента
    не стирает сохранённое (поведение старого merge механика).
    legacy_array_fallback — форма канала, открытая до деплоя, может держать
    строки легаси-WO без сохранённых id (форма менеджера в приложении — id
    "", вкладка офиса — временные id): см. _Merger._legacy_array_fallback.
    Формы механика id получали всегда (его GET сохранял их и раньше).
    """

    name: str
    text_fields: tuple = TEXT_FIELDS
    money_mode: str = ""
    assigned_editable: bool = False
    part_fields: tuple = ("qty", "description")
    misc_editable: bool = False
    blank_rows_not_sent: bool = False
    keep_part_description_when_empty: bool = False
    legacy_array_fallback: bool = False

    @property
    def money_touch_fields(self) -> tuple:
        if self.money_mode == "office":
            return ("hours", "rate_code")
        if self.money_mode == "mobile_manager":
            return MONEY_FIELDS
        return ()


MECHANIC_SPEC = FieldSpec(
    name="mechanic",
    blank_rows_not_sent=True,
    keep_part_description_when_empty=True,
)
MOBILE_MANAGER_SPEC = FieldSpec(
    name="mobile_manager",
    money_mode="mobile_manager",
    # unit приложение менеджера не шлёт — остаётся из current.
    part_fields=(
        "qty", "description", "cost", "price", "core_charge", "misc_charge",
        "misc_charge_description", "one_time_part",
    ),
    misc_editable=True,
    blank_rows_not_sent=True,
    legacy_array_fallback=True,
)
OFFICE_SPEC = FieldSpec(
    name="office",
    money_mode="office",
    assigned_editable=True,
    part_fields=ALL_PART_FIELDS,
    misc_editable=True,
    legacy_array_fallback=True,
)


def _default_mint_id() -> str:
    return str(ObjectId())


@dataclass
class MergeContext:
    """Контекст merge — см. докстринг модуля."""

    user_id: Any = ""
    sid: str = ""
    now: datetime | None = None
    legacy: bool = False
    running_labor_ids: frozenset = frozenset()
    rates: dict = field(default_factory=dict)
    new_block: Callable[[dict], dict] | None = None
    new_part: Callable[[dict], dict | None] | None = None
    mint_id: Callable[[], str] | None = None
    is_mechanic_web: bool = False
    base_minted: frozenset = frozenset()
    extra_notes: tuple = ()
    force_foreign: bool = False

    def __post_init__(self):
        self.running_labor_ids = frozenset(
            norm_text(x) for x in (self.running_labor_ids or ()) if norm_text(x)
        )
        self.base_minted = frozenset(norm_text(x) for x in (self.base_minted or ()) if norm_text(x))
        self.rates = dict(self.rates or {})
        self.extra_notes = tuple(n for n in (self.extra_notes or ()) if n)
        if self.now is None:
            self.now = datetime.now(timezone.utc)


@dataclass
class MergeResult:
    rows: list = field(default_factory=list)
    origins: list = field(default_factory=list)
    labor_ids: list = field(default_factory=list)
    client_keys: dict = field(default_factory=dict)
    minted: list = field(default_factory=list)
    belief: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    foreign_any: bool = False
    foreign_structure: bool = False
    tracked_adjusted: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)


# ───────────────────────── нормализация для сравнений ─────────────────────────

def norm_text(v) -> str:
    if v is None:
        return ""
    return str(v).strip()


def norm_hours(v) -> float:
    s = norm_text(v)
    if not s:
        return 0.0
    n = f64(s)
    if n is None or n != n:
        return 0.0
    return round(n, 4)


def norm_money(v) -> float:
    return round2(v)


def norm_qty(v) -> int:
    """qty как его хранит normalize_parts_payload (целое), но 2.0 → 2:
    float с целым значением не превращается в «изменённое» 0."""
    if v is None:
        return 0
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v == v and v.is_integer() else 0
    s = str(v).strip()
    if not s:
        return 0
    try:
        return int(s)
    except ValueError:
        pass
    n = f64(s)
    if n is not None and n == n and float(n).is_integer():
        return int(n)
    return 0


def norm_assigned(v) -> tuple:
    out = []
    for a in v if isinstance(v, list) else []:
        if not isinstance(a, dict):
            continue
        uid = norm_text(a.get("user_id") or a.get("id"))
        if uid:
            out.append((uid, round2(a.get("percent"))))
    return tuple(sorted(out))


def norm_row_field(f: str, v):
    if f == "hours":
        return norm_hours(v)
    if f == "labor_total":
        return norm_money(v)
    if f == "assigned_mechanics":
        return norm_assigned(v)
    return norm_text(v)


def norm_part_field(f: str, v):
    if f == "qty":
        return norm_qty(v)
    if f in _PART_MONEY_FIELDS:
        return norm_money(v)
    if f == "unit":
        return part_unit_key(v)
    if f == "one_time_part":
        return as_bool(v)
    return norm_text(v)


def _as_parts(v) -> list:
    """parts строки как список: число/строка/dict от кривого клиента — [].

    Ядро никогда не падает на форме строки (ревью D1-06: parts: 5 в запросе
    менеджера давал 500, прежний код молча считал это пустым списком)."""
    if isinstance(v, list):
        return v
    if isinstance(v, tuple):
        return list(v)
    return []


def _clean_row(row: dict) -> dict:
    """Строка с parts-списком (копия, только если parts пришли не списком):
    колбэки адаптеров (new_block и т.п.) получают уже нормальную строку."""
    if "parts" in row and not isinstance(row.get("parts"), list):
        row = dict(row)
        row["parts"] = _as_parts(row.get("parts"))
    return row


def is_blank_row(row) -> bool:
    """Строка без описания и без запчастей — легаси-клиенты такие не шлют."""
    if not isinstance(row, dict):
        return True
    if norm_text(row.get("description")):
        return False
    return not any(isinstance(p, dict) for p in _as_parts(row.get("parts")))


# ───────────────────────── идентичность запчастей ─────────────────────────

def part_key(p: dict) -> str:
    """id:<part_id> | pn:<part_number lower> | anon:<description lower>."""
    pid = norm_text(p.get("part_id"))
    if pid:
        return "id:" + pid
    pn = norm_text(p.get("part_number")).lower()
    if pn:
        return "pn:" + pn
    return "anon:" + norm_text(p.get("description")).lower()


def part_keys(parts) -> list[str]:
    """key#n по списку: n — номер вхождения среди равных ключей."""
    counts: dict[str, int] = {}
    out = []
    for p in parts or []:
        k = part_key(p) if isinstance(p, dict) else "anon:"
        n = counts.get(k, 0)
        counts[k] = n + 1
        out.append(f"{k}#{n}")
    return out


def _is_anon(p: dict) -> bool:
    return not norm_text(p.get("part_id")) and not norm_text(p.get("part_number"))


# Содержимое строки запчасти для выбора пары среди одинаковых ключей /
# анонимных строк. Сравниваются только поля, которые есть у обеих копий
# (клиент механика цен не шлёт).
_CONTENT_FIELDS = ("qty", "price", "cost", "unit", "core_charge", "misc_charge")


def _same_content(pa: dict, pb: dict, fields: tuple) -> bool:
    shared = [f for f in fields if f in pa and f in pb]
    return bool(shared) and all(norm_part_field(f, pa.get(f)) == norm_part_field(f, pb.get(f)) for f in shared)


def match_parts(a_parts, b_parts, *, anon_fallback: bool = True) -> dict[int, int]:
    """Сопоставление запчастей двух копий одной строки: {индекс в a: индекс в b}.

    По порядку: (1) точный ключ (id / номер / описание): строки с одним
    ключом — по вхождению (key#n), а если в копиях их разное число (одну
    удалили или добавили) — сначала строки с одинаковым содержимым (qty,
    цены, единица, описание), остальные по вхождению (ревью D1-05: после
    удаления первой из двух строк одной запчасти вхождения съезжали и
    уцелевшая строка «становилась» удалённой); (2) алиас — запчасть без
    part_id ↔ запчасть с тем же part_number (одноразовую строку сервер
    привязал к каталогу); (3) anon_fallback — оставшиеся анонимные строки:
    сначала с одинаковым содержимым, затем по позиции.
    """
    a = [p if isinstance(p, dict) else {} for p in _as_parts(a_parts)]
    b = [p if isinstance(p, dict) else {} for p in _as_parts(b_parts)]
    groups_a: dict[str, list[int]] = {}
    groups_b: dict[str, list[int]] = {}
    for i, p in enumerate(a):
        groups_a.setdefault(part_key(p), []).append(i)
    for j, p in enumerate(b):
        groups_b.setdefault(part_key(p), []).append(j)
    out: dict[int, int] = {}
    used: set[int] = set()
    for key, ia in groups_a.items():
        free = list(groups_b.get(key) or [])
        if not free:
            continue
        if len(ia) != len(free):
            for i in ia:
                for j in free:
                    if _same_content(a[i], b[j], _CONTENT_FIELDS + ("description",)):
                        out[i] = j
                        free.remove(j)
                        break
        for i, j in zip([i for i in ia if i not in out], free):
            out[i] = j
    used.update(out.values())
    for i, pa in enumerate(a):
        if i in out:
            continue
        pn = norm_text(pa.get("part_number")).lower()
        if not pn:
            continue
        has_id = bool(norm_text(pa.get("part_id")))
        for j, pb in enumerate(b):
            if j in used or norm_text(pb.get("part_number")).lower() != pn:
                continue
            if has_id and norm_text(pb.get("part_id")):
                continue
            out[i] = j
            used.add(j)
            break
    if not anon_fallback:
        return out
    rest_a = [i for i, pa in enumerate(a) if i not in out and _is_anon(pa)]
    rest_b = [j for j, pb in enumerate(b) if j not in used and _is_anon(pb)]
    # Описание анонимной строки и есть её ключ — оно разошлось; одинаковое
    # остальное содержимое — пара надёжнее позиции.
    for i in rest_a:
        for j in rest_b:
            if j not in used and _same_content(a[i], b[j], _CONTENT_FIELDS):
                out[i] = j
                used.add(j)
                break
    for i, j in zip([i for i in rest_a if i not in out], [j for j in rest_b if j not in used]):
        out[i] = j
        used.add(j)
    return out


# ───────────────────────── misc-позиции блока ─────────────────────────

def parse_misc_list(raw) -> list[dict] | None:
    """JSON-список misc-позиций или None (не строка / не JSON-список)."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s.startswith("["):
        return None
    try:
        parsed = json.loads(s)
    except ValueError:
        return None
    if not isinstance(parsed, list):
        return None
    return [dict(it) for it in parsed if isinstance(it, dict)]


def split_misc_items(parts) -> tuple[list[dict] | None, list[dict], Any]:
    """(items | None, копии parts, исходная строка | None).

    JSON-список с parts[0].misc_charge_description поднимается на уровень
    блока, у первой запчасти поле очищается. Не-JSON текст (легаси misc
    отдельной строки) остаётся на своей запчасти.
    """
    out = [dict(p) for p in _as_parts(parts) if isinstance(p, dict)]
    if not out:
        return None, out, None
    raw = out[0].get("misc_charge_description")
    items = parse_misc_list(raw)
    if items is None:
        return None, out, None
    out[0]["misc_charge_description"] = ""
    return items, out, raw


def lift_misc(row: dict) -> tuple[list[dict] | None, list[dict]]:
    """(items | None, parts) строки так, как её видит ядро: готовый
    row["misc_items"] (компактная строка) или JSON с parts[0]."""
    if "misc_items" in row:
        raw_items = row.get("misc_items")
        items = (
            [dict(it) for it in raw_items if isinstance(it, dict)]
            if isinstance(raw_items, list) else None
        )
        return items, [dict(p) for p in _as_parts(row.get("parts")) if isinstance(p, dict)]
    items, parts, _raw = split_misc_items(row.get("parts"))
    return items, parts


def _as_index(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v == v and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return None


def _misc_qty(v):
    n = f64(v)
    if n is None or n != n:
        return None
    return round(n, 4)


@dataclass
class _Entry:
    ident: tuple
    canon: str
    raw: dict
    slot: tuple | None


def _slot_str(slot) -> str:
    return f"{slot[0]}:{slot[1]}"


def _misc_entries(items, slot_of: Callable[[int], tuple], n_parts: int) -> list[_Entry]:
    """Позиции → (идентичность, каноническое значение, исходный dict, слот).

    auto (не manual, partIndex указывает на запчасть) — ("auto", слот
    владельца, описание, №); manual и «висячие» auto — ("manual",
    описание, цена, №). Каноника = (идентичность, цена, quantity, taxable).
    """
    out: list[_Entry] = []
    counts: dict[tuple, int] = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        desc = norm_text(it.get("description")).lower()
        price = round2(it.get("price"))
        slot = None
        if it.get("manual") is not True:
            pi = _as_index(it.get("partIndex"))
            if pi is not None and 0 <= pi < n_parts:
                slot = slot_of(pi)
        key = ("auto", _slot_str(slot), desc) if slot is not None else ("manual", desc, price)
        n = counts.get(key, 0)
        counts[key] = n + 1
        ident = key + (n,)
        canon = repr((ident, price, _misc_qty(it.get("quantity")), it.get("taxable") is not False))
        out.append(_Entry(ident, canon, it, slot))
    return out


def _canon(entries) -> tuple:
    return tuple(sorted(e.canon for e in entries))


def _merge_entries(e_b, e_c, e_m) -> list[_Entry]:
    """И mine, и current поменяли список: по позициям. Mine-версия, если
    mine позицию менял/добавил; версия current, если mine её не трогал;
    позиции base, удалённые current и не тронутые mine, — выпадают;
    новые позиции current — добавляются; удалённые mine — выпадают."""
    b_canon = {e.ident: e.canon for e in e_b}
    c_by = {e.ident: e for e in e_c}
    m_ids = {e.ident for e in e_m}
    out: list[_Entry] = []
    for e in e_m:
        if e.ident in b_canon and e.canon == b_canon[e.ident]:
            if e.ident in c_by:
                out.append(c_by[e.ident])
            continue
        out.append(e)
    for e in e_c:
        if e.ident not in m_ids and e.ident not in b_canon:
            out.append(e)
    return out


# ───────────────────────── прочие хелперы ─────────────────────────

def _clip(v) -> str:
    s = norm_text(v)
    return s if len(s) <= _LABEL_MAX else s[: _LABEL_MAX - 3].rstrip() + "..."


def _job_label(row) -> str:
    row = row if isinstance(row, dict) else {}
    label = _clip(row.get("description")) or _clip(row.get("issue_description")) or "(no description)"
    return f'"{label}"'


def _part_label(p) -> str:
    p = p if isinstance(p, dict) else {}
    label = _clip(p.get("description")) or _clip(p.get("part_number")) or "(no description)"
    return f'"{label}"'


def _as_utc(v):
    if isinstance(v, datetime):
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)
    if isinstance(v, str) and v.strip():
        try:
            parsed = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return _as_utc(parsed)
    return None


def _restore_assigned_ids(assigned) -> list:
    """Назначения из компактной base (user_id строкой) → ObjectId, как в базе."""
    out = []
    for a in assigned if isinstance(assigned, list) else []:
        if not isinstance(a, dict):
            continue
        a = dict(a)
        uid = a.get("user_id")
        if isinstance(uid, str) and ObjectId.is_valid(uid.strip()):
            a["user_id"] = ObjectId(uid.strip())
        out.append(a)
    return out


def _restore_minted(mk):
    if not isinstance(mk, dict):
        return mk
    out = dict(mk)
    at = _as_utc(out.get("at"))
    if at is not None:
        out["at"] = at
    return out


def _desc_compatible(a_row: dict, b_row: dict, *, exact: bool) -> bool:
    a = norm_text(a_row.get("description")).lower()
    b = norm_text(b_row.get("description")).lower()
    if exact:
        return a == b
    return bool(a and b and (a.startswith(b) or b.startswith(a)))


def _pair_rows(mine_items: list, cands: list, *, positional_ok: bool) -> list:
    """Пары (индекс mine, строка current) для id-less / неизвестных строк mine.

    mine_items — [(индекс mine, _Row)] в порядке mine, cands — строки current
    в порядке current. Порядок у них общий (строки добавляются в конец),
    поэтому пары не пересекаются: сначала по описанию — равному, затем
    префиксу (без учёта регистра), — затем, если позиция допустима,
    оставшиеся по порядку, но только внутри промежутков между уже
    найденными парами (ревью D1-07: при равном числе строк пары шли по
    позиции, и удалённая работа с новой обменивались id — время и фото
    чужой работы уезжали на другую). Непарные строки mine станут новыми,
    непарные строки current решит _decide_unconsumed.
    """
    if not mine_items or not cands:
        return []
    pairs: dict[int, int] = {}       # позиция в mine_items -> позиция в cands

    def crosses(a: int, b: int) -> bool:
        return any((a < pa) != (b < pc) for pa, pc in pairs.items())

    for exact in (True, False):
        for a, (_i, m) in enumerate(mine_items):
            if a in pairs:
                continue
            taken = set(pairs.values())
            for b, c in enumerate(cands):
                if b not in taken and _desc_compatible(m.src, c.src, exact=exact) and not crosses(a, b):
                    pairs[a] = b
                    break
    if positional_ok:
        for a in range(len(mine_items)):
            if a in pairs:
                continue
            taken = set(pairs.values())
            lo = max((pc for pa, pc in pairs.items() if pa < a), default=-1)
            hi = min((pc for pa, pc in pairs.items() if pa > a), default=len(cands))
            for b in range(lo + 1, hi):
                if b not in taken:
                    pairs[a] = b
                    break
    return [(mine_items[a][0], cands[pairs[a]]) for a in sorted(pairs)]


class _Row:
    """Строка, подготовленная к merge: id, ключ, поднятые misc-позиции."""

    __slots__ = ("src", "idx", "lid", "ck", "parts", "items", "raw_misc")

    def __init__(self, src: dict, idx: int):
        self.src = src
        self.idx = idx
        self.lid = norm_text(src.get("labor_id"))
        self.ck = norm_text(src.get("client_key"))
        if "misc_items" in src:
            self.items, self.parts = lift_misc(src)
            self.raw_misc = None
        else:
            self.items, self.parts, self.raw_misc = split_misc_items(src.get("parts"))


@dataclass
class _PartsResult:
    merged: list            # [(part, slot)] в итоговом порядке
    refs: list              # выровнено с mine.parts: запчасть-опора для belief (без misc JSON)
    new_slots: set
    resurrected: set
    mc: dict                # mine idx -> current idx
    bm: dict                # base idx -> mine idx
    bc: dict                # base idx -> current idx
    c_to_m: dict
    other: bool             # значение запчасти взято не из mine и отличается
    money_other: bool       # то же для полей, от которых зависят деньги
    structure: bool         # запчасти сохранены/пропущены/восстановлены
    skipped: set = field(default_factory=set)   # слоты ("m", mi) запчастей, удалённых другими (клиент их держит)


# ───────────────────────── сам merge ─────────────────────────

class _Merger:
    def __init__(self, base, current, mine, spec: FieldSpec, ctx: MergeContext):
        self.spec = spec
        self.ctx = ctx
        self.conservative = base is None
        # Квирк старого JS веб-страницы механика (qty ≥ 1): новый JS
        # (merge_protocol 1) шлёт qty как есть.
        self.quirk = bool(ctx.is_mechanic_web and ctx.legacy)
        self.base = None if base is None else [
            _Row(_clean_row(r), i) for i, r in enumerate(base) if isinstance(r, dict)
        ]
        self.cur = [_Row(_clean_row(r), i) for i, r in enumerate(current or []) if isinstance(r, dict)]
        self.mine = [(_Row(_clean_row(r), i) if isinstance(r, dict) else None) for i, r in enumerate(mine or [])]

        self.base_by_id: dict[str, _Row] = {}
        self.base_by_ck: dict[str, _Row] = {}
        for b in self.base or []:
            if b.lid:
                self.base_by_id.setdefault(b.lid, b)
            if b.ck:
                self.base_by_ck.setdefault(b.ck, b)
        self.cur_by_id: dict[str, _Row] = {}
        self.cur_by_ck: dict[str, _Row] = {}
        self.cur_dup: set[int] = set()
        for c in self.cur:
            if c.lid:
                if c.lid in self.cur_by_id:
                    self.cur_dup.add(c.idx)
                else:
                    self.cur_by_id[c.lid] = c
            if c.ck:
                self.cur_by_ck.setdefault(c.ck, c)

        self.used_ids: set[str] = set(self.cur_by_id) | set(self.base_by_id) | {
            m.lid for m in self.mine if m is not None and m.lid
        }
        self.consumed: set[int] = set()
        self.notes: list[str] = []
        self.foreign_any = bool(ctx.force_foreign)
        self.foreign_structure = False
        self.tracked: list[str] = []
        self.stats = {
            "rows_merged": 0, "rows_client_key": 0, "rows_mapped": 0, "rows_adopted": 0,
            "rows_new": 0, "rows_unknown": 0, "rows_legacy_array": 0,
            "kept_ids": [], "kept_silent_ids": [], "skipped_ids": [], "resurrected_ids": [],
            "deleted_ids": [],
            "parts_new": 0, "parts_kept": 0, "parts_skipped": 0, "parts_resurrected": 0,
            "parts_deleted": 0, "parts_rejected": 0, "misc_dropped": 0,
        }

    # ── заметки и флаги ──
    def _note(self, text: str) -> None:
        if text and text not in self.notes:
            self.notes.append(text)

    def _foreign(self, structure: bool = False) -> None:
        self.foreign_any = True
        if structure:
            self.foreign_structure = True

    # ── план: что делать с каждой строкой mine ──
    def _plan(self) -> list:
        ctx = self.ctx
        plan: list = [None] * len(self.mine)
        referenced = {m.lid for m in self.mine if m is not None and m.lid}
        seen: set[str] = set()
        for i, m in enumerate(self.mine):
            if m is None or not m.lid:
                continue
            if m.lid in seen:
                # Тот же id второй раз в одном запросе — отдельная новая строка.
                plan[i] = ("new", None, None)
                continue
            seen.add(m.lid)
            c = self.cur_by_id.get(m.lid)
            b = self.base_by_id.get(m.lid)
            if c is not None:
                plan[i] = ("merged", c, b)
                self.consumed.add(c.idx)
            elif b is not None:
                plan[i] = ("gone", None, b)
            else:
                plan[i] = ("unknown", None, None)

        # Новый клиент: id-less строка с client_key, который уже есть в
        # current, — это она же (ретрай, потерянный ответ).
        for i, m in enumerate(self.mine):
            if m is None or m.lid or plan[i] is not None or not m.ck:
                continue
            c = self.cur_by_ck.get(m.ck)
            if c is not None and c.idx not in self.consumed and (not c.lid or c.lid not in referenced):
                plan[i] = ("client_key", c, self.base_by_id.get(c.lid) if c.lid else None)
                self.consumed.add(c.idx)
                if c.lid:
                    referenced.add(c.lid)
                continue
            b = self.base_by_ck.get(m.ck)
            if b is not None and b.lid and b.lid not in self.cur_by_id and b.lid not in referenced:
                plan[i] = ("gone", None, b)
                referenced.add(b.lid)

        # Легаси-клиент: id-less строки ↔ строки с меткой minted этого же
        # пользователя и устройства (ретрай, наложение запросов,
        # неподхваченный рефетч старого приложения).
        if ctx.legacy:
            idless = [
                (i, m) for i, m in enumerate(self.mine)
                if m is not None and not m.lid and plan[i] is None
            ]
            if idless:
                cands = [
                    c for c in self.cur
                    if c.idx not in self.consumed and c.lid and c.lid not in referenced
                    and c.idx not in self.cur_dup and self._marker_candidate(c)
                ]
                for i, c in _pair_rows(idless, cands, positional_ok=not self.conservative):
                    plan[i] = ("mapped", c, self.base_by_id.get(c.lid))
                    self.consumed.add(c.idx)
                    referenced.add(c.lid)
                # Строку, выданную этой id-less строке в base-вьюхе (sent),
                # уже удалил кто-то другой: та же строка «удалена другим»
                # (пропустить, если не менялась), а не новая. Только по
                # описанию — без позиции.
                rest = [(i, m) for i, m in idless if plan[i] is None]
                gone = [
                    b for b in self.base or []
                    if b.lid and b.lid in ctx.base_minted and b.lid not in self.cur_by_id
                    and b.lid not in referenced
                ]
                for i, b in _pair_rows(rest, gone, positional_ok=False):
                    plan[i] = ("gone", None, b)
                    referenced.add(b.lid)

        # Неизвестные id mine ↔ строки current без labor_id (легаси-WO,
        # страница отрендерена до того, как id сохранили в базу).
        unknown = [(i, self.mine[i]) for i, p in enumerate(plan) if p is not None and p[0] == "unknown"]
        idless_cur = [c for c in self.cur if not c.lid and c.idx not in self.consumed]
        if unknown and idless_cur:
            for i, c in _pair_rows(unknown, idless_cur, positional_ok=True):
                plan[i] = ("adopted", c, None)
                self.consumed.add(c.idx)

        if ctx.legacy and self.conservative and self.spec.legacy_array_fallback:
            self._legacy_array_fallback(plan)

        for i, m in enumerate(self.mine):
            if m is not None and plan[i] is None:
                plan[i] = ("new", None, None)
        return plan

    def _legacy_array_fallback(self, plan: list) -> None:
        """Форма, открытая до деплоя, на легаси-WO (ревью D4-02 / D4-X1).

        Старая форма менеджера в приложении держит строки без id (прежний GET
        их не сохранял), старая вкладка офиса — временные id рендера. Если id
        тем временем сохранил другой GET, ни одна строка формы не узнаётся,
        и консервативный merge задвоил бы каждую работу (тоталы ×2, склад
        списан ещё раз). Прежний код писал массив формы целиком — здесь так
        же сопоставляем строки формы со строками WO, но только когда это
        явно та же форма:
          - ни одна строка формы не узнана по id / метке / client_key;
          - в WO нет строк с меткой minted (строк, созданных merge после
            деплоя: их старая форма знать не могла);
          - строки формы и строки WO (кроме пустых, которых легаси-клиент не
            шлёт) сходятся один к одному по описанию — равному или префиксу,
            в одном порядке, — и хотя бы одно непустое описание совпало
            полностью.
        Только по описанию, без позиции (ревью F1-RR-01): форма после упавшей
        загрузки, набранная заново («Oil change» + «Wipers»), приклеивала
        «Wipers» по порядку к чужой работе «Brakes» — та молча теряла
        описание, жалобу клиента и часы; вкладка офиса — новую строку к
        работе, добавленной после её рендера. Нет такого соответствия —
        консервативный merge: строки WO остаются как есть (офис такой Save
        отклоняет — page_outdated). Цена — форма до деплоя с полностью
        переименованной, добавленной или удалённой строкой задваивает работы
        (приложение менеджера); снимает это backfill_wo_labor_ids до деплоя.
        Пустые строки, которые легаси-клиент не шлёт, остаются на месте.
        """
        if any(p is not None and p[0] in ("merged", "client_key", "mapped", "gone") for p in plan):
            return
        if any(m is not None and m.ck for m in self.mine):
            return
        if any(isinstance(c.src.get("minted"), dict) for c in self.cur):
            return
        foreign = [
            (i, m) for i, m in enumerate(self.mine)
            if m is not None and (plan[i] is None or plan[i][0] == "unknown")
        ]
        skip_blank = self.spec.blank_rows_not_sent
        cands = [
            c for c in self.cur
            if c.idx not in self.consumed and c.idx not in self.cur_dup
            and not (skip_blank and is_blank_row(c.src))
        ]
        if not foreign or len(foreign) != len(cands):
            return
        pairs = _pair_rows(foreign, cands, positional_ok=False)
        if len(pairs) != len(cands):
            return
        if not any(
            norm_text(self.mine[i].src.get("description"))
            and _desc_compatible(self.mine[i].src, c.src, exact=True)
            for i, c in pairs
        ):
            return
        for i, c in pairs:
            plan[i] = ("adopted", c, None)
            self.consumed.add(c.idx)
            self.stats["rows_legacy_array"] += 1

    def _marker_candidate(self, c: _Row) -> bool:
        mk = c.src.get("minted")
        if not isinstance(mk, dict):
            return False
        uid = norm_text(self.ctx.user_id)
        sid = norm_text(self.ctx.sid)
        if not uid or not sid:
            return False
        if norm_text(mk.get("by")) != uid or norm_text(mk.get("sid")) != sid:
            return False
        if self.base is not None:
            # Строку, которую клиент получил с id (она в его base), он
            # удалил сам — привязывать к ней новую строку нельзя. Исключение
            # — id, выданные его же id-less строкам в base-вьюхе (sent).
            return c.lid not in self.base_by_id or c.lid in self.ctx.base_minted
        at = _as_utc(mk.get("at"))
        now = _as_utc(self.ctx.now)
        return at is not None and now is not None and now - at <= LEGACY_MAP_WINDOW

    def _mint(self) -> str:
        fn = self.ctx.mint_id or _default_mint_id
        value = norm_text(fn())
        if not value or value in self.used_ids:
            value = _default_mint_id()
        self.used_ids.add(value)
        return value

    # ── построчные проверки ──
    def _part_changed(self, ref: dict, other: dict, *, mine_side: bool) -> bool:
        spec = self.spec
        for f in spec.part_fields:
            if mine_side and f not in other:
                continue
            ov = norm_part_field(f, other.get(f))
            rv = norm_part_field(f, ref.get(f))
            if mine_side:
                if f == "description" and spec.keep_part_description_when_empty and ov == "":
                    continue
                if f == "qty" and self.quirk and ov == 1 and rv == 0:
                    continue
            if ov != rv:
                return True
        return False

    def _parts_differ(self, ref: _Row, other: _Row, *, mine_side: bool) -> bool:
        pm = match_parts(ref.parts, other.parts)
        if len(pm) != len(ref.parts) or len(pm) != len(other.parts):
            return True
        for ri, oi in pm.items():
            if self._part_changed(ref.parts[ri], other.parts[oi], mine_side=mine_side):
                return True
        if self.spec.misc_editable:
            back = {oi: ri for ri, oi in pm.items()}
            e_ref = _misc_entries(ref.items, lambda pi: ("p", pi), len(ref.parts))
            e_oth = _misc_entries(
                other.items,
                lambda pi: ("p", back[pi]) if pi in back else ("o", pi),
                len(other.parts),
            )
            if _canon(e_ref) != _canon(e_oth):
                return True
        return False

    def _mine_changed(self, b: _Row, m: _Row) -> bool:
        """Клиент менял строку относительно своей base (решение «воскресить»)."""
        spec = self.spec
        for f in spec.text_fields:
            if f in m.src and norm_text(m.src.get(f)) != norm_text(b.src.get(f)):
                return True
        for f in spec.money_touch_fields:
            if f == "hours" and self._untyped_tracked_hours(b.src, m.src):
                continue  # не введённые руками часы tracked-строки — не правка
            if f in m.src and norm_row_field(f, m.src.get(f)) != norm_row_field(f, b.src.get(f)):
                return True
        if spec.assigned_editable and "assigned_mechanics" in m.src:
            if norm_assigned(m.src.get("assigned_mechanics")) != norm_assigned(b.src.get("assigned_mechanics")):
                return True
        return self._parts_differ(b, m, mine_side=True)

    def _changed_by_others(self, b: _Row, c: _Row) -> bool:
        """Кто-то другой менял строку после base клиента."""
        spec = self.spec
        for f in spec.text_fields:
            if norm_text(c.src.get(f)) != norm_text(b.src.get(f)):
                return True
        if spec.money_mode:
            for f in ("hours", "rate_code", "labor_total", "hours_source"):
                if norm_row_field(f, c.src.get(f)) != norm_row_field(f, b.src.get(f)):
                    return True
        # Для всех каналов: часы от таймеров (кто-то работал на строке).
        if norm_text(c.src.get("hours_source")) == "tracked" and norm_hours(c.src.get("hours")) != norm_hours(b.src.get("hours")):
            return True
        return self._parts_differ(b, c, mine_side=False)

    # ── денежная группа ──
    def _merge_money(self, b: _Row | None, c: _Row, m: _Row, out: dict) -> tuple[bool, bool, bool]:
        """(группа из mine, сработало tracked/preset-правило, деньги ≠ mine)."""
        mode = self.spec.money_mode
        ms, cs = m.src, c.src
        if not any(f in ms for f in MONEY_FIELDS):
            return False, False, False
        bs = b.src if b is not None else None
        # Поле группы, которое клиент не прислал, — «не трогал» (значение base/current).
        ref_src = bs if bs is not None else cs
        mh, mr, ml = (ms[f] if f in ms else ref_src.get(f) for f in MONEY_FIELDS)
        ch, cr, cl = cs.get("hours"), cs.get("rate_code"), cs.get("labor_total")
        csrc = norm_text(cs.get("hours_source"))
        hours_edited = mode == "office" and as_bool(ms.get("hours_edited"))
        # Часы клиента отличаются от тех, что он видел (base; без base — от текущих).
        hours_changed = norm_hours(mh) != norm_hours(ref_src.get("hours"))
        # Часы tracked-строки, которых вкладка офиса руками не вводила, — не её
        # правка (см. _untyped_tracked_hours): «часы» вкладки — те, что в current.
        untyped = self._untyped_tracked_hours(bs, ms)
        if untyped:
            hours_changed = False
        total_changed = False

        if mode == "office":
            if bs is None:
                take_m = True
            else:
                # Трогал ли менеджер группу — только по часам и ставке:
                # labor_total офис выводит сам (пересчёт по текущей ставке).
                touched = hours_changed or norm_text(mr) != norm_text(bs.get("rate_code"))
                changed_c = (
                    norm_hours(ch), norm_text(cr), norm_money(cl), csrc,
                ) != (
                    norm_hours(bs.get("hours")), norm_text(bs.get("rate_code")),
                    norm_money(bs.get("labor_total")), norm_text(bs.get("hours_source")),
                )
                # Не трогал и никто не менял — группа mine (как раньше: офис
                # пересчитывает labor по текущей ставке при каждом Save).
                take_m = touched or not changed_c
            # Часы таймеров перебивает только явная правка: hours_edited мог
            # остаться от прошлого Save — считаем его, только если часы
            # реально отличаются.
            tracked_edit = hours_edited and hours_changed
        else:
            if bs is None:
                take_m = True
            else:
                take_m = any(
                    norm_row_field(f, ms.get(f)) != norm_row_field(f, bs.get(f)) for f in MONEY_FIELDS
                )
                total_changed = norm_money(ml) != norm_money(bs.get("labor_total"))
            # Ручной labor_total менеджера — тоже ручное управление деньгами:
            # tracked-правило его не перетирает. Без base правку не отличить
            # от устаревшей формы — часы таймеров остаются.
            tracked_edit = bs is not None and (hours_changed or total_changed)
        # Часы пресета — обычный 3-way (ревью D1-04 / D2-01): пресетную строку
        # таймеры не трогают, поменять её часы может только человек — правкой
        # или «Apply preset» в офисе (эта кнопка пишет часы без флага
        # hours_edited). Отличаются от виденных клиентом — это его правка, как
        # и в прежнем коде; не отличаются, а в current другие — чужая правка.
        preset_edit = hours_changed or total_changed

        hours, rate, total = (mh, mr, ml) if take_m else (ch, cr, cl)
        source = csrc
        adjusted = False
        theirs = False
        if (csrc == "tracked" and not tracked_edit) or (csrc == "preset" and not preset_edit):
            # Часы от таймеров (без явной правки, в т.ч. без base) и чужие
            # часы пресетной строки, которых клиент не трогал, остаются.
            if norm_text(ch) and norm_hours(hours) != norm_hours(ch):
                hours = ch
                total = self._labor_at(ch, rate, cr, cl)
                adjusted = True
        elif untyped and norm_hours(hours) != norm_hours(ch):
            # Группу взяли у вкладки (она меняла ставку), а часы строки уже
            # не tracked — их сменил другой (ручные часы): часы — его, ставка
            # — вкладки. Это чужие деньги, а не tracked-правило: merged-путь.
            hours = ch
            total = self._labor_at(ch, rate, cr, cl)
            theirs = True
        elif take_m:
            if bs is not None:
                changed = hours_changed or (mode == "mobile_manager" and total_changed)
                if mode == "office" and csrc == "preset" and not hours_edited:
                    # «Apply preset» поверх пресетной строки: часы новые, но
                    # они по-прежнему от пресета — метка остаётся (как раньше).
                    changed = False
                source = "" if changed else csrc
            elif mode == "office" and hours_edited and hours_changed:
                # Без base метку снимает только явная правка часов.
                source = ""

        # Значения — как есть (строку/число приводит адаптер так же, как
        # раньше), иначе офисный Save без конфликтов писал бы иначе.
        out["hours"] = hours
        out["rate_code"] = rate
        out["labor_total"] = total
        out["hours_source"] = source
        other = (not adjusted) and (
            norm_hours(hours), norm_text(rate), norm_money(total),
        ) != (norm_hours(mh), norm_text(mr), norm_money(ml))
        return take_m and not adjusted and not theirs, adjusted, other

    def _labor_at(self, hours, rate, cur_rate, cur_total):
        """База строки на часах hours (из current): при той же ставке — база
        current, при другой — hours × ставка."""
        if norm_text(rate) == norm_text(cur_rate):
            return cur_total
        return round2((f64(hours) or 0.0) * (f64(self.ctx.rates.get(norm_text(rate))) or 0.0))

    def _untyped_tracked_hours(self, b_src: dict | None, m_src: dict) -> bool:
        """Офис: строка в base вкладки — tracked, а часы вкладка руками не
        вводила (нет hours_edited). Такие часы формы — не правка вкладки,
        даже если они отличаются от base: после правки тайм-лога в режиме
        Edit база вкладки (производная вьюха) знает свежие часы, а форма
        показывает прежние («Save сам подставит свежие часы»); так же и
        «Apply preset» поверх tracked-строки (как и раньше: tracked-часы
        перебивает только явная правка). Иначе Save откатывал ручные часы,
        ставку или пресет другого пользователя на этой работе и воскрешал
        удалённую другим работу («you changed it» — неправда; ревью
        RR-F2-01 / RW-01). Новый JS снимает флаг после Save, так что флаг
        здесь — ввод с прошлого Save."""
        return (
            self.spec.money_mode == "office"
            and b_src is not None
            and norm_text(b_src.get("hours_source")) == "tracked"
            and not as_bool(m_src.get("hours_edited"))
        )

    # ── запчасти ──
    def _pick_part(self, bp: dict | None, cp: dict, mp: dict) -> tuple[dict, bool, bool]:
        """Поля запчасти 3-way. (запчасть, значение ≠ mine, деньги ≠ mine)."""
        spec = self.spec
        out = copy.deepcopy(cp)
        out.pop("_catalog_misc_items", None)
        other = money_other = False
        for f in spec.part_fields:
            if f not in mp:
                continue
            mv = norm_part_field(f, mp.get(f))
            if f == "description" and spec.keep_part_description_when_empty and mv == "":
                continue
            quirk_same = False
            bv = None
            if bp is None:
                take = True
            else:
                bv = norm_part_field(f, bp.get(f))
                quirk_same = f == "qty" and self.quirk and mv == 1 and bv == 0
                take = (mv != bv) and not quirk_same
            if take:
                out[f] = copy.deepcopy(mp.get(f))
                continue
            got = norm_part_field(f, out.get(f))
            # Квирк qty: «1» клиента против 0 в base — не расхождение, пока
            # current тоже 0; изменил кто-то другой — это чужое значение.
            if got != mv and not (quirk_same and got == bv):
                other = True
                if f in _PART_TOTALS_FIELDS:
                    money_other = True
        # Алиас: одна сторона без part_id, номер тот же — берём ту, что
        # привязана к каталогу.
        if norm_text(mp.get("part_id")) and not norm_text(out.get("part_id")):
            if norm_text(mp.get("part_number")).lower() == norm_text(out.get("part_number")).lower():
                out["part_id"] = copy.deepcopy(mp.get("part_id"))
        return out, other, money_other

    def _new_part(self, mp: dict) -> dict | None:
        fn = self.ctx.new_part
        if fn is None:
            part = copy.deepcopy(mp)
        else:
            part = fn(copy.deepcopy(mp))
        return part if isinstance(part, dict) else None

    def _merge_parts(self, b: _Row | None, c: _Row, m: _Row, job: str) -> _PartsResult:
        B = b.parts if b is not None else None
        C, M = c.parts, m.parts
        if B is None:
            mc = match_parts(M, C)
            bm, bc = {}, {}
        else:
            bm = match_parts(B, M)
            bc = match_parts(B, C)
            # Запчасть, которая была в base, — через base: mine → base →
            # current (ревью D1-05: прямое сопоставление по позиции/вхождению
            # раньше родословной склеивало нетронутую строку клиента с чужой,
            # а его правку принимало за «удалённую другим» — строка в счёте
            # задваивалась). Нет пары в current — её удалил кто-то другой.
            mc = {mi: bc[bi] for bi, mi in bm.items() if bi in bc}
            # Без строки в base (новая у клиента / добавлена другими) — прямо,
            # только по ключу и алиасу: гонка двух запросов с одной новой
            # запчастью не даёт дубль, а разные новые строки не склеиваются.
            b_of_m, b_of_c = set(bm.values()), set(bc.values())
            m_free = [mi for mi in range(len(M)) if mi not in b_of_m]
            c_free = [ci for ci in range(len(C)) if ci not in b_of_c]
            if m_free and c_free:
                direct = match_parts([M[i] for i in m_free], [C[j] for j in c_free], anon_fallback=False)
                for x, y in direct.items():
                    mc[m_free[x]] = c_free[y]
        m_to_b = {mi: bi for bi, mi in bm.items()}
        c_to_m = {ci: mi for mi, ci in mc.items()}
        c_to_b = {ci: bi for bi, ci in bc.items()}

        merged: list = []
        refs: list = [None] * len(M)
        new_slots: set = set()
        resurrected: set = set()
        skipped: set = set()
        other = money_other = structure = False
        for mi, mp in enumerate(M):
            bp = B[m_to_b[mi]] if mi in m_to_b else None
            slot = ("m", mi)
            if mi in mc:
                part, took, took_money = self._pick_part(bp, C[mc[mi]], mp)
                other = other or took
                money_other = money_other or took_money
            elif bp is not None:
                # В base была, в current нет — её удалил кто-то другой.
                if self._part_changed(bp, mp, mine_side=True):
                    part, _t, _tm = self._pick_part(bp, bp, mp)
                    resurrected.add(slot)
                    structure = True
                    self.stats["parts_resurrected"] += 1
                    self._note(f"Restored part {_part_label(part)} on {job}: you changed it after another user removed it.")
                else:
                    structure = True
                    self.stats["parts_skipped"] += 1
                    self._note(f"Part {_part_label(bp)} was removed by another user.")
                    refs[mi] = copy.deepcopy(bp)
                    skipped.add(slot)
                    continue
            else:
                part = self._new_part(mp)
                if part is None:
                    self.stats["parts_rejected"] += 1
                    continue
                new_slots.add(slot)
                self.stats["parts_new"] += 1
            ref = copy.deepcopy(part)
            ref.pop("_catalog_misc_items", None)
            refs[mi] = ref
            merged.append((part, slot))

        for ci, cp in enumerate(C):
            if ci in c_to_m:
                continue
            note = None
            if B is None:
                note = f"Kept part {_part_label(cp)} on {job}: it was not in your copy of the work order."
            elif ci in c_to_b:
                if self._part_changed(B[c_to_b[ci]], cp, mine_side=False):
                    note = f"Kept part {_part_label(cp)} on {job}: it was changed by another user."
            else:
                note = f"Kept part {_part_label(cp)} on {job} added by another user."
            if note is None:
                self.stats["parts_deleted"] += 1
                continue
            merged.append((copy.deepcopy(cp), ("c", ci)))
            structure = True
            self.stats["parts_kept"] += 1
            self._note(note)

        return _PartsResult(
            merged=merged, refs=refs, new_slots=new_slots, resurrected=resurrected,
            mc=mc, bm=bm, bc=bc, c_to_m=c_to_m,
            other=other, money_other=money_other, structure=structure, skipped=skipped,
        )

    # ── misc-позиции ──
    def _merge_misc(self, b: _Row | None, c: _Row, m: _Row, pr: _PartsResult) -> tuple[list | None, bool, list | None]:
        """(итоговый список позиций | None — списка нет ни у кого, misc ≠ mine,
        список для belief канала, который misc не держит — см. _belief_misc)."""
        spec = self.spec
        n_b = len(b.parts) if b is not None else 0

        def c_slot(pi):
            return ("m", pr.c_to_m[pi]) if pi in pr.c_to_m else ("c", pi)

        def b_slot(pi):
            if pi in pr.bm:
                return ("m", pr.bm[pi])
            if pi in pr.bc:
                return c_slot(pr.bc[pi])
            return ("b", pi)

        e_m = _misc_entries(m.items, lambda pi: ("m", pi), len(m.parts)) if spec.misc_editable else []
        e_c = _misc_entries(c.items, c_slot, len(c.parts))
        e_b = _misc_entries(b.items, b_slot, n_b) if b is not None else []
        has_list = (spec.misc_editable and m.items is not None) or c.items is not None
        kept_c = {slot for _p, slot in pr.merged if slot[0] == "c"}

        if not spec.misc_editable:
            # Механик misc не редактирует: список current (+ позиции новых
            # запчастей ниже), позиции удалённых запчастей выпадут.
            result = list(e_c)
        elif b is None:
            # Без base: ручные позиции — как у клиента (как раньше), позиции
            # сохранённых чужих запчастей идут вместе с ними.
            result = list(e_m) + [e for e in e_c if e.slot is not None and e.slot in kept_c]
        else:
            cm, cb, cc = _canon(e_m), _canon(e_b), _canon(e_c)
            if cm == cb:
                result = list(e_c)
            elif cc == cb:
                result = list(e_m)
            else:
                result = _merge_entries(e_b, e_c, e_m)

        # auto-позиции следуют за своей запчастью: сохранённая чужая —
        # с позициями current, восстановленная — с позициями mine/base.
        present = {e.ident for e in result}
        for _part, slot in pr.merged:
            if slot[0] == "c":
                source = e_c
            elif slot in pr.resurrected:
                source = e_m if spec.misc_editable else e_b
            else:
                continue
            for e in source:
                if e.slot == slot and e.ident not in present:
                    result.append(e)
                    present.add(e.ident)
                    # Восстановленная запчасть принесла свой сбор (EPA и т.п.),
                    # даже если у строки в current списка уже нет (ревью
                    # D4-01: иначе сбор терялся — недобор в счёте).
                    has_list = True

        slot_index = {slot: idx for idx, (_p, slot) in enumerate(pr.merged)}
        new_items = []
        for idx, (part, slot) in enumerate(pr.merged):
            catalog = part.pop("_catalog_misc_items", None)
            if slot not in pr.new_slots or not catalog:
                continue
            qty = norm_qty(part.get("qty"))
            if qty <= 0:
                continue
            for it in catalog:
                if isinstance(it, dict):
                    new_items.append({**it, "quantity": qty, "partIndex": idx})

        if not has_list and not new_items:
            return None, False, self._belief_misc(None, pr, e_b)

        final: list[dict] = []
        survived: list[_Entry] = []
        for e in result:
            item = dict(e.raw)
            if e.slot is not None:
                if e.slot not in slot_index:
                    continue  # запчасть-владелец не попала в итог
                idx = slot_index[e.slot]
                if _as_index(item.get("partIndex")) != idx:
                    item["partIndex"] = idx
            final.append(item)
            survived.append(e)
        final.extend(new_items)

        other = False
        if spec.misc_editable:
            other = bool(new_items) or _canon(survived) != _canon(e_m)
        return final, other, self._belief_misc(final, pr, e_b)

    def _belief_misc(self, final: list | None, pr: _PartsResult, e_b: list) -> list | None:
        """Misc-позиции строки в belief канала, где клиент misc не держит
        (механик): итоговый список, partIndex — по запчастям копии клиента
        (belief), плюс позиции запчастей, удалённых другими, но ещё
        лежащих у клиента (из base). Без этого sent-вьюха механика хранила
        misc_items None, и строка/запчасть, восстановленная из неё,
        возвращалась без своего сбора (ревью D4-01). Позиции чужих
        сохранённых запчастей (их у клиента нет) в belief не попадают.
        Для каналов, где misc редактируется, belief держит список клиента —
        здесь None."""
        if self.spec.misc_editable:
            return None
        slots = [slot for _p, slot in pr.merged]
        out: list[dict] = []
        for item in final or []:
            item = dict(item)
            if item.get("manual") is not True:
                pi = _as_index(item.get("partIndex"))
                if pi is not None and 0 <= pi < len(slots):
                    slot = slots[pi]
                    if slot[0] != "m":
                        continue
                    item["partIndex"] = slot[1]
            out.append(item)
        # Пропущенной запчасти в итоге нет — её позиции берутся из base.
        out.extend({**dict(e.raw), "partIndex": e.slot[1]} for e in e_b if e.slot in pr.skipped)
        if final is None and not out:
            return None
        return out

    # ── одна строка: 3-way ──
    def _merge_row(self, b: _Row | None, c: _Row, m: _Row, *, final_id: str, resurrect: bool = False):
        spec = self.spec
        out = copy.deepcopy(c.src)
        out.pop("misc_items", None)
        out.pop("hours_edited", None)
        out["labor_id"] = final_id
        if m.ck and not norm_text(out.get("client_key")):
            out["client_key"] = m.ck
        if resurrect:
            # Нередактируемые поля — из компактной base: id назначений обратно в ObjectId.
            out["assigned_mechanics"] = _restore_assigned_ids(out.get("assigned_mechanics"))
            if "minted" in out:
                out["minted"] = _restore_minted(out.get("minted"))

        other = False
        for f in spec.text_fields:
            if f not in m.src:
                continue
            mv = norm_text(m.src.get(f))
            if b is None or mv != norm_text(b.src.get(f)):
                out[f] = mv
            if norm_text(out.get(f)) != mv:
                other = True

        money_from_mine, adjusted, money_other = False, False, False
        if spec.money_mode:
            money_from_mine, adjusted, money_other = self._merge_money(b, c, m, out)
            if adjusted:
                self.tracked.append(final_id)

        if spec.assigned_editable and "assigned_mechanics" in m.src:
            mv = norm_assigned(m.src.get("assigned_mechanics"))
            if b is None or mv != norm_assigned(b.src.get("assigned_mechanics")):
                out["assigned_mechanics"] = copy.deepcopy(m.src.get("assigned_mechanics") or [])
            if norm_assigned(out.get("assigned_mechanics")) != mv:
                other = True

        job = _job_label(out)
        pr = self._merge_parts(b, c, m, job)
        items, misc_other, belief_misc = self._merge_misc(b, c, m, pr)
        parts_out = [p for p, _slot in pr.merged]
        if items is not None:
            if parts_out:
                raw = None
                sources = ((m.items, m.raw_misc), (c.items, c.raw_misc)) if spec.misc_editable else ((c.items, c.raw_misc),)
                for orig, orig_raw in sources:
                    if orig_raw is not None and orig is not None and orig == items:
                        raw = orig_raw
                        break
                parts_out[0]["misc_charge_description"] = raw if raw is not None else json.dumps(items)
            elif items:
                # Хранить позиции негде (как и раньше без запчастей).
                log.warning("labor %s: %d misc item(s) dropped, the job has no parts", final_id, len(items))
                self.stats["misc_dropped"] += len(items)
        out["parts"] = parts_out

        if other or pr.other or money_other or misc_other or adjusted:
            self._foreign()
        if pr.structure or misc_other or money_other or pr.money_other:
            self._foreign(structure=True)
        return out, money_from_mine, pr.refs, belief_misc

    def _new_block(self, m: _Row, final_id: str, *, mint: bool) -> dict:
        raw = copy.deepcopy(m.src)
        raw.pop("minted", None)  # метку ставит только сервер
        raw["labor_id"] = final_id
        fn = self.ctx.new_block
        row = fn(copy.deepcopy(raw)) if fn is not None else None
        if not isinstance(row, dict):
            row = raw
            row.pop("misc_items", None)
            row.pop("hours_edited", None)
        row.pop("minted", None)
        row["labor_id"] = final_id
        if m.ck:
            row["client_key"] = m.ck
        if mint:
            row["minted"] = {
                "by": norm_text(self.ctx.user_id),
                "sid": norm_text(self.ctx.sid),
                "at": self.ctx.now,
                "ck": m.ck,
            }
        return row

    # ── belief: следующая base клиента ──
    def _belief(self, m: _Row, ref_row: dict, refs: list | None, final_id: str, misc_items=None) -> dict:
        """misc_items — список для канала, где misc не редактируется
        (_belief_misc / _remap_misc): partIndex по запчастям belief."""
        spec = self.spec
        row = copy.deepcopy(ref_row)
        row.pop("misc_items", None)
        row.pop("hours_edited", None)
        row["labor_id"] = final_id
        if m.ck:
            row["client_key"] = m.ck
        for f in spec.text_fields:
            if f in m.src:
                row[f] = norm_text(m.src.get(f))
        if spec.money_mode:
            for f in MONEY_FIELDS:
                if f in m.src:
                    row[f] = copy.deepcopy(m.src.get(f))
        if spec.assigned_editable and "assigned_mechanics" in m.src:
            row["assigned_mechanics"] = copy.deepcopy(m.src.get("assigned_mechanics") or [])
        parts = []
        for mi, mp in enumerate(m.parts):
            ref = refs[mi] if refs is not None and mi < len(refs) and refs[mi] is not None else mp
            p = copy.deepcopy(ref)
            p.pop("_catalog_misc_items", None)
            for f in spec.part_fields:
                if f not in mp:
                    continue
                mv = mp.get(f)
                if f == "description" and spec.keep_part_description_when_empty and not norm_text(mv):
                    continue
                if f == "qty" and self.quirk and norm_qty(mv) == 1 and norm_qty(p.get("qty")) == 0:
                    continue
                p[f] = copy.deepcopy(mv)
            parts.append(p)
        row["parts"] = parts
        if spec.misc_editable:
            row["misc_items"] = copy.deepcopy(m.items) if m.items is not None else None
        else:
            # misc клиент не держит: список строки (из итога / base) уже
            # пересчитан на запчасти belief — нужен, чтобы восстановление из
            # этой base вернуло строку/запчасть вместе с её сборами.
            row["misc_items"] = copy.deepcopy(misc_items) if misc_items is not None else None
            for p in parts[:1]:
                if parse_misc_list(p.get("misc_charge_description")) is not None:
                    p["misc_charge_description"] = ""
        return row

    def _belief_parts_from_base(self, b: _Row, m: _Row) -> list:
        bm = match_parts(b.parts, m.parts)
        m_to_b = {mi: bi for bi, mi in bm.items()}
        return [copy.deepcopy(b.parts[m_to_b[mi]]) if mi in m_to_b else None for mi in range(len(m.parts))]

    def _remap_misc(self, items, n_source: int, part_map: dict) -> list | None:
        """Позиции с partIndex, переведённым по part_map {индекс запчасти
        источника (их n_source): индекс запчасти belief}; auto-позиции
        запчастей, которых у клиента нет, выпадают; ручные и «висячие» (индекс
        вне источника) — как есть. Только для каналов без правки misc (у
        остальных belief держит список клиента)."""
        if self.spec.misc_editable or items is None:
            return None
        out = []
        for it in items:
            it = dict(it)
            if it.get("manual") is not True:
                pi = _as_index(it.get("partIndex"))
                if pi is not None and 0 <= pi < n_source:
                    if pi not in part_map:
                        continue
                    it["partIndex"] = part_map[pi]
            out.append(it)
        return out

    # ── строки current, которых нет в mine ──
    def _keep(self, c: _Row, note: str | None) -> bool:
        """Учесть сохранённую строку current: с заметкой — «чужая» (клиенту
        перечитать), без — тихо. Возвращает silent."""
        if note:
            self._note(note)
            self._foreign(structure=True)
            self.stats["kept_ids"].append(c.lid)
            return False
        self.stats["kept_silent_ids"].append(c.lid)
        return True

    def _decide_unconsumed(self, c: _Row) -> tuple[str, bool, Any]:
        """(решение, silent, belief-строка | None); решение: delete | anchored | tail.

        anchored — строку клиент знает (пустая строка, которую легаси-клиент
        не шлёт): остаётся на своём месте среди строк mine; tail — строки,
        которых клиент не знает, — в конец (индексы клиента не съезжают).
        """
        spec, ctx = self.spec, self.ctx
        legacy_blank = spec.blank_rows_not_sent and ctx.legacy
        label = _job_label(c.src)
        if c.idx in self.cur_dup:
            # Дубль labor_id в самом WO (легаси): не знаем, чья — не трогаем.
            return "tail", self._keep(c, None), None
        if self.conservative:
            if is_blank_row(c.src):
                # Пустую строку легаси-клиент загрузил, но не шлёт — она на своём месте.
                if legacy_blank:
                    return "anchored", self._keep(c, None), copy.deepcopy(c.src)
                return "tail", self._keep(c, None), None
            return "tail", self._keep(c, f"Kept job {label}: it was not in your copy of the work order."), None
        b = self.base_by_id.get(c.lid) if c.lid else None
        if b is not None:
            if legacy_blank and is_blank_row(b.src):
                # Легаси-клиент пустые строки не шлёт: отсутствие — не
                # удаление. Клиент строку знает — она остаётся на месте, а в
                # belief — такой, какой её видел клиент (пустой). Тихо, даже
                # если её кто-то дописал: иначе заметка повторялась бы на
                # каждом автосейве, а старый клиент заметок не показывает.
                return "anchored", self._keep(c, None), copy.deepcopy(b.src)
            if self._changed_by_others(b, c):
                return "tail", self._keep(c, f"Kept job {label}: it was changed by another user."), None
            if c.lid in ctx.running_labor_ids:
                return "tail", self._keep(c, f"Kept job {label}: someone is working on it."), None
            self.stats["deleted_ids"].append(c.lid)
            return "delete", False, None
        if legacy_blank and is_blank_row(c.src):
            return "tail", self._keep(c, None), copy.deepcopy(c.src)
        return "tail", self._keep(c, f"Kept job {label} added by another user."), None

    def run(self) -> MergeResult:
        plan = self._plan()
        res = MergeResult()
        main: list = []           # [(row, origin)] строк mine
        cur_pos: dict[int, int] = {}
        beliefs: list = []
        for i, m in enumerate(self.mine):
            if m is None:
                res.labor_ids.append("")
                continue
            kind, c, b = plan[i]
            origin = {"kind": kind, "mine_index": i, "current_index": None, "money_from_mine": True}
            out = None
            if kind in ("merged", "client_key", "mapped", "adopted"):
                final_id = c.lid or m.lid or self._mint()
                out, from_mine, refs, bmisc = self._merge_row(b, c, m, final_id=final_id)
                origin["current_index"] = c.idx
                origin["money_from_mine"] = from_mine
                self.stats["rows_" + kind] += 1
                belief = self._belief(m, out, refs, final_id, bmisc)
            elif kind == "gone":
                final_id = b.lid
                if self._mine_changed(b, m):
                    out, from_mine, refs, bmisc = self._merge_row(b, b, m, final_id=final_id, resurrect=True)
                    origin["kind"] = "resurrected"
                    origin["money_from_mine"] = from_mine
                    self._note(f"Restored job {_job_label(out)}: you changed it after another user removed it.")
                    self._foreign(structure=True)
                    self.stats["resurrected_ids"].append(final_id)
                    belief = self._belief(m, out, refs, final_id, bmisc)
                else:
                    self._note(f"Job {_job_label(b.src)} was removed by another user.")
                    self._foreign(structure=True)
                    self.stats["skipped_ids"].append(final_id)
                    bm = match_parts(b.parts, m.parts)
                    belief = self._belief(
                        m, b.src, self._belief_parts_from_base(b, m), final_id,
                        self._remap_misc(b.items, len(b.parts), bm),
                    )
            elif kind == "unknown":
                # Id нет ни в current, ни в base (консервативный режим /
                # легаси): как раньше — строка создаётся заново с этим id.
                final_id = m.lid
                out = self._new_block(m, final_id, mint=False)
                self.stats["rows_unknown"] += 1
                belief = self._new_row_belief(m, out, final_id)
            else:
                final_id = self._mint()
                out = self._new_block(m, final_id, mint=True)
                origin["kind"] = "new"
                self.stats["rows_new"] += 1
                belief = self._new_row_belief(m, out, final_id)

            res.labor_ids.append(final_id)
            if m.ck:
                res.client_keys[m.ck] = final_id
            if not m.lid:
                res.minted.append(final_id)
            beliefs.append(belief)
            if out is not None:
                if c is not None:
                    cur_pos[c.idx] = len(main)
                main.append((out, origin))

        head: list = []
        anchored: dict[int, list] = {}
        tail: list = []
        silent_beliefs: list = []
        consumed_sorted = sorted(cur_pos)
        for c in self.cur:
            if c.idx in self.consumed:
                continue
            decision, silent, belief_row = self._decide_unconsumed(c)
            if decision == "delete":
                continue
            row = copy.deepcopy(c.src)
            row.pop("misc_items", None)
            origin = {
                "kind": "kept_silent" if silent else "kept",
                "mine_index": None, "current_index": c.idx, "money_from_mine": False,
            }
            if belief_row is not None:
                silent_beliefs.append(belief_row)
            if decision == "anchored":
                prev = [idx for idx in consumed_sorted if idx < c.idx]
                if prev:
                    anchored.setdefault(cur_pos[prev[-1]], []).append((row, origin))
                else:
                    head.append((row, origin))
            else:
                tail.append((row, origin))

        ordered = list(head)
        for pos, item in enumerate(main):
            ordered.append(item)
            ordered.extend(anchored.get(pos, []))
        ordered.extend(tail)
        res.rows = [r for r, _o in ordered]
        res.origins = [o for _r, o in ordered]
        res.belief = beliefs + silent_beliefs

        notes = list(dict.fromkeys(list(self.ctx.extra_notes) + self.notes))
        if len(notes) > MAX_NOTES:
            more = len(notes) - (MAX_NOTES - 1)
            notes = notes[: MAX_NOTES - 1] + [f"And {more} more changes from other users."]
        res.notes = notes
        res.foreign_any = self.foreign_any or self.foreign_structure
        res.foreign_structure = self.foreign_structure
        res.tracked_adjusted = list(dict.fromkeys(self.tracked))
        res.stats = self.stats
        return res

    @staticmethod
    def _aligned_refs(out: dict, m: _Row) -> list | None:
        """Опоры belief для новой строки: её запчасти, если выровнены с mine."""
        parts = [p for p in _as_parts(out.get("parts")) if isinstance(p, dict)]
        if len(parts) != len(m.parts):
            return None
        return [copy.deepcopy(p) for p in parts]

    def _new_row_belief(self, m: _Row, out: dict, final_id: str) -> dict:
        """Belief новой строки (new_block): misc — сборы каталога, которые
        new_block повесил на её запчасти (partIndex — по запчастям belief,
        т.е. по запчастям mine)."""
        refs = self._aligned_refs(out, m)
        items, out_parts = lift_misc(out)
        if refs is not None:
            part_map = {i: i for i in range(len(out_parts))}
        else:
            part_map = match_parts(out_parts, m.parts)
        return self._belief(m, out, refs, final_id, self._remap_misc(items, len(out_parts), part_map))


def merge_labors(base, current, mine, spec: FieldSpec, *, ctx) -> MergeResult:
    """Трёхсторонний merge строк работ WO — см. докстринг модуля."""
    if not isinstance(ctx, MergeContext):
        ctx = MergeContext(**dict(ctx or {}))
    return _Merger(base, current, mine, spec, ctx).run()


__all__ = [
    "ALL_PART_FIELDS",
    "FieldSpec",
    "LEGACY_MAP_WINDOW",
    "MAX_NOTES",
    "MECHANIC_SPEC",
    "MOBILE_MANAGER_SPEC",
    "MONEY_FIELDS",
    "MergeContext",
    "MergeResult",
    "OFFICE_SPEC",
    "TEXT_FIELDS",
    "is_blank_row",
    "lift_misc",
    "match_parts",
    "merge_labors",
    "norm_assigned",
    "norm_hours",
    "norm_money",
    "norm_part_field",
    "norm_qty",
    "norm_row_field",
    "norm_text",
    "parse_misc_list",
    "part_key",
    "part_keys",
    "split_misc_items",
]
