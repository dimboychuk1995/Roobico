"""Payroll в General Revenue = payroll в Timecard / Salary (один сервис).

Регрессия: General Revenue выбрасывал КАЖДОГО uAttend-сотрудника с матчем на
внутреннего пользователя — у магазина, где все механики заведены и в Roobico
(pay type Hourly), и в uAttend, «Hourly (uAttend)» было $0, хотя Timecard
показывал их зарплату. Плюс правила общего сервиса: salary-пользователь без
недельной ставки не прячет почасовую оплату двойника, ошибки uAttend видны в
отчёте, зарплата считается от точных (не округлённых) часов.
"""
from __future__ import annotations

import re

import pytest
from bson import ObjectId
from pymongo import MongoClient

from werkzeug.security import generate_password_hash

from tests.conftest import SHOP_A_DB, TENANT_A_DB, TEST_MASTER, TEST_MONGO_URI, login

# Неделя, которой нет в других модулях: кэш punches и матчей ключуется датами.
WEEK = "date_preset=custom&date_from=2020-06-01&date_to=2020-06-07"
GR_URL = "/reports/api/standard/general_revenue?" + WEEK
TIMECARD_URL = "/reports/timecard-salary?" + WEEK
PUNCH_CACHE_KEY = "2020-06-01|2020-06-07|811,812,813,814,815,816"


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def payroll_seed(seed, mongo, monkeypatch):
    """Люди с уникальными именами (матчер ищет по всем users тенанта):

      * Hryhir Pohodynnyk  — механик, pay type Hourly, uAttend 811 ($30/hr);
      * Zenon Stavkovyi    — менеджер, Salary $1000/wk, uAttend 812 (без ставки);
      * Lubomyr Bezstavky  — механик без pay_type и без суммы (форма так
                             создаёт пользователя), uAttend 813 ($25/hr);
      * Orest Dvoistyi     — механик, Salary $33/wk (ставку вписали не туда),
                             uAttend 814 ($33/hr);
      * Vilnyi Tabelnyk    — только uAttend 815 ($20/hr), аккаунта нет;
      * Bezstavkovyi Robitnyk — только uAttend 816, часы есть, ставки нет.
    """
    import app.utils.employee_matcher as employee_matcher
    import app.utils.integrations.storage as storage
    import app.utils.integrations.uattend_match as uattend_match

    master = mongo[TEST_MASTER]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    tenant_id = seed["tenant_a"]["_id"]

    def _user(first, last, role, **pay):
        return {
            "_id": ObjectId(), "email": f"{first}.{last}@payroll.test".lower(),
            "first_name": first, "last_name": last, "is_active": True,
            "tenant_id": tenant_id, "shop_ids": [str(shop_a["_id"])],
            "role": role, **pay,
        }

    users = {
        "hourly": _user("Hryhir", "Pohodynnyk", "mechanic", pay_type="hourly"),
        "salary": _user("Zenon", "Stavkovyi", "manager", pay_type="salary", salary_amount=1000.0),
        "no_pay": _user("Lubomyr", "Bezstavky", "mechanic"),
        "conflict": _user("Orest", "Dvoistyi", "mechanic", pay_type="salary", salary_amount=33.0),
    }
    master.users.insert_many(list(users.values()))

    def _emp(uid, first, last, rate):
        return {
            "shop_id": shop_a["_id"], "uattend_user_id": uid, "first_name": first,
            "last_name": last, "email": "", "is_active": True, "selected": True,
            "hourly_rate": rate,
        }

    shop_db.integrations.insert_one({"shop_id": shop_a["_id"], "provider": "uattend", "enabled": True})
    shop_db.uattend_employees.insert_many([
        _emp(811, "Hryhir", "Pohodynnyk", 30.0),
        _emp(812, "Zenon", "Stavkovyi", None),
        _emp(813, "Lubomyr", "Bezstavky", 25.0),
        _emp(814, "Orest", "Dvoistyi", 33.0),
        _emp(815, "Vilnyi", "Tabelnyk", 20.0),
        _emp(816, "Bezstavkovyi", "Robitnyk", None),
    ])

    monkeypatch.setattr(storage, "get_decrypted_api_key", lambda *a, **k: "test-key")
    # Без OpenAI: точное совпадение имён.
    monkeypatch.setattr(uattend_match, "match_employees", employee_matcher._deterministic_match)

    yield {"shop_id": shop_a["_id"], "users": users, "shop_db": shop_db}

    master.users.delete_many({"_id": {"$in": [u["_id"] for u in users.values()]}})
    shop_db.integrations.delete_many({"shop_id": shop_a["_id"], "provider": "uattend"})
    shop_db.uattend_employees.delete_many({"shop_id": shop_a["_id"]})
    shop_db.uattend_match_cache.delete_many({"shop_id": shop_a["_id"]})
    shop_db.uattend_punch_cache.delete_many({"shop_id": shop_a["_id"]})


