const assert = require('node:assert/strict');
const { normalizeExternalUrl } = require('../dist/url-utils');

function runTest(name, fn) {
  try {
    fn();
    console.log(`PASS ${name}`);
  } catch (error) {
    console.error(`FAIL ${name}`);
    throw error;
  }
}

runTest('allows https URLs', () => {
  const normalized = normalizeExternalUrl('https://example.com');
  assert.equal(normalized, 'https://example.com/');
});

runTest('rejects file URLs', () => {
  assert.equal(normalizeExternalUrl('file:///etc/passwd'), null);
});

runTest('rejects javascript URLs', () => {
  assert.equal(normalizeExternalUrl('javascript:alert(1)'), null);
});

console.log('All url-utils tests passed');
