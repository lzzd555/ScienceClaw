import type { ModelConfig } from '@/api/models';

export interface RpaAssistantChatPayload {
  message: string;
  mode: 'trace_first';
  model_config_id?: string;
}

export function getDefaultRpaAssistantModelId(
  models: ModelConfig[],
  currentModelId: string | null,
): string | null {
  if (currentModelId && models.some((model) => model.id === currentModelId)) {
    return currentModelId;
  }
  return models.find((model) => model.is_system)?.id ?? models[0]?.id ?? null;
}

export function buildRpaAssistantChatPayload(
  message: string,
  selectedModelId: string | null,
): RpaAssistantChatPayload {
  const payload: RpaAssistantChatPayload = {
    message,
    mode: 'trace_first',
  };
  if (selectedModelId) {
    payload.model_config_id = selectedModelId;
  }
  return payload;
}

export function shouldSubmitRpaAssistantComposer(event: {
  key: string;
  shiftKey?: boolean;
  isComposing?: boolean;
}): boolean {
  return event.key === 'Enter' && !event.shiftKey && !event.isComposing;
}
