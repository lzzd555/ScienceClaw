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
