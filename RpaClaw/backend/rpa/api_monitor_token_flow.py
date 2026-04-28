"""API Monitor dynamic token flow analyzer.

Builds producer -> consumer relationships from captured API traffic using
deterministic value matching. Token values are never persisted in the output.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
from html import unescape
from math import log2
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

import logging
import re

from backend.rpa.api_monitor.models import CapturedApiCall

logger = logging.getLogger(__name__)

TOKEN_NAME_RE = re.compile(r"(csrf|xsrf|nonce|token|signature|authenticity|guard)", re.I)

NOISE_HEADER_NAMES = {
    "accept",
    "accept-language",
    "content-type",
    "origin",
    "referer",
    "user-agent",
    "host",
    "connection",
    "cache-control",
    "pragma",
}
COMMON_VALUES = {"true", "false", "null", "none", "active", "inactive", "success", "error", "ok"}
DATE_LIKE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[tT ].*)?$")
PURE_NUMERIC_RE = re.compile(r"^\d+$")


@dataclass(frozen=True)
class _TokenCandidate:
    value_hash: str
    call_id: str
    method: str
    url_pattern: str
    timestamp_key: float
    source_kind: str
    source_path: str
    field_name: str
    signals: tuple[str, ...] = ()

    @property
    def _sort_key(self) -> tuple:
        return (self.timestamp_key, self.call_id, self.source_path)


@dataclass(frozen=True)
class _TokenConsumer:
    value_hash: str
    call_id: str
    method: str
    url_pattern: str
    timestamp_key: float
    location: str
    path: str
    field_name: str
    signals: tuple[str, ...] = ()


@dataclass
class _TokenFlow:
    id: str
    name: str
    producer: _TokenCandidate
    consumers: list[_TokenConsumer] = field(default_factory=list)
    confidence: str = "medium"
    reasons: list[str] = field(default_factory=list)


def entropy_per_char(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * log2(count / length) for count in counts.values())


def is_dynamic_value_candidate(value: str, *, field_name: str = "") -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered in COMMON_VALUES or DATE_LIKE_RE.match(text):
        return False
    if PURE_NUMERIC_RE.match(text) and len(text) < 16:
        return False
    strong_name = bool(TOKEN_NAME_RE.search(field_name or ""))
    if strong_name and len(text) >= 6:
        return True
    if len(text) < 16:
        return False
    return entropy_per_char(text) >= 3.0 and entropy_per_char(text) * len(text) >= 40


def build_api_monitor_token_flow_profile(calls: list[CapturedApiCall]) -> dict[str, Any]:
    producers, value_to_producers = _collect_producers(calls)
    consumers = _collect_consumers(calls)
    flows = _match_flows(producers, value_to_producers, consumers)
    flow_docs = [_flow_profile_doc(flow) for flow in flows]
    logger.info(
        "[TokenFlow] calls=%d producers=%d consumers=%d flows=%d",
        len(calls), len(producers), len(consumers), len(flow_docs),
    )
    for call in calls[:3]:
        resp = call.response
        body_preview = (resp.body or "")[:100] if resp else "None"
        logger.debug(
            "[TokenFlow] call=%s method=%s url=%s resp_headers=%s body=%s",
            call.id, call.request.method, call.request.url[:80],
            list(resp.headers.keys()) if resp else [],
            body_preview,
        )
    return {"flow_count": len(flow_docs), "flows": flow_docs}


def resolve_token_flows_for_publish(
    calls: list[CapturedApiCall],
    selections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert selected flow IDs from the session profile into persisted runtime configs.

    Only returns flows that exist in the session profile and are enabled.
    No token values are included in the output.
    """
    if not selections:
        return []
    producers, value_to_producers = _collect_producers(calls)
    consumers = _collect_consumers(calls)
    flows = _match_flows(producers, value_to_producers, consumers)

    selected_ids = {s.get("id", "") for s in selections if s.get("enabled", True)}
    results: list[dict[str, Any]] = []
    for flow in flows:
        if flow.id not in selected_ids:
            continue
        results.append(_flow_runtime_doc(flow))
    return results


# ── Producer collection ─────────────────────────────────────────────────


