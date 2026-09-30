const { expect } = require('@playwright/test');

function isExpectedNavigationAbort(request) {
  if (request.method() !== 'GET') return false;
  const errorText = request.failure()?.errorText;
  if (!['net::ERR_ABORTED', 'cancelled'].includes(errorText)) return false;
  return request.isNavigationRequest();
}

function monitorUnexpectedPageErrors(page) {
  const journal = require('./real-service-evidence').observeJourney(page);
  const check = () => journal.assertHappyPath();
  check.expectFault = journal.expectFault;
  check.evidence = journal.evidence;
  check.waitForSettledRequests = journal.waitForSettledRequests;
  return check;
}

module.exports = { isExpectedNavigationAbort, monitorUnexpectedPageErrors };
