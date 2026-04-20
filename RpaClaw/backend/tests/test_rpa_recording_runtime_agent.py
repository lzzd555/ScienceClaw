import json
from types import SimpleNamespace

import pytest

from backend.rpa.recording_runtime_agent import RecordingRuntimeAgent, _parse_json_object


class _FakePage:
    url = "https://example.test/start"

    async def title(self):
        return "Example"


@pytest.mark.asyncio
async def test_recording_runtime_agent_accepts_successful_python_plan():
    plans = [
        {
            "description": "Extract title",
            "action_type": "run_python",
            "output_key": "page_title",
            "code": "async def run(page, results):\n    return {'title': await page.title()}",
        }
    ]

    async def planner(_payload):
        return plans.pop(0)

    agent = RecordingRuntimeAgent(planner=planner)
    result = await agent.run(page=_FakePage(), instruction="extract title", runtime_results={})

    assert result.success is True
    assert result.trace.output_key == "page_title"
    assert result.trace.output == {"title": "Example"}
    assert result.trace.ai_execution.repair_attempted is False


@pytest.mark.asyncio
async def test_recording_runtime_agent_repairs_once_after_failure():
    calls = []

    async def planner(payload):
        calls.append(payload)
        if "repair" not in payload:
            return {
                "description": "Broken",
                "action_type": "run_python",
                "code": "async def run(page, results):\n    raise RuntimeError('boom')",
            }
        return {
            "description": "Fixed",
            "action_type": "run_python",
            "output_key": "fixed",
            "code": "async def run(page, results):\n    return {'ok': True}",
        }

    agent = RecordingRuntimeAgent(planner=planner)
    result = await agent.run(page=_FakePage(), instruction="do it", runtime_results={})

    assert result.success is True
    assert len(calls) == 2
    assert result.trace.ai_execution.repair_attempted is True
    assert result.diagnostics[0].message == "boom"


def test_parse_json_object_accepts_fenced_json():
    payload = {
        "description": "Run",
        "action_type": "run_python",
        "code": "async def run(page, results):\n    return {'ok': True}",
    }

    parsed = _parse_json_object("prefix\n```json\n" + json.dumps(payload) + "\n```")

    assert parsed["description"] == "Run"
    assert "async def run(page, results)" in parsed["code"]


def test_parse_json_object_rejects_run_python_without_runner():
    payload = {"description": "Bad", "action_type": "run_python", "code": "print('bad')"}

    with pytest.raises(ValueError):
        _parse_json_object(json.dumps(payload))

