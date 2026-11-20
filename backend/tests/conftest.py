from __future__ import annotations

import io
import math
import struct
import time
import wave
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rasva.app import create_app
from rasva.config import Paths, Settings

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        paths=Paths(tmp_path / "home"),
        enable_test_engines=True,
        cancel_grace_s=1.0,
        engine_idle_unload_s=300.0,
        max_upload_bytes=5 * 1024 * 1024,
    )


def make_client(settings: Settings, extra_engines: tuple[str, ...] = (), **kw) -> TestClient:
    app = create_app(settings, trust_test_client=True, extra_engines=extra_engines)
    return TestClient(app, base_url=BASE, client=("127.0.0.1", 50000), **kw)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with make_client(settings) as c:
        yield c


@pytest.fixture
def project(client: TestClient) -> dict:
    r = client.post("/api/v1/projects", json={"name": "Mahabharata ch.1", "module": "audio"})
    assert r.status_code == 201, r.text
    return r.json()


def wait_for(client: TestClient, job_id: str, states: set[str], timeout: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["state"] in states:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} stuck in {job['state']}, wanted {states}")


def wav_bytes(seconds: float = 0.1, rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(
            b"".join(
                struct.pack("<h", int(1000 * math.sin(i / 10))) for i in range(int(seconds * rate))
            )
        )
    return buf.getvalue()
