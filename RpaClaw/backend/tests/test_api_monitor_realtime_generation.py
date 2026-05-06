import unittest
from datetime import datetime

from unittest.mock import patch

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


# ── Upsert and reconcile helper tests ─────────────────────────────────────


from backend.rpa.api_monitor.manager import ApiMonitorSessionManager
from backend.rpa.api_monitor.models import CapturedApiCall, CapturedRequest, CapturedResponse


def _call(call_id: str, url: str = "https://example.com/api/orders?page=1") -> CapturedApiCall:
    return CapturedApiCall(
        id=call_id,
        request=CapturedRequest(
            request_id=call_id,
            url=url,
            method="GET",
            headers={},
            timestamp=datetime(2026, 1, 1),
            resource_type="fetch",
        ),
        response=CapturedResponse(
            status=200,
            status_text="OK",
            headers={"content-type": "application/json"},
            body='{"items":[{"id":1}]}',
            content_type="application/json",
            timestamp=datetime(2026, 1, 1),
        ),
        url_pattern="/api/orders?page={page}",
    )


def _manager_with_session() -> tuple[ApiMonitorSessionManager, str]:
    manager = ApiMonitorSessionManager()
    session = ApiMonitorSession(
        id="session-1",
        user_id="user-1",
        sandbox_session_id="sandbox-1",
        target_url="https://example.com/app",
    )
    manager.sessions[session.id] = session
    return manager, session.id


def test_upsert_generation_candidate_creates_placeholder():
    manager, session_id = _manager_with_session()
    call = _call("call-1")

    candidate, created = manager._upsert_generation_candidate(
        session_id,
        call,
        dom_context={"inputs": [{"name": "page"}]},
        page_url="https://example.com/app",
        title="Orders",
        dom_digest="digest-1",
    )

    assert created is True
    assert candidate.status == "pending"
    assert candidate.method == "GET"
    assert candidate.url_pattern == "/api/orders?page={page}"
    assert candidate.source_call_ids == ["call-1"]
    assert candidate.sample_call_ids == ["call-1"]
    assert candidate.capture_dom_context["inputs"][0]["name"] == "page"
    assert manager.sessions[session_id].generation_candidates == [candidate]


def test_upsert_generation_candidate_dedups_and_marks_generated_stale():
    manager, session_id = _manager_with_session()
    first = _call("call-1", "https://example.com/api/orders?page=1")
    second = _call("call-2", "https://example.com/api/orders?page=2")

    candidate, _ = manager._upsert_generation_candidate(session_id, first)
    candidate.status = "generated"
    candidate.tool_id = "tool-1"

    updated, created = manager._upsert_generation_candidate(session_id, second)

    assert created is False
    assert updated.id == candidate.id
    assert updated.status == "stale"
    assert updated.source_call_ids == ["call-1", "call-2"]
    assert updated.sample_call_ids == ["call-1", "call-2"]


def test_reconcile_generation_candidates_rebuilds_missing_candidate():
    manager, session_id = _manager_with_session()
    session = manager.sessions[session_id]
    session.captured_calls.append(_call("call-1"))

    candidates = manager.reconcile_generation_candidates(session_id, enqueue=False)

    assert len(candidates) == 1
    assert candidates[0].source_call_ids == ["call-1"]
    assert session.generation_candidates[0].dedup_key == "GET /api/orders"


# ── Worker success path tests ────────────────────────────────────────────


