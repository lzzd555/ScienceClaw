// @vitest-environment jsdom

import { createApp, nextTick, onMounted, ref } from 'vue';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}));

vi.mock('lucide-vue-next', () => {
  const icon = {
    template: '<span />',
  };
  return {
    X: icon,
    ChevronRight: icon,
    Zap: icon,
    Lightbulb: icon,
    ListChecks: icon,
    Wrench: icon,
  };
});

vi.mock('./icons/LoadingSpinnerIcon.vue', () => ({
  default: {
    template: '<span />',
  },
}));

vi.mock('../composables/useResizeObserver', () => ({
  useResizeObserver: () => ({
    size: ref(1200),
  }),
}));

vi.mock('../utils/eventBus', () => ({
  eventBus: {
    on: vi.fn(),
    off: vi.fn(),
    emit: vi.fn(),
  },
}));

vi.mock('../utils/mcpUi', () => ({
  isMcpToolMeta: () => false,
  formatMcpToolDisplayName: ({ functionName, fallbackName }: any) => functionName || fallbackName || '',
}));

vi.mock('./SandboxPreview.vue', () => ({
  default: {
    name: 'SandboxPreviewStub',
    props: ['mode', 'history', 'isLive', 'sessionId'],
    emits: ['close'],
    template: `
      <div data-testid="sandbox-preview" :data-mode="mode">
        <button data-testid="sandbox-close" @click="$emit('close')">close</button>
      </div>
    `,
  },
}));

async function mountActivityPanel(initialItems: any[]) {
  const { default: ActivityPanel } = await import('./ActivityPanel.vue');
  const root = document.createElement('div');
  document.body.appendChild(root);

  const items = ref(initialItems);

  const app = createApp({
    components: { ActivityPanel },
    setup() {
      const panelRef = ref<any>(null);
      onMounted(() => {
        panelRef.value?.show();
      });
      return { items, panelRef };
    },
    template: '<ActivityPanel ref="panelRef" :items="items" :isLoading="false" sessionId="session-1" />',
  });

  app.mount(root);
  await nextTick();
  await nextTick();

  return { app, root, items };
}

describe('ActivityPanel', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    vi.resetModules();
  });

  it('reopens terminal preview when the same terminal history appears after the preview was closed', async () => {
    const initialItems = [
      {
        id: 'tool-1',
        type: 'tool',
        timestamp: 1,
        tool: {
          tool_call_id: 'call-1',
          function: 'execute',
          name: 'execute',
          status: 'called',
          args: { command: 'ls downloads' },
          content: { output: 'contract.xlsx' },
        },
      },
    ];

    const { app, root, items } = await mountActivityPanel(initialItems);

    const preview = root.querySelector<HTMLElement>('[data-testid="sandbox-preview"]');
    expect(preview?.dataset.mode).toBe('terminal');

    root.querySelector<HTMLElement>('[data-testid="sandbox-close"]')
      ?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await nextTick();

    expect(preview?.dataset.mode).toBe('none');

    items.value = [
      {
        id: 'tool-2',
        type: 'tool',
        timestamp: 2,
        tool: {
          tool_call_id: 'call-2',
          function: 'execute',
          name: 'execute',
          status: 'called',
          args: { command: 'ls downloads' },
          content: { output: 'contract.xlsx' },
        },
      },
    ];
    await nextTick();
    await nextTick();

    expect(preview?.dataset.mode).toBe('terminal');

    app.unmount();
  });
});
