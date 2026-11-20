"""Script analysis and sentence segmentation for narration (PRD §10, §12.1).

Pure Python with no engine dependencies, so it is testable anywhere and shared
by every audio engine. Engines receive short, sentence-bounded segments; they
should never have to split text themselves (Kokoro, for example, does not know
the Devanagari danda and silently truncates long Hindi input).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

Script = Literal["devanagari", "latin", "mixed", "none"]

# A script counts as present only above this share of letters, so a stray
# English acronym in Hindi text does not flip the whole script to "mixed".
MIXED_THRESHOLD = 0.15

SENTENCE_PAUSE_S = 0.30
PARAGRAPH_PAUSE_S = 0.65
DEFAULT_MAX_CHARS = 220

_TERMINATORS = ".!?।॥…"
_CLOSERS = "\"'”’)]»"
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "eg", "ie",
    "no", "vol", "ch", "fig", "approx", "dept", "govt", "rs", "smt", "shri",
}  # fmt: skip
_SOFT_BREAKS = re.compile(r"(?<=[,;:—–])\s+")
# Devanagari abbreviations written with a full stop (डॉ. = Dr., प्रो. = Prof.).
_DEVANAGARI_ABBREVIATIONS = {"डॉ", "प्रो", "श्री", "श्रीमती", "सुश्री", "कु", "पं", "स्व", "ई", "सं"}


@dataclass(frozen=True)
class Segment:
    index: int
    text: str
    paragraph: int
    pause_after_s: float


def script_counts(text: str) -> dict[str, int]:
    deva = latin = 0
    for ch in text:
        if "ऀ" <= ch <= "ॿ" or "꣠" <= ch <= "ꣿ":
            if unicodedata.category(ch).startswith("L") or unicodedata.category(ch).startswith("M"):
                deva += 1
        elif ch.isascii() and ch.isalpha():
            latin += 1
    return {"devanagari": deva, "latin": latin}


def detect_script(text: str) -> Script:
    c = script_counts(text)
    total = c["devanagari"] + c["latin"]
    if total == 0:
        return "none"
    deva_share = c["devanagari"] / total
    if deva_share >= 1 - MIXED_THRESHOLD:
        return "devanagari"
    if deva_share <= MIXED_THRESHOLD:
        return "latin"
    return "mixed"


def normalize_whitespace(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r" *\n *", "\n", text).strip()


def _is_abbreviation(before: str) -> bool:
    deva = re.search(r"([\u0900-\u097f]+)$", before)
    if deva:
        return deva.group(1) in _DEVANAGARI_ABBREVIATIONS
    word = re.search(r"([A-Za-z.]+)$", before)
    if not word:
        return False
    raw = word.group(1)
    token = raw.replace(".", "").lower()
    # Dotted forms ("p.m", "e.g", "U.S") are abbreviations; single capitals are
    # initials ("A. P. J. Abdul Kalam").
    return (
        token in _ABBREVIATIONS or "." in raw.strip(".") or (len(token) == 1 and raw[0].isupper())
    )


def split_sentences(paragraph: str) -> list[str]:
    sentences: list[str] = []
    start = 0
    i = 0
    n = len(paragraph)
    while i < n:
        ch = paragraph[i]
        if ch in _TERMINATORS:
            j = i + 1
            while j < n and paragraph[j] in _TERMINATORS:
                j += 1
            while j < n and paragraph[j] in _CLOSERS:
                j += 1
            at_boundary = j >= n or paragraph[j].isspace()
            if at_boundary and not (ch == "." and _is_abbreviation(paragraph[start:i])):
                sentence = paragraph[start:j].strip()
                if sentence:
                    sentences.append(sentence)
                start = j
            i = j
            continue
        i += 1
    tail = paragraph[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _split_long(sentence: str, max_chars: int) -> list[str]:
    if len(sentence) <= max_chars:
        return [sentence]
    pieces: list[str] = []
    current = ""
    for part in _SOFT_BREAKS.split(sentence):
        candidate = f"{current} {part}".strip() if current else part
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            pieces.append(current)
        if len(part) <= max_chars:
            current = part
            continue
        # No punctuation to break on: fall back to word boundaries.
        current = ""
        for word in part.split(" "):
            candidate = f"{current} {word}".strip() if current else word
            if len(candidate) <= max_chars or not current:
                current = candidate
            else:
                pieces.append(current)
                current = word
    if current:
        pieces.append(current)
    return pieces


def segment(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[Segment]:
    """Split a script into sentence-bounded segments with natural pauses."""
    paragraphs = [p for p in normalize_whitespace(text).split("\n") if p.strip()]
    out: list[Segment] = []
    for p_index, paragraph in enumerate(paragraphs):
        pieces = [
            piece
            for sentence in split_sentences(paragraph)
            for piece in _split_long(sentence, max_chars)
        ]
        for k, piece in enumerate(pieces):
            last_in_paragraph = k == len(pieces) - 1
            out.append(
                Segment(
                    index=len(out),
                    text=piece,
                    paragraph=p_index,
                    pause_after_s=PARAGRAPH_PAUSE_S if last_in_paragraph else SENTENCE_PAUSE_S,
                )
            )
    return out
