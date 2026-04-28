"""Directed analysis planning and execution for API Monitor."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from backend.deepagent.engine import get_llm_model

from .analysis_modes import AnalysisBusinessSafety


DirectedActionType = Literal["click", "fill", "press", "select", "wait"]
DirectedActionRisk = Literal["safe", "unsafe"]
DirectedGoalStatus = Literal["continue", "done", "blocked"]


class DirectedAction(BaseModel):
    action: DirectedActionType
    locator: Dict[str, Any] = Field(default_factory=dict)
    value: str = ""
    key: str = ""
    timeout_ms: int = 500
    description: str = ""
    risk: DirectedActionRisk = "safe"
    reason: str = ""


class DirectedAnalysisPlan(BaseModel):
    summary: str = ""
    actions: List[DirectedAction] = Field(default_factory=list)


class FilteredDirectedActions(BaseModel):
    allowed: List[DirectedAction] = Field(default_factory=list)
    skipped: List[DirectedAction] = Field(default_factory=list)


class DirectedExecutionResult(BaseModel):
    executed: List[DirectedAction] = Field(default_factory=list)
    skipped: List[DirectedAction] = Field(default_factory=list)


class DirectedStepDecision(BaseModel):
    goal_status: DirectedGoalStatus = "continue"
    summary: str = ""
    next_action: Optional[DirectedAction] = None
    expected_change: str = ""
    done_reason: str = ""


class SingleFilteredDirectedAction(BaseModel):
    allowed: Optional[DirectedAction] = None
    skipped: Optional[DirectedAction] = None


DIRECTED_PLAN_SYSTEM = """\
你是 API Monitor 的浏览器操作规划器。你会收到用户操作目标和精简后的页面 DOM。
只返回 JSON，不要返回 markdown。

返回结构：
{
  "summary": "一句话说明计划",
  "actions": [
    {
      "action": "click|fill|press|select|wait",
      "locator": {"method": "role|text|placeholder|label|css", "role": "button", "name": "搜索", "value": ""},
      "value": "fill/select 使用的值",
      "key": "press 使用的按键",
      "timeout_ms": 500,
      "description": "这个动作做什么",
      "risk": "safe|unsafe",
      "reason": "为什么安全或不安全"
    }
  ]
}

安全判定：
- 搜索、筛选、分页、打开详情、切换 tab、展开区域通常是 safe。
- 删除、注销、支付、提交订单、撤销授权、禁用、不可逆提交通常是 unsafe。

平台约束：
- 只能规划页面内 Playwright 操作。
- 不要规划 shell、文件、权限、下载目录或本地系统操作。
- 不要返回 Python 代码。
"""


DIRECTED_PLAN_USER = """\
用户目标：
{instruction}

当前页面精简 DOM：
{compact_snapshot}

生成最短可执行操作计划。
"""


DIRECTED_STEP_SYSTEM = """\
你是 API Monitor 的动态定向分析控制器。你会收到用户目标、当前页面精简 DOM、运行历史和最新观察事实。
只返回 JSON，不要返回 markdown。

返回结构：
{
  "goal_status": "continue|done|blocked",
  "summary": "本轮判断摘要",
  "next_action": {
    "action": "click|fill|press|select|wait",
    "locator": {"method": "role|text|placeholder|label|css", "role": "button", "name": "搜索", "value": ""},
    "value": "fill/select 使用的值",
    "key": "press 使用的按键",
    "timeout_ms": 500,
    "description": "这个动作做什么",
    "risk": "safe|unsafe",
    "reason": "为什么安全或不安全"
  },
  "expected_change": "执行动作后预期页面或网络发生什么变化",
  "done_reason": "done 或 blocked 时说明原因"
}

规划规则：
- 每次只返回一个下一步动作，不能返回多步计划。
- 当前页面精简 DOM 是事实源，历史动作只说明已经发生过什么。
- 如果 DOM 或 URL 已变化，必须基于新页面推理下一步。
- 目标 API 已捕获或用户目标已满足时返回 done，且 next_action 为空。
- 没有安全或有意义的浏览器动作时返回 blocked，且 next_action 为空。

