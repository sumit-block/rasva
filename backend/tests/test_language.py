from rasva.audio.language import (
    apply_choices,
    classify,
    rule_transliterate,
    spans,
    suggest_devanagari,
    transliteration_plan,
)


def test_classification():
    assert classify("आज मौसम सुहावना है।") == ("hi", 1.0)
    assert classify("Kya tum kal free ho?")[0] == "hinglish"
    assert classify("Arjun ne Krishna se poocha.")[0] == "hinglish"
    assert classify("The meeting is at noon.")[0] == "en"
    assert classify("आज की meeting में बात हुई।")[0] == "mixed"
    lang, conf = classify("Hello")
    assert lang == "unknown" and conf == 0.0  # the UI asks the user
    # Words that are also English don't count as Hinglish evidence.
    assert classify("The main log is to the left.")[0] == "en"


def test_spans_split_scripts_and_keep_offsets():
    text = "आज की meeting में, बात हुई।"
    result = spans(text)
    assert [(s.text, s.language) for s in result] == [
        ("आज की", "hi"), ("meeting", "latin"), ("में, बात हुई।", "hi"),
    ]  # fmt: skip
    assert all(text[s.start : s.end] == s.text for s in result)


def test_rule_transliteration_conventions():
    assert rule_transliterate("arjun") == "अर्जुन"  # cluster takes a halant
    assert rule_transliterate("andar") == "अंदर"  # n before consonant -> anusvara
    assert rule_transliterate("sundar") == "सुंदर"
    assert rule_transliterate("raha") == "रहा"  # final a -> aa
    assert rule_transliterate("thi") == "थी"  # final i -> ee
    assert rule_transliterate("pyaar") == "प्यार"


def test_suggestions_prefer_names_and_words_and_flag_guesses():
    words = {w.original: w for w in suggest_devanagari("Krishna ne main meeting karun")}
    assert words["Krishna"].suggestion == "कृष्ण" and words["Krishna"].source == "name"
    assert words["ne"].confident and words["karun"].suggestion == "करूँ"
    assert not words["main"].confident  # also an English word
    assert words["meeting"].source == "rule" and not words["meeting"].confident


def test_plan_only_touches_hinglish_sentences():
    text = "Arjun ne Krishna se poocha. The meeting is today. आज बारिश है।"
    plan = transliteration_plan(text)
    assert [p["original"] for p in plan] == ["Arjun ne Krishna se poocha."]
    assert plan[0]["suggested"] == "अर्जुन ने कृष्ण से पूछा."


def test_apply_choices_is_exact():
    text = "kya main aaun"
    chosen = [w for w in suggest_devanagari(text) if w.original in ("kya", "main")]
    assert apply_choices(text, chosen) == "क्या मैं aaun"


def test_api(client):
    r = client.post("/api/v1/audio/analyze", json={"text": "Kya haal hai? Fine, thanks."})
    data = r.json()
    assert data["sentences"][0]["language"] == "hinglish"
    assert data["summary"]["counts"]["hinglish"] == 1
    r = client.post("/api/v1/audio/transliterate", json={"text": "Arjun ne kaha."})
    assert r.json()["sentences"][0]["suggested"] == "अर्जुन ने कहा."
