from pathlib import Path

import pytest

from backend import config as backend_config
from backend.deepagent.mcp_config_loader import load_system_mcp_servers
from backend.mcp.models import McpToolPolicy


def test_load_system_mcp_servers_reads_yaml(tmp_path, monkeypatch):
    config_file = tmp_path / "mcp_servers.yaml"
    config_file.write_text(
        """
servers:
  - id: pubmed
    name: PubMed MCP
    transport: streamable_http
    enabled: true
    default_enabled: true
    url: http://mcp-pubmed:8080/mcp
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("STORAGE_BACKEND", "docker")

    servers = load_system_mcp_servers(config_file)

    assert len(servers) == 1
    assert servers[0].id == "pubmed"
    assert servers[0].transport == "streamable_http"
    assert servers[0].scope == "system"


def test_resolve_system_mcp_config_path_defaults_to_repo_root(monkeypatch):
    monkeypatch.delenv("SYSTEM_MCP_CONFIG_PATH", raising=False)

    expected = Path(backend_config.__file__).resolve().parents[2] / "mcp_servers.yaml"

    assert backend_config._resolve_system_mcp_config_path() == str(expected)


def test_load_system_mcp_servers_returns_empty_list_for_missing_file(monkeypatch, tmp_path):
    missing_file = tmp_path / "missing-mcp.yaml"
    monkeypatch.setattr("backend.config.settings.system_mcp_config_path", str(missing_file))

    assert load_system_mcp_servers() == []


def test_load_system_mcp_servers_maps_tool_policy_and_forces_system_scope(tmp_path, monkeypatch):
    config_file = tmp_path / "mcp_servers.yaml"
    config_file.write_text(
        """
servers:
  - id: pubmed
    name: PubMed MCP
    scope: user
    transport: streamable_http
    enabled: true
    default_enabled: true
    allowed_tools: ["search", "lookup"]
    blocked_tools: ["delete"]
    url: http://mcp-pubmed:8080/mcp
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("STORAGE_BACKEND", "docker")

    servers = load_system_mcp_servers(config_file)

    assert servers[0].scope == "system"
    assert servers[0].tool_policy == McpToolPolicy(
        allowed_tools=["search", "lookup"],
        blocked_tools=["delete"],
    )


def test_load_system_mcp_servers_rejects_stdio_outside_local(tmp_path, monkeypatch):
    config_file = tmp_path / "mcp_servers.yaml"
    config_file.write_text(
        """
servers:
  - id: local-python
    name: Local Python MCP
    transport: stdio
    enabled: true
    default_enabled: false
    command: python
    args: ["-m", "demo_mcp"]
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("STORAGE_BACKEND", "docker")

    with pytest.raises(ValueError, match="stdio MCP is only allowed in local mode"):
        load_system_mcp_servers(config_file)


def test_load_system_mcp_servers_allows_stdio_in_local_mode(tmp_path, monkeypatch):
    config_file = tmp_path / "mcp_servers.yaml"
    config_file.write_text(
        """
servers:
  - id: local-python
    name: Local Python MCP
    transport: stdio
    enabled: true
    default_enabled: false
    command: python
    args: ["-m", "demo_mcp"]
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("STORAGE_BACKEND", "local")

    servers = load_system_mcp_servers(config_file)

    assert len(servers) == 1
    assert servers[0].transport == "stdio"


def test_load_system_mcp_servers_accepts_http_header_aliases(tmp_path, monkeypatch):
    config_file = tmp_path / "mcp_servers.yaml"
    config_file.write_text(
        """
servers:
  - id: pubmed
    name: PubMed MCP
    transport: streamable_http
    url: http://mcp-pubmed:8080/mcp
    http_headers:
      Authorization: Bearer token-1
  - id: arxiv
    name: Arxiv MCP
    transport: sse
    url: http://mcp-arxiv:8080/sse
    http_header:
      X-Api-Key: token-2
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("STORAGE_BACKEND", "docker")

    servers = load_system_mcp_servers(config_file)

    assert servers[0].headers == {"Authorization": "Bearer token-1"}
    assert servers[1].headers == {"X-Api-Key": "token-2"}
