// @vitest-environment jsdom

import { createApp, nextTick } from 'vue';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('./components/ui/Toast.vue', () => ({
  default: {
    name: 'ToastStub',
    template: '<div data-testid="toast-stub" />',
  },
}));

vi.mock('./composables/useTheme', () => ({
  useTheme: () => ({
    initTheme: vi.fn(),
  }),
}));

declare global {
  interface Window {
    electronAPI?: {
      desktopWindow?: {
        minimize?: () => void;
        toggleMaximize?: () => void;
        close?: () => void;
        isMaximized?: () => boolean | Promise<boolean>;
        onStateChanged?: (callback: (state: { maximized: boolean }) => void) => () => void;
      };
    };
  }
}

async function mountApp() {
  const { default: App } = await import('./App.vue');
  const root = document.createElement('div');
  document.body.appendChild(root);

  const app = createApp(App);
  app.component('router-view', {
    template: '<div data-testid="router-view-stub" />',
  });
  app.mount(root);
  await nextTick();

  return { app, root };
}

describe('App desktop shell', () => {
  afterEach(() => {
    delete window.electronAPI;
    document.body.innerHTML = '';
    vi.restoreAllMocks();
    vi.resetModules();
  });

  it('does not render the desktop title bar in regular web environments', async () => {
    const { app, root } = await mountApp();

    expect(root.querySelector('header.desktop-title-bar')).toBeNull();

    app.unmount();
  });

  it('renders the desktop title bar when the electron desktop bridge is available', async () => {
    window.electronAPI = {
      desktopWindow: {
        minimize: vi.fn(),
        toggleMaximize: vi.fn(),
        close: vi.fn(),
        isMaximized: vi.fn().mockResolvedValue(false),
        onStateChanged: vi.fn().mockReturnValue(() => undefined),
      },
    };

    const { app, root } = await mountApp();

    expect(root.querySelector('header.desktop-title-bar')).not.toBeNull();

    app.unmount();
  });
});
