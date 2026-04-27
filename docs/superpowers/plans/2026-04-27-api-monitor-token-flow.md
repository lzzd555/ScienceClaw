# API Monitor Token Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic dynamic token flow detection and runtime injection for API Monitor MCP, so CSRF/XSRF/nonce-style values can be extracted from captured traffic and reused safely during tool calls.

**Architecture:** Implement token flow detection as a pure backend analyzer that builds producer/consumer relationships from captured calls without persisting token values. Publish stores only selected flow rules under `api_monitor_auth.token_flows`; runtime uses those rules to perform setup/extract/inject with a shared `httpx.AsyncClient` cookie jar. Frontend only displays masked summaries and lets users confirm enabled flows.

**Tech Stack:** FastAPI, Pydantic v2, async repository abstraction, httpx, pytest, Vue 3 Composition API, TypeScript, Vite/Vitest, Tailwind CSS.

---

## Scope

This plan implements the first production path for the design in `docs/superpowers/specs/2026-04-27-api-monitor-token-flow-design.md`.

Included:

- Backend token flow analyzer for captured JSON/header/cookie/body traffic.
- High-entropy candidate discovery and consumer-first backtracking.
- Masked token flow profile API.
- Publish-time flow selection and persistence.
- Runtime setup/extract/inject, cookie reuse, and one refresh retry.
- Frontend API types and publish dialog display.
- Focused tests.

Deferred:

- HTML document response capture beyond existing captured responses.
- DOM meta/hidden input scan hooks.
- localStorage/sessionStorage scan hooks.
- Cross-process Redis cache.
- HMAC/hash/signature reverse engineering.

The deferred items remain compatible with this data model and can be implemented as additional producer sources later.

---

## File Structure

Backend files:

- Create `RpaClaw/backend/rpa/api_monitor_token_flow.py`: pure analyzer, entropy helpers, profile serialization, runtime flow normalization, extract/inject helpers.
- Modify `RpaClaw/backend/rpa/api_monitor/models.py`: extend publish auth request models with selected token flow IDs.
- Modify `RpaClaw/backend/route/api_monitor.py`: add `GET /session/{session_id}/token-flow-profile`; pass selected flow IDs through publish.
- Modify `RpaClaw/backend/rpa/api_monitor_mcp_registry.py`: convert selected flow IDs into persisted runtime `token_flows`.
- Modify `RpaClaw/backend/rpa/api_monitor_auth.py`: preserve credential auth behavior and expose token-flow auth preview shape if useful.
- Modify `RpaClaw/backend/deepagent/mcp_runtime.py`: run token setup/extract/inject before the target API request and retry once on configured auth failure statuses.
- Test `RpaClaw/backend/tests/test_api_monitor_token_flow.py`: analyzer, entropy, producer-first, consumer-first, masking.
- Test `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`: profile endpoint, publish selection, no token value persistence.
- Test `RpaClaw/backend/tests/deepagent/test_mcp_runtime.py`: runtime setup, injection, cookie reuse, retry, preview masking.

Frontend files:

- Modify `RpaClaw/frontend/src/api/apiMonitor.ts`: token flow profile types and API call; publish payload flow selections.
- Modify `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`: load token flow profile in publish dialog; display/enable selected flows; include selected IDs on publish.
- Modify `RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue`: display saved dynamic token flow summaries.
- Modify `RpaClaw/frontend/src/api/mcp.ts`: include token flow fields on API Monitor MCP detail types.
- Modify `RpaClaw/frontend/src/locales/en.ts` and `RpaClaw/frontend/src/locales/zh.ts`: add UI strings.

---

## Shared Data Shapes

Use these shapes consistently across backend and frontend.

Profile response:

```json
{
  "status": "success",
  "profile": {
    "flow_count": 1,
    "flows": [
      {
        "id": "flow_abcdef123456",
        "name": "csrf_token",
        "producer_summary": "GET /api/session response.body.$.csrfToken",
        "consumer_summaries": ["POST /api/orders request.headers.X-CSRF-Token"],
        "confidence": "high",
        "enabled_by_default": true,
        "reasons": ["exact-value-match", "csrf-name", "producer-before-consumer"]
      }
    ]
  }
}
```

Publish payload extension:

```json
{
  "api_monitor_auth": {
    "credential_type": "placeholder",
    "credential_id": "cred_123",
    "login_url": "",
    "token_flows": [
      { "id": "flow_abcdef123456", "enabled": true }
    ]
  }
}
```

Persisted server config:

```json
{
  "api_monitor_auth": {
    "credential_type": "placeholder",
    "credential_id": "cred_123",
    "token_flows": [
      {
        "id": "flow_abcdef123456",
        "name": "csrf_token",
        "setup": [
          {
            "method": "GET",
            "url": "/api/session",
            "extract": { "from": "response.body", "path": "$.csrfToken" }
          }
        ],
        "inject": {
          "headers": { "X-CSRF-Token": "{{ csrf_token }}" }
        },
        "applies_to": [
          { "method": "POST", "url": "/api/orders" }
        ],
        "refresh_on_status": [401, 403, 419],
        "confidence": "high",
        "summary": {
          "producer": "GET /api/session response.body.$.csrfToken",
          "consumers": ["POST /api/orders request.headers.X-CSRF-Token"],
          "reasons": ["exact-value-match", "csrf-name", "producer-before-consumer"]
        }
      }
    ]
  }
}
```

---

### Task 1: Backend Token Flow Analyzer

**Files:**

- Create: `RpaClaw/backend/rpa/api_monitor_token_flow.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_token_flow.py`

- [ ] **Step 1: Write failing analyzer tests**

Create `RpaClaw/backend/tests/test_api_monitor_token_flow.py`:

