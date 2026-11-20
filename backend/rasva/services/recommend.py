"""Automatic engine and voice choice (PRD §5 "smart defaults", A5).

Simple mode never asks which engine to use. For a script language this picks,
among installed engines that support narration in that language:

1. the engine+voice the owner rated best *for that language* in experiments,
   if at least MIN_RATED of its finished runs in that language were rated and it
   passed every gate (licence, memory, reliability);
2. otherwise the first such engine's default voice for the language.

The reason is returned in plain language so the UI can show why.
"""

from __future__ import annotations

import sqlite3
import statistics
from typing import Any

from rasva.audio.text import script_counts
from rasva.engines.registry import EngineRegistry
from rasva.experiments import service as experiments
from rasva.experiments.scoring import AUDIO_REVIEW_DIMENSIONS
from rasva.services.installer import ModelStore

MIN_RATED = 3
RECENT_EXPERIMENTS = 10
# Which benchmark categories count as evidence for which voice language.
LANGUAGE_CATEGORIES = {
    "hi": {"hindi", "names", "mixed"},
    "en": {"english", "hinglish"},
}
LANGUAGE_NAME = {"hi": "Hindi", "en": "English"}


def _usable_narrators(conn: sqlite3.Connection, registry: EngineRegistry) -> list[Any]:
    store = ModelStore(conn)
    return [
        e
        for e in registry.all()
        if "audio.narrate" in e.descriptor.tasks
        and not e.descriptor.experimental
        and (not e.descriptor.requires_install or store.is_installed(e.descriptor.id))
    ]


def recommend(
    conn: sqlite3.Connection, registry: EngineRegistry, language: str, total_memory: int
) -> dict[str, Any] | None:
    engines = [
        e for e in _usable_narrators(conn, registry)
        if any(v.language == language for v in e.descriptor.voices)
    ]  # fmt: skip
    if not engines:
        return None
    usable_voices = {
        (e.descriptor.id, v.id)
        for e in engines
        for v in e.descriptor.voices
        if v.language == language
    }
    categories = LANGUAGE_CATEGORIES.get(language, set())

    best: dict[str, Any] | None = None
    narration = [e for e in experiments.list_experiments(conn) if e["task"] == "audio.narrate"]
    for exp in narration[:RECENT_EXPERIMENTS]:
        detail = experiments.get(conn, registry, exp["id"], total_memory)
        eligible = {s["key"]: s["eligible"] for s in detail["summary"]}
        settings = {c["key"]: c for c in detail["candidates"]}
        per_candidate: dict[str, list[float]] = {}
        for run in detail["runs"]:
            if run["status"] != "completed":
                continue
            if run["category"] == "custom":
                # Your own sentences count for the language they are written in.
                has_hindi = script_counts(run["text"] or "")["devanagari"] > 0
                if ("hi" if has_hindi else "en") != language:
                    continue
            elif run["category"] not in categories:
                continue
            scores = [run["scores"][d] for d in AUDIO_REVIEW_DIMENSIONS if d in run["scores"]]
            if scores:
                per_candidate.setdefault(run["candidate_key"], []).append(statistics.fmean(scores))
        for key, values in per_candidate.items():
            cand = settings.get(key)
            if cand is None or eligible.get(key) is False or len(values) < MIN_RATED:
                continue
            voice_id = cand["settings"].get("voice_id")
            if (cand["engine_id"], voice_id) not in usable_voices:
                continue
            quality = statistics.fmean(values)
            if best is None or quality > best["quality"]:
                best = {
                    "engine_id": cand["engine_id"], "voice_id": voice_id,
                    "quality": round(quality, 2), "rated": len(values),
                    "experiment_id": exp["id"], "experiment_name": exp["name"],
                }  # fmt: skip

    if best is not None:
        engine = registry.get(best["engine_id"])
        voice = next(v for v in engine.descriptor.voices if v.id == best["voice_id"])
        return {
            "engine_id": best["engine_id"],
            "voice_id": best["voice_id"],
            "reason": (
                f"{engine.descriptor.display_name} · {voice.display_name}: you rated it "
                f"{best['quality']:.1f}/5 for {LANGUAGE_NAME.get(language, language)} in "
                f"“{best['experiment_name']}”."
            ),
            "evidence": best,
        }

    engine = engines[0]
    d = engine.descriptor
    voice_id = d.default_voices.get(language) or next(
        v.id for v in d.voices if v.language == language
    )
    voice = next(v for v in d.voices if v.id == voice_id)
    return {
        "engine_id": d.id,
        "voice_id": voice_id,
        "reason": (
            f"{d.display_name} · {voice.display_name} is the default "
            f"{LANGUAGE_NAME.get(language, language)} voice. Rate voices in an experiment "
            "and Rasva will use your favourite."
        ),
        "evidence": None,
    }
