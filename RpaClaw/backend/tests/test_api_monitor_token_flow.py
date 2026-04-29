"""Tests for API Monitor token flow analyzer."""

from datetime import datetime, timedelta

import pytest

from backend.rpa.api_monitor.models import CapturedApiCall, CapturedRequest, CapturedResponse
from backend.rpa.api_monitor_token_flow import (
    build_api_monitor_token_flow_profile,
    entropy_per_char,
    is_dynamic_value_candidate,
    resolve_token_flows_for_publish,
    validate_manual_token_flow,
    normalize_token_flow_config,
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
    resp = None
    if response_body is not None or response_headers:
        resp = CapturedResponse(
            status=200,
            status_text="OK",
            headers=response_headers or {"content-type": "application/json"},
            body=response_body,
            content_type="application/json" if response_body else None,
            timestamp=ts + timedelta(milliseconds=100),
        )
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
        response=resp,
        url_pattern=url.replace("https://example.test", ""),
    )
    call.id = call_id
    return call


# ── Entropy and value filtering ─────────────────────────────────────────


def test_entropy_per_char_scores_repeated_values_low_and_tokens_higher():
    assert entropy_per_char("aaaaaaaaaaaaaaaa") == 0
    assert entropy_per_char("8fa7c91e2d8a4c90b0f7") > 3.0


def test_dynamic_value_candidate_uses_length_entropy_and_shape_filters():
    assert is_dynamic_value_candidate("8fa7c91e2d8a4c90b0f7") is True
    assert is_dynamic_value_candidate("12345678") is False
    assert is_dynamic_value_candidate("active") is False
    assert is_dynamic_value_candidate("2026-04-27") is False


def test_dynamic_value_candidate_allows_strong_name_with_short_value():
    assert is_dynamic_value_candidate("abc123", field_name="csrfToken") is True


# ── Producer-first: JSON response -> request header ─────────────────────


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


# ── Consumer-first: custom header backtracks to response field ──────────


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


# ── Multiple consumers for one token ────────────────────────────────────


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


# ── False positive: business ID in URL path ─────────────────────────────


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


# ── Cookie-to-header flow ───────────────────────────────────────────────


def test_profile_links_set_cookie_to_header_consumer():
    calls = [
        _call(
            "bootstrap",
            method="GET",
            url="https://example.test/api/session",
            response_headers={
                "content-type": "application/json",
                "set-cookie": "XSRF-TOKEN=abc123def456; path=/",
            },
            response_body="{}",
            seconds=0,
        ),
        _call(
            "create_order",
            method="POST",
            url="https://example.test/api/orders",
            request_headers={"X-XSRF-TOKEN": "abc123def456"},
            request_body='{"name":"order"}',
            seconds=1,
        ),
    ]

    profile = build_api_monitor_token_flow_profile(calls)

    assert profile["flow_count"] == 1
    flow = profile["flows"][0]
    assert "XSRF-TOKEN" in flow["producer_summary"]
    assert flow["consumer_summaries"] == ["POST /api/orders request.headers.X-XSRF-TOKEN"]


# ── Response header token -> request header ─────────────────────────────


