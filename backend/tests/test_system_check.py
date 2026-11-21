"""AI acceleration is reported from the engines' own environments (ADR-0003),
not from the app's Python, which never contains MLX or PyTorch."""

from pathlib import Path

from rasva.services import system_check


def make_env(envs: Path, engine_id: str, *modules: str) -> None:
    site = envs / engine_id / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    for m in modules:
        (site / m).mkdir()


def accel(result: dict) -> dict:
    return next(c for c in result["checks"] if c["id"] == "accelerators")


def test_no_engines_yet(tmp_path):
    c = accel(system_check.run_checks(10**12, 0, tmp_path / "envs"))
    assert c["status"] == "warn" and "No engine installed yet" in c["message"]


def test_frameworks_come_from_engine_environments(tmp_path):
    envs = tmp_path / "envs"
    make_env(envs, "flux2-klein-4b", "mlx", "torch")
    make_env(envs, "kokoro-82m", "torch")
    make_env(envs, "dwpose", "onnxruntime")
    (envs / ".sd15.partial").mkdir()  # a build in progress is not an installed engine
    names = {"flux2-klein-4b": "FLUX.2 Klein", "kokoro-82m": "Kokoro"}
    result = system_check.run_checks(10**12, 0, envs, names)
    c = accel(result)
    assert c["status"] == "pass"
    assert "MLX (FLUX.2 Klein)" in c["message"]
    assert "PyTorch (Metal) (FLUX.2 Klein, Kokoro)" in c["message"]
    assert "ONNX Runtime (dwpose)" in c["message"]  # no display name: the id is shown
    assert result["facts"]["mlx_available"] is True


def test_check_endpoint_reads_the_real_envs_dir(client, settings):
    make_env(settings.paths.envs, "kokoro-82m", "torch")
    body = client.get("/api/v1/system/check").json()
    assert "PyTorch (Metal) (Kokoro)" in accel(body)["message"]
