from tests.conftest import make_client, wait_for

NARRATOR = "tests.fake_narrator:FakeNarrator"
TERMINAL = {"completed", "failed", "canceled", "interrupted"}


def client(settings):
    return make_client(settings, extra_engines=(NARRATOR,))


def entry(c, **kw):
    body = {"written_forms": ["कृष्ण"], "spoken": "क्रिश्न", "language": "hi", "approved": True, **kw}
    r = c.post("/api/v1/pronunciations", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def narrate(c, project_id, text, voice="hf_alpha", **extra):
    r = c.post("/api/v1/jobs", json={
        "project_id": project_id, "task": "audio.narrate", "engine_id": "test-narrator",
        "inputs": {"text": text, "voice_id": voice, **extra},
    })  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()


def test_crud_and_validation(settings):
    with client(settings) as c:
        e = entry(c, phonetic_hint="Kṛṣṇa", examples=["कृष्ण ने कहा।"])
        assert e["approved"] is True and e["project_id"] is None
        assert c.post("/api/v1/pronunciations", json={
            "written_forms": [], "spoken": "x", "language": "hi"}).status_code == 422  # fmt: skip
        multiline = {"written_forms": ["a"], "spoken": "x\ny", "language": "hi"}
        assert c.post("/api/v1/pronunciations", json=multiline).status_code == 422
        r = c.patch(f"/api/v1/pronunciations/{e['id']}", json={"spoken": "कृश्ण", "approved": False})
        assert r.json()["spoken"] == "कृश्ण" and r.json()["approved"] is False
        assert [x["id"] for x in c.get("/api/v1/pronunciations", params={"q": "कृष"}).json()] == [
            e["id"]
        ]
        assert c.delete(f"/api/v1/pronunciations/{e['id']}").status_code == 204
        assert c.delete(f"/api/v1/pronunciations/{e['id']}").status_code == 404


def test_narration_uses_approved_entries_and_keeps_original(settings):
    with client(settings) as c:
        p = c.post("/api/v1/projects", json={"name": "Gita", "module": "audio"}).json()
        entry(c)
        entry(c, written_forms=["अर्जुन"], spoken="अर्जुना", approved=False)  # not applied
        text = "कृष्ण ने अर्जुन से कहा।"
        job = narrate(c, p["id"], text)
        assert job["inputs"]["text"] == "क्रिश्न ने अर्जुन से कहा।"
        assert job["inputs"]["original_text"] == text
        assert [a["written"] for a in job["inputs"]["pronunciations"]] == ["कृष्ण"]
        assert wait_for(c, job["id"], TERMINAL)["state"] == "completed"

        # Opt out for one job.
        plain = narrate(c, p["id"], text, use_dictionary=False)
        assert plain["inputs"]["text"] == text and "original_text" not in plain["inputs"]

        # English voices only get English/any entries.
        eng = narrate(c, p["id"], "Krishna smiled.", voice="af_heart")
        assert eng["inputs"]["text"] == "Krishna smiled."


def test_project_entries_override_global(settings):
    with client(settings) as c:
        p = c.post("/api/v1/projects", json={"name": "p", "module": "audio"}).json()
        entry(c, written_forms=["Arjun"], spoken="Ar-joon", language="en")
        entry(c, written_forms=["Arjun"], spoken="Urr-jun", language="en", project_id=p["id"])
        assert narrate(c, p["id"], "Arjun.", voice="af_heart")["inputs"]["text"] == "Urr-jun."
        other = c.post("/api/v1/projects", json={"name": "q", "module": "audio"}).json()
        assert narrate(c, other["id"], "Arjun.", voice="af_heart")["inputs"]["text"] == "Ar-joon."


def test_retry_repeats_the_same_pronunciations(settings):
    with client(settings) as c:
        p = c.post("/api/v1/projects", json={"name": "p", "module": "audio"}).json()
        e = entry(c)
        # Hold the queue so the narration is still queued when we cancel it.
        blocker = c.post("/api/v1/jobs", json={
            "project_id": p["id"], "task": "test.run", "engine_id": "test-fault",
            "inputs": {"behaviour": "slow", "seconds": 3},
        }).json()  # fmt: skip
        job = narrate(c, p["id"], "कृष्ण।")
        assert job["inputs"]["text"] == "क्रिश्न।"
        assert c.post(f"/api/v1/jobs/{job['id']}/cancel").json()["state"] == "canceled"
        c.patch(f"/api/v1/pronunciations/{e['id']}", json={"spoken": "बदला"})
        again = c.post(f"/api/v1/jobs/{job['id']}/retry").json()
        assert again["inputs"]["text"] == "क्रिश्न।"  # not the edited spoken form
        assert again["inputs"]["original_text"] == "कृष्ण।"
        for j in (again, blocker):
            c.post(f"/api/v1/jobs/{j['id']}/cancel")
            wait_for(c, j["id"], TERMINAL)


def test_check_shows_applied_and_pending(settings):
    with client(settings) as c:
        entry(c)
        entry(c, written_forms=["कर्ण"], spoken="करण", approved=False)
        r = c.post("/api/v1/pronunciations/check",
                   json={"text": "कृष्ण और कर्ण", "language": "hi"}).json()  # fmt: skip
        assert r["normalized"] == "क्रिश्न और कर्ण"
        assert [a["written"] for a in r["applied"]] == ["कृष्ण"]
        assert [a["written"] for a in r["unapproved_matches"]] == ["कर्ण"]


def test_preview_unapproved_entry_in_system_project(settings):
    with client(settings) as c:
        e = entry(c, approved=False, examples=["कृष्ण मुस्कुराए।"])
        body = {"engine_id": "test-narrator", "voice_id": "hf_alpha", "example": 0}
        r = c.post(f"/api/v1/pronunciations/{e['id']}/preview", json=body)
        assert r.status_code == 201, r.text
        job = r.json()
        assert job["inputs"]["text"] == "क्रिश्न मुस्कुराए।"
        assert job["inputs"]["preview"] is True
        assert wait_for(c, job["id"], TERMINAL)["state"] == "completed"
        assert c.get("/api/v1/projects").json() == []  # preview project is hidden
        bad = c.post(f"/api/v1/pronunciations/{e['id']}/preview",
                     json={"engine_id": "test-narrator", "voice_id": "af_heart"})  # fmt: skip
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "language_mismatch"


def test_suggestions_cover_prd_priority_names(settings):
    with client(settings) as c:
        before = c.get("/api/v1/pronunciations/suggestions").json()
        assert len(before) == 10 and before[0] == {"written": "कृष्ण", "hint": "Kṛṣṇa"}
        entry(c)
        after = [s["written"] for s in c.get("/api/v1/pronunciations/suggestions").json()]
        assert "कृष्ण" not in after and len(after) == 9


def test_experiments_apply_global_dictionary(settings):
    with client(settings) as c:
        entry(c)
        exp = c.post("/api/v1/experiments", json={
            "name": "x", "texts": ["कृष्ण आए।"],
            "candidates": [{"engine_id": "test-narrator", "settings": {"voice_id": "hf_alpha"}}],
        }).json()  # fmt: skip
        job = c.get(f"/api/v1/jobs/{exp['runs'][0]['job_id']}").json()
        assert job["inputs"]["text"] == "क्रिश्न आए।"
        assert job["inputs"]["original_text"] == "कृष्ण आए।"
