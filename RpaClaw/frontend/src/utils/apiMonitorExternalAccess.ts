import type { CallerAuthRequirements } from '@/api/mcp';

export type ExternalClientConfigInput = {
  name: string;
  url: string;
  accessToken: string;
};

export function formatCallerAuthRequirement(requirements?: CallerAuthRequirements | null): string {
  if (!requirements || requirements.credential_type === 'placeholder' || !requirements.required) {
    return 'placeholder: no target API credential is injected';
  }
  return 'test: pass _auth.headers.Authorization on each tool call';
}

export function formatExternalAccessTokenHint(tokenHint?: string | null): string {
  return tokenHint && tokenHint.trim() ? tokenHint : 'not generated';
}

export function buildApiMonitorExternalClientConfig(input: ExternalClientConfigInput) {
  return {
    name: input.name,
    transport: 'streamable_http',
    url: input.url,
    headers: {
      Authorization: `Bearer ${input.accessToken}`,
    },
  };
}
