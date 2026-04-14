const assert = require('node:assert/strict');
const launchContext = require('../dist/launch-context');

function runTest(name, fn) {
  try {
    fn();
    console.log(`PASS ${name}`);
  } catch (error) {
    console.error(`FAIL ${name}`);
    throw error;
  }
}

runTest('installer launch flag triggers a startup delay', () => {
  assert.equal(launchContext.getStartupDelayMs(['RpaClaw.exe', '--updated']), 5000);
  assert.equal(launchContext.getStartupDelayMs(['RpaClaw.exe']), 0);
});

runTest('installer launch flag uses extended service startup timeout', () => {
  assert.equal(launchContext.getServiceStartupTimeoutMs(['RpaClaw.exe', '--updated']), 120000);
  assert.equal(launchContext.getServiceStartupTimeoutMs(['RpaClaw.exe']), 30000);
});

runTest('relaunch args drop installer-only flags and keep user args', () => {
  assert.deepEqual(
    launchContext.buildRelaunchArgs([
      'D:\\Programs\\RpaClaw\\RpaClaw.exe',
      '--updated',
      '--profile=work',
      'C:\\docs\\notes.md',
    ]),
    ['--profile=work', 'C:\\docs\\notes.md']
  );
});

console.log('All launch-context tests passed');
