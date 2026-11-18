"""Timecard / Salary: uAttend-сотрудник, заматченный на ПОЧАСОВОГО
внутреннего пользователя (механика), не должен пропадать из отчёта —
скрывается только двойник salary-пользователя (он влит в его строку)."""
from __future__ import annotations

import re

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, login


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def timecard_seed(seed, mongo):
    """Почасовой механик + salary-менеджер, оба с двойниками в uAttend,
    плюс несматченный uAttend-сотрудник. Имена уникальные — матчер ищет
    по всем users тенанта, тёзки из других модулей перехватят матч."""
    master = mongo["roobico_test_master"]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]

    taras_id, petro_id = ObjectId(), ObjectId()
    master.users.insert_many([
        {
            "_id": taras_id,
            "email": "taras.hodynnyk@test.local",
            "first_name": "Taras",
            "last_name": "Hodynnyk",
            "is_active": True,
            "tenant_id": seed["tenant_a"]["_id"],
            "shop_ids": [str(shop_a["_id"])],
            "role": "mechanic",
            "pay_type": "hourly",
        },
        {
            "_id": petro_id,
            "email": "petro.zarplatnyk@test.local",
            "first_name": "Petro",
            "last_name": "Zarplatnyk",
            "is_active": True,
            "tenant_id": seed["tenant_a"]["_id"],
            "shop_ids": [str(shop_a["_id"])],
            "role": "general_manager",
            "pay_type": "salary",
            "salary_amount": 1000.0,
        },
    ])

    shop_db.integrations.insert_one({
        "shop_id": shop_a["_id"], "provider": "uattend", "enabled": True,
    })
    shop_db.uattend_employees.insert_many([
        {"shop_id": shop_a["_id"], "uattend_user_id": 501, "first_name": "Taras",
         "last_name": "Hodynnyk", "email": "", "is_active": True,
         "selected": True, "hourly_rate": 30.0},
        {"shop_id": shop_a["_id"], "uattend_user_id": 502, "first_name": "Petro",
         "last_name": "Zarplatnyk", "email": "", "is_active": True,
         "selected": True, "hourly_rate": None},
        {"shop_id": shop_a["_id"], "uattend_user_id": 503, "first_name": "Free",
         "last_name": "Puncher", "email": "", "is_active": True,
         "selected": True, "hourly_rate": 20.0},
    ])

    yield {"shop_id": shop_a["_id"]}

    master.users.delete_many({"_id": {"$in": [taras_id, petro_id]}})
    shop_db.integrations.delete_many({"shop_id": shop_a["_id"], "provider": "uattend"})
    shop_db.uattend_employees.delete_many({"shop_id": shop_a["_id"]})
    shop_db.uattend_match_cache.delete_many({"shop_id": shop_a["_id"]})
    # Timecard прогревает кэш часов для General Revenue — убираем за собой.
    shop_db.uattend_punch_cache.delete_many({"shop_id": shop_a["_id"]})


