import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {
  isNestAuthDisabled,
  shouldPromptForNestAuth,
  shouldSendNestSessionToken,
} from '../src/utils/nestAuthModeCore.mjs';

assert.equal(isNestAuthDisabled('true'), true);
assert.equal(isNestAuthDisabled('false'), false);
assert.equal(isNestAuthDisabled(undefined), false);
assert.equal(shouldPromptForNestAuth({ disabled: false, status: 401 }), true);
assert.equal(shouldPromptForNestAuth({ disabled: true, status: 401 }), false);
assert.equal(shouldSendNestSessionToken({ disabled: true, token: 'secret' }), false);
assert.equal(shouldSendNestSessionToken({ disabled: false, token: 'secret' }), true);

const root = path.resolve(import.meta.dirname, '..');
const app = fs.readFileSync(path.join(root, 'src/App.vue'), 'utf8');
const http = fs.readFileSync(path.join(root, 'src/apis/nestHttp.js'), 'utf8');
assert.match(app, /v-if="!nestAuthDisabled"/);
assert.match(app, /if \(!this\.nestAuthDisabled\).*addEventListener/s);
assert.match(http, /shouldPromptForNestAuth/);
assert.match(http, /shouldSendNestSessionToken/);

console.log('Nest auth mode contracts PASS');
