import type { ApiMonitorAuthConfig, ApiMonitorAuthConfigPublish, ApiMonitorCredentialType } from '@/api/mcp';

export const API_MONITOR_PLACEHOLDER_CREDENTIAL_TYPE: ApiMonitorCredentialType = 'placeholder';
export const API_MONITOR_TEST_CREDENTIAL_TYPE: ApiMonitorCredentialType = 'test';

export const API_MONITOR_CREDENTIAL_TYPE_OPTIONS = [
  {
    value: API_MONITOR_PLACEHOLDER_CREDENTIAL_TYPE,
    labelKey: 'API Monitor Placeholder credential type',
    descriptionKey: 'API Monitor Placeholder credential type hint',
  },
  {
    value: API_MONITOR_TEST_CREDENTIAL_TYPE,
    labelKey: 'API Monitor Test credential type',
    descriptionKey: 'API Monitor Test credential type hint',
  },
] as const;

export function normalizeApiMonitorAuth(value?: Partial<ApiMonitorAuthConfig> | null): ApiMonitorAuthConfigPublish {
  const result: ApiMonitorAuthConfigPublish = {
    credential_type: value?.credential_type || API_MONITOR_PLACEHOLDER_CREDENTIAL_TYPE,
    credential_id: value?.credential_id || '',
    login_url: value?.login_url || '',
  };
  if ((value as any)?.token_flows) {
    result.token_flows = (value as any).token_flows;
  }
  return result;
}

export function formatApiMonitorAuthStatus(value?: Partial<ApiMonitorAuthConfig> | null): 'configured' | 'missing_credential' {
  const normalized = normalizeApiMonitorAuth(value);
  return normalized.credential_id ? 'configured' : 'missing_credential';
}
