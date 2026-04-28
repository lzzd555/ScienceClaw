import { describe, expect, it } from 'vitest';
import {
  getInlineBrowserPreviewSignalAction,
  shouldPollInlineBrowserPreviewSignal,
} from './inlineBrowserPreviewSignal';

describe('getInlineBrowserPreviewSignalAction', () => {
  it('shows the preview when browser tabs are present during a running turn', () => {
    expect(getInlineBrowserPreviewSignalAction({
      loading: true,
      dismissed: false,
      hasBrowserTabs: true,
      mode: 'none',
    })).toBe('show');
  });

  it('schedules hiding when a running turn loses its browser tabs', () => {
    expect(getInlineBrowserPreviewSignalAction({
      loading: true,
      dismissed: false,
      hasBrowserTabs: false,
      mode: 'browser',
    })).toBe('schedule-hide');
  });

  it('hides immediately when the preview was dismissed by the user', () => {
    expect(getInlineBrowserPreviewSignalAction({
      loading: true,
      dismissed: true,
      hasBrowserTabs: true,
      mode: 'browser',
    })).toBe('hide');
  });

  it('does nothing when there are no browser tabs and no visible preview', () => {
    expect(getInlineBrowserPreviewSignalAction({
      loading: true,
      dismissed: false,
      hasBrowserTabs: false,
      mode: 'none',
    })).toBe('noop');
  });

  it('hides immediately after the turn is no longer running and tabs are gone', () => {
    expect(getInlineBrowserPreviewSignalAction({
      loading: false,
      dismissed: false,
      hasBrowserTabs: false,
      mode: 'browser',
    })).toBe('hide');
  });
});

describe('shouldPollInlineBrowserPreviewSignal', () => {
  it('keeps polling in local mode even before loading starts or browser mode is visible', () => {
    expect(shouldPollInlineBrowserPreviewSignal({
      enabled: true,
      sessionId: 'session-1',
      dismissed: false,
    })).toBe(true);
  });

  it('stops polling after the user dismisses the preview', () => {
    expect(shouldPollInlineBrowserPreviewSignal({
      enabled: true,
      sessionId: 'session-1',
      dismissed: true,
    })).toBe(false);
  });

  it('stops polling when local browser preview signals are disabled', () => {
    expect(shouldPollInlineBrowserPreviewSignal({
      enabled: false,
      sessionId: 'session-1',
      dismissed: false,
    })).toBe(false);
  });
});
