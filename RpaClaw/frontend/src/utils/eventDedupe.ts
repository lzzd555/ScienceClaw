import type { AgentSSEEvent } from '../types/event';

export const shouldProcessAgentEvent = (
  event: AgentSSEEvent,
  processedEventIds: Set<string>,
  dedupe = true,
) => {
  if (!dedupe) return true;

  const eventId = event.data?.event_id;
  if (!eventId) return true;
  if (processedEventIds.has(eventId)) return false;

  processedEventIds.add(eventId);
  return true;
};
