"""Новая локация (Settings -> Locations) сразу получает индексы shop-базы —
те же, что создаёт старт приложения, включая TTL wo_edit_views (база merge
сохранений WO). Раньше индексов не было до ближайшего рестарта."""
from bson import ObjectId

from app.extensions import WO_EDIT_VIEWS_TTL_SECONDS


def test_new_location_db_gets_indexes_without_restart(app):
    from app.blueprints.settings.locations.routes import init_shop_database
    from app.extensions import get_mongo_client

    db_name = "roobico_test_zz_location_init_" + str(ObjectId())[-8:]
    with app.app_context():
        client = get_mongo_client()
        try:
            init_shop_database(
                db_name,
                {"name": "Tenant", "slug": "tenant", "timezone": "UTC"},
                {"_id": ObjectId(), "name": "Second location", "slug": "second-location"},
            )
            sdb = client[db_name]

            views_idx = sdb.wo_edit_views.index_information()
            assert "idx_wo_edit_views_lookup" in views_idx
            assert views_idx["ttl_wo_edit_views_created"]["expireAfterSeconds"] == WO_EDIT_VIEWS_TTL_SECONDS

            # Обычные индексы списков тоже на месте.
            assert "idx_vendors_shop_active_name" in sdb.vendors.index_information()
            assert "idx_parts_shop_active_partnum" in sdb.parts.index_information()

            # Сиды по-прежнему отрабатывают (их unique-индексы создаются первыми).
            assert sdb.labor_rates.count_documents({}) > 0
        finally:
            client.drop_database(db_name)
