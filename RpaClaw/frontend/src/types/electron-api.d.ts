interface DesktopWindowState {
  maximized: boolean;
}

interface DesktopWindowApi {
  minimize: () => void;
  toggleMaximize: () => void;
  close: () => void;
  isMaximized: () => Promise<boolean>;
  onStateChanged: (callback: (state: DesktopWindowState) => void) => () => void;
}

interface ElectronApi {
  getHomeDir: () => Promise<string>;
  setHomeDir: (path: string) => Promise<void>;
  getBackendStatus: () => Promise<{ running: boolean; port: number }>;
  getTaskServiceStatus: () => Promise<{ running: boolean; port: number }>;
  restartApp: () => void;
  openExternal: (url: string) => void;
  desktopWindow: DesktopWindowApi;
  getDefaultHomeDir: () => Promise<string>;
  selectDirectory: () => Promise<string | null>;
  validateHomeDir: (path: string) => Promise<{ valid: boolean; error?: string }>;
  initializeHomeDir: (path: string) => Promise<void>;
  wizardComplete: () => void;
  onWizardComplete: (callback: () => void) => () => void;
}

declare global {
  interface Window {
    electronAPI?: ElectronApi;
  }
}

export {};
