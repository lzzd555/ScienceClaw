from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from backend.credential.vault import get_vault
from backend.mcp.models import McpCredentialBinding


_TEMPLATE_RE = re.compile(r"{{\s*([A-Za-z_][\w-]*)\.(password|username|domain)\s*}}")


class CredentialValueResolver(Protocol):
    async def resolve_credential_values(self, user_id: str, cred_id: str) -> Mapping[str, str] | None: ...


class McpCredentialResolutionError(ValueError):
    """Raised when an MCP credential template cannot be resolved safely."""


@dataclass(frozen=True)
class ResolvedMcpCredentialConfig:
    headers: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    query: dict[str, str] = field(default_factory=dict)


def _credential_aliases(binding: McpCredentialBinding) -> dict[str, str]:
    aliases: dict[str, str] = {}
    if binding.credential_id:
        aliases["credential"] = binding.credential_id
    for item in binding.credentials:
        alias = item.alias.strip()
        cred_id = item.credential_id.strip()
        if alias and cred_id:
            aliases[alias] = cred_id
    return aliases


def _referenced_aliases(binding: McpCredentialBinding) -> list[str]:
    aliases: list[str] = []
    for values in (binding.headers, binding.env, binding.query):
        for value in values.values():
            for match in _TEMPLATE_RE.finditer(str(value)):
                alias = match.group(1)
                if alias not in aliases:
                    aliases.append(alias)
    return aliases


async def _load_alias_values(
    user_id: str,
    aliases: Mapping[str, str],
    vault: CredentialValueResolver,
) -> dict[str, Mapping[str, str]]:
    values: dict[str, Mapping[str, str]] = {}
    for alias, cred_id in aliases.items():
        credential_values = await vault.resolve_credential_values(user_id, cred_id)
        if credential_values is None:
            raise McpCredentialResolutionError(f"MCP credential alias '{alias}' references a missing credential")
        values[alias] = credential_values
    return values


def _render_template(value: str, alias_values: Mapping[str, Mapping[str, str]]) -> str:
    def replace(match: re.Match[str]) -> str:
        alias = match.group(1)
        field_name = match.group(2)
        credential_values = alias_values.get(alias)
        if credential_values is None:
            raise McpCredentialResolutionError(f"MCP credential template references unknown alias '{alias}'")
        return str(credential_values.get(field_name) or "")

    return _TEMPLATE_RE.sub(replace, value)


def _render_map(
    values: Mapping[str, str],
    alias_values: Mapping[str, Mapping[str, str]],
) -> dict[str, str]:
    return {
        str(key): _render_template(str(value), alias_values)
        for key, value in values.items()
        if str(key).strip()
    }


async def resolve_mcp_credential_config(
    user_id: str,
    binding: McpCredentialBinding | Mapping[str, object] | None,
    *,
    vault: CredentialValueResolver | None = None,
) -> ResolvedMcpCredentialConfig:
    if binding is None:
        binding_model = McpCredentialBinding()
    elif isinstance(binding, McpCredentialBinding):
        binding_model = binding
    else:
        binding_model = McpCredentialBinding(**dict(binding))

    aliases = _credential_aliases(binding_model)
    referenced_aliases = _referenced_aliases(binding_model)
    unknown_aliases = [alias for alias in referenced_aliases if alias not in aliases]
    if unknown_aliases:
        unknown = unknown_aliases[0]
        raise McpCredentialResolutionError(f"MCP credential template references unknown alias '{unknown}'")

    aliases_to_load = {alias: aliases[alias] for alias in referenced_aliases}
    if not aliases_to_load:
        alias_values: dict[str, Mapping[str, str]] = {}
    else:
        alias_values = await _load_alias_values(user_id, aliases_to_load, vault or get_vault())

    return ResolvedMcpCredentialConfig(
        headers=_render_map(binding_model.headers, alias_values),
        env=_render_map(binding_model.env, alias_values),
        query=_render_map(binding_model.query, alias_values),
    )


def append_query_params(url: str, query: Mapping[str, str]) -> str:
    if not query:
        return url

    parts = urlsplit(url)
    merged = dict(parse_qsl(parts.query, keep_blank_values=True))
    merged.update({str(key): str(value) for key, value in query.items()})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(merged), parts.fragment))