def _collect_producers(
    calls: list[CapturedApiCall],
) -> tuple[list[_TokenCandidate], dict[str, list[_TokenCandidate]]]:
    candidates: list[_TokenCandidate] = []
    value_map: dict[str, list[_TokenCandidate]] = {}

    for call in calls:
        ts = _call_timestamp(call)
        resp = call.response

        # Response headers
        if resp:
            for name, value in resp.headers.items():
                lowered = name.lower()
                if lowered in NOISE_HEADER_NAMES:
                    continue
                if not is_dynamic_value_candidate(value, field_name=lowered):
                    continue
                signals = _producer_signals(lowered, value)
                if not signals:
                    continue
                c = _TokenCandidate(
                    value_hash=_hash_value(value),
                    call_id=call.id,
                    method=call.request.method,
                    url_pattern=call.url_pattern or "",
                    timestamp_key=ts,
                    source_kind="response.headers",
                    source_path=lowered,
                    field_name=lowered,
                    signals=tuple(signals),
                )
                candidates.append(c)
                value_map.setdefault(c.value_hash, []).append(c)

            # Set-Cookie
            set_cookie = resp.headers.get("set-cookie", "")
            if set_cookie:
                for cookie_name, cookie_value in _parse_set_cookie(set_cookie):
                    if not is_dynamic_value_candidate(cookie_value, field_name=cookie_name):
                        continue
                    signals = _producer_signals(cookie_name, cookie_value)
                    if not signals:
                        continue
                    c = _TokenCandidate(
                        value_hash=_hash_value(cookie_value),
                        call_id=call.id,
                        method=call.request.method,
                        url_pattern=call.url_pattern or "",
                        timestamp_key=ts,
                        source_kind="set-cookie",
                        source_path=cookie_name,
                        field_name=cookie_name,
                        signals=tuple(signals),
                    )
                    candidates.append(c)
                    value_map.setdefault(c.value_hash, []).append(c)

            # Response body (JSON)
            if resp.body:
                content_type = (resp.content_type or "").lower()
                if "json" in content_type or resp.body.strip().startswith("{"):
                    _scan_json_body(
                        resp.body, call, ts, "response.body.", candidates, value_map
                    )

    return candidates, value_map


def _scan_json_body(
    body_text: str,
    call: CapturedApiCall,
    ts: float,
    prefix: str,
    candidates: list[_TokenCandidate],
    value_map: dict[str, list[_TokenCandidate]],
) -> None:
    try:
        data = __import__("json").loads(body_text)
    except (ValueError, TypeError):
        return
    if not isinstance(data, dict):
        return
    _scan_json_dict(data, call, ts, prefix, candidates, value_map)


def _scan_json_dict(
    data: dict[str, Any],
    call: CapturedApiCall,
    ts: float,
    prefix: str,
    candidates: list[_TokenCandidate],
    value_map: dict[str, list[_TokenCandidate]],
) -> None:
    for key, value in data.items():
        path = f"$.{key}"
        if isinstance(value, str):
            if is_dynamic_value_candidate(value, field_name=key):
                signals = _producer_signals(key, value)
                if signals:
                    c = _TokenCandidate(
                        value_hash=_hash_value(value),
                        call_id=call.id,
                        method=call.request.method,
                        url_pattern=call.url_pattern or "",
                        timestamp_key=ts,
                        source_kind="response.body",
                        source_path=path,
                        field_name=key,
                        signals=tuple(signals),
                    )
                    candidates.append(c)
                    value_map.setdefault(c.value_hash, []).append(c)
        elif isinstance(value, dict):
            _scan_json_dict(value, call, ts, path + ".", candidates, value_map)


# ── Consumer collection ─────────────────────────────────────────────────


def _collect_consumers(calls: list[CapturedApiCall]) -> list[_TokenConsumer]:
    consumers: list[_TokenConsumer] = []
    for call in calls:
        ts = _call_timestamp(call)
        req = call.request

        # Request headers
        for name, value in req.headers.items():
            lowered = name.lower()
            if lowered in NOISE_HEADER_NAMES:
                continue
            if not is_dynamic_value_candidate(value, field_name=lowered):
                continue
            consumers.append(_TokenConsumer(
                value_hash=_hash_value(value),
                call_id=call.id,
                method=req.method,
                url_pattern=call.url_pattern or "",
                timestamp_key=ts,
                location="request.headers",
                path=name,
                field_name=lowered,
                signals=("high-entropy",),
            ))

        # Query parameters
        parsed = urlsplit(req.url)
        for qname, qvalue in parse_qsl(parsed.query, keep_blank_values=True):
            if is_dynamic_value_candidate(qvalue, field_name=qname):
                consumers.append(_TokenConsumer(
                    value_hash=_hash_value(qvalue),
                    call_id=call.id,
                    method=req.method,
                    url_pattern=call.url_pattern or "",
                    timestamp_key=ts,
                    location="request.query",
                    path=qname,
                    field_name=qname,
                    signals=("high-entropy",),
                ))

        # Request body
        if req.body:
            content_type = (req.content_type or "").lower()
            is_form_explicit = "form" in content_type
            is_json_content = "json" in content_type or req.body.strip().startswith("{")
            if is_json_content:
                try:
                    __import__("json").loads(req.body)
                except (ValueError, TypeError):
                    is_json_content = False
            if is_json_content:
                _scan_json_body_consumers(req.body, call, ts, consumers)
            elif is_form_explicit or "=" in req.body:
                _scan_form_body_consumers(req.body, call, ts, consumers)

    return consumers


