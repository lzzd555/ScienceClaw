const assert = require('node:assert/strict');
const Module = require('node:module');
const windowChrome = require('../dist/window-chrome');

function runTest(name, fn) {
  try {
    fn();
    console.log(`PASS ${name}`);
  } catch (error) {
    console.error(`FAIL ${name}`);
    throw error;
  }
}

runTest('main window chrome config is frameless and hides the default menu bar', () => {
  const options = windowChrome.buildMainWindowChromeOptions();
  assert.equal(options.frame, false);
  assert.equal(options.autoHideMenuBar, true);
  assert.equal(options.titleBarStyle, 'hidden');
});

runTest('desktop window channels stay stable', () => {
  assert.equal(windowChrome.DESKTOP_WINDOW_CHANNELS.minimize, 'desktop-window:minimize');
  assert.equal(windowChrome.DESKTOP_WINDOW_CHANNELS.toggleMaximize, 'desktop-window:toggle-maximize');
  assert.equal(windowChrome.DESKTOP_WINDOW_CHANNELS.close, 'desktop-window:close');
  assert.equal(windowChrome.DESKTOP_WINDOW_CHANNELS.isMaximized, 'desktop-window:is-maximized');
});

runTest('desktop window state event name is stable', () => {
  assert.equal(windowChrome.DESKTOP_WINDOW_STATE_EVENT, 'desktop-window-state-changed');
});

runTest('preload exposes desktop window controls on window.electronAPI', () => {
  const originalLoad = Module._load;
  const preloadPath = require.resolve('../dist/preload.js');
  const captured = {
    api: null,
    invokeCalls: [],
    sendCalls: [],
    onCalls: [],
    removeListenerCalls: [],
  };

  const ipcRenderer = {
    invoke(channel, ...args) {
      captured.invokeCalls.push([channel, ...args]);
      return channel;
    },
    send(channel, ...args) {
      captured.sendCalls.push([channel, ...args]);
    },
    on(channel, listener) {
      captured.onCalls.push([channel, listener]);
      return ipcRenderer;
    },
    removeListener(channel, listener) {
      captured.removeListenerCalls.push([channel, listener]);
      return ipcRenderer;
    },
  };

  Module._load = function patchedLoad(request, parent, isMain) {
    if (request === 'electron') {
      return {
        contextBridge: {
          exposeInMainWorld(name, api) {
            captured.api = { name, api };
          },
        },
        ipcRenderer,
      };
    }

    return originalLoad(request, parent, isMain);
  };

  delete require.cache[preloadPath];

  try {
    require(preloadPath);
  } finally {
    Module._load = originalLoad;
    delete require.cache[preloadPath];
  }

  assert.ok(captured.api, 'preload should expose an API');
  assert.equal(captured.api.name, 'electronAPI');
  assert.ok(captured.api.api.desktopWindow, 'desktopWindow API should exist');

  const { desktopWindow } = captured.api.api;
  const stateListener = () => {};

  assert.equal(desktopWindow.minimize(), undefined);
  assert.equal(desktopWindow.toggleMaximize(), undefined);
  assert.equal(desktopWindow.close(), undefined);
  assert.equal(
    desktopWindow.isMaximized(),
    windowChrome.DESKTOP_WINDOW_CHANNELS.isMaximized,
  );

  const unsubscribe = desktopWindow.onStateChanged(stateListener);

  assert.deepEqual(captured.sendCalls, [
    [windowChrome.DESKTOP_WINDOW_CHANNELS.minimize],
    [windowChrome.DESKTOP_WINDOW_CHANNELS.toggleMaximize],
    [windowChrome.DESKTOP_WINDOW_CHANNELS.close],
  ]);
  assert.deepEqual(captured.invokeCalls, [
    [windowChrome.DESKTOP_WINDOW_CHANNELS.isMaximized],
  ]);
  assert.equal(captured.onCalls.length, 1);
  assert.equal(captured.onCalls[0][0], windowChrome.DESKTOP_WINDOW_STATE_EVENT);
  assert.equal(typeof captured.onCalls[0][1], 'function');
  assert.equal(typeof unsubscribe, 'function');

  let receivedState = null;
  captured.onCalls[0][1]({}, { maximized: true });
  desktopWindow.onStateChanged((state) => {
    receivedState = state;
  });
  captured.onCalls[1][1]({}, { maximized: false });

  assert.deepEqual(receivedState, { maximized: false });

  unsubscribe();

  assert.equal(captured.removeListenerCalls.length, 1);
  assert.equal(captured.removeListenerCalls[0][0], windowChrome.DESKTOP_WINDOW_STATE_EVENT);
  assert.equal(captured.removeListenerCalls[0][1], captured.onCalls[0][1]);
});

