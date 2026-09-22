import uuid

from analyst.grounding import GroundingFailure


def test_invented_reference_message_names_the_offending_id():
    bad = uuid.uuid4()
    message = GroundingFailure([bad], [], []).message()
    assert str(bad) in message
    assert "do not exist" in message


def test_foreign_reference_message_explains_the_rule():
    """The model must learn why it was rejected, not just that it was —
    otherwise it retries with the same ID."""
    message = GroundingFailure([], [uuid.uuid4()], []).message()
    assert "different investigation" in message
    assert "read other investigations for context" in message


def test_every_failure_tells_the_model_what_to_do_next():
    for failure in (
        GroundingFailure([uuid.uuid4()], [], []),
        GroundingFailure([], [uuid.uuid4()], []),
        GroundingFailure([], [], [uuid.uuid4()]),
    ):
        assert "Re-issue create_investigation_result" in failure.message()


def test_combined_failures_are_all_reported():
    """One round trip should surface every problem, not the first."""
    message = GroundingFailure([uuid.uuid4()], [uuid.uuid4()], [uuid.uuid4()]).message()
    assert "do not exist" in message
    assert "different investigation" in message
    assert "entity_ids do not exist" in message