安全判定：
- 搜索、筛选、分页、打开详情、切换 tab、展开区域通常是 safe。
- 删除、注销、支付、提交订单、撤销授权、禁用、不可逆提交通常是 unsafe。

平台约束：
- 只能规划页面内 Playwright 操作。
- 不要规划 shell、文件、权限、下载目录或本地系统操作。
- 不要返回 Python 代码。
"""


DIRECTED_STEP_USER = """\
用户目标：
{instruction}

当前页面精简 DOM：
{compact_snapshot}

运行历史 run_history：
{run_history}

最新观察 observation：
{observation}

基于当前页面状态决策。每次只返回一个下一步动作，或者返回 done/blocked。
"""


def strip_json_fence(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


async def build_directed_plan(
    *,
    instruction: str,
    compact_snapshot: Dict[str, Any],
    model_config: Optional[Dict] = None,
) -> DirectedAnalysisPlan:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    model = get_llm_model(config=model_config, streaming=False)
    messages = [
        SystemMessage(content=DIRECTED_PLAN_SYSTEM),
        HumanMessage(
            content=DIRECTED_PLAN_USER.format(
                instruction=instruction,
                compact_snapshot=json.dumps(compact_snapshot, ensure_ascii=False, indent=2),
            )
        ),
    ]
    response = await model.ainvoke(messages)
    if isinstance(response, AIMessage):
        raw = response.content or ""
    elif hasattr(response, "content"):
        raw = str(response.content)
    else:
        raw = str(response)

    parsed = json.loads(strip_json_fence(raw))
    return DirectedAnalysisPlan.model_validate(parsed)


async def build_directed_step_decision(
    *,
    instruction: str,
    compact_snapshot: Dict[str, Any],
    run_history: List[Dict[str, Any]],
    observation: Dict[str, Any],
    model_config: Optional[Dict] = None,
) -> DirectedStepDecision:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    model = get_llm_model(config=model_config, streaming=False)
    messages = [
        SystemMessage(content=DIRECTED_STEP_SYSTEM),
        HumanMessage(
            content=DIRECTED_STEP_USER.format(
                instruction=instruction,
                compact_snapshot=json.dumps(compact_snapshot, ensure_ascii=False, indent=2),
                run_history=json.dumps(run_history, ensure_ascii=False, indent=2),
                observation=json.dumps(observation, ensure_ascii=False, indent=2),
            )
        ),
    ]
    response = await model.ainvoke(messages)
    if isinstance(response, AIMessage):
        raw = response.content or ""
    elif hasattr(response, "content"):
        raw = str(response.content)
    else:
        raw = str(response)

    parsed = json.loads(strip_json_fence(raw))
    decision = DirectedStepDecision.model_validate(parsed)
    if decision.goal_status == "continue" and decision.next_action is None:
        return DirectedStepDecision(
            goal_status="blocked",
            summary=decision.summary or "Planner returned continue without next_action",
            done_reason="Planner returned continue without next_action",
        )
    if decision.goal_status != "continue":
        decision.next_action = None
    return decision


def filter_actions_for_business_safety(
    plan: DirectedAnalysisPlan,
    business_safety: AnalysisBusinessSafety,
) -> FilteredDirectedActions:
    if business_safety != "guarded":
        return FilteredDirectedActions(allowed=list(plan.actions), skipped=[])

    allowed: List[DirectedAction] = []
    skipped: List[DirectedAction] = []
    for action in plan.actions:
        if action.risk == "safe":
            allowed.append(action)
        else:
            skipped.append(action)
    return FilteredDirectedActions(allowed=allowed, skipped=skipped)


def filter_action_for_business_safety(
    action: DirectedAction,
    business_safety: AnalysisBusinessSafety,
) -> SingleFilteredDirectedAction:
    if business_safety != "guarded" or action.risk == "safe":
        return SingleFilteredDirectedAction(allowed=action, skipped=None)
    return SingleFilteredDirectedAction(allowed=None, skipped=action)


def describe_action(action: DirectedAction) -> str:
    """Human-readable description of a directed action."""
    parts = [action.action]
    if action.locator:
        method = action.locator.get("method", "")
        if method == "role":
            parts.append(f"{action.locator.get('role', '')}[{action.locator.get('name', '')}]")
        elif method in ("text", "placeholder", "label", "css"):
            parts.append(action.locator.get("value") or action.locator.get("selector") or "")
    if action.value:
        parts.append(f'→ "{action.value}"')
    if action.key:
        parts.append(f"[{action.key}]")
    desc = " ".join(parts)
    if action.description:
        desc += f"  ({action.description})"
    return desc


def describe_locator_code(action: DirectedAction) -> str:
    """Generate pseudo Playwright code for the action."""
    method = action.locator.get("method", "") if action.locator else ""
    locator_str = ""
    if method == "role":
        role = action.locator.get("role", "")
        name = action.locator.get("name")
        locator_str = f'get_by_role("{role}"' + (f', name="{name}")' if name else ')')
    elif method == "text":
        locator_str = f'get_by_text("{action.locator.get("value", "")}")'
    elif method == "placeholder":
        locator_str = f'get_by_placeholder("{action.locator.get("value", "")}")'
    elif method == "label":
        locator_str = f'get_by_label("{action.locator.get("value", "")}")'
    elif method == "css":
        locator_str = f'locator("{action.locator.get("value") or action.locator.get("selector", "")}")'

    if action.action == "wait":
        return f"page.wait_for_timeout({action.timeout_ms})"
    if action.action == "click":
        return f"page.{locator_str}.click()"
    if action.action == "fill":
        return f'page.{locator_str}.fill("{action.value}")'
    if action.action == "press":
        return f'page.{locator_str}.press("{action.key or "Enter"}")'
    if action.action == "select":
        return f'page.{locator_str}.select_option("{action.value}")'
    return f"page.{locator_str}.{action.action}()"


def build_locator(page: Any, locator: Dict[str, Any]) -> Any:
    method = str(locator.get("method") or "").strip()
    if method == "role":
        return page.get_by_role(locator.get("role") or "button", name=locator.get("name") or None)
    if method == "text":
        return page.get_by_text(locator.get("value") or locator.get("text") or locator.get("name") or "")
    if method == "placeholder":
        return page.get_by_placeholder(locator.get("value") or locator.get("placeholder") or "")
    if method == "label":
        return page.get_by_label(locator.get("value") or locator.get("label") or "")
    if method == "css":
        return page.locator(locator.get("value") or locator.get("selector") or "")
    raise ValueError(f"Unsupported directed locator method: {method}")


async def execute_directed_action(page: Any, action: DirectedAction) -> None:
    if action.action == "wait":
        await page.wait_for_timeout(max(0, min(action.timeout_ms, 10_000)))
        return

    target = build_locator(page, action.locator)
    if action.action == "click":
        await target.click()
        await page.wait_for_timeout(500)
        return
    if action.action == "fill":
        await target.fill(action.value)
        await page.wait_for_timeout(300)
        return
    if action.action == "press":
        await target.press(action.key or "Enter")
        await page.wait_for_timeout(500)
        return
    if action.action == "select":
        await target.select_option(action.value)
        await page.wait_for_timeout(300)
        return
    raise ValueError(f"Unsupported directed action: {action.action}")


async def execute_directed_plan(
    page: Any,
    plan: DirectedAnalysisPlan,
    *,
    business_safety: AnalysisBusinessSafety,
    on_action: Optional[Any] = None,
) -> DirectedExecutionResult:
    filtered = filter_actions_for_business_safety(plan, business_safety)
    executed: List[DirectedAction] = []
    for action in filtered.allowed:
        if on_action:
            on_action(action)
        await execute_directed_action(page, action)
        executed.append(action)
    return DirectedExecutionResult(executed=executed, skipped=filtered.skipped)
