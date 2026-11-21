"""Pronunciation entries chosen by each sentence's language (engines that speak
both English and Hindi), and the user's per-text language override."""

import pytest

from rasva.audio.pronunciation import Entry, apply_by_sentence
from rasva.audio.text import reading_language, segment, sentence_spans
from rasva.engines.chatterbox import ChatterboxEngine
from rasva.engines.contract import EngineInputError
from tests.conftest import make_client, wait_for
from tests.test_voices import create as create_voice
from tests.test_voices import speechlike, upload

BILINGUAL = "tests.fake_narrator:BilingualNarrator"
NARRATOR = "tests.fake_narrator:FakeNarrator"
CLONER = "tests.fake_narrator:CloningNarrator"
TERMINAL = {"completed", "failed", "canceled", "interrupted"}

DURYODHANA = Entry("e1", ("Duryodhana",), "Dur-yo-dhuh-na", "en")
DHRITA = Entry("e2", ("Dhritarashtra",), "Dhri-ta-raash-tra", "en")
KRISHNA_HI = Entry("h1", ("कृष्ण",), "क्रिश्न", "hi")
ANY = Entry("a1", ("Kunti",), "Koon-tee", "any")


# ------------------------------------------------------------------ pure rules


def test_reading_language_follows_the_script():
    assert reading_language("Duryodhana spoke to Dhritarashtra.") == "en"
    assert reading_language("कृष्ण ने कहा।") == "hi"
    assert reading_language("आज की meeting में।") == "hi"  # English words in Hindi are fine
    assert reading_language("1, 2, 3.") == "en"


def test_sentence_spans_match_segment_on_the_original_text():
    text = "Duryodhana  spoke.\tThen he left!\n\nकृष्ण आए।   Kunti waited."
    spans = sentence_spans(text)
    assert [" ".join(text[a:b].split()) for a, b in spans] == [s.text for s in segment(text)]
    assert text[spans[0][0] : spans[0][1]] == "Duryodhana  spoke."


def test_entries_follow_each_sentences_language():
    entries = [DURYODHANA, DHRITA, KRISHNA_HI, ANY]
    text = "Duryodhana spoke.\n\nकृष्ण आए। Kunti and Dhritarashtra waited."
    spoken, applied = apply_by_sentence(text, entries)
    assert spoken == "Dur-yo-dhuh-na spoke.\n\nक्रिश्न आए। Koon-tee and Dhri-ta-raash-tra waited."
    assert [(a.written, a.language) for a in applied] == [
        ("Duryodhana", "en"), ("कृष्ण", "hi"), ("Kunti", "en"), ("Dhritarashtra", "en"),
    ]  # fmt: skip
    for a in applied:  # offsets point into the original text
        assert text[a.start : a.end] == a.written


def test_override_wins_over_the_script():
    text = "Duryodhana spoke. Kunti listened."
    spoken, applied = apply_by_sentence(text, [DURYODHANA, ANY], "hi")
    assert spoken == "Duryodhana spoke. Koon-tee listened."  # English entry skipped, "any" kept
    assert [a.language for a in applied] == ["hi"]


def test_chatterbox_accepts_only_english_or_hindi_overrides():
    engine = ChatterboxEngine()
    base = {"text": "Duryodhana spoke.", "voice_id": "default_hi"}
    assert "language" not in engine.validate_input("audio.narrate", base)
    assert engine.validate_input("audio.narrate", {**base, "language": "en"})["language"] == "en"
    assert "language" not in engine.validate_input("audio.narrate", {**base, "language": ""})
    with pytest.raises(EngineInputError):
        engine.validate_input("audio.narrate", {**base, "language": "hinglish"})
    assert "language_per_sentence" in ChatterboxEngine.descriptor.features


# ------------------------------------------------------------------ through the API


def client(settings):
    return make_client(settings, extra_engines=(BILINGUAL, NARRATOR, CLONER))


