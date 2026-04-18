import { apiClient, ApiResponse } from './client';

export type McpTransport = 'stdio' | 'streamable_http' | 'sse';
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
  enabled: boolean;
  default_enabled: boolean;
  readonly: boolean;
  endpoint_config: McpEndpointConfig;
  credential_binding: McpCredentialBinding;
  tool_policy: McpToolPolicy;
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

export interface McpServerPayload {
  name: string;
  description?: string;
  transport: McpTransport;
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
