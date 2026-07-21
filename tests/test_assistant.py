"""AI-помощник: доступ, валидация, лимиты, стриминг и учёт токенов.

LLM в тестах не вызывается: stream_answer подменяется фейковым генератором,
а конфигурационная ветка проверяется отсутствием OPENAI_API_KEY.
"""
import json

import pytest

from tests.conftest import get_csrf_token, login


def _post_chat(client, token, payload):
    return client.post(
        "/assistant/api/chat",
        data=json.dumps(payload),
        content_type="application/json",
        headers={"X-CSRFToken": token},
    )


@pytest.fixture()
def logged_client(client):
    login(client)
    return client


def test_requires_login(client):
    token = get_csrf_token(client)
    resp = _post_chat(client, token, {"messages": []})
    # login_required редиректит на главную
    assert resp.status_code in (301, 302)


def test_validation_empty_messages(logged_client):
    token = get_csrf_token(logged_client)
    resp = _post_chat(logged_client, token, {"messages": []})
    assert resp.status_code == 400

    resp = _post_chat(logged_client, token, {"messages": [{"role": "assistant", "content": "hi"}]})
    assert resp.status_code == 400  # последнее сообщение должно быть от пользователя


def test_not_configured_returns_503(logged_client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    token = get_csrf_token(logged_client)
    resp = _post_chat(logged_client, token, {"messages": [{"role": "user", "content": "help"}]})
    assert resp.status_code == 503


def test_chat_streams_answer(logged_client, monkeypatch):
    import app.blueprints.assistant.services.chat as chat_service

    captured = {}

    def fake_stream_answer(**kwargs):
        captured.update(kwargs)
        yield "Open "
        yield "**Work Orders** and click New."

    monkeypatch.setattr(chat_service, "stream_answer", fake_stream_answer)

    token = get_csrf_token(logged_client)
    resp = _post_chat(logged_client, token, {
        "messages": [
            {"role": "user", "content": "How do I create a work order?"},
        ],
        "page": "/work_orders — Work Orders",
    })
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert body == "Open **Work Orders** and click New."
    assert captured["page"] == "/work_orders — Work Orders"
    assert captured["messages"][-1]["role"] == "user"
    assert captured["tenant_id"]


def test_monthly_cap_returns_429(logged_client, monkeypatch):
    import app.blueprints.assistant.services.chat as chat_service

    monkeypatch.setattr(chat_service, "monthly_tokens_used", lambda tenant_id: 10**9)

    token = get_csrf_token(logged_client)
    resp = _post_chat(logged_client, token, {"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 429


def test_rate_limit_returns_429(logged_client, monkeypatch):
    import app.blueprints.assistant.routes as routes

    monkeypatch.setattr(routes, "hit_rate_limit", lambda *a, **k: True)

    token = get_csrf_token(logged_client)
    resp = _post_chat(logged_client, token, {"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 429


def test_usage_accounting(app, seed):
    """_record_exchange инкрементит месячный счётчик и пишет лог."""
    import app.blueprints.assistant.services.chat as chat_service

    tenant_id = str(seed["tenant_a"]["_id"])
    with app.app_context():
        from app.extensions import get_master_db
        master = get_master_db()
        master.assistant_usage.delete_many({"tenant_id": tenant_id})
        master.assistant_logs.delete_many({"tenant_id": tenant_id})

        chat_service._record_exchange(
            tenant_id=tenant_id, user_id="u1", shop_id="s1", page="/reports",
            question="q?", answer="a.", model="test-model",
            tokens_in=100, tokens_out=50,
        )
        chat_service._record_exchange(
            tenant_id=tenant_id, user_id="u1", shop_id="s1", page="/reports",
            question="q2?", answer="a2.", model="test-model",
            tokens_in=10, tokens_out=5,
        )

        assert chat_service.monthly_tokens_used(tenant_id) == 165
        assert master.assistant_logs.count_documents({"tenant_id": tenant_id}) == 2
        log = master.assistant_logs.find_one({"tenant_id": tenant_id, "question": "q?"})
        assert log["answer"] == "a."
        assert log["model"] == "test-model"


def test_system_prompt_contains_docs(app):
    import app.blueprints.assistant.services.chat as chat_service

    prompt = chat_service.build_system_prompt("/reports — Reports")
    assert "General Revenue" in prompt          # справка подхватилась
    assert "/reports — Reports" in prompt       # контекст страницы попал в промпт
    assert "NEVER invent features" in prompt
    # Ключевые «нестандартные» темы должны быть покрыты справкой:
    assert "Pricing Scale" in prompt            # per-customer ценообразование
    assert "Special parts margin for ONE customer" in prompt  # рецепт маржи
    assert "Override part selling price" in prompt
    assert "mechanic mode" in prompt            # механик без цен
    assert "Bulk Payment" in prompt             # один чек на много инвойсов
    assert "Stocktake" in prompt                # инвентаризация
