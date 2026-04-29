import { apiClient, ApiResponse } from './client';

export type McpTransport = 'stdio' | 'streamable_http' | 'sse' | 'api_monitor';
export type McpWritableTransport = Exclude<McpTransport, 'api_monitor'>;
export type McpSessionMode = 'inherit' | 'enabled' | 'disabled';

export interface McpCredentialRef {
  alias: string;
  credential_id: string;
}

export interface McpCredentialBinding {
  credential_id: string;
  credentials: McpCredentialRef[];
  headers: Record<string, string>;
  env: Record<string, string>;
  query: Record<string, string>;
}

export interface McpToolPolicy {
  allowed_tools: string[];
  blocked_tools: string[];
}

export type ApiMonitorCredentialType = 'placeholder' | 'test';

export interface TokenFlowRuntimeConfig {
  id: string;
  name: string;
  source?: 'auto' | 'manual';
  enabled?: boolean;
  producer?: {
    request: {
      method: string;
      url: string;
      headers?: Record<string, string>;
      query?: Record<string, string>;
      body?: unknown;
      content_type?: string;
    };
    extract: Array<{ name: string; from: string; path: string; secret?: boolean }>;
  };
  consumers?: Array<{
    method: string;
    url: string;
    inject: {
      headers?: Record<string, string>;
      query?: Record<string, string>;
      body?: Record<string, string>;
    };
  }>;
  setup?: Array<{
    method: string;
    url: string;
    extract: { from: string; path: string };
  }>;
  inject?: Record<string, Record<string, string>>;
  applies_to?: Array<{ method: string; url: string }>;
  refresh_on_status: number[];
  confidence: string;
  summary?: {
    producer: string;
    consumers: string[];
    reasons: string[];
    sample_count?: number;
  };
}

export interface ApiMonitorAuthConfig {
  credential_type: ApiMonitorCredentialType;
  credential_id: string;
  login_url?: string;
  token_flows?: TokenFlowRuntimeConfig[];
}

export interface CallerAuthRequirements {
  required: boolean;
  credential_type: ApiMonitorCredentialType;
  accepted_fields: string[];
  notes?: string[];
}

export interface ApiMonitorExternalAccessState {
  enabled: boolean;
  url: string;
  created_at: string;
  last_used_at: string;
  require_caller_credentials: boolean;
  caller_auth_requirements: CallerAuthRequirements;
}

export interface ApiMonitorAuthConfigPublish {
  credential_type: ApiMonitorCredentialType;
  credential_id: string;
  login_url?: string;
  token_flows?: Array<{ id: string; enabled: boolean }>;
  manual_token_flows?: Array<{
    id: string;
    name: string;
    enabled?: boolean;
    producer: Record<string, unknown>;
    consumers: Array<Record<string, unknown>>;
    refresh_on_status?: number[];
  }>;
}

export interface McpEndpointConfig {
  url?: string;
  command?: string;
  args?: string[];
  cwd?: string;
  headers?: Record<string, string>;
  env?: Record<string, string>;
  timeout_ms?: number;
}

export interface McpServerItem {
  id: string;
  server_key: string;
  scope: 'system' | 'user';
  name: string;
  description: string;
  transport: McpTransport;
  source_type?: string;
  enabled: boolean;
  default_enabled: boolean;
  readonly: boolean;
  endpoint_config: McpEndpointConfig;
  credential_binding: McpCredentialBinding;
  api_monitor_auth?: ApiMonitorAuthConfig;
  tool_policy: McpToolPolicy;
  external_access?: ApiMonitorExternalAccessState;
}

export interface SessionMcpServerItem extends McpServerItem {
  session_mode: McpSessionMode;
  effective_enabled: boolean;
}

