import json
import time
from importlib import resources

import pytest

from rasva.audio.text import script_counts
from rasva.engines.sd15 import StableDiffusion15Engine
from rasva.experiments.scoring import (
    IMAGE_PROFILE,
    IMAGE_REVIEW_DIMENSIONS,
    CandidateView,
    RunView,
    summarize,
)
from tests.conftest import make_client, wait_for

GB = 1024**3
NARRATOR = "tests.fake_narrator:FakeNarrator"
PAINTER = "tests.fake_painter:FakePainter"
TERMINAL = {"completed", "failed", "canceled", "interrupted"}


# ------------------------------------------------------------------ scoring


def run(status="completed", rtf=0.2, peak=2 * GB, **scores):
    return RunView(status, {"real_time_factor": rtf, "peak_rss_bytes": peak, "load_s": 1.0}, scores)


def test_no_listening_no_ranking():
    s = summarize([CandidateView("a", "e", "permissive", 100, [run()])], 16 * GB)[0]
    assert s.score_experiment is None and s.score_final is None and s.complete is False


def test_weights_and_relative_normalisation():
    fast = CandidateView("fast", "e1", "permissive", 1 * GB,
                         [run(rtf=0.1, pronunciation=3, naturalness=3)])  # fmt: skip
    good = CandidateView("good", "e2", "permissive", 4 * GB,
                         [run(rtf=0.4, pronunciation=5, naturalness=5)], usability=5)  # fmt: skip
    a, b = summarize([fast, good], 16 * GB)
    assert a.normalized["efficiency"] == 1.0 and b.normalized["efficiency"] == 0.25
    assert a.normalized["storage"] == 1.0 and b.normalized["storage"] == 0.25
    assert b.normalized["quality"] == 1.0 and a.normalized["quality"] == 0.5
    # Final selection weights quality at 70%, so the better-sounding engine wins there.
    assert b.score_final > a.score_final
    # Experiment weights renormalise when usability has not been rated for `fast`.
    expected_a = 100 * (0.40 * 0.5 + 0.25 * 1 + 0.15 * 1 + 0.10 * 1) / 0.90
    assert a.score_experiment == pytest.approx(expected_a, abs=0.1)
    assert a.complete and b.complete


def test_gates_override_scores():
    restricted = CandidateView("r", "e", "restricted", GB, [run(pronunciation=5)])
    hungry = CandidateView("h", "e", "permissive", GB, [run(peak=14 * GB, pronunciation=5)])
    flaky = CandidateView("f", "e", "permissive", GB,
                          [run(pronunciation=5)] + [run(status="failed")] * 2)  # fmt: skip
    pending = CandidateView("p", "e", "permissive", GB, [run(status="queued")])
    r, h, f, p = summarize([restricted, hungry, flaky, pending], 16 * GB)
    assert r.eligible is False and h.eligible is False and f.eligible is False
    assert p.eligible is None  # not enough data yet
    assert {g.id for g in r.gates if g.passed is False} == {"licence"}
    assert {g.id for g in h.gates if g.passed is False} == {"memory"}
    assert {g.id for g in f.gates if g.passed is False} == {"reliability"}


def test_skipped_runs_do_not_count_against_reliability():
    c = CandidateView("c", "e", "permissive", GB, [run(pronunciation=4), RunView("skipped")])
    s = summarize([c], 16 * GB)[0]
    assert s.skipped == 1 and s.total == 1 and s.reliability == 1.0


# ------------------------------------------------------------------ benchmark data


def test_audio_benchmark_matches_prd():
    data = json.loads(
        (resources.files("rasva.experiments.benchmarks") / "audio-v1.json").read_text("utf-8")
    )
    cases = data["cases"]
    counts = {}
    for c in cases:
        counts[c["category"]] = counts.get(c["category"], 0) + 1
    assert counts == {"hinglish": 15, "hindi": 15, "english": 10, "mixed": 10, "names": 10}
    assert len({c["id"] for c in cases}) == 60
    for c in cases:
        sc = script_counts(c["text"])
        if c["category"] in ("hinglish", "english"):
            assert sc["devanagari"] == 0, c["id"]
        elif c["category"] in ("hindi", "names"):
            assert sc["latin"] == 0, c["id"]
        else:
            assert sc["devanagari"] and sc["latin"], c["id"]
    names = " ".join(c["text"] for c in cases if c["category"] == "names")
    for name in ["कृष्ण", "अर्जुन", "कर्ण", "भीष्म", "युधिष्ठिर", "दुर्योधन", "द्रोणाचार्य",
                 "धृतराष्ट्र", "अश्वत्थामा", "कुंती"]:  # fmt: skip
        assert name in names  # PRD §10.3 priority names


