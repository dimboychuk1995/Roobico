"""
Адреса сайтов (вендоры): ввод «как попало» → рабочая ссылка.

Пользователь пишет fleetpride.com / www.x.com/login / //x.com — храним
https://fleetpride.com и т. п., https:// набирать не нужно. Разрешены только
http/https: javascript:, data:, ftp:, mailto: и прочие схемы отклоняются
(None) — вызывающий показывает INVALID_WEBSITE_MESSAGE. Хост обязателен,
приводится к нижнему регистру, без пробелов и без «логина@» в адресе, с
точкой (или localhost), порт допустим; путь, query и fragment сохраняются
как набраны, одиночный завершающий «/» отбрасывается.

- normalize_website — значение для записи в базу (или None);
- clean_website_input — то же для форм: (значение, текст ошибки);
- website_href — ссылка для <a href> (в т. ч. для старых значений, сохранённых
  без https: они сразу становятся рабочими ссылками), "" — ссылки нет;
- website_label — короткая подпись: хост + путь без схемы и без «/» в конце
  (fleetpride.com/login); значение, которое не нормализуется, — как есть.
"""
from __future__ import annotations

import re

INVALID_WEBSITE_MESSAGE = "Enter a valid website, e.g. fleetpride.com"
MAX_WEBSITE_CHARS = 2048

_ALLOWED_SCHEMES = ("http", "https")
# «схема:» в начале строки (RFC 3986: буква, затем буквы/цифры/+.-).
_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")
# «host:порт» — не схема: localhost:5000, x.com:8080/login. Порт — только
# ASCII-цифры ([0-9], не \d: тот ловит и «٣» и т. п. — ссылка бы не работала).
# Правила продублированы в static/js/vendors/vendors.js (normalizeWebsite) —
# менять вместе.
_PORT_TAIL_RE = re.compile(r"^[0-9]{1,5}(?:[/?#]|$)")
_AUTHORITY_RE = re.compile(r"^([^/?#]*)(.*)$", re.S)
_HOST_PORT_RE = re.compile(r"^(.*):([0-9]{1,5})$")
# Метка домена: буквы (в т. ч. IDN), цифры, «_», дефис не по краям.
_HOST_LABEL_RE = re.compile(r"^\w(?:[\w-]*\w)?$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _valid_host(host: str) -> bool:
    if not host or len(host) > 253:
        return False
    if host == "localhost":
        return True
    labels = host.split(".")
    if len(labels) < 2:
        return False
    return all(0 < len(label) <= 63 and _HOST_LABEL_RE.match(label) for label in labels)


def normalize_website(raw) -> str | None:
    """Нормализованный URL сайта или None (пусто / невалидно)."""
    text = str(raw or "").strip()
    if not text or len(text) > MAX_WEBSITE_CHARS or _CONTROL_RE.search(text):
        return None

    match = _SCHEME_RE.match(text)
    if match and not _PORT_TAIL_RE.match(text[match.end():]):
        scheme = match.group(1).lower()
        rest = text[match.end():]
        # Только http(s) и только с «//»: «http:x.com» — не адрес сайта.
        if scheme not in _ALLOWED_SCHEMES or not rest.startswith("//"):
            return None
        rest = rest[2:]
    elif text.startswith("//"):
        scheme, rest = "https", text[2:]
    else:
        scheme, rest = "https", text

    authority, tail = _AUTHORITY_RE.match(rest).groups()
    # «user:pass@host» и «\» (браузеры читают его как «/») — не принимаем:
    # иначе подпись ссылки и реальный адрес разошлись бы.
    if "@" in authority or "\\" in authority or any(ch.isspace() for ch in authority):
        return None

    host, port = authority, ""
    port_match = _HOST_PORT_RE.match(authority)
    if port_match:
        host, port = port_match.groups()
        if not 0 < int(port) <= 65535:
            return None
    elif ":" in authority:
        return None  # «x.com:abc», IPv6-литералы — не поддерживаем

    # Точка в конце хоста («fleetpride.com.» из предложения) — лишняя.
    host = host.lower()
    if host.endswith("."):
        host = host[:-1]
    if not _valid_host(host):
        return None

    if tail == "/":
        tail = ""
    return f"{scheme}://{host}{':' + port if port else ''}{tail}"


def clean_website_input(raw) -> tuple[str | None, str | None]:
    """Поле формы → (нормализованный URL | None, текст ошибки | None).

    Пусто — (None, None): сайт необязателен. Невалидно — (None, сообщение)."""
    text = str(raw or "").strip()
    if not text:
        return None, None
    normalized = normalize_website(text)
    if not normalized:
        return None, INVALID_WEBSITE_MESSAGE
    return normalized, None


def website_href(raw) -> str:
    """Ссылка для href: нормализованный URL или "" (старое значение-мусор)."""
    return normalize_website(raw) or ""


def website_label(raw) -> str:
    """Подпись ссылки: хост + путь без схемы и «/» в конце (fleetpride.com/login)."""
    url = normalize_website(raw)
    if not url:
        return str(raw or "").strip()
    rest = url.split("://", 1)[1]
    rest = re.split(r"[?#]", rest, maxsplit=1)[0]
    return rest.rstrip("/")
