from __future__ import annotations

from types import SimpleNamespace

from backend.deepagent import agent
from backend.deepagent.sse_protocol import SSEProtocolManager, ToolCategory


def test_collect_tools_includes_mcp_tools_and_respects_blocklist_and_dedupe(monkeypatch):
    static_tool = SimpleNamespace(name="shared", description="Static shared tool")
    external_tool = SimpleNamespace(name="external", description="External tool")
    blocked_mcp_tool = SimpleNamespace(
        name="mcp__pubmed__search",
        description="PubMed search",
        metadata={"mcp": {"source": "mcp"}},
    )
    duplicate_mcp_tool = SimpleNamespace(
        name="shared",
        description="Duplicate shared tool",
        metadata={"mcp": {"source": "mcp"}},
    )
    included_mcp_tool = SimpleNamespace(
        name="mcp__notion__lookup",
        description="Notion lookup",
        metadata={"mcp": {"source": "mcp"}},
    )

    monkeypatch.setattr(agent, "_STATIC_TOOLS", [static_tool])
    monkeypatch.setattr(agent, "reload_external_tools", lambda **kwargs: [external_tool])

    tools = agent._collect_tools(
        blocked_tools={"mcp__pubmed__search"},
        mcp_tools=[blocked_mcp_tool, duplicate_mcp_tool, included_mcp_tool],
    )

    assert [tool.name for tool in tools] == ["shared", "external", "mcp__notion__lookup"]


def test_protocol_manager_merges_nested_tool_metadata():
    protocol = SSEProtocolManager()
    protocol.register_tool("mcp__pubmed__search", ToolCategory.EXECUTION, "馃敡", "PubMed search")
    protocol.register_tool_extra_meta("mcp__pubmed__search", {"sandbox": True})
    protocol.merge_tool_extra_meta(
        "mcp__pubmed__search",
        {"mcp": {"source": "mcp", "server_id": "pubmed", "nested": {"tool": "search"}}},
    )

    assert protocol.get_tool_meta("mcp__pubmed__search") == {
        "name": "mcp__pubmed__search",
        "category": "execution",
        "icon": "馃敡",
        "description": "PubMed search",
        "sandbox": True,
        "mcp": {"source": "mcp", "server_id": "pubmed", "nested": {"tool": "search"}},
    }


def test_protocol_manager_replaces_tool_metadata_on_reregistration():
    protocol = SSEProtocolManager()
    protocol.register_tool("mcp__pubmed__search", ToolCategory.EXECUTION, "馃敡", "PubMed search")
    protocol.register_tool_extra_meta(
        "mcp__pubmed__search",
        {
            "sandbox": True,
            "mcp": {
                "source": "legacy",
                "server_id": "old",
                "nested": {"tool": "old-search"},
            },
        },
    )
    protocol.register_tool_extra_meta(
        "mcp__pubmed__search",
        {
            "mcp": {
                "source": "mcp",
                "server_id": "pubmed",
                "nested": {"tool": "search"},
            },
        },
    )

    assert protocol.get_tool_meta("mcp__pubmed__search") == {
        "name": "mcp__pubmed__search",
        "category": "execution",
        "icon": "馃敡",
        "description": "PubMed search",
        "mcp": {"source": "mcp", "server_id": "pubmed", "nested": {"tool": "search"}},
    }


def test_register_tools_in_sse_preserves_nested_mcp_metadata(monkeypatch):
    tool = SimpleNamespace(
        name="mcp__pubmed__search",
        description="PubMed search",
        metadata={"mcp": {"source": "mcp", "server_id": "pubmed", "server_name": "PubMed"}},
    )

    class FakeProtocol:
        def __init__(self) -> None:
            self.registered: dict[str, dict[str, object]] = {}

        def register_tool(self, name: str, category: ToolCategory, icon: str, description: str) -> None:
            self.registered[name] = {
                "name": name,
                "category": category.value,
                "icon": icon,
                "description": description,
            }

        def register_tool_extra_meta(self, name: str, extra_meta: dict[str, object]) -> None:
            self.registered[name] = {**self.registered.get(name, {}), **extra_meta}

        def clear_tool_extra_meta(self, name: str) -> None:
            self.registered.pop(name, None)

    fake_protocol = FakeProtocol()
    monkeypatch.setattr("backend.deepagent.sse_protocol.get_protocol_manager", lambda: fake_protocol)

    agent._register_external_tools_in_sse([tool])

    registered = fake_protocol.registered["mcp__pubmed__search"]
    assert registered["name"] == "mcp__pubmed__search"
    assert registered["category"] == "execution"
    assert registered["description"] == "PubMed search"
    assert registered["mcp"] == {"source": "mcp", "server_id": "pubmed", "server_name": "PubMed"}
