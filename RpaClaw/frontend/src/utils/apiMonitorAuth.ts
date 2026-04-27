import type { ApiMonitorAuthConfig, ApiMonitorCredentialType } from '@/api/mcp';

export const API_MONITOR_PLACEHOLDER_CREDENTIAL_TYPE: ApiMonitorCredentialType = 'placeholder';

export const API_MONITOR_CREDENTIAL_TYPE_OPTIONS = [
  {
    value: API_MONITOR_PLACEHOLDER_CREDENTIAL_TYPE,
    labelKey: 'API Monitor Placeholder credential type',
    descriptionKey: 'API Monitor Placeholder credential type hint',
  },
] as const;

export function normalizeApiMonitorAuth(value?: Partial<ApiMonitorAuthConfig> | null): ApiMonitorAuthConfig {
  return {
    credential_type: value?.credential_type || API_MONITOR_PLACEHOLDER_CREDENTIAL_TYPE,
    credential_id: value?.credential_id || '',
  };
}

export function formatApiMonitorAuthStatus(value?: Partial<ApiMonitorAuthConfig> | null): 'configured' | 'missing_credential' {
  const normalized = normalizeApiMonitorAuth(value);
  return normalized.credential_id ? 'configured' : 'missing_credential';
}