def _scan_json_body_consumers(
    body_text: str,
    call: CapturedApiCall,
    ts: float,
    consumers: list[_TokenConsumer],
) -> None:
    try:
        data = __import__("json").loads(body_text)
    except (ValueError, TypeError):
        return
    if not isinstance(data, dict):
        return
    _scan_json_dict_consumers(data, call, ts, "request.body.$.", consumers)


def _scan_json_dict_consumers(
    data: dict[str, Any],
    call: CapturedApiCall,
    ts: float,
    prefix: str,
    consumers: list[_TokenConsumer],
) -> None:
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, str):
            if is_dynamic_value_candidate(value, field_name=key):
                consumers.append(_TokenConsumer(
                    value_hash=_hash_value(value),
                    call_id=call.id,
                    method=call.request.method,
                    url_pattern=call.url_pattern or "",
                    timestamp_key=ts,
                    location="request.body",
                    path=path,
                    field_name=key,
                    signals=("high-entropy",),
                ))
        elif isinstance(value, dict):
            _scan_json_dict_consumers(value, call, ts, f"{path}.", consumers)


def _scan_form_body_consumers(
    body_text: str,
    call: CapturedApiCall,
    ts: float,
    consumers: list[_TokenConsumer],
) -> None:
    for fname, fvalue in parse_qsl(body_text, keep_blank_values=True):
        if is_dynamic_value_candidate(fvalue, field_name=fname):
            consumers.append(_TokenConsumer(
                value_hash=_hash_value(fvalue),
                call_id=call.id,
                method=call.request.method,
                url_pattern=call.url_pattern or "",
                timestamp_key=ts,
                location="request.body",
                path=f"request.body.$.{fname}",
                field_name=fname,
                signals=("high-entropy",),
            ))


# ── Flow matching ───────────────────────────────────────────────────────


def _match_flows(
    producers: list[_TokenCandidate],
    value_to_producers: dict[str, list[_TokenCandidate]],
    consumers: list[_TokenConsumer],
) -> list[_TokenFlow]:
    flows: list[_TokenFlow] = []
    matched_consumer_ids: set[tuple[str, str]] = set()

    # Producer-first: for each consumer, find matching producer
    for consumer in consumers:
        matching = value_to_producers.get(consumer.value_hash, [])
        # Find best producer (earliest before consumer)
        best_producer = None
        for prod in matching:
            if prod.timestamp_key < consumer.timestamp_key:
                if best_producer is None or prod.timestamp_key < best_producer.timestamp_key:
                    best_producer = prod

        if best_producer:
            cons_origin = _origin_from_url_pattern(consumer.url_pattern)
            prod_origin = _origin_from_url_pattern(best_producer.url_pattern)
            same_origin = cons_origin == prod_origin if cons_origin and prod_origin else True

            flow_id = f"flow_{_hash_value(best_producer.value_hash + consumer.value_hash)[:12]}"
            existing = next((f for f in flows if f.id == flow_id), None)
            if existing:
                existing.consumers.append(consumer)
            else:
                name = _derive_flow_name(best_producer.field_name, consumer.field_name)
                reasons = _compute_reasons(best_producer, consumer, same_origin)
                confidence = _compute_confidence(reasons)
                flow = _TokenFlow(
                    id=flow_id,
                    name=name,
                    producer=best_producer,
                    consumers=[consumer],
                    confidence=confidence,
                    reasons=reasons,
                )
                flows.append(flow)
            matched_consumer_ids.add((consumer.call_id, consumer.path))

    return flows


def _compute_reasons(producer: _TokenCandidate, consumer: _TokenConsumer, same_origin: bool) -> list[str]:
    reasons: list[str] = []
    if producer.value_hash == consumer.value_hash:
        reasons.append("exact-value-match")
    if "csrf-name" in producer.signals or "xsrf-name" in producer.signals:
        reasons.append("csrf-name")
    if "token-name" in producer.signals:
        reasons.append("token-name")
    if "high-entropy" in producer.signals:
        reasons.append("high-entropy")
    if producer.timestamp_key < consumer.timestamp_key:
        reasons.append("producer-before-consumer")
    if same_origin:
        reasons.append("same-origin")
    return reasons


