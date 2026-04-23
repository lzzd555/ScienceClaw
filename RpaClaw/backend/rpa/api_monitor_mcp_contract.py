from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

import yaml


TOOL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TEMPLATE_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


@dataclass
class ApiMonitorToolContract:
    valid: bool
    name: str = ""
    description: str = ""
    method: str = ""
    url: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    response_schema: dict[str, Any] = field(default_factory=dict)
    path_mapping: dict[str, Any] = field(default_factory=dict)
    query_mapping: dict[str, Any] = field(default_factory=dict)
    body_mapping: dict[str, Any] = field(default_factory=dict)
    headers_mapping: dict[str, Any] = field(default_factory=dict)
    validation_errors: list[str] = field(default_factory=list)
    raw_definition: dict[str, Any] = field(default_factory=dict)

    def to_document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "method": self.method,
            "url": self.url,
            "input_schema": self.input_schema,
            "response_schema": self.response_schema,
            "path_mapping": self.path_mapping,
            "query_mapping": self.query_mapping,
            "body_mapping": self.body_mapping,
            "headers_mapping": self.headers_mapping,
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
            validation_errors=[f"YAML parse error: {exc}"],
        )

    if not isinstance(parsed, dict):
        parsed = {}

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

    parameters = _as_dict(parsed.get("parameters"))
    input_schema = parameters
    properties = _as_dict(parameters.get("properties")) if parameters else {}
    if not parameters:
        errors.append("parameters is required")
    elif _string_value(parameters.get("type")) != "object":
        errors.append("parameters.type must be object")
    if parameters and not isinstance(parameters.get("properties"), dict):
        errors.append("parameters.properties must be an object")

    request = _as_dict(parsed.get("request"))
    path_mapping = _as_dict(request.get("path")) if request else {}
    query_mapping = _as_dict(request.get("query")) if request else {}
    body_mapping = _as_dict(request.get("body")) if request else {}
    headers_mapping = _as_dict(request.get("headers")) if request else {}

    for section_name, mapping in (
        ("request.path", path_mapping),
        ("request.query", query_mapping),
        ("request.body", body_mapping),
        ("request.headers", headers_mapping),
    ):
        errors.extend(_validate_mapping_variables(section_name, mapping, properties))

    response_schema = _as_dict(parsed.get("response"))

    return ApiMonitorToolContract(
        valid=not errors,
        name=name,
        description=description,
        method=method,
        url=url,
        input_schema=input_schema,
        response_schema=response_schema,
        path_mapping=path_mapping,
        query_mapping=query_mapping,
        body_mapping=body_mapping,
        headers_mapping=headers_mapping,
        validation_errors=errors,
        raw_definition=parsed,
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _extract_template_variables(value: str) -> set[str]:
    return set(TEMPLATE_RE.findall(value))


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
