"""Memory guard before cold engine starts (PRD §24), with mocked memory readings."""

import time

import pytest

from tests.conftest import make_client, wait_for

GB = 1024**3
HEAVY = "tests.heavy_engine:HeavyEngine"
TERMINAL = {"completed", "failed", "canceled", "interrupted"}
WAITING = "Waiting for memory"


@pytest.fixture
def client(settings):
    """Most tests here cover the "wait" policy; the default is "start"."""
    with make_client(settings, extra_engines=(HEAVY,)) as c:
        r = c.patch("/api/v1/settings", json={"memory.when_short": "wait"})
        assert r.status_code == 200, r.text
        yield c


def use_start_policy(client):
    r = client.patch("/api/v1/settings", json={"memory.when_short": "start"})
    assert r.status_code == 200, r.text


def submit(client, project, engine_id="test-heavy", task="test.run", **inputs):
    r = client.post(
        "/api/v1/jobs",
        json={"project_id": project["id"], "task": task, "engine_id": engine_id, "inputs": inputs},
    )
    assert r.status_code == 201, r.text
    return r.json()


def orchestrator(client):
    return client.app.state.rasva.orchestrator


def status(client):
    return client.get("/api/v1/system/status").json()


def wait_until_waiting(client, job_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["state"] == "queued" and (job["stage_detail"] or "").startswith(WAITING):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never waited for memory: {job}")


def test_starts_when_memory_is_enough(client, project, memory):
    memory.available = 6 * GB  # 4 GB peak + 1 GB default margin = 5 GB
    done = wait_for(client, submit(client, project)["id"], TERMINAL)
    assert done["state"] == "completed", done
    assert done["metrics"]["cold_start"] is True
    assert status(client)["orchestrator"]["memory_wait"] is None


def test_waits_then_starts_when_memory_frees(client, project, memory):
    memory.available = 3 * GB
    job = submit(client, project)
    waiting = wait_until_waiting(client, job["id"])
    assert "Test heavy engine needs about 5 GB free" in waiting["stage_detail"]
    wait = status(client)["orchestrator"]["memory_wait"]
    assert wait["job_id"] == job["id"]
    assert wait["needed_bytes"] == 5 * GB and wait["available_bytes"] == 3 * GB
    # Re-checked periodically, not only once.
    reads = memory.reads
    time.sleep(0.3)
    assert memory.reads > reads
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["state"] == "queued"

    memory.available = 8 * GB
    done = wait_for(client, job["id"], TERMINAL)
    assert done["state"] == "completed", done
    # Waiting adds no state of its own; the job simply started later.
    assert [e["state"] for e in done["events"]] == [
        "queued",
        "preparing",
        "loading_model",
        "processing",
        "finalizing",
        "completed",
    ]
    assert status(client)["orchestrator"]["memory_wait"] is None


def test_unloads_idle_other_engine_before_waiting(client, project, memory):
    first = wait_for(client, submit(client, project, engine_id="test-fault")["id"], TERMINAL)
    assert first["state"] == "completed"
    assert status(client)["orchestrator"]["resident_engine"] == "test-fault"

    # Memory is short only while the other engine's worker is still resident.
    orch = orchestrator(client)
    memory.hook = lambda: 2 * GB if orch.status()["resident_engine"] == "test-fault" else 8 * GB
    done = wait_for(client, submit(client, project)["id"], TERMINAL)
    assert done["state"] == "completed", done
    assert not any((e["detail"] or "").startswith(WAITING) for e in done["events"])
    assert status(client)["orchestrator"]["resident_engine"] == "test-heavy"


def test_unloads_other_engine_and_still_waits_if_short(client, project, memory):
    wait_for(client, submit(client, project, engine_id="test-fault")["id"], TERMINAL)
    memory.available = 2 * GB
    job = submit(client, project)
    wait_until_waiting(client, job["id"])
    assert status(client)["orchestrator"]["resident_engine"] is None
    memory.available = 8 * GB
    assert wait_for(client, job["id"], TERMINAL)["state"] == "completed"


def test_warm_jobs_skip_the_check(client, project, memory):
    assert wait_for(client, submit(client, project)["id"], TERMINAL)["state"] == "completed"
    memory.available = 100 * 1024**2  # far too little for a cold start
    reads = memory.reads
    warm = wait_for(client, submit(client, project)["id"], TERMINAL)
    assert warm["state"] == "completed", warm
    assert warm["metrics"]["cold_start"] is False
    assert memory.reads == reads


def test_light_jobs_run_past_a_waiting_job(client, project, memory):
    memory.available = 1 * GB
    heavy = submit(client, project)
    wait_until_waiting(client, heavy["id"])
    # The tone engine declares no memory peak, so it needs no check.
    tone = submit(client, project, engine_id="diagnostic-tone", task="diagnostic.tone")
    assert wait_for(client, tone["id"], TERMINAL)["state"] == "completed"
    assert client.get(f"/api/v1/jobs/{heavy['id']}").json()["state"] == "queued"
    memory.available = 8 * GB
    assert wait_for(client, heavy["id"], TERMINAL)["state"] == "completed"


def test_heavy_jobs_keep_queue_order_while_waiting(client, project, memory):
    memory.available = 1 * GB
    first = submit(client, project)
    wait_until_waiting(client, first["id"])
    second = submit(client, project)
    time.sleep(0.3)
    assert client.get(f"/api/v1/jobs/{second['id']}").json()["state"] == "queued"
    memory.available = 8 * GB
    a = wait_for(client, first["id"], TERMINAL)
    b = wait_for(client, second["id"], TERMINAL)
    assert a["started_at"] <= b["started_at"]


def test_cancel_a_waiting_job(client, project, memory):
    memory.available = 1 * GB
    job = submit(client, project)
    wait_until_waiting(client, job["id"])
    r = client.post(f"/api/v1/jobs/{job['id']}/cancel")
    assert r.status_code == 200 and r.json()["state"] == "canceled"
    deadline = time.monotonic() + 5
    while status(client)["orchestrator"]["memory_wait"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert status(client)["orchestrator"]["memory_wait"] is None


def test_fails_when_the_mac_can_never_fit_it(client, project, memory):
    memory.total = 4 * GB
    memory.available = 3 * GB
    done = wait_for(client, submit(client, project)["id"], TERMINAL)
    assert done["state"] == "failed"
    assert done["error_code"] == "not_enough_memory"
    assert "more than this Mac has (4 GB)" in done["error_message"]
    # Retry stays available like any failed job.
    assert client.post(f"/api/v1/jobs/{done['id']}/retry").status_code == 201


def test_safety_margin_is_a_setting(client, project, memory):
    assert client.get("/api/v1/settings").json()["memory.safety_margin_bytes"] == 1 * GB
    memory.available = int(4.5 * GB)  # enough for the 4 GB peak, not with the margin
    job = submit(client, project)
    waiting = wait_until_waiting(client, job["id"])
    assert "about 5 GB free" in waiting["stage_detail"]
    r = client.patch("/api/v1/settings", json={"memory.safety_margin_bytes": 0})
    assert r.status_code == 200, r.text
    assert wait_for(client, job["id"], TERMINAL)["state"] == "completed"
    too_big = client.patch("/api/v1/settings", json={"memory.safety_margin_bytes": 9 * GB})
    assert too_big.status_code in (400, 422)


def test_status_reports_memory(client, memory):
    memory.available = 1 * GB
    body = status(client)["memory"]
    assert body == {"total_bytes": 16 * GB, "available_bytes": 1 * GB, "level": "low"}
    memory.available = 3 * GB
    assert status(client)["memory"]["level"] == "tight"
    memory.available = 8 * GB
    assert status(client)["memory"]["level"] == "ok"


def test_default_policy_is_start(settings):
    from rasva.services.settings_store import DEFAULTS

    assert DEFAULTS["memory.when_short"] == "start"


def test_starts_when_short_and_says_it_may_be_slow(client, project, memory):
    use_start_policy(client)
    memory.available = 3 * GB  # 4 GB peak + 1 GB margin wanted
    done = wait_for(client, submit(client, project)["id"], TERMINAL)
    assert done["state"] == "completed", done
    assert done["metrics"]["memory_short"] == {"needed_bytes": 5 * GB, "available_bytes": 3 * GB}
    loading = next(e for e in done["events"] if e["state"] == "loading_model")
    expected = "Memory is short (3 GB free, about 5 GB wanted), so this may be slow"
    assert expected in loading["detail"]
    assert not any((e["detail"] or "").startswith(WAITING) for e in done["events"])
    assert status(client)["orchestrator"]["memory_wait"] is None


def test_start_policy_still_fails_what_can_never_fit(client, project, memory):
    use_start_policy(client)
    memory.total = 4 * GB
    done = wait_for(client, submit(client, project)["id"], TERMINAL)
    assert done["state"] == "failed"
    assert done["error_code"] == "not_enough_memory"


def test_enough_memory_has_no_short_note(client, project, memory):
    use_start_policy(client)
    memory.available = 8 * GB
    done = wait_for(client, submit(client, project)["id"], TERMINAL)
    assert "memory_short" not in done["metrics"]


def test_when_short_rejects_unknown_values(client):
    r = client.patch("/api/v1/settings", json={"memory.when_short": "maybe"})
    assert r.status_code in (400, 422)
