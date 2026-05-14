"""
Helpers for building URLs that target a specific host in the
roobico.com / app.roobico.com split.

In dev (ENFORCE_HOST_SPLIT=false) both helpers fall back to a regular
relative `url_for(...)` so local `python run.py` keeps working unchanged.
"""
from __future__ import annotations

from flask import current_app, url_for


def _enforce() -> bool:
    return bool(current_app.config.get("ENFORCE_HOST_SPLIT"))


def app_url(endpoint: str, **values) -> str:
    """Build a URL that always points at the application host (app.roobico.com)."""
    path = url_for(endpoint, **values)
    if not _enforce():
        return path
    base = (current_app.config.get("APP_BASE_URL") or "").rstrip("/")
    return f"{base}{path}" if base else path


def public_url(endpoint: str, **values) -> str:
    """Build a URL that always points at the public host (roobico.com)."""
    path = url_for(endpoint, **values)
    if not _enforce():
        return path
    base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    return f"{base}{path}" if base else path
