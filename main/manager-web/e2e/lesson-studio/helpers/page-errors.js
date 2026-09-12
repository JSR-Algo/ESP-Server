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
  const journal = require('./real-service-evidence').observeJourney(page);
  const check = () => journal.assertHappyPath();
  check.expectFault = journal.expectFault;
  check.evidence = journal.evidence;
  return check;
}

module.exports = { isExpectedNavigationAbort, monitorUnexpectedPageErrors };
