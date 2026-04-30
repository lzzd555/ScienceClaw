"""Trace helpers for API Monitor directed analysis."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Iterable

from .directed_analyzer import DirectedAction
from .models import (
    CapturedApiCall,
    DirectedAnalysisTrace,
    DirectedDecisionSnapshot,
    DirectedExecutionSnapshot,
    DirectedObservation,
)


CONSECUTIVE_FAILURE_LIMIT = 2
TOTAL_FAILURE_SKIP_LIMIT = 3
LOOP_WINDOW = 4


def directed_action_fingerprint(action: DirectedAction | dict[str, Any] | None) -> str:
    if action is None:
        return ""
    payload = action.model_dump() if isinstance(action, DirectedAction) else dict(action)
    locator = payload.get("locator") if isinstance(payload.get("locator"), dict) else {}
    parts = [
        str(payload.get("action") or ""),
        str(locator.get("method") or ""),
        str(locator.get("role") or ""),
        str(locator.get("name") or locator.get("value") or locator.get("selector") or locator.get("text") or ""),
        str(payload.get("value") or payload.get("key") or ""),
    ]
    return "|".join(part for part in parts if part)


def observation_from_payload(payload: dict[str, Any]) -> DirectedObservation:
    compact = payload.get("compact_snapshot")
    summary = _compact_snapshot_summary(compact if isinstance(compact, dict) else {})
    return DirectedObservation(
        url=str(payload.get("url") or ""),
        title=str(payload.get("title") or ""),
        dom_digest=str(payload.get("dom_digest") or ""),
        compact_snapshot_summary=summary,
    )


def decision_snapshot(decision: Any) -> DirectedDecisionSnapshot:
    action = getattr(decision, "next_action", None)
    action_payload = action.model_dump(mode="json") if action is not None else None
    return DirectedDecisionSnapshot(
        goal_status=str(getattr(decision, "goal_status", "") or "continue"),
        summary=str(getattr(decision, "summary", "") or ""),
        expected_change=str(getattr(decision, "expected_change", "") or ""),
        done_reason=str(getattr(decision, "done_reason", "") or ""),
        action=action_payload,
        risk=str((action_payload or {}).get("risk") or "safe"),
    )


def execution_snapshot(
    *,
    result: str,
    error: str = "",
    before: DirectedObservation | None = None,
    after: DirectedObservation | None = None,
    started_at: datetime | None = None,
) -> DirectedExecutionSnapshot:
    duration_ms = None
    if started_at is not None:
        duration_ms = max(0, int((datetime.now() - started_at).total_seconds() * 1000))
    return DirectedExecutionSnapshot(
        result=result,
        error=error,
        duration_ms=duration_ms,
        url_changed=bool(before and after and before.url != after.url),
        dom_changed=bool(before and after and before.dom_digest != after.dom_digest),
    )


def build_directed_retry_context(
    traces: list[DirectedAnalysisTrace],
    *,
    captured_api_summary: list[dict[str, Any]],
) -> dict[str, Any]:
    recent = traces[-10:]
    recent_summary = [_trace_summary(trace) for trace in recent]
    active_traces = _traces_since_last_success(traces)
    failed = [
        trace
        for trace in active_traces
        if _trace_result(trace) in {"failed", "planner_failed", "retry_guard_skipped"}
    ]
    blocked = _blocked_actions(active_traces)
    loop_detected = _loop_detected(failed)
    if loop_detected:
        for fingerprint in _recent_failed_fingerprints(failed)[-LOOP_WINDOW:]:
            if fingerprint and not any(item["fingerprint"] == fingerprint for item in blocked):
                blocked.append({"fingerprint": fingerprint, "reason": "最近失败呈现 A/B/A/B 循环"})
    return {
        "recent_traces": recent_summary,
        "blocked_actions": blocked,
        "block_steps": blocked,
        "loop_detected": loop_detected,
        "successful_transitions": [
            item
            for item in recent_summary
            if item["result"] == "executed" and (item["url_changed"] or item["dom_changed"])
        ],
        "captured_api_summary": captured_api_summary,
    }


def retry_guard_skip_reason(action_fingerprint: str, traces: list[DirectedAnalysisTrace]) -> str:
    if not action_fingerprint:
        return ""
    active_traces = _traces_since_last_success(traces)
    failures = [
        trace
        for trace in active_traces
        if trace.action_fingerprint == action_fingerprint
        and _trace_result(trace) in {"failed", "retry_guard_skipped"}
    ]
    if len(failures) >= TOTAL_FAILURE_SKIP_LIMIT:
        return f"动作指纹 {action_fingerprint} 已失败 {len(failures)} 次"
    return ""


def captured_call_ids(calls: Iterable[CapturedApiCall]) -> list[str]:
    return [str(call.id) for call in calls]


def _compact_snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    nodes = snapshot.get("actionable_nodes")
    return {
        "url": snapshot.get("url") or "",
        "title": snapshot.get("title") or "",
        "actionable_count": len(nodes) if isinstance(nodes, list) else 0,
    }


def _trace_result(trace: DirectedAnalysisTrace) -> str:
    return trace.execution.result if trace.execution else ""


def _traces_since_last_success(traces: list[DirectedAnalysisTrace]) -> list[DirectedAnalysisTrace]:
    for index in range(len(traces) - 1, -1, -1):
        if _trace_result(traces[index]) == "executed":
            return traces[index + 1 :]
    return traces


def _trace_summary(trace: DirectedAnalysisTrace) -> dict[str, Any]:
    return {
        "step": trace.step,
        "result": _trace_result(trace),
        "fingerprint": trace.action_fingerprint or "",
        "error": trace.execution.error if trace.execution else "",
        "url": trace.after.url if trace.after else trace.before.url,
        "title": trace.after.title if trace.after else trace.before.title,
        "url_changed": bool(trace.execution and trace.execution.url_changed),
        "dom_changed": bool(trace.execution and trace.execution.dom_changed),
        "new_calls": list(trace.captured_call_ids),
    }


def _recent_failed_fingerprints(failed: list[DirectedAnalysisTrace]) -> list[str]:
    return [trace.action_fingerprint or "" for trace in failed if trace.action_fingerprint]


def _loop_detected(failed: list[DirectedAnalysisTrace]) -> bool:
    recent = _recent_failed_fingerprints(failed)[-LOOP_WINDOW:]
    return (
        len(recent) == LOOP_WINDOW
        and recent[0] == recent[2]
        and recent[1] == recent[3]
        and recent[0] != recent[1]
    )


def _blocked_actions(traces: list[DirectedAnalysisTrace]) -> list[dict[str, str]]:
    blocked: list[dict[str, str]] = []
    consecutive: list[DirectedAnalysisTrace] = []
    for trace in reversed(traces):
        result = _trace_result(trace)
        if result == "executed":
            break
        if result in {"failed", "retry_guard_skipped"} and trace.action_fingerprint:
            consecutive.append(trace)
            continue
        break
    if len(consecutive) >= CONSECUTIVE_FAILURE_LIMIT:
        fingerprint = consecutive[0].action_fingerprint or ""
        if all(trace.action_fingerprint == fingerprint for trace in consecutive[:CONSECUTIVE_FAILURE_LIMIT]):
            blocked.append({"fingerprint": fingerprint, "reason": f"连续 {CONSECUTIVE_FAILURE_LIMIT} 次失败"})

    counts = Counter(
        trace.action_fingerprint
        for trace in traces
        if trace.action_fingerprint and _trace_result(trace) in {"failed", "retry_guard_skipped"}
    )
    for fingerprint, count in counts.items():
        if count >= TOTAL_FAILURE_SKIP_LIMIT and not any(item["fingerprint"] == fingerprint for item in blocked):
            blocked.append({"fingerprint": fingerprint, "reason": f"累计失败 {count} 次"})
    return blocked
