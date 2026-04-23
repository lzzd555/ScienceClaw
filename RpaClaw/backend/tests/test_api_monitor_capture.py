"""Tests for network_capture URL normalization and dedup logic."""

from datetime import datetime

from backend.rpa.api_monitor.network_capture import (
    _normalize_url_for_dedup,
    dedup_key,
    parameterize_url,
)
from backend.rpa.api_monitor.models import CapturedApiCall, CapturedRequest


def _make_call(method: str, url: str) -> CapturedApiCall:
    return CapturedApiCall(
        request=CapturedRequest(
            request_id="test",
            url=url,
            method=method,
            headers={},
            timestamp=datetime(2026, 1, 1),
            resource_type="fetch",
        ),
    )


class TestNormalizeUrlForDedup:
    def test_removes_trailing_question_mark(self):
        assert _normalize_url_for_dedup("/api/orders?") == "/api/orders"

    def test_removes_empty_query_params(self):
        assert _normalize_url_for_dedup("/api/orders?&") == "/api/orders"

    def test_sorts_query_params(self):
        result = _normalize_url_for_dedup("/api/orders?b=2&a=1")
        assert result == "/api/orders?a=1&b=2"

    def test_removes_empty_value_params(self):
        result = _normalize_url_for_dedup("/api/orders?name=&id=123")
        assert result == "/api/orders?id=123"

    def test_plain_path_unchanged(self):
        assert _normalize_url_for_dedup("/api/orders") == "/api/orders"

    def test_full_url_strips_to_path_and_sorted_query(self):
        result = _normalize_url_for_dedup("https://example.com/api/orders?page=1&name=test")
        assert result == "/api/orders?name=test&page=1"

    def test_preserves_path_with_param_placeholders(self):
        result = _normalize_url_for_dedup("/api/users/{id}/orders")
        assert result == "/api/users/{id}/orders"


class TestDedupKeyNormalization:
    def test_same_endpoint_with_and_without_trailing_question(self):
        call1 = _make_call("GET", "https://example.com/api/orders?")
        call2 = _make_call("GET", "https://example.com/api/orders")
        assert dedup_key(call1) == dedup_key(call2)

    def test_same_endpoint_different_param_order(self):
        call1 = _make_call("GET", "https://example.com/api/orders?b=2&a=1")
        call2 = _make_call("GET", "https://example.com/api/orders?a=1&b=2")
        assert dedup_key(call1) == dedup_key(call2)

    def test_same_endpoint_with_empty_params_deduped(self):
        call1 = _make_call("GET", "https://example.com/api/orders?name=")
        call2 = _make_call("GET", "https://example.com/api/orders")
        assert dedup_key(call1) == dedup_key(call2)
