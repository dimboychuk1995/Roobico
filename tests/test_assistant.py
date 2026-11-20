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


# ── кэш промпта: неизменное — в системном сообщении, меняющееся — в user ──

def test_static_prompt_is_identical_for_every_request(app):
    """Системное сообщение не зависит ни от страницы, ни от времени, ни от
    набора коллекций — иначе провайдер не кэширует ~70 тыс. токенов справки
    и каждый вопрос оплачивается целиком."""
    import app.blueprints.assistant.services.chat as chat

    with app.app_context():
        data_a = chat.build_data_section({"work_orders", "parts"}, "America/Chicago")
        data_b = chat.build_data_section({"customers"}, "Europe/Kyiv")
        first = chat.build_messages("/reports — Reports", [{"role": "user", "content": "q1"}], data_a)
        second = chat.build_messages("/parts/ — Parts", [{"role": "user", "content": "q2"}], data_b)
        no_data = chat.build_messages("/parts/ — Parts", [{"role": "user", "content": "q2"}])

    assert first[0]["role"] == second[0]["role"] == "system"
    assert first[0]["content"] == second[0]["content"]
    static = first[0]["content"]
    assert "General Revenue" in static and "Database schema of a shop" in static
    for dynamic in ("/reports — Reports", "/parts/ — Parts", "Current UTC time", "America/Chicago", "work_orders, parts"):
        assert dynamic not in static, dynamic
    # Без доступа к данным схемы нет, но начало (правила + уроки + справка) общее.
    assert "Database schema of a shop" not in no_data[0]["content"]
    assert static.startswith(no_data[0]["content"])
    # Системное сообщение одно: второе system/developer ломает кэш у GPT-6.
    assert [m["role"] for m in first] == ["system", "user", "user"]
    assert [m["role"] for m in no_data] == ["system", "user", "user"]


def test_request_context_goes_right_before_the_latest_question(app):
    import app.blueprints.assistant.services.chat as chat

    history = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "follow-up"},
    ]
    with app.app_context():
        msgs = chat.build_messages("/calendar — Calendar", history, chat.build_data_section({"calendar_events"}, "America/Chicago"))

    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user", "user"]
    assert [m["content"] for m in msgs[1:3]] == ["first question", "first answer"]  # история не тронута
    context = msgs[3]["content"]
    assert context.startswith("Context of this request (added by the Roobico app, not typed by the user):")
    assert "/calendar — Calendar" in context and "calendar_events" in context
    assert "Current UTC time" in context and "America/Chicago" in context
    assert msgs[4] == {"role": "user", "content": "follow-up"}


def test_completion_params_follow_the_model_generation(monkeypatch):
    import app.blueprints.assistant.services.chat as chat

    monkeypatch.delenv("ASSISTANT_REASONING_EFFORT", raising=False)
    assert chat.completion_params("gpt-4o-mini") == {"max_tokens": chat.MAX_ANSWER_TOKENS, "temperature": 0.3}
    assert chat.completion_params("gpt-4.1") == {"max_tokens": chat.MAX_ANSWER_TOKENS, "temperature": 0.3}
    # GPT-5 / GPT-6: max_tokens и temperature не принимаются; инструменты в
    # Chat Completions работают только с reasoning_effort="none".
    for model in (chat.DEFAULT_MODEL, "gpt-6-luna", "gpt-5.4-mini", "o3"):
        assert chat.completion_params(model) == {"max_completion_tokens": chat.MAX_ANSWER_TOKENS, "reasoning_effort": "none"}, model
    monkeypatch.setenv("ASSISTANT_REASONING_EFFORT", "low")
    assert chat.completion_params("gpt-6-luna")["reasoning_effort"] == "low"
    monkeypatch.setenv("ASSISTANT_REASONING_EFFORT", "")
    assert "reasoning_effort" not in chat.completion_params("gpt-6-luna")
    assert not chat.DEFAULT_MODEL.startswith(chat._LEGACY_CHAT_PREFIXES)


