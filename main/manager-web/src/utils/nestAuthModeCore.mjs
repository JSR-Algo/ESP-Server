export function isNestAuthDisabled(value = process.env.VUE_APP_NEST_AUTH_DISABLED) {
  return value === 'true';
}

export function shouldPromptForNestAuth({ disabled, status }) {
  return !disabled && Number(status) === 401;
}

export function shouldSendNestSessionToken({ disabled, token }) {
  return !disabled && typeof token === 'string' && token.length > 0;
}
