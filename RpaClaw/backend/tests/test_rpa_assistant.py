import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ASSISTANT_MODULE = importlib.import_module("backend.rpa.assistant")


class _FakeModel:
    def __init__(self, response):
        self._response = response

    async def ainvoke(self, _messages):
        return self._response


class _FakeStreamingModel:
    def __init__(self, chunks):
        self._chunks = chunks

    async def astream(self, _messages):
        for chunk in self._chunks:
            yield chunk


class _FakePage:
    url = "https://example.com"

    async def title(self):
        return "Example"


class RPAReActAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_llm_preserves_whitespace_between_stream_chunks(self):
        response_text = 'await page.goto("https://github.com/trending?since=weekly")\n'
        stream_chunks = [
            SimpleNamespace(content="await", additional_kwargs={}),
            SimpleNamespace(content=" page", additional_kwargs={}),
            SimpleNamespace(content='.goto("https://github.com/trending?since=weekly")\n', additional_kwargs={}),
        ]

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeStreamingModel(stream_chunks),
        ):
            chunks = []
            async for chunk in ASSISTANT_MODULE.RPAReActAgent._stream_llm([]):
                chunks.append(chunk)

        self.assertEqual(chunks, [response_text])

    async def test_stream_llm_extracts_text_from_stream_content_blocks(self):
        response_text = (
            '{"thought":"task done","action":"done","code":"","description":"done","risk":"none","risk_reason":""}'
        )
        stream_chunks = [
            SimpleNamespace(
                content=[
                    {"type": "thinking", "thinking": "inspect the page"},
                    {"type": "text", "text": response_text},
                ],
                additional_kwargs={},
            ),
        ]

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeStreamingModel(stream_chunks),
        ):
            chunks = []
            async for chunk in ASSISTANT_MODULE.RPAReActAgent._stream_llm([]):
                chunks.append(chunk)

        self.assertEqual(chunks, [response_text])

    async def test_stream_llm_falls_back_to_stream_reasoning_content(self):
        response_text = (
            '{"thought":"task done","action":"done","code":"","description":"done","risk":"none","risk_reason":""}'
        )
        stream_chunks = [
            SimpleNamespace(
                content="",
                additional_kwargs={"reasoning_content": response_text},
            ),
        ]

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeStreamingModel(stream_chunks),
        ):
            chunks = []
            async for chunk in ASSISTANT_MODULE.RPAReActAgent._stream_llm([]):
                chunks.append(chunk)

        self.assertEqual(chunks, [response_text])

    async def test_stream_llm_extracts_text_from_content_blocks(self):
        response_text = (
            '{"thought":"task done","action":"done","code":"","description":"done","risk":"none","risk_reason":""}'
        )
        fake_response = SimpleNamespace(
            content=[
                {"type": "thinking", "thinking": "inspect the page"},
                {"type": "text", "text": response_text},
            ],
            additional_kwargs={},
        )

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeModel(fake_response),
        ):
            chunks = []
            async for chunk in ASSISTANT_MODULE.RPAReActAgent._stream_llm([]):
                chunks.append(chunk)

        self.assertEqual(chunks, [response_text])

    async def test_run_falls_back_to_reasoning_content_when_text_is_empty(self):
        response_text = (
            '{"thought":"task done","action":"done","code":"","description":"done","risk":"none","risk_reason":""}'
        )
        fake_response = SimpleNamespace(
            content="",
            additional_kwargs={"reasoning_content": response_text},
        )
        agent = ASSISTANT_MODULE.RPAReActAgent()

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeModel(fake_response),
        ), patch.object(
            ASSISTANT_MODULE,
            "_get_page_elements",
            new=AsyncMock(return_value="[]"),
        ):
            events = []
            async for event in agent.run(
                session_id="session-1",
                page=_FakePage(),
                goal="finish the task",
                existing_steps=[],
            ):
                events.append(event)

        self.assertEqual(
            [event["event"] for event in events],
            ["agent_thought", "agent_done"],
        )


if __name__ == "__main__":
    unittest.main()
