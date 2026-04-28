import { apiClient, createSSEConnection } from '@/api/client'
import type { ApiMonitorAuthConfigPublish } from '@/api/mcp'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ApiToolConfidence = 'high' | 'medium' | 'low'

export interface CapturedRequest {
  request_id: string
  url: string
  method: string
  headers: Record<string, string>
  body?: string
  content_type?: string
  timestamp: string
  resource_type: string
}

export interface CapturedResponse {
  status: number
  status_text: string
  headers: Record<string, string>
  body?: string
  content_type?: string
  timestamp: string
}

export interface CapturedApiCall {
  id: string
  request: CapturedRequest
  response?: CapturedResponse
  trigger_element?: string
  url_pattern?: string
  duration_ms?: number
}

export interface ApiToolDefinition {
  id: string
  session_id: string
  name: string
  description: string
  method: string
  url_pattern: string
  yaml_definition: string
  source_calls: string[]
  source: 'auto' | 'manual'
  confidence: ApiToolConfidence
  score: number
  selected: boolean
  confidence_reasons: string[]
  source_evidence: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface ApiMonitorSession {
  id: string
  user_id: string
  sandbox_session_id: string
  status: 'idle' | 'analyzing' | 'recording' | 'stopped'
  target_url?: string
  captured_calls: CapturedApiCall[]
  tool_definitions: ApiToolDefinition[]
  created_at: string
  updated_at: string
}

export interface TabInfo {
  id: string
  url: string
  title: string
}

export interface AnalyzeEvent {
  event: string
  data: unknown
}

export type AnalysisModeKey = 'free' | 'safe_directed' | 'directed'

export interface AnalyzeSessionPayload {
  mode?: AnalysisModeKey | string
  instruction?: string
}

export type ApiMonitorCredentialType = 'placeholder' | 'test'

export interface ApiMonitorAuthConfig {
  credential_type: ApiMonitorCredentialType
  credential_id: string
  login_url?: string
}

export interface ApiMonitorAuthProfileHeader {
  name: string
  display_name: string
  occurrences: number
  tools: string[]
  signals: string[]
  masked_example: string
}

export interface ApiMonitorAuthProfile {
  header_count: number
  sensitive_header_count: number
  headers: ApiMonitorAuthProfileHeader[]
  recommended_credential_type: ApiMonitorCredentialType
}

export interface TokenFlowProfile {
  id: string
  name: string
  producer_summary: string
  consumer_summaries: string[]
  confidence: 'high' | 'medium' | 'low'
  enabled_by_default: boolean
  reasons: string[]
  sample_count?: number
  source_call_ids?: string[]
}

export interface TokenFlowProfileResponse {
  flow_count: number
  flows: TokenFlowProfile[]
}

export interface TokenFlowSelection {
  id: string
  enabled: boolean
}

export interface ApiMonitorManualTokenFlow {
  id: string
  name: string
  enabled?: boolean
  producer: {
    request: {
      method: string
      url: string
      headers?: Record<string, string>
      query?: Record<string, string>
      body?: unknown
      content_type?: string
    }
    extract: Array<{ name: string; from: string; path: string; secret?: boolean }>
  }
  consumers: Array<{
    method: string
    url: string
    inject: {
      headers?: Record<string, string>
      query?: Record<string, string>
      body?: Record<string, string>
    }
  }>
  refresh_on_status?: number[]
}

export interface PublishMcpPayload {
  mcp_name: string
  description: string
  confirm_overwrite: boolean
  api_monitor_auth?: ApiMonitorAuthConfigPublish
}

export interface PublishMcpResult {
  saved: boolean
  server_id: string
  tool_count: number
  overwritten: boolean
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

/**
 * Start a new API monitor session.
 */
export async function startSession(url: string): Promise<ApiMonitorSession> {
  const response = await apiClient.post('/api-monitor/session/start', { url })
  return response.data.session
}

/**
 * Get an existing API monitor session by ID.
 */
export async function getSession(sessionId: string): Promise<ApiMonitorSession> {
  const response = await apiClient.get(`/api-monitor/session/${sessionId}`)
  return response.data.session
}

/**
 * Stop an API monitor session.
 */
export async function stopSession(sessionId: string): Promise<void> {
  await apiClient.post(`/api-monitor/session/${sessionId}/stop`)
}

/**
 * Navigate the monitored browser to a new URL.
 */
export async function navigateSession(sessionId: string, url: string): Promise<void> {
  await apiClient.post(`/api-monitor/session/${sessionId}/navigate`, { url })
}

/**
 * List open browser tabs for the session.
 */
export async function listTabs(sessionId: string): Promise<TabInfo[]> {
  const response = await apiClient.get(`/api-monitor/session/${sessionId}/tabs`)
  return response.data.tabs
}

/**
 * Start SSE-based analysis of captured API calls.
 * Returns a cleanup function to abort the connection.
 */
export function analyzeSession(
  sessionId: string,
  onMessage: (evt: AnalyzeEvent) => void,
  payload: AnalyzeSessionPayload = {},
): () => void {
  // createSSEConnection is async but returns the cleanup fn via promise;
  // we store it so the caller can still get a synchronous cleanup handle.
  let cleanup: (() => void) | null = null
  const body = {
    mode: payload.mode || 'free',
    instruction: payload.instruction || '',
  }

  createSSEConnection<unknown>(
    `/api-monitor/session/${sessionId}/analyze`,
    { method: 'POST', body },
    {
      onMessage({ event, data }) {
        onMessage({ event, data })
      },
    },
  ).then((fn) => {
    cleanup = fn
  })

  return () => {
    cleanup?.()
  }
}

/**
 * Start recording API calls in the session.
 */
export async function startRecording(sessionId: string): Promise<void> {
  await apiClient.post(`/api-monitor/session/${sessionId}/record/start`)
}

/**
 * Stop recording and return the generated tool definitions.
 */
export async function stopRecording(sessionId: string): Promise<ApiToolDefinition[]> {
  const response = await apiClient.post(`/api-monitor/session/${sessionId}/record/stop`)
  return response.data.tools
}

/**
 * List all tool definitions for a session.
 */
export async function listTools(sessionId: string): Promise<ApiToolDefinition[]> {
  const response = await apiClient.get(`/api-monitor/session/${sessionId}/tools`)
  return response.data.tools
}

/**
 * Update a tool definition's YAML.
 */
export async function updateTool(
  sessionId: string,
  toolId: string,
  yamlDefinition: string,
): Promise<ApiToolDefinition> {
  const response = await apiClient.put(
    `/api-monitor/session/${sessionId}/tools/${toolId}`,
    { yaml_definition: yamlDefinition },
  )
  return response.data.tool
}

/**
 * Delete a tool definition.
 */
export async function deleteTool(sessionId: string, toolId: string): Promise<void> {
  await apiClient.delete(`/api-monitor/session/${sessionId}/tools/${toolId}`)
}

/**
 * Toggle tool selection (adopted/not-adopted).
 */
export async function updateToolSelection(
  sessionId: string,
  toolId: string,
  selected: boolean,
): Promise<ApiToolDefinition> {
  const response = await apiClient.patch(
    `/api-monitor/session/${sessionId}/tools/${toolId}/selection`,
    { selected },
  )
  return response.data.tool
}

/**
 * Publish the current session tool definitions as one MCP server.
 */
export async function publishMcpToolBundle(
  sessionId: string,
  payload: PublishMcpPayload,
): Promise<PublishMcpResult> {
  const response = await apiClient.post(`/api-monitor/session/${sessionId}/publish-mcp`, payload)
  return response.data.data
}

/**
 * Get the transient auth profile for a session's captured requests.
 */
export async function getAuthProfile(sessionId: string): Promise<ApiMonitorAuthProfile> {
  const response = await apiClient.get(`/api-monitor/session/${sessionId}/auth-profile`)
  return response.data.profile
}

/**
 * Get the token flow profile for a session's captured traffic.
 */
export async function getTokenFlowProfile(sessionId: string): Promise<TokenFlowProfileResponse> {
  const response = await apiClient.get(`/api-monitor/session/${sessionId}/token-flow-profile`)
  return response.data.profile
}
