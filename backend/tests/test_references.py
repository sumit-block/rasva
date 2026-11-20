"""Reference photos with roles and the image.edit job contract (I1–I3)."""

from __future__ import annotations

import io

import pytest

from tests.conftest import make_client, wait_for
from tests.fake_image_editor import PNG

EDITOR = "tests.fake_image_editor:FakeImageEditor"
TERMINAL = {"completed", "failed", "canceled", "interrupted"}


def upload(c, project_id, name="me.png", data=PNG, **form):
    files = {"file": (name, io.BytesIO(data), "application/octet-stream")}
    return c.post(f"/api/v1/projects/{project_id}/assets", files=files, data=form)


def image_project(c, name="Edits"):
    return c.post("/api/v1/projects", json={"name": name, "module": "image"}).json()


def test_upload_with_role_and_change_it(settings):
    with make_client(settings) as c:
        p = image_project(c)
        r = upload(c, p["id"], role="person")
        assert r.status_code == 201, r.text
        asset = r.json()
        assert asset["metadata"] == {"role": "person"} and asset["kind"] == "reference"
        r = c.patch(f"/api/v1/assets/{asset['id']}", json={"role": "garment"})
        assert r.status_code == 200 and r.json()["metadata"]["role"] == "garment"
        r = c.patch(f"/api/v1/assets/{asset['id']}", json={"role": "hat"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_reference"


def test_roles_only_for_image_project_references(settings):
    with make_client(settings) as c:
        p = image_project(c)
        assert upload(c, p["id"], role="hat").status_code == 422
        assert upload(c, p["id"], kind="import", role="person").status_code == 422
        audio = c.post("/api/v1/projects", json={"name": "a", "module": "audio"}).json()
        r = upload(c, audio["id"], role="person")
        assert r.status_code == 422 and "image projects" in r.json()["error"]["message"]
        assert upload(c, audio["id"]).status_code == 201  # plain references still fine


def test_edit_job_resolves_references_and_runs(settings):
    with make_client(settings, extra_engines=(EDITOR,)) as c:
        p = image_project(c)
        person = upload(c, p["id"], role="person").json()
        garment = upload(c, p["id"], name="kurta.png", role="garment").json()
        r = c.post("/api/v1/jobs", json={
            "project_id": p["id"], "task": "image.edit", "engine_id": "test-image-editor",
            "inputs": {"prompt": "Wear the blue kurta", "seed": 5, "references": [
                {"asset_id": person["id"]},  # role from the upload
                {"asset_id": garment["id"], "role": "garment"},
            ]},
        })  # fmt: skip
        assert r.status_code == 201, r.text
        refs = r.json()["inputs"]["references"]
        assert [x["role"] for x in refs] == ["person", "garment"]
        assert all(x["path"].endswith(".png") for x in refs)
        done = wait_for(c, r.json()["id"], TERMINAL)
        assert done["state"] == "completed", done
        out = c.get(f"/api/v1/assets/{done['outputs'][0]}").json()
        assert out["metadata"]["source_asset_ids"] == [person["id"], garment["id"]]
        assert out["metadata"]["synthetic"] is True


def test_reference_errors(settings):
    with make_client(settings, extra_engines=(EDITOR,)) as c:
        p, other = image_project(c), image_project(c, "Other")
        mine = upload(c, p["id"], role="person").json()
        theirs = upload(c, other["id"], role="person").json()
        text = upload(c, p["id"], name="notes.txt", data=b"hello").json()

        def edit(refs):
            return c.post("/api/v1/jobs", json={
                "project_id": p["id"], "task": "image.edit", "engine_id": "test-image-editor",
                "inputs": {"prompt": "x", "references": refs},
            })  # fmt: skip

        cases = [
            ([], "at least one"),
            ([{"asset_id": theirs["id"]}], "this project's"),
            ([{"asset_id": text["id"]}], "not a photo"),
            ([{"asset_id": mine["id"]}, {"asset_id": mine["id"]}], "twice"),
            ([{"asset_id": "ast_missing"}], "deleted"),
            ([{"asset_id": mine["id"], "role": "hat"}], "Unknown photo role"),
            ([{"asset_id": mine["id"]}] * 5, "at most"),
        ]
        for refs, message in cases:
            r = edit(refs)
            assert r.status_code == 422, (refs, r.text)
            assert r.json()["error"]["code"] == "invalid_reference"
            assert message in r.json()["error"]["message"], (message, r.text)


def test_retry_re_resolves_references(settings):
    with make_client(settings, extra_engines=(EDITOR,)) as c:
        p = image_project(c)
        person = upload(c, p["id"], role="person").json()
        r = c.post("/api/v1/jobs", json={
            "project_id": p["id"], "task": "image.edit", "engine_id": "test-image-editor",
            "inputs": {"prompt": "x", "seed": 9, "references": [{"asset_id": person["id"]}]},
        })  # fmt: skip
        job = r.json()
        c.post(f"/api/v1/jobs/{job['id']}/cancel")
        if wait_for(c, job["id"], TERMINAL)["state"] == "completed":
            pytest.skip("the job finished before the cancel landed")
        retry = c.post(f"/api/v1/jobs/{job['id']}/retry")
        assert retry.status_code == 201, retry.text
        assert retry.json()["inputs"]["references"][0]["asset_id"] == person["id"]
        assert retry.json()["inputs"]["seed"] == 9
