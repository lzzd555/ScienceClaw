import { describe, expect, it } from 'vitest';
import {
  getInitialRpaAgentProgress,
  getRpaAgentProgressForEvent,
} from './rpaAgentProgress';

describe('rpaAgentProgress', () => {
  it('starts trace-first commands with a visible planning state', () => {
    expect(getInitialRpaAgentProgress()).toEqual({
      status: 'executing',
      label: 'Agent 正在规划录制步骤...',
    });
  });

  it('maps trace-first stream events to in-message progress labels', () => {
    expect(getRpaAgentProgressForEvent('agent_thought')).toEqual({
      status: 'executing',
      label: 'Agent 正在分析页面与任务...',
    });
    expect(getRpaAgentProgressForEvent('agent_action')).toEqual({
      status: 'executing',
      label: 'Agent 正在执行浏览器操作...',
    });
    expect(getRpaAgentProgressForEvent('agent_step_done')).toEqual({
      status: 'executing',
      label: '正在记录有效步骤...',
    });
  });

  it('clears progress labels for terminal events', () => {
    expect(getRpaAgentProgressForEvent('agent_done')).toEqual({ status: 'done', label: '' });
    expect(getRpaAgentProgressForEvent('agent_aborted')).toEqual({ status: 'error', label: '' });
  });
});
