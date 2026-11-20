"""Language detection, mixed-language spans and Roman-to-Devanagari suggestions
(PRD §10.1-10.2, A2-002/003/004). Pure Python, no model.

Detection classifies each sentence as:
  "hi"       Hindi in Devanagari
  "hinglish" Hindi written in Roman letters
  "en"       English
  "mixed"    Devanagari and Roman words together
with a confidence, so the UI can ask the user when it is unsure (PRD §10.1).

Transliteration never changes text by itself: it returns per-word suggestions,
marking each as confident (known word or name) or a rule-based guess to check.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Literal

from rasva.audio.hinglish_lexicon import AMBIGUOUS, ENGLISH_STOPWORDS, NAMES, WORDS
from rasva.audio.text import normalize_whitespace, segment

Language = Literal["hi", "hinglish", "en", "mixed", "unknown"]

_TOKEN = re.compile(r"[ऀ-ॿ꣠-ꣿ]+|[A-Za-z]+(?:'[A-Za-z]+)?|\S")
_DEVA = re.compile(r"[ऀ-ॿ]")
# Hinglish words that are also everyday English: suggested, never pre-selected.
_UNSURE_TRANSLIT = {"to", "the", "main", "log", "din", "par", "pe", "ho", "kar", "fir", "acha"}


@dataclass(frozen=True)
class Span:
    text: str
    language: Literal["hi", "latin", "other"]
    start: int
    end: int


@dataclass(frozen=True)
class SentenceLanguage:
    index: int
    text: str
    language: Language
    confidence: float  # 0..1
    spans: list[Span]


def _kind(token: str) -> Literal["hi", "latin", "other"]:
    if _DEVA.search(token):
        return "hi"
    if token[:1].isascii() and token[:1].isalpha():
        return "latin"
    return "other"


def spans(text: str) -> list[Span]:
    """Split text into runs of Devanagari and Latin words.

    Spaces, digits and punctuation join the run they sit in, so "आज की meeting
    में" becomes [आज की ][meeting ][में].
    """
    out: list[Span] = []
    for m in _TOKEN.finditer(text):
        kind = _kind(m.group(0))
        if kind == "other" and out:
            prev = out[-1]
            out[-1] = Span(text[prev.start : m.end()], prev.language, prev.start, m.end())
            continue
        if out and out[-1].language == kind:
            prev = out[-1]
            out[-1] = Span(text[prev.start : m.end()], kind, prev.start, m.end())
        else:
            # Leading punctuation/digits are attached to the first word run.
            out.append(Span(text[m.start() : m.end()], kind, m.start(), m.end()))
    merged: list[Span] = []
    for s in out:
        if merged and s.language == "other":
            p = merged[-1]
            merged[-1] = Span(text[p.start : s.end], p.language, p.start, s.end)
        elif merged and merged[-1].language == "other":
            p = merged.pop()
            merged.append(Span(text[p.start : s.end], s.language, p.start, s.end))
        else:
            merged.append(s)
    return merged


def classify(text: str) -> tuple[Language, float]:
    words = [t for t in _TOKEN.findall(text) if t[:1].isalpha() or _DEVA.search(t)]
    deva = sum(1 for w in words if _DEVA.search(w))
    latin = [w.lower() for w in words if not _DEVA.search(w)]
    if not words:
        return "unknown", 0.0
    if deva and latin:
        share = deva / len(words)
        # A single English word in a Hindi sentence is still "mixed": English
        # voices would drop the Hindi, Hindi voices must read the English.
        return "mixed", round(max(share, 1 - share), 2)
    if deva:
        return "hi", 1.0
    hinglish = sum(1 for w in latin if w in WORDS and w not in AMBIGUOUS) + sum(
        1 for w in latin if w in NAMES
    )
    english = sum(1 for w in latin if w in ENGLISH_STOPWORDS)
    if hinglish == english == 0:
        return "unknown", 0.0
    if hinglish > english:
        return "hinglish", round(hinglish / (hinglish + english), 2)
    return "en", round(english / (hinglish + english), 2)


def analyze(text: str) -> dict[str, object]:
    sentences = []
    counts: dict[str, int] = {}
    for seg in segment(text):
        language, confidence = classify(seg.text)
        counts[language] = counts.get(language, 0) + 1
        sentences.append(
            SentenceLanguage(seg.index, seg.text, language, confidence, spans(seg.text))
        )
    uncertain = sum(1 for s in sentences if s.confidence < 0.67)
    dominant = max(counts, key=lambda k: counts[k]) if counts else "unknown"
    return {
        "sentences": [asdict(s) for s in sentences],
        "summary": {"counts": counts, "dominant": dominant, "uncertain": uncertain},
    }


# ------------------------------------------------------------------ transliteration

_VOWELS = [  # (roman, independent, matra), longest first
    ("aa", "आ", "ा"), ("ai", "ऐ", "ै"), ("au", "औ", "ौ"), ("ee", "ई", "ी"),
    ("ii", "ई", "ी"), ("oo", "ऊ", "ू"), ("uu", "ऊ", "ू"), ("a", "अ", ""),
    ("i", "इ", "ि"), ("u", "उ", "ु"), ("e", "ए", "े"), ("o", "ओ", "ो"),
]  # fmt: skip
_CONSONANTS = [  # longest first
    ("chh", "छ"), ("shh", "ष"), ("kh", "ख"), ("gh", "घ"), ("ch", "च"), ("jh", "झ"),
    ("th", "थ"), ("dh", "ध"), ("ph", "फ"), ("bh", "भ"), ("sh", "श"), ("k", "क"),
    ("g", "ग"), ("c", "क"), ("j", "ज"), ("t", "त"), ("d", "द"), ("n", "न"), ("p", "प"),
    ("b", "ब"), ("m", "म"), ("y", "य"), ("r", "र"), ("l", "ल"), ("v", "व"), ("w", "व"),
    ("s", "स"), ("h", "ह"), ("z", "ज़"), ("f", "फ़"), ("q", "क़"), ("x", "क्स"),
]  # fmt: skip
_HALANT = "्"
_ANUSVARA = "ं"


def _match(word: str, i: int, table: list[tuple]) -> tuple | None:
    for entry in table:
        if word.startswith(entry[0], i):
            return entry
    return None


def rule_transliterate(word: str) -> str:
    """Best-effort casual-Hinglish transliteration. Always a guess to check.

    Conventions: consonant clusters take a halant; n/m before a consonant become
    anusvara; a final "a" after a consonant reads as "aa" (raha -> रहा) and a
    final "i" as "ee" (thi -> थी), as is usual in casual Roman Hindi.
    """
    w = word.lower()
    out: list[str] = []
    i = 0
    prev_consonant = False
    while i < len(w):
        cons = _match(w, i, _CONSONANTS)
        if cons:
            nxt = i + len(cons[0])
            if cons[0] in ("n", "m") and prev_consonant is False and out and nxt < len(w):
                following = _match(w, nxt, _CONSONANTS)
                if following:
                    out.append(_ANUSVARA)
                    i = nxt
                    continue
            if prev_consonant:
                out.append(_HALANT)
            out.append(cons[1])
            prev_consonant = True
            i = nxt
            continue
        vowel = _match(w, i, _VOWELS)
        if vowel:
            end = i + len(vowel[0]) == len(w)
            roman = vowel[0]
            if end and prev_consonant and roman == "a":
                roman = "aa"
            if end and prev_consonant and roman == "i":
                roman = "ee"
            entry = next(v for v in _VOWELS if v[0] == roman)
            out.append(entry[2] if prev_consonant else entry[1])
            prev_consonant = False
            i += len(vowel[0])
            continue
        out.append(w[i])  # unknown character: keep
        prev_consonant = False
        i += 1
    return "".join(out)


@dataclass(frozen=True)
class WordSuggestion:
    original: str
    suggestion: str
    source: Literal["name", "word", "rule"]
    confident: bool
    start: int
    end: int


def suggest_devanagari(text: str) -> list[WordSuggestion]:
    out = []
    for m in re.finditer(r"[A-Za-z]+", text):
        word = m.group(0)
        key = word.lower()
        if key in NAMES:
            out.append(WordSuggestion(word, NAMES[key], "name", True, m.start(), m.end()))
        elif key in WORDS:
            out.append(WordSuggestion(word, WORDS[key], "word", key not in _UNSURE_TRANSLIT,
                                      m.start(), m.end()))  # fmt: skip
        else:
            out.append(
                WordSuggestion(word, rule_transliterate(word), "rule", False, m.start(), m.end())
            )
    return out


def transliteration_plan(text: str) -> list[dict[str, object]]:
    """Suggestions for every sentence that reads as Roman Hinglish."""
    plan = []
    for seg in segment(normalize_whitespace(text)):
        language, confidence = classify(seg.text)
        if language != "hinglish":
            continue
        words = suggest_devanagari(seg.text)
        plan.append({
            "index": seg.index,
            "original": seg.text,
            "confidence": confidence,
            "words": [asdict(w) for w in words],
            "suggested": apply_choices(seg.text, [w for w in words if w.confident]),
        })  # fmt: skip
    return plan


def apply_choices(text: str, chosen: list[WordSuggestion]) -> str:
    out, pos = [], 0
    for w in sorted(chosen, key=lambda w: w.start):
        out.append(text[pos : w.start])
        out.append(w.suggestion)
        pos = w.end
    out.append(text[pos:])
    return "".join(out)
