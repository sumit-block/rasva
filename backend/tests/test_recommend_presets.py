from tests.conftest import make_client, wait_for

NARRATOR = "tests.fake_narrator:FakeNarrator"
PRESETS = "tests.fake_narrator:PresetNarrator"
TERMINAL = {"completed", "failed", "canceled", "interrupted"}
HINDI = ["कृष्ण आए।", "अर्जुन ने पूछा।", "युद्ध शुरू हुआ।", "शांति लौटी।"]


def recommend(c, language):
    r = c.get("/api/v1/audio/recommendation", params={"language": language})
    assert r.status_code == 200, r.text
    return r.json()


def rate_all(c, exp, voice, score, limit=None):
    exp = c.get(f"/api/v1/experiments/{exp['id']}").json()
    runs = [x for x in exp["runs"] if x["candidate_key"].endswith(voice)][:limit]
    for run in runs:
        wait_for(c, run["job_id"], TERMINAL)
        r = c.put(f"/api/v1/experiments/{exp['id']}/runs/{run['id']}/scores",
                  json={"scores": {"pronunciation": score, "naturalness": score}})  # fmt: skip
        assert r.status_code == 200, r.text


def test_default_then_your_ratings_decide(settings):
    with make_client(settings, extra_engines=(NARRATOR,)) as c:
        first = recommend(c, "hi")
        assert first["voice_id"] == "hf_alpha" and first["evidence"] is None
        assert "default" in first["reason"]

        exp = c.post("/api/v1/experiments", json={
            "name": "Hindi voices", "texts": HINDI,
            "candidates": [{"engine_id": "test-narrator", "settings": {"voice_id": v}}
                           for v in ("hf_alpha", "hm_omega")],
        }).json()  # fmt: skip
        rate_all(c, exp, "hf_alpha", 3)
        rate_all(c, exp, "hm_omega", 5, limit=2)  # too little evidence yet
        assert recommend(c, "hi")["voice_id"] == "hf_alpha"
        rate_all(c, exp, "hm_omega", 5)
        best = recommend(c, "hi")
        assert best["voice_id"] == "hm_omega" and best["evidence"]["rated"] == 4
        assert "Hindi voices" in best["reason"] and "5.0/5" in best["reason"]
        # Hindi ratings say nothing about English voices.
        assert recommend(c, "en")["evidence"] is None


def test_no_engine_for_language(client):
    r = client.get("/api/v1/audio/recommendation", params={"language": "hi"})
    assert r.status_code == 404


def test_presets_map_to_real_settings(settings):
    with make_client(settings, extra_engines=(NARRATOR, PRESETS)) as c:
        p = c.post("/api/v1/projects", json={"name": "p", "module": "audio"}).json()
        job = c.post("/api/v1/jobs", json={
            "project_id": p["id"], "task": "audio.narrate", "engine_id": "test-preset-narrator",
            "inputs": {"text": "Hello.", "voice_id": "af_heart", "preset": "fast"},
        })  # fmt: skip
        assert job.status_code == 201, job.text
        assert job.json()["inputs"]["speed"] == 1.25 and job.json()["inputs"]["preset"] == "fast"

        # No silent no-ops: a preset for an engine without presets is refused.
        bad = c.post("/api/v1/jobs", json={
            "project_id": p["id"], "task": "audio.narrate", "engine_id": "test-narrator",
            "inputs": {"text": "Hello.", "voice_id": "af_heart", "preset": "fast"},
        })  # fmt: skip
        assert bad.status_code == 422 and "no quality presets" in bad.json()["error"]["message"]

        ch = c.post(f"/api/v1/projects/{p['id']}/chapters", json={
            "title": "c", "script": "One. Two.",
            "settings": {"engine_id": "test-preset-narrator", "voice_id": "af_heart",
                         "preset": "best"},
        })  # fmt: skip
        assert ch.status_code == 201, ch.text
        ch = c.post(f"/api/v1/chapters/{ch.json()['id']}/generate", json={}).json()
        take_job = ch["segments"][0]["takes"][0]["job_id"]
        assert c.get(f"/api/v1/jobs/{take_job}").json()["inputs"]["speed"] == 0.9

        # Switching preset marks existing audio as out of date.
        ch = c.patch(f"/api/v1/chapters/{ch['id']}", json={"settings": {"preset": "fast"}}).json()
        for s in ch["segments"]:
            wait_for(c, s["takes"][0]["job_id"], TERMINAL)
        ch = c.get(f"/api/v1/chapters/{ch['id']}").json()
        assert {s["status"] for s in ch["segments"]} == {"outdated"}

        r = c.patch(f"/api/v1/chapters/{ch['id']}", json={"settings": {"preset": "turbo"}})
        assert r.status_code == 422
