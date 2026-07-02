"""Healthcheck, security headers, глобальные обработчики ошибок."""


def test_healthz_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}


def test_security_headers_present(client):
    resp = client.get("/")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    # HSTS только по HTTPS — в тестах http, заголовка быть не должно.
    assert "Strict-Transport-Security" not in resp.headers


def test_404_html(client):
    resp = client.get("/no-such-page-xyz")
    assert resp.status_code == 404
    assert "text/html" in resp.content_type
    assert "404" in resp.get_data(as_text=True)


def test_404_json_for_api_paths(client):
    resp = client.get("/work_orders/api/no-such-endpoint-xyz")
    assert resp.status_code == 404
    assert resp.get_json() == {"ok": False, "error": "not_found"}


def test_dynamic_html_not_cached(client):
    resp = client.get("/")
    assert "no-store" in (resp.headers.get("Cache-Control") or "")
