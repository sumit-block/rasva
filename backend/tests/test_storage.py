from rasva.config import GB, Paths
from rasva.services.storage import StorageService


def _svc(tmp_path):
    paths = Paths(tmp_path / "home")
    paths.ensure()
    return paths, StorageService(paths, ttl_s=0)


def test_counts_redirected_caches(tmp_path):
    paths, svc = _svc(tmp_path)
    (paths.cache / "huggingface" / "blob").write_bytes(b"x" * 100_000)
    u = svc.usage(50 * GB, 0, force=True)
    assert u.categories["caches"] >= 100_000


def test_blocks_over_budget(tmp_path):
    _, svc = _svc(tmp_path)
    r = svc.preflight(60 * GB, "download", budget_bytes=50 * GB, os_reserve_bytes=0)
    assert not r.allowed
    assert "storage is left" in r.reasons[0]


def test_warns_above_80_percent(tmp_path):
    _, svc = _svc(tmp_path)
    r = svc.preflight(42 * GB, "generation", budget_bytes=50 * GB, os_reserve_bytes=0)
    # Disk may be smaller than 42 GB in CI; only assert the budget logic when space allows.
    if r.allowed:
        assert r.warnings


def test_blocks_downloads_when_nearly_full(tmp_path, monkeypatch):
    paths, svc = _svc(tmp_path)
    real = svc._categories
    monkeypatch.setattr(svc, "_categories", lambda: {**real(), "audio_models": int(46 * GB)})
    r = svc.preflight(1, "download", budget_bytes=50 * GB, os_reserve_bytes=0)
    assert not r.allowed and "90%" in r.reasons[0]
    # Small generations are still allowed at that level.
    assert svc.preflight(1, "generation", budget_bytes=50 * GB, os_reserve_bytes=0).allowed


def test_os_reserve_is_independent_of_budget(tmp_path):
    _, svc = _svc(tmp_path)
    r = svc.preflight(1, "upload", budget_bytes=50 * GB, os_reserve_bytes=10_000 * GB)
    assert not r.allowed
    assert any("macOS" in reason for reason in r.reasons)
