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
    generation_candidate_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ── Generation candidate ──────────────────────────────────────────────


GenerationStatus = Literal[
    "pending",
    "running",
    "generated",
    "failed",
    "rate_limited",
    "stale",
]


class ApiToolGenerationCandidate(BaseModel):
    id: str = Field(default_factory=_gen_id)
    session_id: str
    dedup_key: str
    method: str
    url_pattern: str
    source_call_ids: List[str] = Field(default_factory=list)
    sample_call_ids: List[str] = Field(default_factory=list)
    status: GenerationStatus = "pending"
    tool_id: Optional[str] = None
    error: str = ""
    retry_after: Optional[datetime] = None
    attempts: int = 0
    capture_dom_context: Dict = Field(default_factory=dict)
    capture_page_url: str = ""
    capture_title: str = ""
    capture_dom_digest: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ── Directed analysis trace ──────────────────────────────────────────


class DirectedObservation(BaseModel):
    url: str = ""
    title: str = ""
    dom_digest: str = ""
    compact_snapshot_summary: Dict = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=datetime.now)


class DirectedDecisionSnapshot(BaseModel):
    goal_status: str = "continue"
    summary: str = ""
    expected_change: str = ""
    done_reason: str = ""
    action: Optional[Dict] = None
    risk: str = "safe"


class DirectedExecutionSnapshot(BaseModel):
    result: str
    error: str = ""
    duration_ms: Optional[int] = None
    url_changed: bool = False
    dom_changed: bool = False


class DirectedAnalysisTrace(BaseModel):
    id: str = Field(default_factory=_gen_id)
    step: int
    instruction: str
    mode: str
    before: DirectedObservation
    decision: Optional[DirectedDecisionSnapshot] = None
    action_fingerprint: Optional[str] = None
    execution: Optional[DirectedExecutionSnapshot] = None
    after: Optional[DirectedObservation] = None
    captured_call_ids: List[str] = Field(default_factory=list)
    retry_advice: Dict = Field(default_factory=dict)
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
    evidence_calls: List[CapturedApiCall] = Field(default_factory=list)
    tool_definitions: List[ApiToolDefinition] = Field(default_factory=list)
    directed_traces: List[DirectedAnalysisTrace] = Field(default_factory=list)
    generation_candidates: List[ApiToolGenerationCandidate] = Field(default_factory=list)
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


class ManualTokenFlowRequest(BaseModel):
    id: str
    name: str
    enabled: bool = True
    producer: Dict
    consumers: List[Dict]
    refresh_on_status: List[int] = Field(default_factory=lambda: [401, 403, 419])


class ApiMonitorAuthConfigRequest(BaseModel):
    credential_type: str = "placeholder"
    credential_id: str = ""
    login_url: str = ""
    token_flows: List[TokenFlowSelection] = Field(default_factory=list)
    manual_token_flows: List[ManualTokenFlowRequest] = Field(default_factory=list)


class PublishMcpRequest(BaseModel):
    mcp_name: str
    description: str = ""
    confirm_overwrite: bool = False
    api_monitor_auth: Optional[ApiMonitorAuthConfigRequest] = None


class UpdateToolSelectionRequest(BaseModel):
    selected: bool


class AnalyzeSessionRequest(BaseModel):
    mode: str = "free"
    instruction: str = ""
