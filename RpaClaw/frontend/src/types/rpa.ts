export interface RpaContextWriteEntry {
  key: string;
  value: unknown;
}

export type RpaAttemptEventType =
  | 'attempt_started'
  | 'attempt_output'
  | 'attempt_failed'
  | 'attempt_succeeded';

export interface RpaAttemptEvent {
  attempt: number;
  action?: string;
  summary?: string;
  description?: string;
  error?: string;
  failure_kind?: string;
  retrying?: boolean;
  expected_output_keys?: string[];
  output_payload?: Record<string, unknown>;
  context_writes?: RpaContextWriteEntry[];
}

export interface RpaStepResult {
  output_payload?: Record<string, unknown>;
}

export interface RpaResultEvent {
  success?: boolean;
  status?: string;
  error?: string;
  output?: unknown;
  output_payload?: Record<string, unknown>;
  context_writes?: string[];
  step?: RpaStepResult;
}

export interface RpaAttemptTimelineEvent {
  eventType: RpaAttemptEventType;
  data: RpaAttemptEvent;
}
