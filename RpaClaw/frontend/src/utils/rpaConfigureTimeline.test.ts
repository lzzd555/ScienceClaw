import { describe, expect, it } from 'vitest';
import {
  getLegacyRpaSteps,
  getManualRecordingDiagnostics,
  hasManualRecordingDiagnostics,
  isRpaTimelineStepDeletable,
  mapRpaConfigureDisplaySteps,
} from './rpaConfigureTimeline';

describe('rpaConfigureTimeline', () => {
  it('deduplicates recorded actions and their derived manual traces', () => {
    const session = {
      steps: [
        {
          id: 'step-search',
          action: 'click',
          description: 'legacy click should only remain for parameterization',
        },
      ],
      traces: [
        {
          trace_id: 'trace-step-search',
          trace_type: 'manual_action',
          source: 'manual',
          action: 'click',
          description: 'derived manual trace should not duplicate recorded action',
        },
      ],
      recorded_actions: [
        {
          step_id: 'step-search',
          action_kind: 'click',
          description: '点击 button("Search")',
          target: { method: 'role', role: 'button', name: 'Search' },
          validation: { status: 'ok' },
          page_state: { url: 'https://example.test/search' },
        },
      ],
    };

    const displaySteps = mapRpaConfigureDisplaySteps(session);

    expect(displaySteps).toHaveLength(1);
    expect(displaySteps[0]).toMatchObject({
      id: 'step-search',
      stepId: 'step-search',
      traceId: 'trace-step-search',
      action: 'click',
      description: '点击 button("Search")',
      source: 'record',
      url: 'https://example.test/search',
      validation: { status: 'ok', details: 'Accepted manual action' },
    });
    expect(displaySteps[0].target).toEqual({ method: 'role', role: 'button', name: 'Search' });
  });

  it('keeps AI traces visible when recorded actions are present', () => {
    const session = {
      steps: [
        { id: 'step-search', action: 'click', description: 'legacy click' },
      ],
      traces: [
        {
          trace_id: 'trace-step-search',
          trace_type: 'manual_action',
          source: 'manual',
          action: 'click',
          description: 'derived manual trace should be deduplicated',
        },
        {
          trace_id: 'trace-ai-project',
          trace_type: 'ai_operation',
          source: 'ai',
          user_instruction: '抓取第一个项目的信息',
          description: '抓取第一个项目的信息',
          output_key: 'selected_project',
        },
      ],
      recorded_actions: [
        {
          step_id: 'step-search',
          action_kind: 'click',
          description: 'click search',
          target: { method: 'role', role: 'button', name: 'Search' },
          validation: { status: 'ok' },
        },
      ],
    };

    const displaySteps = mapRpaConfigureDisplaySteps(session);

    expect(displaySteps).toHaveLength(2);
    expect(displaySteps.map((step) => step.id)).toEqual(['step-search', 'trace-ai-project']);
    expect(displaySteps[1]).toMatchObject({
      traceId: 'trace-ai-project',
      source: 'ai',
      action: 'ai_operation',
      description: '抓取第一个项目的信息',
    });
  });

  it('keeps accepted traces as fallback when recorded actions are absent', () => {
    const session = {
      steps: [
        { id: 'fill-1', action: 'fill', value: 'Alice', sensitive: false },
      ],
      traces: [
        {
          trace_id: 'trace-fill',
          trace_type: 'dataflow_fill',
          description: 'Dataflow fill',
        },
      ],
    };

    const displaySteps = mapRpaConfigureDisplaySteps(session);

    expect(displaySteps).toHaveLength(1);
    expect(displaySteps[0]).toMatchObject({
      id: 'trace-fill',
      traceId: 'trace-fill',
    });
    expect(getLegacyRpaSteps(session)).toEqual(session.steps);
  });

  it('falls back to legacy steps when no recorded actions or traces are present', () => {
    const session = {
      steps: [
        { id: 'click-1', action: 'click', description: 'Click search' },
      ],
      traces: [],
      recorded_actions: [],
    };

    expect(mapRpaConfigureDisplaySteps(session)).toEqual(session.steps);
  });

  it('maps recording diagnostics back to legacy step indexes', () => {
    const session = {
      steps: [
        {
          id: 'step-bad',
          action: 'fill',
          description: '输入 "foo" 到 None',
          locator_candidates: [{ playwright_locator: 'page.locator(".mystery")', selected: true }],
          url: 'https://example.test/search',
        },
      ],
      recording_diagnostics: [
        {
          related_step_id: 'step-bad',
          related_action_kind: 'fill',
          failure_reason: 'canonical_target_missing',
          raw_candidates: [{ playwright_locator: 'page.locator(".mystery")', selected: true }],
          page_state: { url: 'https://example.test/search' },
        },
      ],
    };

    const diagnostics = getManualRecordingDiagnostics(session);

    expect(diagnostics).toHaveLength(1);
    expect(diagnostics[0]).toMatchObject({
      stepId: 'step-bad',
      stepIndex: 0,
      action: 'fill',
      failureReason: 'canonical_target_missing',
      validation: { status: 'broken', details: 'canonical target missing' },
      configurable: true,
      url: 'https://example.test/search',
    });
    expect(hasManualRecordingDiagnostics(session)).toBe(true);
  });

  it('allows deleting AI timeline items only when they have stable trace ids', () => {
    expect(isRpaTimelineStepDeletable({ source: 'ai', traceId: 'trace-ai-project' })).toBe(true);
    expect(isRpaTimelineStepDeletable({ source: 'ai' })).toBe(false);
    expect(isRpaTimelineStepDeletable({ source: 'record', stepId: 'step-search' })).toBe(true);
  });
});
