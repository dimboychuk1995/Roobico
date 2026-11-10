# -*- coding: utf-8 -*-
"""
Справка (app/help/*.md) и схема для AI-помощника (db_schema.md) против кода.

Справка — это и база знаний AI-помощника: неверная строка в ней становится
неверным ответом. Тесты закрепляют то, что ревью D9 нашло расходящимся с
кодом, и тексты сообщений, которые справка цитирует:

- D9-02: время таймеров на удалённых работах НЕ идёт в зарплату
  (Timecard / Salary — оклад и панчи uAttend);
- D9-03: правка часов механиков вне режима правки перезагружает страницу,
  в режиме правки — нет (часы видны после Save);
- D9-05: имена, переименованные до появления синка снимков, чистит
  разовый прогон администратора — справка не обещает большего;
- цитаты сообщений (page_outdated, unit_customer_mismatch, отказы таймера,
  тосты приложения…) дословно совпадают с кодом;
- db_schema.md: labors_rev, client_key / minted, wo_edit_views (TTL как в
  extensions), а учётки порталов вендоров не названы.

Каталоги можно подменить переменными HELP_DOCS_DIR / DB_SCHEMA_PATH (так
проверяется, что тесты падают на справке до исправления).
"""
import os
import re

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELP_DIR = os.environ.get("HELP_DOCS_DIR") or os.path.join(_REPO, "app", "help")
DB_SCHEMA_PATH = os.environ.get("DB_SCHEMA_PATH") or os.path.join(
    _REPO, "app", "blueprints", "assistant", "services", "db_schema.md"
)
_CODE_DIRS = (os.path.join(_REPO, "app"), os.path.join(_REPO, "mobile", "src"))
_CODE_EXTS = (".py", ".js", ".ts", ".tsx", ".html")
_SKIP_DIRS = {"node_modules", "__pycache__", "help"}


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _flat(text: str) -> str:
    """Переносы строк markdown → пробелы: цитата может разорваться переносом."""
    return " ".join(text.split())


def _help(name: str) -> str:
    return _flat(_read(os.path.join(HELP_DIR, name)))


def _paragraph(name: str, anchor: str) -> str:
    """Абзац справки (между пустыми строками), в котором есть anchor."""
    for para in re.split(r"\n\s*\n", _read(os.path.join(HELP_DIR, name))):
        if anchor in para:
            return _flat(para)
    raise AssertionError(f"{name}: no paragraph with {anchor!r}")


@pytest.fixture(scope="module")
def code_text() -> str:
    parts = []
    for base in _CODE_DIRS:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                if name.endswith(_CODE_EXTS):
                    try:
                        parts.append(_read(os.path.join(dirpath, name)))
                    except (OSError, UnicodeDecodeError):
                        continue
    return "\n".join(parts)


def _function_source(path: str, name: str) -> str:
    """Тело функции модуля верхнего уровня: от `def name(` до следующего
    объявления с нулевым отступом."""
    src = _read(path).replace("\r\n", "\n")
    start = src.index(f"\ndef {name}(")
    nxt = re.search(r"\n(?:def |class |@)", src[start + 1:])
    return src[start: start + 1 + nxt.start()] if nxt else src[start:]


# ── D9-02: таймеры не платят зарплату ────────────────────────────────

WRONG_PAYROLL_CLAIMS = (
    "still counts in payroll",
    "still count in payroll",
    "payroll/Timecard reports",
    "payroll reports recalculate",
)


def test_help_never_says_job_timer_hours_feed_payroll():
    for name in sorted(os.listdir(HELP_DIR)):
        if not name.endswith(".md"):
            continue
        text = _help(name).lower()
        for claim in WRONG_PAYROLL_CLAIMS:
            assert claim.lower() not in text, f"{name}: {claim!r}"

    # Где время на удалённой работе остаётся и откуда на самом деле зарплата.
    para = _paragraph("work_orders.md", "**Time on removed jobs is not lost.**")
    assert "Mechanic Hours" in para and "uAttend" in para and "Timecard / Salary" in para
    jd = _paragraph("recipes.md", "**A job disappeared — where did it go?**")
    assert "uAttend" in jd and "payroll" not in jd.lower()
    hours_fix = _paragraph("recipes.md", "**Mechanic forgot to stop the timer / hours look wrong.**")
    assert "uAttend" in hours_fix and "payroll" not in hours_fix.lower()
    assert "**Job timers are not used for pay.**" in _help("reports.md")


def test_payroll_code_really_ignores_job_timers():
    """Справку выше держит код: зарплата не читает тайм-логи WO."""
    routes = os.path.join(_REPO, "app", "blueprints", "reports", "audit", "routes.py")
    for fn in ("_compute_payroll_totals", "timecard_salary_page"):
        body = _function_source(routes, fn)
        assert "wo_time_logs" not in body and "summarize_mechanic_hours" not in body, fn