PUNCHES = {"Punches": [
    {"UserId": 811, "Hours": 9.5},
    {"UserId": 812, "Hours": 8.0},
    {"UserId": 813, "Hours": 8.0},
    {"UserId": 814, "Hours": 40.0},
    {"UserId": 815, "Hours": 4.0},
    {"UserId": 816, "Hours": 6.0},
]}


def _fake_client(monkeypatch, response=None, error=None, calls=None):
    import app.utils.integrations.uattend_client as uattend_client

    class FakeClient:
        def __init__(self, api_key):
            pass

        def get_punches(self, date_from, date_to, user_ids=None):
            if calls is not None:
                calls.append((date_from, date_to))
            if error is not None:
                raise uattend_client.UAttendError(error)
            return PUNCHES if response is None else response

    monkeypatch.setattr(uattend_client, "UAttendClient", FakeClient)


def _general_revenue(client):
    resp = client.get(GR_URL)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    body = resp.get_json()
    assert body["ok"] is True
    return body["report_data"]


def _amounts(report):
    return {r["category"]: r["amount"] for r in report["rows"] if r.get("row_type") != "section"}


def _row(report, prefix):
    return next(r for r in report["rows"] if str(r.get("category") or "").startswith(prefix))


def _timecard_subtotals(html):
    salary = float(re.search(r"Salary subtotal: \$([\d.]+)", html).group(1))
    hourly = float(re.search(r"Hourly subtotal: \$([\d.]+)", html).group(1))
    return salary, hourly


def test_general_revenue_counts_uattend_pay_of_matched_hourly_users(client, payroll_seed, monkeypatch):
    _fake_client(monkeypatch)
    login(client)
    report = _general_revenue(client)
    amounts = _amounts(report)

    # Почасовой механик с матчем на аккаунт — раньше пропадал целиком.
    assert amounts["Hryhir Pohodynnyk — 9.50h x $30.00/hr"] == 285.0
    # Несматченный сотрудник — как и раньше.
    assert amounts["Vilnyi Tabelnyk — 4.00h x $20.00/hr"] == 80.0
    # Salary-менеджер: одна неделя; его uAttend-двойник отдельно не считается.
    assert amounts["Zenon Stavkovyi — salary $1,000.00/wk"] == 1000.0
    assert not any(c.startswith("Zenon Stavkovyi — 8") for c in amounts)

    # 285 (Hryhir) + 200 (Lubomyr) + 80 (Vilnyi)
    assert amounts["Hourly (uAttend)"] == 565.0
    assert report["summary"]["labor_cost"] == amounts["Total payroll"]
    assert amounts["- Payroll (total)"] == amounts["Total payroll"]
    assert "payroll_warning" not in report["summary"]


def test_general_revenue_payroll_equals_timecard(client, payroll_seed, monkeypatch):
    _fake_client(monkeypatch)
    login(client)

    html = client.get(TIMECARD_URL).get_data(as_text=True)
    salary, hourly = _timecard_subtotals(html)
    amounts = _amounts(_general_revenue(client))

    assert amounts["Salaries (x 1 weeks)"] == salary
    assert amounts["Hourly (uAttend)"] == hourly
    assert amounts["Total payroll"] == round(salary + hourly, 2)
    assert hourly == 565.0


def test_salary_user_without_amount_does_not_hide_hourly_pay(client, payroll_seed, monkeypatch):
    """pay_type не задан → по умолчанию salary, но суммы нет: раньше такой
    пользователь «поглощал» uAttend-двойника, и оплата не считалась нигде."""
    _fake_client(monkeypatch)
    login(client)

    amounts = _amounts(_general_revenue(client))
    assert amounts["Lubomyr Bezstavky — 8.00h x $25.00/hr"] == 200.0

    html = client.get(TIMECARD_URL).get_data(as_text=True)
    assert "↔ Internal: Lubomyr Bezstavky" in html
    assert "200.00" in html


