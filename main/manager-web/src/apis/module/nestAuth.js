import { getNestUrl } from '../api';
import { settle, NEST_TOKEN_KEY } from '../nestHttp';
import RequestService from '../httpRequest';

/**
 * Per-user NestJS admin login for the course/lesson authoring UIs. These routes
 * (/v1/admin/auth/login, /mfa-verify) are @Public() on the backend, and the
 * responses are NOT enveloped (they return the session body directly, so settle()
 * passes the body through unchanged). On success the issued session_token is
 * stored under localStorage['nestjs_session_token']; nestHttp.js then sends it on
 * the X-Nest-Authorization header, which the /nestjs proxy promotes to
 * Authorization (replacing the shared dev token). See vue.config.js onProxyReq.
 */

function authUrl(path) {
  return `${getNestUrl()}/auth${path}`;
}

function post(url, data, onSuccess, onError) {
  RequestService.sendRequest()
    .url(url)
    .method('POST')
    .header({ 'content-type': 'application/json; charset=utf-8' })
    .data(data)
    .success((res) => {
      RequestService.clearRequestTime();
      settle(res, onSuccess, onError);
    })
    .networkFail((res) => {
      RequestService.clearRequestTime();
      settle(res, onSuccess, onError);
    })
    .send();
}

export function setNestToken(token) {
  try {
    if (token) localStorage.setItem(NEST_TOKEN_KEY, token);
    else localStorage.removeItem(NEST_TOKEN_KEY);
  } catch (e) {
    /* ignore */
  }
}

export function getNestToken() {
  try {
    return localStorage.getItem(NEST_TOKEN_KEY);
  } catch (e) {
    return null;
  }
}

export default {
  hasToken() {
    return !!getNestToken();
  },
  logout() {
    setNestToken(null);
  },

  // POST /auth/login { email, password }
  // -> { mfaRequired, mfaToken } | { done, role }
  login(email, password, onSuccess, onError) {
    post(
      authUrl('/login'),
      { email, password },
      (r) => {
        const body = r || {};
        if (body.session_token) {
          setNestToken(body.session_token);
          onSuccess({ done: true, role: body.role });
        } else if (body.mfa_required) {
          onSuccess({ mfaRequired: true, mfaToken: body.mfa_token });
        } else {
          onError('Unexpected login response');
        }
      },
      onError,
    );
  },

  // POST /auth/mfa-verify { mfa_token, totp } -> { done, role }
  mfaVerify(mfaToken, totp, onSuccess, onError) {
    post(
      authUrl('/mfa-verify'),
      { mfa_token: mfaToken, totp },
      (r) => {
        const body = r || {};
        if (body.session_token) {
          setNestToken(body.session_token);
          onSuccess({ done: true, role: body.role });
        } else {
          onError('MFA verification failed');
        }
      },
      onError,
    );
  },
};
