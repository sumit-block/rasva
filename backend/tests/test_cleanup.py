import os
import time

from rasva.db import connect
from rasva.services import cleanup
from tests.conftest import make_client, wait_for, wav_bytes
from tests.test_chapters import NARRATOR, SETTINGS, generate, settle

TERMINAL = {"completed", "failed", "canceled", "interrupted"}
SCRIPT = "First line.\nSecond line.\nThird line."


def asset_exists(c, asset_id):
    return c.get(f"/api/v1/assets/{asset_id}/content").status_code == 200


def build_project(c, settings):
    p = c.post("/api/v1/projects", json={"name": "Book", "module": "audio"}).json()
    ch = c.post(f"/api/v1/projects/{p['id']}/chapters",
                json={"title": "Ch", "script": SCRIPT, "settings": SETTINGS}).json()  # fmt: skip
    generate(c, ch["id"])
    ch = settle(c, ch["id"])
    s0, s1 = ch["segments"][0], ch["segments"][1]
    old_take_asset = s0["current"]["asset_id"]

    # Segment 0: regenerate and use the new version -> old version is superseded.
    c.post(f"/api/v1/segments/{s0['id']}/regenerate")
    ch = settle(c, ch["id"])
    new_take = ch["segments"][0]["takes"][-1]
    c.post(f"/api/v1/segments/{s0['id']}/takes/{new_take['id']}/use")
    # Segment 1: regenerate but don't decide yet -> must be kept.
    c.post(f"/api/v1/segments/{s1['id']}/regenerate")
    ch = settle(c, ch["id"])
    pending_review = ch["segments"][1]["takes"][-1]["asset_id"]
    assert ch["segments"][1]["status"] == "needs_review"

    # Remove the third sentence -> its audio is orphaned.
    orphan = ch["segments"][2]["current"]["asset_id"]
    ch = c.patch(f"/api/v1/chapters/{ch['id']}",
                 json={"script": "First line.\nSecond line."}).json()  # fmt: skip

    preview = c.post("/api/v1/jobs", json={
        "project_id": p["id"], "task": "audio.narrate", "engine_id": "test-narrator",
        "inputs": {"text": "Hi.", "voice_id": "af_heart", "preview": True},
    }).json()  # fmt: skip
    preview = wait_for(c, preview["id"], TERMINAL)

    exports = []
    for _ in range(2):
        job = c.post(f"/api/v1/chapters/{ch['id']}/export", json={}).json()
        exports.append(wait_for(c, job["id"], TERMINAL))

    ref = c.post(f"/api/v1/projects/{p['id']}/assets",
                 files={"file": ("voice.wav", wav_bytes(), "audio/wav")}).json()  # fmt: skip
    return {
        "project": p, "chapter": ch, "old_take": old_take_asset, "pending_review": pending_review,
        "orphan": orphan, "preview": preview["outputs"][0], "old_export": exports[0]["outputs"][0],
        "latest_export": exports[1]["outputs"][0], "reference": ref["id"],
    }  # fmt: skip


def test_cleanup_reclaims_only_what_is_safe(settings):
    with make_client(settings, extra_engines=(NARRATOR,)) as c:
        x = build_project(c, settings)
        paths = settings.paths
        old = time.time() - 2 * 3600
        stray_upload = paths.uploads_tmp / "ast_x.part"
        stray_upload.write_bytes(b"x" * 1000)
        os.utime(stray_upload, (old, old))
        (paths.staging / "job_gone").mkdir(parents=True)
        (paths.staging / "job_gone" / "tmp.wav").write_bytes(b"y" * 500)
        untracked = paths.projects / x["project"]["id"] / "outputs" / "ghost.wav"
        untracked.write_bytes(b"z" * 300)
        os.utime(untracked, (old, old))

        summary = {g["id"]: g for g in c.get("/api/v1/storage/cleanup").json()["groups"]}
        assert summary["previews"]["count"] == 0  # today's previews are kept by default
        assert summary["superseded_takes"]["count"] == 1
        assert summary["orphaned_narration"]["count"] == 1
        assert summary["old_exports"]["count"] == 1
        assert summary["temp"]["count"] == 2
        assert summary["untracked"]["count"] == 1
        assert summary["temp"]["default"] is True and summary["old_exports"]["default"] is False

        conn = connect(paths.db_file)
        later = time.time() + 2 * 86_400
        assert len(cleanup.plan(conn, paths, now=later).groups["previews"]) == 1
        result = cleanup.execute(conn, paths, list(cleanup.GROUPS), now=later)
        conn.close()
        assert result["freed_bytes"] > 0
        assert set(result["deleted"]) >= {"previews", "superseded_takes", "orphaned_narration",
                                          "old_exports", "temp", "untracked"}  # fmt: skip

        for gone in ("old_take", "orphan", "preview", "old_export"):
            assert not asset_exists(c, x[gone]), gone
        for kept in ("pending_review", "latest_export", "reference"):
            assert asset_exists(c, x[kept]), kept
        assert not stray_upload.exists() and not untracked.exists()

        # The chapter still has every sentence it uses, so it still exports.
        ch = c.get(f"/api/v1/chapters/{x['chapter']['id']}").json()
        assert ch["summary"]["exportable"] and ch["segments"][1]["status"] == "needs_review"
        assert all(asset_exists(c, s["current"]["asset_id"]) for s in ch["segments"])
        job = c.post(f"/api/v1/chapters/{ch['id']}/export", json={}).json()
        assert wait_for(c, job["id"], TERMINAL)["state"] == "completed"
        # Job records no longer point at deleted files.
        jobs = c.get("/api/v1/jobs?limit=200").json()
        preview_job = next(j for j in jobs if j["inputs"].get("preview"))
        assert preview_job["outputs"] == []

        # Nothing left to reclaim except today's work; a second run is a no-op.
        again = {g["id"]: g["count"] for g in c.get("/api/v1/storage/cleanup").json()["groups"]}
        assert again["superseded_takes"] == again["orphaned_narration"] == again["untracked"] == 0


def test_cleanup_reports_missing_files_and_rejects_unknown_groups(settings):
    with make_client(settings, extra_engines=(NARRATOR,)) as c:
        p = c.post("/api/v1/projects", json={"name": "p", "module": "audio"}).json()
        ref = c.post(f"/api/v1/projects/{p['id']}/assets",
                     files={"file": ("a.wav", wav_bytes(), "audio/wav")}).json()  # fmt: skip
        (settings.paths.projects / ref["rel_path"]).unlink()
        assert c.get("/api/v1/storage/cleanup").json()["missing_files"] == 1
        r = c.post("/api/v1/storage/cleanup", json={"groups": ["everything"]})
        assert r.status_code == 422
        assert c.post("/api/v1/storage/cleanup", json={"groups": []}).status_code == 422
        # Uploads are never offered, even when everything is selected.
        r = c.post("/api/v1/storage/cleanup", json={"groups": list(cleanup.GROUPS)}).json()
        assert c.get(f"/api/v1/assets/{ref['id']}").status_code == 200
        assert r["freed_bytes"] == 0
