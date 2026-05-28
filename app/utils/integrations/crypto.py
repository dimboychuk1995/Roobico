"""Symmetric encryption for 3rd-party integration secrets (API keys, etc.).

Uses Fernet (AES-128-CBC + HMAC) with a key from ``INTEGRATIONS_SECRET_KEY``
in Flask config. The key is base64-urlsafe-encoded 32 raw bytes.

Generate one with::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

from flask import current_app
from cryptography.fernet import Fernet, InvalidToken


class IntegrationsCryptoError(RuntimeError):
    """Raised when the master key is missing or a token cannot be decrypted."""


def _get_fernet() -> Fernet:
    key = (current_app.config.get("INTEGRATIONS_SECRET_KEY") or "").strip()
    if not key:
        raise IntegrationsCryptoError(
            "INTEGRATIONS_SECRET_KEY is not set in environment. "
            "Generate one with `python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and add it to .env."
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise IntegrationsCryptoError(
            "INTEGRATIONS_SECRET_KEY is not a valid Fernet key (must be 32 url-safe "
            "base64-encoded bytes)."
        ) from exc


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret string. Returns the Fernet token as a UTF-8 string."""
    if plaintext is None:
        plaintext = ""
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Decrypt a Fernet token back to the plaintext string."""
    if not token:
        return ""
    f = _get_fernet()
    try:
        return f.decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise IntegrationsCryptoError(
            "Stored integration secret could not be decrypted. "
            "Has INTEGRATIONS_SECRET_KEY been rotated without re-encryption?"
        ) from exc


def mask_secret(plaintext: str, *, visible: int = 4) -> str:
    """Return a masked preview: ``••••••••abcd`` (last *visible* chars shown)."""
    if not plaintext:
        return ""
    s = str(plaintext)
    if len(s) <= visible:
        return "•" * len(s)
    return "•" * 8 + s[-visible:]
