import { describe, expect, it } from 'vitest';
import {
  getInlineSandboxPreviewMode,
  hasActiveInlineSandboxPreviewTool,
} from './inlineSandboxPreview';

describe('getInlineSandboxPreviewMode', () => {
  it('returns browser when a browser tool is active in the current turn', () => {
    expect(getInlineSandboxPreviewMode([
      {
        type: 'tool',
        tool: {
          function: 'browser_click',
          name: 'browser_click',
          status: 'calling',
        },
      },
    ])).toBe('browser');
  });

  it('does not return browser for sandbox terminal tools', () => {
    expect(getInlineSandboxPreviewMode([
      {
        type: 'tool',
        tool: {
          function: 'execute',
          name: 'execute',
          status: 'calling',
        },
      },
    ])).toBe('none');
  });

  it('does not return browser for sandbox proxy tools that are not browser actions', () => {
    expect(getInlineSandboxPreviewMode([
      {
        type: 'tool',
        tool: {
          function: 'custom_tool',
          name: 'custom_tool',
          status: 'calling',
          tool_meta: {
            sandbox: true,
          },
        },
      },
    ])).toBe('none');
  });

  it('keeps browser mode while the browser tool result is still the latest sandbox preview source', () => {
    expect(getInlineSandboxPreviewMode([
      {
        type: 'tool',
        tool: {
          function: 'browser_click',
          name: 'browser_click',
          status: 'called',
        },
      },
    ])).toBe('browser');
  });

  it('returns none when a non-browser tool is newer than a browser tool', () => {
    expect(getInlineSandboxPreviewMode([
      {
        type: 'tool',
        tool: {
          function: 'browser_click',
          name: 'browser_click',
          status: 'called',
        },
      },
      {
        type: 'tool',
        tool: {
          function: 'execute',
          name: 'execute',
          status: 'calling',
        },
      },
    ])).toBe('none');
  });

  it('returns browser for sandbox browser tools', () => {
    expect(getInlineSandboxPreviewMode([
      {
        type: 'tool',
        tool: {
          function: 'sandbox_browser_execute_action',
          name: 'sandbox_browser_execute_action',
          status: 'calling',
        },
      },
    ])).toBe('browser');
  });
});

describe('hasActiveInlineSandboxPreviewTool', () => {
  it('treats a calling browser tool as still actively running', () => {
    expect(hasActiveInlineSandboxPreviewTool([
      {
        type: 'tool',
        tool: {
          function: 'browser_click',
          name: 'browser_click',
          status: 'calling',
        },
      },
    ])).toBe(true);
  });

  it('does not treat sandbox terminal tools as active browser previews', () => {
    expect(hasActiveInlineSandboxPreviewTool([
      {
        type: 'tool',
        tool: {
          function: 'sandbox_execute_bash',
          name: 'sandbox_execute_bash',
          status: 'calling',
        },
      },
    ])).toBe(false);
  });

  it('stops treating browser preview as active when a newer non-browser tool starts', () => {
    expect(hasActiveInlineSandboxPreviewTool([
      {
        type: 'tool',
        tool: {
          function: 'browser_click',
          name: 'browser_click',
          status: 'calling',
        },
      },
      {
        type: 'tool',
        tool: {
          function: 'execute',
          name: 'execute',
          status: 'calling',
        },
      },
    ])).toBe(false);
  });

  it('stops treating the preview as active once relevant tools are all called', () => {
    expect(hasActiveInlineSandboxPreviewTool([
      {
        type: 'tool',
        tool: {
          function: 'browser_click',
          name: 'browser_click',
          status: 'called',
        },
      },
      {
        type: 'thinking',
      },
    ])).toBe(false);
  });
});