def test_cached_tokens_are_logged_and_count_towards_the_cap_at_a_discount(app, seed):
    import app.blueprints.assistant.services.chat as chat

    tenant_id = str(seed["tenant_a"]["_id"])
    with app.app_context():
        from app.extensions import get_master_db

        master = get_master_db()
        master.assistant_usage.delete_many({"tenant_id": tenant_id})
        master.assistant_logs.delete_many({"tenant_id": tenant_id})
        # Промах кэша: весь вход по полной.
        chat._record_exchange(tenant_id=tenant_id, user_id="u", shop_id="s", page="/p", question="miss?", answer="a",
                              model="m", tokens_in=70_000, tokens_out=200)
        assert chat.monthly_tokens_used(tenant_id) == 70_200
        # Попадание: 69 000 из 70 000 прочитаны из кэша -> 1 000 + 6 900 + выход.
        chat._record_exchange(tenant_id=tenant_id, user_id="u", shop_id="s", page="/p", question="hit?", answer="a",
                              model="m", tokens_in=70_000, tokens_out=300, tokens_cached=69_000)
        assert chat.monthly_tokens_used(tenant_id) == 70_200 + 1_000 + 6_900 + 300

        usage = master.assistant_usage.find_one({"tenant_id": tenant_id})
        assert (usage["tokens_in"], usage["tokens_cached"], usage["requests"]) == (140_000, 69_000, 2)
        log = master.assistant_logs.find_one({"tenant_id": tenant_id, "question": "hit?"})
        assert (log["tokens_in"], log["tokens_cached"], log["tokens_out"]) == (70_000, 69_000, 300)
        master.assistant_usage.delete_many({"tenant_id": tenant_id})
        master.assistant_logs.delete_many({"tenant_id": tenant_id})


class _FakeStream(list):
    pass


def _chunk(text=None, finish=None, usage=None):
    from types import SimpleNamespace as NS

    choices = [] if usage is not None else [NS(delta=NS(content=text, tool_calls=None), finish_reason=finish)]
    return NS(choices=choices, usage=usage)


def test_stream_answer_sends_the_right_request_and_records_cache_usage(app, seed, logged_client, monkeypatch):
    """Без сети: фейковый клиент OpenAI. Проверяем сам запрос (модель, параметры
    поколения, раскладка сообщений) и что usage со cached_tokens дошёл до лога."""
    from types import SimpleNamespace as NS

    import app.blueprints.assistant.services.chat as chat

    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            usage = NS(prompt_tokens=71_000, completion_tokens=40, prompt_tokens_details=NS(cached_tokens=70_500))
            return _FakeStream([_chunk("Open **Parts**"), _chunk(" and click Add.", finish="stop"), _chunk(usage=usage)])

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("ASSISTANT_MODEL", raising=False)
    monkeypatch.delenv("ASSISTANT_REASONING_EFFORT", raising=False)
    monkeypatch.setenv("ASSISTANT_DATA_TOOLS", "0")
    monkeypatch.setattr(chat, "_make_client", lambda api_key: NS(chat=NS(completions=FakeCompletions())))

    tenant_id = str(seed["tenant_a"]["_id"])
    with app.app_context():
        from app.extensions import get_master_db

        master = get_master_db()
        master.assistant_logs.delete_many({"tenant_id": tenant_id})
        master.assistant_usage.delete_many({"tenant_id": tenant_id})

    token = get_csrf_token(logged_client)
    resp = _post_chat(logged_client, token, {"messages": [{"role": "user", "content": "how to add a part?"}], "page": "/parts/ — Parts"})
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "Open **Parts** and click Add."

    assert len(calls) == 1
    sent = calls[0]
    assert sent["model"] == chat.DEFAULT_MODEL and sent["stream"] is True
    assert sent["max_completion_tokens"] == chat.MAX_ANSWER_TOKENS and sent["reasoning_effort"] == "none"
    assert "max_tokens" not in sent and "temperature" not in sent and "tools" not in sent
    assert [m["role"] for m in sent["messages"]] == ["system", "user", "user"]
    assert "/parts/ — Parts" in sent["messages"][1]["content"] and "/parts/ — Parts" not in sent["messages"][0]["content"]
    assert sent["messages"][2] == {"role": "user", "content": "how to add a part?"}

    with app.app_context():
        log = master.assistant_logs.find_one({"tenant_id": tenant_id})
        assert (log["model"], log["tokens_in"], log["tokens_cached"], log["tokens_out"]) == (chat.DEFAULT_MODEL, 71_000, 70_500, 40)
        assert chat.monthly_tokens_used(tenant_id) == 500 + 7_050 + 40
        master.assistant_logs.delete_many({"tenant_id": tenant_id})
        master.assistant_usage.delete_many({"tenant_id": tenant_id})

    # Прежняя модель через env — прежняя форма запроса.
    monkeypatch.setenv("ASSISTANT_MODEL", "gpt-4o-mini")
    calls.clear()
    resp = _post_chat(logged_client, token, {"messages": [{"role": "user", "content": "again"}]})
    assert resp.status_code == 200 and resp.get_data(as_text=True)
    assert calls[0]["model"] == "gpt-4o-mini" and calls[0]["max_tokens"] == chat.MAX_ANSWER_TOKENS and calls[0]["temperature"] == 0.3
    assert "max_completion_tokens" not in calls[0] and "reasoning_effort" not in calls[0]
    with app.app_context():
        master.assistant_logs.delete_many({"tenant_id": tenant_id})
        master.assistant_usage.delete_many({"tenant_id": tenant_id})
