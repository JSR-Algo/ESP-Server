# NestJS Author Login Bypass for Local and Production

## Goal

Remove the second "Sign in as author (NestJS)" prompt from manager-web in both
local development and production. The primary manager-web login remains
required, and production NestJS authoring requests remain restricted to active
manager super-admin users.

## Authentication Boundary

NestJS admin authentication uses the existing server-only admin proxy key:

- NestJS reads `TBOT_ADMIN_PROXY_KEY`.
- Local manager-web dev proxy reads `NESTJS_ADMIN_PROXY_KEY`.
- Production manager-web nginx reads `NESTJS_ADMIN_PROXY_KEY`.
- The browser never receives, stores, logs, or sends the proxy key.

The matching values are deployment secrets. They must be at least 32
characters and satisfy the existing backend production validation. This design
does not enable `ADMIN_AUTH_DISABLED`, `TRUST_CLOUDFLARE_ACCESS`, or a shared
`NESTJS_TOKEN`.

## Request Flow

### Local development

```text
manager-web browser
  -> Vue dev server /nestjs/* proxy
  -> proxy overwrites X-TBOT-Admin-Key from NESTJS_ADMIN_PROXY_KEY
  -> NestJS AdminSessionGuard validates TBOT_ADMIN_PROXY_KEY
  -> request receives the existing trusted author principal
```

Local development does not add a second manager-auth subrequest. The dev server
is bound to `127.0.0.1`, and NestJS still rejects requests when the configured
proxy key is absent or incorrect.

### Production

```text
authenticated manager super-admin browser
  -> manager-web nginx /nestjs/*
  -> nginx auth_request validates the manager bearer with manager-api
  -> nginx overwrites X-TBOT-Admin-Key from NESTJS_ADMIN_PROXY_KEY
  -> NestJS AdminSessionGuard validates TBOT_ADMIN_PROXY_KEY
  -> request receives the existing trusted author principal
```

The existing manager-api `proxy-auth` gate remains mandatory. Missing, expired,
inactive, or non-super-admin manager credentials stop at nginx and never reach
NestJS.

## Frontend Behavior

`VUE_APP_NEST_AUTH_DISABLED` defaults to `true` in supported local and
production build paths. In this mode:

- `NestLoginDialog` is not mounted.
- `tbot:nest-auth-required` is not subscribed to or emitted for NestJS 401s.
- `nestjs_session_token` is not attached to requests.
- A manager-auth 401/403 still clears the manager session and returns to the
  primary login page.
- A NestJS 401 after a successful manager gate is surfaced as a proxy/backend
  configuration error and does not create a login loop.

The old per-user NestJS login implementation may remain available behind an
explicit `VUE_APP_NEST_AUTH_DISABLED=false` build for diagnostic rollback, but
it is no longer the default local or production behavior.

## Configuration

Local `.env.development.local`:

```text
VUE_APP_NEST_AUTH_DISABLED=true
NESTJS_TARGET=http://127.0.0.1:3000
```

Set `NESTJS_ADMIN_PROXY_KEY` in that file and `TBOT_ADMIN_PROXY_KEY` in the
local NestJS environment to the same randomly generated value of at least 32
characters.

Local NestJS also keeps these bypass flags unset:

```text
ADMIN_AUTH_DISABLED=
TRUST_CLOUDFLARE_ACCESS=
```

Production manager-web environment:

```text
NESTJS_TOKEN=
```

Set production manager-web `NESTJS_ADMIN_PROXY_KEY` and production NestJS
`TBOT_ADMIN_PROXY_KEY` to the same independently generated production value.
Production NestJS keeps these bypass flags unset:

```text
ADMIN_AUTH_DISABLED=
TRUST_CLOUDFLARE_ACCESS=
```

Build scripts, Docker builds, and CI-produced manager-web images default
`VUE_APP_NEST_AUTH_DISABLED` to `true`. An explicit `false` remains a supported
rollback override.

## Failure Handling

- Missing local proxy key: NestJS returns 401; manager-web shows the request
  failure without opening the author dialog.
- Mismatched local or production proxy key: NestJS returns
  `ADMIN_PROXY_KEY_INVALID`; no fallback authentication path is attempted when
  the header is present.
- Invalid production manager session: nginx returns 401/403 and manager-web
  returns to its primary login page.
- Invalid production key material: NestJS or manager-web startup validation
  fails before serving the authoring surface.

No secret value may appear in errors, generated assets, service-worker caches,
startup logs, or committed configuration.

## Tests

Tests are written before implementation and cover:

- Nest auth mode defaults to disabled when the build value is omitted, while an
  explicit `false` restores the diagnostic login mode.
- manager-web does not mount or open `NestLoginDialog` in the default mode.
- local Vue proxy overwrites `X-TBOT-Admin-Key` from the server process
  environment and does not expose it to browser code.
- production Docker, CI, and local build paths default the frontend flag to
  `true`.
- production nginx continues to validate the manager bearer before injecting
  the proxy key.
- built frontend and service-worker output do not contain the configured proxy
  key.
- existing backend proxy-key guard and production-posture tests remain green.

## Rollout and Rollback

Rollout provisions matching proxy keys in local and production server
environments, rebuilds manager-web with the new default, and verifies an
authoring request succeeds after only the primary manager login.

Rollback rebuilds manager-web with `VUE_APP_NEST_AUTH_DISABLED=false`. Normal
per-user NestJS login remains available without a database migration. Removing
the proxy keys restores the normal NestJS session path.

## Non-Goals

- Bypassing or removing the primary manager-web login.
- Exposing the proxy key to JavaScript or accepting a browser-chosen proxy key.
- Enabling unauthenticated production NestJS admin routes.
- Changing parent, device, internal RPC, or other non-admin authentication.
- Mapping each manager account to a distinct NestJS audit principal.
