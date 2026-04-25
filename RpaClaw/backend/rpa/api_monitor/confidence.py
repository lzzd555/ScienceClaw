from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from .models import CapturedApiCall
from .network_capture import parameterize_url

ConfidenceLevel = Literal["high", "medium", "low"]

NOISE_PATH_MARKERS = (
    "config",
    "queryconfig",
    "telemetry",
    "collect",
    "track",
    "metrics",
    "heartbeat",
    "ping",
    "log",
    "rum",
    "modelalias",
)

BUSINESS_PATH_MARKERS = (
    "/api/",
    "/biz/",
    "/v1/",
    "/v2/",
    "/graphql",
)

INJECTED_SOURCE_MARKERS = (
    "chrome-extension://",
    "moz-extension://",
    "safari-extension://",
    "userscript",
    "injected",
    "eval",
    "webpack://",
    "VM",
)


@dataclass(frozen=True)
class ConfidenceResult:
    confidence: ConfidenceLevel
    selected: bool
    reasons: list[str]
    evidence_summary: dict
    score: int
    breakdown: dict[str, int]


def score_api_candidate(calls: list[CapturedApiCall]) -> ConfidenceResult:
    first = calls[0]
    evidence = _merge_evidence(calls)
    reasons: list[str] = []
    breakdown: dict[str, int] = {}

    path = urlparse(first.request.url).path.lower()
    body = (first.response.body if first.response else "") or ""
    content_type = ((first.response.content_type if first.response else "") or "").lower()
    action_window_matched = bool(evidence.get("action_window_matched"))
    source_urls = [
        *evidence.get("initiator_urls", []),
        *evidence.get("js_stack_urls", []),
    ]

    has_source = bool(source_urls)
    injected_source = any(_contains_marker(url, INJECTED_SOURCE_MARKERS) for url in source_urls)
    noise_path = any(marker in path for marker in NOISE_PATH_MARKERS)
    business_path = any(marker in path for marker in BUSINESS_PATH_MARKERS)
    json_response = "json" in content_type or body.strip().startswith(("{", "["))
    score = 0

    if action_window_matched:
        score += 30
        breakdown["action_window"] = 30
        reasons.append("由用户动作触发")
    else:
        breakdown["action_window"] = 0

    if business_path:
        score += 25
        breakdown["business_path"] = 25
        reasons.append("路径疑似业务接口")
    else:
        breakdown["business_path"] = 0

    if json_response:
        score += 20
        breakdown["json_response"] = 20
        reasons.append("响应疑似 JSON 业务数据")
    else:
        breakdown["json_response"] = 0

    if has_source:
        score += 15
        breakdown["has_source"] = 15
        if injected_source:
            reasons.append("来源疑似注入脚本或扩展")
        else:
            reasons.append("由页面业务脚本发起")
    else:
        breakdown["has_source"] = 0
        reasons.append("缺少 initiator 或 JS 调用栈")

    richness_score, richness_reason = _score_response_richness(body)
    score += richness_score
    breakdown["response_richness"] = richness_score
    if richness_reason:
        reasons.append(richness_reason)

    if injected_source:
        score -= 40
        breakdown["injected_source"] = -40

    if noise_path:
        score -= 30
        breakdown["noise_path"] = -30
        reasons.append("路径疑似配置或后台请求")

    if not action_window_matched:
        score -= 20
        breakdown["no_action_window"] = -20
        reasons.append("不在动作时间窗口内")

    score = max(0, min(100, score))

    if score >= 75:
        confidence: ConfidenceLevel = "high"
        selected = True
    elif score >= 40:
        confidence = "medium"
        selected = False
    else:
        confidence = "low"
        selected = False

    evidence_summary = dict(evidence)
    evidence_summary["score"] = score
    evidence_summary["breakdown"] = breakdown

    return ConfidenceResult(
        confidence=confidence,
        selected=selected,
        reasons=_dedupe(reasons),
        evidence_summary=evidence_summary,
        score=score,
        breakdown=breakdown,
    )


def classify_api_candidate(calls: list[CapturedApiCall]) -> ConfidenceResult:
    return score_api_candidate(calls)


def confidence_rank(level: ConfidenceLevel) -> int:
    return {"high": 2, "medium": 1, "low": 0}.get(level, 0)


def dedup_key_for_tool(method: str, url_pattern: str) -> str:
    parsed = urlparse(url_pattern)
    path = parsed.path or url_pattern.split("?", 1)[0] or "/"
    return f"{method.upper()} {parameterize_url(path)}"


def _score_response_richness(body: str | None) -> tuple[int, str]:
    if not body or not body.strip():
        return 0, "无响应体"

    stripped = body.strip()
    if not stripped.startswith(("{", "[")):
        return 5, "有响应体"

    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return 5, "有响应体但非标准 JSON"

    if isinstance(parsed, dict):
        return (10, "响应体为有效 JSON") if parsed else (5, "响应体为 JSON 但内容为空")
    if isinstance(parsed, list):
        return (10, "响应体为有效 JSON 数组") if parsed else (5, "响应体为 JSON 但内容为空")
    return 5, "响应体为 JSON 但内容为空"


def _merge_evidence(calls: list[CapturedApiCall]) -> dict:
    initiator_urls: list[str] = []
    js_stack_urls: list[str] = []
    action_window_matched = False
    frame_url = ""
    initiator_type = ""

    for call in calls:
        evidence = call.source_evidence or {}
        initiator_urls.extend(str(url) for url in evidence.get("initiator_urls", []) if url)
        js_stack_urls.extend(str(url) for url in evidence.get("js_stack_urls", []) if url)
        action_window_matched = action_window_matched or bool(evidence.get("action_window_matched"))
        frame_url = frame_url or str(evidence.get("frame_url") or "")
        initiator_type = initiator_type or str(evidence.get("initiator_type") or "")

    return {
        "initiator_type": initiator_type,
        "initiator_urls": _dedupe(initiator_urls),
        "js_stack_urls": _dedupe(js_stack_urls),
        "frame_url": frame_url,
        "action_window_matched": action_window_matched,
    }


def _contains_marker(value: str, markers: tuple[str, ...]) -> bool:
    lower = value.lower()
    return any(marker.lower() in lower for marker in markers)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
