"""
Категории партов в Settings → Parts & Pricing.

На коллекции уникальный индекс (shop_id, slug) — его создаёт сидинг
магазина. Форма настроек сохраняла категорию БЕЗ slug, поэтому в магазине
проходила только первая своя категория, а каждая следующая падала с E11000
и страницей 500 (так было у клиента семь раз подряд). Здесь: slug ставится
всегда, дубль — понятное сообщение, старая категория без slug не мешает.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, get_csrf_token, login

CREATE_URL = "/settings/parts-settings/categories/create"


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def categories(seed, mongo):
    """Магазин как на проде: уникальный индекс, две сидированные категории
    со slug, одна старая без slug и категория чужого shop_id в той же базе."""
    coll = mongo[SHOP_A_DB].parts_categories
    coll.delete_many({})
    coll.create_index([("shop_id", 1), ("slug", 1)], unique=True, name="uniq_parts_categories_shop_slug")
    shop_id = seed["shop_a"]["_id"]
    now = _now()
    docs = {
        "filters": {"_id": ObjectId(), "shop_id": shop_id, "name": "Filters", "slug": "filters", "is_active": True, "created_at": now, "updated_at": now},
        "electrical": {"_id": ObjectId(), "shop_id": shop_id, "name": "Electrical", "slug": "electrical", "is_active": True, "created_at": now, "updated_at": now},
        "legacy": {"_id": ObjectId(), "shop_id": shop_id, "name": "ENGINE", "is_active": True, "created_at": now, "updated_at": now},
        "foreign": {"_id": ObjectId(), "shop_id": ObjectId(), "name": "Hydraulics", "slug": "hydraulics", "is_active": True, "created_at": now, "updated_at": now},
    }
    coll.insert_many(list(docs.values()))
    yield {"coll": coll, "shop_id": shop_id, **docs}
    coll.delete_many({})
    coll.drop_index("uniq_parts_categories_shop_slug")


def _post(client, url, name):
    return client.post(url, data={"name": name, "csrf_token": get_csrf_token(client)})


def _flashes(client) -> list[tuple[str, str]]:
    with client.session_transaction() as sess:
        return list(sess.pop("_flashes", []) or [])


def test_every_new_category_is_saved_not_only_the_first(client, categories):
    login(client)
    coll, shop_id = categories["coll"], categories["shop_id"]

    for name in ("Brakes", "Suspension", "Air system", "Cooling / HVAC"):
        resp = _post(client, CREATE_URL, name)
        assert resp.status_code == 302, (name, resp.get_data(as_text=True)[:300])
        assert _flashes(client) == [("success", "Category created.")], name

    saved = {c["name"]: c.get("slug") for c in coll.find({"shop_id": shop_id})}
    assert saved["Brakes"] == "brakes" and saved["Suspension"] == "suspension"
    assert saved["Air system"] == "air-system" and saved["Cooling / HVAC"] == "cooling-hvac"
    # Категория чужого магазина с тем же именем не мешает.
    assert _post(client, CREATE_URL, "Hydraulics").status_code == 302
    assert _flashes(client) == [("success", "Category created.")]
    assert coll.count_documents({"shop_id": shop_id, "slug": "hydraulics"}) == 1


def test_duplicate_category_is_a_message_not_an_error_page(client, categories):
    login(client)
    coll, shop_id = categories["coll"], categories["shop_id"]
    before = coll.count_documents({"shop_id": shop_id})

    for name, shown in (("filters", "Filters"), ("  FILTERS ", "Filters"), ("engine", "ENGINE"), ("Engine!", "ENGINE")):
        resp = _post(client, CREATE_URL, name)
        assert resp.status_code == 302, name
        assert _flashes(client) == [("error", f'Category "{shown}" already exists.')], name
    assert coll.count_documents({"shop_id": shop_id}) == before

    resp = _post(client, CREATE_URL, "   ")
    assert resp.status_code == 302 and _flashes(client) == [("error", "Category name is required.")]

    # Имена из одних знаков получают общий slug — вторая такая «уже есть».
    assert _post(client, CREATE_URL, "###").status_code == 302
    assert _flashes(client) == [("success", "Category created.")]
    assert _post(client, CREATE_URL, "!!!").status_code == 302
    assert _flashes(client) == [("error", 'Category "###" already exists.')]


def test_rename_keeps_slug_in_step_and_refuses_a_taken_name(client, categories):
    login(client)
    coll = categories["coll"]
    legacy_id, filters_id = categories["legacy"]["_id"], categories["filters"]["_id"]

    # Старая категория без slug получает его при первом же сохранении.
    resp = _post(client, f"/settings/parts-settings/categories/{legacy_id}/update", "Engine parts")
    assert resp.status_code == 302 and _flashes(client) == [("success", "Category updated.")]
    assert coll.find_one({"_id": legacy_id}, {"name": 1, "slug": 1, "_id": 0}) == {"name": "Engine parts", "slug": "engine-parts"}

    # Своё имя в другом регистре — можно.
    resp = _post(client, f"/settings/parts-settings/categories/{filters_id}/update", "FILTERS")
    assert resp.status_code == 302 and _flashes(client) == [("success", "Category updated.")]
    assert coll.find_one({"_id": filters_id})["slug"] == "filters"

    # Занятое имя — отказ, категория не тронута.
    resp = _post(client, f"/settings/parts-settings/categories/{filters_id}/update", "electrical")
    assert resp.status_code == 302 and _flashes(client) == [("error", 'Category "Electrical" already exists.')]
    assert coll.find_one({"_id": filters_id})["name"] == "FILTERS"

    # После переименования старое имя снова свободно.
    assert _post(client, CREATE_URL, "Engine").status_code == 302
    assert _flashes(client) == [("success", "Category created.")]


def test_slug_rule_is_shared_with_shop_seeding():
    from app.blueprints.settings.locations.routes import DEFAULT_PARTS_CATEGORIES as LOCATION_DEFAULTS
    from app.blueprints.tenant.routes import DEFAULT_PARTS_CATEGORIES
    from app.utils.parts_categories import slugify_category

    assert list(LOCATION_DEFAULTS) == list(DEFAULT_PARTS_CATEGORIES)
    slugs = [slugify_category(n) for n in DEFAULT_PARTS_CATEGORIES]
    assert len(set(slugs)) == len(slugs) and "filters" in slugs and "def" in slugs
    assert slugify_category("  Brake Parts / Air ") == "brake-parts-air"
    assert slugify_category("") == slugify_category(None) == slugify_category("--") == "category"
