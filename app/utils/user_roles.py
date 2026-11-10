"""Роли пользователей для отображения: каталог ролей тенанта и группировка
списка пользователей по ролям (Settings → Users & Roles).

Только подписи и порядок показа. Права считает app/utils/permissions.py
(compute_user_permissions): ключ роли пользователя ищется в tenant.roles,
и если документа роли нет — прав от роли у человека нет. Здесь такие
пользователи попадают в группу kind="missing" («Role not found»).
"""
from __future__ import annotations

from app.constants.permissions import SYSTEM_ROLE_LABELS, SYSTEM_ROLE_ORDER

DEFAULT_ROLE_KEY = "viewer"

ROLE_KIND_SYSTEM = "system"
ROLE_KIND_CUSTOM = "custom"
ROLE_KIND_MISSING = "missing"
ROLE_KIND_INACTIVE = "inactive"

# Деактивированные пользователи не делятся по ролям — одна общая группа
# в конце списка (ключ группы не пересекается с данными: группы ролей и
# эта группа собираются раздельно).
INACTIVE_GROUP_KEY = "inactive"
INACTIVE_GROUP_LABEL = "Inactive"


def normalize_role_key(u) -> str:
    """Ключ роли пользователя так же, как его читает проверка прав:
    strip + lower; пустая роль — viewer (дефолт всего приложения)."""
    raw = (u or {}).get("role")
    key = str(raw).strip().lower() if raw is not None else ""
    return key or DEFAULT_ROLE_KEY


def role_label_from_key(key) -> str:
    """Подпись роли без документа роли: системная — из каталога, иначе
    ключ «по-человечески» (parts_editor → Parts editor)."""
    key = str(key or "")
    if key in SYSTEM_ROLE_LABELS:
        return SYSTEM_ROLE_LABELS[key]
    text = " ".join(key.replace("_", " ").split())
    return text.capitalize() if text else "—"


def _doc_label(doc) -> str:
    name = (doc or {}).get("name")
    return " ".join(name.split()) if isinstance(name, str) else ""


def load_role_catalog(tdb) -> list[dict]:
    """Роли для UI в порядке показа: системные (SYSTEM_ROLE_ORDER), затем
    кастомные A→Z по названию. Элемент: {key, label, kind: system|custom}.

    Системные роли есть всегда — даже без документа в tenant DB (тестовый
    тенант, tdb=None); подпись берётся из документа роли, потому что
    системную роль можно переименовать в Roles & Permissions.
    """
    docs: dict[str, dict] = {}
    if tdb is not None:
        for doc in tdb.roles.find({}, {"key": 1, "name": 1}):
            key = doc.get("key")
            # Неканоничный ключ (не строка / пробелы / регистр) проверка прав
            # не найдёт никогда (ищет strip+lower) — такую роль не показываем
            # и не предлагаем назначать.
            if not isinstance(key, str) or not key or key != key.strip().lower():
                continue
            docs.setdefault(key, doc)

    catalog = [
        {
            "key": key,
            "label": _doc_label(docs.get(key)) or SYSTEM_ROLE_LABELS[key],
            "kind": ROLE_KIND_SYSTEM,
        }
        for key in SYSTEM_ROLE_ORDER
    ]
    custom = [
        {"key": key, "label": _doc_label(doc) or role_label_from_key(key), "kind": ROLE_KIND_CUSTOM}
        for key, doc in docs.items()
        if key not in SYSTEM_ROLE_LABELS
    ]
    custom.sort(key=lambda r: (r["label"].casefold(), r["key"]))
    return catalog + custom


def role_keys_matching(catalog, q) -> list[str]:
    """Ключи ролей, чьё название содержит строку поиска (без учёта регистра).

    В users.role лежит ключ (parts_manager), а список показывает название
    («Parts manager», или новое имя переименованной роли) — поиск по
    названию тоже должен находить людей с этой ролью.
    """
    needle = " ".join(str(q or "").split()).casefold()
    if not needle:
        return []
    return [r["key"] for r in catalog or [] if needle in str(r.get("label") or "").casefold()]


def _display_name(u) -> str:
    """Имя, как его показывает строка списка: name, иначе «first last»."""
    name = u.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    first = u.get("first_name") if isinstance(u.get("first_name"), str) else ""
    last = u.get("last_name") if isinstance(u.get("last_name"), str) else ""
    return f"{first} {last}".strip()


def _user_sort_key(u):
    name = _display_name(u)
    # Безымянные (только email) — в конце группы, между собой по email.
    return (0 if name else 1, name.casefold(), str(u.get("email") or "").casefold())


def group_users_by_role(users, catalog, keep_order: bool = False) -> list[dict]:
    """Разложить пользователей по ролям для списка Settings → Users & Roles.

    Возвращает [{key, label, kind, users, invited_count}] в порядке показа:
    системные роли (порядок каталога), кастомные A→Z, роли, которых нет в
    каталоге (kind="missing" — «Role not found», прав от роли у таких людей
    нет), и последней — общая группа Inactive (kind="inactive"):
    деактивированные по ролям не делятся. Пустые группы не возвращаются.

    Внутри группы — по имени (casefold); keep_order=True сохраняет входной
    порядок (явная сортировка из URL). Приглашённые (invite_pending) остаются
    в группе своей роли и считаются в invited_count. Каждому пользователю
    проставляется u["role_label"] — колонка Role и для неактивных.
    """
    by_key = {r["key"]: (idx, r) for idx, r in enumerate(catalog or [])}
    buckets: dict[str, dict] = {}
    inactive: list[dict] = []

    for u in users or []:
        key = normalize_role_key(u)
        hit = by_key.get(key)
        role = hit[1] if hit else None
        u["role_label"] = role["label"] if role else role_label_from_key(key)
        # Та же проверка, что у бэджа статуса в строке: is_active по
        # умолчанию True, ложное значение — Inactive.
        if not u.get("is_active", True):
            inactive.append(u)
            continue
        group = buckets.get(key)
        if group is None:
            group = buckets[key] = {
                "key": key,
                "label": u["role_label"],
                "kind": role["kind"] if role else ROLE_KIND_MISSING,
                "users": [],
                "invited_count": 0,
            }
        group["users"].append(u)
        if u.get("invite_pending"):
            group["invited_count"] += 1

    def _group_order(g):
        hit = by_key.get(g["key"])
        if hit:
            return (0, hit[0], "", "")
        return (1, 0, g["label"].casefold(), g["key"])

    groups = sorted(buckets.values(), key=_group_order)
    if inactive:
        groups.append({
            "key": INACTIVE_GROUP_KEY,
            "label": INACTIVE_GROUP_LABEL,
            "kind": ROLE_KIND_INACTIVE,
            "users": inactive,
            "invited_count": 0,
        })

    if not keep_order:
        for g in groups:
            g["users"].sort(key=_user_sort_key)
    return groups
