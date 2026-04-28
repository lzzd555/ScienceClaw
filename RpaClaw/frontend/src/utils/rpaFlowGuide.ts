export type RpaFlowStepId = 'record' | 'configure' | 'test';
export type RpaFlowStepStatus = 'active' | 'completed' | 'available' | 'disabled';
export type RpaTestState = 'idle' | 'running' | 'success' | 'failed';

export interface RpaFlowGuideState {
  currentStep: RpaFlowStepId;
  sessionId?: string | null;
  recordedStepCount?: number;
  diagnosticCount?: number;
  isRecording?: boolean;
  recordingTime?: string;
  testState?: RpaTestState;
  skillName?: string;
}

export interface RpaFlowStepView {
  id: RpaFlowStepId;
  index: number;
  title: string;
  caption: string;
  status: RpaFlowStepStatus;
  disabled: boolean;
  disabledReason: string;
  destructive: boolean;
}

export const DISCARD_RECORDING_CONFIRMATION = {
  title: '重新录制？',
  message: '重新录制会丢弃之前的所有工作，包括录制步骤、参数配置、脚本预览和测试结果。是否继续？',
  confirmText: '重新录制',
  cancelText: '取消',
} as const;

const STEP_TITLES: Record<RpaFlowStepId, string> = {
  record: '录制',
  configure: '配置',
  test: '测试保存',
};

const STEP_CAPTIONS: Record<RpaFlowStepId, string> = {
  record: '采集操作',
  configure: '修复与参数',
  test: '回放并保存',
};

const ORDER: RpaFlowStepId[] = ['record', 'configure', 'test'];

const baseStep = (id: RpaFlowStepId, index: number): RpaFlowStepView => ({
  id,
  index,
  title: STEP_TITLES[id],
  caption: STEP_CAPTIONS[id],
  status: 'disabled',
  disabled: true,
  disabledReason: '',
  destructive: false,
});

export function buildRpaFlowSteps(state: RpaFlowGuideState): RpaFlowStepView[] {
  const hasSession = !!state.sessionId;
  const diagnostics = Math.max(0, state.diagnosticCount || 0);

  return ORDER.map((id, index) => {
    const step = baseStep(id, index + 1);

    if (id === state.currentStep) {
      step.status = 'active';
      step.disabled = false;
      return step;
    }

    if (id === 'record') {
      step.status = 'available';
      step.disabled = false;
      step.destructive = state.currentStep !== 'record';
      return step;
    }

    if (id === 'configure') {
      if (hasSession) {
        step.status = state.currentStep === 'test' ? 'completed' : 'available';
        step.disabled = false;
      } else {
        step.disabledReason = '等待录制会话';
      }
      return step;
    }

    if (id === 'test') {
      if (state.currentStep === 'record') {
        step.disabledReason = '先完成配置';
        return step;
      }

      if (!hasSession) {
        step.disabledReason = '等待录制会话';
        return step;
      }

      if (diagnostics > 0) {
        step.disabledReason = `${diagnostics} 个步骤待修复`;
        return step;
      }

      step.status = 'available';
      step.disabled = false;
    }

    return step;
  });
}

export function getRpaFlowMetaChips(state: RpaFlowGuideState): string[] {
  const chips: string[] = [];
  const count = Math.max(0, state.recordedStepCount || 0);
  const diagnostics = Math.max(0, state.diagnosticCount || 0);

  if (state.isRecording && state.recordingTime) chips.push(`正在录制 ${state.recordingTime}`);
  if (count > 0) chips.push(`${count} 步`);
  if (diagnostics > 0) chips.push(`${diagnostics} 个待修复`);
  if (state.testState === 'running') chips.push('测试执行中');
  if (state.testState === 'success') chips.push('测试通过');
  if (state.testState === 'failed') chips.push('测试失败');

  return chips;
}
