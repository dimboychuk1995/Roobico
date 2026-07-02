"""Изоляция тенантов: сессия одного тенанта не должна открывать чужой магазин."""
from flask import session


def test_own_shop_resolves(app, seed):
    from app.extensions import get_master_db
    from app.utils.tenant import get_shop_db

    with app.test_request_context("/"):
        session["tenant_id"] = str(seed["tenant_a"]["_id"])
        session["shop_id"] = str(seed["shop_a"]["_id"])
        db, shop = get_shop_db(get_master_db())
        assert db is not None
        assert shop["_id"] == seed["shop_a"]["_id"]


def test_foreign_shop_denied(app, seed):
    """Тенант A с shop_id магазина тенанта B — доступ закрыт."""
    from app.extensions import get_master_db
    from app.utils.tenant import get_shop_db

    with app.test_request_context("/"):
        session["tenant_id"] = str(seed["tenant_a"]["_id"])
        session["shop_id"] = str(seed["shop_b"]["_id"])
        db, shop = get_shop_db(get_master_db())
        assert db is None
        assert shop is None


def test_no_session_denied(app, seed):
    from app.extensions import get_master_db
    from app.utils.tenant import get_shop_db

    with app.test_request_context("/"):
        db, shop = get_shop_db(get_master_db())
        assert db is None and shop is None


def test_protected_page_redirects_anonymous(client):
    resp = client.get("/customers")
    assert resp.status_code in (301, 302, 401, 403)
