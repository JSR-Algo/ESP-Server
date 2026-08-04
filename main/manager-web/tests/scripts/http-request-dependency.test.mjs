import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../../src/apis/httpRequest.js', import.meta.url), 'utf8');
const serviceWorker = readFileSync(new URL('../../src/service-worker.js', import.meta.url), 'utf8');
const serviceWorkerRegistration = readFileSync(new URL('../../src/registerServiceWorker.js', import.meta.url), 'utf8');

test('legacy request wrapper uses maintained axios without the flyio dependency chain', () => {
  assert.match(source, /import axios from ['"]axios['"]/);
  assert.doesNotMatch(source, /flyio/);
  assert.match(source, /axios\.create\(\{\s*timeout:\s*30000\s*\}\)/);
  assert.match(source, /error\.response/);
});

test('CDN fallback does not cache the retired vulnerable Axios build', () => {
  for (const file of [serviceWorker, serviceWorkerRegistration]) {
    assert.match(file, /axios@1\.19\.0/);
    assert.doesNotMatch(file, /axios@0\.27\.2/);
  }
});
