const test = require('node:test');
const assert = require('node:assert/strict');
const { isExpectedNavigationAbort } = require('../e2e/lesson-studio/helpers/page-errors');

function request(method, errorText) {
  return { method: () => method, failure: () => ({ errorText }) };
}

test('allows only GET requests aborted by browser navigation', () => {
  assert.equal(isExpectedNavigationAbort(request('GET', 'net::ERR_ABORTED')), true);
  assert.equal(isExpectedNavigationAbort(request('POST', 'net::ERR_ABORTED')), false);
  assert.equal(isExpectedNavigationAbort(request('GET', 'net::ERR_CONNECTION_REFUSED')), false);
});