# ── D9-03: когда страница показывает пересчитанные часы ─────────────

def test_help_mechanic_hours_reload_rules_match_the_page(code_text):
    wo = _help("work_orders.md")
    assert "mechanic-hours fixes never trigger a reload" not in wo
    assert "outside Edit mode the page reloads by itself" in wo
    toast = "Hours updated. Save the work order to see the recalculated labor hours."
    assert toast in wo
    assert toast in code_text


# ── D9-05: переименования до синка снимков ───────────────────────────

def test_settings_help_does_not_promise_old_renames_are_rewritten():
    settings = _help("settings.md")
    assert "Renames made before this feature existed are cleaned up by a one-time run" in settings
    # Само переписывание снимков — только хук правки пользователя и разовый скрипт.
    users_routes = _read(os.path.join(_REPO, "app", "blueprints", "settings", "users", "routes.py"))
    assert "refresh_user_name_snapshots" in users_routes
    assert os.path.isfile(os.path.join(_REPO, "app", "scripts", "backfill_user_name_snapshots.py"))


# ── цитаты сообщений: справка = код ──────────────────────────────────

QUOTED_MESSAGES = (
    # (дословный текст, файл справки)
    ("This page was opened before Roobico was updated. Reload the page and make your change again.",
     "work_orders.md"),
    ("Couldn't confirm what you started from, so nothing was removed. Please review the work order.",
     "work_orders.md"),
    ("You changed the work order while it was being saved. ", "work_orders.md"),
    ("Those changes are still on the page but are not saved yet — save the work order again.",
     "work_orders.md"),
    ("The selected unit belongs to a different customer. Select a unit of this customer.", "work_orders.md"),
    ("This work order was moved to another customer by another user. Reload the page to see the change.",
     "work_orders.md"),
    ("Saved, but stock levels could not be updated. Please check this work order's parts in inventory.",
     "work_orders.md"),
    ("Changes other people made in the meantime were merged in.", "work_orders.md"),
    ("Issue description updated. It will be saved with the work order.", "work_orders.md"),
    ("This job is no longer on the work order. Reload the page.", "work_orders.md"),
    ("This work order is an estimate. Time can be tracked after the office converts it to a work order.",
     "work_orders.md"),
    ("This work order is an estimate. Time can be tracked after the office converts it to a work order.",
     "mechanic_mobile.md"),
    ("This job is no longer on the work order. Refresh the work order and try again.", "mechanic_mobile.md"),
    ("No timer is running.", "mechanic_mobile.md"),
    ("That job was removed by someone else — the part was not added.", "mechanic_mobile.md"),
    ("That job was removed by someone else — the polished text was not applied.", "mechanic_mobile.md"),
    ("This job is no longer on the work order — the approval request was not sent.", "mechanic_mobile.md"),
    ("The server did not answer in time. Check the connection and try again.", "mechanic_mobile.md"),
    ("A manager locked this work order — changes can't be saved right now.", "mechanic_mobile.md"),
    ("A manager locked this work order — your unsaved changes can't be saved.", "mechanic_mobile.md"),
    ("This work order is an estimate — only the office can change it.", "mechanic_mobile.md"),
    ("A manager locked this work order, and it changed since you opened it — the approval request was not "
     "sent. Ask a manager to send it.", "mechanic_mobile.md"),
    ("This work order is locked. Time can't be tracked on it.", "mechanic_mobile.md"),
    ("This page was opened before Roobico was updated", "recipes.md"),
)


@pytest.mark.parametrize("message,help_name", QUOTED_MESSAGES)
def test_quoted_message_matches_the_code(code_text, message, help_name):
    assert message in code_text, f"message is gone from the code — update {help_name}"
    assert _flat(message) in _help(help_name), f"{help_name} does not quote the current message"


# ── db_schema.md для AI-помощника ────────────────────────────────────

def test_db_schema_documents_merge_internals_without_vendor_secrets():
    from app.blueprints.assistant.services import data_tools
    from app.extensions import WO_EDIT_VIEWS_TTL_SECONDS

    schema = _read(DB_SCHEMA_PATH)
    flat = _flat(schema)
    for word in ("`labors_rev: int`", "`client_key`", "`minted: {by, sid, at, ck}`",
                 "work_order_date_input", "shop_supply_pct", "`derived_from`"):
        assert word in flat, word
    assert re.search(r"^### wo_edit_views\s*$", schema, re.M)
    # Справка говорит «не читается и не нужна» — так и есть в реестре.
    assert "wo_edit_views" not in data_tools.COLLECTIONS
    assert WO_EDIT_VIEWS_TTL_SECONDS == 14 * 24 * 3600
    assert "expire automatically after 14 days" in flat
    # Учётки порталов вендоров модели не называем.
    for secret in sorted(data_tools._SECRET_KEYS):
        assert secret not in schema, secret
