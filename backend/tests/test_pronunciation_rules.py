from rasva.audio.pronunciation import Entry, applicable, apply


def E(id, forms, spoken, lang="any", **kw):
    return Entry(id, tuple(forms), spoken, lang, **kw)


def test_replaces_whole_words_only_and_records_offsets():
    karna = E("k", ["कर्ण"], "करण")
    original = "कर्ण ने कर्णफूल पहना। कर्ण!"
    text, applied = apply(original, [karna])
    assert text == "करण ने कर्णफूल पहना। करण!"
    last = original.rindex("कर्ण")
    assert [(a.written, a.start, a.end) for a in applied] == [
        ("कर्ण", 0, 4),
        ("कर्ण", last, last + 4),
    ]
    assert all(original[a.start : a.end] == a.written for a in applied)


def test_devanagari_matras_count_as_part_of_the_word():
    # "कृष्णा" (with ā) is a different name and must not match "कृष्ण".
    text, _ = apply("कृष्णा और कृष्ण", [E("k", ["कृष्ण"], "क्रिश्न")])
    assert text == "कृष्णा और क्रिश्न"


def test_longest_form_wins_and_case_insensitive_latin():
    entries = [E("a", ["Dhrita"], "X"), E("b", ["Dhritarashtra"], "Dhri-ta-raash-tra")]
    text, applied = apply("DHRITARASHTRA was blind. dhrita.", entries)
    assert text == "Dhri-ta-raash-tra was blind. X."
    assert [a.entry_id for a in applied] == ["b", "a"]


def test_case_sensitive_entries():
    entries = [E("us", ["US"], "U.S.", case_sensitive=True)]
    assert apply("US and us", entries)[0] == "U.S. and us"


def test_substring_mode():
    assert apply("Krishnaji", [E("k", ["Krishna"], "Krishn", whole_word=False)])[0] == "Krishnji"


def test_language_filter_and_project_override():
    glob = E("g", ["Arjun"], "Ar-joon", "en")
    proj = E("p", ["Arjun"], "Urr-jun", "en", project_scoped=True)
    hindi = E("h", ["अर्जुन"], "अर्जुन्", "hi")
    for_en = applicable([proj, glob, hindi], "en")
    assert [e.id for e in for_en] == ["p"]
    assert apply("Arjun", for_en)[0] == "Urr-jun"
    assert [e.id for e in applicable([glob, hindi], "hi")] == ["h"]


def test_unicode_normalisation():
    decomposed = "क़"  # क + nukta as two code points
    composed = "क़"  # precomposed क़
    text, applied = apply(f"{decomposed}लम", [E("q", [f"{composed}लम"], "कलम")])
    assert applied and text == "कलम"


def test_no_entries_is_identity():
    assert apply("कुछ नहीं", []) == ("कुछ नहीं", [])


def test_danda_and_punctuation_end_a_word():
    k = E("k", ["कृष्ण"], "क्रिश्न")
    assert apply("कृष्ण। कृष्ण॥ कृष्ण, “कृष्ण” कृष्ण?", [k])[0] == ("क्रिश्न। क्रिश्न॥ क्रिश्न, “क्रिश्न” क्रिश्न?")
