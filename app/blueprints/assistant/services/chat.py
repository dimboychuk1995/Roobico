"""AI-помощник по продукту: отвечает «как сделать X в Roobico».

Знания = markdown-файлы в app/help/. Вся справка целиком уходит в системный
промпт — при текущем размере (< 30 KB) RAG/векторная база не нужны, а
кэширование промпта на стороне провайдера делает повторные запросы дешёвыми.

Данные: помощник имеет read-only инструменты к базе АКТИВНОГО магазина
(db_find/db_count/db_aggregate, см. data_tools) — видит ровно то, что видит
сам пользователь (permissions), деньги закрыты view_costs. Отключается env
ASSISTANT_DATA_TOOLS=0.

Учёт: месячный счётчик токенов на тенанта (master.assistant_usage) — жёсткий
колпак расходов; каждый вопрос-ответ пишется в master.assistant_logs — это и
аудит, и продуктовая аналитика (что пользователям непонятно в интерфейсе).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app

from app.extensions import get_master_db
from . import data_tools

# Справочный чат — недорогая быстрая модель; переопределяется через env.
DEFAULT_MODEL = "gpt-4o-mini"
MAX_ANSWER_TOKENS = 700
# Сколько раундов «модель → инструменты → модель» разрешаем на один вопрос.
MAX_TOOL_ROUNDS = 4
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
{data_section}
Roobico documentation:

{docs}
"""

_DATA_SECTION_TEMPLATE = """\

Live shop data:
- You have read-only database tools (db_find, db_count, db_aggregate) over the
  CURRENT shop's data. Use them whenever the user asks about their own numbers,
  records or history ("how many open work orders", "does customer X owe us",
  "what's in stock") — never guess such answers.
- You may only read these collections: {collections}.
- All data is scoped to the current shop automatically. You cannot write.
- Dates in the database are UTC. Current UTC time: {now_utc}. The shop's
  timezone is {shop_tz} — when the user says "today"/"this week", convert the
  shop-local period to a UTC range in the filter.
- IDs are 24-hex strings; to follow a reference (e.g. work_order.customer_id →
  customers), query the other collection by _id with that string.
- Keep queries cheap: use projections, small limits, and $group for totals
  instead of fetching many documents.
- If a tool returns an error, fix the query and retry; if data access is
  denied, tell the user their role doesn't include that data.

Database schema (collections and their fields):

{schema}
"""


def _load_db_schema() -> str:
    path = Path(__file__).resolve().parent / "db_schema.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return "(schema description is unavailable — rely on db_find samples to discover fields)"


def build_data_section(collections: set[str], shop_tz: str) -> str:
    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return _DATA_SECTION_TEMPLATE.format(
        collections=", ".join(sorted(collections)),
        now_utc=now_utc,
        shop_tz=shop_tz or "unknown",
        schema=_load_db_schema(),
    )


def build_system_prompt(page: str, data_section: str = "") -> str:
    return _SYSTEM_TEMPLATE.format(
        page=page or "unknown", docs=_load_help_docs(), data_section=data_section or ""
    )


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
    tool_events: list[dict] | None = None,
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
        "tool_calls": (tool_events or [])[:20],
        "created_at": now,
    })


# ── Chat ─────────────────────────────────────────────────────────────


