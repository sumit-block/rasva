"""Job state machine (PRD §21.4).

Terminal states are immutable; a retry creates a new job linked via ``retry_of``
so history stays explainable. There is deliberately no "paused" state: engines
do not support true inference pause/resume.
"""

from __future__ import annotations

from enum import StrEnum


class JobState(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PREPARING = "preparing"
    LOADING_MODEL = "loading_model"
    PROCESSING = "processing"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    INTERRUPTED = "interrupted"


TERMINAL: frozenset[JobState] = frozenset(
    {JobState.COMPLETED, JobState.FAILED, JobState.CANCELED, JobState.INTERRUPTED}
)

# States in which a worker process may be doing work on the job.
ACTIVE: frozenset[JobState] = frozenset(
    {
        JobState.DOWNLOADING,
        JobState.PREPARING,
        JobState.LOADING_MODEL,
        JobState.PROCESSING,
        JobState.FINALIZING,
    }
)

_S = JobState
TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    _S.DRAFT: frozenset({_S.QUEUED, _S.CANCELED}),
    _S.QUEUED: frozenset({_S.DOWNLOADING, _S.PREPARING, _S.CANCELED, _S.FAILED}),
    _S.DOWNLOADING: frozenset({_S.PREPARING, _S.FAILED, _S.CANCELED, _S.INTERRUPTED}),
    _S.PREPARING: frozenset(
        {_S.LOADING_MODEL, _S.PROCESSING, _S.FAILED, _S.CANCELED, _S.INTERRUPTED}
    ),
    _S.LOADING_MODEL: frozenset({_S.PROCESSING, _S.FAILED, _S.CANCELED, _S.INTERRUPTED}),
    _S.PROCESSING: frozenset({_S.FINALIZING, _S.FAILED, _S.CANCELED, _S.INTERRUPTED}),
    # Finalizing is a short, local file move; it cannot be canceled half-way.
    _S.FINALIZING: frozenset({_S.COMPLETED, _S.FAILED, _S.INTERRUPTED}),
    _S.COMPLETED: frozenset(),
    _S.FAILED: frozenset(),
    _S.CANCELED: frozenset(),
    _S.INTERRUPTED: frozenset(),
}


class InvalidTransition(Exception):
    def __init__(self, current: JobState, target: JobState) -> None:
        super().__init__(f"Cannot move job from {current} to {target}")
        self.current = current
        self.target = target


def check_transition(current: JobState, target: JobState) -> None:
    if target not in TRANSITIONS[current]:
        raise InvalidTransition(current, target)
