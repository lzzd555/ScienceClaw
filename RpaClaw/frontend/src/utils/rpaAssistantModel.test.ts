import { describe, expect, it } from 'vitest';
import {
  buildRpaAssistantChatPayload,
  getDefaultRpaAssistantModelId,
  shouldSubmitRpaAssistantComposer,
} from './rpaAssistantModel';
import type { ModelConfig } from '@/api/models';

const model = (overrides: Partial<ModelConfig>): ModelConfig => ({
  id: 'model-1',
  name: 'Model 1',
  provider: 'openai',
  model_name: 'gpt-test',
  is_system: false,
  is_active: true,
  created_at: 1,
  updated_at: 1,
  ...overrides,
});

describe('rpaAssistantModel', () => {
  it('keeps the selected model when it is still available', () => {
    const models = [model({ id: 'model-1' }), model({ id: 'model-2' })];

    expect(getDefaultRpaAssistantModelId(models, 'model-2')).toBe('model-2');
  });

  it('defaults to the system model before the first user model', () => {
    const models = [
      model({ id: 'user-model', is_system: false }),
      model({ id: 'system-model', is_system: true }),
    ];

    expect(getDefaultRpaAssistantModelId(models, null)).toBe('system-model');
  });

  it('includes the selected model id in assistant chat payloads', () => {
    expect(buildRpaAssistantChatPayload('click search', 'model-2')).toEqual({
      message: 'click search',
      mode: 'trace_first',
      model_config_id: 'model-2',
    });
  });

  it('omits model_config_id when no model is selected', () => {
    expect(buildRpaAssistantChatPayload('click search', null)).toEqual({
      message: 'click search',
      mode: 'trace_first',
    });
  });

  it('submits on Enter but preserves multiline editing shortcuts', () => {
    expect(shouldSubmitRpaAssistantComposer({ key: 'Enter' })).toBe(true);
    expect(shouldSubmitRpaAssistantComposer({ key: 'Enter', shiftKey: true })).toBe(false);
    expect(shouldSubmitRpaAssistantComposer({ key: 'Enter', isComposing: true })).toBe(false);
    expect(shouldSubmitRpaAssistantComposer({ key: 'a' })).toBe(false);
  });
});
