import asyncio

from backend.deepagent.mcp_registry import build_effective_mcp_servers
from backend.mcp.models import McpServerDefinition


def test_build_effective_mcp_servers_applies_defaults_and_session_overrides(monkeypatch):
    default_enabled_system_server = McpServerDefinition(
        id="pubmed",
        name="PubMed",
        transport="streamable_http",
        scope="system",
        enabled=True,
        default_enabled=True,
        url="http://pubmed/mcp",
    )
    session_enabled_user_server = McpServerDefinition(
        id="private-notion",
        name="Private Notion",
        transport="sse",
        scope="user",
        enabled=True,
        default_enabled=False,
        url="http://notion/mcp",
    )

    monkeypatch.setattr(
        "backend.deepagent.mcp_registry.load_system_mcp_servers",
        lambda: [default_enabled_system_server],
    )

    async def fake_user_servers(user_id: str):
        assert user_id == "u1"
        return [session_enabled_user_server]

    async def fake_bindings(session_id: str, user_id: str):
        return {"user:private-notion": "enabled"}

    monkeypatch.setattr("backend.deepagent.mcp_registry._load_user_mcp_servers", fake_user_servers)
    monkeypatch.setattr("backend.deepagent.mcp_registry._load_session_mcp_bindings", fake_bindings)

    servers = asyncio.run(build_effective_mcp_servers("s1", "u1"))

    assert [server.id for server in servers] == ["pubmed", "private-notion"]
