"""Candidate scoring for experiments (PRD §20.1-20.3).

Pure functions: no database, no I/O. Quality comes from human review (1-5);
efficiency and storage are normalised against the best candidate in the same
experiment; reliability is the share of finished runs that succeeded.

Mandatory gates (licence, memory, reliability) always override weighted
scores: a candidate that fails a gate is never "recommended", whatever it scores.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

AUDIO_REVIEW_DIMENSIONS = ("pronunciation", "naturalness", "intelligibility", "pacing")

# PRD §20.1. Usability is reviewed as a gate at final selection, not weighted.
AUDIO_WEIGHTS = {
    "experiment": {
        "quality": 0.40,
        "efficiency": 0.25,
        "reliability": 0.15,
        "storage": 0.10,
        "usability": 0.10,
    },
    "final": {"quality": 0.70, "efficiency": 0.10, "reliability": 0.15, "storage": 0.05},
}

MEMORY_SHARE_LIMIT = 0.75  # of the Mac's total memory; leaves room for macOS and the browser
MAX_FAILURE_RATE = 0.05


@dataclass
class RunView:
    status: str  # job state, or "skipped"
    metrics: dict[str, Any] = field(default_factory=dict)
    scores: dict[str, int] = field(default_factory=dict)


@dataclass
class CandidateView:
    key: str
    engine_id: str
    license_class: str
    installed_bytes: int
    runs: list[RunView]
    usability: int | None = None


@dataclass
class Gate:
    id: str
    passed: bool | None  # None = not enough data yet
    message: str


@dataclass
class CandidateSummary:
    key: str
    engine_id: str
    total: int
    completed: int
    failed: int
    pending: int
    skipped: int
    rated: int
    quality: float | None
    quality_by_dimension: dict[str, float]
    median_rtf: float | None
    median_load_s: float | None
    peak_memory_bytes: int | None
    installed_bytes: int
    reliability: float | None
    normalized: dict[str, float | None]
    score_experiment: float | None
    score_final: float | None
    complete: bool
    gates: list[Gate]
    eligible: bool | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


FINISHED_OK = {"completed"}
FINISHED_BAD = {"failed", "interrupted"}


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _weighted(parts: dict[str, float | None], weights: dict[str, float]) -> float | None:
    """Weighted mean over the dimensions that have data, renormalising weights.

    Returns None without quality: a ranking without listening is not a ranking.
    """
    if parts.get("quality") is None:
        return None
    present = {k: w for k, w in weights.items() if parts.get(k) is not None}
    total = sum(present.values())
    return round(100 * sum(parts[k] * w for k, w in present.items()) / total, 1)  # type: ignore[operator]


def summarize(
    candidates: list[CandidateView],
    total_memory_bytes: int,
    dimensions: tuple[str, ...] = AUDIO_REVIEW_DIMENSIONS,
    weights: dict[str, dict[str, float]] | None = None,
) -> list[CandidateSummary]:
    weights = weights or AUDIO_WEIGHTS
    base: list[dict[str, Any]] = []
    for c in candidates:
        attempted = [r for r in c.runs if r.status != "skipped"]
        completed = [r for r in attempted if r.status in FINISHED_OK]
        failed = [r for r in attempted if r.status in FINISHED_BAD]
        pending = [
            r for r in attempted if r.status not in FINISHED_OK | FINISHED_BAD | {"canceled"}
        ]
        by_dim = {
            d: statistics.fmean(r.scores[d] for r in completed if d in r.scores)
            for d in dimensions
            if any(d in r.scores for r in completed)
        }
        all_scores = [r.scores[d] for r in completed for d in dimensions if d in r.scores]
        rtfs = [float(r.metrics["real_time_factor"]) for r in completed
                if isinstance(r.metrics.get("real_time_factor"), int | float)]  # fmt: skip
        loads = [float(r.metrics["load_s"]) for r in attempted
                 if isinstance(r.metrics.get("load_s"), int | float)]  # fmt: skip
        peaks = [int(r.metrics["peak_rss_bytes"]) for r in attempted
                 if isinstance(r.metrics.get("peak_rss_bytes"), int | float)]  # fmt: skip
        finished = len(completed) + len(failed)
        base.append({
            "c": c,
            "total": len(attempted),
            "completed": len(completed),
            "failed": len(failed),
            "pending": len(pending),
            "skipped": len(c.runs) - len(attempted),
            "rated": sum(1 for r in completed if any(d in r.scores for d in dimensions)),
            "quality": statistics.fmean(all_scores) if all_scores else None,
            "by_dim": {k: round(v, 2) for k, v in by_dim.items()},
            "rtf": _median(rtfs),
            "load": _median(loads),
            "peak": max(peaks) if peaks else None,
            "reliability": len(completed) / finished if finished else None,
        })  # fmt: skip

    rtf_values = [b["rtf"] for b in base if b["rtf"]]
    best_rtf = min(rtf_values) if rtf_values else None
    size_values = [b["c"].installed_bytes for b in base if b["c"].installed_bytes > 0]
    best_size = min(size_values) if size_values else None

    out: list[CandidateSummary] = []
    for b in base:
        c: CandidateView = b["c"]
        normalized: dict[str, float | None] = {
            "quality": (b["quality"] - 1) / 4 if b["quality"] is not None else None,
            "efficiency": (best_rtf / b["rtf"]) if best_rtf and b["rtf"] else None,
            "reliability": b["reliability"],
            "storage": (best_size / c.installed_bytes) if best_size and c.installed_bytes else
                       (1.0 if c.installed_bytes == 0 else None),
            "usability": (c.usability - 1) / 4 if c.usability else None,
        }  # fmt: skip
        gates = _gates(c, b, total_memory_bytes)
        decided = [g.passed for g in gates]
        eligible = False if False in decided else (None if None in decided else True)
        out.append(
            CandidateSummary(
                key=c.key,
                engine_id=c.engine_id,
                total=b["total"],
                completed=b["completed"],
                failed=b["failed"],
                pending=b["pending"],
                skipped=b["skipped"],
                rated=b["rated"],
                quality=round(b["quality"], 2) if b["quality"] is not None else None,
                quality_by_dimension=b["by_dim"],
                median_rtf=round(b["rtf"], 4) if b["rtf"] is not None else None,
                median_load_s=round(b["load"], 3) if b["load"] is not None else None,
                peak_memory_bytes=b["peak"],
                installed_bytes=c.installed_bytes,
                reliability=round(b["reliability"], 3) if b["reliability"] is not None else None,
                normalized={
                    k: round(v, 3) if v is not None else None for k, v in normalized.items()
                },
                score_experiment=_weighted(normalized, weights["experiment"]),
                score_final=_weighted(normalized, weights["final"]),
                complete=b["pending"] == 0 and b["rated"] == b["completed"] and b["completed"] > 0,
                gates=gates,
                eligible=eligible,
            )
        )
    return out


def _gates(c: CandidateView, b: dict[str, Any], total_memory_bytes: int) -> list[Gate]:
    gates = []
    if c.license_class in ("restricted", "unknown"):
        gates.append(Gate("licence", False, "Licence is restricted or unverified; review needed."))
    else:
        gates.append(Gate("licence", True, "Licence allows local use."))

    limit = int(total_memory_bytes * MEMORY_SHARE_LIMIT)
    if b["peak"] is None:
        gates.append(Gate("memory", None, "No memory measurement yet."))
    elif b["peak"] > limit:
        gates.append(
            Gate("memory", False, f"Peak memory exceeded {MEMORY_SHARE_LIMIT:.0%} of RAM.")
        )
    else:
        gates.append(Gate("memory", True, "Peak memory within the safe limit."))

    finished = b["completed"] + b["failed"]
    if finished == 0:
        gates.append(Gate("reliability", None, "No finished runs yet."))
    elif b["failed"] / finished > MAX_FAILURE_RATE:
        message = f"{b['failed']} of {finished} runs failed (limit {MAX_FAILURE_RATE:.0%})."
        gates.append(Gate("reliability", False, message))
    else:
        gates.append(Gate("reliability", True, "Runs complete reliably."))
    return gates
