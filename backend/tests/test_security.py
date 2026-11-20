from fastapi.testclient import TestClient

from rasva.app import create_app


def test_rejects_non_loopback_client(settings):
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8765", client=("192.168.1.20", 1)) as c:
        r = c.get("/api/v1/system/health")
    assert r.status_code == 403
    assert "only accepts connections from this Mac" in r.json()["error"]["message"]


def test_rejects_dns_rebinding_host(client):
    r = client.get("/api/v1/system/health", headers={"Host": "attacker.example:8765"})
    assert r.status_code == 403


def test_rejects_cross_site_post(client):
    r = client.post(
        "/api/v1/projects",
        json={"name": "x", "module": "audio"},
        headers={"Origin": "https://attacker.example"},
    )
    assert r.status_code == 403


def test_allows_same_origin_post(client):
    r = client.post(
        "/api/v1/projects",
        json={"name": "x", "module": "audio"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert r.status_code == 201


def test_security_headers(client):
    r = client.get("/api/v1/system/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in r.headers["content-security-policy"]
