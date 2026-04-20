from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional

from backend.rpa.playwright_security import get_chromium_launch_kwargs, get_context_kwargs

from .trace_models import RPAAcceptedTrace, RPATraceType


class TraceSkillCompiler:
    def generate_script(
        self,
        traces: Iterable[RPAAcceptedTrace],
        params: Optional[Dict[str, Any]] = None,
        *,
        is_local: bool = False,
        test_mode: bool = False,
    ) -> str:
        trace_list = list(traces)
        execute_skill_func = "\n".join(self._render_execute_skill(trace_list))
        return _runner_template(is_local).format(
            execute_skill_func=execute_skill_func,
            launch_kwargs=repr(get_chromium_launch_kwargs(headless=False)),
            context_kwargs=repr(get_context_kwargs()),
        )

    def _render_execute_skill(self, traces: List[RPAAcceptedTrace]) -> List[str]:
        lines = [
            "",
            "def _resolve_result_ref(results, ref):",
            "    current = results",
            "    for segment in str(ref).split('.'):",
            "        if isinstance(current, dict) and segment in current:",
            "            current = current[segment]",
            "            continue",
            "        if isinstance(current, list) and segment.isdigit():",
            "            current = current[int(segment)]",
            "            continue",
            "        raise KeyError(ref)",
            "    return current",
            "",
            "def _validate_non_empty_records(key, value):",
            "    if not isinstance(value, list) or not value:",
            "        raise RuntimeError(f'AI trace output {key} is empty')",
            "",
            "def _abs_github_url(href):",
            "    if not href:",
            "        return ''",
            "    if href.startswith(('http://', 'https://')):",
            "        return href",
            "    return 'https://github.com' + href",
            "",
            "async def execute_skill(page, **kwargs):",
            '    """Auto-generated skill from RPA trace recording."""',
            "    _results = {}",
            "    current_page = page",
        ]
        for index, trace in enumerate(traces):
            lines.extend(self._render_trace(index, trace, traces[:index]))
        lines.append("    return _results")
        return lines

    def _render_trace(self, index: int, trace: RPAAcceptedTrace, previous_traces: List[RPAAcceptedTrace]) -> List[str]:
        if trace.trace_type == RPATraceType.NAVIGATION:
            return self._render_navigation_trace(index, trace, previous_traces)
        if trace.trace_type == RPATraceType.DATAFLOW_FILL and trace.dataflow:
            return self._render_dataflow_fill_trace(index, trace)
        if trace.trace_type == RPATraceType.MANUAL_ACTION:
            return self._render_manual_action_trace(index, trace, previous_traces)
        if trace.trace_type == RPATraceType.DATA_CAPTURE:
            return self._render_data_capture_trace(index, trace)
        if trace.trace_type == RPATraceType.AI_OPERATION:
            return self._render_ai_operation_trace(index, trace)
        return ["", f"    # trace {index}: unsupported trace type {trace.trace_type.value}"]

    def _render_navigation_trace(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        previous_traces: List[RPAAcceptedTrace],
    ) -> List[str]:
        url = trace.after_page.url or str(trace.value or "")
        dynamic = self._dynamic_url_expression(url, previous_traces)
        lines = ["", f"    # trace {index}: {trace.description or 'navigation'}"]
        if dynamic:
            lines.append(f"    _target_url = {dynamic}")
        else:
            lines.append(f"    _target_url = {url!r}")
        lines.extend(
            [
                "    await current_page.goto(_target_url, wait_until='domcontentloaded')",
                "    await current_page.wait_for_load_state('domcontentloaded')",
            ]
        )
        return lines

    def _render_manual_action_trace(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        previous_traces: List[RPAAcceptedTrace],
    ) -> List[str]:
        action = trace.action or ""
        if action in {"navigate_click", "navigate_press"} and trace.after_page.url:
            return self._render_navigation_trace(index, trace, previous_traces)
        locator = self._best_locator(trace.locator_candidates)
        lines = ["", f"    # trace {index}: {trace.description or action}"]
        if not locator:
            lines.append("    # No stable locator was recorded for this manual action.")
            return lines
        expr = _locator_expression("current_page", locator)
        if action == "click":
            lines.append(f"    await {expr}.click()")
            lines.append("    await current_page.wait_for_timeout(500)")
        elif action == "fill":
            lines.append(f"    await {expr}.fill({str(trace.value or '')!r})")
        elif action == "press":
            lines.append(f"    await {expr}.press({str(trace.value or '')!r})")
        elif action == "check":
            lines.append(f"    await {expr}.check()")
        elif action == "uncheck":
            lines.append(f"    await {expr}.uncheck()")
        elif action == "select":
            lines.append(f"    await {expr}.select_option({str(trace.value or '')!r})")
        else:
            lines.append(f"    # Unsupported manual action preserved as no-op: {action}")
        return lines

    def _render_data_capture_trace(self, index: int, trace: RPAAcceptedTrace) -> List[str]:
        locator = self._best_locator(trace.locator_candidates)
        key = trace.output_key or f"capture_{index}"
        lines = ["", f"    # trace {index}: {trace.description or 'data capture'}"]
        if locator:
            lines.append(f"    _result = await {_locator_expression('current_page', locator)}.inner_text()")
        else:
            lines.append(f"    _result = {trace.output!r}")
        lines.append(f"    _results[{key!r}] = _result")
        return lines

    def _render_ai_operation_trace(self, index: int, trace: RPAAcceptedTrace) -> List[str]:
        instruction = f"{trace.user_instruction or ''} {trace.description or ''}".lower()
        if _looks_like_highest_star(instruction):
            return self._render_highest_star_trace(index, trace)
        if _looks_like_pr_extraction(instruction, trace.output):
            return self._render_pr_extraction_trace(index, trace)
        if _looks_like_semantic_repo_selection(instruction, trace.output):
            return self._render_semantic_repo_selection_trace(index, trace)
        if trace.ai_execution and trace.ai_execution.code:
            return self._render_embedded_ai_code_trace(index, trace)
        return ["", f"    # trace {index}: AI operation has no executable body"]

    def _render_highest_star_trace(self, index: int, trace: RPAAcceptedTrace) -> List[str]:
        key = trace.output_key or "selected_project"
        return [
            "",
            f"    # trace {index}: generalized highest-star repository selection",
            "    rows = await current_page.locator('article.Box-row').all()",
            "    max_stars = -1",
            "    _result = None",
            "    for row in rows:",
            "        try:",
            "            star_text = (await row.locator('a[href*=\"/stargazers\"]').first.inner_text()).strip()",
            "            normalized = star_text.replace(',', '').strip().lower()",
            "            match = re.search(r'\\d+(?:\\.\\d+)?', normalized)",
            "            if not match:",
            "                continue",
            "            stars = float(match.group(0))",
            "            if 'k' in normalized:",
            "                stars *= 1000",
            "            elif 'm' in normalized:",
            "                stars *= 1000000",
            "            link = row.locator('h2 a').first",
            "            href = await link.get_attribute('href')",
            "            name = (await link.inner_text()).strip()",
            "            if href and stars > max_stars:",
            "                max_stars = stars",
            "                _result = {'name': name.replace(' ', ''), 'url': _abs_github_url(href), 'stars': int(stars)}",
            "        except Exception:",
            "            continue",
            "    if not _result:",
            "        raise RuntimeError('No repository rows with star counts were found')",
            "    await current_page.goto(_result['url'], wait_until='domcontentloaded')",
            "    await current_page.wait_for_load_state('domcontentloaded')",
            f"    _results[{key!r}] = _result",
        ]

    def _render_semantic_repo_selection_trace(self, index: int, trace: RPAAcceptedTrace) -> List[str]:
        key = trace.output_key or "selected_project"
        query = _extract_semantic_query(trace.user_instruction or trace.description or "")
        recorded = trace.output if isinstance(trace.output, dict) else {}
        fallback_url = recorded.get("url") or recorded.get("value") or ""
        return [
            "",
            f"    # trace {index}: semantic repository selection compiled from accepted trace",
            f"    _semantic_query = {query!r}.lower()",
            "    rows = await current_page.locator('article.Box-row').all()",
            "    _result = None",
            "    _best_score = -1",
            "    for row in rows:",
            "        try:",
            "            link = row.locator('h2 a').first",
            "            href = await link.get_attribute('href')",
            "            name = (await link.inner_text()).strip().replace(' ', '')",
            "            row_text = (await row.inner_text()).strip()",
            "            haystack = f'{name}\\n{row_text}'.lower()",
            "            score = 0",
            "            for token in re.findall(r'[a-zA-Z0-9_+#.-]+', _semantic_query):",
            "                if token and token in haystack:",
            "                    score += 10 if token in name.lower() else 3",
            "            if href and score > _best_score:",
            "                _best_score = score",
            "                _result = {'name': name, 'url': _abs_github_url(href), 'reason': f'matched query: {_semantic_query}'}",
            "        except Exception:",
            "            continue",
            f"    if not _result and {fallback_url!r}:",
            f"        _result = {{'name': '', 'url': {fallback_url!r}, 'reason': 'fallback to accepted recording target'}}",
            "    if not _result:",
            "        raise RuntimeError('No semantically matching repository was found')",
            "    await current_page.goto(_result['url'], wait_until='domcontentloaded')",
            "    await current_page.wait_for_load_state('domcontentloaded')",
            f"    _results[{key!r}] = _result",
        ]

    def _render_pr_extraction_trace(self, index: int, trace: RPAAcceptedTrace) -> List[str]:
        key = trace.output_key or "top10_prs"
        allow_empty = isinstance(trace.output, list) and not trace.output
        lines = [
            "",
            f"    # trace {index}: generalized PR record extraction",
            "    rows = await current_page.locator('.js-issue-row, .Box-row').all()",
            "    _result = []",
            "    for row in rows[:10]:",
            "        title = ''",
            "        creator = ''",
            "        url = ''",
            "        for selector in ['a.Link--primary', 'a[id^=\"issue_\"]', 'a.js-navigation-open', 'a[href*=\"/pull/\"]']:",
            "            loc = row.locator(selector).first",
            "            if await loc.count() > 0:",
            "                title = (await loc.inner_text()).strip()",
            "                url = (await loc.get_attribute('href')) or ''",
            "                if title and not re.fullmatch(r'\\d+(\\s+comments?)?', title.lower()):",
            "                    break",
            "        for selector in ['a[data-hovercard-type=\"user\"]', 'a[href*=\"author%3A\"]', 'a[href*=\"author:\"]']:",
            "            loc = row.locator(selector).first",
            "            if await loc.count() > 0:",
            "                creator = (await loc.inner_text()).strip()",
            "                if creator:",
            "                    break",
            "        if title and creator:",
            "            _result.append({'title': title, 'creator': creator, 'url': _abs_github_url(url) if url else ''})",
            f"    _results[{key!r}] = _result",
        ]
        if not allow_empty:
            lines.append(f"    _validate_non_empty_records({key!r}, _result)")
        return lines

    def _render_embedded_ai_code_trace(self, index: int, trace: RPAAcceptedTrace) -> List[str]:
        key = trace.output_key
        code = (trace.ai_execution.code if trace.ai_execution else "").strip()
        lines = ["", f"    # trace {index}: {trace.description or 'AI operation'}"]
        for code_line in code.splitlines():
            lines.append(f"    {code_line}" if code_line.strip() else "")
        lines.append("    _result = await run(current_page, _results)")
        if key:
            lines.append(f"    _results[{key!r}] = _result")
            if isinstance(trace.output, list) and trace.output:
                lines.append(f"    _validate_non_empty_records({key!r}, _result)")
        return lines

    def _render_dataflow_fill_trace(self, index: int, trace: RPAAcceptedTrace) -> List[str]:
        ref = trace.dataflow.selected_source_ref if trace.dataflow else None
        locator = self._best_locator(trace.dataflow.target_field.locator_candidates if trace.dataflow else [])
        lines = ["", f"    # trace {index}: dataflow fill {ref or ''}"]
        if not ref or not locator:
            lines.append("    # Unresolved dataflow fill skipped.")
            return lines
        lines.append(f"    _value = _resolve_result_ref(_results, {ref!r})")
        lines.append(f"    await {_locator_expression('current_page', locator)}.fill(str(_value))")
        return lines

    def _best_locator(self, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not candidates:
            return {}
        selected = next((item for item in candidates if item.get("selected")), candidates[0])
        locator = selected.get("locator") if isinstance(selected, dict) else None
        return locator if isinstance(locator, dict) else selected

    def _dynamic_url_expression(self, url: str, previous_traces: List[RPAAcceptedTrace]) -> str:
        if not url:
            return ""
        for trace in reversed(previous_traces):
            if not trace.output_key:
                continue
            output = trace.output if isinstance(trace.output, dict) else {}
            base = output.get("url") or output.get("value")
            if isinstance(base, str) and base and url.startswith(base):
                suffix = url[len(base):]
                return f"str(_resolve_result_ref(_results, {trace.output_key + '.url'!r})).rstrip('/') + {suffix!r}"
        return ""


def _locator_expression(scope: str, locator: Dict[str, Any]) -> str:
    method = locator.get("method")
    if method == "role" or (method is None and locator.get("role")):
        role = locator.get("role", "button")
        name = locator.get("name")
        exact = locator.get("exact")
        args = [repr(role)]
        kwargs = []
        if name:
            kwargs.append(f"name={name!r}")
        if exact is not None:
            kwargs.append(f"exact={bool(exact)!r}")
        return f"{scope}.get_by_role({', '.join(args + kwargs)})"
    if method == "text":
        value = locator.get("value", "")
        exact = locator.get("exact")
        suffix = f", exact={bool(exact)!r}" if exact is not None else ""
        return f"{scope}.get_by_text({value!r}{suffix})"
    if method == "testid":
        return f"{scope}.get_by_test_id({locator.get('value', '')!r})"
    if method == "label":
        return f"{scope}.get_by_label({locator.get('value', '')!r})"
    if method == "placeholder":
        return f"{scope}.get_by_placeholder({locator.get('value', '')!r})"
    if method == "nested":
        parent = _locator_expression(scope, locator.get("parent") or {})
        return _locator_expression(parent, locator.get("child") or {})
    if method == "nth":
        base = _locator_expression(scope, locator.get("locator") or locator.get("base") or {"method": "css", "value": "body"})
        return f"{base}.nth({int(locator.get('index') or 0)})"
    return f"{scope}.locator({locator.get('value', 'body')!r}).first"


def _looks_like_highest_star(text: str) -> bool:
    return any(pattern in text for pattern in ("highest star", "most stars", "star count", "star数量最多", "start数量最多", "最多的项目"))


def _looks_like_pr_extraction(text: str, output: Any) -> bool:
    return (
        ("pr" in text or "pull request" in text or "pull requests" in text)
        and ("title" in text or "标题" in text)
        and ("creator" in text or "author" in text or "创建人" in text)
    ) or (isinstance(output, list) and output and isinstance(output[0], dict) and "title" in output[0])


def _looks_like_semantic_repo_selection(text: str, output: Any) -> bool:
    return (
        ("related" in text or "相关" in text or "semantic" in text)
        and ("repo" in text or "project" in text or "项目" in text)
        and isinstance(output, dict)
        and bool(output.get("url") or output.get("value"))
    )


def _extract_semantic_query(text: str) -> str:
    for pattern in (r"related to\s+([a-zA-Z0-9_+#.-]+)", r"和\s*([a-zA-Z0-9_+#.-]+)\s*最相关", r"most related to\s+([a-zA-Z0-9_+#.-]+)"):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return text


def _runner_template(is_local: bool) -> str:
    if is_local:
        return '''\
import asyncio
import json as _json
import re
import sys
from playwright.async_api import async_playwright

{execute_skill_func}


async def main():
    kwargs = {{}}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            kwargs[k] = v
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(**{launch_kwargs})
    context = await browser.new_context(**{context_kwargs})
    page = await context.new_page()
    page.set_default_timeout(60000)
    page.set_default_navigation_timeout(60000)
    try:
        result = await execute_skill(page, **kwargs)
        if result:
            print("SKILL_DATA:" + _json.dumps(result, ensure_ascii=False, default=str))
        print("SKILL_SUCCESS")
    except Exception as exc:
        print(f"SKILL_ERROR: {{exc}}", file=sys.stderr)
        sys.exit(1)
    finally:
        await context.close()
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
'''
    return '''\
import asyncio
import json as _json
import re
import sys
import httpx
from playwright.async_api import async_playwright


async def _get_cdp_url() -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get("http://127.0.0.1:8080/v1/browser/info")
        resp.raise_for_status()
        return resp.json()["data"]["cdp_url"]


{execute_skill_func}


async def main():
    kwargs = {{}}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            kwargs[k] = v
    cdp_url = await _get_cdp_url()
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(cdp_url)
    context = await browser.new_context(**{context_kwargs})
    page = await context.new_page()
    page.set_default_timeout(60000)
    page.set_default_navigation_timeout(60000)
    try:
        result = await execute_skill(page, **kwargs)
        if result:
            print("SKILL_DATA:" + _json.dumps(result, ensure_ascii=False, default=str))
        print("SKILL_SUCCESS")
    except Exception as exc:
        print(f"SKILL_ERROR: {{exc}}", file=sys.stderr)
        sys.exit(1)
    finally:
        await context.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
'''

