export function isNestAuthDisabled(value = process.env.VUE_APP_NEST_AUTH_DISABLED) {
  return value !== 'false';
}

export function getManagerAuthStatus(response) {
  const headers = response && (response.headers || (response.response && response.response.headers));
  if (!headers) return null;

  let value;
  if (typeof headers.get === 'function') {
    value = headers.get('X-TBOT-Manager-Auth-Status');
  } else {
    const key = Object.keys(headers).find(
      (candidate) => candidate.toLowerCase() === 'x-tbot-manager-auth-status',
    );
    value = key ? headers[key] : null;
  }

  const normalized = Array.isArray(value) ? value[0] : value;
  const status = Number(normalized);
  return Number.isFinite(status) && normalized !== null && normalized !== '' ? status : null;
}

export function isManagerAuthFailure({ status, managerAuthStatus }) {
  const responseStatus = Number(status);
  const authStatus = Number(managerAuthStatus);
  return (authStatus === 401 || authStatus === 403) && responseStatus === authStatus;
}

export function shouldPromptForNestAuth({ disabled, status, managerAuthStatus }) {
  return !disabled
    && Number(status) === 401
    && !isManagerAuthFailure({ status, managerAuthStatus });
}

export function shouldClearManagerAuth({ status, managerAuthStatus }) {
  return isManagerAuthFailure({ status, managerAuthStatus });
}

export function shouldHandleAuthFailure({ status, managerAuthStatus, handle401 = true }) {
  return isManagerAuthFailure({ status, managerAuthStatus })
    || (handle401 && Number(status) === 401);
}

export function shouldSendNestSessionToken({ disabled, token }) {
  return !disabled && typeof token === 'string' && token.length > 0;
}
