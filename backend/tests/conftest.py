from __future__ import annotations

import io
import math
import struct
import time
import wave
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rasva.app import create_app
from rasva.config import Paths, Settings
from rasva.engines.contract import ModelArtifact, SystemFacts
from rasva.services import memory as memory_svc
from rasva.services import system_check

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        paths=Paths(tmp_path / "home"),
        enable_test_engines=True,
        cancel_grace_s=1.0,
        engine_idle_unload_s=300.0,
        max_upload_bytes=5 * 1024 * 1024,
        memory_recheck_s=0.05,
    )


class FakeMemory:
    """Stands in for psutil so the memory guard never depends on the test host.

    Plenty of memory by default. Tests change ``available`` or set ``hook`` to
    compute a reading from the app's state at the moment it is taken.
    """

    def __init__(self) -> None:
        self.total = 16 * 1024**3
        self.available = 12 * 1024**3
        self.hook: Callable[[], int] | None = None
        self.reads = 0

    def __call__(self) -> memory_svc.MemoryReading:
        self.reads += 1
        available = self.hook() if self.hook else self.available
        return memory_svc.MemoryReading(total_bytes=self.total, available_bytes=available)


@pytest.fixture(autouse=True)
def memory(monkeypatch: pytest.MonkeyPatch) -> FakeMemory:
    fake = FakeMemory()
    monkeypatch.setattr(memory_svc, "read", fake)
    return fake


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


FAKE_ARTIFACT = ModelArtifact(
    path="weights.bin",
    url="https://huggingface.co/example/model/resolve/0123456789abcdef0123456789abcdef01234567/weights.bin",
    sha256="0" * 64,
    size_bytes=1,
)


def set_host(monkeypatch: pytest.MonkeyPatch, *, apple_silicon: bool) -> None:
    """Make compatibility checks independent of the machine running the tests."""
    facts = SystemFacts(
        os="macOS" if apple_silicon else "Linux",
        arch="arm64" if apple_silicon else "x86_64",
        total_memory_bytes=16 * 1024**3,
        apple_silicon=apple_silicon,
        mlx_available=False,
        mps_available=False,
    )
    monkeypatch.setattr(system_check, "facts", lambda: facts)


def set_pinned(monkeypatch: pytest.MonkeyPatch, engine_cls: type, *, pinned: bool) -> None:
    """Pin or unpin an engine's download list, whatever its committed manifest says.
    Call before creating the client: the registry copies descriptors at startup."""
    artifacts = [FAKE_ARTIFACT] if pinned else []
    monkeypatch.setattr(
        engine_cls, "descriptor", engine_cls.descriptor.model_copy(update={"artifacts": artifacts})
    )
