import { app, BrowserWindow, ipcMain, dialog, Tray, Menu, shell } from 'electron';
import type { Event } from 'electron';
import * as fs from 'node:fs';
import * as path from 'path';
import { ConfigManager } from './config';
import { buildRelaunchArgs, getStartupDelayMs } from './launch-context';
import { ProcessManager } from './process-manager';
import {
  buildMainWindowChromeOptions,
  DESKTOP_WINDOW_CHANNELS,
  DESKTOP_WINDOW_STATE_EVENT,
} from './window-chrome';
import { normalizeExternalUrl } from './url-utils';

let mainWindow: BrowserWindow | null = null;
let wizardWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let configManager: ConfigManager;
let processManager: ProcessManager | null = null;
let isQuitting = false;
let cleanupPromise: Promise<void> | null = null;

function isMainWindowMaximizedOrFullScreen() {
  if (!mainWindow) {
    return false;
  }

  return mainWindow.isMaximized() || mainWindow.isFullScreen();
}

function emitMainWindowState() {
  if (!mainWindow || mainWindow.webContents.isDestroyed()) {
    return;
  }

  const maximizedState = isMainWindowMaximizedOrFullScreen();
  mainWindow.webContents.send(DESKTOP_WINDOW_STATE_EVENT, {
    maximized: maximizedState,
  });
}

function registerMainWindowStateEvents(window: BrowserWindow) {
  const emit = () => emitMainWindowState();
  window.on('maximize', emit);
  window.on('unmaximize', emit);
  window.on('enter-full-screen', emit);
  window.on('leave-full-screen', emit);
}

// In development, use frontend dev server; in production, use backend
const FRONTEND_URL = app.isPackaged
  ? 'http://127.0.0.1:12001'
  : 'http://localhost:5173';

/**
 * Create the wizard window
 */
