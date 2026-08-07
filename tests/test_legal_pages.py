"""Публичные юридические страницы (/privacy, /terms) — доступны без логина.

Их URL уходит в App Store Connect (privacy policy URL обязателен для ревью),
поэтому страницы обязаны отдаваться анонимно и ссылаться друг на друга.
"""


def test_privacy_page_public(client):
    resp = client.get("/privacy")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Privacy Policy" in html
    assert "/terms" in html


def test_terms_page_public(client):
    resp = client.get("/terms")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Terms of Service" in html
    assert "/privacy" in html
