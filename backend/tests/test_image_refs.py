"""Reference photos with roles (I1-I4 contract stand-in) and pose figures as
reusable assets, through the real API, queue and worker."""

from __future__ import annotations

import pytest

from tests.conftest import make_client, wait_for

POSE = "tests.fake_pose:FakePoseFinder"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def client(settings):
    with make_client(settings, extra_engines=(POSE,)) as c:
        yield c


def new_project(client, module="image"):
    r = client.post("/api/v1/projects", json={"name": "Poses", "module": module})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def upload(client, pid, role=None, data=PNG, name="me.png"):
    form = {"kind": "reference", **({"role": role} if role else {})}
    return client.post(
        f"/api/v1/projects/{pid}/assets", files={"file": (name, data, "image/png")}, data=form
    )


def pose_job(client, pid, asset_id, role="pose"):
    return client.post(
        "/api/v1/jobs",
        json={
            "project_id": pid,
            "task": "image.pose",
            "engine_id": "test-pose",
            "inputs": {"references": [{"asset_id": asset_id, "role": role}]},
        },
    )


def test_upload_stores_role_in_metadata(client):
    pid = new_project(client)
    r = upload(client, pid, "person")
    assert r.status_code == 201 and r.json()["metadata"] == {"role": "person"}
    assert upload(client, pid).json()["metadata"] == {}
    bad = upload(client, pid, "face")
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_role"


def test_pose_figure_becomes_a_reusable_asset(client):
    pid = new_project(client)
    photo = upload(client, pid, "pose").json()
    r = pose_job(client, pid, photo["id"])
    assert r.status_code == 201, r.text
    stored = r.json()["inputs"]["references"]
    # The stored inputs keep the asset id and role (path too, for the worker).
    assert stored[0]["asset_id"] == photo["id"] and stored[0]["role"] == "pose"
    job = wait_for(client, r.json()["id"], {"completed", "failed"})
    assert job["state"] == "completed", job.get("error_message")
    (out_id,) = job["outputs"]
    asset = client.get(f"/api/v1/assets/{out_id}").json()
    assert asset["kind"] == "output" and asset["media_type"] == "image/png"
    meta = asset["metadata"]
    assert meta["kind"] == "pose" and meta["source_asset_ids"] == [photo["id"]]
    assert meta["framing"] == "full_body" and len(meta["keypoints"]) == 18
    body = client.get(f"/api/v1/assets/{out_id}/content").content
    assert body.startswith(b"\x89PNG")
    # The figure itself can be the reference of a later job (here: read again).
    again = pose_job(client, pid, out_id)
    assert again.status_code == 201, again.text


def test_references_are_checked(client):
    pid, other = new_project(client), new_project(client)
    photo = upload(client, pid, "pose").json()
    foreign = upload(client, other, "pose").json()
    text = client.post(
        f"/api/v1/projects/{pid}/assets",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"kind": "reference"},
    ).json()
    cases = [
        (foreign["id"], "pose", "this project"),
        (text["id"], "pose", "pictures"),
        ("ast_missing", "pose", "deleted"),
        (photo["id"], "face", "Unknown photo role"),
    ]
    for asset_id, role, message in cases:
        r = pose_job(client, pid, asset_id, role)
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "invalid_reference"
        assert message in r.json()["error"]["message"]


def test_no_person_fails_plainly_and_retry_re_resolves(client):
    pid = new_project(client)
    photo = upload(client, pid, "pose", data=PNG + b"NOBODY").json()
    job = wait_for(client, pose_job(client, pid, photo["id"]).json()["id"], {"completed", "failed"})
    assert job["state"] == "failed" and "No person" in job["error_message"]
    assert job["outputs"] == []
    retry = client.post(f"/api/v1/jobs/{job['id']}/retry")
    assert retry.status_code == 201, retry.text
    assert retry.json()["inputs"]["references"][0]["asset_id"] == photo["id"]