def test_salary_user_with_uattend_rate_is_paid_salary_and_flagged(client, payroll_seed, monkeypatch):
    """Salary $33/wk + ставка uAttend $33/hr: платится salary, конфликт виден."""
    _fake_client(monkeypatch)
    login(client)

    report = _general_revenue(client)
    row = _row(report, "Orest Dvoistyi — salary $33.00/wk")
    assert row["amount"] == 33.0
    assert "Pay type is Salary" in row["desc"] and "$33.00/hr" in row["desc"]
    assert not any(c.startswith("Orest Dvoistyi — 40") for c in _amounts(report))

    html = client.get(TIMECARD_URL).get_data(as_text=True)
    assert "uAttend rate $33.00/hr is not used" in html


def test_hours_without_rate_are_shown_as_zero_pay(client, payroll_seed, monkeypatch):
    _fake_client(monkeypatch)
    login(client)

    row = _row(_general_revenue(client), "Bezstavkovyi Robitnyk")
    assert row["category"] == "Bezstavkovyi Robitnyk — 6.00h, hourly rate not set"
    assert row["amount"] == 0.0
    assert "no hourly rate" in row["desc"]


def test_uattend_error_is_reported_not_silently_zero(client, payroll_seed, monkeypatch):
    _fake_client(monkeypatch, error="uAttend returned HTTP 429 after 4 attempts.")
    login(client)

    report = _general_revenue(client)
    warning = report["summary"]["payroll_warning"]
    assert "uAttend hours could not be loaded" in warning
    assert "HTTP 429" in warning
    assert "only salaries are counted" in warning

    amounts = _amounts(report)
    assert amounts["Hourly (uAttend)"] == 0.0
    assert amounts["Zenon Stavkovyi — salary $1,000.00/wk"] == 1000.0
    payroll_section = next(
        r for r in report["rows"]
        if r.get("row_type") == "section" and r["category"] == "Money Out — Payroll"
    )
    assert "WARNING" in payroll_section["desc"]
    # PDF-движок (cp1252): предупреждение — только ASCII.
    assert warning.isascii()


def test_all_time_explains_why_payroll_is_zero(client, payroll_seed, monkeypatch):
    _fake_client(monkeypatch)
    login(client)

    resp = client.get("/reports/api/standard/general_revenue?date_preset=all_time")
    report = resp.get_json()["report_data"]
    assert report["summary"]["labor_cost"] == 0.0
    assert "needs both a start and an end date" in report["summary"]["payroll_warning"]
    sections = [r["category"] for r in report["rows"] if r.get("row_type") == "section"]
    assert "Money Out — Payroll" not in sections

    # Полуоткрытый Custom (только From): та же причина, текст её называет.
    resp = client.get(
        "/reports/api/standard/general_revenue?date_preset=custom&date_from=2020-06-01"
    )
    warning = resp.get_json()["report_data"]["summary"]["payroll_warning"]
    assert "open-ended Custom range" in warning


def test_uattend_error_text_is_made_pdf_safe(client, payroll_seed, monkeypatch):
    """Настоящий текст клиента (HTTP 403) содержит длинное тире, сетевые
    ошибки — repr объектов: в предупреждение идёт ASCII разумной длины."""
    import app.utils.integrations.uattend_client as uattend_client

    class FakeClient:
        def __init__(self, api_key):
            pass

        def get_punches(self, date_from, date_to, user_ids=None):
            raise uattend_client.UAttendForbiddenError(
                "uAttend rejected the request (HTTP 403). "
                "Likely an IP allowlist on the API gateway — contact uAttend "
                "support with your server's outbound IP. " + "x" * 400,
                status=403,
            )

    monkeypatch.setattr(uattend_client, "UAttendClient", FakeClient)
    login(client)

    warning = _general_revenue(client)["summary"]["payroll_warning"]
    assert warning.isascii()
    assert "HTTP 403" in warning and "API gateway - contact uAttend" in warning
    assert len(warning) < 420


def test_unreadable_uattend_report_keeps_salaries(client, payroll_seed, monkeypatch):
    """Неожиданная форма ответа uAttend не обнуляет весь payroll: salary
    считается, про часы — предупреждение (Timecard не падает с 500)."""
    import app.utils.integrations.uattend_hours as uattend_hours

    def broken(report):
        raise ValueError("unexpected shape")

    _fake_client(monkeypatch)
    monkeypatch.setattr(uattend_hours, "aggregate_hours_by_user", broken)
    login(client)

    report = _general_revenue(client)
    assert "Could not read the uAttend punch report" in report["summary"]["payroll_warning"]
    assert _amounts(report)["Zenon Stavkovyi — salary $1,000.00/wk"] == 1000.0

    resp = client.get(TIMECARD_URL)
    assert resp.status_code == 200
    assert "Could not read the uAttend punch report" in resp.get_data(as_text=True)


