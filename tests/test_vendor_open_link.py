"""Глобальный поиск даёт вендору ссылку /vendors/<id>: на вебе она открывает
список, отфильтрованный по имени вендора (раньше был 404)."""
from urllib.parse import parse_qs, urlparse

from bson import ObjectId

from tests.conftest import SHOP_A_DB, login


def test_vendor_link_from_global_search_opens_filtered_list(app, client, seed):
    from app.extensions import get_mongo_client

    with app.app_context():
        coll = get_mongo_client()[SHOP_A_DB].vendors
        vid = coll.insert_one({
            "name": "Linked Vendor LLC", "shop_id": seed["shop_a"]["_id"],
            "tenant_id": seed["tenant_a"]["_id"], "is_active": True,
        }).inserted_id
    try:
        login(client)
        resp = client.get(f"/vendors/{vid}")
        assert resp.status_code == 302
        target = urlparse(resp.headers["Location"])
        assert target.path == "/vendors/"
        assert parse_qs(target.query)["q"] == ["Linked Vendor LLC"]

        # Чужой / несуществующий id — на список с сообщением, не 404/500.
        resp = client.get(f"/vendors/{ObjectId()}")
        assert resp.status_code == 302
        assert urlparse(resp.headers["Location"]).path == "/vendors/"
    finally:
        with app.app_context():
            get_mongo_client()[SHOP_A_DB].vendors.delete_one({"_id": vid})
