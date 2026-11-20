import pytest

from rasva.jobs.states import TERMINAL, TRANSITIONS, InvalidTransition, JobState, check_transition


def test_happy_path_is_allowed():
    path = [
        JobState.QUEUED,
        JobState.PREPARING,
        JobState.LOADING_MODEL,
        JobState.PROCESSING,
        JobState.FINALIZING,
        JobState.COMPLETED,
    ]
    for a, b in zip(path, path[1:], strict=False):
        check_transition(a, b)


@pytest.mark.parametrize("terminal", sorted(TERMINAL))
def test_terminal_states_are_final(terminal):
    assert TRANSITIONS[terminal] == frozenset()
    with pytest.raises(InvalidTransition):
        check_transition(terminal, JobState.QUEUED)


def test_finalizing_cannot_be_canceled():
    with pytest.raises(InvalidTransition):
        check_transition(JobState.FINALIZING, JobState.CANCELED)


def test_every_state_has_rules():
    assert set(TRANSITIONS) == set(JobState)
