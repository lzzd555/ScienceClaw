import { describe, expect, it } from 'vitest';
import { buildActivitySandboxTerminalHistory } from './activitySandboxTerminal';

describe('buildActivitySandboxTerminalHistory', () => {
  it('builds calling and called entries for terminal tools', () => {
    expect(buildActivitySandboxTerminalHistory([
      {
        id: 'tool-1',
        type: 'tool',
        tool: {
          tool_call_id: 'call-1',
          function: 'execute',
          status: 'called',
          args: { command: 'ls workspace' },
          content: { output: 'downloads' },
        },
      },
    ])).toEqual([
      { toolName: 'execute', command: 'ls workspace', status: 'calling' },
      { toolName: 'execute', command: 'ls workspace', output: 'downloads', status: 'called' },
    ]);
  });

  it('rebuilds only the currently selected turn history', () => {
    const currentTurn = buildActivitySandboxTerminalHistory([
      {
        id: 'tool-2',
        type: 'tool',
        tool: {
          tool_call_id: 'call-2',
          function: 'execute',
          status: 'called',
          args: { command: 'ls downloads' },
          content: { output: 'contract.xlsx' },
        },
      },
    ]);

    expect(currentTurn).toEqual([
      { toolName: 'execute', command: 'ls downloads', status: 'calling' },
      { toolName: 'execute', command: 'ls downloads', output: 'contract.xlsx', status: 'called' },
    ]);
  });

  it('ignores non-terminal tools', () => {
    expect(buildActivitySandboxTerminalHistory([
      {
        id: 'tool-1',
        type: 'tool',
        tool: {
          tool_call_id: 'call-1',
          function: 'browser_click',
          status: 'called',
          args: { url: 'https://example.com' },
        },
      },
    ])).toEqual([]);
  });
});
