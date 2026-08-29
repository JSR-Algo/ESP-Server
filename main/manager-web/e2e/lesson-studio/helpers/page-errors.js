const { expect } = require('@playwright/test');

function isExpectedNavigationAbort(request) {
  if (request.method() !== 'GET') return false;
  const errorText = request.failure()?.errorText;
  if (!['net::ERR_ABORTED', 'cancelled'].includes(errorText)) return false;
  if (request.isNavigationRequest()) return true;
  try {
    const url = new URL(request.url());
    const resourceType = request.resourceType();
    const path = url.pathname.toLowerCase();
    const frameOrigin = new URL(request.frame().url()).origin;
    const internalMediaProbe = ['media', 'other'].includes(resourceType)
      && url.origin === frameOrigin
      && path.startsWith('/tvideo-demo/')
      && path.endsWith('.mp4');
    const canonicalAdminMediaProbe = ['media', 'other'].includes(resourceType)
      && url.origin === 'https://admin.tjbot.vn'
      && path.startsWith('/tvideo-demo/assets/')
      && path.endsWith('.mp4');
    const task4FixtureMediaProbe = ['media', 'other'].includes(resourceType)
      && /^https:\/\/task4-media\.localhost:\d+$/.test(url.origin)
      && path.startsWith('/tvideo-demo/assets/')
      && path.endsWith('.mp4');
    const canonicalAdminImage = resourceType === 'image'
      && url.origin === 'https://admin.tjbot.vn'
      && path.startsWith('/tvideo-demo/assets/');
    return internalMediaProbe || canonicalAdminMediaProbe || task4FixtureMediaProbe
      || canonicalAdminImage;
  } catch {
    return false;
  }
}

function monitorUnexpectedPageErrors(page) {
  const errors = [];
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    if (/Failed to load resource: the server responded with a status of (400|401|422|503)/.test(message.text())) return;
    errors.push(`console: ${message.text()}`);
  });
  page.on('requestfailed', (request) => {
    if (request.url().startsWith('fixture://lesson-studio-e2e/')) return;
    if (isExpectedNavigationAbort(request)) return;
    errors.push(`requestfailed: ${request.method()} ${request.url()} ${request.failure()?.errorText || ''} type=${request.resourceType()} navigation=${request.isNavigationRequest()}`.trim());
  });
  page.on('response', (response) => {
    if (response.status() < 400) return;
    const url = response.url();
    const expectedAuthorChallenge = response.status() === 401 && url.includes('/nestjs/v1/admin/');
    // 422 is the correct "not publishable yet" answer from both proof endpoints.
    // The editor auto-requests a manifest preview for cinematic lessons as soon
    // as steps load, so a lesson without an asset bundle legitimately 422s there
    // too — same gate as /validate, not a failure.
    const expectedValidationGate = [400, 422].includes(response.status())
      && url.includes('/nestjs/v1/admin/lessons/')
      && (url.endsWith('/validate') || url.includes('/manifest-preview'));
    // The public generation index answers 503 `generation_unavailable` until an
    // asset generation is published. The seeded e2e stack has none, so this is
    // the designed retryable answer, not a failure.
    const expectedNoGenerationYet = response.status() === 503
      && url.endsWith('/v1/public/lesson-assets/latest');
    if (!expectedAuthorChallenge && !expectedValidationGate && !expectedNoGenerationYet) {
      errors.push(`http: ${response.status()} ${response.request().method()} ${url}`);
    }
  });
  return () => expect(errors, 'unexpected browser, console, or HTTP errors').toEqual([]);
}

module.exports = { isExpectedNavigationAbort, monitorUnexpectedPageErrors };
