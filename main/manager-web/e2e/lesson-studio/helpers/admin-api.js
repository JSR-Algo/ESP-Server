const { expect } = require('@playwright/test');

const apiRoot = '/nestjs/v1/admin';

/**
 * Both credentials the `/nestjs/` proxy needs:
 *  - `Authorization` is the manager bearer nginx validates with auth_request
 *    (`/tbot/user/proxy-auth`). Without it every request is 401 at the edge and
 *    never reaches NestJS.
 *  - `X-Nest-Authorization` is the Nest author session; the proxy promotes it to
 *    `Authorization` upstream (a browser-set `Authorization` would be overwritten).
 *
 * `page.request` shares cookies with the page but not localStorage, so both
 * tokens have to be read out of the page and passed explicitly.
 */
async function adminAuthHeaders(page) {
  const tokens = await page.evaluate(() => ({
    manager: (JSON.parse(localStorage.getItem('token') || 'null') || {}).token || '',
    nest: localStorage.getItem('nestjs_session_token') || '',
  }));
  expect(tokens.manager, 'manager session token must exist after UI login').toBeTruthy();
  expect(tokens.nest, 'real Nest Author session token must exist after UI login').toBeTruthy();
  return {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${tokens.manager}`,
    'X-Nest-Authorization': `Bearer ${tokens.nest}`,
  };
}

/** Authenticated admin API call through the same-origin `/nestjs/` proxy. */
async function adminApi(page, method, path, data) {
  const headers = await adminAuthHeaders(page);
  const response = await page.request.fetch(`${apiRoot}${path}`, { method, data, headers });
  expect(response.ok(), `${method} ${path}: ${response.status()} ${await response.text()}`).toBe(true);
  const body = await response.json();
  return body.data;
}

/**
 * Same call, issued from inside the page (so a spec can assert on what the SPA's
 * own origin sees). Both tokens are read in-page; a bare `X-Nest-Authorization`
 * fetch is rejected by nginx's auth_request before it reaches NestJS.
 */
async function nestFetch(page, path, { method = 'GET', body } = {}) {
  return page.evaluate(async ({ path: p, method: m, body: b, root }) => {
    const stored = JSON.parse(localStorage.getItem('token') || 'null') || {};
    const nest = localStorage.getItem('nestjs_session_token');
    const headers = {};
    if (stored.token) headers.Authorization = `Bearer ${stored.token}`;
    if (nest) headers['X-Nest-Authorization'] = `Bearer ${nest}`;
    let requestBody;
    if (b !== undefined) {
      headers['Content-Type'] = 'application/json';
      requestBody = JSON.stringify(b);
    }
    const response = await fetch(`${root}${p}`, { method: m, headers, body: requestBody });
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw new Error(`${m} ${p} failed (${response.status}): ${JSON.stringify(payload)}`);
    return payload && Object.prototype.hasOwnProperty.call(payload, 'data') ? payload.data : payload;
  }, { path, method, body, root: apiRoot });
}

module.exports = { adminApi, adminAuthHeaders, apiRoot, nestFetch };
