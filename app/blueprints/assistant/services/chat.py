"""AI-помощник по продукту: отвечает «как сделать X в Roobico».

Знания = markdown-файлы в app/help/. Вся справка целиком уходит в системный
промпт — при текущем размере (< 30 KB) RAG/векторная база не нужны, а
кэширование промпта на стороне провайдера делает повторные запросы дешёвыми.

Учёт: месячный счётчик токенов на тенанта (master.assistant_usage) — жёсткий
колпак расходов; каждый вопрос-ответ пишется в master.assistant_logs — это и
аудит, и продуктовая аналитика (что пользователям непонятно в интерфейсе).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app

from app.extensions import get_master_db

# Справочный чат — недорогая быстрая модель; переопределяется через env.
DEFAULT_MODEL = "gpt-4o-mini"
MAX_ANSWER_TOKENS = 700
# Месячный колпак токенов (вход + выход) на тенанта.
DEFAULT_MONTHLY_TOKEN_LIMIT = 2_000_000

_HELP_DIR = Path(__file__).resolve().parents[3] / "help"

_docs_cache: str | None = None


class AssistantUnavailable(RuntimeError):
    """Помощник не сконфигурирован (нет API-ключа и т.п.)."""


def _load_help_docs() -> str:
    global _docs_cache
    if _docs_cache is None:
        parts = []
        for path in sorted(_HELP_DIR.glob("*.md")):
            body = path.read_text(encoding="utf-8").strip()
            parts.append(f'<doc name="{path.stem}">\n{body}\n</doc>')
        _docs_cache = "\n\n".join(parts)
    return _docs_cache


_SYSTEM_TEMPLATE = """\
You are the built-in help assistant of Roobico, a shop-management app for auto
repair shops and fleets. Your job is to tell users HOW to do things in the app.

Rules:
- Answer only questions about using Roobico. For anything else, politely say
  you can only help with Roobico.
- Ground every answer in the documentation below — NEVER invent features,
  buttons or menu items that are not documented.
- Users rarely use the exact words of the documentation. First understand the
  INTENT, then map it to documented capabilities: "special price for a client"
  / "discount for a fleet" / "20% margin for one customer" are all the
  per-customer Pricing Scale; "who owes me" is Customer Balances; "how much
  did I make" is the General Revenue report, and so on. If the goal can be
  reached by COMBINING documented features, explain the combination step by
  step (the recipes doc shows the pattern).
- Only say you are not sure when, after honestly checking the docs, nothing
  covers the goal even indirectly. In that case say what the closest
  documented capability is, and suggest contacting support for the rest.
- Reply in the same language the user writes in.
- Be short and practical: numbered steps, exact section names (Work Orders,
  Reports, Settings...). No fluff.
- The user is currently on this page of the app: {page}

Roobico documentation:

{docs}
"""


def build_system_prompt(page: str) -> str:
    return _SYSTEM_TEMPLATE.format(page=page or "unknown", docs=_load_help_docs())


# ── Token accounting ─────────────────────────────────────────────────


def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def monthly_limit() -> int:
    try:
        return int(os.environ.get("ASSISTANT_MONTHLY_TOKEN_LIMIT") or DEFAULT_MONTHLY_TOKEN_LIMIT)
    except ValueError:
        return DEFAULT_MONTHLY_TOKEN_LIMIT


def monthly_tokens_used(tenant_id: str) -> int:
    doc = get_master_db().assistant_usage.find_one(
        {"tenant_id": str(tenant_id), "month": _month_key()},
        {"tokens_total": 1},
    )
    return int((doc or {}).get("tokens_total") or 0)


def _record_exchange(
    *, tenant_id: str, user_id: str, shop_id: str, page: str,
    question: str, answer: str, model: str, tokens_in: int, tokens_out: int,
) -> None:
    now = datetime.now(timezone.utc)
    master = get_master_db()
    master.assistant_usage.update_one(
        {"tenant_id": str(tenant_id), "month": _month_key()},
        {
            "$inc": {
                "tokens_in": int(tokens_in),
                "tokens_out": int(tokens_out),
                "tokens_total": int(tokens_in) + int(tokens_out),
                "requests": 1,
            },
            "$set": {"updated_at": now},
        },
        upsert=True,
    )
    master.assistant_logs.insert_one({
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "shop_id": str(shop_id or ""),
        "page": page,
        "question": question[:2000],
        "answer": answer[:8000],
        "model": model,
        "tokens_in": int(tokens_in),
        "tokens_out": int(tokens_out),
        "created_at": now,
    })


# ── Chat ─────────────────────────────────────────────────────────────


def stream_answer(
    *, tenant_id: str, user_id: str, shop_id: str, page: str, messages: list[dict],
):
    """Стримит текст ответа кусками; в конце записывает usage и лог.

    messages — уже провалидированные [{role: user|assistant, content: str}].
    Raises AssistantUnavailable, если ключ не сконфигурирован.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise AssistantUnavailable("OPENAI_API_KEY is not configured")

    from openai import OpenAI

    model = os.environ.get("ASSISTANT_MODEL", DEFAULT_MODEL)
    client = OpenAI(api_key=api_key)

    api_messages = [{"role": "system", "content": build_system_prompt(page)}]
    api_messages.extend(messages)

    stream = client.chat.completions.create(
        model=model,
        messages=api_messages,
        max_tokens=MAX_ANSWER_TOKENS,
        temperature=0.3,
        stream=True,
        stream_options={"include_usage": True},
    )

    chunks: list[str] = []
    tokens_in = 0
    tokens_out = 0
    try:
        for event in stream:
            usage = getattr(event, "usage", None)
            if usage:
                tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
                tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)
            if event.choices:
                delta = event.choices[0].delta.content or ""
                if delta:
                    chunks.append(delta)
                    yield delta
    finally:
        # Записываем даже оборванный диалог: токены уже потрачены.
        try:
            question = next(
                (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
            )
            _record_exchange(
                tenant_id=tenant_id, user_id=user_id, shop_id=shop_id, page=page,
                question=question, answer="".join(chunks), model=model,
                tokens_in=tokens_in, tokens_out=tokens_out,
            )
        except Exception:  # noqa: BLE001
            current_app.logger.exception("Assistant: failed to record usage/log")
