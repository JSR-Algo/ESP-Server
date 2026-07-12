const { expect } = require('@playwright/test');

function isExpectedNavigationAbort(request) {
  return request.method() === 'GET' && request.failure()?.errorText === 'net::ERR_ABORTED';
}

function monitorUnexpectedPageErrors(page) {
  const errors = [];
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    if (/Failed to load resource: the server responded with a status of (401|422)/.test(message.text())) return;
    errors.push(`console: ${message.text()}`);
  });
  page.on('requestfailed', (request) => {
    if (request.url().startsWith('fixture://lesson-studio-e2e/')) return;
    if (isExpectedNavigationAbort(request)) return;
    errors.push(`requestfailed: ${request.method()} ${request.url()} ${request.failure()?.errorText || ''}`.trim());
  });
  page.on('response', (response) => {
    if (response.status() < 400) return;
    const url = response.url();
    const expectedAuthorChallenge = response.status() === 401 && url.includes('/nestjs/v1/admin/');
    const expectedValidationGate = response.status() === 422 && url.includes('/nestjs/v1/admin/lessons/') && url.endsWith('/validate');
    if (!expectedAuthorChallenge && !expectedValidationGate) {
      errors.push(`http: ${response.status()} ${response.request().method()} ${url}`);
    }
  });
  return () => expect(errors, 'unexpected browser, console, or HTTP errors').toEqual([]);
}

module.exports = { isExpectedNavigationAbort, monitorUnexpectedPageErrors };