runTest('preload exposes onWizardComplete with unsubscribe support', () => {
  const originalLoad = Module._load;
  const preloadPath = require.resolve('../dist/preload.js');
  const captured = {
    api: null,
    onCalls: [],
    removeListenerCalls: [],
  };

  const ipcRenderer = {
    invoke() {
      throw new Error('invoke should not be used in this test');
    },
    send() {
      throw new Error('send should not be used in this test');
    },
    on(channel, listener) {
      captured.onCalls.push([channel, listener]);
      return ipcRenderer;
    },
    removeListener(channel, listener) {
      captured.removeListenerCalls.push([channel, listener]);
      return ipcRenderer;
    },
  };

  Module._load = function patchedLoad(request, parent, isMain) {
    if (request === 'electron') {
      return {
        contextBridge: {
          exposeInMainWorld(name, api) {
            captured.api = { name, api };
          },
        },
        ipcRenderer,
      };
    }

    return originalLoad(request, parent, isMain);
  };

  delete require.cache[preloadPath];

  try {
    require(preloadPath);
  } finally {
    Module._load = originalLoad;
    delete require.cache[preloadPath];
  }

  assert.ok(captured.api, 'preload should expose an API');
  assert.equal(captured.api.name, 'electronAPI');
  assert.equal(typeof captured.api.api.onWizardComplete, 'function');

  let callbackCallCount = 0;
  const unsubscribe = captured.api.api.onWizardComplete(() => {
    callbackCallCount += 1;
  });

  assert.equal(captured.onCalls.length, 1);
  assert.equal(captured.onCalls[0][0], 'wizard-complete');
  assert.equal(typeof captured.onCalls[0][1], 'function');
  assert.equal(typeof unsubscribe, 'function');

  captured.onCalls[0][1]({ sender: 'electron' });
  assert.equal(callbackCallCount, 1);

  unsubscribe();

  assert.equal(captured.removeListenerCalls.length, 1);
  assert.equal(captured.removeListenerCalls[0][0], 'wizard-complete');
  assert.equal(captured.removeListenerCalls[0][1], captured.onCalls[0][1]);
});

runTest('preload loads in sandbox-compatible mode without local module requires', () => {
  const originalLoad = Module._load;
  const preloadPath = require.resolve('../dist/preload.js');
  const captured = {
    api: null,
  };

  Module._load = function patchedLoad(request, parent, isMain) {
    if (request === 'electron') {
      return {
        contextBridge: {
          exposeInMainWorld(name, api) {
            captured.api = { name, api };
          },
        },
        ipcRenderer: {
          invoke() {
            return undefined;
          },
          send() {},
          on() {},
          removeListener() {},
        },
      };
    }

    if (request === './window-chrome') {
      const error = new Error(`Cannot find module '${request}'`);
      error.code = 'MODULE_NOT_FOUND';
      throw error;
    }

    return originalLoad(request, parent, isMain);
  };

  delete require.cache[preloadPath];

  try {
    require(preloadPath);
  } finally {
    Module._load = originalLoad;
    delete require.cache[preloadPath];
  }

  assert.ok(captured.api, 'preload should still expose an API without local runtime requires');
  assert.equal(captured.api.name, 'electronAPI');
});

console.log('All window-chrome tests passed');
