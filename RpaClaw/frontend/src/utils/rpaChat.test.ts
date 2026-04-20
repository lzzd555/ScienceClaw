import { describe, expect, it } from 'vitest';

import {
  cleanupAssistantText,
  formatAttemptStatusLabel,
  formatContextWrites,
  formatResultContextWrites,
  formatResultOutput,
  formatResultStatusSummary,
} from './rpaChat';

describe('cleanupAssistantText', () => {
  it('removes the analysis placeholder for structured action messages', () => {
    const text = '正在分析当前页面......\n\n用户想要获取"购买人"的值。';

    expect(cleanupAssistantText(text)).toBe('用户想要获取"购买人"的值。');
  });

  it('removes embedded python code blocks and the exact script body', () => {
    const text = '正在分析当前页面......\n\n说明\n```python\nprint(\"hi\")\n```\nprint(\"hi\")';

    expect(cleanupAssistantText(text, 'print("hi")')).toBe('说明');
  });
});

describe('formatContextWrites', () => {
  it('renders key-value pairs for context writes', () => {
    expect(formatContextWrites([{ key: 'buyer', value: '李雨晨' }])).toEqual([
      '已写入上下文：buyer = 李雨晨',
    ]);
  });

  it('renders missing values explicitly instead of dropping the key', () => {
    expect(formatContextWrites([{ key: 'department', value: undefined }])).toEqual([
      '已写入上下文：department = （未提供）',
    ]);
  });

  it('renders result context writes from keys and payload', () => {
    expect(formatResultContextWrites({
      context_writes: ['buyer', 'department'],
      step: {
        output_payload: {
          buyer: '李雨晨',
          department: '研发效能组',
        },
      },
    })).toEqual([
      '已写入上下文：buyer = 李雨晨',
      '已写入上下文：department = 研发效能组',
    ]);
  });

  it('prefers the top-level payload when present', () => {
    expect(formatResultContextWrites({
      context_writes: ['buyer'],
      output_payload: {
        buyer: '张三',
      },
    })).toEqual([
      '已写入上下文：buyer = 张三',
    ]);
  });

  it('returns no context writes when the field is missing', () => {
    expect(formatResultContextWrites({
      output_payload: {
        buyer: '王五',
      },
    })).toEqual([]);
  });

  it('does not fall back to payload keys when context_writes is an empty array', () => {
    expect(formatResultContextWrites({
      context_writes: [],
      output_payload: {
        buyer: '赵六',
      },
    })).toEqual([]);
  });
});

describe('formatAttemptStatusLabel', () => {
  it('renders failure and retry text in Chinese', () => {
    expect(formatAttemptStatusLabel('attempt_failed', {
      attempt: 1,
      summary: '获取购买人',
      error: 'boom',
      retrying: true,
    })).toBe('第 1 轮失败：boom，准备重试。');
  });

  it('renders attempt output target keys', () => {
    expect(formatAttemptStatusLabel('attempt_output', {
      attempt: 2,
      summary: '获取 PR 单核心字段',
      expected_output_keys: ['buyer', 'supplier'],
    })).toBe('第 2 轮上下文提取：获取 PR 单核心字段，目标字段：buyer、supplier');
  });

  it('renders attempt started and success labels', () => {
    expect(formatAttemptStatusLabel('attempt_started', {
      attempt: 3,
      action: 'ai_script',
    })).toBe('第 3 轮开始：ai_script');

    expect(formatAttemptStatusLabel('attempt_succeeded', {
      attempt: 3,
      summary: '获取购买人',
    })).toBe('第 3 轮成功：获取购买人');
  });
});

describe('formatResultOutput', () => {
  it('formats object output as JSON when there is no structured payload to suppress it', () => {
    expect(formatResultOutput({
      output: { ok: true, count: 2 },
    })).toBe('输出：{"ok":true,"count":2}');
  });

  it('does not suppress object output for an empty payload', () => {
    expect(formatResultOutput({
      output: { ok: true, count: 2 },
      output_payload: {},
    })).toBe('输出：{"ok":true,"count":2}');
  });

  it('skips object output when structured payload is already present', () => {
    expect(formatResultOutput({
      output: { ok: true, count: 2 },
      output_payload: { ok: true },
    })).toBe('');
  });

  it('skips object output when explicit context writes are present', () => {
    expect(formatResultOutput({
      output: { ok: true, count: 2 },
      context_writes: ['ok'],
    })).toBe('');
  });
});

describe('formatResultStatusSummary', () => {
  it('renders the supported final result statuses in Chinese', () => {
    expect(formatResultStatusSummary({ status: 'success' })).toBe('最终状态：执行成功。');
    expect(formatResultStatusSummary({ status: 'failed' })).toBe('最终状态：执行失败。');
    expect(formatResultStatusSummary({ status: 'recovered_after_retry' })).toBe('最终状态：重试后恢复成功。');
    expect(formatResultStatusSummary({ status: 'partial_success' })).toBe('最终状态：部分成功，仍有部分结果未完成。');
  });

  it('falls back to success when status is missing and success is not false', () => {
    expect(formatResultStatusSummary({})).toBe('最终状态：执行成功。');
  });

  it('falls back to failed when the event marks success as false', () => {
    expect(formatResultStatusSummary({ success: false })).toBe('最终状态：执行失败。');
  });
});