# ------------------------------------------------------------------ API


def _create(c, **body):
    payload = {"name": "Voices", "candidates": [
        {"engine_id": "test-narrator", "settings": {"voice_id": "af_heart"}},
        {"engine_id": "test-narrator", "settings": {"voice_id": "hf_alpha"}},
    ], **body}  # fmt: skip
    return c.post("/api/v1/experiments", json=payload)


def test_experiment_lifecycle(settings):
    with make_client(settings, extra_engines=(NARRATOR,)) as c:
        r = _create(c, texts=["Hello there.", "नमस्ते दुनिया।"])
        assert r.status_code == 201, r.text
        exp = r.json()
        runs = exp["runs"]
        assert len(runs) == 4
        skipped = [x for x in runs if x["status"] == "skipped"]
        assert len(skipped) == 1 and "Hindi voice" in skipped[0]["skip_reason"]
        for x in runs:
            if x["job_id"]:
                wait_for(c, x["job_id"], TERMINAL)

        exp = c.get(f"/api/v1/experiments/{exp['id']}").json()
        done = [x for x in exp["runs"] if x["status"] == "completed"]
        assert len(done) == 3 and all(x["output_asset_id"] for x in done)

        target = next(x for x in done if x["candidate_key"].endswith("voice_id=hf_alpha"))
        url = f"/api/v1/experiments/{exp['id']}/runs/{target['id']}/scores"
        exp = c.put(url, json={"scores": {"pronunciation": 4, "naturalness": 5}}).json()
        summary = {s["key"]: s for s in exp["summary"]}
        assert summary[target["candidate_key"]]["quality"] == 4.5
        assert c.put(url, json={"scores": {"vibes": 3}}).status_code == 422
        assert c.put(url, json={"scores": {"pacing": 9}}).status_code == 422
        exp = c.put(url, json={"scores": {"naturalness": None}}).json()
        assert next(x for x in exp["runs"] if x["id"] == target["id"])["scores"] == {
            "pronunciation": 4
        }

        u = c.put(f"/api/v1/experiments/{exp['id']}/usability",
                  json={"candidate_key": target["candidate_key"], "score": 4})  # fmt: skip
        assert u.status_code == 200
        d = c.post(f"/api/v1/experiments/{exp['id']}/decision",
                   json={"decision": "Keep Alpha for Hindi previews."})  # fmt: skip
        assert d.json()["status"] == "decided"

        listed = c.get("/api/v1/experiments").json()[0]
        assert listed["runs"] == 4 and listed["completed"] == 3 and listed["rated"] == 1
        # Experiment output projects stay out of the Library.
        assert c.get("/api/v1/projects").json() == []


def test_benchmark_experiment_skips_unsupported_inputs(settings):
    with make_client(settings, extra_engines=(NARRATOR,)) as c:
        project = c.post("/api/v1/projects", json={"name": "p", "module": "audio"}).json()
        blocker = c.post("/api/v1/jobs", json={
            "project_id": project["id"], "task": "test.run", "engine_id": "test-fault",
            "inputs": {"behaviour": "slow", "seconds": 5},
        }).json()  # fmt: skip
        exp = _create(c, benchmark="audio-v1").json()
        by_key: dict[str, dict[str, int]] = {}
        for x in exp["runs"]:
            k = by_key.setdefault(x["candidate_key"], {"skipped": 0, "queued": 0})
            k["skipped" if x["status"] == "skipped" else "queued"] += 1
        assert by_key["test-narrator:voice_id=af_heart"] == {"skipped": 35, "queued": 25}
        assert by_key["test-narrator:voice_id=hf_alpha"] == {"skipped": 0, "queued": 60}

        exp = c.post(f"/api/v1/experiments/{exp['id']}/cancel").json()
        c.post(f"/api/v1/jobs/{blocker['id']}/cancel")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            exp = c.get(f"/api/v1/experiments/{exp['id']}").json()
            if all(x["status"] in TERMINAL | {"skipped"} for x in exp["runs"]):
                break
            time.sleep(0.1)
        assert all(x["status"] in TERMINAL | {"skipped"} for x in exp["runs"])


