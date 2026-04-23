from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml


TOOL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TEMPLATE_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
SINGLE_TEMPLATE_RE = re.compile(r"^\s*{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}\s*$")
ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "token",
}
SENSITIVE_PREVIEW_KEY_NAMES = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "token",
    "access_token",
    "refresh_token",
    "credential",
    "secret",
    "password",
    "key",
}


@dataclass
class ApiMonitorToolContract:
    valid: bool
    yaml_definition: str = ""
    name: str = ""
    description: str = ""
    method: str = ""
    url: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    response_schema: dict[str, Any] = field(default_factory=dict)
    path_mapping: dict[str, Any] = field(default_factory=dict)
    query_mapping: dict[str, Any] = field(default_factory=dict)
    body_mapping: dict[str, Any] = field(default_factory=dict)
    header_mapping: dict[str, Any] = field(default_factory=dict)
    validation_errors: list[str] = field(default_factory=list)
    raw_definition: Any = field(default_factory=dict)

    def to_document(self) -> dict[str, Any]:
        return {
            "yaml_definition": self.yaml_definition,
            "name": self.name,
            "description": self.description,
            "method": self.method,
            "url": self.url,
            "input_schema": self.input_schema,
            "response_schema": self.response_schema,
            "path_mapping": self.path_mapping,
            "query_mapping": self.query_mapping,
            "body_mapping": self.body_mapping,
            "header_mapping": self.header_mapping,
            "raw_definition": self.raw_definition,
            "validation_status": "valid" if self.valid else "invalid",
            "validation_errors": list(self.validation_errors),
        }


def parse_api_monitor_tool_yaml(yaml_definition: str) -> ApiMonitorToolContract:
    try:
        parsed = yaml.safe_load(yaml_definition) or {}
    except Exception as exc:  # noqa: BLE001
        return ApiMonitorToolContract(
            valid=False,
            yaml_definition=yaml_definition,
            validation_errors=[f"YAML parse error: {exc}"],
        )

    if not isinstance(parsed, dict):
        return ApiMonitorToolContract(
            valid=False,
            yaml_definition=yaml_definition,
            raw_definition=parsed,
            validation_errors=["YAML root must be an object"],
        )

    errors: list[str] = []
    name = _string_value(parsed.get("name"))
    description = _string_value(parsed.get("description"))
    method = _string_value(parsed.get("method")).upper()
    url = _string_value(parsed.get("url"))

    if not name:
        errors.append("name is required")
    elif not TOOL_NAME_RE.match(name):
        errors.append("name must match ^[A-Za-z_][A-Za-z0-9_]*$")

    if not description:
        errors.append("description is required")
    if not method:
        errors.append("method is required")
    elif method not in ALLOWED_METHODS:
        errors.append("method must be one of GET, POST, PUT, PATCH, DELETE")
    if not url:
        errors.append("url is required")

    parameters_raw = parsed.get("parameters")
    parameters = _as_dict(parameters_raw)
    input_schema = parameters
    properties = _as_dict(parameters.get("properties")) if parameters else {}
    if parameters_raw is None:
        errors.append("parameters is required")
    elif not isinstance(parameters_raw, dict):
        errors.append("parameters must be an object")
    elif _string_value(parameters.get("type")) != "object":
        errors.append("parameters.type must be object")
    if parameters and not isinstance(parameters.get("properties"), dict):
        errors.append("parameters.properties must be an object")

    request_raw = parsed.get("request")
    request: dict[str, Any] = {}
    if request_raw is None:
        pass
    elif isinstance(request_raw, dict):
        request = request_raw
    else:
        errors.append("request must be an object")

    path_mapping, path_errors = _validate_mapping_section("request.path", request.get("path"), properties)
    query_mapping, query_errors = _validate_mapping_section("request.query", request.get("query"), properties)
    body_mapping, body_errors = _validate_mapping_section("request.body", request.get("body"), properties)
    header_mapping, headers_errors = _validate_mapping_section("request.headers", request.get("headers"), properties)
    errors.extend(path_errors)
    errors.extend(query_errors)
    errors.extend(body_errors)
    errors.extend(headers_errors)

    response_raw = parsed.get("response")
    if response_raw is None:
        response_schema = {}
    elif isinstance(response_raw, dict):
        response_schema = response_raw
    else:
        response_schema = {}
        errors.append("response must be an object")

    return ApiMonitorToolContract(
        valid=not errors,
        yaml_definition=yaml_definition,
        name=name,
        description=description,
        method=method,
        url=url,
        input_schema=input_schema,
        response_schema=response_schema,
        path_mapping=path_mapping,
        query_mapping=query_mapping,
        body_mapping=body_mapping,
        header_mapping=header_mapping,
        validation_errors=errors,
        raw_definition=parsed,
    )


def render_template_value(value: Any, arguments: dict[str, Any] | Any) -> Any:
    if isinstance(value, str):
        single_match = SINGLE_TEMPLATE_RE.match(value)
        if single_match:
            return arguments.get(single_match.group(1))

        def replace(match: re.Match[str]) -> str:
            argument_value = arguments.get(match.group(1), "")
            return "" if argument_value is None else str(argument_value)

        return TEMPLATE_RE.sub(replace, value)
    if isinstance(value, dict):
        return render_mapping(value, arguments)
    if isinstance(value, list):
        return [render_template_value(item, arguments) for item in value]
    return value


