"""Pronunciation dictionary application (PRD §10.2 step 4, §10.3).

Pure functions. The user's visible script is never modified: callers keep the
original and send the returned text to the engine, recording every substitution
so the transformation is explainable later (PRD §10.2 step 7).

Matching rules:
- Longest written form wins, so "Dhritarashtra" is not caught by "Dhrita".
- Whole-word by default, with word characters that include Devanagari vowel
  signs and nasalisation marks (Python's ``\\w`` does not cover those), so
  "कर्ण" never matches inside "कर्णफूल".
- Latin forms are case-insensitive unless the entry says otherwise; Devanagari
  has no case.
- Project entries override global entries for the same written form.
- Entries are chosen by language: the voice's language for engines that read
  everything in one language, or each sentence's language for engines that
  speak both (:func:`apply_by_sentence`).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Literal

from rasva.audio.text import reading_language, sentence_spans

Language = Literal["hi", "en", "any"]

# Devanagari letters, signs and digits, but not the danda, double danda or
# abbreviation sign: those are punctuation and must act as word boundaries.
_WORD_CHARS = r"\w\u0900-\u0963\u0966-\u096F\u0971-\u097F\uA8E0-\uA8FF\u200c\u200d"


@dataclass(frozen=True)
class Entry:
    id: str
    written_forms: tuple[str, ...]
    spoken: str
    language: Language
    whole_word: bool = True
    case_sensitive: bool = False
    project_scoped: bool = False


@dataclass(frozen=True)
class Applied:
    entry_id: str
    written: str
    spoken: str
    start: int  # offsets in the original text
    end: int
    language: str | None = None  # the sentence's language, when chosen per sentence


def _norm(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def applicable(entries: list[Entry], voice_language: str) -> list[Entry]:
    """Entries for this voice language, with project entries overriding global ones."""
    chosen: dict[str, Entry] = {}
    for e in sorted(entries, key=lambda e: e.project_scoped):  # global first, project wins
        if e.language not in ("any", voice_language):
            continue
        for form in e.written_forms:
            key = _norm(form) if e.case_sensitive else _norm(form).casefold()
            chosen[key] = e
    return list({e.id: e for e in chosen.values()}.values())


def apply(text: str, entries: list[Entry]) -> tuple[str, list[Applied]]:
    """Replace written forms with spoken forms in one left-to-right pass."""
    text = _norm(text)
    forms: list[tuple[str, Entry]] = [
        (_norm(f), e) for e in entries for f in e.written_forms if f.strip()
    ]
    if not forms:
        return text, []
    forms.sort(key=lambda fe: len(fe[0]), reverse=True)

    patterns = []
    by_group: dict[str, Entry] = {}
    for k, (form, entry) in enumerate(forms):
        body = re.escape(form)
        if entry.whole_word:
            body = rf"(?<![{_WORD_CHARS}]){body}(?![{_WORD_CHARS}])"
        group = f"f{k}"
        by_group[group] = entry
        patterns.append(
            f"(?P<{group}>{body})" if entry.case_sensitive else f"(?P<{group}>(?i:{body}))"
        )
    regex = re.compile("|".join(patterns))

    out: list[str] = []
    applied: list[Applied] = []
    pos = 0
    for m in regex.finditer(text):
        entry = by_group[m.lastgroup or ""]
        out.append(text[pos : m.start()])
        out.append(entry.spoken)
        applied.append(Applied(entry.id, m.group(0), entry.spoken, m.start(), m.end()))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out), applied


def apply_by_sentence(
    text: str, entries: list[Entry], override: str | None = None
) -> tuple[str, list[Applied]]:
    """Like :func:`apply`, but entries are chosen by each sentence's language.

    For engines that speak both English and Hindi: a Latin-script sentence gets
    English (and "any") entries, a sentence with Devanagari gets Hindi ones,
    using the same sentence boundaries and rule as the engine. ``override``
    ("en" or "hi") is the user's choice for the whole text and wins over the
    script rule. Offsets stay offsets into the original text.
    """
    text = _norm(text)
    by_language: dict[str, list[Entry]] = {}
    out: list[str] = []
    applied: list[Applied] = []
    pos = 0
    for start, end in sentence_spans(text):
        language = override or reading_language(text[start:end])
        chosen = by_language.setdefault(language, applicable(entries, language))
        spoken, got = apply(text[start:end], chosen)
        out.append(text[pos:start])
        out.append(spoken)
        applied.extend(
            replace(a, start=a.start + start, end=a.end + start, language=language) for a in got
        )
        pos = end
    out.append(text[pos:])
    return "".join(out), applied
