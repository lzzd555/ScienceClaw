import { describe, expect, it } from 'vitest';

import {
  buildRpaStepTimelineItem,
  formatRpaActionLabel,
  formatRpaStepLocator,
  getRpaStepStatus,
} from './rpaStepTimeline';

describe('formatRpaActionLabel', () => {
  it('uses business-facing Chinese labels for common recorded actions', () => {
    expect(formatRpaActionLabel('click')).toBe('点击');
    expect(formatRpaActionLabel('fill')).toBe('输入');
    expect(formatRpaActionLabel('navigate')).toBe('导航');
    expect(formatRpaActionLabel('ai_operation')).toBe('AI 操作');
    expect(formatRpaActionLabel('navigate_click')).toBe('点击后跳转');
    expect(formatRpaActionLabel('download_click')).toBe('点击下载');
  });
});

describe('getRpaStepStatus', () => {
  it('marks the focused failed step as failed before validation status', () => {
    expect(getRpaStepStatus({
      step: { validation: { status: 'ok' } },
      index: 2,
      failedStepIndex: 2,
    })).toEqual({
      label: '执行失败',
      tone: 'danger',
    });
  });

  it('maps validation states to compact business status chips', () => {
    expect(getRpaStepStatus({ step: { validation: { status: 'strict' } }, index: 0 })).toEqual({
      label: '已确认',
      tone: 'success',
    });
    expect(getRpaStepStatus({ step: { validation: { status: 'broken' } }, index: 0 })).toEqual({
      label: '待修复',
      tone: 'danger',
    });
  });
});

describe('buildRpaStepTimelineItem', () => {
  it('keeps default content business-facing and moves locator to technical details', () => {
    const item = buildRpaStepTimelineItem({
      step: {
        id: 'step-1',
        action: 'click',
        description: '点击保存按钮',
        target: { type: 'role', role: 'button', name: 'Save current configuration with very long name' },
        frame_path: ['main'],
        validation: { status: 'ok', details: 'Accepted manual action' },
        locator_candidates: [{ kind: 'role', score: 95, selected: true, locator: { type: 'css', selector: '#save' } }],
        source: 'record',
      },
      index: 0,
    });

    expect(item.actionLabel).toBe('点击');
    expect(item.title).toBe('点击保存按钮');
    expect(item.summary).not.toContain('Locator');
    expect(item.summary).not.toContain('Accepted manual action');
    expect(item.summary).toContain('定位器：role=button');
    expect(item.summaryLabel).toBe('定位器');
    expect(item.summaryValue).toContain('role=button');
    expect(item.technical.locator).toContain('role=button');
    expect(item.technical.frame).toBe('Main frame');
    expect(item.technical.candidateCount).toBe(1);
  });

  it('falls back to a readable title for long technical-only steps', () => {
    const item = buildRpaStepTimelineItem({
      step: {
        id: 'step-2',
        action: 'click',
        target: { type: 'css', selector: 'button[data-testid="extremely-long-selector-value-that-should-stay-in-technical-details"]' },
      },
      index: 1,
    });

    expect(item.title).toBe('点击页面元素');
    expect(item.summary).toContain('定位器：button[data-testid=');
    expect(item.technical.locator).toContain('extremely-long-selector-value');
  });

  it('uses recorder-page locatorSummary when raw target is not present', () => {
    const item = buildRpaStepTimelineItem({
      step: {
        id: 'step-3',
        action: 'click',
        title: '点击 button("Date range: Today")',
        description: 'selected Playwright candidate is strict unique',
        locatorSummary: 'role=button[name="Date range: Today"]',
        frameSummary: 'Main frame',
        validationStatus: 'ok',
        validationDetails: 'selected Playwright candidate is strict unique',
      },
      index: 2,
    });

    expect(item.summary).toBe('定位器：role=button[name="Date range: Today"]');
    expect(item.summary).not.toContain('selected Playwright candidate');
    expect(item.technical.locator).toBe('role=button[name="Date range: Today"]');
    expect(item.technical.frame).toBe('Main frame');
    expect(item.technical.validation).toBe('selected Playwright candidate is strict unique');
  });

  it('uses URL as the collapsed summary when navigation has no locator', () => {
    const item = buildRpaStepTimelineItem({
      step: {
        id: 'step-4',
        action: 'navigate',
        description: '导航到 https://github.com/trending',
        url: 'https://github.com/trending',
        validation: { status: 'ok', details: 'Accepted manual action' },
      },
      index: 3,
    });

    expect(item.summary).toBe('页面：https://github.com/trending');
    expect(item.summaryLabel).toBe('页面');
    expect(item.summaryValue).toBe('https://github.com/trending');
  });
});

describe('formatRpaStepLocator', () => {
  it('formats legacy method-based locators without dumping JSON', () => {
    expect(formatRpaStepLocator({ method: 'role', role: 'button', name: 'Submit' })).toBe('role=button[name="Submit"]');
    expect(formatRpaStepLocator({
      method: 'nested',
      parent: { method: 'css', value: '#form' },
      child: { method: 'nth', locator: { method: 'text', value: 'Save' }, index: 0 },
    })).toBe('#form >> text:"Save" >> nth=0');
  });
});
