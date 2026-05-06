from datetime import datetime

from backend.rpa.api_monitor.models import (
    ApiMonitorSession,
    ApiToolDefinition,
    ApiToolGenerationCandidate,
)


def test_generation_candidate_defaults_are_serializable():
    candidate = ApiToolGenerationCandidate(
        session_id="session-1",
        dedup_key="GET /api/orders",
        method="GET",
        url_pattern="/api/orders",
    )

    dumped = candidate.model_dump(mode="json")

    assert dumped["status"] == "pending"
    assert dumped["source_call_ids"] == []
    assert dumped["sample_call_ids"] == []
    assert dumped["tool_id"] is None
    assert dumped["attempts"] == 0
    assert dumped["retry_after"] is None
    assert dumped["capture_dom_context"] == {}
    assert isinstance(dumped["created_at"], str)
    assert isinstance(dumped["updated_at"], str)


def test_session_contains_generation_candidates_by_default():
    session = ApiMonitorSession(
        user_id="user-1",
        sandbox_session_id="sandbox-1",
    )

    assert session.generation_candidates == []


def test_tool_definition_can_reference_generation_candidate():
    tool = ApiToolDefinition(
        session_id="session-1",
        name="list_orders",
        description="List orders",
        method="GET",
        url_pattern="/api/orders",
        yaml_definition="name: list_orders",
        generation_candidate_id="candidate-1",
    )

    assert tool.generation_candidate_id == "candidate-1"
    assert tool.model_dump(mode="json")["generation_candidate_id"] == "candidate-1"
