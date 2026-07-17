const DEFAULT_WEB_ORIGIN = 'http://127.0.0.1:8102';

function lessonStudioWebOrigin(env = process.env) {
  const hostPort = env.LESSON_STUDIO_E2E_WEB_HOST_PORT;
  if (hostPort !== undefined
      && (!/^[0-9]+$/.test(hostPort) || Number(hostPort) < 1 || Number(hostPort) > 65535)) {
    throw new Error('LESSON_STUDIO_E2E_WEB_HOST_PORT must be a valid TCP port');
  }
  const configured = env.LESSON_STUDIO_E2E_WEB_ORIGIN
    || env.LESSON_STUDIO_E2E_BASE_URL
    || (hostPort ? `http://127.0.0.1:${hostPort}` : '')
    || DEFAULT_WEB_ORIGIN;
  let url;
  try {
    url = new URL(configured);
  } catch {
    throw new Error('LESSON_STUDIO_E2E_BASE_URL must be a safe HTTP(S) origin');
  }
  const safe = (url.protocol === 'http:' || url.protocol === 'https:')
    && !url.username
    && !url.password
    && url.pathname === '/'
    && !url.search
    && !url.hash;
  if (!safe) {
    throw new Error('LESSON_STUDIO_E2E_BASE_URL must be a safe HTTP(S) origin');
  }
  return url.origin;
}

function lessonStudioAssetUrl(path, env = process.env) {
  if (typeof path !== 'string' || !path || path.startsWith('/')
      || path.includes('\\') || path.includes('?') || path.includes('#')) {
    throw new Error('tvideo demo asset must use a safe relative asset path');
  }
  const segments = path.split('/');
  if (segments.some((segment) => {
    if (!segment) return true;
    let decoded;
    try {
      decoded = decodeURIComponent(segment);
    } catch {
      return true;
    }
    return decoded === '.' || decoded === '..'
      || decoded.includes('/') || decoded.includes('\\')
      || /[\u0000-\u001f\u007f]/.test(decoded);
  })) {
    throw new Error('tvideo demo asset must use a safe relative asset path');
  }
  const origin = lessonStudioWebOrigin(env);
  const url = new URL(`/tvideo-demo/${path}`, origin);
  if (url.origin !== origin || !url.pathname.startsWith('/tvideo-demo/')) {
    throw new Error('tvideo demo asset must use a safe relative asset path');
  }
  return url.href;
}

module.exports = {
  DEFAULT_WEB_ORIGIN,
  lessonStudioAssetUrl,
  lessonStudioWebOrigin,
};
