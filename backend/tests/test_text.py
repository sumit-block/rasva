from rasva.audio.text import (
    PARAGRAPH_PAUSE_S,
    SENTENCE_PAUSE_S,
    detect_script,
    segment,
    split_sentences,
)


def test_hindi_danda_and_question_split():
    assert split_sentences("अर्जुन ने पूछा। कृष्ण ने उत्तर दिया॥ क्यों?") == [
        "अर्जुन ने पूछा।",
        "कृष्ण ने उत्तर दिया॥",
        "क्यों?",
    ]


def test_english_abbreviations_initials_and_decimals():
    text = 'Dr. Rao met A. P. J. Abdul Kalam. Pi is 3.14 approx. He said "Go!" Then left.'
    assert split_sentences(text) == [
        "Dr. Rao met A. P. J. Abdul Kalam.",
        'Pi is 3.14 approx. He said "Go!"',
        "Then left.",
    ]


def test_paragraph_pauses_and_indices():
    segs = segment("One. Two.\n\nThree.")
    assert [s.text for s in segs] == ["One.", "Two.", "Three."]
    assert [s.paragraph for s in segs] == [0, 0, 1]
    assert [s.pause_after_s for s in segs] == [
        SENTENCE_PAUSE_S,
        PARAGRAPH_PAUSE_S,
        PARAGRAPH_PAUSE_S,
    ]
    assert [s.index for s in segs] == [0, 1, 2]


def test_long_sentences_split_at_commas_then_words():
    clause = "युधिष्ठिर ने धर्म की बात कही"
    long = ", ".join([clause] * 12) + "।"
    pieces = segment(long, max_chars=80)
    assert len(pieces) > 1 and all(len(p.text) <= 80 for p in pieces)
    assert " ".join(p.text for p in pieces).replace(" ,", ",") == long
    words = " ".join(["word"] * 100)
    assert all(len(p.text) <= 50 for p in segment(words, max_chars=50))


def test_no_text_lost():
    text = "Arjun ne Krishna se kaha... kya karun? 'Yuddh karo!' Bas."
    assert " ".join(s.text for s in segment(text)) == text


def test_script_detection():
    assert detect_script("कृष्ण और अर्जुन") == "devanagari"
    assert detect_script("Krishna aur Arjun") == "latin"
    assert detect_script("कृष्ण aur Arjun") == "mixed"
    # A stray acronym in a longer Hindi passage does not make it "mixed".
    assert detect_script("कुरुक्षेत्र में कृष्ण और अर्जुन का लंबा संवाद हुआ, NASA") == "devanagari"
    assert detect_script("123 ...") == "none"