def _compute_confidence(reasons: list[str]) -> str:
    has_exact = "exact-value-match" in reasons
    has_temporal = "producer-before-consumer" in reasons
    has_name = any(r in reasons for r in ("csrf-name", "token-name"))
    has_origin = "same-origin" in reasons

    if has_exact and has_temporal and has_name and has_origin:
        return "high"
    if has_exact and has_temporal:
        return "medium"
    return "low"


def _derive_flow_name(producer_name: str, consumer_name: str) -> str:
    for name in (producer_name, consumer_name):
        lowered = name.lower()
        if "csrf" in lowered or "xsrf" in lowered:
            return "csrf_token"
        if "nonce" in lowered:
            return "nonce"
        if "token" in lowered:
            return "token"
    return producer_name or consumer_name or "dynamic_value"


# ── Profile serialization ───────────────────────────────────────────────


def _flow_profile_doc(flow: _TokenFlow) -> dict[str, Any]:
    producer_summary = (
        f"{flow.producer.method} {flow.producer.url_pattern} "
        f"{flow.producer.source_kind}.{_display_path(flow.producer.source_path)}"
    )
    consumer_summaries = [
        f"{c.method} {c.url_pattern} {c.location}.{_display_path(c.path)}"
        for c in flow.consumers
    ]
    return {
        "id": flow.id,
        "name": flow.name,
        "producer_summary": producer_summary,
        "consumer_summaries": consumer_summaries,
        "confidence": flow.confidence,
        "enabled_by_default": flow.confidence in ("high", "medium"),
        "reasons": flow.reasons,
    }


def _flow_runtime_doc(flow: _TokenFlow) -> dict[str, Any]:
    """Convert a matched flow into a persisted runtime config for injection."""
    producer = flow.producer
    extract_from = producer.source_kind
    extract_path = _runtime_extract_path(producer.source_kind, producer.source_path)

    inject: dict[str, dict[str, str]] = {}
    applies_to: list[dict[str, str]] = []
    consumer_summaries: list[str] = []

    for consumer in flow.consumers:
        applies_to.append({"method": consumer.method, "url": consumer.url_pattern})
        consumer_summaries.append(
            f"{consumer.method} {consumer.url_pattern} {consumer.location}.{consumer.path}"
        )
        if consumer.location == "request.headers":
            inject.setdefault("headers", {})[consumer.path] = "{{ " + flow.name + " }}"

    setup = [{
        "method": producer.method,
        "url": producer.url_pattern,
        "extract": {"from": extract_from, "path": extract_path},
    }]

    producer_summary = (
        f"{producer.method} {producer.url_pattern} "
        f"{producer.source_kind}.{_display_path(producer.source_path)}"
    )

    return {
        "id": flow.id,
        "name": flow.name,
        "setup": setup,
        "inject": inject,
        "applies_to": applies_to,
        "refresh_on_status": [401, 403, 419],
        "confidence": flow.confidence,
        "summary": {
            "producer": producer_summary,
            "consumers": consumer_summaries,
            "reasons": flow.reasons,
        },
    }


def _runtime_extract_path(source_kind: str, source_path: str) -> str:
    """Convert source_path to the JSON-path-like extract path for runtime."""
    if source_kind == "response.body":
        return source_path
    if source_kind == "response.headers":
        return source_path
    if source_kind == "set-cookie":
        return source_path
    return source_path


# ── Helpers ─────────────────────────────────────────────────────────────


def _hash_value(value: str) -> str:
    return sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _norm_hashes(value_hash: str) -> set[str]:
    return {value_hash}


def _call_timestamp(call: CapturedApiCall) -> float:
    return call.request.timestamp.timestamp()


def _origin_from_url_pattern(url_pattern: str) -> str:
    if not url_pattern:
        return ""
    if url_pattern.startswith(("http://", "https://")):
        parsed = urlsplit(url_pattern)
        return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def _display_path(path: str) -> str:
    if path.startswith("$."):
        return path
    return path


def _producer_signals(field_name: str, value: str) -> list[str]:
    signals: list[str] = []
    lowered = field_name.lower()
    if TOKEN_NAME_RE.search(lowered):
        if "csrf" in lowered or "xsrf" in lowered:
            signals.append("csrf-name")
        elif "token" in lowered:
            signals.append("token-name")
        else:
            signals.append("token-name")
    if is_dynamic_value_candidate(value, field_name=field_name):
        signals.append("high-entropy")
    return signals


def _parse_set_cookie(header_value: str) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for part in header_value.split(","):
        part = part.strip()
        if "=" not in part:
            continue
        name_value = part.split(";")[0].strip()
        eq_idx = name_value.index("=")
        name = name_value[:eq_idx].strip()
        value = name_value[eq_idx + 1:].strip()
        if name and value:
            results.append((name, value))
    return results
