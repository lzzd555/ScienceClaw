from backend.rpa.trace_models import (
    RPAAcceptedTrace,
    RPAAIExecution,
    RPAPageState,
    RPARuntimeResults,
    RPATraceType,
)


def test_ai_operation_trace_serializes_execution_and_page_state():
    trace = RPAAcceptedTrace(
        trace_id="trace-1",
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="open the project with the highest star count",
        before_page=RPAPageState(url="https://github.com/trending", title="Trending"),
        after_page=RPAPageState(url="https://github.com/owner/repo", title="owner/repo"),
        ai_execution=RPAAIExecution(
            language="python",
            code="async def run(page, results):\n    return {'url': page.url}",
            output={"selected_project": {"url": "https://github.com/owner/repo"}},
        ),
        accepted=True,
    )

    payload = trace.model_dump()

    assert payload["trace_type"] == "ai_operation"
    assert payload["before_page"]["url"] == "https://github.com/trending"
    assert payload["ai_execution"]["language"] == "python"
    assert payload["accepted"] is True


def test_runtime_results_resolves_dotted_refs_and_list_indexes():
    results = RPARuntimeResults(
        values={
            "customer_info": {
                "name": "Alice Zhang",
                "emails": ["alice@example.com"],
            }
        }
    )

    assert results.resolve_ref("customer_info.name") == "Alice Zhang"
    assert results.resolve_ref("customer_info.emails.0") == "alice@example.com"
    assert results.find_value_refs("Alice Zhang") == ["customer_info.name"]

