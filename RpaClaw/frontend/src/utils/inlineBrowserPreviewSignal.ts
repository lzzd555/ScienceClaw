import type { InlineSandboxPreviewMode } from './inlineSandboxPreview';

export type InlineBrowserPreviewSignalAction = 'show' | 'schedule-hide' | 'hide' | 'noop';

export interface InlineBrowserPreviewSignalInput {
  loading: boolean;
  dismissed: boolean;
  hasBrowserTabs: boolean;
  mode: InlineSandboxPreviewMode;
}

export interface InlineBrowserPreviewPollingInput {
  enabled: boolean;
  sessionId: string | null | undefined;
  dismissed: boolean;
}

export const shouldPollInlineBrowserPreviewSignal = (
  input: InlineBrowserPreviewPollingInput,
): boolean => (
  input.enabled
  && Boolean(input.sessionId)
  && !input.dismissed
);

export const getInlineBrowserPreviewSignalAction = (
  input: InlineBrowserPreviewSignalInput,
): InlineBrowserPreviewSignalAction => {
  if (input.dismissed) {
    return input.mode === 'browser' ? 'hide' : 'noop';
  }

  if (input.hasBrowserTabs) {
    return 'show';
  }

  if (input.mode !== 'browser') {
    return 'noop';
  }

  return input.loading ? 'schedule-hide' : 'hide';
};
