"""Tests for token flow producer discovery narrowing."""

import pytest
from backend.rpa.api_monitor_token_flow import is_dynamic_value_candidate


class TestProducerDiscoveryNarrowing:
    """改动2: 只保留语义规则，去掉高熵扫描。"""

    def test_semantic_name_short_value_passes(self):
        """字段名含 token 且值长度>=6，应通过。"""
        assert is_dynamic_value_candidate("abc123", field_name="csrf_token") is True

    def test_semantic_name_short_value_too_short_fails(self):
        """字段名含 token 但值太短（<6），应拒绝。"""
        assert is_dynamic_value_candidate("ab", field_name="csrf_token") is False

    def test_non_semantic_non_token_name_rejected(self):
        """字段名完全不相关，高熵值也应拒绝。"""
        high_entropy_value = "xK9$mB2#nL5&pQ8!rT3"
        assert is_dynamic_value_candidate(high_entropy_value, field_name="data") is False

    def test_non_semantic_long_value_rejected(self):
        """字段名不相关，长值高熵也应拒绝。"""
        long_random = "8fa7c91e2d8a4c90b0f7a3d5e1c2b4a6"
        assert is_dynamic_value_candidate(long_random, field_name="r") is False

    def test_common_values_still_rejected(self):
        """常见值仍被拒绝。"""
        for val in ["true", "false", "null", "active", "success"]:
            assert is_dynamic_value_candidate(val, field_name="token") is False

    def test_pure_numeric_short_rejected(self):
        """短纯数字仍被拒绝。"""
        assert is_dynamic_value_candidate("12345", field_name="token") is False

    def test_non_semantic_high_entropy_enabled(self):
        """启用扩展发现后，高熵值可通过。"""
        high_entropy_value = "xK9$mB2#nL5&pQ8!rT3"
        assert is_dynamic_value_candidate(high_entropy_value, field_name="data", enable_extended_discovery=True) is True

    def test_non_semantic_long_value_enabled(self):
        """启用扩展发现后，长高熵值可通过。"""
        long_random = "8fa7c91e2d8a4c90b0f7a3d5e1c2b4a6"
        assert is_dynamic_value_candidate(long_random, field_name="r", enable_extended_discovery=True) is True


from backend.rpa.api_monitor.models import CapturedApiCall, CapturedRequest, CapturedResponse
from backend.rpa.api_monitor_token_flow import build_api_monitor_token_flow_profile
from datetime import datetime, timezone


def _make_call(
    call_id: str, method: str, url: str,
    resp_body: str = "", resp_headers: dict | None = None,
    req_headers: dict | None = None, timestamp: datetime | None = None,
) -> CapturedApiCall:
    ts = timestamp or datetime.now(timezone.utc)
    return CapturedApiCall(
        id=call_id,
        request=CapturedRequest(
            request_id=call_id, method=method, url=url,
            headers=req_headers or {}, body="", timestamp=ts,
            resource_type="xhr",
        ),
        response=CapturedResponse(
            status=200, status_text="OK", headers=resp_headers or {},
            body=resp_body, content_type="application/json",
            timestamp=ts,
        ),
        url_pattern=url,
    )


class TestProducerDedupByUrl:
    """改动3: 不同接口同名字段产生独立 flow。"""

    def test_different_urls_same_field_different_values(self):
        """不同接口返回同名字段但值不同，产生两个独立 flow。"""
        calls = [
            _make_call("c1", "GET", "https://example.com/api/session",
                       resp_body='{"token": "abc_token_value_1"}'),
            _make_call("c2", "GET", "https://example.com/api/config",
                       resp_body='{"token": "xyz_token_value_2"}'),
            _make_call("c3", "POST", "https://example.com/api/orders",
                       req_headers={"X-Token": "abc_token_value_1"}),
            _make_call("c4", "POST", "https://example.com/api/settings",
                       req_headers={"X-Token": "xyz_token_value_2"}),
        ]
        profile = build_api_monitor_token_flow_profile(calls)
        assert profile["flow_count"] == 2, (
            f"Expected 2 independent flows, got {profile['flow_count']}"
        )

    def test_different_urls_same_field_same_value(self):
        """不同接口返回同名字段且值相同，仍然产生两个独立 flow。"""
        calls = [
            _make_call("c1", "GET", "https://example.com/api/session",
                       resp_body='{"csrfToken": "shared_csrf_value_12345"}'),
            _make_call("c2", "GET", "https://example.com/api/bootstrap",
                       resp_body='{"csrfToken": "shared_csrf_value_12345"}'),
            _make_call("c3", "POST", "https://example.com/api/orders",
                       req_headers={"X-CSRF-Token": "shared_csrf_value_12345"}),
        ]
        profile = build_api_monitor_token_flow_profile(calls)
        assert profile["flow_count"] == 2, (
            f"Expected 2 independent flows (one per URL), got {profile['flow_count']}"
        )