def test_profile_links_response_header_token_to_request_header():
    calls = [
        _call(
            "bootstrap",
            method="GET",
            url="https://example.test/api/session",
            response_headers={
                "content-type": "application/json",
                "x-csrf-token": "8fa7c91e2d8a4c90b0f7",
            },
            response_body="{}",
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
    assert "response.headers.x-csrf-token" in flow["producer_summary"]


# ── Query parameter consumer ────────────────────────────────────────────


def test_profile_links_response_token_to_query_consumer():
    calls = [
        _call(
            "bootstrap",
            method="GET",
            url="https://example.test/api/session",
            response_body='{"csrfToken":"8fa7c91e2d8a4c90b0f7"}',
            seconds=0,
        ),
        _call(
            "upload",
            method="POST",
            url="https://example.test/api/upload?csrf_token=8fa7c91e2d8a4c90b0f7",
            seconds=1,
        ),
    ]

    profile = build_api_monitor_token_flow_profile(calls)

    assert profile["flow_count"] == 1
    flow = profile["flows"][0]
    assert "request.query.csrf_token" in flow["consumer_summaries"][0]


# ── Form-urlencoded body consumer ───────────────────────────────────────


def test_profile_links_response_token_to_form_body_consumer():
    calls = [
        _call(
            "bootstrap",
            method="GET",
            url="https://example.test/api/session",
            response_body='{"csrfToken":"8fa7c91e2d8a4c90b0f7"}',
            seconds=0,
        ),
        _call(
            "submit",
            method="POST",
            url="https://example.test/api/submit",
            request_body="_csrf=8fa7c91e2d8a4c90b0f7&name=order",
            seconds=1,
        ),
    ]

    profile = build_api_monitor_token_flow_profile(calls)

    assert profile["flow_count"] == 1
    flow = profile["flows"][0]
    assert "request.body.$._csrf" in flow["consumer_summaries"][0]


# ── JSON body consumer ──────────────────────────────────────────────────


def test_profile_links_response_token_to_json_body_consumer():
    calls = [
        _call(
            "bootstrap",
            method="GET",
            url="https://example.test/api/session",
            response_body='{"csrfToken":"8fa7c91e2d8a4c90b0f7"}',
            seconds=0,
        ),
        _call(
            "submit",
            method="POST",
            url="https://example.test/api/submit",
            request_body='{"_csrf":"8fa7c91e2d8a4c90b0f7","name":"order"}',
            seconds=1,
        ),
    ]

    profile = build_api_monitor_token_flow_profile(calls)

    assert profile["flow_count"] == 1
    flow = profile["flows"][0]
    assert "request.body.$._csrf" in flow["consumer_summaries"][0]


# ── Time ordering: consumer before producer is rejected ─────────────────


def test_profile_rejects_consumer_before_producer():
    calls = [
        _call(
            "create_order",
            method="POST",
            url="https://example.test/api/orders",
            request_headers={"X-CSRF-Token": "8fa7c91e2d8a4c90b0f7"},
            seconds=0,
        ),
        _call(
            "bootstrap",
            method="GET",
            url="https://example.test/api/session",
            response_body='{"csrfToken":"8fa7c91e2d8a4c90b0f7"}',
            seconds=1,
        ),
    ]

    profile = build_api_monitor_token_flow_profile(calls)

    assert profile["flow_count"] == 0


# ── Short IDs and enum values not treated as tokens ─────────────────────


def test_profile_ignores_short_numeric_ids_and_enums():
    calls = [
        _call(
            "list",
            method="GET",
            url="https://example.test/api/status",
            response_body='{"status":"active","code":"12345678"}',
            seconds=0,
        ),
        _call(
            "detail",
            method="GET",
            url="https://example.test/api/detail",
            request_headers={"X-Status": "active"},
            seconds=1,
        ),
    ]

    profile = build_api_monitor_token_flow_profile(calls)

    assert profile["flow_count"] == 0


# ── No token value in profile output ────────────────────────────────────


def test_profile_never_contains_token_value():
    token_value = "8fa7c91e2d8a4c90b0f7_deadbeef"
    calls = [
        _call(
            "bootstrap",
            method="GET",
            url="https://example.test/api/session",
            response_body=f'{{"csrfToken":"{token_value}"}}',
            seconds=0,
        ),
        _call(
            "create_order",
            method="POST",
            url="https://example.test/api/orders",
            request_headers={"X-CSRF-Token": token_value},
            request_body='{"name":"order"}',
            seconds=1,
        ),
    ]

    profile = build_api_monitor_token_flow_profile(calls)
    profile_str = str(profile)

    assert token_value not in profile_str


# ── Consumer dedup ────────────────────────────────────────────────────────


def test_profile_deduplicates_repeated_same_endpoint_consumers():
    calls = [
        _call(
            "producer",
            method="GET",
            url="https://example.test/api/session",
            response_body='{"csrfToken":"8fa7c91e2d8a4c90b0f7"}',
            seconds=0,
        ),
        _call(
            "orders_1",
            method="GET",
            url="https://example.test/api/orders",
            request_headers={"X-CSRF-Token": "8fa7c91e2d8a4c90b0f7"},
            seconds=1,
        ),
        _call(
            "orders_2",
            method="GET",
            url="https://example.test/api/orders",
            request_headers={"X-CSRF-Token": "8fa7c91e2d8a4c90b0f7"},
            seconds=2,
        ),
    ]

    profile = build_api_monitor_token_flow_profile(calls)

    assert profile["flow_count"] == 1
    flow = profile["flows"][0]
    assert flow["consumer_summaries"] == ["GET /api/orders request.headers.X-CSRF-Token"]
    assert flow["sample_count"] == 2
    assert flow["source_call_ids"] == ["orders_1", "orders_2"]


def test_token_flow_consumers_use_path_without_query_for_endpoint_identity():
    calls = [
        _call(
            "producer",
            method="GET",
            url="https://example.test/api/session",
            response_body='{"csrfToken":"8fa7c91e2d8a4c90b0f7"}',
            seconds=0,
        ),
        _call(
            "orders_page_1",
            method="GET",
            url="https://example.test/api/orders?page=1",
            request_headers={"X-CSRF-Token": "8fa7c91e2d8a4c90b0f7"},
            seconds=1,
        ),
        _call(
            "orders_page_2",
            method="GET",
            url="https://example.test/api/orders?page=2",
            request_headers={"X-CSRF-Token": "8fa7c91e2d8a4c90b0f7"},
            seconds=2,
        ),
    ]

    profile = build_api_monitor_token_flow_profile(calls)
    flow = profile["flows"][0]
    runtime_flows = resolve_token_flows_for_publish(calls, [{"id": flow["id"], "enabled": True}])

    assert flow["consumer_summaries"] == ["GET /api/orders request.headers.X-CSRF-Token"]
    assert flow["source_call_ids"] == ["orders_page_1", "orders_page_2"]
    assert runtime_flows[0]["consumers"] == [
        {
            "method": "GET",
            "url": "/api/orders",
            "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}, "query": {}, "body": {}},
        }
    ]


# ── Manual token flow validation ──────────────────────────────────────────


def test_validate_manual_token_flow_accepts_complete_config():
    flow = validate_manual_token_flow(
        {
            "id": "manual_csrf",
            "name": "csrf_token",
            "enabled": True,
            "source": "manual",
            "producer": {
                "request": {"method": "GET", "url": "/api/session"},
                "extract": [{"name": "csrf_token", "from": "response.body", "path": "$.csrfToken"}],
            },
            "consumers": [
                {
                    "method": "GET",
                    "url": "/api/orders",
                    "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}},
                }
            ],
        }
    )

    assert flow["id"] == "manual_csrf"
    assert flow["producer"]["request"]["method"] == "GET"
    assert flow["consumers"][0]["inject"]["headers"] == {"X-CSRF-Token": "{{ csrf_token }}"}


def test_validate_manual_token_flow_rejects_missing_extract():
    with pytest.raises(ValueError, match="producer.extract"):
        validate_manual_token_flow(
            {
                "id": "manual_csrf",
                "name": "csrf_token",
                "producer": {"request": {"method": "GET", "url": "/api/session"}, "extract": []},
                "consumers": [{"method": "GET", "url": "/api/orders", "inject": {"headers": {}}}],
            }
        )


def test_validate_manual_token_flow_rejects_unknown_template_variable():
    with pytest.raises(ValueError, match="unknown template variable: missing_token"):
        validate_manual_token_flow(
            {
                "id": "manual_csrf",
                "name": "csrf_token",
                "producer": {
                    "request": {"method": "GET", "url": "/api/session"},
                    "extract": [{"name": "csrf_token", "from": "response.body", "path": "$.csrfToken"}],
                },
                "consumers": [
                    {
                        "method": "GET",
                        "url": "/api/orders",
                        "inject": {"headers": {"X-CSRF-Token": "{{ missing_token }}"}},
                    }
                ],
            }
        )


# ── V1 migration ──────────────────────────────────────────────────────────


def test_normalize_token_flow_config_converts_legacy_v1_flow():
    flow = normalize_token_flow_config(
        {
            "id": "legacy_csrf",
            "name": "csrf_token",
            "setup": {"method": "GET", "url": "/api/session"},
            "extract": {"from": "response.body", "path": "$.csrfToken", "name": "csrf_token"},
            "inject": {
                "method": "GET",
                "url": "/api/orders",
                "to": "request.headers",
                "name": "X-CSRF-Token",
            },
        }
    )

    assert flow["producer"]["request"]["method"] == "GET"
    assert flow["producer"]["request"]["url"] == "/api/session"
    assert flow["producer"]["extract"] == [
        {"name": "csrf_token", "from": "response.body", "path": "$.csrfToken", "secret": True}
    ]
    assert flow["consumers"] == [
        {
            "method": "GET",
            "url": "/api/orders",
            "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}, "query": {}, "body": {}},
        }
    ]


def test_normalize_token_flow_config_passes_through_v2_flow():
    v2_flow = {
        "id": "flow_v2",
        "name": "csrf_token",
        "producer": {
            "request": {"method": "GET", "url": "/api/session"},
            "extract": [{"name": "csrf_token", "from": "response.body", "path": "$.csrfToken"}],
        },
        "consumers": [
            {
                "method": "GET",
                "url": "/api/orders",
                "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}},
            }
        ],
    }
    assert normalize_token_flow_config(v2_flow) == v2_flow