```python
from datetime import datetime, timedelta

from backend.rpa.api_monitor.models import CapturedApiCall, CapturedRequest, CapturedResponse
from backend.rpa.api_monitor_token_flow import (
    build_api_monitor_token_flow_profile,
    entropy_per_char,
    is_dynamic_value_candidate,
)


def _call(
    call_id: str,
    *,
    method: str,
    url: str,
    request_headers: dict[str, str] | None = None,
    request_body: str | None = None,
    response_headers: dict[str, str] | None = None,
    response_body: str | None = None,
    seconds: int = 0,
) -> CapturedApiCall:
    ts = datetime(2026, 4, 27, 10, 0, 0) + timedelta(seconds=seconds)
    call = CapturedApiCall(
        request=CapturedRequest(
            request_id=f"req-{call_id}",
            url=url,
            method=method,
            headers=request_headers or {},
            body=request_body,
            content_type="application/json" if request_body else None,
            timestamp=ts,
            resource_type="fetch",
        ),
        response=CapturedResponse(
            status=200,
            status_text="OK",
            headers=response_headers or {"content-type": "application/json"},
            body=response_body,
            content_type="application/json" if response_body else None,
            timestamp=ts + timedelta(milliseconds=100),
        ) if response_body is not None or response_headers else None,
        url_pattern=url.replace("https://example.test", ""),
    )
    call.id = call_id
    return call


def test_entropy_per_char_scores_repeated_values_low_and_tokens_higher():
    assert entropy_per_char("aaaaaaaaaaaaaaaa") == 0
    assert entropy_per_char("8fa7c91e2d8a4c90b0f7") > 3.0


def test_dynamic_value_candidate_uses_length_entropy_and_shape_filters():
    assert is_dynamic_value_candidate("8fa7c91e2d8a4c90b0f7") is True
    assert is_dynamic_value_candidate("12345678") is False
    assert is_dynamic_value_candidate("active") is False
    assert is_dynamic_value_candidate("2026-04-27") is False


def test_profile_links_json_csrf_token_to_later_header_consumer():
    calls = [
        _call(
            "bootstrap",
            method="GET",
            url="https://example.test/api/session",
            response_body='{"csrfToken":"8fa7c91e2d8a4c90b0f7"}',
            seconds=0,
        ),
        _call(
            "create_order",
            method="POST",
            url="https://example.test/api/orders",
            request_headers={"X-CSRF-Token": "8fa7c91e2d8a4c90b0f7"},
            request_body='{"name":"order"}',
            seconds=1,
        ),
    ]

    profile = build_api_monitor_token_flow_profile(calls)

    assert profile["flow_count"] == 1
    flow = profile["flows"][0]
    assert flow["name"] == "csrf_token"
    assert flow["confidence"] == "high"
    assert flow["enabled_by_default"] is True
    assert flow["producer_summary"] == "GET /api/session response.body.$.csrfToken"
    assert flow["consumer_summaries"] == ["POST /api/orders request.headers.X-CSRF-Token"]
    assert "8fa7c91e2d8a4c90b0f7" not in str(profile)


def test_profile_supports_consumer_first_custom_header_backtracking():
    calls = [
        _call(
            "bootstrap",
            method="GET",
            url="https://example.test/api/bootstrap",
            response_body='{"r":"8fa7c91e2d8a4c90b0f7"}',
            seconds=0,
        ),
        _call(
            "guarded",
            method="POST",
            url="https://example.test/api/guarded",
            request_headers={"X-Company-Guard": "8fa7c91e2d8a4c90b0f7"},
            seconds=1,
        ),
    ]

    profile = build_api_monitor_token_flow_profile(calls)

    assert profile["flow_count"] == 1
    flow = profile["flows"][0]
    assert flow["producer_summary"] == "GET /api/bootstrap response.body.$.r"
    assert flow["consumer_summaries"] == ["POST /api/guarded request.headers.X-Company-Guard"]
    assert "high-entropy" in flow["reasons"]


def test_profile_groups_multiple_consumers_for_one_token():
    calls = [
        _call(
            "bootstrap",
            method="GET",
            url="https://example.test/api/session",
            response_body='{"csrfToken":"8fa7c91e2d8a4c90b0f7"}',
            seconds=0,
        ),
        _call(
            "orders",
            method="POST",
            url="https://example.test/api/orders",
            request_headers={"X-CSRF-Token": "8fa7c91e2d8a4c90b0f7"},
            seconds=1,
        ),
        _call(
            "profile",
            method="PUT",
            url="https://example.test/api/profile",
            request_headers={"X-CSRF-Token": "8fa7c91e2d8a4c90b0f7"},
            seconds=2,
        ),
    ]

    profile = build_api_monitor_token_flow_profile(calls)

    assert profile["flow_count"] == 1
    assert profile["flows"][0]["consumer_summaries"] == [
        "POST /api/orders request.headers.X-CSRF-Token",
        "PUT /api/profile request.headers.X-CSRF-Token",
    ]


def test_profile_ignores_business_id_path_usage():
    calls = [
        _call(
            "list",
            method="GET",
            url="https://example.test/api/orders",
            response_body='{"id":"8fa7c91e2d8a4c90b0f7"}',
            seconds=0,
        ),
        _call(
            "detail",
            method="GET",
            url="https://example.test/api/orders/8fa7c91e2d8a4c90b0f7",
            seconds=1,
        ),
    ]

    profile = build_api_monitor_token_flow_profile(calls)

    assert profile["flow_count"] == 0
```

- [ ] **Step 2: Run the new tests and confirm they fail**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_token_flow.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.rpa.api_monitor_token_flow'`.

- [ ] **Step 3: Implement the analyzer module**

Create `RpaClaw/backend/rpa/api_monitor_token_flow.py` with these public functions and dataclasses:

```python
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from hashlib import sha256
from html import unescape
from math import log2
import json
import re
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from backend.rpa.api_monitor.models import CapturedApiCall


TOKEN_NAME_RE = re.compile(r"(csrf|xsrf|nonce|token|signature|authenticity|guard|session)", re.I)
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
class TokenCandidate:
    value: str
    value_hash: str
    call_id: str
    method: str
    url_pattern: str
    timestamp_key: float
    source_kind: str
    source_path: str
    field_name: str
    signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class TokenConsumer:
    value: str
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
class TokenFlow:
    id: str
    name: str
    producer: TokenCandidate
    consumers: list[TokenConsumer] = field(default_factory=list)
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
    producers = _collect_producers(calls)
    consumers = _collect_consumers(calls)
    flows = _match_flows(producers, consumers)
    flow_docs = [_flow_profile_doc(flow) for flow in flows]
    return {"flow_count": len(flow_docs), "flows": flow_docs}
```

Continue the file with private helpers:

