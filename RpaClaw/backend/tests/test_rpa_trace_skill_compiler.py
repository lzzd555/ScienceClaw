from backend.rpa.trace_models import (
    RPAAcceptedTrace,
    RPAAIExecution,
    RPADataflowMapping,
    RPAPageState,
    RPATargetField,
    RPATraceType,
)
from backend.rpa.trace_skill_compiler import TraceSkillCompiler


def _execute_body(script: str) -> str:
    start = script.index("async def execute_skill")
    return script[start:]


def test_compiler_renders_navigation_trace():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.NAVIGATION,
                after_page=RPAPageState(url="https://github.com/trending"),
            )
        ],
        is_local=True,
    )

    assert "async def execute_skill" in script
    assert "https://github.com/trending" in script


def test_compiler_generalizes_highest_star_trace_instead_of_hardcoding_url():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_id="trace-star",
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                user_instruction="open the project with the highest star count",
                output_key="selected_project",
                output={"url": "https://github.com/recorded/repo"},
                ai_execution=RPAAIExecution(
                    language="python",
                    code="async def run(page, results):\n    return {'url': 'https://github.com/recorded/repo'}",
                ),
            )
        ],
        is_local=True,
    )

    assert "stargazers" in script
    assert "max_stars" in script
    assert "https://github.com/recorded/repo" not in _execute_body(script)


def test_compiler_preserves_pr_record_extraction_as_python_playwright():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_id="trace-prs",
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                user_instruction="collect the first 10 PRs in the current repository with title and creator",
                output_key="top10_prs",
                output=[{"title": "Fix bug", "creator": "alice"}],
                ai_execution=RPAAIExecution(
                    language="python",
                    code="async def run(page, results):\n    return [{'title': 'Fix bug', 'creator': 'alice'}]",
                ),
            )
        ],
        is_local=True,
    )

    assert "top10_prs" in script
    assert "page.evaluate" not in script
    assert "_validate_non_empty_records('top10_prs', _result)" in script


def test_compiler_uses_source_ref_for_dataflow_fill():
    trace = RPAAcceptedTrace(
        trace_id="fill-1",
        trace_type=RPATraceType.DATAFLOW_FILL,
        source="manual",
        action="fill",
        value="Alice Zhang",
        dataflow=RPADataflowMapping(
            target_field=RPATargetField(
                label="Customer Name",
                locator_candidates=[{"locator": {"method": "role", "role": "textbox", "name": "Customer Name"}}],
            ),
            value="Alice Zhang",
            source_ref_candidates=["customer_info.name"],
            selected_source_ref="customer_info.name",
            confidence="exact_value_match",
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)

    assert "customer_info.name" in script
    assert "await current_page.get_by_role('textbox', name='Customer Name').fill(str(_value))" in script
    assert "Alice Zhang" not in _execute_body(script)


def test_navigation_after_selected_project_uses_dynamic_result_url():
    traces = [
        RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            user_instruction="open the project most related to Python",
            output_key="selected_project",
            output={"url": "https://github.com/openai/openai-agents-python"},
            ai_execution=RPAAIExecution(
                code="async def run(page, results):\n    return {'url': 'https://github.com/openai/openai-agents-python'}",
            ),
        ),
        RPAAcceptedTrace(
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://github.com/openai/openai-agents-python/pulls"),
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)

    assert "_resolve_result_ref(_results, 'selected_project.url')" in script
    assert "+ '/pulls'" in script
