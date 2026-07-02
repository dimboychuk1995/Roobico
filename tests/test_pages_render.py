"""Смоук-рендер ключевых страниц под залогиненным владельцем.

Ловит ошибки Jinja (сломанные макросы/инклюды) и 500-ки после
фронтенд-рефакторингов. Не проверяет вид — только что страница
рендерится и содержит ожидаемый маркер.
"""
import pytest

from tests.conftest import login

PAGES = [
    ("/customers", "Customers"),
    ("/vendors", "Vendors"),
    ("/parts", "Parts"),
    ("/work_orders", "Work Orders"),
    ("/settings", "Settings"),
    ("/settings/users", "Users"),
    ("/reports", "Reports"),
    ("/calendar", "Calendar"),
    ("/import-export/", "Import"),
]


@pytest.fixture()
def logged_in(client):
    assert login(client).status_code == 302
    return client


@pytest.mark.parametrize("path,marker", PAGES)
def test_page_renders(logged_in, path, marker):
    resp = logged_in.get(path, follow_redirects=True)
    assert resp.status_code == 200, f"{path} -> {resp.status_code}"
    body = resp.get_data(as_text=True)
    assert marker.lower() in body.lower(), f"{path}: marker '{marker}' not found"
    # Базовый layout с темой и CSRF-мета присутствует
    assert 'name="csrf-token"' in body


def test_auth_pages_render(client):
    for path in ("/", "/forgot-password"):
        resp = client.get(path)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "css/auth.css" in body, f"{path}: auth.css not linked"
