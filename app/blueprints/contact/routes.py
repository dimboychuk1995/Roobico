from __future__ import annotations

from flask import current_app, jsonify, request

from app.utils.rate_limit import hit_rate_limit
from . import contact_bp
from . import services


@contact_bp.post("/contact")
def submit():
    """Форма «Contact us» со страницы логина (JSON-ответ, сабмит fetch'ем)."""
    if hit_rate_limit("contact", request.remote_addr, max_attempts=5, window_seconds=3600):
        return jsonify(ok=False, errors=["Too many messages from this address. Please call us or try again later."]), 429

    reason = services.spam_reason(request.form)
    if reason:
        current_app.logger.warning(
            "Blocked bot-like contact request from %s (%s), email=%r",
            request.remote_addr, reason, (request.form.get("email") or "")[:120],
        )
        return jsonify(ok=False, errors=["We couldn't send your message. Please call us instead."]), 400

    payload, errors = services.validate(request.form)
    if errors:
        return jsonify(ok=False, errors=errors), 400

    try:
        services.create_request(payload)
    except Exception:
        current_app.logger.exception("Failed to store contact request")
        return jsonify(ok=False, errors=["Something went wrong. Please call us or try again later."]), 500

    return jsonify(ok=True, message="Thanks! We received your message and will get back to you shortly.")
