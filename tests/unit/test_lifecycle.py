import pytest

from core.lifecycle import TERMINAL, VALID_TRANSITIONS, InvalidTransition, Status, is_valid


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (Status.PENDING, Status.ENRICHING),
        (Status.ENRICHING, Status.ANALYZING),
        (Status.ANALYZING, Status.CORRELATING),
        (Status.CORRELATING, Status.COMPLETE),
    ],
)
def test_happy_path_transitions_are_valid(source, target):
    assert is_valid(source, target)


@pytest.mark.parametrize("source", [s for s in Status if s not in TERMINAL])
def test_any_active_stage_can_fail_or_cancel(source):
    assert is_valid(source, Status.FAILED)
    assert is_valid(source, Status.CANCELLED)


@pytest.mark.parametrize("source", sorted(TERMINAL))
def test_terminal_statuses_have_no_exits(source):
    assert VALID_TRANSITIONS[source] == frozenset()


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (Status.ANALYZING, Status.ENRICHING),  # backwards
        (Status.PENDING, Status.COMPLETE),  # skips stages
        (Status.COMPLETE, Status.ENRICHING),  # out of terminal
        (Status.CANCELLED, Status.ANALYZING),
        (Status.FAILED, Status.COMPLETE),
    ],
)
def test_invalid_transitions_are_rejected(source, target):
    assert not is_valid(source, target)


def test_every_status_has_a_transition_rule():
    """A new status must be added to the table deliberately, not by accident."""
    assert set(VALID_TRANSITIONS) == set(Status)


def test_invalid_transition_is_an_error_type_we_can_catch():
    assert issubclass(InvalidTransition, ValueError)