export interface McpToolDiscoveryItem {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface ApiMonitorMcpToolDetail {
  id: string;
  name: string;
  description: string;
  yaml_definition: string;
  method: string;
  url: string;
  input_schema: Record<string, unknown>;
  path_mapping: Record<string, unknown>;
  query_mapping: Record<string, unknown>;
  body_mapping: Record<string, unknown>;
  header_mapping: Record<string, unknown>;
  response_schema: Record<string, unknown>;
  validation_status: string;
  validation_errors: string[];
  order: number;
  caller_auth_requirements?: CallerAuthRequirements;
}

export interface ApiMonitorMcpDetail {
  server: McpServerItem;
  tools: ApiMonitorMcpToolDetail[];
}

export interface ApiMonitorMcpConfigPayload {
  name?: string;
  description?: string;
  enabled?: boolean;
  default_enabled?: boolean;
  endpoint_config?: Record<string, unknown>;
  credential_binding?: Record<string, unknown>;
  api_monitor_auth?: ApiMonitorAuthConfig;
}

export interface ApiMonitorMcpToolTestResponse {
  success: boolean;
  status_code?: number;
  headers?: Record<string, unknown>;
  body?: unknown;
  error?: unknown;
  validation_status?: string;
  validation_errors?: string[];
  request_preview?: unknown;
}

export interface McpServerPayload {
  name: string;
  description?: string;
  transport: McpWritableTransport;
  enabled?: boolean;
  default_enabled: boolean;
  endpoint_config: McpEndpointConfig;
  credential_binding?: Partial<McpCredentialBinding>;
  tool_policy?: Partial<McpToolPolicy>;
}

const encodeServerKey = (serverKey: string) => encodeURIComponent(serverKey);

export async function listMcpServers(): Promise<McpServerItem[]> {
  const response = await apiClient.get<ApiResponse<McpServerItem[]>>('/mcp/servers');
  return response.data.data;
}

export async function getMcpServer(serverKey: string): Promise<McpServerItem> {
  const response = await apiClient.get<ApiResponse<McpServerItem>>(`/mcp/servers/${encodeServerKey(serverKey)}`);
  return response.data.data;
}

export async function createMcpServer(payload: McpServerPayload): Promise<{ id: string; saved: boolean }> {
  const response = await apiClient.post<ApiResponse<{ id: string; saved: boolean }>>('/mcp/servers', payload);
  return response.data.data;
}

export async function updateMcpServer(serverId: string, payload: McpServerPayload): Promise<{ id: string; saved: boolean }> {
  const response = await apiClient.put<ApiResponse<{ id: string; saved: boolean }>>(`/mcp/servers/${encodeURIComponent(serverId)}`, payload);
  return response.data.data;
}

export async function deleteMcpServer(serverId: string): Promise<{ id: string; deleted: boolean }> {
  const response = await apiClient.delete<ApiResponse<{ id: string; deleted: boolean }>>(`/mcp/servers/${encodeURIComponent(serverId)}`);
  return response.data.data;
}

export async function testMcpServer(serverKey: string): Promise<{ server_key: string; ok: boolean; tool_count: number }> {
  const response = await apiClient.post<ApiResponse<{ server_key: string; ok: boolean; tool_count: number }>>(
    `/mcp/servers/${encodeServerKey(serverKey)}/test`,
  );
  return response.data.data;
}

export async function getApiMonitorMcpDetail(serverKey: string): Promise<ApiMonitorMcpDetail> {
  const response = await apiClient.get<ApiResponse<ApiMonitorMcpDetail>>(
    `/mcp/servers/${encodeServerKey(serverKey)}/api-monitor-detail`,
  );
  return response.data.data;
}

export async function getApiMonitorExternalAccess(serverKey: string): Promise<ApiMonitorExternalAccessState> {
  const response = await apiClient.get<ApiResponse<ApiMonitorExternalAccessState>>(
    `/mcp/servers/${encodeServerKey(serverKey)}/api-monitor-external-access`,
  );
  return response.data.data;
}

export async function enableApiMonitorExternalAccess(serverKey: string): Promise<ApiMonitorExternalAccessState> {
  const response = await apiClient.post<ApiResponse<ApiMonitorExternalAccessState>>(
    `/mcp/servers/${encodeServerKey(serverKey)}/api-monitor-external-access/enable`,
  );
  return response.data.data;
}

export async function disableApiMonitorExternalAccess(serverKey: string): Promise<ApiMonitorExternalAccessState> {
  const response = await apiClient.post<ApiResponse<ApiMonitorExternalAccessState>>(
    `/mcp/servers/${encodeServerKey(serverKey)}/api-monitor-external-access/disable`,
  );
  return response.data.data;
}

export async function updateApiMonitorMcpConfig(
  serverKey: string,
  payload: ApiMonitorMcpConfigPayload,
): Promise<{ server: McpServerItem; saved: boolean }> {
  const response = await apiClient.put<ApiResponse<{ server: McpServerItem; saved: boolean }>>(
    `/mcp/servers/${encodeServerKey(serverKey)}/api-monitor-config`,
    payload,
  );
  return response.data.data;
}

export async function updateApiMonitorMcpTool(
  serverKey: string,
  toolId: string,
  payload: { yaml_definition: string },
): Promise<ApiMonitorMcpToolDetail> {
  const response = await apiClient.put<ApiResponse<ApiMonitorMcpToolDetail>>(
    `/mcp/servers/${encodeServerKey(serverKey)}/api-monitor-tools/${encodeURIComponent(toolId)}`,
    payload,
  );
  return response.data.data;
}

export async function testApiMonitorMcpTool(
  serverKey: string,
  toolId: string,
  payload: { arguments?: Record<string, unknown> },
): Promise<ApiMonitorMcpToolTestResponse> {
  const response = await apiClient.post<ApiResponse<ApiMonitorMcpToolTestResponse>>(
    `/mcp/servers/${encodeServerKey(serverKey)}/api-monitor-tools/${encodeURIComponent(toolId)}/test`,
    payload,
  );
  return response.data.data;
}

export async function discoverMcpTools(serverKey: string): Promise<{ server_key: string; tools: McpToolDiscoveryItem[]; tool_count: number }> {
  const response = await apiClient.post<ApiResponse<{ server_key: string; tools: McpToolDiscoveryItem[]; tool_count: number }>>(
    `/mcp/servers/${encodeServerKey(serverKey)}/discover-tools`,
  );
  return response.data.data;
}

export async function listSessionMcpServers(sessionId: string): Promise<SessionMcpServerItem[]> {
  const response = await apiClient.get<ApiResponse<SessionMcpServerItem[]>>(`/sessions/${encodeURIComponent(sessionId)}/mcp`);
  return response.data.data;
}

export async function updateSessionMcpServerMode(
  sessionId: string,
  serverKey: string,
  mode: McpSessionMode,
): Promise<{ session_id: string; server_key: string; mode: McpSessionMode }> {
  const response = await apiClient.put<ApiResponse<{ session_id: string; server_key: string; mode: McpSessionMode }>>(
    `/sessions/${encodeURIComponent(sessionId)}/mcp/servers/${encodeServerKey(serverKey)}`,
    { mode },
  );
  return response.data.data;
}
