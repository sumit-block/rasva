import json
import time
from importlib import resources

import pytest

from rasva.audio.text import script_counts
from rasva.experiments.scoring import CandidateView, RunView, summarize
from tests.conftest import make_client, wait_for

GB = 1024**3
NARRATOR = "tests.fake_narrator:FakeNarrator"
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