def entry(c, **kw):
    body = {"written_forms": ["Duryodhana"], "spoken": "Dur-yo-dhuh-na", "language": "en",
            "approved": True, **kw}  # fmt: skip
    r = c.post("/api/v1/pronunciations", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def narrate(c, project_id, text, engine="test-bilingual", **inputs):
    r = c.post("/api/v1/jobs", json={
        "project_id": project_id, "task": "audio.narrate", "engine_id": engine,
        "inputs": {"text": text, **inputs},
    })  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()


def hindi_voice(c):
    asset = upload(c, speechlike())["asset"]["id"]
    r = create_voice(c, asset, language="hi")
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_hindi_recorded_voice_gets_english_entries_for_english_text(settings):
    with client(settings) as c:
        p = c.post("/api/v1/projects", json={"name": "p", "module": "audio"}).json()
        entry(c)
        entry(c, written_forms=["कृष्ण"], spoken="क्रिश्न", language="hi")
        voice = hindi_voice(c)
        text = "Duryodhana  spoke. कृष्ण आए।"
        job = narrate(c, p["id"], text, voice_profile_id=voice)
        assert job["inputs"]["text"] == "Dur-yo-dhuh-na  spoke. क्रिश्न आए।"
        assert job["inputs"]["original_text"] == text
        applied = job["inputs"]["pronunciations"]
        assert [(a["written"], a["language"]) for a in applied] == [
            ("Duryodhana", "en"), ("कृष्ण", "hi"),
        ]  # fmt: skip
        assert all(text[a["start"] : a["end"]] == a["written"] for a in applied)
        done = wait_for(c, job["id"], TERMINAL)
        assert done["state"] == "completed", done.get("error_message")

        # A built-in voice labelled Hindi on the same engine behaves the same way.
        built_in = narrate(c, p["id"], "Duryodhana spoke.", voice_id="default_hi")
        assert built_in["inputs"]["text"] == "Dur-yo-dhuh-na spoke."

        # Single-language engines keep choosing by the voice: Kokoro-style English
        # voices get English entries, Hindi voices do not.
        eng = narrate(c, p["id"], "Duryodhana spoke.", engine="test-narrator", voice_id="af_heart")
        assert eng["inputs"]["text"] == "Dur-yo-dhuh-na spoke."
        hin = narrate(c, p["id"], "Duryodhana spoke.", engine="test-narrator", voice_id="hf_alpha")
        assert hin["inputs"]["text"] == "Duryodhana spoke."
        for j in (built_in, eng, hin):
            wait_for(c, j["id"], TERMINAL)


def test_single_language_cloning_engine_still_uses_the_recordings_language(settings):
    with client(settings) as c:
        p = c.post("/api/v1/projects", json={"name": "p", "module": "audio"}).json()
        entry(c)
        job = narrate(c, p["id"], "Duryodhana spoke.", engine="test-cloner",
                      voice_profile_id=hindi_voice(c))  # fmt: skip
        assert job["inputs"]["text"] == "Duryodhana spoke."
        assert "original_text" not in job["inputs"]
        wait_for(c, job["id"], TERMINAL)


def test_check_without_a_language_chooses_per_sentence(settings):
    with client(settings) as c:
        entry(c)
        entry(c, written_forms=["कृष्ण"], spoken="क्रिश्न", language="hi")
        r = c.post("/api/v1/pronunciations/check", json={"text": "Duryodhana spoke. कृष्ण आए।"})
        assert r.status_code == 200, r.text
        assert r.json()["normalized"] == "Dur-yo-dhuh-na spoke. क्रिश्न आए।"
        r = c.post("/api/v1/pronunciations/check",
                   json={"text": "Duryodhana spoke. कृष्ण आए।", "language": "hi"})  # fmt: skip
        assert r.json()["normalized"] == "Duryodhana spoke. क्रिश्न आए।"


def test_override_reaches_dictionary_and_engine(settings):
    with client(settings) as c:
        p = c.post("/api/v1/projects", json={"name": "p", "module": "audio"}).json()
        entry(c)
        job = narrate(c, p["id"], "Duryodhana spoke.", voice_id="default_en", language="hi")
        assert job["inputs"]["text"] == "Duryodhana spoke."  # English entry not chosen
        assert job["inputs"]["language"] == "hi"
        done = wait_for(c, job["id"], TERMINAL)
        asset = c.get(f"/api/v1/assets/{done['outputs'][0]}").json()
        assert asset["metadata"]["languages"] == ["hi"]


def test_retry_reuses_per_sentence_choices(settings):
    with client(settings) as c:
        p = c.post("/api/v1/projects", json={"name": "p", "module": "audio"}).json()
        e = entry(c)
        voice = hindi_voice(c)
        blocker = c.post("/api/v1/jobs", json={
            "project_id": p["id"], "task": "test.run", "engine_id": "test-fault",
            "inputs": {"behaviour": "slow", "seconds": 3},
        }).json()  # fmt: skip
        job = narrate(c, p["id"], "Duryodhana spoke.", voice_profile_id=voice, language="en")
        assert c.post(f"/api/v1/jobs/{job['id']}/cancel").json()["state"] == "canceled"
        c.patch(f"/api/v1/pronunciations/{e['id']}", json={"spoken": "Changed"})
        again = c.post(f"/api/v1/jobs/{job['id']}/retry").json()
        assert again["inputs"]["text"] == "Dur-yo-dhuh-na spoke."
        assert again["inputs"]["original_text"] == "Duryodhana spoke."
        assert again["inputs"]["language"] == "en"
        assert again["inputs"]["pronunciations"] == job["inputs"]["pronunciations"]
        for j in (again, blocker):
            c.post(f"/api/v1/jobs/{j['id']}/cancel")
            wait_for(c, j["id"], TERMINAL)


def test_preview_on_an_engine_that_speaks_both(settings):
    with client(settings) as c:
        e = entry(c, approved=False)
        body = {"engine_id": "test-bilingual", "voice_id": "default_hi"}
        r = c.post(f"/api/v1/pronunciations/{e['id']}/preview", json=body)
        assert r.status_code == 201, r.text
        assert r.json()["inputs"]["text"] == "Dur-yo-dhuh-na"
        hi = entry(c, written_forms=["कृष्ण"], spoken="क्रिश्न", language="en", approved=False)
        r = c.post(f"/api/v1/pronunciations/{hi['id']}/preview", json=body)
        assert r.status_code == 422 and r.json()["error"]["code"] == "language_mismatch"
        # A single-language engine still checks the voice's language.
        r = c.post(f"/api/v1/pronunciations/{e['id']}/preview",
                   json={"engine_id": "test-narrator", "voice_id": "hf_alpha"})  # fmt: skip
        assert r.status_code == 422


# ------------------------------------------------------------------ chapter overrides

CHAPTER = "Duryodhana spoke. Then he left.\n\nDhritarashtra waited."


def chapter(c, engine="test-bilingual", voice="default_en"):
    from tests.test_chapters import new_chapter

    entry(c)
    entry(c, written_forms=["Dhritarashtra"], spoken="Dhri-ta-raash-tra")
    return new_chapter(c, CHAPTER, engine_id=engine, voice_id=voice)


def mark(c, segment_id, language):
    r = c.patch(f"/api/v1/segments/{segment_id}", json={"language": language})
    assert r.status_code == 200, r.text
    return r.json()


def narration_inputs(c, seg):
    return c.get(f"/api/v1/jobs/{seg['takes'][-1]['job_id']}").json()["inputs"]


def test_sentence_language_override_in_a_chapter(settings):
    from tests.test_chapters import generate, settle, statuses

    with client(settings) as c:
        _, ch = chapter(c)
        assert ch["language_per_sentence"] is True
        first, second = ch["segments"]
        assert first["language_override"] is None and first["reads_as"] == "en"
        assert (
            c.patch(f"/api/v1/segments/{first['id']}", json={"language": "xx"}).status_code == 422
        )
        assert c.patch("/api/v1/segments/nope", json={"language": "en"}).status_code == 404

        ch = mark(c, first["id"], "hi")
        assert ch["segments"][0]["language_override"] == "hi"
        assert ch["segments"][0]["reads_as"] == "hi"
        generate(c, ch["id"])
        ch = settle(c, ch["id"])
        assert statuses(ch) == ["ready", "ready"]
        marked, plain = (narration_inputs(c, s) for s in ch["segments"])
        # The marked sentence goes to the engine as Hindi and skips English entries.
        assert marked["language"] == "hi" and marked["text"] == "Duryodhana spoke. Then he left."
        assert "language" not in plain and plain["text"] == "Dhri-ta-raash-tra waited."
        assert plain["original_text"] == "Dhritarashtra waited."

        # Changing a choice makes only that sentence need generating again.
        ch = mark(c, ch["segments"][1]["id"], "en")
        assert statuses(ch) == ["ready", "outdated"]
        ch = mark(c, ch["segments"][1]["id"], None)
        assert statuses(ch) == ["ready", "ready"]

        # Edits elsewhere keep the choice and the audio.
        script = CHAPTER.replace("Dhritarashtra waited.", "Dhritarashtra waited long.")
        ch = c.patch(f"/api/v1/chapters/{ch['id']}", json={"script": script}).json()
        assert ch["segments"][0]["id"] == first["id"]
        assert ch["segments"][0]["language_override"] == "hi"
        assert statuses(ch) == ["ready", "pending"]

        # Editing a neighbouring sentence in the same group keeps the choice too.
        script = script.replace("Then he left.", "Then he went home.")
        ch = c.patch(f"/api/v1/chapters/{ch['id']}", json={"script": script}).json()
        assert ch["segments"][0]["text"] == "Duryodhana spoke. Then he went home."
        assert ch["segments"][0]["language_override"] == "hi"
        assert ch["segments"][1]["language_override"] is None


def test_one_language_engines_keep_the_choice_without_using_it(settings):
    from tests.test_chapters import generate, settle, statuses

    with client(settings) as c:
        _, ch = chapter(c, engine="test-narrator", voice="af_heart")
        assert ch["language_per_sentence"] is False
        generate(c, ch["id"])
        ch = settle(c, ch["id"])
        ch = mark(c, ch["segments"][0]["id"], "hi")
        # Saved for later (another engine), but this voice reads everything in
        # English, so nothing needs generating again.
        assert ch["segments"][0]["language_override"] == "hi"
        assert statuses(ch) == ["ready", "ready"]
        generate(c, ch["id"], scope="all")
        ch = settle(c, ch["id"])
        inputs = narration_inputs(c, ch["segments"][0])
        assert inputs["text"] == "Dur-yo-dhuh-na spoke. Then he left."  # by the voice: English