```python
def _collect_producers(calls: list[CapturedApiCall]) -> list[TokenCandidate]:
    producers: list[TokenCandidate] = []
    for call in calls:
        if call.response:
            producers.extend(_response_header_producers(call))
            producers.extend(_json_body_producers(call))
            producers.extend(_set_cookie_producers(call))
    return producers


def _collect_consumers(calls: list[CapturedApiCall]) -> list[TokenConsumer]:
    consumers: list[TokenConsumer] = []
    for call in calls:
        consumers.extend(_request_header_consumers(call))
        consumers.extend(_query_consumers(call))
        consumers.extend(_json_body_consumers(call))
    return consumers


def _response_header_producers(call: CapturedApiCall) -> list[TokenCandidate]:
    result: list[TokenCandidate] = []
    for name, value in (call.response.headers if call.response else {}).items():
        normalized = name.lower()
        if normalized in NOISE_HEADER_NAMES or normalized.startswith("sec-"):
            continue
        result.extend(_candidate_from_value(call, value, "response.headers", name, name))
    return result


def _set_cookie_producers(call: CapturedApiCall) -> list[TokenCandidate]:
    header = (call.response.headers if call.response else {}).get("set-cookie") or ""
    result: list[TokenCandidate] = []
    for cookie_pair in header.split(","):
        first = cookie_pair.split(";", 1)[0]
        if "=" not in first:
            continue
        name, value = first.split("=", 1)
        result.extend(_candidate_from_value(call, value, "set-cookie", name.strip(), name.strip()))
    return result


def _json_body_producers(call: CapturedApiCall) -> list[TokenCandidate]:
    body = call.response.body if call.response else None
    if not body:
        return []
    try:
        parsed = json.loads(body)
    except (TypeError, ValueError):
        return []
    result: list[TokenCandidate] = []
    for path, key, value in _walk_json(parsed):
        result.extend(_candidate_from_value(call, value, "response.body", path, key))
    return result


def _request_header_consumers(call: CapturedApiCall) -> list[TokenConsumer]:
    result: list[TokenConsumer] = []
    for name, value in call.request.headers.items():
        normalized = name.lower()
        if normalized in NOISE_HEADER_NAMES or normalized.startswith("sec-"):
            continue
        result.extend(_consumer_from_value(call, value, "request.headers", name, name))
    return result


def _query_consumers(call: CapturedApiCall) -> list[TokenConsumer]:
    parsed = urlsplit(call.request.url)
    result: list[TokenConsumer] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        result.extend(_consumer_from_value(call, value, "request.query", key, key))
    return result


def _json_body_consumers(call: CapturedApiCall) -> list[TokenConsumer]:
    if not call.request.body or "json" not in (call.request.content_type or ""):
        return []
    try:
        parsed = json.loads(call.request.body)
    except (TypeError, ValueError):
        return []
    result: list[TokenConsumer] = []
    for path, key, value in _walk_json(parsed):
        result.extend(_consumer_from_value(call, value, "request.body", path, key))
    return result
```

Add the shared helpers:

```python
def _candidate_from_value(
    call: CapturedApiCall,
    value: Any,
    source_kind: str,
    source_path: str,
    field_name: str,
) -> list[TokenCandidate]:
    if not isinstance(value, str) or not is_dynamic_value_candidate(value, field_name=field_name):
        return []
    normalized = _normalized_values(value)
    return [
        TokenCandidate(
            value=item,
            value_hash=_hash_value(item),
            call_id=call.id,
            method=call.request.method.upper(),
            url_pattern=call.url_pattern or _path_from_url(call.request.url),
            timestamp_key=call.response.timestamp.timestamp() if call.response else call.request.timestamp.timestamp(),
            source_kind=source_kind,
            source_path=source_path,
            field_name=field_name,
            signals=tuple(_signals(field_name, value)),
        )
        for item in normalized
    ]


def _consumer_from_value(
    call: CapturedApiCall,
    value: Any,
    location: str,
    path: str,
    field_name: str,
) -> list[TokenConsumer]:
    if not isinstance(value, str) or not is_dynamic_value_candidate(value, field_name=field_name):
        return []
    return [
        TokenConsumer(
            value=item,
            value_hash=_hash_value(item),
            call_id=call.id,
            method=call.request.method.upper(),
            url_pattern=call.url_pattern or _path_from_url(call.request.url),
            timestamp_key=call.request.timestamp.timestamp(),
            location=location,
            path=path,
            field_name=field_name,
            signals=tuple(_signals(field_name, value)),
        )
        for item in _normalized_values(value)
    ]


def _match_flows(producers: list[TokenCandidate], consumers: list[TokenConsumer]) -> list[TokenFlow]:
    producers_by_hash: dict[str, list[TokenCandidate]] = defaultdict(list)
    for producer in producers:
        producers_by_hash[producer.value_hash].append(producer)

    grouped: dict[tuple[str, str, str], TokenFlow] = {}
    for consumer in consumers:
        for producer in producers_by_hash.get(consumer.value_hash, []):
            if producer.timestamp_key >= consumer.timestamp_key:
                continue
            if _looks_like_business_path_usage(consumer):
                continue
            key = (producer.call_id, producer.source_kind, producer.source_path)
            if key not in grouped:
                grouped[key] = TokenFlow(
                    id=_flow_id(producer),
                    name=_token_name(producer, consumer),
                    producer=producer,
                )
            if not any(existing.call_id == consumer.call_id and existing.location == consumer.location and existing.path == consumer.path for existing in grouped[key].consumers):
                grouped[key].consumers.append(consumer)

    flows = list(grouped.values())
    for flow in flows:
        flow.reasons = _flow_reasons(flow)
        flow.confidence = "high" if "exact-value-match" in flow.reasons and any(reason.endswith("-name") for reason in flow.reasons) else "medium"
    return sorted(flows, key=lambda flow: flow.id)
```

Add serialization helpers:

```python
def _flow_profile_doc(flow: TokenFlow) -> dict[str, Any]:
    return {
        "id": flow.id,
        "name": flow.name,
        "producer_summary": _producer_summary(flow.producer),
        "consumer_summaries": [_consumer_summary(consumer) for consumer in flow.consumers],
        "confidence": flow.confidence,
        "enabled_by_default": flow.confidence == "high",
        "reasons": flow.reasons,
        "runtime_flow": _runtime_flow_doc(flow),
    }


def _runtime_flow_doc(flow: TokenFlow) -> dict[str, Any]:
    first_consumer = flow.consumers[0]
    return {
        "id": flow.id,
        "name": flow.name,
        "setup": [
            {
                "method": flow.producer.method,
                "url": flow.producer.url_pattern,
                "extract": _extract_rule(flow.producer),
            }
        ],
        "inject": _inject_rule(flow.name, flow.consumers),
        "applies_to": [
            {"method": consumer.method, "url": consumer.url_pattern}
            for consumer in flow.consumers
        ],
        "refresh_on_status": [401, 403, 419],
        "confidence": flow.confidence,
        "summary": {
            "producer": _producer_summary(flow.producer),
            "consumers": [_consumer_summary(consumer) for consumer in flow.consumers],
            "reasons": flow.reasons,
        },
    }


def _extract_rule(producer: TokenCandidate) -> dict[str, str]:
    if producer.source_kind == "response.body":
        return {"from": "response.body", "path": producer.source_path}
    if producer.source_kind == "set-cookie":
        return {"from": "cookie", "name": producer.source_path}
    return {"from": producer.source_kind, "path": producer.source_path}


def _inject_rule(token_name: str, consumers: list[TokenConsumer]) -> dict[str, dict[str, str]]:
    inject: dict[str, dict[str, str]] = {}
    template = "{{ " + token_name + " }}"
    for consumer in consumers:
        if consumer.location == "request.headers":
            inject.setdefault("headers", {})[consumer.path] = template
        elif consumer.location == "request.query":
            inject.setdefault("query", {})[consumer.path] = template
        elif consumer.location == "request.body":
            inject.setdefault("body", {})[consumer.path] = template
    return inject
```

Add utility helpers:

