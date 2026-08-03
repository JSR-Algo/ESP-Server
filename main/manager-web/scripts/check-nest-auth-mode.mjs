import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {
  getManagerAuthStatus,
  isNestAuthDisabled,
  isManagerAuthFailure,
  shouldClearManagerAuth,
  shouldHandleAuthFailure,
  shouldPromptForNestAuth,
  shouldSendNestSessionToken,
} from '../src/utils/nestAuthModeCore.mjs';

assert.equal(isNestAuthDisabled('true'), true);
assert.equal(isNestAuthDisabled('false'), false);
assert.equal(isNestAuthDisabled(undefined), true);
assert.equal(isNestAuthDisabled(''), true);
assert.equal(getManagerAuthStatus({ headers: { 'x-tbot-manager-auth-status': '204' } }), 204);
assert.equal(getManagerAuthStatus({ response: { headers: { 'X-TBOT-Manager-Auth-Status': '401' } } }), 401);
assert.equal(getManagerAuthStatus({ headers: { get: (name) => name === 'X-TBOT-Manager-Auth-Status' ? '403' : null } }), 403);
assert.equal(getManagerAuthStatus({ headers: {} }), null);
assert.equal(isManagerAuthFailure({ status: 401, managerAuthStatus: 401 }), true);
assert.equal(isManagerAuthFailure({ status: 403, managerAuthStatus: 403 }), true);
assert.equal(isManagerAuthFailure({ status: 401, managerAuthStatus: 204 }), false);
assert.equal(isManagerAuthFailure({ status: 401, managerAuthStatus: null }), false);
assert.equal(shouldPromptForNestAuth({ disabled: false, status: 401, managerAuthStatus: 204 }), true);
assert.equal(shouldPromptForNestAuth({ disabled: false, status: 401, managerAuthStatus: 401 }), false);
assert.equal(shouldPromptForNestAuth({ disabled: true, status: 401, managerAuthStatus: 204 }), false);
assert.equal(shouldClearManagerAuth({ disabled: true, status: 401, managerAuthStatus: 401 }), true);
assert.equal(shouldClearManagerAuth({ disabled: false, status: 401, managerAuthStatus: 401 }), true);
assert.equal(shouldClearManagerAuth({ disabled: true, status: 401, managerAuthStatus: 204 }), false);
assert.equal(shouldClearManagerAuth({ disabled: true, status: 401, managerAuthStatus: null }), false);
assert.equal(shouldClearManagerAuth({ disabled: true, status: 403 }), false);
assert.equal(shouldHandleAuthFailure({ status: 401, managerAuthStatus: 401, handle401: false }), true);
assert.equal(shouldHandleAuthFailure({ status: 403, managerAuthStatus: 403, handle401: false }), true);
assert.equal(shouldHandleAuthFailure({ status: 401, managerAuthStatus: 204, handle401: false }), false);
assert.equal(shouldHandleAuthFailure({ status: 401, managerAuthStatus: 204, handle401: true }), true);
assert.equal(shouldSendNestSessionToken({ disabled: true, token: 'secret' }), false);
assert.equal(shouldSendNestSessionToken({ disabled: false, token: 'secret' }), true);

const root = path.resolve(import.meta.dirname, '..');
const app = fs.readFileSync(path.join(root, 'src/App.vue'), 'utf8');
const http = fs.readFileSync(path.join(root, 'src/apis/nestHttp.js'), 'utf8');
assert.match(app, /v-if="!nestAuthDisabled"/);
assert.match(app, /if \(!this\.nestAuthDisabled\).*addEventListener/s);
assert.match(http, /shouldPromptForNestAuth/);
assert.match(http, /shouldClearManagerAuth/);
assert.match(http, /getManagerAuthStatus/);
assert.match(http, /localStorage\.removeItem\('token'\)/);
assert.match(http, /localStorage\.removeItem\('userInfo'\)/);
assert.match(http, /window\.location\.hash = '#\/login'/);
assert.match(http, /shouldSendNestSessionToken/);
assert.match(http, /function managerTokenHeader\(\)/);
assert.match(
  http,
  /fetch\([\s\S]*headers:\s*\{\s*\.\.\.managerTokenHeader\(\),\s*\.\.\.nestTokenHeader\(\)\s*\}/,
);

console.log('Nest auth mode contracts PASS');
