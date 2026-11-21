"""Image phase I4 (engine-independent): versions, favourites, cleanup, recovery,
low-memory mode and PNG export, through the real queue with a fake image engine."""

from __future__ import annotations

import sqlite3
import time

import pytest

from rasva import db as db_mod
from rasva.db import connect
from rasva.engines.sd15 import StableDiffusion15Engine
from rasva.services import cleanup, image_export
from rasva.services import images as image_svc
from tests.conftest import make_client, wait_for
from tests.fake_versioned_image import png_bytes

PAINTER = "tests.fake_versioned_image:FakeVersionedImage"
TERMINAL = {"completed", "failed", "canceled", "interrupted"}
API = "/api/v1"
LATER = time.time() + 3 * 86_400  # "now" for cleanup: everything is days old


@pytest.fixture
def c(settings):
    with make_client(settings, extra_engines=(PAINTER,)) as client:
        yield client


@pytest.fixture
def pid(c):
    return c.post(f"{API}/projects", json={"name": "Pictures", "module": "image"}).json()["id"]


def generate(c, pid, prompt="a lighthouse at dusk", **extra):
    body = {"project_id": pid, "task": "image.generate", "engine_id": "test-versioned-image",
            "inputs": {"prompt": prompt, "preset": "quick"}, **extra}  # fmt: skip
    r = c.post(f"{API}/jobs", json=body)
    assert r.status_code == 201, r.text
    return wait_for(c, r.json()["id"], TERMINAL)


def again(c, job_id, **body):
    r = c.post(f"{API}/jobs/{job_id}/again", json=body)
    assert r.status_code == 201, r.text
    return wait_for(c, r.json()["id"], TERMINAL)


def groups(c, pid):
    return c.get(f"{API}/projects/{pid}/image-groups").json()


def group_with(c, pid, job_id):
    return next(g for g in groups(c, pid) if job_id in {v["id"] for v in g["versions"]})


