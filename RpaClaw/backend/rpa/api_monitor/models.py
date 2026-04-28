"""Data models for the API Monitor feature."""

import uuid
from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


def _gen_id() -> str:
    return str(uuid.uuid4())


ConfidenceLevel = Literal["high", "medium", "low"]


# ── Captured request/response ────────────────────────────────────────


class CapturedRequest(BaseModel):
    request_id: str
    url: str
    method: str
    headers: Dict[str, str]
    body: Optional[str] = None
    content_type: Optional[str] = None
    timestamp: datetime
    resource_type: str  # "xhr" or "fetch"


class CapturedResponse(BaseModel):
    status: int
    status_text: str
    headers: Dict[str, str]
    body: Optional[str] = None
    content_type: Optional[str] = None
    timestamp: datetime


class CapturedApiCall(BaseModel):
    id: str = Field(default_factory=_gen_id)
    request: CapturedRequest
    response: Optional[CapturedResponse] = None
    trigger_element: Optional[Dict] = None
    url_pattern: Optional[str] = None
    duration_ms: Optional[float] = None
    source_evidence: Dict = Field(default_factory=dict)


# ── Tool definition ──────────────────────────────────────────────────


class ApiToolDefinition(BaseModel):
    id: str = Field(default_factory=_gen_id)
    session_id: str
    name: str
    description: str
    method: str
    url_pattern: str
    headers_schema: Optional[Dict] = None
    request_body_schema: Optional[Dict] = None
    response_body_schema: Optional[Dict] = None
    trigger_locator: Optional[Dict] = None
    yaml_definition: str
    source_calls: List[str] = Field(default_factory=list)
    source: str = "auto"  # "auto" or "manual"
    confidence: ConfidenceLevel = "medium"
    score: int = 0
    selected: bool = False
    confidence_reasons: List[str] = Field(default_factory=list)
    source_evidence: Dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ── Session ──────────────────────────────────────────────────────────


class ApiMonitorSession(BaseModel):
    id: str = Field(default_factory=_gen_id)
    user_id: str
    sandbox_session_id: str
    status: str = "idle"  # idle, analyzing, recording, stopped
    target_url: Optional[str] = None
    captured_calls: List[CapturedApiCall] = Field(default_factory=list)
    tool_definitions: List[ApiToolDefinition] = Field(default_factory=list)
    active_tab_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ── Request schemas for API endpoints ────────────────────────────────


class StartSessionRequest(BaseModel):
    url: str


class NavigateRequest(BaseModel):
    url: str


class UpdateToolRequest(BaseModel):
    yaml_definition: str


class TokenFlowSelection(BaseModel):
    id: str
    enabled: bool = True


class ApiMonitorAuthConfigRequest(BaseModel):
    credential_type: str = "placeholder"
    credential_id: str = ""
    login_url: str = ""
    token_flows: List[TokenFlowSelection] = Field(default_factory=list)


class PublishMcpRequest(BaseModel):
    mcp_name: str
    description: str = ""
    confirm_overwrite: bool = False
    api_monitor_auth: Optional[ApiMonitorAuthConfigRequest] = None


class UpdateToolSelectionRequest(BaseModel):
    selected: bool
