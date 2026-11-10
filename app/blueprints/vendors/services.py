"""
Бизнес-логика вендоров: сайт и учётка портала вендора.

Сайт: любой ввод нормализуется app/utils/urls.py (https:// дописывается сам,
чужие схемы отклоняются) — clean_website на каждом пути записи.

Учётка портала (логин/пароль на сайте вендора):
- portal_login — открытый текст (его показывают, до 256 символов);
- portal_password_enc — Fernet-токен (app/utils/integrations/crypto.py, ключ
  INTEGRATIONS_SECRET_KEY — тот же, что у uAttend), плюс
  portal_password_updated_at / portal_password_updated_by.
Открытый пароль не хранится, не логируется, не пишется в Activity Journal
(там только set / changed / removed), не рендерится в HTML и не отдаётся
списками/деталями — только эндпоинтом /vendors/api/<id>/credentials по праву
vendors.view_credentials (rate limit + запись «Viewed» в журнал). Логин в
журнал тоже идёт без значения (set / changed / removed), а в теле
отклонённого запроса маскируется (reports/audit/journal.py): журнал видят
и пользователи без права на учётки.
Без права поля учётки не показываются, не отдаются и молча игнорируются
при сохранении.
"""
from __future__ import annotations

from bson import ObjectId
from flask import current_app

from app.utils import audit_log
from app.utils.integrations.crypto import (
    IntegrationsCryptoError,
    decrypt_secret,
    encrypt_secret,
    secret_key_configured,
)
from app.utils.urls import clean_website_input, website_href, website_label

CREDENTIALS_PERMISSION = "vendors.view_credentials"

MAX_PORTAL_LOGIN_CHARS = 256
MAX_PORTAL_PASSWORD_CHARS = 256

# Показ пароля: не чаще 60 раз за 10 минут на пользователя.
REVEAL_RATE_LIMIT = 60
REVEAL_RATE_WINDOW_SECONDS = 600

ENCRYPTION_NOT_CONFIGURED_MESSAGE = (
    "Password encryption is not configured on the server — contact support"
)
PASSWORD_UNREADABLE_MESSAGE = (
    "The saved password can't be decrypted (the server key changed) — enter it again."
)
REVEAL_RATE_LIMIT_MESSAGE = (
    "Too many password requests — please wait a few minutes and try again."
)

# Поля, которые никогда не уходят клиенту как есть.
SECRET_FIELDS = ("portal_password_enc",)


class VendorInputError(ValueError):
    """Ошибка ввода/конфигурации с кодом и текстом для UI."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def payload(self) -> dict:
        return {"ok": False, "error": self.code, "message": self.message}


def clean_website(raw) -> str | None:
    """Сайт из формы → нормализованный URL / None; мусор → VendorInputError."""
    website, error = clean_website_input(raw)
    if error:
        raise VendorInputError("invalid_website", error)
    return website


def website_fields(stored) -> dict:
    """website (как хранится), website_href (рабочая ссылка), website_label."""
    return {
        "website": str(stored or "").strip(),
        "website_href": website_href(stored),
        "website_label": website_label(stored),
    }


def has_portal_password(vendor: dict | None) -> bool:
    return bool((vendor or {}).get("portal_password_enc"))


def credential_fields(vendor: dict | None) -> dict:
    """Публичная часть учётки (только для пользователей с правом): логин и
    флаг «пароль сохранён». Ни пароля, ни токена."""
    vendor = vendor or {}
    return {
        "portal_login": str(vendor.get("portal_login") or ""),
        "has_portal_password": has_portal_password(vendor),
    }


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _encryption_error() -> VendorInputError:
    current_app.logger.error(
        "INTEGRATIONS_SECRET_KEY is missing or invalid — vendor portal passwords cannot be saved or shown"
    )
    return VendorInputError("encryption_not_configured", ENCRYPTION_NOT_CONFIGURED_MESSAGE, 503)


def build_credential_update(
    payload: dict,
    existing: dict | None,
    *,
    user_id: ObjectId | None,
    now,
) -> tuple[dict, list[dict]]:
    """Поля учётки для $set + изменения для журнала (без значений пароля).

    existing=None — создание вендора. Ключа portal_login нет в payload —
    логин не трогаем (старые клиенты, частичные формы); пустая строка —
    логин удаляется. Пустой portal_password — сохранённый пароль остаётся;
    portal_password_clear — удаляется (новый пароль важнее флага удаления).
    """
    existing = existing or {}
    fields: dict = {}
    changes: list[dict] = []

    if "portal_login" in payload:
        login = str(payload.get("portal_login") or "").strip()
        if len(login) > MAX_PORTAL_LOGIN_CHARS:
            raise VendorInputError(
                "portal_login_too_long",
                f"Portal login is too long (max {MAX_PORTAL_LOGIN_CHARS} characters).",
            )
        old_login = str(existing.get("portal_login") or "")
        if login != old_login:
            fields["portal_login"] = login or None
            # Как и пароль — без значений: журнал видят все с reports.view_audit
            # (в т. ч. Viewer), а логин показывается только с правом на учётки.
            changes.append(audit_log.change(
                "portal_login",
                "set" if old_login else "",
                ("changed" if old_login else "set") if login else "removed",
                "Portal login",
            ))

    # Имя переменной — password: стандартный denylist Sentry вычищает такие
    # локальные переменные из отчётов об ошибках.
    password = payload.get("portal_password")
    password = password if isinstance(password, str) else ""
    had_password = has_portal_password(existing)
    if password.strip():
        if len(password) > MAX_PORTAL_PASSWORD_CHARS:
            raise VendorInputError(
                "portal_password_too_long",
                f"Portal password is too long (max {MAX_PORTAL_PASSWORD_CHARS} characters).",
            )
        try:
            token = encrypt_secret(password)
        except IntegrationsCryptoError:
            raise _encryption_error() from None
        fields.update({
            "portal_password_enc": token,
            "portal_password_updated_at": now,
            "portal_password_updated_by": user_id,
        })
        changes.append(audit_log.change(
            "portal_password", "set" if had_password else "", "changed" if had_password else "set",
            "Portal password",
        ))
    elif _truthy(payload.get("portal_password_clear")) and had_password:
        fields.update({
            "portal_password_enc": None,
            "portal_password_updated_at": now,
            "portal_password_updated_by": user_id,
        })
        changes.append(audit_log.change("portal_password", "set", "removed", "Portal password"))

    return fields, changes


def reveal_credentials(vendor: dict) -> dict:
    """Расшифрованная учётка для эндпоинта показа (и только для него)."""
    token = str(vendor.get("portal_password_enc") or "")
    try:
        password = decrypt_secret(token) if token else ""
    except IntegrationsCryptoError:
        if not secret_key_configured():
            raise _encryption_error() from None
        current_app.logger.error(
            "vendor %s: stored portal password cannot be decrypted with the current key", vendor.get("_id")
        )
        raise VendorInputError("password_unreadable", PASSWORD_UNREADABLE_MESSAGE, 503) from None
    site = website_fields(vendor.get("website"))
    return {
        "website": site["website"],
        "website_href": site["website_href"],
        "login": str(vendor.get("portal_login") or ""),
        "password": password,
    }


def shop_vendor_filter(vendor_id: ObjectId, shop: dict) -> dict:
    """Вендор активного магазина: _id + shop_id (ObjectId или легаси-строка;
    у очень старых документов shop_id может не быть — они лежат в базе этого
    же магазина, поэтому тоже свои)."""
    shop_id = shop["_id"]
    return {"_id": vendor_id, "shop_id": {"$in": [shop_id, str(shop_id), None]}}