def _data_tools_enabled() -> bool:
    return (os.environ.get("ASSISTANT_DATA_TOOLS") or "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _make_client(api_key: str):
    """Шов для тестов: подменяется фейковым клиентом."""
    from openai import OpenAI

    return OpenAI(api_key=api_key)


def _resolve_data_context() -> dict | None:
    """Контекст доступа к данным текущей сессии (или None, если недоступно).

    Требует request context: активный магазин и права берутся из сессии.
    """
    if not _data_tools_enabled():
        return None
    try:
        from app.utils.display_datetime import get_active_shop_timezone_name
        from app.utils.permissions import get_effective_permissions
        from app.utils.tenant import get_shop_db

        shop_db, shop = get_shop_db(get_master_db())
        if shop_db is None or not shop:
            return None
        permissions = get_effective_permissions()
        collections = data_tools.allowed_collections(permissions)
        if not collections:
            return None
        return {
            "shop_db": shop_db,
            "shop_id": shop["_id"],
            "permissions": permissions,
            "collections": collections,
            "shop_tz": get_active_shop_timezone_name(),
        }
    except Exception:  # noqa: BLE001
        # Данные — усиление помощника, не точка отказа: чат должен работать
        # и без них.
        current_app.logger.exception("Assistant: failed to resolve data context")
        return None


def _execute_tool_call(call: dict, ctx: dict, tool_events: list[dict]) -> str:
    """Выполнить один tool call; вернуть JSON-строку для tool-сообщения."""
    started = time.monotonic()
    try:
        arguments = json.loads(call["arguments"] or "{}")
    except ValueError:
        arguments = None

    if not isinstance(arguments, dict):
        result = {"error": "tool arguments must be a JSON object"}
    else:
        result = data_tools.run_tool(
            call["name"], arguments,
            shop_db=ctx["shop_db"], shop_id=ctx["shop_id"],
            permissions=ctx["permissions"],
        )

    event = {
        "tool": call["name"],
        "collection": (arguments or {}).get("collection") if isinstance(arguments, dict) else None,
        "ms": int((time.monotonic() - started) * 1000),
    }
    if "error" in result:
        event["error"] = str(result["error"])[:300]
    elif "docs" in result:
        event["docs"] = len(result["docs"])
    elif "count" in result:
        event["count"] = result["count"]
    tool_events.append(event)

    return data_tools.dump_tool_result(result)


def stream_answer(
    *, tenant_id: str, user_id: str, shop_id: str, page: str, messages: list[dict],
):
    """Стримит текст ответа кусками; в конце записывает usage и лог.

    Внутри — цикл tool use: модель может несколько раундов читать базу шопа
    (db_find/db_count/db_aggregate) перед финальным текстом; пользователю
    стримится только текст.

    messages — уже провалидированные [{role: user|assistant, content: str}].
    Raises AssistantUnavailable, если ключ не сконфигурирован.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise AssistantUnavailable("OPENAI_API_KEY is not configured")

    model = os.environ.get("ASSISTANT_MODEL", DEFAULT_MODEL)
    client = _make_client(api_key)

    data_ctx = _resolve_data_context()
    tools = None
    data_section = ""
    if data_ctx:
        tools = data_tools.openai_tool_specs(data_ctx["collections"])
        data_section = build_data_section(data_ctx["collections"], data_ctx["shop_tz"])

    api_messages = [{"role": "system", "content": build_system_prompt(page, data_section)}]
    api_messages.extend(messages)

    chunks: list[str] = []
    tool_events: list[dict] = []
    tokens_in = 0
    tokens_out = 0
    try:
        rounds = 0
        iterations = 0
        while True:
            # Предохранитель: не больше раундов, чем инструментных + 2
            # (стартовый и финальный без tools), даже если модель зациклится.
            iterations += 1
            if iterations > MAX_TOOL_ROUNDS + 2:
                break
            kwargs = {}
            if tools:
                kwargs["tools"] = tools
            stream = client.chat.completions.create(
                model=model,
                messages=api_messages,
                max_tokens=MAX_ANSWER_TOKENS,
                temperature=0.3,
                stream=True,
                stream_options={"include_usage": True},
                **kwargs,
            )

            finish_reason = None
            calls_acc: dict[int, dict] = {}
            for event in stream:
                usage = getattr(event, "usage", None)
                if usage:
                    tokens_in += int(getattr(usage, "prompt_tokens", 0) or 0)
                    tokens_out += int(getattr(usage, "completion_tokens", 0) or 0)
                if not event.choices:
                    continue
                choice = event.choices[0]
                delta = choice.delta
                text = getattr(delta, "content", None) or ""
                if text:
                    chunks.append(text)
                    yield text
                for tc in getattr(delta, "tool_calls", None) or []:
                    acc = calls_acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        acc["id"] = tc.id
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        if fn.name:
                            acc["name"] = fn.name
                        if fn.arguments:
                            acc["arguments"] += fn.arguments
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

            if finish_reason != "tool_calls" or not calls_acc or not data_ctx:
                break
            if rounds >= MAX_TOOL_ROUNDS:
                # Бюджет инструментов исчерпан: снимаем tools и просим модель
                # ответить текстом по тому, что уже собрано.
                tools = None
                continue
            rounds += 1

            calls = [calls_acc[i] for i in sorted(calls_acc)]
            api_messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["arguments"]},
                    }
                    for c in calls
                ],
            })
            for c in calls:
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": c["id"],
                    "content": _execute_tool_call(c, data_ctx, tool_events),
                })
    finally:
        # Записываем даже оборванный диалог: токены уже потрачены.
        try:
            question = next(
                (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
            )
            _record_exchange(
                tenant_id=tenant_id, user_id=user_id, shop_id=shop_id, page=page,
                question=question, answer="".join(chunks), model=model,
                tokens_in=tokens_in, tokens_out=tokens_out, tool_events=tool_events,
            )
        except Exception:  # noqa: BLE001
            current_app.logger.exception("Assistant: failed to record usage/log")
