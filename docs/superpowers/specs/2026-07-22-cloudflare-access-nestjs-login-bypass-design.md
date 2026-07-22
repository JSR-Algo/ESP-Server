# Cloudflare Access NestJS Login Bypass Design

## Goal

Allow an operator who has already passed Cloudflare Access in Safari to use the
manager-web course and lesson administration pages without a second NestJS
"Author sign-in" prompt.

This change removes only the per-user NestJS admin login. The existing
manager-web login remains unchanged. The bypass is valid only for the protected
production deployment on the VPS; normal deployments continue to require a
NestJS admin session.

## Security Boundary

Cloudflare Access becomes the authentication boundary for the NestJS authoring
surface. The deployment must satisfy all of these conditions:

1. The admin hostname is protected by a Cloudflare Access application.
2. Direct access to the VPS origin is blocked by firewall, Cloudflare Tunnel, or
   an equivalent origin restriction. A user must not be able to reach the admin
   nginx listener without traversing Cloudflare.
3. Cloudflare forwards `Cf-Access-Jwt-Assertion` on authenticated requests.
4. NestJS is started with both `ADMIN_AUTH_DISABLED=true` and
   `TRUST_CLOUDFLARE_ACCESS=true`.
5. manager-web is built with `VUE_APP_NEST_AUTH_DISABLED=true`.

The two explicit server flags prevent a generic production deployment from
enabling the existing escape hatch accidentally. The Cloudflare assertion
header is also required on each bypassed request, so missing or misrouted Access
traffic fails closed with HTTP 401. Header presence is not a substitute for
origin restriction: a directly reachable origin would let a client spoof the
header.

## Backend Behavior

`AdminSessionGuard` keeps its existing session-token path as the default.

When `ADMIN_AUTH_DISABLED=true`:

- Production startup is accepted only when `TRUST_CLOUDFLARE_ACCESS=true`.
- The guard requires a non-empty `Cf-Access-Jwt-Assertion` header when the trust
  flag is enabled. A missing header is rejected before any admin identity is
  attached.
- The guard resolves or creates the existing placeholder admin principal and
  attaches the current `super_admin`/authoring capability principal exactly as
  the existing bypass does.
- The assertion value is never logged or returned.

For local lab usage, the current `ADMIN_AUTH_DISABLED=true` behavior remains
available outside production without requiring Cloudflare. This avoids breaking
the established local authoring workflow.

`assertProductionPosture()` changes from rejecting every production bypass to
allowing the narrow combination where both flags are exactly `true`. It rejects
either incomplete combination, including a trust flag without the bypass flag,
because that represents ambiguous deployment intent.

## Frontend Behavior

manager-web exposes a small Nest auth-mode helper based on the compile-time
`VUE_APP_NEST_AUTH_DISABLED` flag.

In normal mode, behavior is unchanged:

- NestJS 401 responses clear `nestjs_session_token`.
- `tbot:nest-auth-required` opens `NestLoginDialog`.
- Per-user NestJS tokens are sent through `X-Nest-Authorization`.

In bypass mode:

- `App.vue` does not mount or open `NestLoginDialog` and does not subscribe to
  the auth-required event.
- NestJS requests omit `X-Nest-Authorization`; Cloudflare Access authenticates
  the browser before nginx proxies the request.
- A NestJS 401 is surfaced as a configuration/access error and does not open the
  author login dialog. This makes a missing Cloudflare assertion visible instead
  of creating an impossible login loop.
- Existing manager-web route protection, `userInfo.superAdmin` navigation
  checks, and capability checks remain unchanged.

The service worker build must naturally receive a new revision when the SPA is
rebuilt, so existing clients update from the login-dialog bundle to the bypass
bundle.

## Reverse Proxy and Deployment

The `/nestjs/` nginx proxy continues to forward Cloudflare request headers to
NestJS and continues to strip the `/nestjs` prefix. No shared `NESTJS_TOKEN` is
required for this mode.

The deployment configuration documents and passes these values:

```text
NestJS runtime:
  NODE_ENV=production
  ADMIN_AUTH_DISABLED=true
  TRUST_CLOUDFLARE_ACCESS=true

manager-web build:
  VUE_APP_NEST_AUTH_DISABLED=true

manager-web/nginx runtime:
  NESTJS_TOKEN=
```

`NESTJS_BASIC_HTPASSWD` may remain unset when `NESTJS_TOKEN` is empty. Cloudflare
Access is the outer gate; adding nginx Basic Auth would recreate a second prompt
and defeat the user goal.

## Error Handling

- Missing Cloudflare assertion in trusted bypass mode: HTTP 401 with a stable
  code/message that identifies missing trusted-proxy authentication without
  exposing assertion contents.
- Incomplete production flag combination: process startup fails through the
  production posture check.
- Frontend receives HTTP 401 in bypass mode: show the API error in the current
  page flow; do not clear the manager session or open a NestJS login dialog.
- Cloudflare Access rejection or expiry: Cloudflare owns the redirect/login
  experience before the request reaches manager-web or NestJS.

## Tests

Backend tests are written first and cover:

- Production accepts both flags set to `true`.
- Production rejects `ADMIN_AUTH_DISABLED=true` without the trust flag.
- Production rejects the trust flag without the bypass flag.
- The guard accepts a token-less request only when the Cloudflare trust flag and
  assertion header are present.
- The guard rejects the same request when the assertion header is missing.
- Existing non-production lab bypass and normal session authentication remain
  unchanged.

Frontend tests are written first and cover:

- Normal mode still emits the Nest auth-required event on HTTP 401.
- Bypass mode does not emit the event and does not attach a Nest session header.
- `App.vue` does not open/mount the Nest login dialog in bypass mode.
- Normal mode retains the existing login-dialog behavior.

Deployment/static checks cover:

- nginx preserves the Cloudflare assertion header through `/nestjs/`.
- the documented production environment contains both server acknowledgement
  flags and the frontend build flag.

## Rollout and Rollback

Rollout order:

1. Confirm Cloudflare Access policy and origin restriction from an external
   network.
2. Deploy the backend with the paired server flags.
3. Build and deploy manager-web with the frontend bypass flag.
4. Verify Safari enters the course-management page after only the Cloudflare
   Access challenge and that a `/nestjs/v1/admin/*` request succeeds without
   `nestjs_session_token`.

Rollback is configuration-first: rebuild manager-web without the frontend flag,
unset both backend flags, and restore normal NestJS admin sessions. No database
migration or persistent data conversion is involved.

## Non-Goals

- Removing or bypassing the manager-web login.
- Replacing Cloudflare Access with direct JWT verification inside NestJS.
- Mapping individual Cloudflare identities to distinct NestJS admin audit users.
- Opening the NestJS authoring API directly to the public Internet.
