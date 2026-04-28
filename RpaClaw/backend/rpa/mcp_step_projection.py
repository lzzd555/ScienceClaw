from __future__ import annotations

from datetime import datetime
from typing import Any

from .manual_recording_models import ManualRecordedAction
from .trace_models import RPAAcceptedTrace, RPATraceType
from .trace_recorder import recorded_action_to_trace


def _first_locator_candidate(trace: RPAAcceptedTrace) -> dict[str, Any]:
    for candidate in trace.locator_candidates or []:
        if candidate.get("selected"):
            return candidate.get("locator") or candidate
    if trace.locator_candidates:
        candidate = trace.locator_candidates[0]
        return candidate.get("locator") or candidate
    return {}


def _step_by_id(session: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for step in getattr(session, "steps", None) or []:
        step_id = str(getattr(step, "id", "") or "")
        if step_id:
            result[step_id] = step
    return result


def _merge_step_metadata(trace: RPAAcceptedTrace, step: Any | None) -> RPAAcceptedTrace:
    if step is None:
        return trace
    timestamp = getattr(step, "timestamp", None)
    if timestamp is not None:
        trace.started_at = timestamp
        trace.ended_at = timestamp

    signals = getattr(step, "signals", None)
    merged_signals: dict[str, Any] = dict(trace.signals or {})
    if isinstance(signals, dict) and signals:
        merged_signals.update(signals)

    recording_signal: dict[str, Any] = {}
    for key in ("sequence", "event_timestamp_ms"):
        value = getattr(step, key, None)
        if value is not None:
            recording_signal[key] = value
    if recording_signal:
        existing = merged_signals.get("recording") if isinstance(merged_signals.get("recording"), dict) else {}
        merged_signals["recording"] = {**existing, **recording_signal}

    trace.signals = merged_signals
    return trace


def _trace_order_ms(trace: RPAAcceptedTrace) -> float | None:
    started_at = getattr(trace, "started_at", None)
    if started_at is not None:
        try:
            return started_at.timestamp() * 1000
        except OSError:
            return (
                started_at.replace(tzinfo=None) - datetime(1970, 1, 1)
            ).total_seconds() * 1000

    recording = (trace.signals or {}).get("recording") if isinstance(trace.signals, dict) else None
    if isinstance(recording, dict):
        value = recording.get("event_timestamp_ms")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _step_order_ms(step: Any | None) -> float | None:
    if step is None:
        return None
    timestamp = getattr(step, "timestamp", None)
    if timestamp is not None:
        try:
            return timestamp.timestamp() * 1000
        except OSError:
            return (
                timestamp.replace(tzinfo=None) - datetime(1970, 1, 1)
            ).total_seconds() * 1000
    event_timestamp_ms = getattr(step, "event_timestamp_ms", None)
    if isinstance(event_timestamp_ms, (int, float)):
        return float(event_timestamp_ms)
    return None


def _order_projected_steps(projected: list[tuple[float | None, int, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [
        step
        for _, _, _, step in sorted(
            (
                (0 if order_ms is not None else 1, order_ms or 0, index, step)
                for order_ms, index, step in projected
            ),
            key=lambda item: (item[0], item[1], item[2]),
        )
    ]


def recorded_action_to_mcp_step(action: ManualRecordedAction, *, step: Any | None = None) -> dict[str, Any]:
    trace = _merge_step_metadata(recorded_action_to_trace(action), step)
    page_state = action.page_state if isinstance(action.page_state, dict) else {}
    return {
        "id": action.step_id or trace.trace_id,
        "action": action.action_kind.value,
        "target": action.target or {},
        "frame_path": list(action.frame_path or trace.frame_path or []),
        "locator_candidates": action.raw_candidates or trace.locator_candidates,
        "validation": dict(action.validation or {}),
        "signals": dict(trace.signals or {}),
        "value": action.value,
        "description": action.description or action.action_kind.value,
        "label": action.action_kind.value,
        "url": str(page_state.get("url", "") or trace.after_page.url or ""),
        "source": "record",
        "configurable": True,
        "rpa_trace": trace.model_dump(mode="json"),
    }


def trace_to_mcp_step(trace: RPAAcceptedTrace) -> dict[str, Any]:
    locator = _first_locator_candidate(trace)
    if trace.trace_type == RPATraceType.AI_OPERATION:
        action = "ai_script"
        value = trace.ai_execution.code if trace.ai_execution else ""
    elif trace.trace_type == RPATraceType.NAVIGATION:
        action = "navigate"
        value = trace.value
    else:
        action = trace.action or trace.trace_type.value
        value = trace.value

    return {
        "id": trace.trace_id,
        "action": action,
        "target": locator,
        "frame_path": list(trace.frame_path or []),
        "locator_candidates": list(trace.locator_candidates or []),
        "validation": dict(trace.validation or {}),
        "signals": dict(trace.signals or {}),
        "value": value,
        "description": trace.description or trace.user_instruction or trace.trace_type.value,
        "label": trace.user_instruction or trace.action or trace.trace_type.value,
        "url": trace.after_page.url or "",
        "source": "ai" if trace.source == "ai" or trace.trace_type == RPATraceType.AI_OPERATION else "record",
        "prompt": trace.user_instruction,
        "result_key": trace.output_key,
        "configurable": False,
        "rpa_trace": trace.model_dump(mode="json"),
    }


def session_to_mcp_steps(session: Any) -> list[dict[str, Any]]:
    recorded_actions = list(getattr(session, "recorded_actions", None) or [])
    traces = list(getattr(session, "traces", None) or [])
    legacy_steps = list(getattr(session, "steps", None) or [])

    if not recorded_actions and not traces:
        return [step.model_dump(mode="json") for step in legacy_steps]
    if not recorded_actions:
        return [trace_to_mcp_step(trace) for trace in traces]

    steps_by_id = _step_by_id(session)
    actions_by_trace_id = {
        f"trace-{action.step_id}": action
        for action in recorded_actions
        if action.step_id
    }
    emitted_action_ids: set[str] = set()
    projected: list[tuple[float | None, int, dict[str, Any]]] = []

    for index, trace in enumerate(traces):
        replacement = actions_by_trace_id.get(trace.trace_id)
        if replacement is not None:
            step = steps_by_id.get(replacement.step_id)
            order_ms = _step_order_ms(step)
            if order_ms is None:
                order_ms = _trace_order_ms(trace)
            projected.append((order_ms, index, recorded_action_to_mcp_step(replacement, step=step)))
            emitted_action_ids.add(replacement.step_id)
            continue
        projected.append((_trace_order_ms(trace), index, trace_to_mcp_step(trace)))

    for offset, action in enumerate(recorded_actions, start=len(projected)):
        if action.step_id in emitted_action_ids:
            continue
        step = steps_by_id.get(action.step_id)
        projected.append((_step_order_ms(step), offset, recorded_action_to_mcp_step(action, step=step)))

    return _order_projected_steps(projected)
