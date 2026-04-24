from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field


McpTransport = Literal["stdio", "streamable_http", "sse", "api_monitor"]
McpScope = Literal["system", "user"]
McpSessionMode = Literal["inherit", "enabled", "disabled"]


class McpCredentialRef(BaseModel):
    alias: str
    credential_id: str


class McpCredentialBinding(BaseModel):
    credential_id: str = ""
    credentials: List[McpCredentialRef] = Field(default_factory=list)
    headers: Dict[str, str] = Field(default_factory=dict)
    env: Dict[str, str] = Field(default_factory=dict)
    query: Dict[str, str] = Field(default_factory=dict)


class McpToolPolicy(BaseModel):
    allowed_tools: List[str] = Field(default_factory=list)
    blocked_tools: List[str] = Field(default_factory=list)


class McpServerDefinition(BaseModel):
    id: str
    name: str
    description: str = ""
    transport: McpTransport
    scope: McpScope = "system"
    enabled: bool = True
    default_enabled: bool = False
    url: str = ""
    command: str = ""
    args: List[str] = Field(default_factory=list)
    cwd: str = ""
    headers: Dict[str, str] = Field(default_factory=dict)
    env: Dict[str, str] = Field(default_factory=dict)
    timeout_ms: int = 20000
    credential_ref: str = ""
    credential_binding: McpCredentialBinding = Field(default_factory=McpCredentialBinding)
    tool_policy: McpToolPolicy = Field(default_factory=McpToolPolicy)


class UserMcpServerCreate(BaseModel):
    name: str
    description: str = ""
    transport: McpTransport
    endpoint_config: Dict[str, Any] = Field(default_factory=dict)
    credential_binding: McpCredentialBinding = Field(default_factory=McpCredentialBinding)
    tool_policy: McpToolPolicy = Field(default_factory=McpToolPolicy)
    default_enabled: bool = False


class UserMcpServerUpdate(UserMcpServerCreate):
    enabled: bool = True


class SessionMcpBindingUpdate(BaseModel):
    mode: McpSessionMode