def test_employee_pay_lines_need_the_payroll_permission(client, payroll_seed, mongo, seed, monkeypatch):
    """General Revenue открыт всем с reports.view_financial, но кто сколько
    получает — данные Timecard / Salary: строки по сотрудникам видит только
    тот, у кого есть reports.view_payroll. Итоги payroll остаются."""
    _fake_client(monkeypatch)
    master = mongo[TEST_MASTER]
    tdb = mongo[TENANT_A_DB]
    shop_a = seed["shop_a"]
    role_key = "pytest_fin_no_payroll"
    tdb.roles.update_one(
        {"key": role_key},
        {"$set": {"key": role_key, "name": "Fin no payroll", "is_system": False,
                  "permissions": ["reports.view", "reports.view_financial"]}},
        upsert=True,
    )
    user_id = ObjectId()
    master.users.insert_one({
        "_id": user_id, "email": "fin.viewer@payroll.test",
        "password_hash": generate_password_hash("pw-12345678"),
        "first_name": "Fin", "last_name": "Viewer", "name": "Fin Viewer",
        "is_active": True, "tenant_id": seed["tenant_a"]["_id"],
        "shop_ids": [str(shop_a["_id"])], "role": role_key,
    })
    try:
        login(client, email="fin.viewer@payroll.test", password="pw-12345678")
        assert client.get(TIMECARD_URL).status_code == 302  # Timecard закрыт

        report = _general_revenue(client)
        amounts = _amounts(report)
        # Итоги — те же, что у владельца.
        assert amounts["Hourly (uAttend)"] == 565.0
        assert amounts["- Payroll (total)"] == amounts["Total payroll"]
        # Ни имён, ни ставок, ни часов сотрудников.
        text = " ".join(str(r.get("category")) + " " + str(r.get("desc")) for r in report["rows"])
        for leaked in ("Hryhir", "Zenon", "Orest", "Vilnyi", "/hr", "/wk"):
            assert leaked not in text, leaked
        section = next(
            r for r in report["rows"]
            if r.get("row_type") == "section" and r["category"] == "Money Out — Payroll"
        )
        assert "View Timecard / Salary report" in section["desc"]
    finally:
        master.users.delete_one({"_id": user_id})
        tdb.roles.delete_one({"key": role_key})


def test_general_revenue_reuses_hours_loaded_by_timecard(client, payroll_seed, monkeypatch):
    """Timecard всегда ходит в uAttend и прогревает кэш; General Revenue в
    пределах TTL читает кэш — те же часы, без второго похода в API."""
    calls = []
    _fake_client(monkeypatch, calls=calls)
    login(client)

    client.get(TIMECARD_URL)
    client.get(TIMECARD_URL)
    assert len(calls) == 2  # Timecard не кэшируется

    _fake_client(monkeypatch, error="must not be called", calls=calls)
    report = _general_revenue(client)
    assert "payroll_warning" not in report["summary"]
    assert _amounts(report)["Hourly (uAttend)"] == 565.0
    assert len(calls) == 2


def test_pay_is_computed_from_exact_hours(client, payroll_seed, monkeypatch):
    """8:20 = 8.3333 ч; × $30 = $250.00, а не $249.90 от округлённых 8.33."""
    _fake_client(monkeypatch, response={"Body": {"PunchReportLineItems": [
        {"UserId": 811, "Tot": "8:20"},
    ]}})
    login(client)

    html = client.get(TIMECARD_URL).get_data(as_text=True)
    assert "250.00" in html and "249.90" not in html

    amounts = _amounts(_general_revenue(client))
    assert amounts["Hryhir Pohodynnyk — 8.33h x $30.00/hr"] == 250.0

    cached = payroll_seed["shop_db"].uattend_punch_cache.find_one(
        {"shop_id": payroll_seed["shop_id"], "key": PUNCH_CACHE_KEY}
    )
    assert cached["by_uid"]["811"] == 8.33
    assert abs(cached["by_uid_exact"]["811"] - 8.3333) < 0.001


def test_payroll_service_never_reads_job_timers():
    """Зарплата — salary и punches uAttend; тайм-логи работ в неё не входят."""
    import inspect

    import app.blueprints.reports.services.payroll as payroll

    source = inspect.getsource(payroll)
    assert "wo_time_logs" not in source and "summarize_mechanic_hours" not in source
