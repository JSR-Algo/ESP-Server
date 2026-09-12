const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const source = readFileSync(path.join(__dirname, '../../src/components/lesson/RobotLessonPreview.vue'), 'utf8');
const code = source.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/^import .*;$/gm, '').replace('export default', 'return');
const component = new Function('RobotEspTftProjectionPreview','RobotManifestServerPreview',code)({},{});
test('saved v5 server response uses the same exact projection as manifest prop', () => {
  const manifest = {manifestVersion:'teebot-lesson-renderer.v5',steps:[]};
  assert.equal(component.computed.exactManifest.call({manifest:null,manifestPreview:{manifest}}),manifest);
});
test('explicit manifest keeps precedence and old server preview remains available', () => {
  const manifest={steps:[]};
  assert.equal(component.computed.exactManifest.call({manifest,manifestPreview:{manifest:{}}}),manifest);
  assert.equal(component.computed.exactManifest.call({manifest:null,manifestPreview:{manifest:{manifestVersion:'teebot-lesson-renderer.v1'}}}),null);
});
