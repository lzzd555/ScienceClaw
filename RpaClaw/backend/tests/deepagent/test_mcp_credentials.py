import asyncio

import pytest

from backend.deepagent.mcp_credentials import (
    McpCredentialResolutionError,
    append_query_params,
    resolve_mcp_credential_config,
)
from backend.mcp.models import McpCredentialBinding


class FakeVault:
    def __init__(self, values):
        self.values = values
        self.calls = []

    async def resolve_credential_values(self, user_id: str, cred_id: str):
        self.calls.append((user_id, cred_id))
        return self.values.get(cred_id)


def test_resolve_mcp_credential_config_supports_multiple_credential_aliases():
    vault = FakeVault(
        {
            "cred-github": {
                "username": "octo",
                "password": "ghp_secret",
                "domain": "github.com",
            },
            "cred-sentry": {
                "username": "sentry-user",
                "password": "sentry_secret",
                "domain": "sentry.io",
            },
        }
    )
    binding = McpCredentialBinding(
        credentials=[
            {"alias": "github", "credential_id": "cred-github"},
            {"alias": "sentry", "credential_id": "cred-sentry"},
        ],
        headers={
            "Authorization": "Bearer {{ github.password }}",
            "X-Sentry-Token": "{{ sentry.password }}",
        },
        env={
            "GITHUB_USER": "{{ github.username }}",
            "GITHUB_HOST": "{{ github.domain }}",
        },
        query={"sentry_token": "{{ sentry.password }}"},
    )

    resolved = asyncio.run(resolve_mcp_credential_config("user-1", binding, vault=vault))

    assert resolved.headers == {
        "Authorization": "Bearer ghp_secret",
        "X-Sentry-Token": "sentry_secret",
    }
    assert resolved.env == {
        "GITHUB_USER": "octo",
        "GITHUB_HOST": "github.com",
    }
    assert resolved.query == {"sentry_token": "sentry_secret"}
    assert vault.calls == [("user-1", "cred-github"), ("user-1", "cred-sentry")]


def test_resolve_mcp_credential_config_keeps_legacy_single_credential_alias():
    vault = FakeVault(
        {
            "cred-legacy": {
                "username": "legacy-user",
                "password": "legacy-secret",
                "domain": "legacy.example",
            },
        }
    )
    binding = McpCredentialBinding(
        credential_id="cred-legacy",
        headers={"Authorization": "Bearer {{ credential.password }}"},
        env={"MCP_USERNAME": "{{ credential.username }}"},
    )

    resolved = asyncio.run(resolve_mcp_credential_config("user-1", binding, vault=vault))

    assert resolved.headers == {"Authorization": "Bearer legacy-secret"}
    assert resolved.env == {"MCP_USERNAME": "legacy-user"}


def test_resolve_mcp_credential_config_rejects_unknown_template_alias():
    binding = McpCredentialBinding(
        credentials=[{"alias": "github", "credential_id": "cred-github"}],
        headers={"Authorization": "Bearer {{ missing.password }}"},
    )

    with pytest.raises(McpCredentialResolutionError, match="missing"):
        asyncio.run(resolve_mcp_credential_config("user-1", binding, vault=FakeVault({})))


def test_resolve_mcp_credential_config_rejects_missing_credential():
    binding = McpCredentialBinding(
        credentials=[{"alias": "github", "credential_id": "cred-missing"}],
        headers={"Authorization": "Bearer {{ github.password }}"},
    )

    with pytest.raises(McpCredentialResolutionError, match="github"):
        asyncio.run(resolve_mcp_credential_config("user-1", binding, vault=FakeVault({})))


def test_append_query_params_merges_and_overrides_existing_query():
    assert append_query_params(
        "https://mcp.example.com/mcp?old=1&api_key=old",
        {"api_key": "new secret", "space": "a b"},
    ) == "https://mcp.example.com/mcp?old=1&api_key=new+secret&space=a+b"
