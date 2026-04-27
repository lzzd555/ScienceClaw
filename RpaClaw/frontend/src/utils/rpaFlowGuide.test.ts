import { describe, expect, it } from 'vitest';

import {
  DISCARD_RECORDING_CONFIRMATION,
  buildRpaFlowSteps,
  type RpaFlowGuideState,
} from './rpaFlowGuide';

describe('buildRpaFlowSteps', () => {
  it('keeps configure available after recorder has a session and gates test', () => {
    const state: RpaFlowGuideState = {
      currentStep: 'record',
      sessionId: 'session-1',
      recordedStepCount: 4,
      diagnosticCount: 0,
      isRecording: true,
      recordingTime: '01:20',
    };

    expect(buildRpaFlowSteps(state)).toEqual([
      expect.objectContaining({ id: 'record', status: 'active', disabled: false }),
      expect.objectContaining({ id: 'configure', status: 'available', disabled: false }),
      expect.objectContaining({ id: 'test', status: 'disabled', disabled: true, disabledReason: '先完成配置' }),
    ]);
  });

  it('blocks test and save when configuration has diagnostics', () => {
    const state: RpaFlowGuideState = {
      currentStep: 'configure',
      sessionId: 'session-1',
      recordedStepCount: 8,
      diagnosticCount: 3,
    };

    expect(buildRpaFlowSteps(state)).toEqual([
      expect.objectContaining({ id: 'record', status: 'available', disabled: false, destructive: true }),
      expect.objectContaining({ id: 'configure', status: 'active', disabled: false }),
      expect.objectContaining({ id: 'test', status: 'disabled', disabled: true, disabledReason: '3 个步骤待修复' }),
    ]);
  });

  it('allows test navigation from configure when diagnostics are clear', () => {
    const state: RpaFlowGuideState = {
      currentStep: 'configure',
      sessionId: 'session-1',
      recordedStepCount: 8,
      diagnosticCount: 0,
    };

    expect(buildRpaFlowSteps(state)).toEqual([
      expect.objectContaining({ id: 'record', status: 'available', destructive: true }),
      expect.objectContaining({ id: 'configure', status: 'active' }),
      expect.objectContaining({ id: 'test', status: 'available', disabled: false }),
    ]);
  });

  it('keeps configure available from test and marks record as destructive', () => {
    const state: RpaFlowGuideState = {
      currentStep: 'test',
      sessionId: 'session-1',
      recordedStepCount: 8,
      diagnosticCount: 0,
      testState: 'success',
    };

    expect(buildRpaFlowSteps(state)).toEqual([
      expect.objectContaining({ id: 'record', status: 'available', destructive: true }),
      expect.objectContaining({ id: 'configure', status: 'completed', disabled: false }),
      expect.objectContaining({ id: 'test', status: 'active', disabled: false }),
    ]);
  });
});

describe('DISCARD_RECORDING_CONFIRMATION', () => {
  it('explicitly says previous work will be discarded', () => {
    expect(DISCARD_RECORDING_CONFIRMATION.title).toBe('重新录制？');
    expect(DISCARD_RECORDING_CONFIRMATION.message).toContain('会丢弃之前的所有工作');
    expect(DISCARD_RECORDING_CONFIRMATION.message).toContain('录制步骤、参数配置、脚本预览和测试结果');
  });
});
