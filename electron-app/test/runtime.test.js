const assert = require('node:assert/strict');
const path = require('node:path');
const runtime = require('../dist/runtime');

function runTest(name, fn) {
  try {
    fn();
    console.log(`PASS ${name}`);
  } catch (error) {
    console.error(`FAIL ${name}`);
    throw error;
  }
}

runTest('packaged mode resolves install-root config and env paths', () => {
  const runtimePaths = runtime.resolveRuntimePaths({
    isPackaged: true,
    execPath: 'C:\\Apps\\RpaClaw\\RpaClaw.exe',
    resourcesPath: 'C:\\Apps\\RpaClaw\\resources',
    currentDir: 'D:\\code\\MyScienceClaw\\electron-app\\dist',
  });

  assert.equal(runtimePaths.installRootDir, 'C:\\Apps\\RpaClaw');
  assert.equal(runtimePaths.resourceDir, 'C:\\Apps\\RpaClaw\\resources');
  assert.equal(runtimePaths.configFilePath, 'C:\\Apps\\RpaClaw\\app-config.json');
  assert.equal(runtimePaths.envFilePath, 'C:\\Apps\\RpaClaw\\.env');
});

runTest('env parsing ignores comments and strips quotes', () => {
  const parsed = runtime.parseEnvContent(
    ['# comment', 'BACKEND_PORT=13001', 'LOG_LEVEL="DEBUG"', "CUSTOM_VALUE='hello world'"].join(
      '\n'
    )
  );

  assert.deepEqual(parsed, {
    BACKEND_PORT: '13001',
    LOG_LEVEL: 'DEBUG',
    CUSTOM_VALUE: 'hello world',
  });
});

runTest('extra env overrides defaults and preserves built-in resource paths', () => {
  const resourceDir = 'C:\\Apps\\RpaClaw\\resources';
  const env = runtime.buildBackendEnv({
    homeDir: 'D:\\Users\\Alice\\RpaClaw',
    resourceDir,
    extraEnv: {
      BACKEND_PORT: '13001',
      FEATURE_FLAG: 'enabled',
    },
  });

  assert.equal(env.BACKEND_PORT, '13001');
  assert.equal(env.FEATURE_FLAG, 'enabled');
  assert.equal(env.BUILTIN_SKILLS_DIR, path.join(resourceDir, 'builtin_skills'));
});

console.log('All runtime tests passed');
