# NestJS Author Login Bypass for Local and Production Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make manager-web skip the second NestJS author login by default in local and production while authenticating authoring requests through the existing server-only admin proxy key.

**Architecture:** The frontend defaults to bypass UI mode unless an explicit `VUE_APP_NEST_AUTH_DISABLED=false` rollback build is requested. Local Vue dev proxy and production nginx inject `X-TBOT-Admin-Key` from server process environment; NestJS continues validating the matching `TBOT_ADMIN_PROXY_KEY`, and production nginx keeps the primary manager super-admin gate in front of key injection.

**Tech Stack:** Vue 2, Vue CLI dev server proxy, Node contract scripts, nginx, Docker/Compose, GitHub Actions, NestJS admin proxy-key guard.

---

## File Map

- `main/manager-web/src/utils/nestAuthModeCore.mjs`: define the default frontend Nest auth mode and explicit rollback override.
- `main/manager-web/scripts/check-nest-auth-mode.mjs`: executable contract for dialog, token, and auth-failure behavior.
- `main/manager-web/vue.config.js`: inject the server-only proxy key for local `/nestjs` requests.
- `main/manager-web/scripts/check-admin-proxy-key-wiring.mjs`: verify local and production proxy-key wiring without exposing a key to browser source.
- `Dockerfile-web`: default production SPA builds to bypass UI mode.
- `main/tbot-server/docker-compose_all.yml`: default local Compose builds to bypass UI mode and pass the runtime proxy key.
- `deploy/build-local.sh`: default release/local image builds to bypass UI mode.
- `.github/workflows/docker-image.yml`: default tag, workflow-run, and manual builds to bypass UI mode while preserving an explicit false rollback.
- `main/manager-web/scripts/check-web-cache-policy.mjs`: verify every supported build path uses the new default.
- `deploy/.env.example`: document the required production server-only key pair and remove the obsolete per-user sign-in guidance.
- `deploy/PORTABILITY.md`: document the proxy key as the normal `/nestjs` authentication mechanism.

### Task 1: Default the Frontend to No Author Dialog

**Files:**
- Modify: `main/manager-web/scripts/check-nest-auth-mode.mjs`
- Modify: `main/manager-web/src/utils/nestAuthModeCore.mjs`

- [ ] **Step 1: Write the failing auth-mode contract**

Change the first auth-mode assertions in `main/manager-web/scripts/check-nest-auth-mode.mjs` to:

```js
assert.equal(isNestAuthDisabled('true'), true);
assert.equal(isNestAuthDisabled('false'), false);
assert.equal(isNestAuthDisabled(undefined), true);
assert.equal(isNestAuthDisabled(''), true);
```

Keep the existing assertions proving that disabled mode does not prompt, does not send `nestjs_session_token`, and still clears primary manager auth when nginx reports a manager-auth failure.

- [ ] **Step 2: Run the contract and verify RED**

Run:

```bash
cd main/manager-web
npm run test:nest-auth-mode
```

Expected: FAIL because `isNestAuthDisabled(undefined)` currently returns `false`.

- [ ] **Step 3: Implement the minimal explicit-false rollback rule**

Replace `isNestAuthDisabled` in `main/manager-web/src/utils/nestAuthModeCore.mjs` with:

```js
export function isNestAuthDisabled(value = process.env.VUE_APP_NEST_AUTH_DISABLED) {
  return value !== 'false';
}
```

This makes missing/empty build configuration fail closed to the no-dialog proxy mode while preserving `false` as the diagnostic rollback.

- [ ] **Step 4: Run the contract and verify GREEN**

Run:

```bash
cd main/manager-web
npm run test:nest-auth-mode
```

Expected: `Nest auth mode contracts PASS`.

- [ ] **Step 5: Commit the frontend default**

```bash
git add main/manager-web/scripts/check-nest-auth-mode.mjs \
  main/manager-web/src/utils/nestAuthModeCore.mjs
git commit -m "fix(admin): default to proxy-backed NestJS auth"
```

### Task 2: Inject the Proxy Key in Local Development