def render_mapping(mapping: dict[str, Any] | Any, arguments: dict[str, Any] | Any) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    return {key: render_template_value(value, arguments) for key, value in mapping.items()}


def sanitize_headers(headers: dict[str, Any] | Any) -> dict[str, Any]:
    return sanitize_preview_mapping(headers)


def sanitize_preview_mapping(value: dict[str, Any] | list[Any] | Any) -> dict[str, Any] | list[Any] | Any:
    return _sanitize_preview_value(value)


def sanitize_preview_url(
    url: str,
    *,
    url_template: str = "",
    arguments: dict[str, Any] | None = None,
) -> str:
    if not url:
        return ""

    parsed = urlsplit(url)
    sanitized_path = _sanitize_preview_path(parsed.path, url_template, arguments or {})
    sanitized_query = _sanitize_preview_query_string(parsed.query)
    sanitized_fragment = _sanitize_preview_fragment(parsed.fragment)
    return urlunsplit((parsed.scheme, parsed.netloc, sanitized_path, sanitized_query, sanitized_fragment))


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _extract_template_variables(value: str) -> list[str]:
    seen: set[str] = set()
    variables: list[str] = []
    for variable in TEMPLATE_RE.findall(value):
        if variable not in seen:
            seen.add(variable)
            variables.append(variable)
    return variables


def _is_sensitive_header_name(name: str) -> bool:
    return _is_sensitive_preview_key_name(name)


def _sanitize_preview_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***" if _is_sensitive_preview_key_name(key) else _sanitize_preview_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_preview_value(item) for item in value]
    return value


def _is_sensitive_preview_key_name(name: str) -> bool:
    normalized = str(name).strip().lower()
    if not normalized:
        return False
    if normalized in SENSITIVE_PREVIEW_KEY_NAMES or normalized in SENSITIVE_HEADER_NAMES:
        return True
    camel_spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name).strip())
    parts = re.split(r"[^a-z0-9]+", camel_spaced.lower())
    return any(part in SENSITIVE_PREVIEW_KEY_NAMES or part in SENSITIVE_HEADER_NAMES for part in parts if part)


def _sanitize_preview_query_string(query: str) -> str:
    if not query:
        return ""
    query_pairs = parse_qsl(query, keep_blank_values=True)
    return urlencode(
        [
            (key, "***" if _is_sensitive_preview_key_name(key) else value)
            for key, value in query_pairs
        ],
        doseq=True,
        safe="*",
    )


def _sanitize_preview_fragment(fragment: str) -> str:
    if not fragment:
        return ""
    if "=" not in fragment and "&" not in fragment:
        return fragment
    return _sanitize_preview_query_string(fragment)


def _sanitize_preview_path(path: str, url_template: str, arguments: dict[str, Any]) -> str:
    if not path:
        return ""

    template_segments = urlsplit(url_template).path.split("/") if url_template else []
    path_segments = path.split("/")
    sanitized_segments: list[str] = []
    mask_next_segment = False

    for index, segment in enumerate(path_segments):
        if not segment:
            sanitized_segments.append(segment)
            continue

        template_segment = template_segments[index] if index < len(template_segments) else ""
        sanitized_segment = segment

        if mask_next_segment:
            sanitized_segment = "***"
            mask_next_segment = False
        elif template_segment:
            single_match = SINGLE_TEMPLATE_RE.match(template_segment)
            if single_match and _is_sensitive_preview_key_name(single_match.group(1)):
                sanitized_segment = "***"
            elif _is_sensitive_preview_key_name(template_segment):
                mask_next_segment = True
        elif "=" in segment:
            key, value = segment.split("=", 1)
            if _is_sensitive_preview_key_name(key):
                sanitized_segment = f"{key}=***"
        elif _is_sensitive_preview_key_name(segment):
            mask_next_segment = True

        sanitized_segments.append(sanitized_segment)

    return "/".join(sanitized_segments)


def _validate_mapping_section(
    prefix: str,
    section_value: Any,
    allowed_properties: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    if section_value is None:
        return {}, []
    if not isinstance(section_value, dict):
        return {}, [f"{prefix} must be an object"]
    return section_value, _validate_mapping_variables(prefix, section_value, allowed_properties)


def _validate_mapping_variables(
    prefix: str,
    mapping: dict[str, Any],
    allowed_properties: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    for key, value in mapping.items():
        current_path = f"{prefix}.{key}"
        if isinstance(value, str):
            for variable in _extract_template_variables(value):
                if variable not in allowed_properties:
                    errors.append(f"{current_path} references unknown parameter '{variable}'")
        elif isinstance(value, dict):
            errors.extend(_validate_mapping_variables(current_path, value, allowed_properties))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                item_path = f"{current_path}[{index}]"
                if isinstance(item, str):
                    for variable in _extract_template_variables(item):
                        if variable not in allowed_properties:
                            errors.append(f"{item_path} references unknown parameter '{variable}'")
                elif isinstance(item, dict):
                    errors.extend(_validate_mapping_variables(item_path, item, allowed_properties))
    return errors