```python
def _walk_json(value: Any, prefix: str = "$") -> list[tuple[str, str, Any]]:
    result: list[tuple[str, str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            result.extend(_walk_json(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.extend(_walk_json(item, f"{prefix}[{index}]"))
    else:
        key = prefix.rsplit(".", 1)[-1].split("[", 1)[0]
        result.append((prefix, key, value))
    return result


def _normalized_values(value: str) -> list[str]:
    values = [value.strip(), unquote(value.strip()), unescape(value.strip())]
    result: list[str] = []
    for item in values:
        if item and item not in result:
            result.append(item)
    return result


def _hash_value(value: str) -> str:
    return "sha256:" + sha256(value.encode("utf-8")).hexdigest()


def _signals(field_name: str, value: str) -> list[str]:
    signals: list[str] = []
    lowered = field_name.lower()
    for fragment in ("csrf", "xsrf", "nonce", "token", "signature", "guard", "session"):
        if fragment in lowered:
            signals.append(f"{fragment}-name")
    if is_dynamic_value_candidate(value):
        signals.append("high-entropy")
    return signals


def _flow_reasons(flow: TokenFlow) -> list[str]:
    reasons = ["exact-value-match", "producer-before-consumer"]
    for signal in flow.producer.signals:
        if signal not in reasons:
            reasons.append(signal)
    for consumer in flow.consumers:
        for signal in consumer.signals:
            if signal not in reasons:
                reasons.append(signal)
    return reasons


def _token_name(producer: TokenCandidate, consumer: TokenConsumer) -> str:
    source = producer.field_name or consumer.field_name or "token"
    if "csrf" in source.lower() or "xsrf" in source.lower():
        return "csrf_token"
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", source).strip("_").lower()
    return cleaned if cleaned.endswith("token") else f"{cleaned or 'dynamic'}_token"


def _flow_id(producer: TokenCandidate) -> str:
    raw = f"{producer.method}|{producer.url_pattern}|{producer.source_kind}|{producer.source_path}"
    return "flow_" + sha256(raw.encode("utf-8")).hexdigest()[:12]


def _producer_summary(producer: TokenCandidate) -> str:
    return f"{producer.method} {producer.url_pattern} {producer.source_kind}.{producer.source_path}"


def _consumer_summary(consumer: TokenConsumer) -> str:
    return f"{consumer.method} {consumer.url_pattern} {consumer.location}.{consumer.path}"


def _path_from_url(url: str) -> str:
    parsed = urlsplit(url)
    return parsed.path or "/"


def _looks_like_business_path_usage(consumer: TokenConsumer) -> bool:
    return consumer.location == "request.path"
```

- [ ] **Step 4: Run analyzer tests**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_token_flow.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit analyzer**

```bash
git add RpaClaw/backend/rpa/api_monitor_token_flow.py RpaClaw/backend/tests/test_api_monitor_token_flow.py
git commit -m "feat: add api monitor token flow analyzer"
```

---

### Task 2: Token Flow Profile API And Publish Persistence

**Files:**

- Modify: `RpaClaw/backend/rpa/api_monitor/models.py`
- Modify: `RpaClaw/backend/route/api_monitor.py`
- Modify: `RpaClaw/backend/rpa/api_monitor_mcp_registry.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`

- [ ] **Step 1: Write failing route and persistence tests**

Append to `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`:

```python
def test_token_flow_profile_endpoint_returns_masked_flow(monkeypatch):
    from backend.rpa.api_monitor.models import CapturedApiCall, CapturedRequest, CapturedResponse
    from datetime import datetime, timedelta

    app = _build_app()
    client = TestClient(app)
    session = _build_session()
    token = "8fa7c91e2d8a4c90b0f7"
    session.captured_calls = [
        CapturedApiCall(
            id="bootstrap",
            request=CapturedRequest(
                request_id="req-bootstrap",
                url="https://example.test/api/session",
                method="GET",
                headers={},
                timestamp=datetime.now(),
                resource_type="fetch",
            ),
            response=CapturedResponse(
                status=200,
                status_text="OK",
                headers={"content-type": "application/json"},
                body=f'{{"csrfToken":"{token}"}}',
                content_type="application/json",
                timestamp=datetime.now() + timedelta(milliseconds=100),
            ),
            url_pattern="/api/session",
        ),
        CapturedApiCall(
            id="orders",
            request=CapturedRequest(
                request_id="req-orders",
                url="https://example.test/api/orders",
                method="POST",
                headers={"X-CSRF-Token": token},
                timestamp=datetime.now() + timedelta(seconds=1),
                resource_type="fetch",
            ),
            url_pattern="/api/orders",
        ),
    ]
    monkeypatch.setattr(api_monitor_route.api_monitor_manager, "get_session", lambda session_id: session)

    response = client.get("/api/v1/api-monitor/session/session_1/token-flow-profile")

    assert response.status_code == 200
    profile = response.json()["profile"]
    assert profile["flow_count"] == 1
    assert profile["flows"][0]["producer_summary"] == "GET /api/session response.body.$.csrfToken"
    assert token not in str(profile)


def test_publish_persists_selected_token_flow_without_token_value(monkeypatch):
    from backend.rpa.api_monitor.models import CapturedApiCall, CapturedRequest, CapturedResponse
    from datetime import datetime, timedelta

    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo()
    tool_repo = _MemoryRepo()
    token = "8fa7c91e2d8a4c90b0f7"
    session = _build_session()
    session.captured_calls = [
        CapturedApiCall(
            id="bootstrap",
            request=CapturedRequest(
                request_id="req-bootstrap",
                url="https://example.test/api/session",
                method="GET",
                headers={},
                timestamp=datetime.now(),
                resource_type="fetch",
            ),
            response=CapturedResponse(
                status=200,
                status_text="OK",
                headers={"content-type": "application/json"},
                body=f'{{"csrfToken":"{token}"}}',
                content_type="application/json",
                timestamp=datetime.now() + timedelta(milliseconds=100),
            ),
            url_pattern="/api/session",
        ),
        CapturedApiCall(
            id="orders",
            request=CapturedRequest(
                request_id="req-orders",
                url="https://example.test/api/orders",
                method="POST",
                headers={"X-CSRF-Token": token},
                timestamp=datetime.now() + timedelta(seconds=1),
                resource_type="fetch",
            ),
            url_pattern="/api/orders",
        ),
    ]

    monkeypatch.setattr(
        "backend.rpa.api_monitor_mcp_registry.get_repository",
        lambda collection_name: server_repo if collection_name == "user_mcp_servers" else tool_repo,
    )
    monkeypatch.setattr(api_monitor_route.api_monitor_manager, "get_session", lambda session_id: session)

    profile_response = client.get("/api/v1/api-monitor/session/session_1/token-flow-profile")
    flow_id = profile_response.json()["profile"]["flows"][0]["id"]
    publish_response = client.post(
        "/api/v1/api-monitor/session/session_1/publish-mcp",
        json={
            "mcp_name": "Example MCP",
            "description": "Captured APIs",
            "confirm_overwrite": False,
            "api_monitor_auth": {
                "credential_type": "placeholder",
                "credential_id": "",
                "token_flows": [{"id": flow_id, "enabled": True}],
            },
        },
    )

    assert publish_response.status_code == 200
    saved_server = next(iter(server_repo.docs.values()))
    assert saved_server["api_monitor_auth"]["token_flows"][0]["id"] == flow_id
    assert saved_server["api_monitor_auth"]["token_flows"][0]["inject"]["headers"] == {
        "X-CSRF-Token": "{{ csrf_token }}"
    }
    assert token not in str(saved_server)
```

