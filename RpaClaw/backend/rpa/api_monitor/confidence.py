from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from .models import CapturedApiCall

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


def classify_api_candidate(calls: list[CapturedApiCall]) -> ConfidenceResult:
    first = calls[0]
    evidence = _merge_evidence(calls)
    reasons: list[str] = []

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

    if action_window_matched:
        reasons.append("由用户动作触发")
    else:
        reasons.append("不在动作时间窗口内")

    if injected_source:
        reasons.append("来源疑似注入脚本或扩展")
    elif has_source:
        reasons.append("由页面业务脚本发起")
    else:
        reasons.append("缺少 initiator 或 JS 调用栈")

    if noise_path:
        reasons.append("路径疑似配置或后台请求")
    elif business_path:
        reasons.append("路径疑似业务接口")

    if json_response:
        reasons.append("响应疑似 JSON 业务数据")

    if injected_source or noise_path or not action_window_matched:
        return ConfidenceResult("low", False, _dedupe(reasons), evidence)

    if action_window_matched and has_source and business_path and json_response:
        return ConfidenceResult("high", True, _dedupe(reasons), evidence)

    return ConfidenceResult("medium", False, _dedupe(reasons), evidence)


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
