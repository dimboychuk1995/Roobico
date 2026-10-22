"""Education Center: страницы, прогресс, целостность каталога и интеграция
с AI-помощником.

Целостность: у каждого урока из каталога есть шаблон; каждый скриншот,
на который ссылается шаблон, описан в shots.SHOTS и лежит в
static/img/education; каждый описанный скриншот используется хотя бы одним
уроком (иначе это мусор в промпте помощника).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from bson import ObjectId
from werkzeug.security import generate_password_hash

from app.education import catalog, shots
from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, get_csrf_token, login

STATIC_EDU = Path(__file__).resolve().parents[1] / "app" / "static" / "img" / "education"


@pytest.fixture()
def logged(client):
    assert login(client).status_code == 302
    return client


@pytest.fixture(autouse=True)
def _clean_progress(app):
    from app.extensions import get_master_db
    with app.app_context():
        get_master_db().education_progress.delete_many({})
    yield


# ── Каталог ───────────────────────────────────────────────────────────


def test_catalog_ids_are_unique_and_slug_like():
    slug = re.compile(r"^[a-z0-9][a-z0-9-]*$")
    module_ids = [m.id for m in catalog.MODULES]
    assert len(module_ids) == len(set(module_ids))
    keys = [l.key for l in catalog.ALL_LESSONS]
    assert len(keys) == len(set(keys))
    for m in catalog.MODULES:
        assert slug.match(m.id), m.id
        assert m.lessons, f"module {m.id} has no lessons"
        for l in m.lessons:
            assert slug.match(l.id), l.key
            assert l.minutes > 0
            assert l.summary and l.title


def test_every_lesson_has_a_template():
    missing = [l.key for l in catalog.ALL_LESSONS if not catalog.lesson_template_exists(l)]
    assert not missing, f"lessons without template: {missing}"


def test_lesson_screenshots_are_catalogued_and_exist():
    problems = []
    used: set[str] = set()
    for l in catalog.ALL_LESSONS:
        ids = l.shot_ids()
        if not ids:
            # Уроки про мобильное приложение показывают кадры приложения из
            # static/img/landing (mobile_shot) — им каталог не нужен.
            src = (catalog._LESSONS_DIR / l.module_id / f"{l.id}.html").read_text(encoding="utf-8")
            if "mobile_shot(" not in src:
                problems.append(f"{l.key}: no screenshots")
        for sid in ids:
            used.add(sid)
            if sid not in shots.SHOTS:
                problems.append(f"{l.key}: shot '{sid}' has no caption in shots.SHOTS")
            if not (STATIC_EDU / f"{sid}.{shots.SHOT_EXT}").exists():
                problems.append(f"{l.key}: file for shot '{sid}' is missing")
    unused = sorted(set(shots.SHOTS) - used)
    assert not problems, "\n".join(problems)
    assert not unused, f"captioned screenshots not used by any lesson: {unused}"


def test_catalog_is_big_enough():
    """Учебный центр задуман большим: много уроков и много скриншотов."""
    assert catalog.TOTAL_LESSONS >= 60
    all_shots = {sid for l in catalog.ALL_LESSONS for sid in l.shot_ids()}
    assert len(all_shots) >= 120


def test_assistant_catalog_text_lists_lessons_and_shots():
    text = catalog.assistant_catalog_text()
    assert "LESSON /education/work-orders/create-wo" in text
    assert "SHOT /static/img/education/" in text


# ── Страницы ──────────────────────────────────────────────────────────


def test_pages_require_login(client):
    for path in ("/education/", "/education/work-orders/", "/education/work-orders/create-wo"):
        resp = client.get(path)
        assert resp.status_code == 302, path


def test_index_renders_modules_and_progress(logged):
    from markupsafe import escape

    body = logged.get("/education/").get_data(as_text=True)
    assert "Education Center" in body
    for m in catalog.MODULES:
        assert str(escape(m.title)) in body
    assert "0 of %d lessons" % catalog.TOTAL_LESSONS in body or "<strong>0</strong> of %d" % catalog.TOTAL_LESSONS in body
    assert 'id="eduLessonIndex"' in body
    assert "css/education.css" in body
    # Пункт меню в сайдбаре
    assert 'href="/education/"' in body


def test_module_and_lesson_pages_render(logged):
    resp = logged.get("/education/work-orders/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Work Orders" in body
    assert "Creating a work order" in body

    resp = logged.get("/education/work-orders/create-wo")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Mark lesson as complete" in body
    assert 'data-lesson-key="work-orders/create-wo"' in body
    assert "img/education/" in body


def test_every_lesson_page_renders(logged):
    """Ловим сломанные макросы/инклюды во всех уроках."""
    from markupsafe import escape

    for l in catalog.ALL_LESSONS:
        resp = logged.get(l.url)
        assert resp.status_code == 200, f"{l.url} -> {resp.status_code}"
        body = resp.get_data(as_text=True)
        assert str(escape(l.title)) in body


def test_lesson_cross_links_resolve():
    """lesson_link("module/lesson") в текстах уроков ссылается на существующие уроки."""
    broken = []
    for l in catalog.ALL_LESSONS:
        src = (catalog._LESSONS_DIR / l.module_id / f"{l.id}.html").read_text(encoding="utf-8")
        for key in re.findall(r'lesson_link\("([^"]+)"', src):
            if not catalog.get_lesson_by_key(key):
                broken.append(f"{l.key} -> {key}")
    assert not broken, broken


def test_unknown_module_or_lesson_404(logged):
    assert logged.get("/education/nope/").status_code == 404
    assert logged.get("/education/work-orders/nope").status_code == 404


# ── Прогресс ──────────────────────────────────────────────────────────


def _set(client, key, completed=True):
    return client.post(
        "/education/api/progress",
        data=json.dumps({"lesson": key, "completed": completed}),
        content_type="application/json",
        headers={"X-CSRFToken": get_csrf_token(client)},
    )


def test_progress_toggle_and_summary(logged):
    resp = _set(logged, "work-orders/create-wo")
    assert resp.status_code == 200
    p = resp.get_json()["progress"]
    assert p["done"] == 1
    assert "work-orders/create-wo" in p["completed"]
    assert p["modules"]["work-orders"]["done"] == 1
    assert p["continue"]["key"] != "work-orders/create-wo"

    # Страница урока показывает «Completed», модуль — галочку.
    body = logged.get("/education/work-orders/create-wo").get_data(as_text=True)
    assert 'data-completed="1"' in body
    body = logged.get("/education/work-orders/").get_data(as_text=True)
    assert "1 / %d completed" % len(catalog.get_module("work-orders").lessons) in body

    # Снятие отметки
    resp = _set(logged, "work-orders/create-wo", completed=False)
    assert resp.get_json()["progress"]["done"] == 0

    # Неизвестный урок
    assert _set(logged, "work-orders/nope").status_code == 404


def test_progress_reset(logged):
    _set(logged, "dashboard/dashboard-overview")
    _set(logged, "dashboard/dashboard-goals")
    assert logged.get("/education/api/progress").get_json()["progress"]["done"] == 2
    resp = logged.post("/education/api/progress/reset", headers={"X-CSRFToken": get_csrf_token(logged)})
    assert resp.status_code == 200
    assert resp.get_json()["progress"]["done"] == 0


def test_continue_points_to_last_viewed_unfinished(logged):
    logged.get("/education/parts/stocktakes")
    p = logged.get("/education/api/progress").get_json()["progress"]
    assert p["continue"]["key"] == "parts/stocktakes"
    _set(logged, "parts/stocktakes")
    p = logged.get("/education/api/progress").get_json()["progress"]
    assert p["continue"]["key"] != "parts/stocktakes"


def test_progress_is_per_user(app, client, seed):
    """Второй пользователь того же тенанта не видит чужой прогресс."""
    from app.extensions import get_master_db
    with app.app_context():
        master = get_master_db()
        master.users.delete_many({"email": "learner@test.local"})
        master.users.insert_one({
            "_id": ObjectId(), "email": "learner@test.local",
            "password_hash": generate_password_hash("password123"),
            "is_active": True, "tenant_id": seed["tenant_a"]["_id"],
            "shop_ids": [str(seed["shop_a"]["_id"])], "role": "manager",
        })

    login(client)
    _set(client, "calendar/calendar-basics")
    assert client.get("/education/api/progress").get_json()["progress"]["done"] == 1
    client.get("/logout")

    login(client, email="learner@test.local", password="password123")
    assert client.get("/education/api/progress").get_json()["progress"]["done"] == 0
    client.get("/logout")
    login(client, email=OWNER_EMAIL, password=OWNER_PASSWORD)


# ── AI-помощник ───────────────────────────────────────────────────────


def test_system_prompt_includes_education_catalog(app):
    from app.blueprints.assistant.services import chat as chat_service
    with app.app_context():
        prompt = chat_service.build_system_prompt("/work_orders")
    assert "Education Center" in prompt
    assert "LESSON /education/" in prompt
    assert "SHOT /static/img/education/" in prompt