- [ ] **Step 2: Run publish tests and confirm they fail**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_publish_mcp.py -q
```

Expected: FAIL because `/token-flow-profile` and `api_monitor_auth.token_flows` model support do not exist.

- [ ] **Step 3: Extend publish request models**

Modify `RpaClaw/backend/rpa/api_monitor/models.py`:

```python
class ApiMonitorTokenFlowSelection(BaseModel):
    id: str
    enabled: bool = True


class ApiMonitorAuthConfigRequest(BaseModel):
    credential_type: str = "placeholder"
    credential_id: str = ""
    login_url: str = ""
    token_flows: List[ApiMonitorTokenFlowSelection] = Field(default_factory=list)
```

- [ ] **Step 4: Add profile route**

Modify `RpaClaw/backend/route/api_monitor.py` imports:

```python
from backend.rpa.api_monitor_token_flow import build_api_monitor_token_flow_profile
```

Add route after `get_auth_profile`:

```python
@router.get("/session/{session_id}/token-flow-profile")
async def get_token_flow_profile(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    return {
        "status": "success",
        "profile": build_api_monitor_token_flow_profile(session.captured_calls),
    }
```

- [ ] **Step 5: Preserve token flow selections during auth validation**

Modify `RpaClaw/backend/rpa/api_monitor_auth.py` so `normalize_api_monitor_auth_config()` preserves token flow selections without trying to validate their runtime shape:

```python
def normalize_api_monitor_auth_config(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("api_monitor_auth must be an object")
    credential_type = str(value.get("credential_type") or "").strip()
    credential_id = str(value.get("credential_id") or "").strip()
    login_url = str(value.get("login_url") or "").strip()
    token_flows_raw = value.get("token_flows") or []
    if not credential_type and not credential_id and not token_flows_raw:
        return {}
    if credential_type not in ALLOWED_API_MONITOR_CREDENTIAL_TYPES:
        allowed = ", ".join(sorted(ALLOWED_API_MONITOR_CREDENTIAL_TYPES))
        raise ValueError(f"api_monitor_auth.credential_type must be one of: {allowed}")
    token_flows = [
        {"id": str(item.get("id") or "").strip(), "enabled": bool(item.get("enabled", True))}
        for item in token_flows_raw
        if isinstance(item, Mapping) and str(item.get("id") or "").strip()
    ]
    result: dict[str, Any] = {
        "credential_type": credential_type,
        "credential_id": credential_id,
        "token_flows": token_flows,
    }
    if login_url:
        result["login_url"] = login_url
    return result
```

Keep existing credential ownership validation unchanged.

- [ ] **Step 6: Convert selected flow IDs to runtime configs at publish**

Modify `RpaClaw/backend/rpa/api_monitor_mcp_registry.py` imports:

```python
from backend.rpa.api_monitor_token_flow import build_api_monitor_token_flow_profile
```

Inside `publish_session()`, after `normalized_auth = normalize_api_monitor_auth_config(api_monitor_auth)`, add:

```python
        normalized_auth = _api_monitor_auth_with_runtime_token_flows(session, normalized_auth)
```

Add helper at end of file:

```python
def _api_monitor_auth_with_runtime_token_flows(
    session: ApiMonitorSession,
    auth_config: dict[str, Any],
) -> dict[str, Any]:
    if not auth_config:
        return {}
    selected_ids = {
        str(item.get("id") or "")
        for item in auth_config.get("token_flows", [])
        if isinstance(item, dict) and item.get("enabled", True)
    }
    if not selected_ids:
        cleaned = dict(auth_config)
        cleaned["token_flows"] = []
        return cleaned
    profile = build_api_monitor_token_flow_profile(session.captured_calls)
    runtime_flows = [
        flow["runtime_flow"]
        for flow in profile.get("flows", [])
        if flow.get("id") in selected_ids
    ]
    cleaned = dict(auth_config)
    cleaned["token_flows"] = runtime_flows
    return cleaned
```

- [ ] **Step 7: Run backend publish tests**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_token_flow.py tests/test_api_monitor_publish_mcp.py tests/test_api_monitor_auth.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit profile and publish persistence**

```bash
git add RpaClaw/backend/rpa/api_monitor/models.py RpaClaw/backend/route/api_monitor.py RpaClaw/backend/rpa/api_monitor_auth.py RpaClaw/backend/rpa/api_monitor_mcp_registry.py RpaClaw/backend/tests/test_api_monitor_publish_mcp.py
git commit -m "feat: persist api monitor token flow selections"
```

---

### Task 3: Runtime Setup, Injection, And Retry

**Files:**

- Modify: `RpaClaw/backend/rpa/api_monitor_token_flow.py`
- Modify: `RpaClaw/backend/deepagent/mcp_runtime.py`
- Test: `RpaClaw/backend/tests/deepagent/test_mcp_runtime.py`

- [ ] **Step 1: Add failing runtime tests**

Append to `RpaClaw/backend/tests/deepagent/test_mcp_runtime.py`:

```python
class _SequenceApiMonitorAsyncClient:
    def __init__(self, responses):
        self.calls = []
        self.responses = list(responses)
        self.kwargs = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def test_api_monitor_runtime_extracts_and_injects_csrf_token(monkeypatch):
    repo = _MemoryRepo([
        {
            "mcp_server_id": "mcp_api_monitor",
            "name": "create_order",
            "validation_status": "valid",
            "method": "POST",
            "url": "/api/orders",
            "body_mapping": {"name": "{{ name }}"},
        }
    ])
    client = _SequenceApiMonitorAsyncClient([
        _ApiResponse(json_body={"csrfToken": "runtime-csrf-secret"}),
        _ApiResponse(json_body={"ok": True}),
    ])
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda collection_name: repo)
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: client)

    runtime = McpSdkRuntimeFactory().create_runtime(
        McpServerDefinition(
            id="mcp_api_monitor",
            user_id="user-1",
            name="Example MCP",
            transport="api_monitor",
            scope="user",
            url="https://api.example.test",
            api_monitor_auth={
                "credential_type": "placeholder",
                "credential_id": "",
                "token_flows": [
                    {
                        "id": "flow_1",
                        "name": "csrf_token",
                        "setup": [
                            {
                                "method": "GET",
                                "url": "/api/session",
                                "extract": {"from": "response.body", "path": "$.csrfToken"},
                            }
                        ],
                        "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}},
                        "applies_to": [{"method": "POST", "url": "/api/orders"}],
                        "refresh_on_status": [401, 403, 419],
                        "summary": {
                            "producer": "GET /api/session response.body.$.csrfToken",
                            "consumers": ["POST /api/orders request.headers.X-CSRF-Token"],
                            "reasons": ["exact-value-match"],
                        },
                    }
                ],
            },
        )
    )

    result = asyncio.run(runtime.call_tool("create_order", {"name": "Book"}))

    assert result["success"] is True
    assert client.calls[0][0:2] == ("GET", "https://api.example.test/api/session")
    assert client.calls[1] == (
        "POST",
        "https://api.example.test/api/orders",
        {
            "params": {},
            "headers": {"X-CSRF-Token": "runtime-csrf-secret"},
            "json": {"name": "Book"},
        },
    )
    assert "runtime-csrf-secret" not in str(result["request_preview"])
    assert result["request_preview"]["auth"]["token_flows"][0]["applied"] is True


