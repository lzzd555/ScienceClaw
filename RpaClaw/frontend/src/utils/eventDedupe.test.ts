import { describe, expect, it } from 'vitest';
import type { AgentSSEEvent } from '../types/event';
import { shouldProcessAgentEvent } from './eventDedupe';

const messageEvent = (event_id: string, content: string): AgentSSEEvent => ({
  event: 'message',
  data: {
    event_id,
    timestamp: 1,
    role: 'user',
    content,
    attachments: [],
  },
});

describe('shouldProcessAgentEvent', () => {
  it('deduplicates duplicate live events by event_id', () => {
    const processedEventIds = new Set<string>();
    const first = messageEvent('same-event-id', 'first user message');
    const second = messageEvent('same-event-id', 'second user message');

    expect(shouldProcessAgentEvent(first, processedEventIds)).toBe(true);
    expect(shouldProcessAgentEvent(second, processedEventIds)).toBe(false);
  });

  it('processes every persisted history event when dedupe is disabled', () => {
    const processedEventIds = new Set<string>();
    const first = messageEvent('same-event-id', 'first user message');
    const second = messageEvent('same-event-id', 'second user message');

    expect(shouldProcessAgentEvent(first, processedEventIds, false)).toBe(true);
    expect(shouldProcessAgentEvent(second, processedEventIds, false)).toBe(true);
    expect(processedEventIds.size).toBe(0);
  });
});
