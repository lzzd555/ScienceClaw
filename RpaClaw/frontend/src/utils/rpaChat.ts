import type {
  RpaAttemptEvent,
  RpaAttemptEventType,
  RpaContextWriteEntry,
  RpaResultEvent,
} from '@/types/rpa';

export const cleanupAssistantText = (text: string, script = '') => {
  let next = text;
  next = next.replace(/^正在分析当前页面(?:\.{3,}|…+)\s*/u, '');
  next = next.replace(/```python[\s\S]*?```/gu, '');
  if (script) {
    next = next.replace(script, '');
  }
  return next.trim();
};

const stringifyValue = (value: unknown) => {
  if (value === null || value === undefined) return '（未提供）';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value, null, 0);
  } catch {
    return String(value);
  }
};

const formatContextWriteEntry = (item: RpaContextWriteEntry) => `已写入上下文：${item.key} = ${stringifyValue(item.value)}`;

export const formatDisplayValue = (value: unknown) => stringifyValue(value);

export const formatContextWrites = (writes: RpaContextWriteEntry[]) =>
  writes.map((item) => formatContextWriteEntry(item));

export const formatResultContextWrites = (data: RpaResultEvent) => {
  if (!('context_writes' in data) || !Array.isArray(data.context_writes) || data.context_writes.length === 0) {
    return [];
  }

  const payload = data.output_payload && typeof data.output_payload === 'object'
    ? data.output_payload
    : (data.step?.output_payload && typeof data.step.output_payload === 'object'
      ? data.step.output_payload
      : {});

  return data.context_writes.map((key) => `已写入上下文：${key} = ${stringifyValue(payload[key])}`);
};

const formatResultStatusText = (status?: string) => {
  if (!status || status === 'success') return '执行成功';
  if (status === 'failed') return '执行失败';
  if (status === 'recovered_after_retry') return '重试后恢复成功';
  if (status === 'partial_success') return '部分成功，仍有部分结果未完成';
  return status.replace(/_/g, ' ');
};

const hasNonEmptyObject = (value: unknown) => (
  !!value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value as Record<string, unknown>).length > 0
);

export const formatResultOutput = (data: RpaResultEvent) => {
  if (!('output' in data)) return '';
  const output = data.output;
  if (output === null || output === undefined || output === '' || output === 'ok' || output === 'None') return '';
  const hasStructuredPayload = (
    ('context_writes' in data && Array.isArray(data.context_writes) && data.context_writes.length > 0)
    || hasNonEmptyObject(data.output_payload)
    || hasNonEmptyObject(data.step?.output_payload)
  );
  if (hasStructuredPayload && typeof output === 'object') return '';
  return `输出：${stringifyValue(output)}`;
};

export const formatResultStatusSummary = (data: RpaResultEvent) => {
  const status = data.status || (data.success === false ? 'failed' : 'success');
  return `最终状态：${formatResultStatusText(status)}。`;
};

const formatAttemptPrefix = (attempt: number) => `第 ${attempt} 轮`;

const formatExpectedOutputKeys = (keys?: string[]) => (
  Array.isArray(keys) && keys.length > 0 ? `，目标字段：${keys.join('、')}` : ''
);

export const formatAttemptStatusLabel = (eventType: RpaAttemptEventType, data: RpaAttemptEvent) => {
  const attempt = data.attempt || 0;
  const summary = data.summary || data.description || data.action || '执行步骤';

  if (eventType === 'attempt_started') {
    return `${formatAttemptPrefix(attempt)}开始：${summary}`;
  }

  if (eventType === 'attempt_output') {
    return `${formatAttemptPrefix(attempt)}上下文提取：${summary}${formatExpectedOutputKeys(data.expected_output_keys)}`;
  }

  if (eventType === 'attempt_failed') {
    const reason = data.failure_kind || data.error || '未知错误';
    const suffix = data.retrying ? '，准备重试。' : '。';
    return `${formatAttemptPrefix(attempt)}失败：${reason}${suffix}`;
  }

  if (eventType === 'attempt_succeeded') {
    return `${formatAttemptPrefix(attempt)}成功：${summary}`;
  }

  return '';
};

export const formatAttemptContextWrites = (data: RpaAttemptEvent) => {
  if (!Array.isArray(data.context_writes) || data.context_writes.length === 0) return [];
  return formatContextWrites(data.context_writes);
};

export const formatResultContextWriteLines = (data: RpaResultEvent) => formatResultContextWrites(data);
