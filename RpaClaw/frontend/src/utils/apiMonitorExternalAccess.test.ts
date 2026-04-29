import { describe, expect, it } from 'vitest';
import {
  buildApiMonitorExternalClientConfig,
  formatCallerAuthRequirement,
  formatExternalAccessTokenHint,
} from './apiMonitorExternalAccess';

describe('apiMonitorExternalAccess', () => {
  it('formats placeholder caller auth requirement', () => {
    expect(formatCallerAuthRequirement({ required: false, credential_type: 'placeholder', accepted_fields: [] })).toBe(
      'placeholder: no target API credential is injected',
    );
  });

  it('formats test caller auth requirement', () => {
    expect(formatCallerAuthRequirement({ required: true, credential_type: 'test', accepted_fields: ['_auth.headers.Authorization'] })).toBe(
      'test: pass _auth.headers.Authorization on each tool call',
    );
  });

  it('formats empty token hint', () => {
    expect(formatExternalAccessTokenHint('')).toBe('not generated');
  });

  it('builds external MCP client config', () => {
    expect(
      buildApiMonitorExternalClientConfig({
        name: 'Orders API Monitor MCP',
        url: 'http://localhost:12001/api/v1/api-monitor-mcp/mcp_abc/mcp',
        accessToken: 'rpamcp_secret',
      }),
    ).toEqual({
      name: 'Orders API Monitor MCP',
      transport: 'streamable_http',
      url: 'http://localhost:12001/api/v1/api-monitor-mcp/mcp_abc/mcp',
      headers: {
        Authorization: 'Bearer rpamcp_secret',
      },
    });
  });
});