def test_api_monitor_runtime_refreshes_token_once_on_419(monkeypatch):
    repo = _MemoryRepo([
        {
            "mcp_server_id": "mcp_api_monitor",
            "name": "create_order",
            "validation_status": "valid",
            "method": "POST",
            "url": "/api/orders",
        }
    ])
    client = _SequenceApiMonitorAsyncClient([
        _ApiResponse(json_body={"csrfToken": "old-token"}),
        _ApiResponse(status_code=419, json_body={"error": "expired"}, text='{"error":"expired"}'),
        _ApiResponse(json_body={"csrfToken": "new-token"}),
        _ApiResponse(json_body={"ok": True}),
    ])
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda collection_name: repo)
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: client)

    runtime = McpSdkRuntimeFactory().create_runtime(
        McpServerDefinition(
            id="mcp_api_monitor",
            user_id="user-1",
            name="Example MCP",
            transport="api_monitor",
            scope="user",
            url="https://api.example.test",
            api_monitor_auth={
                "credential_type": "placeholder",
                "credential_id": "",
                "token_flows": [
                    {
                        "id": "flow_1",
                        "name": "csrf_token",
                        "setup": [
                            {
                                "method": "GET",
                                "url": "/api/session",
                                "extract": {"from": "response.body", "path": "$.csrfToken"},
                            }
                        ],
                        "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}},
                        "applies_to": [{"method": "POST", "url": "/api/orders"}],
                        "refresh_on_status": [419],
                    }
                ],
            },
        )
    )

    result = asyncio.run(runtime.call_tool("create_order", {}))

    assert result["success"] is True
    assert [call[0] for call in client.calls] == ["GET", "POST", "GET", "POST"]
    assert client.calls[1][2]["headers"] == {"X-CSRF-Token": "old-token"}
    assert client.calls[3][2]["headers"] == {"X-CSRF-Token": "new-token"}
```

- [ ] **Step 2: Run runtime tests and confirm they fail**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/deepagent/test_mcp_runtime.py::test_api_monitor_runtime_extracts_and_injects_csrf_token tests/deepagent/test_mcp_runtime.py::test_api_monitor_runtime_refreshes_token_once_on_419 -q
```

Expected: FAIL because runtime token flows are not applied.

- [ ] **Step 3: Add runtime helper functions**

Append to `RpaClaw/backend/rpa/api_monitor_token_flow.py`:

```python
def token_flows_for_request(
    token_flows: list[dict[str, Any]] | Any,
    *,
    method: str,
    url_pattern: str,
) -> list[dict[str, Any]]:
    if not isinstance(token_flows, list):
        return []
    result = []
    for flow in token_flows:
        if not isinstance(flow, dict):
            continue
        applies = flow.get("applies_to") or []
        for rule in applies:
            if (
                isinstance(rule, dict)
                and str(rule.get("method") or "").upper() == method.upper()
                and str(rule.get("url") or "") == url_pattern
            ):
                result.append(flow)
                break
    return result


async def resolve_token_flow_values(
    *,
    client: Any,
    base_url: str,
    flows: list[dict[str, Any]],
    build_url,
) -> dict[str, str]:
    values: dict[str, str] = {}
    for flow in flows:
        name = str(flow.get("name") or "").strip()
        setup_steps = flow.get("setup") or []
        if not name or not setup_steps:
            continue
        setup = setup_steps[0]
        response = await client.request(
            str(setup.get("method") or "GET").upper(),
            build_url(base_url, str(setup.get("url") or ""), {}),
            params={},
            headers={},
        )
        token = _extract_token_from_response(response, setup.get("extract") or {})
        if token:
            values[name] = token
    return values


def inject_token_flow_values(
    *,
    flows: list[dict[str, Any]],
    values: dict[str, str],
    headers: dict[str, Any],
    query: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, list[str]]:
    applied: dict[str, list[str]] = {}
    for flow in flows:
        name = str(flow.get("name") or "")
        token = values.get(name)
        if not token:
            continue
        template = "{{ " + name + " }}"
        inject = flow.get("inject") or {}
        for header_name, raw_value in (inject.get("headers") or {}).items():
            headers[str(header_name)] = str(raw_value).replace(template, token)
            applied.setdefault(name, []).append(f"headers.{header_name}")
        for query_name, raw_value in (inject.get("query") or {}).items():
            query[str(query_name)] = str(raw_value).replace(template, token)
            applied.setdefault(name, []).append(f"query.{query_name}")
        for body_path, raw_value in (inject.get("body") or {}).items():
            body[str(body_path).replace("$.", "")] = str(raw_value).replace(template, token)
            applied.setdefault(name, []).append(f"body.{body_path}")
    return applied


def token_flow_preview(flows: list[dict[str, Any]], applied: dict[str, list[str]]) -> list[dict[str, Any]]:
    previews: list[dict[str, Any]] = []
    for flow in flows:
        name = str(flow.get("name") or "")
        summary = flow.get("summary") or {}
        previews.append(
            {
                "name": name,
                "applied": bool(applied.get(name)),
                "source": summary.get("producer", ""),
                "injected": applied.get(name, []),
            }
        )
    return previews


def should_refresh_for_token_flows(flows: list[dict[str, Any]], status_code: int) -> bool:
    return any(status_code in set(flow.get("refresh_on_status") or []) for flow in flows)


def _extract_token_from_response(response: Any, extract: dict[str, Any]) -> str:
    source = str(extract.get("from") or "")
    if source == "response.body":
        try:
            body = response.json()
        except ValueError:
            return ""
        return str(_read_json_path(body, str(extract.get("path") or "")) or "")
    if source == "response.headers":
        return str(response.headers.get(str(extract.get("path") or ""), "") or "")
    return ""


def _read_json_path(value: Any, path: str) -> Any:
    if not path.startswith("$."):
        return None
    current = value
    for part in path[2:].split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current
```

