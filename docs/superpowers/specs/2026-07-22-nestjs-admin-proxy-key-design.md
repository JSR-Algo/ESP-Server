# NestJS Admin Proxy Key Design

## Goal

Allow the production manager website to call every NestJS endpoint protected by
`AdminSessionGuard` without showing the separate NestJS Author sign-in dialog.
The browser must never receive the shared credential.

## Scope

The proxy key authenticates only routes that already declare
`AdminSessionGuard`, currently the `/v1/admin/*` authoring, monitoring, insight,
rollout, and visual-library surfaces. It does not bypass the parent JWT guard,
device guards, factory guards, internal RPC authentication, or public endpoint
rules.

The existing manager-web login remains unchanged and is the public HTTP gate.
nginx validates its bearer with an internal manager-api subrequest before it
injects the proxy key. Only active manager super-admin accounts pass.

## Request Flow

```text
Safari
  -> https://admin.tjbot.vn/nestjs/v1/admin/*
  -> nginx validates the existing manager super-admin bearer via manager-api
  -> manager-web nginx adds X-TBOT-Admin-Key
  -> NestJS AdminSessionGuard validates the key
  -> request receives a super_admin authoring principal
```

The key is stored only in server environment variables:

- NestJS: `TBOT_ADMIN_PROXY_KEY`
- manager-web/nginx: `NESTJS_ADMIN_PROXY_KEY`

manager-web JavaScript sends no admin key and stores no admin key in cookies,
local storage, session storage, service-worker caches, or generated assets.

## Backend Authentication

`AdminSessionGuard` evaluates authentication in this order:

1. Reuse an existing `req.admin` principal when the global prelude has already
   authenticated the request.
2. If `TBOT_ADMIN_PROXY_KEY` is configured, read `X-TBOT-Admin-Key` and compare
   it with the configured key using a constant-time comparison over equal-length
   buffers.
3. If the proxy key matches, resolve or create the existing placeholder admin
   database row and attach a `super_admin` principal with authoring capability.
4. If no proxy-key header is present, continue to normal opaque admin-session
   authentication. A configured proxy key does not disable ordinary admin login.
5. If a proxy-key header is present but incorrect, reject with HTTP 401 and do
   not fall through to another authentication path.

The key value is never logged, included in exception bodies, or persisted in the
database. Audit rows use the placeholder admin identity and a stable
`admin-proxy-key` session identifier.

`ADMIN_AUTH_DISABLED` remains a separate local-lab mechanism and is not enabled
for this production deployment.

## Configuration Validation

Production startup validates `TBOT_ADMIN_PROXY_KEY` when configured:

- minimum 32 characters;
- no surrounding whitespace;
- not a known placeholder such as `change-me`, `secret`, or `password`;
- usable independently of `ADMIN_AUTH_DISABLED` and
  `TRUST_CLOUDFLARE_ACCESS`.

An absent key preserves the existing production posture and per-user admin
sessions. Invalid configured key material fails startup.

## Reverse Proxy

The manager-web container startup script renders an nginx header value from
`NESTJS_ADMIN_PROXY_KEY`. Inside `location /nestjs/`, nginx always replaces any
browser-supplied `X-TBOT-Admin-Key` value:

```nginx
proxy_set_header X-TBOT-Admin-Key "<server-rendered value>";
```

This prevents clients from choosing or forwarding their own key. The key is
escaped before template substitution and must not appear in startup logs.

Before that header is injected, `auth_request` calls the internal
`/tbot/user/proxy-auth` endpoint with the browser's manager bearer. Missing,
expired, inactive, or non-super-admin manager credentials return 401/403 and
the NestJS request is not proxied.

The previous `NESTJS_TOKEN` session-token injection remains available for
backward compatibility but is empty for this deployment. Per-user
`X-Nest-Authorization` continues to override only the Authorization header; it
cannot override `X-TBOT-Admin-Key`.

## Frontend

The already implemented `VUE_APP_NEST_AUTH_DISABLED=true` build mode remains the
frontend control:

- `NestLoginDialog` is not mounted;
- `nestjs_session_token` is not sent;
- NestJS 401 responses surface as access/configuration errors instead of opening
  the dialog.

No frontend change exposes or reads `NESTJS_ADMIN_PROXY_KEY`.

## Key Generation and Rotation

Generate the production key from cryptographically secure random bytes, for
example:

```bash
openssl rand -base64 48
```

For routine rotation without downtime, the backend may temporarily accept
`TBOT_ADMIN_PROXY_KEY_PREVIOUS` in addition to the current key:

1. Set new `TBOT_ADMIN_PROXY_KEY` and move the old value to
   `TBOT_ADMIN_PROXY_KEY_PREVIOUS`; restart NestJS.
2. Set nginx `NESTJS_ADMIN_PROXY_KEY` to the new value; restart manager-web.
3. Verify admin requests succeed.
4. Remove `TBOT_ADMIN_PROXY_KEY_PREVIOUS`; restart NestJS.

The previous-key variable receives the same production validation and is never
configured during initial rollout.

## Failure Behavior

- Missing proxy header: normal admin-session authentication applies.
- Incorrect proxy header: HTTP 401 with code `ADMIN_PROXY_KEY_INVALID`.
- Key configured only in nginx: NestJS rejects the request.
- Key configured only in NestJS: browser requests have no proxy credential and
  normal admin-session authentication applies.
- Invalid/short production key: NestJS refuses to start.
- nginx key empty: nginx forwards an empty header and gains no proxy-key access.

## Tests

Backend tests are written first and prove:

- a correct proxy key authenticates a token-less request as super admin;
- an incorrect presented key is rejected before database/session fallback;
- a missing header preserves normal session authentication;
- equal-length and unequal-length wrong keys are rejected;
- the current and temporary previous keys are accepted during rotation;
- production rejects short, whitespace-padded, and placeholder keys;
- production accepts a strong proxy key without `ADMIN_AUTH_DISABLED`.

manager-web/deployment tests are written first and prove:

- nginx overwrites `X-TBOT-Admin-Key` with the server-rendered value;
- startup safely escapes the key during template rendering;
- Compose and VPS release configuration pass `NESTJS_ADMIN_PROXY_KEY` only at
  container runtime, never as a Vue build argument;
- built JavaScript and service-worker output contain no proxy key;
- bypass-mode frontend contracts remain green.

## VPS Rollout

1. Build and deploy the manager-web image with
   `VUE_APP_NEST_AUTH_DISABLED=true`.
2. Build and deploy the updated NestJS image on the private Docker network.
3. Generate one random key locally without printing it into logs.
4. Store the matching values in the protected VPS/backend environment files.
5. Point `NESTJS_UPSTREAM_HOST` to the private NestJS container and use HTTP on
   the Docker network.
6. Keep `NESTJS_TOKEN`, `ADMIN_AUTH_DISABLED`, and
   `TRUST_CLOUDFLARE_ACCESS` unset/empty.
7. Restart NestJS, then manager-web.
8. Verify public direct NestJS requests without the key return 401, while the
   same admin request through `admin.tjbot.vn/nestjs/*` succeeds.

Rollback restores the prior web image/upstream settings and removes both proxy
key variables. No database migration is required.
