import { describe, expect, it } from 'vitest';
import {
  applyRpaAssistantRunEvent,
  createRpaAssistantRun,
} from './rpaAssistantRun';

describe('rpaAssistantRun', () => {
  it('groups multiple assistant attempts under one user command', () => {
    let run = createRpaAssistantRun('23:53');

    run = applyRpaAssistantRunEvent(run, 'agent_thought', {
      text: 'Planning one trace-first recording command.',
    });
    run = applyRpaAssistantRunEvent(run, 'agent_action', {
      description: '打开仓库 Issues 列表并读取最近一条 issue 标题',
      code: 'async def run(page, results):\n    raise Exception("brace quote")',
    });
    run = applyRpaAssistantRunEvent(run, 'agent_step_done', {
      success: false,
      output: 'Command contains brace with quote character ERROR',
    });

    run = applyRpaAssistantRunEvent(run, 'agent_thought', {
      text: 'Repairing with current page state.',
    });
    run = applyRpaAssistantRunEvent(run, 'agent_action', {
      description: '读取第一条 issue 标题',
      code: 'async def run(page, results):\n    return {"title": "401 error when sending via Telegram"}',
    });
    run = applyRpaAssistantRunEvent(run, 'trace_added', {
      trace_id: 'trace-ai-2',
      description: '读取第一条 issue 标题',
    });
    run = applyRpaAssistantRunEvent(run, 'agent_step_done', {
      success: true,
      output: { title: '401 error when sending via Telegram' },
      trace: { trace_id: 'trace-ai-2' },
    });
    run = applyRpaAssistantRunEvent(run, 'agent_done', {
      message: 'Task completed',
      trace_count: 4,
    });

    expect(run.status).toBe('done');
    expect(run.traceCount).toBe(4);
    expect(run.rounds).toHaveLength(2);
    expect(run.rounds.map((round) => round.status)).toEqual(['error', 'done']);
    expect(run.rounds[0].items.map((item) => item.kind)).toEqual(['plan', 'action', 'output']);
    expect(run.rounds[1].items.map((item) => item.kind)).toEqual(['plan', 'action', 'trace', 'output']);
    expect(run.rounds[1].items[3].detail).toContain('401 error when sending via Telegram');
  });

  it('puts abort diagnostics into the active round and preserves raw facts first', () => {
    let run = createRpaAssistantRun('23:54');

    run = applyRpaAssistantRunEvent(run, 'agent_thought', { text: 'Planning command.' });
    run = applyRpaAssistantRunEvent(run, 'agent_aborted', {
      reason: 'Selector failed',
      diagnostics: [
        { code: 'runtime_error', message: 'Timeout waiting for locator' },
        { code: 'hint', message: 'Try a role locator' },
      ],
    });

    expect(run.status).toBe('error');
    expect(run.error).toBe('Selector failed');
    expect(run.diagnostics).toEqual([
      'runtime_error: Timeout waiting for locator',
      'hint: Try a role locator',
    ]);
    expect(run.rounds[0].items[run.rounds[0].items.length - 1]).toMatchObject({
      kind: 'diagnostic',
      title: '执行停止',
      detail: 'Selector failed',
    });
  });

  it('uses business-facing labels for technical recording events', () => {
    let run = createRpaAssistantRun('00:18');

    run = applyRpaAssistantRunEvent(run, 'agent_thought', {
      text: 'Planning one trace-first recording command.',
    });
    run = applyRpaAssistantRunEvent(run, 'agent_action', {
      description: '提取项目名、star数和fork数',
      code: 'async def run(page, results):\n    return {}',
    });
    run = applyRpaAssistantRunEvent(run, 'trace_added', {
      trace_id: 'trace-ai-1',
      description: '提取项目名、star数和fork数',
    });
    run = applyRpaAssistantRunEvent(run, 'agent_step_done', {
      success: true,
      output: {
        project_name: 'free-claude-code',
        star_count: '12.8k',
        fork_count: '1.9k',
      },
    });
    run = applyRpaAssistantRunEvent(run, 'agent_done', {
      message: 'Task completed',
      trace_count: 5,
    });

    expect(run.summary).toBe('任务完成');
    expect(run.rounds[0].items.map((item) => item.title)).toEqual([
      '正在理解你的目标',
      '正在操作页面',
      '已记录为技能步骤',
      '获取到的结果',
    ]);
    expect(run.rounds[0].items[0].detail).toBe('正在把你的目标拆成可录制的浏览器操作。');
    expect(run.rounds[0].items[2].detail).toBe('提取项目名、star数和fork数');
  });
});
