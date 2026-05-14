"""
Per-host session split.

The tenant app (`roobico.com` / `app.roobico.com`) and the admin panel
(`admin.roobico.com`) must NEVER share a session. We achieve that with a
single Flask app by swapping cookie name + cookie domain on every request
based on the Host header.

  * Tenant hosts → cookie `session`, domain `.roobico.com` (so login on
    roobico.com is recognised on app.roobico.com).
  * Admin host   → cookie `admin_session`, host-only (no domain), so the
    cookie is only ever sent to `admin.roobico.com`.

In dev (when no `*_HOST` setting matches) we fall back to whatever Flask
was configured with — keeping `python run.py` behaviour unchanged.
"""
from __future__ import annotations

from flask import Flask, request
from flask.sessions import SecureCookieSessionInterface


def _is_admin_host(app: Flask) -> bool:
    if not request:
        return False
    host = (request.host or "").split(":", 1)[0].lower()
    admin_host = (app.config.get("ADMIN_HOST") or "").lower()
    return bool(admin_host) and host == admin_host


class HostAwareSessionInterface(SecureCookieSessionInterface):
    """
    Overrides `get_cookie_name` and `get_cookie_domain` so admin requests
    use a separate cookie that is never visible to the tenant app.

    Everything else (signing, expiry, secure flag) inherits from
    SecureCookieSessionInterface.
    """

    def get_cookie_name(self, app: Flask) -> str:
        if _is_admin_host(app):
            return app.config.get("ADMIN_SESSION_COOKIE_NAME", "admin_session")
        return super().get_cookie_name(app)

    def get_cookie_domain(self, app: Flask):
        if _is_admin_host(app):
            # Host-only cookie: admin cookie must never leak to other subdomains.
            return None
        return super().get_cookie_domain(app)
