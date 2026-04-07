# Electron Install Dir Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the packaged Electron app load extra environment variables from an install-directory `.env` file and store `app-config.json` in the install directory root.

**Architecture:** Introduce a small runtime helper layer that separates bundled resource lookup from install-root user-editable files. Keep startup defaults unchanged, then merge install-directory `.env` values on top, and route `ConfigManager` to the new install-root config path.

**Tech Stack:** Electron, TypeScript, Node built-in test runner

---

### Task 1: Add failing tests for install-root path and env behavior

**Files:**
- Create: `electron-app/test/runtime.test.js`

- [ ] **Step 1: Write the failing test**

```javascript
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const runtime = require('../dist/runtime');

test('packaged mode resolves install-root config and env paths', () => {
  const paths = runtime.resolveRuntimePaths({
    isPackaged: true,
    execPath: 'C:\\Apps\\RpaClaw\\RpaClaw.exe',
    resourcesPath: 'C:\\Apps\\RpaClaw\\resources',
    currentDir: 'D:\\code\\MyScienceClaw\\electron-app\\dist',
  });

  assert.equal(paths.installRootDir, 'C:\\Apps\\RpaClaw');
  assert.equal(paths.configFilePath, 'C:\\Apps\\RpaClaw\\app-config.json');
  assert.equal(paths.envFilePath, 'C:\\Apps\\RpaClaw\\.env');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run build`
Run: `node --test test/runtime.test.js`
Expected: FAIL because `../dist/runtime` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```typescript
export function resolveRuntimePaths(...) { ... }
export function parseEnvContent(...) { ... }
export function buildBackendEnv(...) { ... }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run build`
Run: `node --test test/runtime.test.js`
Expected: PASS

### Task 2: Wire runtime helpers into Electron startup

**Files:**
- Create: `electron-app/src/runtime.ts`
- Modify: `electron-app/src/config.ts`
- Modify: `electron-app/src/process-manager.ts`

- [ ] **Step 1: Update `ConfigManager` to use install-root config path**

```typescript
const runtimePaths = resolveRuntimePaths({
  isPackaged: app.isPackaged,
  execPath: process.execPath,
  resourcesPath: process.resourcesPath,
  currentDir: __dirname,
});

this.configPath = runtimePaths.configFilePath;
```

- [ ] **Step 2: Update `ProcessManager` to separate install root from resource dir**

```typescript
const runtimePaths = resolveRuntimePaths({
  isPackaged: app.isPackaged,
  execPath: process.execPath,
  resourcesPath: process.resourcesPath,
  currentDir: __dirname,
});

this.resourceDir = runtimePaths.resourceDir;
```

- [ ] **Step 3: Merge install-root `.env` into backend env**

```typescript
const extraEnv = loadEnvFile(this.runtimePaths.envFilePath);
return buildBackendEnv({
  homeDir: this.homeDir,
  resourceDir: this.resourceDir,
  extraEnv,
});
```

- [ ] **Step 4: Run build and focused tests**

Run: `npm run build`
Run: `node --test test/runtime.test.js`
Expected: PASS

### Task 3: Final regression verification

**Files:**
- Modify: `electron-app/README.md`

- [ ] **Step 1: Document install-directory `.env` and `app-config.json` behavior**

```markdown
- Packaged app reads extra variables from `<install-dir>/.env`
- Setup wizard writes `<install-dir>/app-config.json`
```

- [ ] **Step 2: Run full verification**

Run: `npm run build`
Run: `node --test test/runtime.test.js`
Expected: PASS with all tests green
