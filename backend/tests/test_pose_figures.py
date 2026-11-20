"""Pose figures (I2) through the real API, queue and worker, on I1's reference
contract: a pose-finder figure is an output asset that can be used again as the
``pose`` reference, and only as the pose."""

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


def extract(client, pid):
    photo = upload(client, pid, "pose").json()
    job = wait_for(client, pose_job(client, pid, photo["id"]).json()["id"], {"completed", "failed"})
    assert job["state"] == "completed", job.get("error_message")
    return photo, job["outputs"][0]


def test_pose_figure_becomes_a_reusable_asset(client):
    pid = new_project(client)
    photo, out_id = extract(client, pid)
    asset = client.get(f"/api/v1/assets/{out_id}").json()
    assert asset["kind"] == "output" and asset["media_type"] == "image/png"
    meta = asset["metadata"]
    assert meta["kind"] == "pose" and meta["source_asset_ids"] == [photo["id"]]
    assert meta["framing"] == "full_body" and len(meta["keypoints"]) == 18
    assert client.get(f"/api/v1/assets/{out_id}/content").content.startswith(b"\x89PNG")
    # The figure itself can be a later job's pose reference (here: read again).
    again = pose_job(client, pid, out_id)
    assert again.status_code == 201, again.text
    ref = again.json()["inputs"]["references"][0]
    assert ref["asset_id"] == out_id and ref["role"] == "pose"


def test_figure_only_as_the_pose_and_other_outputs_refused(client):
    pid = new_project(client)
    _, figure = extract(client, pid)
    r = pose_job(client, pid, figure, role="person")
    assert r.status_code == 422 and "only be used as the pose" in r.json()["error"]["message"]


def test_no_person_fails_plainly_and_retry_re_resolves(client):
    pid = new_project(client)
    photo = upload(client, pid, "pose", data=PNG + b"NOBODY").json()
    job = wait_for(client, pose_job(client, pid, photo["id"]).json()["id"], {"completed", "failed"})
    assert job["state"] == "failed" and "No person" in job["error_message"]
    assert job["outputs"] == []
    retry = client.post(f"/api/v1/jobs/{job['id']}/retry")
    assert retry.status_code == 201, retry.text
    assert retry.json()["inputs"]["references"][0]["asset_id"] == photo["id"]
