from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

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
    if not isinstance(headers, dict):
        return {}
    return {
        key: "***" if _is_sensitive_header_name(key) else value
        for key, value in headers.items()
    }


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
    normalized = str(name).strip().lower()
    return normalized in SENSITIVE_HEADER_NAMES or normalized.endswith("-token")


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