class TestGenerateToolForCandidate(unittest.IsolatedAsyncioTestCase):

    async def test_generate_candidate_creates_tool_and_applies_confidence(self):
        manager, session_id = _manager_with_session()
        session = manager.sessions[session_id]
        # Provide source_evidence so the confidence scorer gives a high score:
        #   action_window_matched=True (+30), business_path (+25),
        #   json_response (+20), has_source (+15), response_richness (+10) = 100
        call = _call("call-1")
        call.source_evidence = {
            "action_window_matched": True,
            "initiator_urls": ["https://example.com/app"],
            "js_stack_urls": [],
            "frame_url": "https://example.com/app",
        }
        session.captured_calls.append(call)
        candidate, _ = manager._upsert_generation_candidate(
            session_id,
            call,
            dom_context={"forms": [{"inputs": [{"name": "page"}]}]},
            page_url="https://example.com/app",
            title="Orders",
            dom_digest="digest-1",
        )

        async def fake_generate_tool_definition(**kwargs):
            assert kwargs["method"] == "GET"
            assert kwargs["url_pattern"] == "/api/orders?page={page}"
            assert "page" in kwargs["dom_context"]
            return (
                "name: list_orders\n"
                "description: List orders\n"
                "method: GET\n"
                "url: /api/orders\n"
                "parameters:\n  type: object\n  properties: {}\n"
                "response:\n  type: object\n  properties: {}\n"
            )

        with patch(
            "backend.rpa.api_monitor.manager.generate_tool_definition",
            fake_generate_tool_definition,
        ):
            tool = await manager._generate_tool_for_candidate(session_id, candidate.id)

        assert tool is not None
        assert tool.name == "list_orders"
        assert tool.generation_candidate_id == candidate.id
        assert tool.source_calls == ["call-1"]
        assert tool.confidence == "high"
        assert tool.selected is True
        assert candidate.status == "generated"
        assert candidate.tool_id == tool.id
        assert session.tool_definitions == [tool]

    async def test_candidate_generation_rate_limit_sets_retry_after(self):
        manager, session_id = _manager_with_session()
        session = manager.sessions[session_id]
        call = _call("call-1")
        session.captured_calls.append(call)
        candidate, _ = manager._upsert_generation_candidate(session_id, call)

        async def fake_generate_tool_definition(**kwargs):
            raise RuntimeError("429 rate limit exceeded")

        with patch(
            "backend.rpa.api_monitor.manager.generate_tool_definition",
            fake_generate_tool_definition,
        ):
            tool = await manager._generate_tool_for_candidate(session_id, candidate.id)

        assert tool is None
        assert candidate.status == "rate_limited"
        assert candidate.attempts == 1
        assert candidate.retry_after is not None
        assert "429" in candidate.error

    async def test_candidate_generation_failure_keeps_captured_call(self):
        manager, session_id = _manager_with_session()
        session = manager.sessions[session_id]
        call = _call("call-1")
        session.captured_calls.append(call)
        candidate, _ = manager._upsert_generation_candidate(session_id, call)

        async def fake_generate_tool_definition(**kwargs):
            raise ValueError("bad yaml")

        with patch(
            "backend.rpa.api_monitor.manager.generate_tool_definition",
            fake_generate_tool_definition,
        ):
            tool = await manager._generate_tool_for_candidate(session_id, candidate.id)

        assert tool is None
        assert candidate.status == "failed"
        assert candidate.attempts == 1
        assert candidate.error == "bad yaml"
        assert session.captured_calls == [call]


# ── Processing helper tests ───────────────────────────────────────────────


class TestProcessCapturedCalls(unittest.IsolatedAsyncioTestCase):

    async def test_process_captured_calls_appends_session_and_enqueues(self):
        manager, session_id = _manager_with_session()
        enqueued: list[tuple[str, str]] = []

        def fake_enqueue(session_id_arg: str, candidate_id: str, **kwargs):
            enqueued.append((session_id_arg, candidate_id))

        with patch.object(manager, "_enqueue_generation_candidate", side_effect=fake_enqueue):
            calls = [_call("call-1")]
            candidates = await manager._process_captured_calls_for_generation(
                session_id,
                calls,
                dom_context={"inputs": [{"name": "q"}]},
                page_url="https://example.com/app",
                title="Orders",
                dom_digest="digest-1",
            )

        session = manager.sessions[session_id]
        assert session.captured_calls == calls
        assert len(candidates) == 1
        assert enqueued == [(session_id, candidates[0].id)]


# ── Retry generation candidate tests ───────────────────────────────────


def test_retry_generation_candidate_resets_failed_candidate():
    manager, session_id = _manager_with_session()
    call = _call("call-1")
    manager.sessions[session_id].captured_calls.append(call)
    candidate, _ = manager._upsert_generation_candidate(session_id, call)
    candidate.status = "failed"
    candidate.error = "bad yaml"
    candidate.attempts = 2
    enqueued: list[str] = []

    # Patch enqueue to track calls
    import unittest.mock
    with unittest.mock.patch.object(
        manager,
        "_enqueue_generation_candidate",
        side_effect=lambda sid, cid, **kw: enqueued.append(cid),
    ):
        result = manager.retry_generation_candidate(session_id, candidate.id)

    assert result.id == candidate.id
    assert result.status == "pending"
    assert result.error == ""
    assert result.retry_after is None
    assert enqueued == [candidate.id]