**Files:**
- Modify: `main/manager-web/scripts/check-admin-proxy-key-wiring.mjs`
- Modify: `main/manager-web/vue.config.js`

- [ ] **Step 1: Write the failing local proxy wiring contract**

Add these checks near the existing nginx proxy-key checks in `main/manager-web/scripts/check-admin-proxy-key-wiring.mjs`:

```js
expectContains(
  'main/manager-web/vue.config.js',
  "const adminProxyKey = browserE2E ? '' : (process.env.NESTJS_ADMIN_PROXY_KEY || '');",
);
expectContains(
  'main/manager-web/vue.config.js',
  "proxyReq.setHeader('X-TBOT-Admin-Key', adminProxyKey);",
);
```

Keep `expectNotContains('main/manager-web/src', 'NESTJS_ADMIN_PROXY_KEY')` so the server environment variable cannot move into browser code.

- [ ] **Step 2: Run the contract and verify RED**

Run:

```bash
cd main/manager-web
npm run test:admin-proxy-key-wiring
```

Expected: FAIL because `vue.config.js` does not yet read or inject `NESTJS_ADMIN_PROXY_KEY`.

- [ ] **Step 3: Read the local server-only key**

In `main/manager-web/vue.config.js`, immediately after `sharedNestAdminToken`, add:

```js
const adminProxyKey = browserE2E ? '' : (process.env.NESTJS_ADMIN_PROXY_KEY || '');
```

The browser E2E path remains isolated from shared credentials.

- [ ] **Step 4: Overwrite the browser proxy-key header**

At the start of the `/nestjs` `onProxyReq(proxyReq)` callback, add:

```js
proxyReq.setHeader('X-TBOT-Admin-Key', adminProxyKey);
```

Keep the existing per-user `X-Nest-Authorization` promotion below it for the explicit rollback build. Setting the header from server configuration overwrites any browser-supplied value; an empty configured value gains no backend access.

- [ ] **Step 5: Run the wiring and auth-mode contracts**

Run:

```bash
cd main/manager-web
npm run test:admin-proxy-key-wiring
npm run test:nest-auth-mode
```

Expected: both scripts exit 0 and print their PASS messages.

- [ ] **Step 6: Commit local proxy authentication**

```bash
git add main/manager-web/scripts/check-admin-proxy-key-wiring.mjs \
  main/manager-web/vue.config.js
git commit -m "feat(admin): inject NestJS proxy key in local dev"
```

### Task 3: Default Every Supported Build Path to Bypass Mode

**Files:**
- Modify: `main/manager-web/scripts/check-web-cache-policy.mjs`
- Modify: `Dockerfile-web`
- Modify: `main/tbot-server/docker-compose_all.yml`
- Modify: `deploy/build-local.sh`
- Modify: `.github/workflows/docker-image.yml`

- [ ] **Step 1: Write failing build-default contracts**

Update the four build-default expectations in `main/manager-web/scripts/check-web-cache-policy.mjs` to require `true`:

```js
expectRegex(
  'Dockerfile-web',
  /ARG VUE_APP_NEST_AUTH_DISABLED=true[\s\S]*ENV VUE_APP_NEST_AUTH_DISABLED=\$VUE_APP_NEST_AUTH_DISABLED[\s\S]*RUN npm run build/,
  'the Vue production build must default to proxy-backed Nest auth',
);
expectRegex(
  'main/tbot-server/docker-compose_all.yml',
  /args:[\s\S]*VUE_APP_NEST_AUTH_DISABLED:\s*\$\{VUE_APP_NEST_AUTH_DISABLED:-true\}/,
  'local Compose must default to proxy-backed Nest auth',
);
expectRegex(
  'deploy/build-local.sh',
  /VUE_APP_NEST_AUTH_DISABLED="\$\{VUE_APP_NEST_AUTH_DISABLED:-true\}"/,
  'the release builder must default to proxy-backed Nest auth',
);
expectRegex(
  '.github/workflows/docker-image.yml',
  /workflow_dispatch:[\s\S]*nest_auth_disabled:[\s\S]*default:\s*true[\s\S]*VUE_APP_NEST_AUTH_DISABLED=\$\{\{ github\.event_name != 'workflow_dispatch' \|\| inputs\.nest_auth_disabled \}\}/,
  'release builds must default to bypass mode and preserve a manual false rollback',
);
```

