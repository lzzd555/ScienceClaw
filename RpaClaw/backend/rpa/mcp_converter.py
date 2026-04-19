from __future__ import annotations

import re
from urllib.parse import urlparse

from backend.rpa.generator import PlaywrightGenerator
from backend.rpa.mcp_models import (
    RpaMcpSanitizeReport,
    RpaMcpSource,
    RpaMcpToolDefinition,
    build_rpa_mcp_output_schema,
)


_LOGIN_BUTTON_RE = re.compile(r"\b(login|log in|sign in|登录)\b", re.IGNORECASE)
_PASSWORD_RE = re.compile(r"password|密码", re.IGNORECASE)
_EMAIL_RE = re.compile(r"email|e-mail|username|user name|account|账号", re.IGNORECASE)


class RpaMcpConverter:
    def __init__(self) -> None:
        self._generator = PlaywrightGenerator()

    def preview(self, *, user_id: str, session_id: str, skill_name: str, name: str, description: str, steps: list[dict], params: dict) -> RpaMcpToolDefinition:
        normalized = self._generator._normalize_step_signals(
            self._generator._infer_missing_tab_transitions(
                self._generator._deduplicate_steps(steps)
            )
        )
        login_range = self._detect_login_range(normalized)
        sanitized_steps, report = self._strip_login_steps(normalized, login_range)
        sanitized_params = self._strip_login_params(params, report)
        requires_cookies = bool(report.removed_steps)
        allowed_domains = self._collect_domains(normalized)
        post_auth_start_url = self._pick_post_auth_start_url(normalized, sanitized_steps)
        input_schema = self._build_input_schema(sanitized_params, requires_cookies=requires_cookies)
        recommended_output_schema, inference_report = self._build_recommended_output_schema(sanitized_steps)
        return RpaMcpToolDefinition(
            id="preview",
            user_id=user_id,
            name=name,
            tool_name=self._tool_name(name),
            description=description,
            requires_cookies=requires_cookies,
            source=RpaMcpSource(session_id=session_id, skill_name=skill_name),
            allowed_domains=allowed_domains,
            post_auth_start_url=post_auth_start_url,
            steps=sanitized_steps,
            params=sanitized_params,
            input_schema=input_schema,
            output_schema=recommended_output_schema,
            recommended_output_schema=recommended_output_schema,
            output_inference_report=inference_report,
            sanitize_report=report,
        )

    def infer_output_from_execution(self, tool: RpaMcpToolDefinition, execution_result: dict) -> tuple[dict, dict]:
        data_schema = self._infer_schema_from_value((execution_result or {}).get("data"))
        downloads_schema = self._infer_array_schema((execution_result or {}).get("downloads") or [])
        artifacts_schema = self._infer_array_schema((execution_result or {}).get("artifacts") or [])
        recommended_output_schema = build_rpa_mcp_output_schema(data_schema)
        recommended_output_schema["properties"]["downloads"] = downloads_schema
        recommended_output_schema["properties"]["artifacts"] = artifacts_schema
        report = dict(tool.output_inference_report or {})
        report["test_result_keys"] = sorted(((execution_result or {}).get("data") or {}).keys()) if isinstance((execution_result or {}).get("data"), dict) else []
        report["last_successful_example_at"] = "test"
        return recommended_output_schema, report

    def _detect_login_range(self, steps: list[dict]) -> tuple[int, int] | None:
        start = None
        end = None
        for index, step in enumerate(steps):
            text = self._step_text(step)
            is_password = bool(step.get("sensitive")) or "{{credential}}" in text or _PASSWORD_RE.search(text)
            is_login_button = step.get("action") == "click" and _LOGIN_BUTTON_RE.search(text)
            is_login_page = "login" in str(step.get("url") or "").lower() or "signin" in str(step.get("url") or "").lower()
            is_email_field = step.get("action") == "fill" and _EMAIL_RE.search(text)
            if start is None and (is_password or is_login_page or is_email_field):
                start = index
            if start is not None and is_login_button:
                end = index
                break
        if start is None or end is None:
            return None
        return start, end

    def _strip_login_steps(self, steps: list[dict], login_range: tuple[int, int] | None) -> tuple[list[dict], RpaMcpSanitizeReport]:
        report = RpaMcpSanitizeReport()
        if login_range is None:
            if self._contains_auth_signals(steps):
                report.warnings.append("Could not determine login step range automatically.")
            return list(steps), report
        start, end = login_range
        report.removed_steps = list(range(start, end + 1))
        return [dict(step) for idx, step in enumerate(steps) if idx < start or idx > end], report

    def _strip_login_params(self, params: dict, report: RpaMcpSanitizeReport) -> dict:
        sanitized = {}
        for key, value in params.items():
            info = dict(value or {})
            original = str(info.get("original_value") or "")
            if info.get("sensitive") or info.get("credential_id") or "{{credential}}" in original:
                report.removed_params.append(key)
                continue
            if _EMAIL_RE.search(key) or _EMAIL_RE.search(original):
                report.removed_params.append(key)
                continue
            sanitized[key] = info
        return sanitized

    def _collect_domains(self, steps: list[dict]) -> list[str]:
        domains = []
        for step in steps:
            host = (urlparse(str(step.get("url") or "")).hostname or "").lower().lstrip(".")
            if host and host not in domains:
                domains.append(host)
        return domains

    def _pick_post_auth_start_url(self, steps: list[dict], sanitized_steps: list[dict]) -> str:
        for step in sanitized_steps:
            url = str(step.get("url") or "").strip()
            if url:
                return url
        for step in steps:
            url = str(step.get("url") or "").strip()
            if url:
                return url
        return ""

    def _build_input_schema(self, params: dict, *, requires_cookies: bool) -> dict:
        properties = {}
        required = []
        if requires_cookies:
            properties["cookies"] = {
                "type": "array",
                "description": "Playwright-compatible cookies for allowed domains",
            }
            required.append("cookies")
        for key, info in params.items():
            prop = {
                "type": info.get("type", "string"),
                "description": info.get("description", ""),
            }
            original = info.get("original_value")
            if original and original != "{{credential}}":
                prop["default"] = original
            if info.get("required"):
                required.append(key)
            properties[key] = prop
        return {"type": "object", "properties": properties, "required": required}

    def _build_recommended_output_schema(self, steps: list[dict]) -> tuple[dict, dict]:
        properties = {}
        recording_signals = []
        has_download = False
        for step in steps:
            action = str(step.get("action") or "")
            result_key = self._generator._normalize_result_key(step.get("result_key"))
            if action == "extract_text" and result_key:
                properties[result_key] = {"type": "string", "description": str(step.get("description") or "")}
                recording_signals.append({"kind": "extract_text", "key": result_key, "description": str(step.get("description") or "")})
            download_signal = self._generator._download_signal(step)
            if download_signal:
                has_download = True
                recording_signals.append({
                    "kind": "download",
                    "filename": str(download_signal.get("filename") or ""),
                    "description": str(step.get("description") or ""),
                })
        data_schema = {
            "type": "object",
            "properties": properties,
            "additionalProperties": not bool(properties),
        }
        output_schema = build_rpa_mcp_output_schema(data_schema)
        if has_download:
            output_schema["properties"]["downloads"] = self._infer_array_schema([{"filename": "sample.file", "path": "/tmp/sample.file"}])
        return output_schema, {"recording_signals": recording_signals}

    def _infer_schema_from_value(self, value):
        if value is None:
            return {"type": "object", "properties": {}, "additionalProperties": True}
        if isinstance(value, bool):
            return {"type": "boolean"}
        if isinstance(value, int) and not isinstance(value, bool):
            return {"type": "integer"}
        if isinstance(value, float):
            return {"type": "number"}
        if isinstance(value, str):
            return {"type": "string"}
        if isinstance(value, list):
            return self._infer_array_schema(value)
        if isinstance(value, dict):
            return {
                "type": "object",
                "properties": {str(key): self._infer_schema_from_value(item) for key, item in value.items()},
                "additionalProperties": False,
            }
        return {"type": "string"}

    def _infer_array_schema(self, value):
        items = value if isinstance(value, list) else []
        if not items:
            return {"type": "array", "items": {"type": "object", "additionalProperties": True}}
        merged_items = self._merge_object_schemas([self._infer_schema_from_value(item) for item in items])
        return {"type": "array", "items": merged_items}

    def _merge_object_schemas(self, schemas: list[dict]) -> dict:
        if not schemas:
            return {"type": "object", "additionalProperties": True}
        object_schemas = [schema for schema in schemas if schema.get("type") == "object"]
        if len(object_schemas) != len(schemas):
            return schemas[0]
        properties = {}
        for schema in object_schemas:
            for key, value in (schema.get("properties") or {}).items():
                properties.setdefault(key, value)
        return {"type": "object", "properties": properties, "additionalProperties": False}

    def _contains_auth_signals(self, steps: list[dict]) -> bool:
        return any(self._is_auth_like_step(step) for step in steps)

    def _is_auth_like_step(self, step: dict) -> bool:
        text = self._step_text(step)
        url = str(step.get("url") or "").lower()
        return bool(
            step.get("sensitive")
            or "{{credential}}" in text
            or _PASSWORD_RE.search(text)
            or (step.get("action") == "fill" and _EMAIL_RE.search(text))
            or _LOGIN_BUTTON_RE.search(text)
            or "login" in url
            or "signin" in url
        )

    def _step_text(self, step: dict) -> str:
        return " ".join(
            str(step.get(key) or "") for key in ("description", "value", "target", "url")
        )

    def _tool_name(self, name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", (name or "tool").strip().lower()).strip("_")
        return f"rpa_{slug or 'tool'}"
