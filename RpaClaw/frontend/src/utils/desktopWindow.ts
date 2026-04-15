type DesktopWindowState = {
  maximized: boolean;
};

type DesktopWindowControls = {
  isSupported: boolean;
  minimize: () => boolean;
  toggleMaximize: () => Promise<boolean>;
  close: () => boolean;
  isMaximized: () => Promise<boolean>;
  onStateChanged: (callback: (state: DesktopWindowState) => void) => () => void;
};

type DesktopWindowApi = NonNullable<Window['electronAPI']>['desktopWindow'];

const getDesktopWindowApi = (target: Window = window) => target.electronAPI?.desktopWindow;
const REQUIRED_METHODS = ['minimize', 'toggleMaximize', 'close', 'isMaximized', 'onStateChanged'] as const;

const warnUnavailable = () => {
  if (import.meta.env.DEV) {
    console.warn(
      '[desktopWindow] window.electronAPI.desktopWindow is unavailable; desktop window controls are disabled.'
    );
  }
};

const warnBridgeError = (action: string, error: unknown) => {
  if (import.meta.env.DEV) {
    console.warn(`[desktopWindow] ${action} failed; desktop window controls are disabled.`, error);
  }
};

const isSupportedDesktopWindowApi = (
  desktopWindow: Window['electronAPI'] extends { desktopWindow: infer T } ? T | undefined : never
): desktopWindow is DesktopWindowApi =>
  Boolean(
    desktopWindow &&
    REQUIRED_METHODS.every((method) => typeof desktopWindow[method] === 'function')
  );

export function hasDesktopWindowControls(target: Window = window): boolean {
  return isSupportedDesktopWindowApi(getDesktopWindowApi(target));
}

export function createDesktopWindowControls(): DesktopWindowControls {
  const desktopWindow = getDesktopWindowApi();
  const supportedDesktopWindow = isSupportedDesktopWindowApi(desktopWindow) ? desktopWindow : undefined;

  if (!supportedDesktopWindow) {
    warnUnavailable();
  }

  return {
    isSupported: Boolean(supportedDesktopWindow),
    minimize() {
      if (!supportedDesktopWindow) {
        return false;
      }

      try {
        supportedDesktopWindow.minimize();
        return true;
      } catch (error) {
        warnBridgeError('minimize', error);
        return false;
      }
    },
    async toggleMaximize() {
      if (!supportedDesktopWindow) {
        return false;
      }

      try {
        supportedDesktopWindow.toggleMaximize();
        return true;
      } catch (error) {
        warnBridgeError('toggleMaximize', error);
        return false;
      }
    },
    close() {
      if (!supportedDesktopWindow) {
        return false;
      }

      try {
        supportedDesktopWindow.close();
        return true;
      } catch (error) {
        warnBridgeError('close', error);
        return false;
      }
    },
    async isMaximized() {
      if (!supportedDesktopWindow) {
        return false;
      }

      try {
        return await supportedDesktopWindow.isMaximized();
      } catch {
        return false;
      }
    },
    onStateChanged(callback) {
      if (!supportedDesktopWindow) {
        return () => undefined;
      }

      try {
        return supportedDesktopWindow.onStateChanged(callback);
      } catch (error) {
        warnBridgeError('onStateChanged', error);
        return () => undefined;
      }
    },
  };
}
