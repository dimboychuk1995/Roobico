"""Ночная проверка рекаллов (check_shop_recalls): baseline, дайджесты, идемпотентность.

NHTSA и отправка почты подменяются; проверяем логику снапшотов
`recalls_notified`, журнала `recall_notifications` и группировку писем
по клиенту.
"""
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MONGO_URI


CAMPAIGN_A = "19V066000"
CAMPAIGN_B = "25V999000"


def _recall_row(campaign, component="AIR BAGS"):
    return {
        "NHTSACampaignNumber": campaign,
        "ReportReceivedDate": "07/02/2019",
        "Component": component,
        "Summary": "Summary text.",
        "Consequence": "Consequence text.",
        "Remedy": "Remedy text.",
    }


@pytest.fixture()
def shop_env(seed):
    """Клиент с email и два его юнита одной модели в shop A."""
    mongo = MongoClient(TEST_MONGO_URI)
    shop_db = mongo[SHOP_A_DB]
    shop = seed["shop_a"]
    now = datetime.now(timezone.utc)

    # Джоб обходит ВСЕ активные юниты магазина — прячем юниты, оставшиеся
    # от других тестов, чтобы счётчики и письма были детерминированными.
    shop_db.units.update_many(
        {"is_active": True},
        {"$set": {"is_active": False, "_recall_test_disabled": True}},
    )

    customer = {
        "_id": ObjectId(),
        "shop_id": shop["_id"],
        "company_name": "Acme Trucking",
        "contacts": [{"first_name": "Bob", "last_name": "Lee",
                      "email": "bob@acme.test", "phone": "", "is_main": True}],
        "is_active": True,
        "created_at": now,
    }
    shop_db.customers.insert_one(customer)

    units = []
    for n in ("101", "102"):
        unit = {
            "_id": ObjectId(),
            "shop_id": shop["_id"],
            "customer_id": customer["_id"],
            "unit_number": n,
            "year": 2020,
            "make": "FREIGHTLINER",
            "model": "CASCADIA",
            "is_active": True,
            "created_at": now,
        }
        shop_db.units.insert_one(unit)
        units.append(unit)

    yield {"shop_db": shop_db, "shop": shop, "customer": customer, "units": units}

    shop_db.customers.delete_one({"_id": customer["_id"]})
    shop_db.units.delete_many({"customer_id": customer["_id"]})
    shop_db.recall_notifications.delete_many({"customer_id": customer["_id"]})
    shop_db.units.update_many(
        {"_recall_test_disabled": True},
        {"$set": {"is_active": True}, "$unset": {"_recall_test_disabled": ""}},
    )
    mongo.close()


@pytest.fixture()
def env(app, shop_env, monkeypatch):
    """Подменённые NHTSA и почта + app context."""
    from app.blueprints.work_orders import recalls_api
    from app.blueprints.work_orders.services import recall_check

    state = {"rows": [_recall_row(CAMPAIGN_A)], "sent": [], "fail_send": False}

    monkeypatch.setattr(recall_check, "THROTTLE_SECONDS", 0)
    monkeypatch.setattr(recalls_api, "_fetch_model_catalog", lambda make, year: [])
    monkeypatch.setattr(
        recalls_api, "_fetch_recalls",
        lambda make, model, year: {"Count": len(state["rows"]), "results": state["rows"]},
    )

    def fake_send(to, subject, html_body, **kwargs):
        if state["fail_send"]:
            raise RuntimeError("smtp down")
        state["sent"].append({"to": to, "subject": subject, "html": html_body, **kwargs})

    monkeypatch.setattr(recall_check, "send_email", fake_send)

    with app.app_context():
        yield {**shop_env, "state": state, "check": recall_check.check_shop_recalls}


def _run(env, **kwargs):
    # Кэш новый на каждый вызов: state["rows"] меняется между прогонами.
    return env["check"](env["shop_db"], env["shop"], {}, **kwargs)


def _unit_doc(env, unit):
    return env["shop_db"].units.find_one({"_id": unit["_id"]})


def test_first_run_baselines_without_emails(env):
    stats = _run(env)
    assert stats["units_baselined"] == 2
    assert stats["emails_sent"] == 0
    assert env["state"]["sent"] == []
    for unit in env["units"]:
        assert _unit_doc(env, unit)["recalls_notified"]["campaigns"] == [CAMPAIGN_A]


