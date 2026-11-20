"""Engine workers cannot reach the network during load or generation (PRD §21.5).

These go through the real orchestrator -> subprocess worker path.
"""

import json
import socket
from collections.abc import Iterator

import pytest

from rasva.engines import netguard
from tests.conftest import make_client, wait_for

PROBE = "tests.net_probe:NetProbe"
TERMINAL = {"completed", "failed", "canceled", "interrupted"}
BLOCKED_KEYS = ("import", "load", "connect", "connect_ex", "create_connection", "getaddrinfo")


@pytest.fixture
def listener() -> Iterator[socket.socket]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.2)
    yield srv
    srv.close()


def _accepted(srv: socket.socket) -> int:
    count = 0
    while True:
        try:
            conn, _ = srv.accept()
        except (TimeoutError, OSError):
            return count
        conn.close()
        count += 1


def _probe(settings, monkeypatch, port: int, runs: int = 1) -> tuple[dict, list[dict]]:
    """Run the probe ``runs`` times on one worker; outcomes of the first run."""
    monkeypatch.setenv("RASVA_TEST_PROBE_PORT", str(port))
    metrics = []
    with make_client(settings, extra_engines=(PROBE,)) as c:
        project = c.post("/api/v1/projects", json={"name": "Net", "module": "audio"}).json()
        for _ in range(runs):
            job = c.post("/api/v1/jobs", json={
                "project_id": project["id"], "task": "test.run",
                "engine_id": "test-net-probe", "inputs": {"port": port},
            }).json()  # fmt: skip
            done = wait_for(c, job["id"], TERMINAL)
            assert done["state"] == "completed", done
            metrics.append(done["metrics"])
            if len(metrics) == 1:
                body = c.get(f"/api/v1/assets/{done['outputs'][0]}/content").content
    return json.loads(body), metrics


def test_worker_cannot_open_network_connections(settings, monkeypatch, listener):
    monkeypatch.delenv(netguard.ALLOW_ENV, raising=False)
    port = listener.getsockname()[1]
    outcomes, (cold, warm) = _probe(settings, monkeypatch, port, runs=2)

    for key in BLOCKED_KEYS:
        assert outcomes[key].startswith("blocked:"), (key, outcomes)
    assert outcomes["getaddrinfo"] == "blocked:LookupBlocked"
    assert outcomes["socketpair"] == "ok"  # local IPC is unaffected
    assert _accepted(listener) == 0
    # Counted per job: the cold start includes import and load, the warm run does not.
    assert cold["network_blocked"] == len(BLOCKED_KEYS)
    assert warm["network_blocked"] == len(BLOCKED_KEYS) - 2

    log = (settings.paths.logs / "engines" / "test-net-probe.log").read_text()
    assert f"rasva-netguard: blocked network connection to 127.0.0.1:{port}" in log
    assert "rasva-netguard: blocked network lookup of example.com" in log
    assert "net_probe.py:" in log  # names the calling code


def test_escape_hatch_allows_network(settings, monkeypatch, listener):
    """Positive control: without the guard the same probe does connect."""
    monkeypatch.setenv(netguard.ALLOW_ENV, "1")
    port = listener.getsockname()[1]
    outcomes, (metrics,) = _probe(settings, monkeypatch, port)

    for key in ("import", "load", "connect", "connect_ex", "create_connection"):
        assert outcomes[key] == "connected", (key, outcomes)
    assert _accepted(listener) >= 5
    assert "network_blocked" not in metrics
    log = (settings.paths.logs / "engines" / "test-net-probe.log").read_text()
    assert "network access is NOT blocked" in log


def test_normal_engines_still_work_under_the_guard(client, project, monkeypatch):
    monkeypatch.delenv(netguard.ALLOW_ENV, raising=False)
    job = client.post("/api/v1/jobs", json={
        "project_id": project["id"], "task": "diagnostic.tone", "inputs": {"duration_s": 0.2},
    }).json()  # fmt: skip
    done = wait_for(client, job["id"], TERMINAL)
    assert done["state"] == "completed", done
    assert "network_blocked" not in done["metrics"]
