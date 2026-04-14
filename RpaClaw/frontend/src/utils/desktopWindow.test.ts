// @vitest-environment jsdom

import { createApp, nextTick } from 'vue';
import { afterEach, describe, expect, it, vi } from 'vitest';
import DesktopTitleBar from '@/components/DesktopTitleBar.vue';

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

describe('desktopWindow helper', () => {
  afterEach(() => {
    delete window.electronAPI;
    vi.restoreAllMocks();
  });

  it('fails soft when the electron desktop bridge is unavailable', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const { createDesktopWindowControls } = await import('./desktopWindow');

    const controls = createDesktopWindowControls();

    expect(warn).toHaveBeenCalledWith(
      '[desktopWindow] window.electronAPI.desktopWindow is unavailable; desktop window controls are disabled.'
    );
    expect(await controls.isMaximized()).toBe(false);
    expect(controls.minimize()).toBe(false);
    expect(await controls.toggleMaximize()).toBe(false);
    expect(controls.close()).toBe(false);
  });

  it('delegates desktop window actions through the existing electron bridge', async () => {
    const minimize = vi.fn();
    const toggleMaximize = vi.fn();
    const close = vi.fn();
    const isMaximized = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const unsubscribe = vi.fn();
    const onStateChanged = vi.fn().mockReturnValue(unsubscribe);

    window.electronAPI = {
      desktopWindow: {
        minimize,
        toggleMaximize,
        close,
        isMaximized,
        onStateChanged,
      },
    };

    const { createDesktopWindowControls } = await import('./desktopWindow');
    const controls = createDesktopWindowControls();

    expect(await controls.isMaximized()).toBe(false);
    expect(controls.minimize()).toBe(true);
    expect(await controls.toggleMaximize()).toBe(true);
    expect(await controls.toggleMaximize()).toBe(true);
    expect(await controls.isMaximized()).toBe(true);
    expect(controls.close()).toBe(true);

    expect(minimize).toHaveBeenCalledTimes(1);
    expect(toggleMaximize).toHaveBeenCalledTimes(2);
    expect(close).toHaveBeenCalledTimes(1);
    expect(isMaximized).toHaveBeenCalledTimes(2);
    expect(typeof controls.onStateChanged(() => undefined)).toBe('function');
    expect(onStateChanged).toHaveBeenCalledTimes(1);
  });

  it('treats incomplete desktop bridges as unsupported', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);

    window.electronAPI = {
      desktopWindow: {
        minimize: vi.fn(),
      },
    };

    const { createDesktopWindowControls } = await import('./desktopWindow');
    const controls = createDesktopWindowControls();

    expect(controls.isSupported).toBe(false);
    expect(warn).toHaveBeenCalledWith(
      '[desktopWindow] window.electronAPI.desktopWindow is unavailable; desktop window controls are disabled.'
    );
    expect(controls.minimize()).toBe(false);
    expect(await controls.toggleMaximize()).toBe(false);
    expect(controls.close()).toBe(false);
    expect(await controls.isMaximized()).toBe(false);
    expect(typeof controls.onStateChanged(() => undefined)).toBe('function');
  });

  it('keeps the desktop title bar visible when desktop controls are unsupported', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const root = document.createElement('div');
    document.body.appendChild(root);

    const app = createApp(DesktopTitleBar);
    app.mount(root);
    await nextTick();

    expect(warn).toHaveBeenCalledWith(
      '[desktopWindow] window.electronAPI.desktopWindow is unavailable; desktop window controls are disabled.'
    );
    expect(root.querySelector('header.desktop-title-bar')).not.toBeNull();
    expect(root.querySelectorAll('button').length).toBe(3);

    app.unmount();
    root.remove();
  });

  it('subscribes before the initial maximize sync and unsubscribes on unmount', async () => {
    let resolveIsMaximized: ((value: boolean) => void) | null = null;
    let stateChangedCallback: ((state: { maximized: boolean }) => void) | null = null;

    const unsubscribe = vi.fn();
    const isMaximized = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          resolveIsMaximized = resolve;
        })
    );
    const onStateChanged = vi.fn((callback: (state: { maximized: boolean }) => void) => {
      stateChangedCallback = callback;
      return unsubscribe;
    });

    window.electronAPI = {
      desktopWindow: {
        minimize: vi.fn(),
        toggleMaximize: vi.fn(),
        close: vi.fn(),
        isMaximized,
        onStateChanged,
      },
    };

    const root = document.createElement('div');
    document.body.appendChild(root);

    const app = createApp(DesktopTitleBar);
    app.mount(root);
    await nextTick();

    expect(onStateChanged).toHaveBeenCalledTimes(1);

    stateChangedCallback?.({ maximized: true });
    await nextTick();

    expect(root.querySelectorAll('button')[1]?.getAttribute('aria-label')).toBe('Restore window');

    resolveIsMaximized?.(true);
    await Promise.resolve();
    await nextTick();

    app.unmount();

    expect(unsubscribe).toHaveBeenCalledTimes(1);

    root.remove();
  });

  it('fails soft when bridge methods throw during actions or subscription', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);

    window.electronAPI = {
      desktopWindow: {
        minimize: vi.fn(() => {
          throw new Error('minimize failed');
        }),
        toggleMaximize: vi.fn(() => {
          throw new Error('toggle failed');
        }),
        close: vi.fn(() => {
          throw new Error('close failed');
        }),
        isMaximized: vi.fn(async () => {
          throw new Error('state failed');
        }),
        onStateChanged: vi.fn(() => {
          throw new Error('subscribe failed');
        }),
      },
    };

    const { createDesktopWindowControls } = await import('./desktopWindow');
    const controls = createDesktopWindowControls();

    expect(controls.minimize()).toBe(false);
    expect(await controls.toggleMaximize()).toBe(false);
    expect(controls.close()).toBe(false);
    expect(await controls.isMaximized()).toBe(false);
    expect(typeof controls.onStateChanged(() => undefined)).toBe('function');
    expect(warn).toHaveBeenCalled();
  });
});
