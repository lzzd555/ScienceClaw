import { describe, expect, it } from 'vitest';

import {
  API_MONITOR_CREDENTIAL_TYPE_OPTIONS,
  formatApiMonitorAuthStatus,
  normalizeApiMonitorAuth,
} from './apiMonitorAuth';

describe('API Monitor auth helpers', () => {
  it('exposes the placeholder and test credential types', () => {
    expect(API_MONITOR_CREDENTIAL_TYPE_OPTIONS).toEqual([
      {
        value: 'placeholder',
        labelKey: 'API Monitor Placeholder credential type',
        descriptionKey: 'API Monitor Placeholder credential type hint',
      },
      {
        value: 'test',
        labelKey: 'API Monitor Test credential type',
        descriptionKey: 'API Monitor Test credential type hint',
      },
    ]);
  });

  it('normalizes empty auth to placeholder with no credential', () => {
    expect(normalizeApiMonitorAuth(undefined)).toEqual({
      credential_type: 'placeholder',
      credential_id: '',
      login_url: '',
    });
  });

  it('formats configured and unconfigured status', () => {
    expect(formatApiMonitorAuthStatus({ credential_type: 'placeholder', credential_id: 'cred_1' })).toBe('configured');
    expect(formatApiMonitorAuthStatus({ credential_type: 'placeholder', credential_id: '' })).toBe('missing_credential');
  });
});