Also add a runtime wiring assertion for local Compose:

```js
expectRegex(
  'main/tbot-server/docker-compose_all.yml',
  /NESTJS_ADMIN_PROXY_KEY=\$\{NESTJS_ADMIN_PROXY_KEY:-\}/,
  'local Compose must pass the server-only proxy key at container runtime',
);
```

- [ ] **Step 2: Run the build contract and verify RED**

Run:

```bash
cd main/manager-web
npm run test:web-cache-policy
```

Expected: FAIL on the first old `false` default.

- [ ] **Step 3: Change the Dockerfile default**

In `Dockerfile-web`, change:

```dockerfile
ARG VUE_APP_NEST_AUTH_DISABLED=true
ENV VUE_APP_NEST_AUTH_DISABLED=$VUE_APP_NEST_AUTH_DISABLED
```

- [ ] **Step 4: Change local Compose defaults and runtime key wiring**

In `main/tbot-server/docker-compose_all.yml`, set:

```yaml
args:
  VUE_APP_NEST_AUTH_DISABLED: ${VUE_APP_NEST_AUTH_DISABLED:-true}
```

Add this line beside `NESTJS_TOKEN` in the web service environment:

```yaml
- NESTJS_ADMIN_PROXY_KEY=${NESTJS_ADMIN_PROXY_KEY:-}
```

Update the adjacent comment so local NestJS is started with matching
`TBOT_ADMIN_PROXY_KEY`, not `ADMIN_AUTH_DISABLED=true`.

- [ ] **Step 5: Change the local/release build-script default**

In `deploy/build-local.sh`, change the initialization to:

```bash
VUE_APP_NEST_AUTH_DISABLED="${VUE_APP_NEST_AUTH_DISABLED:-true}"
```

Change the usage text to:

```text
VUE_APP_NEST_AUTH_DISABLED
                           Defaults to true for server-proxy authentication.
                           Set false only to restore per-user NestJS login.
```

Keep the existing strict `true|false` validation and all existing forwarding to host/Docker builds.

- [ ] **Step 6: Change GitHub release defaults without breaking rollback**

In `.github/workflows/docker-image.yml`, change the input to:

```yaml
nest_auth_disabled:
  description: Build manager-web with server-proxy NestJS auth instead of per-user author login
  required: false
  type: boolean
  default: true
```

Change the build arg to:

```yaml
VUE_APP_NEST_AUTH_DISABLED=${{ github.event_name != 'workflow_dispatch' || inputs.nest_auth_disabled }}
```

Tag pushes and workflow-run releases therefore build `true`; a manual dispatch can explicitly choose `false`.

- [ ] **Step 7: Run the build and proxy wiring contracts**

Run:

```bash
cd main/manager-web
npm run test:web-cache-policy
npm run test:admin-proxy-key-wiring
```

Expected: both scripts exit 0.

- [ ] **Step 8: Commit build defaults**

```bash
git add Dockerfile-web \
  main/tbot-server/docker-compose_all.yml \
  deploy/build-local.sh \
  .github/workflows/docker-image.yml \
  main/manager-web/scripts/check-web-cache-policy.mjs
git commit -m "fix(deploy): bypass NestJS author login by default"
```

### Task 4: Update Operator Configuration and Verify Secret Isolation

**Files:**
- Modify: `deploy/.env.example`
- Modify: `deploy/PORTABILITY.md`

- [ ] **Step 1: Update the production environment example**

Replace the `/nestjs` authentication comment in `deploy/.env.example` with:

