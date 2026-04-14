import { contextBridge, ipcRenderer } from 'electron';

// Sandboxed preload scripts cannot rely on local runtime requires.
const DESKTOP_WINDOW_CHANNELS = {
  minimize: 'desktop-window:minimize',
  toggleMaximize: 'desktop-window:toggle-maximize',
  close: 'desktop-window:close',
  isMaximized: 'desktop-window:is-maximized',
} as const;

const DESKTOP_WINDOW_STATE_EVENT = 'desktop-window-state-changed';

// Expose protected methods that allow the renderer process to use
// the ipcRenderer without exposing the entire object
contextBridge.exposeInMainWorld('electronAPI', {
  // Config
  getHomeDir: () => ipcRenderer.invoke('get-home-dir'),
  setHomeDir: (path: string) => ipcRenderer.invoke('set-home-dir', path),

  // Process status
  getBackendStatus: () => ipcRenderer.invoke('get-backend-status'),
  getTaskServiceStatus: () => ipcRenderer.invoke('get-task-service-status'),

  // App control
  restartApp: () => ipcRenderer.send('restart-app'),
  openExternal: (url: string) => ipcRenderer.send('open-external', url),
  desktopWindow: {
    minimize: () => ipcRenderer.send(DESKTOP_WINDOW_CHANNELS.minimize),
    toggleMaximize: () => ipcRenderer.send(DESKTOP_WINDOW_CHANNELS.toggleMaximize),
    close: () => ipcRenderer.send(DESKTOP_WINDOW_CHANNELS.close),
    isMaximized: () => ipcRenderer.invoke(DESKTOP_WINDOW_CHANNELS.isMaximized),
    onStateChanged: (callback: (state: { maximized: boolean }) => void) => {
      const listener = (_event: unknown, state: { maximized: boolean }) => callback(state);
      ipcRenderer.on(DESKTOP_WINDOW_STATE_EVENT, listener);
      return () => ipcRenderer.removeListener(DESKTOP_WINDOW_STATE_EVENT, listener);
    },
  },

  // Wizard
  getDefaultHomeDir: () => ipcRenderer.invoke('get-default-home-dir'),
  selectDirectory: () => ipcRenderer.invoke('select-directory'),
  validateHomeDir: (path: string) => ipcRenderer.invoke('validate-home-dir', path),
  initializeHomeDir: (path: string) => ipcRenderer.invoke('initialize-home-dir', path),
  wizardComplete: () => ipcRenderer.send('wizard-complete'),
  onWizardComplete: (callback: () => void) => {
    const listener = () => callback();
    ipcRenderer.on('wizard-complete', listener);
    return () => ipcRenderer.removeListener('wizard-complete', listener);
  },
});

// Type definitions for window.electronAPI
declare global {
  interface Window {
    electronAPI: {
      getHomeDir: () => Promise<string>;
      setHomeDir: (path: string) => Promise<void>;
      getBackendStatus: () => Promise<{ running: boolean; port: number }>;
      getTaskServiceStatus: () => Promise<{ running: boolean; port: number }>;
      restartApp: () => void;
      openExternal: (url: string) => void;
      desktopWindow: {
        minimize: () => void;
        toggleMaximize: () => void;
        close: () => void;
        isMaximized: () => Promise<boolean>;
        onStateChanged: (callback: (state: { maximized: boolean }) => void) => () => void;
      };
      getDefaultHomeDir: () => Promise<string>;
      selectDirectory: () => Promise<string | null>;
      validateHomeDir: (path: string) => Promise<{ valid: boolean; error?: string }>;
      initializeHomeDir: (path: string) => Promise<void>;
      wizardComplete: () => void;
      onWizardComplete: (callback: () => void) => () => void;
    };
  }
}