function createWizardWindow() {
  wizardWindow = new BrowserWindow({
    width: 600,
    height: 500,
    resizable: false,
    frame: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  wizardWindow.loadFile(path.join(__dirname, 'wizard', 'wizard.html'));

  wizardWindow.on('closed', () => {
    wizardWindow = null;
  });
}

/**
 * Create the main application window
 */
function showAndFocusMainWindow() {
  if (!mainWindow) {
    return;
  }

  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }

  mainWindow.show();
  mainWindow.focus();
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    ...buildMainWindowChromeOptions(),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.setMenuBarVisibility(false);
  mainWindow.setAutoHideMenuBar(true);
  registerMainWindowStateEvents(mainWindow);

  // Load frontend URL
  mainWindow.loadURL(FRONTEND_URL);

  mainWindow.on('close', (event) => {
    if (isQuitting) {
      return;
    }

    if (!tray) {
      return;
    }

    event.preventDefault();
    mainWindow?.hide();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

/**
 * Create system tray icon
 */
function requestAppQuit() {
  if (isQuitting) {
    return;
  }

  isQuitting = true;
  app.quit();
}

function createTray() {
  if (tray) {
    return;
  }

  const iconPath = path.join(__dirname, '..', 'resources', 'icon.ico');
  if (!fs.existsSync(iconPath)) {
    console.warn(`Tray icon not found at ${iconPath}; tray icon creation skipped.`);
    return;
  }

  try {
    tray = new Tray(iconPath);
  } catch (error) {
    console.error('Failed to create tray icon', error);
    return;
  }

  const contextMenu = Menu.buildFromTemplate([
    {
      label: 'Show RpaClaw',
      click: () => {
        showAndFocusMainWindow();
      },
    },
    {
      label: 'Quit',
      click: () => {
        requestAppQuit();
      },
    },
  ]);

  tray.setToolTip('RpaClaw');
  tray.setContextMenu(contextMenu);

  tray.on('click', () => {
    showAndFocusMainWindow();
  });
}

/**
 * Initialize application
 */
async function initialize() {
  configManager = new ConfigManager();

  if (configManager.isFirstRun()) {
    // Show wizard
    createWizardWindow();
  } else {
    // Load config and start services
    const config = configManager.load();
    if (!config) {
      console.error('Failed to load config');
      app.quit();
      return;
    }

    // Start backend services
    processManager = new ProcessManager(config.homeDir);
    try {
      await processManager.startBackend();
      await processManager.startTaskService();
    } catch (error) {
      console.error('Failed to start services:', error);
      dialog.showErrorBox('Startup Error', `Failed to start services: ${error}`);
      app.quit();
      return;
    }

    // Create main window and tray
    createMainWindow();
    createTray();
  }
}

async function handleAppReady() {
  const startupDelayMs = getStartupDelayMs(process.argv);
  if (startupDelayMs > 0) {
    console.log(`Installer launch detected, delaying startup by ${startupDelayMs}ms`);
    await new Promise((resolve) => setTimeout(resolve, startupDelayMs));
  }

  await initialize();
}

// App lifecycle
app.on('ready', () => {
  void handleAppReady();
});

app.on('window-all-closed', () => {
  // On macOS, keep app running when all windows closed
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (!configManager) {
    return;
  }

  if (configManager.isFirstRun()) {
    if (wizardWindow) {
      wizardWindow.show();
    } else {
      createWizardWindow();
    }
    return;
  }

  if (mainWindow === null) {
    createMainWindow();
    if (!tray) {
      createTray();
    }
  } else {
    showAndFocusMainWindow();
    if (!tray) {
      createTray();
    }
  }
});

function scheduleCleanupAndQuit() {
  if (!processManager || cleanupPromise) {
    return;
  }

  cleanupPromise = processManager
    .stopAll()
    .catch((error) => {
      console.error('Failed to stop services before quit:', error);
    })
    .finally(() => {
      cleanupPromise = null;
      app.removeListener('before-quit', handleBeforeQuit);
      app.quit();
    });
}

function handleBeforeQuit(event: Event) {
  if (cleanupPromise) {
    event.preventDefault();
    return;
  }

  isQuitting = true;

  if (!processManager) {
    return;
  }

  event.preventDefault();
  scheduleCleanupAndQuit();
}

app.on('before-quit', handleBeforeQuit);

// IPC Handlers

// Config
ipcMain.handle('get-home-dir', () => {
  const config = configManager.get();
  return config?.homeDir || '';
});

ipcMain.handle('set-home-dir', async (event, newPath: string) => {
  const config = configManager.get();
  if (config) {
    config.homeDir = newPath;
    configManager.save(config);

    // Restart required
    dialog.showMessageBox({
      type: 'info',
      title: 'Restart Required',
      message: 'Please restart RpaClaw for changes to take effect.',
      buttons: ['OK'],
    });
  }
});

// Process status
ipcMain.handle('get-backend-status', () => {
  return processManager?.getBackendStatus() || { running: false, port: 12001 };
});

ipcMain.handle('get-task-service-status', () => {
  return processManager?.getTaskServiceStatus() || { running: false, port: 12002 };
});

// App control
ipcMain.on('restart-app', () => {
  app.relaunch({ args: buildRelaunchArgs(process.argv) });
  app.quit();
});

ipcMain.on('open-external', async (event, externalUrl: string) => {
  const normalized = normalizeExternalUrl(externalUrl);
  if (!normalized) {
    console.warn(`Blocked external URL with disallowed protocol or invalid URL: ${externalUrl}`);
    return;
  }

  try {
    await shell.openExternal(normalized);
  } catch (error) {
    console.warn('open-external failed', normalized, error);
  }
});

ipcMain.on(DESKTOP_WINDOW_CHANNELS.minimize, () => {
  mainWindow?.minimize();
});

ipcMain.on(DESKTOP_WINDOW_CHANNELS.toggleMaximize, () => {
  if (!mainWindow) {
    return;
  }

  if (mainWindow.isFullScreen()) {
    mainWindow.setFullScreen(false);
    return;
  }

  if (mainWindow.isMaximized()) {
    mainWindow.unmaximize();
  } else {
    mainWindow.maximize();
  }
});

ipcMain.on(DESKTOP_WINDOW_CHANNELS.close, () => {
  mainWindow?.close();
});

ipcMain.handle(DESKTOP_WINDOW_CHANNELS.isMaximized, () => {
  return isMainWindowMaximizedOrFullScreen();
});

// Wizard
ipcMain.handle('get-default-home-dir', () => {
  return configManager.getDefaultHomeDir();
});

ipcMain.handle('select-directory', async () => {
  const result = await dialog.showOpenDialog({
    properties: ['openDirectory', 'createDirectory'],
    title: 'Select Home Directory',
  });

  if (result.canceled) {
    return null;
  }

  return result.filePaths[0];
});

ipcMain.handle('validate-home-dir', (event, dirPath: string) => {
  return configManager.validateHomeDir(dirPath);
});

ipcMain.handle('initialize-home-dir', async (event, dirPath: string) => {
  try {
    configManager.initializeHomeDir(dirPath);

    // Save config
    const config = {
      homeDir: dirPath,
      version: app.getVersion(),
    };
    configManager.save(config);

    return { success: true };
  } catch (error) {
    throw new Error(`Initialization failed: ${error}`);
  }
});

ipcMain.on('wizard-complete', () => {
  // Close wizard and relaunch app so it goes through normal initialize() path
  wizardWindow?.close();
  app.relaunch({ args: buildRelaunchArgs(process.argv) });
  app.quit();
});