def test_experiment_validation(settings):
    with make_client(settings, extra_engines=(NARRATOR,)) as c:
        assert _create(c).status_code == 422  # neither benchmark nor texts
        assert _create(c, benchmark="audio-v1", texts=["x"]).status_code == 422
        r = c.post("/api/v1/experiments", json={
            "name": "x", "texts": ["hi"],
            "candidates": [{"engine_id": "kokoro-82m", "settings": {"voice_id": "af_heart"}}],
        })  # fmt: skip
        assert r.status_code == 422 and "not installed" in r.json()["error"]["message"]
        dup = {"engine_id": "test-narrator", "settings": {"voice_id": "af_heart"}}
        r = c.post("/api/v1/experiments", json={"name": "x", "texts": ["hi"],
                                                "candidates": [dup, dup]})  # fmt: skip
        assert r.status_code == 422
        assert c.get("/api/v1/experiments/exp_missing").status_code == 404
        assert c.get("/api/v1/experiments/benchmarks").json()[0]["cases"] == 60


# ------------------------------------------------------------------ images: scoring


def image_run(status="completed", secs=30.0, rss=GB // 2, gpu=8 * GB, **scores):
    metrics = {"generation_s": secs, "peak_rss_bytes": rss, "load_s": 5.0}
    if gpu is not None:
        metrics["gpu_peak_bytes"] = gpu
    return RunView(status, metrics, scores)


def test_image_scoring_uses_prd_20_2_weights():
    quick = CandidateView("quick", "e", "permissive", 2 * GB,
                          [image_run(secs=20, proportions=3, photorealism=3)])  # fmt: skip
    slow = CandidateView("slow", "e", "permissive", 2 * GB,
                         [image_run(secs=40, proportions=5, anatomical_artifacts=5)],
                         usability=5)  # fmt: skip
    a, b = summarize([quick, slow], 16 * GB, IMAGE_PROFILE)
    # Efficiency is seconds per image, best / own.
    assert a.median_generation_s == 20 and a.median_rtf is None
    assert a.normalized["efficiency"] == 1.0 and b.normalized["efficiency"] == 0.5
    assert b.normalized["quality"] == 1.0 and a.normalized["quality"] == 0.5
    # §20.2 experiment weights: 45/25/15/10/5 (usability unrated for `quick`).
    expected_a = 100 * (0.45 * 0.5 + 0.25 * 1 + 0.15 * 1 + 0.10 * 1) / 0.95
    assert a.score_experiment == pytest.approx(expected_a, abs=0.1)
    expected_b = 100 * (0.45 * 1 + 0.25 * 0.5 + 0.15 * 1 + 0.10 * 1 + 0.05 * 1)
    assert b.score_experiment == pytest.approx(expected_b, abs=0.1)
    # §20.2 final selection: 70/10/15/5.
    expected_final_b = 100 * (0.70 * 1 + 0.10 * 0.5 + 0.15 * 1 + 0.05 * 1)
    assert b.score_final == pytest.approx(expected_final_b, abs=0.1)
    # Unused dimensions (identity, pose, garment) don't stop a run counting as rated.
    assert a.complete and b.complete
    assert set(b.quality_by_dimension) == {"proportions", "anatomical_artifacts"}


def test_image_memory_gate_counts_gpu_memory():
    # 0.5 GB RSS alone would pass; with 11.8 GB of GPU memory it is over 75% of 16 GB.
    hungry = CandidateView("h", "e", "permissive", GB,
                           [image_run(gpu=int(11.8 * GB), proportions=4)])  # fmt: skip
    fits = CandidateView("f", "e", "permissive", GB, [image_run(gpu=8 * GB, proportions=4)])
    cpu = CandidateView("c", "e", "permissive", GB,
                        [image_run(gpu=None, rss=6 * GB, proportions=4)])  # fmt: skip
    h, f, c = summarize([hungry, fits, cpu], 16 * GB, IMAGE_PROFILE)
    assert h.peak_memory_bytes == GB // 2 + int(11.8 * GB) and h.peak_gpu_bytes == int(11.8 * GB)
    assert {g.id for g in h.gates if g.passed is False} == {"memory"}
    assert f.eligible is True and f.peak_memory_bytes == GB // 2 + 8 * GB
    # Without a GPU measurement (CPU run), RSS alone is the peak.
    assert c.peak_memory_bytes == 6 * GB and c.peak_gpu_bytes is None and c.eligible is True


def test_image_gates_still_override_scores():
    restricted = CandidateView("r", "e", "restricted", GB, [image_run(photorealism=5)])
    flaky = CandidateView("f", "e", "permissive", GB,
                          [image_run(photorealism=5)] + [image_run(status="failed")])  # fmt: skip
    r, f = summarize([restricted, flaky], 16 * GB, IMAGE_PROFILE)
    assert r.score_final is not None and r.eligible is False
    assert {g.id for g in r.gates if g.passed is False} == {"licence"}
    assert {g.id for g in f.gates if g.passed is False} == {"reliability"}


# ------------------------------------------------------------------ images: benchmark data


def test_image_benchmark_is_fixed_and_valid():
    data = json.loads(
        (resources.files("rasva.experiments.benchmarks") / "image-v1.json").read_text("utf-8")
    )
    assert data["task"] == "image.generate"
    cases = data["cases"]
    counts: dict[str, int] = {}
    for c in cases:
        counts[c["category"]] = counts.get(c["category"], 0) + 1
    assert counts == {"objects": 3, "scenes": 3, "styles": 3, "text": 3, "people": 3}
    assert len({c["id"] for c in cases}) == len(cases)
    assert len({c["seed"] for c in cases}) == len(cases)
    engine = StableDiffusion15Engine()
    for c in cases:
        assert c["focus"]
        # Every prompt is accepted by SD 1.5 as written, and keeps its seed.
        out = engine.validate_input("image.generate", {"prompt": c["prompt"], "seed": c["seed"]})
        assert out["prompt"] == c["prompt"] and out["seed"] == c["seed"]
        assert len(c["prompt"].split()) <= 40  # well inside CLIP's 77 tokens


# ------------------------------------------------------------------ images: API


def _create_image(c, **body):
    payload = {"name": "Presets", "task": "image.generate", "candidates": [
        {"engine_id": "test-painter", "settings": {"preset": "quick"}},
        {"engine_id": "test-painter", "settings": {"preset": "balanced"}},
    ], **body}  # fmt: skip
    return c.post("/api/v1/experiments", json=payload)


def _finish(c, exp):
    for x in exp["runs"]:
        if x["job_id"]:
            wait_for(c, x["job_id"], TERMINAL)
    return c.get(f"/api/v1/experiments/{exp['id']}").json()


def test_image_experiment_lifecycle(settings):
    with make_client(settings, extra_engines=(PAINTER,)) as c:
        r = _create_image(c, prompts=["A red teapot", "  A blue   bicycle "])
        assert r.status_code == 201, r.text
        exp = _finish(c, r.json())
        assert exp["task"] == "image.generate"
        assert exp["review_dimensions"] == list(IMAGE_REVIEW_DIMENSIONS)
        assert exp["weights"]["experiment"]["quality"] == 0.45
        runs = exp["runs"]
        assert len(runs) == 4 and all(x["status"] == "completed" for x in runs)
        assert {x["text"] for x in runs} == {"A red teapot", "A blue bicycle"}

        # Both candidates got the same seed for the same prompt, and the preset steps.
        by_case: dict[str, set[int]] = {}
        for x in runs:
            by_case.setdefault(x["case_id"], set()).add(x["seed"])
            job = c.get(f"/api/v1/jobs/{x['job_id']}").json()
            assert job["inputs"]["seed"] == x["seed"]
            assert job["inputs"]["steps"] == (15 if x["candidate_key"].endswith("quick") else 25)
            assert x["metrics"]["gpu_peak_bytes"] == 3 * GB
            asset = c.get(f"/api/v1/assets/{x['output_asset_id']}/content")
            assert asset.status_code == 200 and asset.content.startswith(b"\x89PNG")
        assert all(len(seeds) == 1 for seeds in by_case.values())

        target = runs[0]
        url = f"/api/v1/experiments/{exp['id']}/runs/{target['id']}/scores"
        exp = c.put(url, json={"scores": {"proportions": 4, "anatomical_artifacts": 2}}).json()
        summary = {s["key"]: s for s in exp["summary"]}
        s = summary[target["candidate_key"]]
        assert s["quality"] == 3.0 and s["rated"] == 1
        assert s["median_generation_s"] is not None and s["median_rtf"] is None
        assert {g["id"]: g["passed"] for g in s["gates"]}["licence"] is True
        # Audio dimensions are not image dimensions.
        assert c.put(url, json={"scores": {"pronunciation": 3}}).status_code == 422

        listed = c.get("/api/v1/experiments").json()[0]
        assert listed["task"] == "image.generate" and listed["rated"] == 1
        # The experiment project is an image project, kept out of the Library.
        assert c.get("/api/v1/projects").json() == []


def test_image_benchmark_experiment(settings):
    with make_client(settings, extra_engines=(PAINTER,)) as c:
        one = [{"engine_id": "test-painter", "settings": {"steps": 10}}]
        r = _create_image(c, benchmark="image-v1", candidates=one)
        assert r.status_code == 201, r.text
        exp = _finish(c, r.json())
        assert len(exp["runs"]) == 15
        assert all(x["status"] == "completed" for x in exp["runs"])
        tea = next(x for x in exp["runs"] if x["case_id"] == "ob01")
        assert tea["seed"] == 1101 and tea["focus"] and tea["text"].startswith("A red ceramic")


def test_image_experiment_failures_count_against_reliability(settings):
    with make_client(settings, extra_engines=(PAINTER,)) as c:
        r = _create_image(c, prompts=["A cat", "A dog [[fail]]"],
                          candidates=[{"engine_id": "test-painter", "settings": {}}])  # fmt: skip
        exp = _finish(c, r.json())
        assert sorted(x["status"] for x in exp["runs"]) == ["completed", "failed"]
        s = exp["summary"][0]
        assert s["reliability"] == 0.5
        assert {g["id"]: g["passed"] for g in s["gates"]}["reliability"] is False


def test_image_experiment_validation(settings):
    with make_client(settings, extra_engines=(PAINTER, NARRATOR)) as c:
        assert _create_image(c).status_code == 422  # neither benchmark nor prompts
        assert _create_image(c, benchmark="image-v1", prompts=["x"]).status_code == 422
        assert _create_image(c, texts=["x"]).status_code == 422  # texts are for narration
        assert _create_image(c, benchmark="audio-v1").status_code == 422  # wrong task
        assert _create(c, prompts=["x"]).status_code == 422  # prompts are for images
        r = _create_image(c, prompts=["x"],
                          candidates=[{"engine_id": "test-narrator", "settings": {}}])  # fmt: skip
        assert r.status_code == 422 and "cannot do this task" in r.json()["error"]["message"]
        seeded = [{"engine_id": "test-painter", "settings": {"seed": 1}}]
        r = _create_image(c, prompts=["x"], candidates=seeded)
        assert r.status_code == 422 and "seed" in r.json()["error"]["message"]
        r = _create_image(c, prompts=["x"],
                          candidates=[{"engine_id": "sd15", "settings": {}}])  # fmt: skip
        assert r.status_code == 422
        # Engine input errors skip the case rather than failing the experiment.
        exp = _create_image(c, prompts=["x"],
                            candidates=[{"engine_id": "test-painter",
                                         "settings": {"steps": 99}}]).json()  # fmt: skip
        assert exp["runs"][0]["status"] == "skipped" and "Steps" in exp["runs"][0]["skip_reason"]
        ids = {b["id"]: b for b in c.get("/api/v1/experiments/benchmarks").json()}
        assert ids["image-v1"]["task"] == "image.generate" and ids["image-v1"]["cases"] == 15