```text
# Course CMS (/nestjs) backend. nginx validates the primary manager bearer and
# injects NESTJS_ADMIN_PROXY_KEY server-side. Configure the matching backend
# value as TBOT_ADMIN_PROXY_KEY. Keep NESTJS_TOKEN empty unless rolling back to
# the legacy per-user NestJS login mode.
NESTJS_UPSTREAM_HOST=tbot-backend-8wmh.onrender.com
NESTJS_UPSTREAM_SCHEME=https
NESTJS_TOKEN=
NESTJS_ADMIN_PROXY_KEY=
```

Do not add a real key.

- [ ] **Step 2: Update the portability table and instructions**

In `deploy/PORTABILITY.md`, replace the shared-token row with:

```markdown
| admin proxy key | `NESTJS_ADMIN_PROXY_KEY` | — |
```

Add this instruction beneath the table:

```markdown
- Set manager-web `NESTJS_ADMIN_PROXY_KEY` and NestJS `TBOT_ADMIN_PROXY_KEY` to
  the same random value of at least 32 characters. The browser receives neither
  value; nginx injects the manager-web value only after manager super-admin
  authentication.
- Keep `NESTJS_TOKEN` empty in the normal deployment. It exists only for legacy
  rollback compatibility.
```

- [ ] **Step 3: Run focused contracts**

Run:

```bash
cd main/manager-web
npm run test:nest-auth-mode
npm run test:admin-proxy-key-wiring
npm run test:web-cache-policy
```

Expected: all three scripts exit 0.

- [ ] **Step 4: Build with a sentinel server-only key**

Run:

```bash
cd main/manager-web
NESTJS_ADMIN_PROXY_KEY=LOCAL_SENTINEL_PROXY_KEY_1234567890 npm run build
```

Expected: build exits 0. The variable is available only to `vue.config.js`; it is not a `VUE_APP_*` value.

- [ ] **Step 5: Prove the sentinel is absent from browser artifacts**

Run:

```bash
rg -n "LOCAL_SENTINEL_PROXY_KEY_1234567890" dist
```

Expected: exit 1 with no matches.

- [ ] **Step 6: Run the full Lesson Studio contract suite**

Run:

```bash
cd main/manager-web
npm run test:lesson-studio
```

Expected: all Lesson Studio contract scripts pass. If the browser-dependent checks require unavailable local services, record exactly which command is blocked and still run every non-browser script individually.

- [ ] **Step 7: Run backend proxy-key regressions**

Run:

```bash
cd ../../../../tbot-backend
npx vitest run \
  src/lessons/authoring/admin-proxy-key.spec.ts \
  src/lessons/authoring/admin-session.guard.proxy-key.spec.ts \
  src/lessons/authoring/admin-session.guard.auth-disabled-bypass.spec.ts \
  tests/prod-posture.spec.ts
```

Expected: all targeted backend tests pass; no backend production code change is needed.

- [ ] **Step 8: Review the final diff for secrets and unrelated changes**

Run from the `robot/esp32-server` repository root:

```bash
git diff --check
git diff -- main/manager-web Dockerfile-web deploy .github/workflows/docker-image.yml main/tbot-server/docker-compose_all.yml
```

Expected: no whitespace errors, no real credentials, and only the approved author-login bypass files changed. Preserve the pre-existing changes in `main/tbot-server/config/config_loader.py`, `main/tbot-server/tests/test_config_loader_edges.py`, `.playwright-cli/`, and `.superpowers/`.

- [ ] **Step 9: Commit docs and verification contract updates**

```bash
git add deploy/.env.example deploy/PORTABILITY.md
git commit -m "docs(deploy): configure proxy-backed NestJS author access"
```

## Completion Evidence

Before reporting completion, capture:

- `npm run test:nest-auth-mode` output.
- `npm run test:admin-proxy-key-wiring` output.
- `npm run test:web-cache-policy` output.
- `npm run test:lesson-studio` output or exact browser-service blockers.
- Sentinel build result and zero-match `rg` result.
- Targeted NestJS Vitest result.
- Final `git diff --check` result.

Production rollout still requires setting matching secret values in the actual manager-web and NestJS deployment environments and rebuilding/redeploying manager-web. Do not print those values in command output or commit them.
