import pytest
from playwright.async_api import async_playwright

from backend.rpa.trace_models import (
    RPAAcceptedTrace,
    RPAAIExecution,
    RPADataflowMapping,
    RPAPageState,
    RPATargetField,
    RPATraceType,
)
from backend.rpa.trace_skill_compiler import TraceSkillCompiler


def _load_execute_skill(script: str):
    namespace = {}
    exec(script, namespace, namespace)
    return namespace["execute_skill"]


@pytest.mark.asyncio
async def test_generated_highest_star_skill_recomputes_current_page():
    trace = RPAAcceptedTrace(
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
    execute_skill = _load_execute_skill(TraceSkillCompiler().generate_script([trace], is_local=True))

    html = """
    <html><body>
      <article class="Box-row">
        <h2><a href="/small/repo">small / repo</a></h2>
        <a href="/small/repo/stargazers">10</a>
      </article>
      <article class="Box-row">
        <h2><a href="/big/repo">big / repo</a></h2>
        <a href="/big/repo/stargazers">99</a>
      </article>
    </body></html>
    """

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.route("https://github.com/big/repo", lambda route: route.fulfill(body="<html>big repo</html>"))
    await page.set_content(html)
    result = await execute_skill(page)
    await browser.close()
    await pw.stop()

    assert result["selected_project"]["url"] == "https://github.com/big/repo"


@pytest.mark.asyncio
async def test_generated_pr_extraction_skill_returns_records_from_current_page():
    trace = RPAAcceptedTrace(
        trace_id="trace-pr",
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="collect the first 10 PR titles and creators",
        output_key="top10_prs",
        output=[{"title": "Recorded", "creator": "alice"}],
        ai_execution=RPAAIExecution(code="async def run(page, results):\n    return []"),
    )
    execute_skill = _load_execute_skill(TraceSkillCompiler().generate_script([trace], is_local=True))

    html = """
    <html><body>
      <div class="Box-row">
        <a class="Link--primary" href="/owner/repo/pull/2">Fix parser</a>
        <a data-hovercard-type="user" href="/alice">alice</a>
      </div>
      <div class="Box-row">
        <a class="Link--primary" href="/owner/repo/pull/1">Add docs</a>
        <a data-hovercard-type="user" href="/bob">bob</a>
      </div>
    </body></html>
    """

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content(html)
    result = await execute_skill(page)
    await browser.close()
    await pw.stop()

    assert result["top10_prs"][:2] == [
        {"title": "Fix parser", "creator": "alice", "url": "https://github.com/owner/repo/pull/2"},
        {"title": "Add docs", "creator": "bob", "url": "https://github.com/owner/repo/pull/1"},
    ]


@pytest.mark.asyncio
async def test_generated_dataflow_skill_fills_from_previous_runtime_result():
    traces = [
        RPAAcceptedTrace(
            trace_id="capture",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="capture customer info",
            output_key="customer_info",
            output={"name": "Alice Zhang"},
            ai_execution=RPAAIExecution(
                code="async def run(page, results):\n    return {'name': 'Alice Zhang'}",
            ),
        ),
        RPAAcceptedTrace(
            trace_id="fill",
            trace_type=RPATraceType.DATAFLOW_FILL,
            source="manual",
            action="fill",
            value="Alice Zhang",
            dataflow=RPADataflowMapping(
                target_field=RPATargetField(
                    locator_candidates=[
                        {"locator": {"method": "role", "role": "textbox", "name": "Customer Name"}}
                    ],
                ),
                value="Alice Zhang",
                source_ref_candidates=["customer_info.name"],
                selected_source_ref="customer_info.name",
                confidence="exact_value_match",
            ),
        ),
    ]
    execute_skill = _load_execute_skill(TraceSkillCompiler().generate_script(traces, is_local=True))

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content("<label>Customer Name <input /></label>")
    result = await execute_skill(page)
    filled = await page.get_by_role("textbox", name="Customer Name").input_value()
    await browser.close()
    await pw.stop()

    assert result["customer_info"]["name"] == "Alice Zhang"
    assert filled == "Alice Zhang"


@pytest.mark.asyncio
async def test_generated_skill_replays_trending_semantic_project_to_pr_extraction_flow():
    traces = [
        RPAAcceptedTrace(
            trace_id="nav-trending",
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://github.com/trending"),
        ),
        RPAAcceptedTrace(
            trace_id="select-python",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="open the project most related to Python",
            description="open the project most related to Python",
            output_key="selected_project",
            output={
                "name": "openai/openai-agents-python",
                "url": "https://github.com/openai/openai-agents-python",
            },
            ai_execution=RPAAIExecution(code="async def run(page, results):\n    return {}"),
        ),
        RPAAcceptedTrace(
            trace_id="nav-pulls",
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://github.com/openai/openai-agents-python/pulls"),
        ),
        RPAAcceptedTrace(
            trace_id="extract-prs",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="collect the first 10 PR titles and creators",
            output_key="top10_prs",
            output=[{"title": "Recorded", "creator": "alice"}],
            ai_execution=RPAAIExecution(code="async def run(page, results):\n    return []"),
        ),
    ]
    execute_skill = _load_execute_skill(TraceSkillCompiler().generate_script(traces, is_local=True))

    trending_html = """
    <html><body>
      <article class="Box-row">
        <h2><a href="/other/js-tool">other / js-tool</a></h2>
        <p>JavaScript utility</p>
      </article>
      <article class="Box-row">
        <h2><a href="/openai/openai-agents-python">openai / openai-agents-python</a></h2>
        <p>A Python framework for building agents</p>
      </article>
    </body></html>
    """
    pulls_html = """
    <html><body>
      <div class="Box-row">
        <a class="Link--primary" href="/openai/openai-agents-python/pull/20">Add memory backend</a>
        <a data-hovercard-type="user" href="/alice">alice</a>
      </div>
    </body></html>
    """

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.route("https://github.com/trending", lambda route: route.fulfill(body=trending_html))
    await page.route(
        "https://github.com/openai/openai-agents-python",
        lambda route: route.fulfill(body="<html><body>repo</body></html>"),
    )
    await page.route(
        "https://github.com/openai/openai-agents-python/pulls",
        lambda route: route.fulfill(body=pulls_html),
    )

    result = await execute_skill(page)
    await browser.close()
    await pw.stop()

    assert result["selected_project"]["url"] == "https://github.com/openai/openai-agents-python"
    assert result["top10_prs"] == [
        {
            "title": "Add memory backend",
            "creator": "alice",
            "url": "https://github.com/openai/openai-agents-python/pull/20",
        }
    ]