def upload_reference(c, pid, rgb=(200, 100, 50)):
    files = {"file": ("me.png", png_bytes(rgb), "image/png")}
    r = c.post(f"{API}/projects/{pid}/assets", files=files, data={"kind": "reference"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ------------------------------------------------------------------ versions


def test_versions_group_variations_and_same_seed(c, pid):
    first = generate(c, pid)
    assert first["state"] == "completed"
    other = generate(c, pid, prompt="a different picture")
    assert group_with(c, pid, first["id"])["id"] != group_with(c, pid, other["id"])["id"]

    variation = again(c, first["id"], seed="new")
    same = again(c, first["id"], seed="same", prompt="a lighthouse at dawn")
    assert same["inputs"]["seed"] == first["inputs"]["seed"]
    assert same["inputs"]["prompt"] == "a lighthouse at dawn"
    assert variation["inputs"]["prompt"] == first["inputs"]["prompt"]
    assert variation["inputs"]["preset"] == "quick" and variation["inputs"]["steps"] == 15
    # A fresh seed is chosen (2**32 values; a clash would be a 1-in-4-billion fluke).
    assert variation["inputs"]["seed"] != first["inputs"]["seed"]

    g = group_with(c, pid, first["id"])
    assert [v["id"] for v in g["versions"]] == [first["id"], variation["id"], same["id"]]
    assert g["favourite_job_id"] is None and g["current_job_id"] == same["id"]  # newest

    # Explicit version_of on POST /jobs joins the group too.
    r = c.post(f"{API}/jobs", json={"project_id": pid, "task": "image.generate",
                                     "engine_id": "test-versioned-image", "version_of": first["id"],
                                     "inputs": {"prompt": "edited by hand"}})  # fmt: skip
    hand = wait_for(c, r.json()["id"], TERMINAL)
    assert group_with(c, pid, hand["id"])["id"] == g["id"]


def test_favourite_is_current_and_validated(c, pid):
    first = generate(c, pid)
    second = again(c, first["id"])
    gid = group_with(c, pid, first["id"])["id"]
    r = c.patch(f"{API}/image-groups/{gid}", json={"favourite_job_id": first["id"]})
    assert r.status_code == 200 and r.json()["current_job_id"] == first["id"]

    other = generate(c, pid, prompt="another")
    r = c.patch(f"{API}/image-groups/{gid}", json={"favourite_job_id": other["id"]})
    assert r.status_code == 422  # belongs to another image
    r = c.patch(f"{API}/image-groups/{gid}", json={"favourite_job_id": None})
    assert r.json()["current_job_id"] == second["id"]
    assert c.get(f"{API}/image-groups/nope").status_code == 404


def test_version_of_must_be_an_image_in_the_same_project(c, pid):
    first = generate(c, pid)
    other = c.post(f"{API}/projects", json={"name": "Else", "module": "image"}).json()["id"]
    body = {"project_id": other, "task": "image.generate", "engine_id": "test-versioned-image",
            "version_of": first["id"], "inputs": {"prompt": "x"}}  # fmt: skip
    r = c.post(f"{API}/jobs", json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_version"
    r = c.post(f"{API}/jobs", json={**body, "project_id": pid, "version_of": "job_missing"})
    assert r.status_code == 422


def test_edits_of_the_same_references_are_versions(c, pid):
    person = upload_reference(c, pid)
    garment = upload_reference(c, pid, (10, 20, 30))

    def edit(prompt, refs):
        inputs = {"prompt": prompt, "references": refs}
        body = {"project_id": pid, "task": "image.edit", "engine_id": "test-versioned-image"}
        r = c.post(f"{API}/jobs", json={**body, "inputs": inputs})
        assert r.status_code == 201, r.text
        return wait_for(c, r.json()["id"], TERMINAL)

    refs = [{"asset_id": person, "role": "person"}, {"asset_id": garment, "role": "garment"}]
    a = edit("wearing the jacket", refs)
    b = edit("wearing the jacket, smiling", list(reversed(refs)))  # order doesn't matter
    d = edit("in the jacket", [{"asset_id": person, "role": "person"}])
    assert group_with(c, pid, a["id"])["id"] == group_with(c, pid, b["id"])["id"]
    assert group_with(c, pid, d["id"])["id"] != group_with(c, pid, a["id"])["id"]
    strip = lambda rs: [{"asset_id": r["asset_id"], "role": r["role"]} for r in rs]  # noqa: E731
    assert strip(b["inputs"]["references"]) == list(reversed(refs))
    assert all(r["path"].endswith(".png") for r in b["inputs"]["references"])  # resolved (I1)
    # References survive "again" and output metadata names its sources.
    v = again(c, a["id"])
    assert strip(v["inputs"]["references"]) == refs
    meta = c.get(f"{API}/assets/{v['outputs'][0]}").json()["metadata"]
    assert set(meta["source_asset_ids"]) == {person, garment}


def test_again_refuses_missing_jobs(c, pid):
    assert c.post(f"{API}/jobs/job_missing/again", json={}).status_code == 404
    first = generate(c, pid)
    assert c.post(f"{API}/jobs/{first['id']}/again", json={"seed": "maybe"}).status_code == 422


def test_migration_backfills_existing_images(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    conn = connect(path)
    every = db_mod._available_migrations()
    monkeypatch.setattr(db_mod, "_available_migrations", lambda: [m for m in every if m[0] < 9])
    db_mod.migrate(conn, tmp_path / "backups")
    now = db_mod.utcnow()
    conn.execute("INSERT INTO projects(id, name, module, created_at, updated_at)"
                 " VALUES ('prj_1', 'P', 'image', ?, ?)", (now, now))  # fmt: skip
    jobs = (
        ("job_aaa", "prj_1", "image.generate"),
        ("job_bbb", "prj_1", "audio.narrate"),
    )
    for jid, project, task in jobs:
        conn.execute("INSERT INTO jobs(id, project_id, task, engine_id, state, created_at,"
                     " updated_at) VALUES (?, ?, ?, 'e', 'completed', ?, ?)",
                     (jid, project, task, now, now))  # fmt: skip
    monkeypatch.setattr(db_mod, "_available_migrations", lambda: every)
    assert db_mod.migrate(conn, tmp_path / "backups") >= 9
    rows = conn.execute("SELECT job_id, group_id FROM image_versions").fetchall()
    assert [(r["job_id"], r["group_id"]) for r in rows] == [("job_aaa", "imgg_aaa")]
    assert list(tmp_path.joinpath("backups").glob("*pre-v0009*"))  # backed up first


# ------------------------------------------------------------------ cleanup


def test_cleanup_offers_only_older_non_favourite_versions(c, pid, settings):
    paths = settings.paths
    reference = upload_reference(c, pid)
    fav_group = [generate(c, pid)]
    fav_group += [again(c, fav_group[0]["id"]), again(c, fav_group[0]["id"])]
    gid = group_with(c, pid, fav_group[0]["id"])["id"]
    c.patch(f"{API}/image-groups/{gid}", json={"favourite_job_id": fav_group[1]["id"]})
    no_fav = [generate(c, pid, prompt="no favourite")]
    no_fav.append(again(c, no_fav[0]["id"]))
    single = generate(c, pid, prompt="only one")
    # The newest version of a group with no favourite is kept; so is a photo used by an edit.
    source = again(c, no_fav[0]["id"])
    refs = [{"asset_id": reference, "role": "person"}]
    body = {"project_id": pid, "task": "image.edit", "engine_id": "test-versioned-image"}
    edit = c.post(f"{API}/jobs", json={**body, "inputs": {"prompt": "x", "references": refs}})
    edit = wait_for(c, edit.json()["id"], TERMINAL)
    out = lambda j: j["outputs"][0]  # noqa: E731

    conn = connect(paths.db_file)
    try:
        # Today's versions are not offered yet.
        assert not cleanup.plan(conn, paths).groups.get("older_image_versions")
        offered = {
            i.asset_id for i in cleanup.plan(conn, paths, LATER).groups["older_image_versions"]
        }
        assert offered == {out(fav_group[0]), out(fav_group[2]), out(no_fav[0]), out(no_fav[1])}
        never = {reference, out(fav_group[1]), out(single), out(source), out(edit)}
        every = {i.asset_id for items in cleanup.plan(conn, paths, LATER).groups.values()
                 for i in items}  # fmt: skip
        assert not never & every
        meta = {g["id"]: g for g in cleanup.plan(conn, paths).summary()["groups"]}
        assert meta["older_image_versions"]["default"] is False

        result = cleanup.execute(conn, paths, ["older_image_versions"], LATER)
        assert result["deleted"]["older_image_versions"] == 4
    finally:
        conn.close()
    assert c.get(f"{API}/assets/{out(fav_group[1])}/content").status_code == 200
    assert c.get(f"{API}/assets/{out(fav_group[0])}").status_code == 404
    g = group_with(c, pid, fav_group[0]["id"])
    assert g["current_job_id"] == fav_group[1]["id"]
    assert next(v for v in g["versions"] if v["id"] == fav_group[0]["id"])["outputs"] == []
    # The no-favourite group keeps its newest.
    assert group_with(c, pid, no_fav[0]["id"])["current_job_id"] == source["id"]


def test_failed_image_files_are_temporary_and_ungrouped_images_are_kept(c, pid, settings):
    paths = settings.paths
    failed = generate(c, pid)
    legacy = generate(c, pid, prompt="from before versions")
    conn = connect(paths.db_file)
    try:
        conn.execute("UPDATE jobs SET state = 'failed' WHERE id = ?", (failed["id"],))
        conn.execute("DELETE FROM image_versions WHERE job_id = ?", (legacy["id"],))
        plan = cleanup.plan(conn, paths, LATER)
        temp = {i.asset_id for i in plan.groups["temp"]}
        assert failed["outputs"][0] in temp
        every = {i.asset_id for items in plan.groups.values() for i in items}
        assert legacy["outputs"][0] not in every
    finally:
        conn.close()


# ------------------------------------------------------------------ recovery


def test_interrupted_image_job_retries_with_same_seed(settings, monkeypatch):
    monkeypatch.setenv("FAKE_PAINTER_HANG", "1")
    with make_client(settings, extra_engines=(PAINTER,)) as c:
        pid = c.post(f"{API}/projects", json={"name": "P", "module": "image"}).json()["id"]
        r = c.post(f"{API}/jobs", json={"project_id": pid, "task": "image.generate",
                                         "engine_id": "test-versioned-image",
                                         "inputs": {"prompt": "a cat", "seed": 1234}})  # fmt: skip
        job = wait_for(c, r.json()["id"], {"processing"})
    # Simulate a hard stop (power loss): the row is still "processing" at next start.
    conn = connect(settings.paths.db_file)
    conn.execute("UPDATE jobs SET state = 'processing' WHERE id = ?", (job["id"],))
    conn.close()
    monkeypatch.delenv("FAKE_PAINTER_HANG")
    with make_client(settings, extra_engines=(PAINTER,)) as c:
        job = c.get(f"{API}/jobs/{job['id']}").json()
        assert job["state"] == "interrupted" and job["error_code"] == "interrupted"
        retry = c.post(f"{API}/jobs/{job['id']}/retry")
        assert retry.status_code == 201, retry.text
        retry = wait_for(c, retry.json()["id"], TERMINAL)
        assert retry["state"] == "completed" and retry["inputs"]["seed"] == 1234
        assert retry["inputs"]["prompt"] == "a cat"
        g = group_with(c, pid, job["id"])
        assert [v["id"] for v in g["versions"]] == [job["id"], retry["id"]]
        assert g["current_job_id"] == retry["id"]
        # "Try again with the same seed" from the interrupted version works as well.
        same = again(c, job["id"], seed="same")
        assert same["inputs"]["seed"] == 1234 and same["state"] == "completed"


# ------------------------------------------------------------------ low-memory mode


def test_low_memory_mode_reaches_engines_that_support_it(c, pid, settings):
    first = generate(c, pid)
    assert "low_memory" not in first["inputs"]
    assert c.patch(f"{API}/settings", json={"image.low_memory": True}).status_code == 200
    second = again(c, first["id"])
    assert second["inputs"]["low_memory"] is True and second["metrics"]["low_memory"] is True
    meta = c.get(f"{API}/assets/{second['outputs'][0]}").json()["metadata"]
    assert meta["low_memory"] is True
    # "Again" and Retry read the current setting, not the old job's.
    c.patch(f"{API}/settings", json={"image.low_memory": False})
    off = c.post(f"{API}/jobs/{second['id']}/again", json={"seed": "same"}).json()
    assert "low_memory" not in off["inputs"]
    conn = connect(settings.paths.db_file)
    conn.execute("UPDATE jobs SET state = 'failed' WHERE id = ?", (first["id"],))
    conn.close()
    c.patch(f"{API}/settings", json={"image.low_memory": True})
    retry = c.post(f"{API}/jobs/{first['id']}/retry")
    assert retry.status_code == 201 and retry.json()["inputs"]["low_memory"] is True
    assert retry.json()["inputs"]["seed"] == first["inputs"]["seed"]
    assert c.patch(f"{API}/settings", json={"image.low_memory": "yes"}).status_code == 422


def test_low_memory_is_not_sent_to_engines_without_the_feature():
    from tests.fake_versioned_image import FakeVersionedImage

    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    mem.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    mem.execute("INSERT INTO settings VALUES ('image.low_memory', 'true', '')")
    assert image_svc.engine_extras(mem, StableDiffusion15Engine.descriptor) == {}
    assert image_svc.engine_extras(mem, FakeVersionedImage.descriptor) == {"low_memory": True}


# ------------------------------------------------------------------ export


def test_png_export_with_and_without_details(c, pid):
    job = generate(c, pid, prompt="a private description")
    aid = job["outputs"][0]
    original = c.get(f"{API}/assets/{aid}/content").content
    labelled = c.get(f"{API}/assets/{aid}/export")
    assert labelled.status_code == 200 and labelled.headers["content-type"] == "image/png"
    seed = job["inputs"]["seed"]
    assert f'filename="rasva-image-seed-{seed}.png"' in labelled.headers["content-disposition"]
    text = image_export.text_chunks(labelled.content)
    assert "AI-generated" in text["Comment"] and f"Seed {seed}" in text["Comment"]
    assert "Test versioned image" in text["Source"]
    assert b"private description" not in labelled.content

    bare = c.get(f"{API}/assets/{aid}/export", params={"details": "false"}).content
    assert image_export.text_chunks(bare) == {}
    assert bare == original  # the engine wrote no text, so nothing else changed

    prov = c.get(f"{API}/assets/{aid}/provenance").json()["details"]
    assert prov == text


def test_png_export_strips_existing_metadata_and_refuses_non_png(c, pid):
    noisy = image_export.export_png(png_bytes((1, 2, 3)), {"Comment": "old secret", "Author": "x"})
    assert image_export.text_chunks(noisy)["Comment"] == "old secret"
    clean = image_export.export_png(noisy, None)
    assert image_export.text_chunks(clean) == {} and clean == png_bytes((1, 2, 3))
    with pytest.raises(image_export.ExportError):
        image_export.export_png(b"not a png", None)
    with pytest.raises(image_export.ExportError):
        image_export.export_png(png_bytes((1, 2, 3))[:-5], None)

    files = {"file": ("n.txt", b"hello", "text/plain")}
    r = c.post(f"{API}/projects/{pid}/assets", files=files, data={"kind": "import"})
    assert c.get(f"{API}/assets/{r.json()['id']}/export").status_code == 422
    ref = upload_reference(c, pid)
    assert c.get(f"{API}/assets/{ref}/provenance").json()["details"] == {}