def test_new_campaign_sends_one_digest_per_customer(env):
    _run(env)  # baseline
    env["state"]["rows"] = [_recall_row(CAMPAIGN_A), _recall_row(CAMPAIGN_B, "BRAKES")]

    stats = _run(env)
    assert stats["new_campaigns"] == 2  # по одной новой кампании на каждый из двух юнитов
    assert stats["emails_sent"] == 1  # но письмо одно — дайджест на клиента
    sent = env["state"]["sent"]
    assert len(sent) == 1
    assert sent[0]["to"] == ["bob@acme.test"]
    assert CAMPAIGN_B in sent[0]["html"]
    assert CAMPAIGN_A not in sent[0]["html"]  # старая кампания в письмо не попадает
    assert "101" in sent[0]["html"] and "102" in sent[0]["html"]

    # Журнал и снапшоты обновлены.
    journal = list(env["shop_db"].recall_notifications.find({"customer_id": env["customer"]["_id"]}))
    assert {(j["campaign_number"], j["status"]) for j in journal} == {(CAMPAIGN_B, "sent")}
    assert len(journal) == 2  # по записи на юнит
    for unit in env["units"]:
        assert _unit_doc(env, unit)["recalls_notified"]["campaigns"] == [CAMPAIGN_A, CAMPAIGN_B]

    # Повторный прогон ничего не шлёт.
    stats = _run(env)
    assert stats["emails_sent"] == 0
    assert len(env["state"]["sent"]) == 1


def test_journal_prevents_resend_even_if_snapshot_regressed(env):
    _run(env)  # baseline
    env["state"]["rows"] = [_recall_row(CAMPAIGN_A), _recall_row(CAMPAIGN_B)]
    _run(env)  # уведомили о CAMPAIGN_B

    # «Откатываем» снапшот одного юнита — журнал всё равно не даст послать дубль.
    env["shop_db"].units.update_one(
        {"_id": env["units"][0]["_id"]},
        {"$set": {"recalls_notified.campaigns": [CAMPAIGN_A]}},
    )
    stats = _run(env)
    assert stats["emails_sent"] == 0
    assert len(env["state"]["sent"]) == 1


def test_send_failure_keeps_snapshot_for_retry(env):
    _run(env)  # baseline
    env["state"]["rows"] = [_recall_row(CAMPAIGN_A), _recall_row(CAMPAIGN_B)]
    env["state"]["fail_send"] = True

    stats = _run(env)
    assert stats["emails_failed"] == 1
    assert stats["emails_sent"] == 0
    # Снапшот не сдвинут, журналу нечего фиксировать.
    for unit in env["units"]:
        assert _unit_doc(env, unit)["recalls_notified"]["campaigns"] == [CAMPAIGN_A]
    assert env["shop_db"].recall_notifications.count_documents(
        {"customer_id": env["customer"]["_id"]}) == 0

    # Почта ожила — кампания уходит при следующем прогоне.
    env["state"]["fail_send"] = False
    stats = _run(env)
    assert stats["emails_sent"] == 1


def test_customer_without_email_is_journaled_and_skipped(env):
    env["shop_db"].customers.update_one(
        {"_id": env["customer"]["_id"]},
        {"$set": {"contacts": [{"first_name": "Bob", "last_name": "Lee",
                                "email": "", "phone": "555", "is_main": True}],
                  "email": None}},
    )
    _run(env)  # baseline
    env["state"]["rows"] = [_recall_row(CAMPAIGN_A), _recall_row(CAMPAIGN_B)]

    stats = _run(env)
    assert stats["customers_no_email"] == 1
    assert stats["emails_sent"] == 0
    journal = list(env["shop_db"].recall_notifications.find({"customer_id": env["customer"]["_id"]}))
    assert journal and all(j["status"] == "skipped_no_email" for j in journal)
    # Снапшот сдвинут — прогоны не будут спотыкаться об эту кампанию.
    for unit in env["units"]:
        assert _unit_doc(env, unit)["recalls_notified"]["campaigns"] == [CAMPAIGN_A, CAMPAIGN_B]


def test_email_override_redirects_all_mail(env):
    _run(env)  # baseline
    env["state"]["rows"] = [_recall_row(CAMPAIGN_A), _recall_row(CAMPAIGN_B)]

    stats = _run(env, email_override="qa@roobico.test")
    assert stats["emails_sent"] == 1
    assert env["state"]["sent"][0]["to"] == ["qa@roobico.test"]


def test_dry_run_writes_and_sends_nothing(env):
    stats = _run(env, dry_run=True)
    assert stats["units_baselined"] == 2
    for unit in env["units"]:
        assert "recalls_notified" not in _unit_doc(env, unit)
    assert env["state"]["sent"] == []

    _run(env)  # baseline
    env["state"]["rows"] = [_recall_row(CAMPAIGN_A), _recall_row(CAMPAIGN_B)]
    stats = _run(env, dry_run=True)
    assert stats["new_campaigns"] == 2
    assert env["state"]["sent"] == []
    assert env["shop_db"].recall_notifications.count_documents(
        {"customer_id": env["customer"]["_id"]}) == 0
    for unit in env["units"]:
        assert _unit_doc(env, unit)["recalls_notified"]["campaigns"] == [CAMPAIGN_A]


def test_units_without_ymm_are_skipped(env):
    env["shop_db"].units.update_one(
        {"_id": env["units"][0]["_id"]},
        {"$set": {"model": None}},
    )
    stats = _run(env)
    assert stats["units_skipped_no_info"] == 1
    assert stats["units_baselined"] == 1
