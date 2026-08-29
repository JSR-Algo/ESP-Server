const test = require('node:test');
const assert = require('node:assert/strict');
const { isExpectedNavigationAbort } = require('../e2e/lesson-studio/helpers/page-errors');

function request(
  method,
  errorText,
  navigation = false,
  resourceType = 'document',
  url = 'http://127.0.0.1:18102/lesson-editor',
) {
  return {
    method: () => method,
    failure: () => ({ errorText }),
    isNavigationRequest: () => navigation,
    resourceType: () => resourceType,
    url: () => url,
    frame: () => ({ url: () => 'http://127.0.0.1:18102/login#/lesson-editor' }),
  };
}

test('allows only GET requests aborted by browser navigation', () => {
  assert.equal(isExpectedNavigationAbort(request('GET', 'net::ERR_ABORTED', true)), true);
  assert.equal(isExpectedNavigationAbort(request('GET', 'net::ERR_ABORTED', false)), false);
  assert.equal(isExpectedNavigationAbort(request('POST', 'net::ERR_ABORTED')), false);
  assert.equal(isExpectedNavigationAbort(request('GET', 'net::ERR_CONNECTION_REFUSED')), false);
});

test('allows WebKit cancelled only for navigation requests', () => {
  assert.equal(isExpectedNavigationAbort(request('GET', 'cancelled', true)), true);
  assert.equal(isExpectedNavigationAbort(request('GET', 'cancelled', false, 'image')), false);
  assert.equal(isExpectedNavigationAbort(request('GET', 'cancelled', false, 'xhr')), false);
  assert.equal(isExpectedNavigationAbort(request('POST', 'cancelled', true)), false);
});

test('allows WebKit cancelled only for internal Lesson Studio MP4 range probes', () => {
  const video = 'http://127.0.0.1:18102/tvideo-demo/assets/background/barn-round-field.mp4';
  assert.equal(isExpectedNavigationAbort(request('GET', 'cancelled', false, 'other', video)), true);
  assert.equal(isExpectedNavigationAbort(request('POST', 'cancelled', false, 'other', video)), false);
  assert.equal(isExpectedNavigationAbort(request('GET', 'cancelled', false, 'image', video)), false);
  assert.equal(isExpectedNavigationAbort(request(
    'GET', 'cancelled', false, 'other', 'http://127.0.0.1:18102/nestjs/v1/admin/video.mp4',
  )), false);
  assert.equal(isExpectedNavigationAbort(request(
    'GET', 'cancelled', false, 'other', 'http://127.0.0.1:18102/tvideo-demo/assets/background/poster.png',
  )), false);
  assert.equal(isExpectedNavigationAbort(request(
    'GET', 'cancelled', false, 'other', 'https://admin.tjbot.vn/tvideo-demo/assets/scenes/farm.mp4',
  )), true);
  assert.equal(isExpectedNavigationAbort(request(
    'GET', 'cancelled', false, 'other', 'https://attacker.invalid/tvideo-demo/assets/scenes/farm.mp4',
  )), false);
  assert.equal(isExpectedNavigationAbort(request(
    'GET', 'cancelled', false, 'other', 'https://admin.tjbot.vn/private/farm.mp4',
  )), false);
  assert.equal(isExpectedNavigationAbort(request(
    'GET', 'net::ERR_ABORTED', false, 'other', video,
  )), true);
  assert.equal(isExpectedNavigationAbort(request(
    'GET', 'net::ERR_ABORTED', false, 'media',
    'https://task4-media.localhost:18443/tvideo-demo/assets/t54-layered/robot-teach.mp4',
  )), true);
  assert.equal(isExpectedNavigationAbort(request(
    'GET', 'net::ERR_ABORTED', false, 'media',
    'https://task4-media.localhost:18443/private/robot-teach.mp4',
  )), false);
  assert.equal(isExpectedNavigationAbort(request(
    'GET', 'net::ERR_ABORTED', false, 'other', 'https://attacker.invalid/tvideo-demo/assets/scenes/farm.mp4',
  )), false);
});

test('allows browser-aborted canonical admin asset images without hiding other failures', () => {
  assert.equal(isExpectedNavigationAbort(request(
    'GET', 'net::ERR_ABORTED', false, 'image',
    'https://admin.tjbot.vn/tvideo-demo/assets/objects/hay.png',
  )), true);
  assert.equal(isExpectedNavigationAbort(request(
    'GET', 'cancelled', false, 'image',
    'https://admin.tjbot.vn/tvideo-demo/assets/robot/bright-teach.webp',
  )), true);
  assert.equal(isExpectedNavigationAbort(request(
    'GET', 'net::ERR_ABORTED', false, 'image',
    'https://attacker.invalid/tvideo-demo/assets/objects/hay.png',
  )), false);
  assert.equal(isExpectedNavigationAbort(request(
    'GET', 'net::ERR_CERT_AUTHORITY_INVALID', false, 'image',
    'https://admin.tjbot.vn/tvideo-demo/assets/objects/hay.png',
  )), false);
  assert.equal(isExpectedNavigationAbort(request(
    'GET', 'net::ERR_ABORTED', false, 'xhr',
    'https://admin.tjbot.vn/tvideo-demo/assets/objects/hay.png',
  )), false);
});
