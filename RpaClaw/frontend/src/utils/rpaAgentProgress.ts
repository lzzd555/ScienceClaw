export type RpaAgentMessageStatus = 'streaming' | 'executing' | 'done' | 'error';

export interface RpaAgentProgress {
  status: RpaAgentMessageStatus;
  label: string;
}

export const getInitialRpaAgentProgress = (): RpaAgentProgress => ({
  status: 'executing',
  label: 'Agent 正在规划录制步骤...',
});

export const getRpaAgentProgressForEvent = (eventType: string): RpaAgentProgress | null => {
  switch (eventType) {
    case 'agent_thought':
      return { status: 'executing', label: 'Agent 正在分析页面与任务...' };
    case 'agent_action':
      return { status: 'executing', label: 'Agent 正在执行浏览器操作...' };
    case 'agent_step_done':
    case 'trace_added':
      return { status: 'executing', label: '正在记录有效步骤...' };
    case 'agent_done':
      return { status: 'done', label: '' };
    case 'agent_aborted':
    case 'error':
      return { status: 'error', label: '' };
    default:
      return null;
  }
};
