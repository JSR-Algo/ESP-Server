import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';

const rootDir = path.resolve(import.meta.dirname, '../..');
const sourcePath = path.join(rootDir, 'src/apis/httpRequest.js');

function loadHttpRequest({ axiosImpl }) {
  let source = fs.readFileSync(sourcePath, 'utf8');
  source = source
    .replace(/^import axios from 'axios';\n/m, '')
    .replace(/^import Fly from 'flyio\/dist\/npm\/fly';\n/m, '')
    .replace(/^import store from '\.\.\/store\/index';\n/m, '')
    .replace(/^import Constant from '\.\.\/utils\/constant';\n/m, '')
    .replace(/^import \{ goToPage, isNotNull, showDanger, showWarning \} from '\.\.\/utils\/index';\n/m, '')
    .replace(/^import i18n from '\.\.\/i18n\/index';\n/m, '')
    .replace('export default {', 'module.exports = {');

  const calls = {
    commits: [],
    dangers: [],
    warnings: [],
    navigations: [],
  };
  const context = {
    module: { exports: {} },
    console: { log() {} },
    setTimeout,
    axios: axiosImpl,
    store: {
      getters: { getToken: JSON.stringify({ token: 'token-123' }) },
      commit: (...args) => calls.commits.push(args),
    },
    Constant: { PAGE: { LOGIN: '/login' } },
    goToPage: (...args) => calls.navigations.push(args),
    isNotNull: value => value !== null && value !== undefined && value !== '',
    showDanger: message => calls.dangers.push(message),
    showWarning: message => calls.warnings.push(message),
    i18n: { locale: 'en' },
  };

  vm.runInNewContext(source, context, { filename: sourcePath });
  return { requestService: context.module.exports, calls };
}

test('sendRequest preserves fluent API while dispatching through axios', async () => {
  const requests = [];
  const axiosImpl = {
    create(options) {
      assert.equal(options.timeout, 30000);
      assert.deepEqual(Object.keys(options), ['timeout']);
      return {
        request(config) {
          requests.push(config);
          return Promise.resolve({ status: 200, data: { code: 'success' } });
        },
      };
    },
  };
  const { requestService } = loadHttpRequest({ axiosImpl });
  let successPayload;

  const chain = requestService.sendRequest()
    .url('/tbot$api')
    .method('POST')
    .data({ hello: 'world' })
    .header({ 'content-type': 'application/json; charset=utf-8' })
    .type('arraybuffer')
    .success(res => {
      successPayload = res;
    });

  assert.equal(chain.send(), chain);
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(requests.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(requests[0])), {
    url: '/tbot/api',
    method: 'POST',
    data: { hello: 'world' },
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'Accept-Language': 'en-US',
      Authorization: 'Bearer token-123',
    },
    responseType: 'arraybuffer',
  });
  assert.deepEqual(successPayload, { status: 200, data: { code: 'success' } });
});

test('sendRequest normalizes axios error.response before network failure handling', async () => {
  const axiosImpl = {
    create() {
      return {
        request() {
          return Promise.reject({
            response: { status: 503, data: { msg: 'backend unavailable' } },
          });
        },
      };
    },
  };
  const { requestService } = loadHttpRequest({ axiosImpl });
  let networkFailure;

  requestService.sendRequest()
    .url('/tbot/api')
    .networkFail(info => {
      networkFailure = info;
    })
    .send();
  await new Promise(resolve => setImmediate(resolve));

  assert.deepEqual(networkFailure, {
    status: 503,
    data: { msg: 'backend unavailable' },
  });
});