- [ ] **Step 4: Wire token flow runtime into `ApiMonitorMcpRuntime.call_tool()`**

Modify imports in `RpaClaw/backend/deepagent/mcp_runtime.py`:

```python
from backend.rpa.api_monitor_token_flow import (
    inject_token_flow_values,
    resolve_token_flow_values,
    should_refresh_for_token_flows,
    token_flow_preview,
    token_flows_for_request,
)
```

Inside `call_tool()`, after `json_body = request_body or None`, compute active flows:

```python
        token_flows = token_flows_for_request(
            (self._server.api_monitor_auth or {}).get("token_flows", []),
            method=method,
            url_pattern=_api_monitor_tool_url(doc),
        )
        token_flow_applied: dict[str, list[str]] = {}
```

Replace the current one-shot `httpx.AsyncClient` block with:

```python
        async with httpx.AsyncClient(timeout=_api_monitor_timeout_seconds(self._server)) as client:
            if token_flows:
                token_values = await resolve_token_flow_values(
                    client=client,
                    base_url=_api_monitor_request_base_url(self._server, doc),
                    flows=token_flows,
                    build_url=_build_api_monitor_url,
                )
                token_flow_applied = inject_token_flow_values(
                    flows=token_flows,
                    values=token_values,
                    headers=request_headers,
                    query=request_query,
                    body=request_body,
                )
                json_body = request_body or None
                request_kwargs = {
                    "params": request_query,
                    "headers": request_headers,
                }
                if json_body is not None:
                    request_kwargs["json"] = json_body

            response = await client.request(method, url, **request_kwargs)

            if token_flows and should_refresh_for_token_flows(token_flows, response.status_code):
                request_headers = auth_application.headers
                request_query = auth_application.query
                request_body = auth_application.body
                token_values = await resolve_token_flow_values(
                    client=client,
                    base_url=_api_monitor_request_base_url(self._server, doc),
                    flows=token_flows,
                    build_url=_build_api_monitor_url,
                )
                token_flow_applied = inject_token_flow_values(
                    flows=token_flows,
                    values=token_values,
                    headers=request_headers,
                    query=request_query,
                    body=request_body,
                )
                retry_kwargs = {
                    "params": request_query,
                    "headers": request_headers,
                }
                if request_body:
                    retry_kwargs["json"] = request_body
                response = await client.request(method, url, **retry_kwargs)
                request_kwargs = retry_kwargs
                json_body = request_body or None
```

In the returned preview auth field, merge token flow previews:

```python
                "auth": {
                    **auth_application.preview,
                    "token_flows": token_flow_preview(token_flows, token_flow_applied),
                } if token_flows else auth_application.preview,
```

- [ ] **Step 5: Run targeted runtime tests**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/deepagent/test_mcp_runtime.py::test_api_monitor_runtime_extracts_and_injects_csrf_token tests/deepagent/test_mcp_runtime.py::test_api_monitor_runtime_refreshes_token_once_on_419 -q
```

Expected: PASS.

- [ ] **Step 6: Run broader backend runtime suite**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_token_flow.py tests/test_api_monitor_auth.py tests/test_api_monitor_publish_mcp.py tests/deepagent/test_mcp_runtime.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit runtime**

```bash
git add RpaClaw/backend/rpa/api_monitor_token_flow.py RpaClaw/backend/deepagent/mcp_runtime.py RpaClaw/backend/tests/deepagent/test_mcp_runtime.py
git commit -m "feat: apply api monitor token flows at runtime"
```

---

### Task 4: Frontend Publish Dialog And Detail Display

**Files:**

- Modify: `RpaClaw/frontend/src/api/apiMonitor.ts`
- Modify: `RpaClaw/frontend/src/api/mcp.ts`
- Modify: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`
- Modify: `RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue`
- Modify: `RpaClaw/frontend/src/locales/en.ts`
- Modify: `RpaClaw/frontend/src/locales/zh.ts`

- [ ] **Step 1: Extend frontend API types**

Modify `RpaClaw/frontend/src/api/apiMonitor.ts`:

```ts
export type ApiMonitorTokenFlowConfidence = 'high' | 'medium' | 'low'

export interface ApiMonitorTokenFlowProfileItem {
  id: string
  name: string
  producer_summary: string
  consumer_summaries: string[]
  confidence: ApiMonitorTokenFlowConfidence
  enabled_by_default: boolean
  reasons: string[]
}

export interface ApiMonitorTokenFlowProfile {
  flow_count: number
  flows: ApiMonitorTokenFlowProfileItem[]
}

export interface ApiMonitorTokenFlowSelection {
  id: string
  enabled: boolean
}
```

Extend `ApiMonitorAuthConfig`:

```ts
export interface ApiMonitorAuthConfig {
  credential_type: ApiMonitorCredentialType
  credential_id: string
  login_url?: string
  token_flows?: ApiMonitorTokenFlowSelection[]
}
```

Add API call:

```ts
export async function getTokenFlowProfile(sessionId: string): Promise<ApiMonitorTokenFlowProfile> {
  const response = await apiClient.get(`/api-monitor/session/${sessionId}/token-flow-profile`)
  return response.data.profile
}
```

- [ ] **Step 2: Load token flow profile in publish dialog**

Modify imports in `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`:

```ts
  getTokenFlowProfile,
  type ApiMonitorTokenFlowProfile,
```

Add state near existing auth profile state:

```ts
const tokenFlowProfile = ref<ApiMonitorTokenFlowProfile | null>(null);
const selectedTokenFlowIds = ref<Set<string>>(new Set());
```

In `openPublishDialog()`, after loading `authProfile`, load token flow profile:

```ts
    const tokenProfile = await getTokenFlowProfile(sessionId.value);
    tokenFlowProfile.value = tokenProfile;
    selectedTokenFlowIds.value = new Set(
      tokenProfile.flows
        .filter((flow) => flow.enabled_by_default)
        .map((flow) => flow.id),
    );
```

In the catch branch, reset:

```ts
    tokenFlowProfile.value = null;
    selectedTokenFlowIds.value = new Set();
```

Add helper:

```ts
const toggleTokenFlowSelection = (flowId: string, enabled: boolean) => {
  const next = new Set(selectedTokenFlowIds.value);
  if (enabled) {
    next.add(flowId);
  } else {
    next.delete(flowId);
  }
  selectedTokenFlowIds.value = next;
};
```

In `submitPublish()`, include selected flows:

```ts
      api_monitor_auth: {
        ...normalizeApiMonitorAuth(publishAuth),
        token_flows: Array.from(selectedTokenFlowIds.value).map((id) => ({ id, enabled: true })),
      },
```

