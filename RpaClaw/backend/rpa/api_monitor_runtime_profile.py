from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import re
from typing import Any


TEMPLATE_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
SINGLE_TEMPLATE_RE = re.compile(r"^\s*{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}\s*$")


class ApiMonitorRuntimeProfileError(ValueError):
    pass


@dataclass
class ApiMonitorRuntimeProfile:
    base_url: str = ""
    variables: dict[str, Any] = field(default_factory=dict)
    secret_variables: set[str] = field(default_factory=set)
    variable_sources: dict[str, str] = field(default_factory=dict)
    headers: dict[str, Any] = field(default_factory=dict)
    secret_headers: set[str] = field(default_factory=set)
    has_cookies: bool = False

    def set_variable(self, name: str, value: Any, *, secret: bool = True, source: str = "") -> None:
        key = str(name or "").strip()
        if not key:
            return
        if key in self.variables and self.variables[key] != value:
            previous_source = self.variable_sources.get(key, "")
            if previous_source and source and previous_source != source:
                raise ApiMonitorRuntimeProfileError(f"Runtime profile variable conflict: {key}")
        self.variables[key] = value
        if source:
            self.variable_sources[key] = source
        if secret:
            self.secret_variables.add(key)

    def set_header(self, name: str, value: Any, *, secret: bool = True) -> None:
        key = str(name or "").strip()
        if not key:
            return
        self.headers[key] = value
        if secret:
            self.secret_headers.add(key)

    def render_template(self, value: str) -> Any:
        single = SINGLE_TEMPLATE_RE.match(value)
        if single:
            key = single.group(1)
            if key not in self.variables:
                raise ApiMonitorRuntimeProfileError(f"Runtime profile missing variable: {key}")
            return self.variables[key]

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in self.variables:
                raise ApiMonitorRuntimeProfileError(f"Runtime profile missing variable: {key}")
            rendered = self.variables[key]
            return "" if rendered is None else str(rendered)

        return TEMPLATE_RE.sub(replace, value)

    def render_value(self, value: Any) -> Any:
        value = deepcopy(value)
        if isinstance(value, str):
            return self.render_template(value)
        if isinstance(value, dict):
            return {key: self.render_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.render_value(item) for item in value]
        return value

    def apply_injection(
        self,
        inject: dict[str, Any] | Any,
        *,
        headers: dict[str, Any],
        query: dict[str, Any],
        body: dict[str, Any],
    ) -> list[str]:
        if not isinstance(inject, dict):
            return []
        applied: list[str] = []
        for name, template in (inject.get("headers") or {}).items():
            headers[str(name)] = self.render_value(template)
            applied.append(f"headers.{name}")
        for name, template in (inject.get("query") or {}).items():
            query[str(name)] = self.render_value(template)
            applied.append(f"query.{name}")
        for name, template in (inject.get("body") or {}).items():
            body[str(name).removeprefix("$.")] = self.render_value(template)
            applied.append(f"body.{name}")
        return applied

    def preview(self) -> dict[str, Any]:
        return {
            "headers": sorted(self.headers.keys()),
            "variables": sorted(self.variables.keys()),
            "cookies": self.has_cookies,
        }
