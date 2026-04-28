// @vitest-environment jsdom

import { createApp, nextTick, ref } from 'vue';
import { afterEach, describe, expect, it, vi } from 'vitest';

const terminalInstances: any[] = [];

vi.mock('@xterm/xterm', () => {
  class Terminal {
    output: string[] = [];
    resetCalls = 0;

    constructor() {
      terminalInstances.push(this);
    }

    loadAddon() {}
    open() {}
    writeln(line: string) {
      this.output.push(line);
    }
    reset() {
      this.output = [];
      this.resetCalls += 1;
    }
    scrollToBottom() {}
    dispose() {}
  }

  return { Terminal };
});

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class {
    fit() {}
  },
}));

class ResizeObserverMock {
  observe() {}
  disconnect() {}
}

async function mountSandboxTerminal(initialHistory: Array<Record<string, any>>) {
  const { default: SandboxTerminal } = await import('./SandboxTerminal.vue');
  const root = document.createElement('div');
  document.body.appendChild(root);

  const history = ref(initialHistory);
  const active = ref(true);

  const app = createApp({
    components: { SandboxTerminal },
    setup() {
      return { active, history };
    },
    template: '<SandboxTerminal :active="active" :history="history" />',
  });

  app.mount(root);
  await nextTick();
  await nextTick();

  return { app, root, history, active };
}

describe('SandboxTerminal', () => {
  afterEach(() => {
    terminalInstances.length = 0;
    document.body.innerHTML = '';
    vi.resetModules();
    vi.unstubAllGlobals();
  });

  it('rerenders the terminal when history changes but keeps the same entry count', async () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);

    const { app, history } = await mountSandboxTerminal([
      { toolName: 'execute', command: 'python old.py', status: 'calling' },
      { toolName: 'execute', command: 'python old.py', output: 'old output', status: 'called' },
    ]);

    const terminal = terminalInstances[0];
    expect(terminal.output.join('\n')).toContain('python old.py');

    history.value = [
      { toolName: 'execute', command: 'python new.py', status: 'calling' },
      { toolName: 'execute', command: 'python new.py', output: 'new output', status: 'called' },
    ];

    await nextTick();
    await nextTick();

    expect(terminal.resetCalls).toBeGreaterThan(0);
    expect(terminal.output.join('\n')).toContain('python new.py');
    expect(terminal.output.join('\n')).toContain('new output');
    expect(terminal.output.join('\n')).not.toContain('python old.py');

    app.unmount();
  });

  it('clears prior output when the selected turn has no terminal history', async () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);

    const { app, history } = await mountSandboxTerminal([
      { toolName: 'execute', command: 'python old.py', status: 'calling' },
      { toolName: 'execute', command: 'python old.py', output: 'old output', status: 'called' },
    ]);

    const terminal = terminalInstances[0];
    expect(terminal.output.length).toBeGreaterThan(0);

    history.value = [];
    await nextTick();
    await nextTick();

    expect(terminal.resetCalls).toBeGreaterThan(0);
    expect(terminal.output).toEqual([]);

    app.unmount();
  });
});
