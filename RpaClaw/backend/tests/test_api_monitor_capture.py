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

    def test_ignores_query_params(self):
        result = _normalize_url_for_dedup("/api/orders?b=2&a=1")
        assert result == "/api/orders"

    def test_ignores_empty_value_params(self):
        result = _normalize_url_for_dedup("/api/orders?name=&id=123")
        assert result == "/api/orders"

    def test_plain_path_unchanged(self):
        assert _normalize_url_for_dedup("/api/orders") == "/api/orders"

    def test_full_url_strips_to_path_only(self):
        result = _normalize_url_for_dedup("https://example.com/api/orders?page=1&name=test")
        assert result == "/api/orders"

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

    def test_same_endpoint_different_query_values_deduped(self):
        call1 = _make_call("GET", "https://example.com/api/orders?page=1")
        call2 = _make_call("GET", "https://example.com/api/orders?page=2")
        assert dedup_key(call1) == dedup_key(call2)

    def test_same_endpoint_with_empty_params_deduped(self):
        call1 = _make_call("GET", "https://example.com/api/orders?name=")
        call2 = _make_call("GET", "https://example.com/api/orders")
        assert dedup_key(call1) == dedup_key(call2)


from backend.rpa.api_monitor.network_capture import should_capture


class TestShouldCaptureNoiseFilter:
    def test_glb_model_filtered(self):
        assert should_capture("https://example.com/models/car.glb", "fetch") is False

    def test_gltf_model_filtered(self):
        assert should_capture("https://example.com/models/scene.gltf", "fetch") is False

    def test_wasm_filtered(self):
        assert should_capture("https://example.com/app.wasm", "fetch") is False

    def test_bin_data_filtered(self):
        assert should_capture("https://example.com/data.bin", "fetch") is False

    def test_pdf_download_not_filtered(self):
        assert should_capture("https://example.com/api/report.pdf", "fetch") is True

    def test_docx_download_not_filtered(self):
        assert should_capture("https://example.com/api/document.docx", "fetch") is True

    def test_zip_download_not_filtered(self):
        assert should_capture("https://example.com/api/export.zip", "fetch") is True

    def test_xlsx_download_not_filtered(self):
        assert should_capture("https://example.com/api/data.xlsx", "fetch") is True

    def test_normal_api_not_filtered(self):
        assert should_capture("https://example.com/api/orders", "fetch") is True

    def test_same_origin_api_not_filtered(self):
        assert (
            should_capture(
                "https://example.com/api/orders",
                "fetch",
                page_url="https://example.com/app",
            )
            is True
        )

    def test_relative_api_not_filtered_with_page_url(self):
        assert should_capture("/api/orders", "fetch", page_url="https://example.com/app") is True

    def test_third_party_api_filtered_with_page_url(self):
        assert (
            should_capture(
                "https://analytics.example.net/collect",
                "fetch",
                page_url="https://example.com/app",
            )
            is False
        )

    def test_capture_without_page_url_keeps_existing_behavior(self):
        assert should_capture("https://analytics.example.net/collect", "fetch") is True


class _Request:
    url = "https://example.com/api/orders"
    method = "GET"
    headers = {"accept": "application/json"}
    resource_type = "fetch"
    post_data = None


class TestCaptureEvidence:
    def test_on_request_stores_source_evidence(self):
        from backend.rpa.api_monitor.network_capture import NetworkCaptureEngine

        engine = NetworkCaptureEngine(
            page_url_provider=lambda: "https://example.com/app",
            evidence_provider=lambda request: {
                "initiator_type": "script",
                "initiator_urls": ["https://example.com/app/assets/main.js"],
                "js_stack_urls": ["https://example.com/app/assets/main.js"],
                "frame_url": "https://example.com/app",
                "action_window_matched": True,
            },
        )

        request = _Request()
        engine.on_request(request)

        stored = engine._in_flight[id(request)]["request"]
        evidence = engine._in_flight[id(request)]["source_evidence"]

        assert stored.url == "https://example.com/api/orders"
        assert evidence["initiator_type"] == "script"
        assert evidence["action_window_matched"] is True

    def test_on_request_uses_request_frame_url_for_origin_filter(self):
        from backend.rpa.api_monitor.network_capture import NetworkCaptureEngine

        class _Frame:
            url = "https://popup.example.test/workbench"

        class _PopupRequest:
            url = "https://popup.example.test/api/search"
            method = "GET"
            headers = {"accept": "application/json"}
            resource_type = "fetch"
            post_data = None
            frame = _Frame()

        engine = NetworkCaptureEngine(
            page_url_provider=lambda: "https://app.example.test/home",
        )

        request = _PopupRequest()
        engine.on_request(request)

        assert id(request) in engine._in_flight


class TestSourceEvidenceHelpers:
    def test_extract_initiator_urls_from_cdp_stack(self):
        from backend.rpa.api_monitor.manager import _initiator_to_evidence

        evidence = _initiator_to_evidence({
            "type": "script",
            "stack": {
                "callFrames": [
                    {"url": "https://example.com/app/assets/main.js", "functionName": "load"},
                    {"url": "chrome-extension://abc/injected.js", "functionName": "run"},
                ]
            },
        })

        assert evidence["initiator_type"] == "script"
        assert evidence["initiator_urls"] == [
            "https://example.com/app/assets/main.js",
            "chrome-extension://abc/injected.js",
        ]

    def test_extract_stack_urls_from_js_error_stack(self):
        from backend.rpa.api_monitor.manager import _stack_to_urls

        urls = _stack_to_urls(
            "Error\n"
            " at fetchData (https://example.com/app/assets/main.js:10:1)\n"
            " at run (chrome-extension://abc/injected.js:2:3)\n"
        )

        assert "https://example.com/app/assets/main.js" in urls
        assert "chrome-extension://abc/injected.js" in urls
