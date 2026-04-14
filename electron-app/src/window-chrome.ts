import type { BrowserWindowConstructorOptions } from 'electron';

export const DESKTOP_WINDOW_STATE_EVENT = 'desktop-window-state-changed';

export const DESKTOP_WINDOW_CHANNELS = {
  minimize: 'desktop-window:minimize',
  toggleMaximize: 'desktop-window:toggle-maximize',
  close: 'desktop-window:close',
  isMaximized: 'desktop-window:is-maximized',
} as const;

export function buildMainWindowChromeOptions(): BrowserWindowConstructorOptions {
  return {
    frame: false,
    autoHideMenuBar: true,
    titleBarStyle: 'hidden',
  };
}
