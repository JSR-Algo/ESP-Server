const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { observeJourney } = require('../../e2e/lesson-studio/helpers/real-service-evidence');

function fixture() {
  const page = new EventEmitter();
  let token = 'document-a';
  let binding;
  const frame = { evaluate: async () => token, isDetached: () => false, parentFrame: () => null };
  page.frames = () => [frame];
  page.exposeBinding = async (_name, callback) => { binding = callback; };
  page.addInitScript = async () => {};
  const journal = observeJourney(page);
  const request = (type = 'image', error = 'net::ERR_ABORTED') => ({
    method: () => 'GET', resourceType: () => type, url: () => 'https://assets.example/same.png',
    failure: () => ({ errorText: error }), isNavigationRequest: () => false, frame: () => frame,
  });
  return { page, frame, journal, request,
    hide: (event = 'pagehide') => binding({ frame }, { token, event }), replace: () => { token = 'document-b'; } };
}

for (const event of ['pagehide', 'beforeunload']) test(`explicit document replacement after ${event} retains the exact old request`, async () => {
  const f = fixture();
  const old = f.request();
  f.page.emit('request', old);
  await f.journal.retireDocumentForNavigation(async () => {
    f.hide(event);
    f.page.emit('requestfailed', old);
    f.replace();
    f.page.emit('framenavigated', f.frame);
  });
  assert.equal(f.journal.evidence.requestFailures.length, 1, 'failure remains in evidence');
  assert.equal(f.journal.evidence.mediaRetirements.length, 1);
  assert.doesNotThrow(() => f.journal.assertHappyPath());
});

for (const related of [true, false]) {
  test(`ancestor retirement ${related ? 'covers its child' : 'does not cover a sibling'}`, async () => {
    const f = fixture();
    const other = { evaluate: async () => 'other-document', isDetached: () => true };
    f.page.frames = () => [f.frame, other];
    other.parentFrame = () => related ? f.frame : null;
    const request = { ...f.request(), frame: () => other };
    f.page.emit('request', request);
    await f.journal.retireDocumentForNavigation(async () => {
      f.hide();
      f.page.emit('requestfailed', request);
      f.replace();
      f.page.emit('framenavigated', f.frame);
    });
    if (related) assert.doesNotThrow(() => f.journal.assertHappyPath());
    else assert.throws(() => f.journal.assertHappyPath());
  });
}

test('removing a child without replacing its ancestor remains a failure', async () => {
  const f = fixture();
  let detached = false;
  const child = { evaluate: async () => 'child-document', isDetached: () => detached, parentFrame: () => f.frame };
  f.page.frames = () => [f.frame, child];
  const request = { ...f.request(), frame: () => child };
  f.page.emit('request', request);
  await f.journal.retireDocumentForNavigation(async () => {
    f.hide('beforeunload');
    f.page.emit('requestfailed', request);
    detached = true;
    f.page.emit('framedetached', child);
  });
  assert.throws(() => f.journal.assertHappyPath());
});

for (const variant of ['same-document', 'action-failed', 'fetch', 'network-error', 'unregistered', 'new-document', 'missing-pagehide', 'failed-before-pagehide']) {
  test(`${variant} cancellation remains a journey failure`, async () => {
    const f = fixture();
    const old = f.request(variant === 'fetch' ? 'fetch' : 'image', variant === 'network-error' ? 'net::ERR_CONNECTION_RESET' : 'net::ERR_ABORTED');
    f.page.emit('request', old);
    const navigation = async () => {
      if (variant === 'action-failed') { f.hide('beforeunload'); throw new Error('navigation failed'); }
      if (variant === 'failed-before-pagehide') f.page.emit('requestfailed', old);
      if (!['unregistered', 'missing-pagehide'].includes(variant)) f.hide();
      if (variant !== 'same-document') { f.replace(); f.page.emit('framenavigated', f.frame); }
      if (variant === 'new-document') {
        const replacement = f.request();
        f.page.emit('request', replacement);
        f.page.emit('requestfailed', replacement);
      } else if (variant !== 'failed-before-pagehide') f.page.emit('requestfailed', old);
    };
    if (variant === 'unregistered') await navigation();
    else if (variant === 'action-failed') {
      await assert.rejects(f.journal.retireDocumentForNavigation(navigation), /navigation failed/);
      f.page.emit('requestfailed', old);
    } else await f.journal.retireDocumentForNavigation(navigation);
    assert.throws(() => f.journal.assertHappyPath());
  });
}
