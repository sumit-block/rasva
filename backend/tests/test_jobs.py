import sqlite3

from rasva.db import utcnow
from tests.conftest import make_client, wait_for

TERMINAL = {"completed", "failed", "canceled", "interrupted"}


def submit(client, project, **body):
    payload = {"project_id": project["id"], **body}
    r = client.post("/api/v1/jobs", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_tone_job_end_to_end(client, project):
    job = submit(client, project, task="diagnostic.tone", inputs={"duration_s": 0.5})
    assert job["engine_id"] == "diagnostic-tone"  # chosen automatically
    done = wait_for(client, job["id"], TERMINAL)
    assert done["state"] == "completed", done
    assert done["progress"] == 1.0
    assert done["metrics"]["cold_start"] is True and "run_s" in done["metrics"]
    states = [e["state"] for e in done["events"]]
    assert states == [
        "queued",
        "preparing",
        "loading_model",
        "processing",
        "finalizing",
        "completed",
    ]

    asset_id = done["outputs"][0]
    wav = client.get(f"/api/v1/assets/{asset_id}/content").content
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"

    # Second job reuses the warm worker.
    again = wait_for(client, submit(client, project, task="diagnostic.tone")["id"], TERMINAL)
    assert again["metrics"]["cold_start"] is False


def test_invalid_input_rejected_before_queueing(client, project):
    r = client.post(
        "/api/v1/jobs",
        json={"project_id": project["id"], "task": "diagnostic.tone", "inputs": {"duration_s": 99}},
    )
    assert r.status_code == 422
    assert "Duration" in r.json()["error"]["message"]
    assert client.get("/api/v1/jobs").json() == []


def test_engine_error_is_user_readable(client, project):
    job = submit(
        client, project, task="test.run", engine_id="test-fault", inputs={"behaviour": "error"}
    )
    done = wait_for(client, job["id"], TERMINAL)
    assert done["state"] == "failed"
    assert done["error_message"] == "The test engine failed on purpose."


def test_crash_fails_only_that_job_and_recovers(client, project):
    crash = submit(
        client, project, task="test.run", engine_id="test-fault", inputs={"behaviour": "crash"}
    )
    done = wait_for(client, crash["id"], TERMINAL)
    assert done["state"] == "failed" and done["error_code"] == "engine_crashed"
    ok = submit(
        client, project, task="test.run", engine_id="test-fault", inputs={"behaviour": "ok"}
    )
    assert wait_for(client, ok["id"], TERMINAL)["state"] == "completed"


def test_cooperative_cancel(client, project):
    job = submit(
        client,
        project,
        task="test.run",
        engine_id="test-fault",
        inputs={"behaviour": "slow", "seconds": 10},
    )
    wait_for(client, job["id"], {"processing"})
    client.post(f"/api/v1/jobs/{job['id']}/cancel")
    done = wait_for(client, job["id"], TERMINAL, timeout=5)
    assert done["state"] == "canceled"


def test_cancel_kills_unresponsive_engine(client, project):
    job = submit(
        client, project, task="test.run", engine_id="test-fault", inputs={"behaviour": "hang"}
    )
    wait_for(client, job["id"], {"processing"})
    client.post(f"/api/v1/jobs/{job['id']}/cancel")
    done = wait_for(client, job["id"], TERMINAL, timeout=10)
    assert done["state"] == "canceled"
    # The queue keeps working after a forced kill.
    nxt = submit(client, project, task="diagnostic.tone", inputs={"duration_s": 0.2})
    assert wait_for(client, nxt["id"], TERMINAL)["state"] == "completed"


def test_cancel_queued_job_never_runs(client, project):
    blocker = submit(
        client,
        project,
        task="test.run",
        engine_id="test-fault",
        inputs={"behaviour": "slow", "seconds": 1.5},
    )
    queued = submit(client, project, task="diagnostic.tone")
    r = client.post(f"/api/v1/jobs/{queued['id']}/cancel")
    assert r.json()["state"] == "canceled"
    wait_for(client, blocker["id"], TERMINAL)
    assert client.get(f"/api/v1/jobs/{queued['id']}").json()["started_at"] is None


def test_engine_cannot_write_outside_staging(client, project, settings):
    job = submit(
        client, project, task="test.run", engine_id="test-fault", inputs={"behaviour": "bad_output"}
    )
    done = wait_for(client, job["id"], TERMINAL)
    assert done["state"] == "failed" and done["error_code"] == "invalid_output"
    assert not list(settings.paths.home.rglob("escape.txt"))


def test_retry_creates_linked_job(client, project):
    job = submit(
        client, project, task="test.run", engine_id="test-fault", inputs={"behaviour": "error"}
    )
    wait_for(client, job["id"], TERMINAL)
    r = client.post(f"/api/v1/jobs/{job['id']}/retry")
    assert r.status_code == 201 and r.json()["retry_of"] == job["id"]
    ok = submit(client, project, task="diagnostic.tone")
    wait_for(client, ok["id"], TERMINAL)
    assert client.post(f"/api/v1/jobs/{ok['id']}/retry").status_code == 409


def test_one_engine_resident_at_a_time(client, project):
    a = submit(client, project, task="diagnostic.tone", inputs={"duration_s": 0.2})
    wait_for(client, a["id"], TERMINAL)
    assert (
        client.get("/api/v1/system/status").json()["orchestrator"]["resident_engine"]
        == "diagnostic-tone"
    )
    b = submit(client, project, task="test.run", engine_id="test-fault")
    wait_for(client, b["id"], TERMINAL)
    status = client.get("/api/v1/system/status").json()["orchestrator"]
    assert status["resident_engine"] == "test-fault"


def test_restart_marks_running_jobs_interrupted(settings):
    with make_client(settings) as c:
        p = c.post("/api/v1/projects", json={"name": "p", "module": "audio"}).json()
    # Simulate a crash mid-generation: a job left in "processing" in the database.
    conn = sqlite3.connect(settings.paths.db_file)
    now = utcnow()
    conn.execute(
        "INSERT INTO jobs(id, project_id, task, engine_id, state, created_at, updated_at)"
        " VALUES ('job_stale', ?, 'diagnostic.tone', 'diagnostic-tone', 'processing', ?, ?)",
        (p["id"], now, now),
    )
    conn.commit()
    conn.close()
    with make_client(settings) as c:
        job = c.get("/api/v1/jobs/job_stale").json()
        assert job["state"] == "interrupted"
        assert "retry" in job["error_message"]
        assert c.get(f"/api/v1/projects/{p['id']}").status_code == 200


def test_models_lists_user_engines_only(client):
    models = client.get("/api/v1/models").json()
    ids = [m["id"] for m in models]
    assert "diagnostic-tone" in ids and "test-fault" not in ids
    tone = next(m for m in models if m["id"] == "diagnostic-tone")
    assert tone["install_state"] == "built_in" and tone["license_class"] == "permissive"
