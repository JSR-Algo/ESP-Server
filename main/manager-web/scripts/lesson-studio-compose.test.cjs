const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const test = require('node:test');

const composeFile = resolve(
  __dirname,
  '../../../docs/docker/docker-compose.lesson-studio-e2e.yml',
);

function serviceBlock(compose, serviceName) {
  const marker = `  ${serviceName}:\n`;
  const start = compose.indexOf(marker);
  assert.notEqual(start, -1, `Compose service ${serviceName} must exist`);
  const bodyStart = start + marker.length;
  const nextService = compose.slice(bodyStart).search(/^  [a-zA-Z0-9_-]+:\s*$/m);
  return nextService === -1
    ? compose.slice(bodyStart)
    : compose.slice(bodyStart, bodyStart + nextService);
}

test('lesson studio backend requires shared live settings and passes through robot fan-out URLs', () => {
  const backend = serviceBlock(readFileSync(composeFile, 'utf8'), 'backend');

  assert.match(
    backend,
    /TBOT_DEVICE_MINT_SECRET:\s*\$\{TBOT_DEVICE_MINT_SECRET:\?[^\n]+\}/,
  );
  assert.match(
    backend,
    /LESSON_ASSET_ORIGIN_BASE:\s*\$\{LESSON_ASSET_ORIGIN_BASE:\?[^\n]+\}/,
  );
  assert.match(
    backend,
    /ROBOT_ESP_BASE_URL:\s*\$\{ROBOT_ESP_BASE_URL:\?[^\n]+\}/,
  );
  assert.match(backend, /TBOT_ESP_SERVER_URL:\s*\$\{TBOT_ESP_SERVER_URL:-\}/);
});

test('compose contract assertions stay scoped to the backend service', () => {
  const compose = `services:\n  backend:\n    environment:\n      KEEP: backend\n  web:\n    environment:\n      TBOT_DEVICE_MINT_SECRET: wrong-service\n`;
  const backend = serviceBlock(compose, 'backend');

  assert.match(backend, /KEEP: backend/);
  assert.doesNotMatch(backend, /wrong-service/);
});

test('the browser-and-robot origin serves canonical derivatives and seeded assets', () => {
  const compose = readFileSync(composeFile, 'utf8');
  const web = serviceBlock(compose, 'web');

  assert.doesNotMatch(web, /tvideo-raw-code\/assets:\/usr\/share\/nginx\/html\/tvideo-demo:ro/);
  assert.match(web, /tvideo-raw-code\/assets\/asset-manifest\.json:\/usr\/share\/nginx\/html\/tvideo-demo\/asset-manifest\.json:ro/);
  assert.match(web, /tvideo-raw-code\/assets\/admin:\/usr\/share\/nginx\/html\/tvideo-demo\/admin:ro/);
  assert.match(web, /tvideo-raw-code\/assets\/esp-tft:\/usr\/share\/nginx\/html\/tvideo-demo\/esp-tft:ro/);
  assert.match(web, /lesson\/assets:\/usr\/share\/nginx\/html\/tvideo-demo\/assets:ro/);
});
