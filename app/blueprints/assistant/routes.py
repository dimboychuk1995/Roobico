from __future__ import annotations

from flask import Response, current_app, jsonify, request, session, stream_with_context

from app.utils.auth import (
    SESSION_SHOP_ID,
    SESSION_TENANT_ID,
    SESSION_USER_ID,
    login_required,
)
from app.utils.rate_limit import hit_rate_limit
from . import assistant_bp
from .services import chat as chat_service

# Клиент шлёт хвост диалога; жёсткие рамки, чтобы промпт не раздувался.
MAX_MESSAGES = 12
MAX_CONTENT_CHARS = 2000


@assistant_bp.post("/api/chat")
@login_required
def chat():
    user_id = str(session.get(SESSION_USER_ID) or "")
    tenant_id = str(session.get(SESSION_TENANT_ID) or "")
    shop_id = str(session.get(SESSION_SHOP_ID) or "")

    if hit_rate_limit("assistant_chat", user_id or request.remote_addr,
                      max_attempts=20, window_seconds=300):
        return jsonify(ok=False, error="Too many messages — please wait a few minutes."), 429

    data = request.get_json(silent=True) or {}
    raw_messages = data.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        return jsonify(ok=False, error="messages are required"), 400

    messages: list[dict] = []
    for m in raw_messages[-MAX_MESSAGES:]:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = str(m.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        messages.append({"role": role, "content": content[:MAX_CONTENT_CHARS]})
    if not messages or messages[-1]["role"] != "user":
        return jsonify(ok=False, error="last message must be from the user"), 400

    page = str(data.get("page") or "")[:300]

    if chat_service.monthly_tokens_used(tenant_id) >= chat_service.monthly_limit():
        return jsonify(
            ok=False,
            error="The AI assistant reached its monthly usage limit for your organization.",
        ), 429

    try:
        gen = chat_service.stream_answer(
            tenant_id=tenant_id, user_id=user_id, shop_id=shop_id,
            page=page, messages=messages,
        )
        # Форсируем первый чанк здесь: ошибки конфигурации/API становятся
        # нормальным JSON-ответом, а не оборванным стримом.
        first = next(gen, "")
    except chat_service.AssistantUnavailable:
        return jsonify(ok=False, error="AI assistant is not configured."), 503
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Assistant chat failed")
        return jsonify(ok=False, error="Assistant failed — please try again."), 502

    def generate():
        yield first
        try:
            for chunk in gen:
                yield chunk
        except Exception:  # noqa: BLE001
            current_app.logger.exception("Assistant stream interrupted")
            yield "\n\n[Connection error — please try again]"

    resp = Response(stream_with_context(generate()), mimetype="text/plain; charset=utf-8")
    resp.headers["X-Accel-Buffering"] = "no"  # nginx: отдавать чанки сразу
    resp.headers["Cache-Control"] = "no-cache"
    return resp
