import { describe, expect, it } from 'vitest';

import {
  buildSampleArguments,
  formatValidationStatus,
  syncYamlTopLevelField,
} from './apiMonitorMcp';

describe('syncYamlTopLevelField', () => {
  it('updates name and inserts description after name', () => {
    const withName = syncYamlTopLevelField(
      'method: GET\nname: search_orders\n',
      'name',
      'find_orders',
    );

    expect(withName).toBe('method: GET\nname: find_orders\n');

    const withDescription = syncYamlTopLevelField(withName, 'description', 'Find orders');

    expect(withDescription).toBe('method: GET\nname: find_orders\ndescription: Find orders\n');
  });
});

describe('buildSampleArguments', () => {
  it('builds sample values from a simple JSON schema', () => {
    expect(buildSampleArguments({
      type: 'object',
      properties: {
        query: { type: 'string' },
        limit: { type: 'integer' },
        includeArchived: { type: 'boolean' },
        filters: {
          type: 'array',
          items: { type: 'string' },
        },
        nested: {
          type: 'object',
          properties: {
            term: { type: 'string' },
          },
        },
      },
      required: ['query'],
    })).toEqual({
      query: 'sample',
      limit: 1,
      includeArchived: true,
      filters: ['sample'],
      nested: { term: 'sample' },
    });
  });
});

describe('formatValidationStatus', () => {
  it('formats valid and invalid labels', () => {
    expect(formatValidationStatus('valid')).toBe('Valid');
    expect(formatValidationStatus('invalid')).toBe('Invalid');
  });
});