- [ ] **Step 3: Render token flow cards in publish dialog**

In `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`, inside the publish dialog below the sensitive header profile block, add:

```vue
<div
  v-if="tokenFlowProfile?.flows.length"
  class="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-3 text-xs text-emerald-950 dark:border-emerald-900/50 dark:bg-emerald-950/20 dark:text-emerald-100"
>
  <div class="mb-2 font-semibold">
    {{ t('Detected dynamic token flows') }}
  </div>
  <div class="space-y-2">
    <label
      v-for="flow in tokenFlowProfile.flows"
      :key="flow.id"
      class="flex gap-3 rounded-lg bg-white/70 p-2 dark:bg-white/5"
    >
      <input
        type="checkbox"
        class="mt-1"
        :checked="selectedTokenFlowIds.has(flow.id)"
        @change="toggleTokenFlowSelection(flow.id, ($event.target as HTMLInputElement).checked)"
      />
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2 font-medium">
          <span>{{ flow.name }}</span>
          <span class="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] uppercase text-emerald-800 dark:bg-emerald-900 dark:text-emerald-100">
            {{ flow.confidence }}
          </span>
        </div>
        <div class="mt-1 break-words text-[11px] opacity-80">
          {{ flow.producer_summary }}
        </div>
        <div
          v-for="consumer in flow.consumer_summaries"
          :key="consumer"
          class="mt-0.5 break-words text-[11px] opacity-80"
        >
          {{ consumer }}
        </div>
      </div>
    </label>
  </div>
</div>
```

- [ ] **Step 4: Add locale strings**

Add to `RpaClaw/frontend/src/locales/en.ts`:

```ts
'Detected dynamic token flows': 'Detected dynamic token flows',
```

Add to `RpaClaw/frontend/src/locales/zh.ts`:

```ts
'Detected dynamic token flows': '检测到动态 Token 流程',
```

- [ ] **Step 5: Extend MCP detail types and display**

Modify `RpaClaw/frontend/src/api/mcp.ts` so `ApiMonitorAuthConfig` includes:

```ts
token_flows?: Array<{
  id: string;
  name: string;
  confidence?: string;
  summary?: {
    producer?: string;
    consumers?: string[];
    reasons?: string[];
  };
}>;
```

Modify `RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue` in the overview area where auth status is shown:

```vue
<div
  v-if="detail.server.api_monitor_auth?.token_flows?.length"
  class="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-950 dark:border-emerald-900/50 dark:bg-emerald-950/20 dark:text-emerald-100"
>
  <div class="mb-2 font-semibold">{{ t('Dynamic token flows') }}</div>
  <div
    v-for="flow in detail.server.api_monitor_auth.token_flows"
    :key="flow.id"
    class="mb-2 last:mb-0"
  >
    <div class="font-medium">{{ flow.name }}</div>
    <div class="break-words opacity-80">{{ flow.summary?.producer }}</div>
    <div
      v-for="consumer in flow.summary?.consumers || []"
      :key="consumer"
      class="break-words opacity-80"
    >
      {{ consumer }}
    </div>
  </div>
</div>
```

Add locale strings:

```ts
'Dynamic token flows': 'Dynamic token flows',
```

and:

```ts
'Dynamic token flows': '动态 Token 流程',
```

- [ ] **Step 6: Run frontend checks**

Run:

```bash
cd RpaClaw/frontend
npm run type-check
```

Expected: PASS.

If this repository does not define `type-check`, run:

```bash
cd RpaClaw/frontend
npm run build
```

Expected: PASS.

- [ ] **Step 7: Commit frontend**

```bash
git add RpaClaw/frontend/src/api/apiMonitor.ts RpaClaw/frontend/src/api/mcp.ts RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue RpaClaw/frontend/src/locales/en.ts RpaClaw/frontend/src/locales/zh.ts
git commit -m "feat: show api monitor token flows"
```

---

### Task 5: End-To-End Verification And Hardening

**Files:**

- Modify only files found by verification failures.

- [ ] **Step 1: Run backend focused suite**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_token_flow.py tests/test_api_monitor_auth.py tests/test_api_monitor_publish_mcp.py tests/deepagent/test_mcp_runtime.py -q
```

Expected: PASS.

- [ ] **Step 2: Run existing API Monitor related backend suite**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_capture.py tests/test_api_monitor_confidence.py tests/test_api_monitor_mcp_contract.py tests/test_api_monitor_publish_mcp.py tests/deepagent/test_mcp_registry.py tests/deepagent/test_mcp_runtime.py -q
```

Expected: PASS.

- [ ] **Step 3: Run frontend build or type check**

Run:

```bash
cd RpaClaw/frontend
npm run build
```

Expected: PASS.

- [ ] **Step 4: Manual API smoke test**

Start the app in the usual local dev setup, then:

1. Start an API Monitor session.
2. Record a flow where `/api/session` returns `csrfToken` and `/api/orders` sends `X-CSRF-Token`.
3. Open publish dialog.
4. Confirm the dynamic token flow card appears without token value.
5. Save MCP with the flow enabled.
6. Open MCP detail and confirm the saved flow summary appears.
7. Call the MCP tool and confirm request preview shows `token_flows[].applied=true` without token value.

- [ ] **Step 5: Inspect persisted documents for secret leakage**

Use the local database inspection method already used by the project, or add a temporary test if easier. Verify:

```text
user_mcp_servers.api_monitor_auth.token_flows does not contain actual token values
api_monitor_mcp_tools does not contain actual token values beyond captured tool mappings already expected
logs do not include token values
request_preview redacts injected token values
```

- [ ] **Step 6: Commit verification fixes**

If Step 1-5 required fixes:

```bash
git add <fixed-files>
git commit -m "fix: harden api monitor token flow handling"
```

If no fixes were needed, do not create an empty commit.

---

## Self-Review Notes

Spec coverage:

- Producer/consumer model: Task 1.
- High-entropy and consumer-first discovery: Task 1.
- No token persistence: Task 1 and Task 2 tests.
- Profile API: Task 2.
- Publish and selected flow persistence: Task 2.
- Runtime setup/extract/inject/retry: Task 3.
- UI confirmation and detail display: Task 4.
- Verification and secret leakage checks: Task 5.

Type consistency:

- Backend uses `api_monitor_auth.token_flows` for both publish selection and persisted runtime flows.
- Publish request token flow items use `{id, enabled}`.
- Persisted runtime flow uses `{id, name, setup, inject, applies_to, refresh_on_status, confidence, summary}`.
- Frontend profile type uses `producer_summary` and `consumer_summaries`, matching backend response.

Execution order:

1. Analyzer first because all later features depend on profile/runtime flow generation.
2. API and persistence second because frontend and runtime need stable config shape.
3. Runtime third because persisted flows must exist first.
4. Frontend fourth because it depends on API shape.
5. Verification last.
