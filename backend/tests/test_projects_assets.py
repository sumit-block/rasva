from rasva.services import assets as asset_svc
from tests.conftest import wav_bytes


def test_health_and_system_check(client):
    h = client.get("/api/v1/system/health").json()
    assert h["status"] == "ok" and h["schema_version"] >= 1
    check = client.get("/api/v1/system/check").json()
    assert {c["id"] for c in check["checks"]} >= {"platform", "memory", "disk"}
    storage = client.get("/api/v1/system/storage").json()
    assert storage["budget_bytes"] == 50 * 1024**3


def test_project_crud_and_search(client, project):
    client.post("/api/v1/projects", json={"name": "Ramayana 50%_draft", "module": "audio"})
    names = [p["name"] for p in client.get("/api/v1/projects", params={"q": "Maha"}).json()]
    assert names == ["Mahabharata ch.1"]
    # LIKE wildcards are escaped
    assert [p["name"] for p in client.get("/api/v1/projects", params={"q": "50%_"}).json()] == [
        "Ramayana 50%_draft"
    ]
    r = client.patch(f"/api/v1/projects/{project['id']}", json={"status": "archived"})
    assert r.json()["status"] == "archived"
    assert client.get("/api/v1/projects/prj_missing").status_code == 404


def test_upload_and_download_reference(client, project):
    data = wav_bytes()
    r = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        files={"file": ("../../कृष्ण voice.wav", data, "audio/wav")},
        data={"kind": "reference"},
    )
    assert r.status_code == 201, r.text
    asset = r.json()
    assert asset["original_name"] == "कृष्ण voice.wav"  # dirs stripped, Hindi kept
    assert asset["protected"] is True
    assert ".." not in asset["rel_path"]
    content = client.get(f"/api/v1/assets/{asset['id']}/content")
    assert content.content == data


def test_rejects_unsupported_and_spoofed_files(client, project):
    url = f"/api/v1/projects/{project['id']}/assets"
    r = client.post(url, files={"file": ("x.exe", b"MZ....", "application/octet-stream")})
    assert r.status_code == 422
    r = client.post(url, files={"file": ("photo.png", b"not really a png", "image/png")})
    assert r.status_code == 422
    assert "do not match" in r.json()["error"]["message"]
    r = client.post(url, files={"file": ("empty.txt", b"", "text/plain")})
    assert r.status_code == 422


def test_upload_size_limit(client, project):
    big = b"a" * (6 * 1024 * 1024)
    r = client.post(
        f"/api/v1/projects/{project['id']}/assets", files={"file": ("big.txt", big, "text/plain")}
    )
    assert r.status_code == 422
    assert "limit" in r.json()["error"]["message"]


def test_quota_blocks_upload(client, project, monkeypatch):
    from rasva.services.storage import Preflight

    state = client.app.state.rasva
    monkeypatch.setattr(
        state, "preflight", lambda conn, n, purpose: Preflight(False, n, ["Storage is full."])
    )
    r = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        files={"file": ("a.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 507
    assert not any(state.settings.paths.uploads_tmp.iterdir())  # temp file cleaned up


def test_resolve_rejects_escape(settings):
    settings.paths.ensure()
    try:
        asset_svc.resolve(settings.paths, "../../etc/passwd")
    except asset_svc.AssetError:
        return
    raise AssertionError("path escape not rejected")


def test_settings_validation(client):
    r = client.patch("/api/v1/settings", json={"storage.budget_bytes": 1})
    assert r.status_code == 422
    r = client.patch("/api/v1/settings", json={"lan.enabled": True})
    assert r.status_code == 422
    r = client.patch("/api/v1/settings", json={"ui.mode": "advanced"})
    assert r.json()["ui.mode"] == "advanced"


def test_unknown_api_route_is_json_404(client):
    r = client.get("/api/v1/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
