"""VIN decode API: предупреждения vPIC не должны блокировать расшифровку.

NHTSA vPIC возвращает ненулевой ErrorCode и для предупреждений (3 — «VIN
исправлен в одной позиции», 14 — «нет данных по части символов»), при этом
Make/Model/Year присутствуют. Ошибка — только когда данных нет совсем.
"""
import pytest

from tests.conftest import login

VIN = "7UZFG4020RL006790"


@pytest.fixture()
def logged_in(client):
    assert login(client).status_code == 302
    return client


def _mock_vpic(monkeypatch, row):
    from app.blueprints.work_orders import vin_api

    monkeypatch.setattr(vin_api, "_fetch_vpic", lambda vin: {"Results": [row]})


def test_vin_decoded_with_warnings_returns_ok(logged_in, monkeypatch):
    _mock_vpic(monkeypatch, {
        "ErrorCode": "3,14",
        "ErrorText": "3 - VIN corrected...; 14 - Unable to provide information...",
        "Make": "KAUFMAN TRAILERS GROUP, LLC",
        "Model": "Kaufman Trailers Group, LLC",
        "ModelYear": "2024",
        "VehicleType": "TRAILER",
        "SuggestedVIN": "7UZFX4020RL006790",
    })
    resp = logged_in.get(f"/work_orders/api/vin?vin={VIN}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["make"] == "KAUFMAN TRAILERS GROUP, LLC"
    assert data["year"] == "2024"
    assert data["type"] == "TRAILER"
    assert data["warning"]
    assert data["suggested_vin"] == "7UZFX4020RL006790"


def test_vin_decoded_clean_has_no_warning(logged_in, monkeypatch):
    _mock_vpic(monkeypatch, {
        "ErrorCode": "0",
        "ErrorText": "0 - VIN decoded clean.",
        "Make": "BIG TEX",
        "Model": "Big Tex",
        "ModelYear": "2024",
        "VehicleType": "TRAILER",
        "SuggestedVIN": "",
    })
    resp = logged_in.get(f"/work_orders/api/vin?vin={VIN}")
    data = resp.get_json()
    assert data["ok"] is True
    assert data["warning"] is None
    assert data["suggested_vin"] is None


def test_vin_without_any_data_is_invalid(logged_in, monkeypatch):
    _mock_vpic(monkeypatch, {
        "ErrorCode": "400",
        "ErrorText": "400 - Invalid Characters Present",
        "Make": "",
        "Model": "",
        "ModelYear": "",
        "VehicleType": "",
    })
    resp = logged_in.get(f"/work_orders/api/vin?vin={VIN}")
    data = resp.get_json()
    assert data["ok"] is False
    assert data["error"] == "vin_invalid"
    assert "400" in data["message"]


def test_vin_length_validation(logged_in):
    resp = logged_in.get("/work_orders/api/vin?vin=ABC123")
    data = resp.get_json()
    assert data["ok"] is False
    assert data["error"] == "vin_length"


def test_vin_forbidden_chars(logged_in):
    resp = logged_in.get("/work_orders/api/vin?vin=IOQFG4020RL006790")
    data = resp.get_json()
    assert data["ok"] is False
    assert data["error"] == "vin_invalid_chars"
