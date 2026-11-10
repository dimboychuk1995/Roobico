"""Мобилка: вендор, созданный в приложении, хранит сайт нормализованным (https:// сам)."""
from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, SHOP_A_DB


def _login(client):
    resp = client.post(
        "/api/mobile/login",
        json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
        environ_base={"REMOTE_ADDR": "127.0.0.11"},
    )
    assert resp.status_code == 200
    return {"X-CSRFToken": resp.get_json()["csrf_token"]}


def test_mobile_vendor_create_normalizes_website(app, client):
    from app.extensions import get_mongo_client

    headers = _login(client)
    resp = client.post(
        "/api/mobile/vendors",
        json={"name": "Mobile Site Vendor", "website": "  FleetPride.com/login  "},
        headers=headers,
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    vid = resp.get_json()["id"]
    with app.app_context():
        from bson import ObjectId

        doc = get_mongo_client()[SHOP_A_DB].vendors.find_one({"_id": ObjectId(vid)})
        assert doc["website"] == "https://fleetpride.com/login"
        get_mongo_client()[SHOP_A_DB].vendors.delete_one({"_id": ObjectId(vid)})


def test_mobile_vendor_create_rejects_script_website(client):
    headers = _login(client)
    resp = client.post(
        "/api/mobile/vendors",
        json={"name": "Bad Site Vendor", "website": "javascript:alert(1)"},
        headers=headers,
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "invalid_website"
    assert "fleetpride.com" in data["message"]