def test_matched_hourly_user_stays_in_report(client, timecard_seed, monkeypatch):
    import app.utils.integrations.storage as storage
    import app.utils.integrations.uattend_client as uattend_client

    monkeypatch.setattr(storage, "get_decrypted_api_key", lambda *a, **k: "test-key")

    class FakeClient:
        def __init__(self, api_key):
            pass

        def get_punches(self, date_from, date_to, user_ids=None):
            assert set(user_ids or ()) == {501, 502, 503}
            return {"Punches": [
                {"UserId": 501, "Hours": 9.5},
                {"UserId": 502, "Hours": 8.0},
                {"UserId": 503, "Hours": 4.0},
            ]}

    monkeypatch.setattr(uattend_client, "UAttendClient", FakeClient)

    login(client)
    resp = client.get(
        "/reports/timecard-salary"
        "?date_preset=custom&date_from=2021-04-05&date_to=2021-04-11"
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Механик с матчем в uAttend — в отчёте, со своими часами и ролью
    assert "Taras Hodynnyk" in html
    assert "9.50" in html
    # Несматченный uAttend-сотрудник — как раньше
    assert "Free Puncher" in html
    # Salary-пользователь один: его uAttend-двойник влит в строку,
    # отдельной строки «uAttend employee» для него нет
    assert html.count("Petro Zarplatnyk") >= 1
    assert html.count("uAttend employee") == 1  # только Free Puncher
    # Итог по почасовым: 9.5×30 + 4×20 = 365
    assert "365.00" in html
    # Ответ без дат — разбивки по дням нет: ни кнопок, ни скрытых строк.
    assert "data-row-toggle" not in html and "table-detail-row" not in html


# ── часы по дням (раскрытие строки) ──────────────────────────────────

def _line(uid, day, time_in, time_out, tot, **extra):
    """Строка /reports/punch в том виде, в каком её отдаёт uAttend."""
    item = {
        "Id": "1", "UserId": str(uid), "PaycodeId": 1, "DepartmentName": "Mechanics",
        "PunchDate": f"{day}T00:00:00", "InDate": f"{day}T00:00:00", "OutDate": f"{day}T00:00:00",
        "InTime": time_in, "OutTime": time_out, "Tot": tot,
        "InPunchMethod": "Time Clock", "OutPunchMethod": "Time Clock",
    }
    item.update(extra)
    return item


REAL_SHAPE_REPORT = {"Product": "uAttend", "Body": {"PunchReportLineItems": [
    # Taras: понедельник двумя парами (вторая — раньше по времени, идёт первой
    # в ответе не по порядку), вторник с ручной правкой, среда — ещё на смене.
    _line(501, "2021-04-05", "13:00:00", "17:30:00", "4:30"),
    _line(501, "2021-04-05", "08:00:00", "12:00:00", "4:00"),
    _line(501, "2021-04-06", "09:15:00", "12:45:00", "3:30", OutPunchMethod="Manual Edit"),
    _line(501, "2021-04-07", "08:05:00", None, "0:00"),
    # Petro (salary): один день — виден в его salary-строке.
    _line(502, "2021-04-05", "09:00:00", "17:00:00", "8:00", DepartmentName="Office"),
    # Строка без даты: в итог сотрудника идёт, в дни — нет.
    {"UserId": "503", "Tot": "2:00"},
    _line(503, "2021-04-08", "10:00:00", "12:00:00", "2:00"),
]}}


def test_days_by_user_from_the_real_punch_report_shape():
    from app.utils.integrations.uattend_hours import aggregate_days_by_user, aggregate_hours_by_user

    days = aggregate_days_by_user(REAL_SHAPE_REPORT)
    assert set(days) == {501, 502, 503}

    taras = days[501]
    assert [d["date"] for d in taras] == ["2021-04-05", "2021-04-06", "2021-04-07"]
    monday = taras[0]
    assert monday["hours"] == pytest.approx(8.5)
    assert [(p["time_in"], p["time_out"]) for p in monday["punches"]] == [("08:00", "12:00"), ("13:00", "17:30")]
    assert monday["punches"][0]["department"] == "Mechanics" and monday["punches"][0]["edited"] is False
    edited = taras[1]["punches"][0]
    assert edited["edited"] is True and edited["method"] == "Time Clock / Manual Edit"
    still_in = taras[2]
    assert still_in["hours"] == 0 and still_in["punches"] == [{
        "hours": 0.0, "time_in": "08:05", "time_out": "", "department": "Mechanics",
        "method": "Time Clock", "edited": False,
    }]

    # Сумма по дням = итог сотрудника; строка без даты остаётся только в итоге.
    totals = aggregate_hours_by_user(REAL_SHAPE_REPORT)
    assert sum(d["hours"] for d in taras) == pytest.approx(totals[501]) == pytest.approx(12.0)
    assert [d["date"] for d in days[503]] == ["2021-04-08"]
    assert totals[503] == pytest.approx(4.0)

    # Другие формы ответа и мусор не роняют разбор.
    assert aggregate_days_by_user({"Punches": [{"UserId": 7, "Hours": 3}]}) == {}
    assert aggregate_days_by_user({"Punches": [
        {"UserId": 7, "PunchIn": "2021-04-05T08:00:00", "PunchOut": "2021-04-05T11:30:00"},
    ]})[7][0]["punches"][0]["time_out"] == "11:30"
    assert aggregate_days_by_user({"Users": [{"UserId": 9, "Workdays": [
        {"Date": "04/05/2021", "PunchPairs": [{"PunchIn": "2021-04-05T07:00:00", "PunchOut": "2021-04-05T09:00:00"}]},
    ]}]})[9][0]["date"] == "2021-04-05"
    for junk in (None, [], "x", {"Body": None}, {"Body": {"PunchReportLineItems": [None, "x", {"UserId": "abc"}]}}):
        assert aggregate_days_by_user(junk) == {}


def test_clock_formats():
    from app.utils.integrations.uattend_hours import _clock

    assert _clock("11:01:00") == "11:01"
    assert _clock("7:05") == "07:05"
    assert _clock("1:05 PM") == "13:05" and _clock("12:10 AM") == "00:10" and _clock("12:10 PM") == "12:10"
    assert _clock("2021-04-05T16:45:00") == "16:45"
    for bad in (None, "", "soon", "25:00", "10:99"):
        assert _clock(bad) == ""


def test_timecard_rows_expand_by_day_and_stay_collapsed_by_default(client, timecard_seed, monkeypatch, mongo):
    import app.utils.integrations.storage as storage
    import app.utils.integrations.uattend_client as uattend_client

    monkeypatch.setattr(storage, "get_decrypted_api_key", lambda *a, **k: "test-key")

    class FakeClient:
        def __init__(self, api_key):
            pass

        def get_punches(self, date_from, date_to, user_ids=None):
            return REAL_SHAPE_REPORT

    monkeypatch.setattr(uattend_client, "UAttendClient", FakeClient)

    login(client)
    resp = client.get(
        "/reports/timecard-salary"
        "?date_preset=custom&date_from=2021-04-05&date_to=2021-04-11"
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # У каждого из трёх сотрудников — кнопка раскрытия и строка-деталь,
    # все строки-детали скрыты, все кнопки «свёрнуты».
    details = re.findall(r'<tr class="([^"]*)" id="timecardDays\d+">', html)
    assert len(details) == 3 and all(cls.split() == ["table-detail-row", "d-none"] for cls in details)
    toggles = re.findall(r'<button type="button" class="row-toggle" data-row-toggle="#(timecardDays\d+)"\s+aria-expanded="(\w+)"', html)
    assert len(toggles) == 3 and {state for _, state in toggles} == {"false"}
    assert {target for target, _ in toggles} == set(re.findall(r'id="(timecardDays\d+)"', html))
    assert 'data-row-toggle-all="#timecardTable"' in html and "Show daily hours" in html

    # Содержимое разбивки: день недели + дата, пары вход–выход, часы дня.
    assert "Mon 04/05/2021" in html and "Tue 04/06/2021" in html
    assert re.search(r"08:00 – 12:00\s*</span>", html) and re.search(r"13:00 – 17:30\s*</span>", html)
    assert "08:05 – on the clock" in html                      # ещё не отметил выход
    assert "Time Clock / Manual Edit" in html and "bi-pencil" in html  # ручная правка в uAttend
    assert "Hours uAttend returned without a date" in html     # 2 ч Free Puncher без даты

    # Итоги строк не изменились: 12 ч × $30 + 4 ч × $20 = $440.
    assert "440.00" in html

    # Разбивка легла и в кэш часов (General Revenue её не читает, но формат один).
    cached = mongo[SHOP_A_DB].uattend_punch_cache.find_one({"shop_id": timecard_seed["shop_id"]})
    assert [d["date"] for d in cached["days_by_uid"]["501"]] == ["2021-04-05", "2021-04-06", "2021-04-07"]


def test_payroll_day_rows_and_undated_remainder():
    from app.blueprints.reports.services.payroll import _day_rows

    raw = [
        {"date": "2021-04-05", "hours": 8.5, "punches": [
            {"time_in": "08:00", "time_out": "12:00", "hours": 4.0, "department": "Mechanics", "method": "Time Clock", "edited": False},
            "junk",
        ]},
        {"hours": 3}, "junk",
    ]
    days, undated = _day_rows(raw, 10.5)
    assert [(d["date"], d["label"], d["hours"]) for d in days] == [("2021-04-05", "Mon 04/05/2021", 8.5)]
    assert len(days[0]["punches"]) == 1 and undated == pytest.approx(2.0)
    assert _day_rows(raw[:1], 8.5) == (days, 0.0)        # всё по дням — остатка нет
    assert _day_rows(None, 7.0) == ([], 0.0)             # нет разбивки — нет и «без даты»
    assert _day_rows([], None) == ([], 0.0)


def test_employee_still_on_the_clock_is_not_reported_as_no_punches(client, timecard_seed, monkeypatch):
    """Единственный панч периода — вход без выхода: часов ещё нет, но строка
    раскрывается и не говорит «No punches in this period»."""
    import app.utils.integrations.storage as storage
    import app.utils.integrations.uattend_client as uattend_client

    monkeypatch.setattr(storage, "get_decrypted_api_key", lambda *a, **k: "test-key")

    class FakeClient:
        def __init__(self, api_key):
            pass

        def get_punches(self, date_from, date_to, user_ids=None):
            return {"Body": {"PunchReportLineItems": [_line(503, "2021-04-07", "08:05:00", None, "0:00")]}}

    monkeypatch.setattr(uattend_client, "UAttendClient", FakeClient)

    login(client)
    html = client.get(
        "/reports/timecard-salary?date_preset=custom&date_from=2021-04-05&date_to=2021-04-11"
    ).get_data(as_text=True)
    assert "On the clock — no completed punches in this period yet." in html
    assert "08:05 – on the clock" in html
    assert len(re.findall(r'id="timecardDays\d+"', html)) == 1
    # Остальные двое без панчей — прежняя подпись.
    assert html.count("No punches in this period.") == 1          # Taras: ставка есть, панчей нет
    assert "Hourly rate not set · no punches in this period." not in html  # Petro — salary-строка
