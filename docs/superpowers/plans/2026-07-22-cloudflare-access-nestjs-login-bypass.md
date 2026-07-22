# Cloudflare Access NestJS Login Bypass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Cloudflare Access-authenticated VPS users enter the NestJS-backed admin pages without the second Author sign-in dialog while preserving normal session auth everywhere else.

**Architecture:** NestJS keeps the existing bypass principal but production permits it only with an explicit Cloudflare trust acknowledgement and a per-request `Cf-Access-Jwt-Assertion` header. manager-web centralizes its Nest auth mode in a small pure helper, omits session headers and dialogs in bypass builds, and exposes the build flag through Docker Compose without changing the safe default.

**Tech Stack:** NestJS 11, TypeScript, Vitest, Vue 2, Node contract tests, nginx, Docker Compose

---

## File Structure

- Modify `../../tbot-backend/tests/prod-posture.spec.ts`: production flag-pair acceptance and rejection tests.
- Modify `../../tbot-backend/src/prod-posture.ts`: fail-closed validation for the paired Cloudflare bypass flags.
- Modify `../../tbot-backend/src/lessons/authoring/admin-session.guard.auth-disabled-bypass.spec.ts`: request-header and local/production bypass coverage.
- Modify `../../tbot-backend/src/lessons/authoring/admin-session.guard.ts`: require the Cloudflare assertion in trusted production bypass mode.
- Create `main/manager-web/src/utils/nestAuthModeCore.mjs`: pure mode and 401-prompt decisions shared by browser code and Node tests.
- Create `main/manager-web/scripts/check-nest-auth-mode.mjs`: executable frontend/auth-mode contract test.
- Modify `main/manager-web/src/apis/nestHttp.js`: omit Nest session auth and suppress login events in bypass mode.
- Modify `main/manager-web/src/App.vue`: do not mount or subscribe to `NestLoginDialog` in bypass mode.
- Modify `main/manager-web/package.json`: add the focused auth-mode test command to the Lesson Studio suite.
- Modify `Dockerfile-web`: pass `VUE_APP_NEST_AUTH_DISABLED` into the Vue build.
- Modify `main/tbot-server/docker-compose_all.yml`: expose the build argument with a safe false default.
- Modify `main/manager-web/scripts/check-web-cache-policy.mjs`: statically verify Cloudflare header forwarding and build-flag wiring.

### Task 1: Production Posture Flag Pair

**Files:**
- Modify: `../../tbot-backend/tests/prod-posture.spec.ts`
- Modify: `../../tbot-backend/src/prod-posture.ts`

- [ ] **Step 1: Write failing production posture tests**

Add `TRUST_CLOUDFLARE_ACCESS` to `ENV_KEYS`, replace the old unconditional rejection test, and add:

```ts
it('allows the admin bypass only with explicit Cloudflare Access trust', () => {
  setProdEnv({
    ADMIN_AUTH_DISABLED: 'true',
    TRUST_CLOUDFLARE_ACCESS: 'true',
  });

  expect(() => assertProductionPosture()).not.toThrow();
});

it('rejects ADMIN_AUTH_DISABLED=true without Cloudflare Access trust', () => {
  setProdEnv({ ADMIN_AUTH_DISABLED: 'true' });

  expect(() => assertProductionPosture()).toThrow(
    'TRUST_CLOUDFLARE_ACCESS=true',
  );
});

it('rejects Cloudflare Access trust without the admin bypass', () => {
  setProdEnv({ TRUST_CLOUDFLARE_ACCESS: 'true' });

  expect(() => assertProductionPosture()).toThrow(
    'TRUST_CLOUDFLARE_ACCESS requires ADMIN_AUTH_DISABLED=true',
  );
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npx vitest run tests/prod-posture.spec.ts
```

Expected: FAIL because production still rejects every `ADMIN_AUTH_DISABLED=true` configuration and does not validate `TRUST_CLOUDFLARE_ACCESS`.

- [ ] **Step 3: Implement the paired production validation**

Replace the unconditional check in `src/prod-posture.ts` with:

```ts
const adminAuthDisabled = process.env.ADMIN_AUTH_DISABLED === 'true';
const trustsCloudflareAccess = process.env.TRUST_CLOUDFLARE_ACCESS === 'true';
if (adminAuthDisabled && !trustsCloudflareAccess) {
  failures.push(
    'ADMIN_AUTH_DISABLED=true requires TRUST_CLOUDFLARE_ACCESS=true in production',
  );
}
if (trustsCloudflareAccess && !adminAuthDisabled) {
  failures.push('TRUST_CLOUDFLARE_ACCESS requires ADMIN_AUTH_DISABLED=true');
}
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run `npx vitest run tests/prod-posture.spec.ts`.

Expected: all production posture tests PASS.

- [ ] **Step 5: Commit the backend posture change**

```bash
git add tests/prod-posture.spec.ts src/prod-posture.ts
git commit -m "feat(auth): acknowledge Cloudflare admin bypass"
```

### Task 2: Cloudflare Assertion Guard

**Files:**
- Modify: `../../tbot-backend/src/lessons/authoring/admin-session.guard.auth-disabled-bypass.spec.ts`
- Modify: `../../tbot-backend/src/lessons/authoring/admin-session.guard.ts`

- [ ] **Step 1: Extend the guard harness and write failing tests**

Track and restore both environment flags, and let `makeContext` accept headers:

```ts
function makeContext(opts: {
  authorization?: string;
  cloudflareAssertion?: string;
  requiredRoles?: string[];
}): { ctx: ExecutionContext; req: AdminAuthedRequest } {
  const headers: Record<string, string> = {};
  if (opts.authorization) headers.authorization = opts.authorization;
  if (opts.cloudflareAssertion) {
    headers['cf-access-jwt-assertion'] = opts.cloudflareAssertion;
  }
  // Keep the existing handler, controller, request, and context construction.
}
```

Add these cases:

```ts
it('allows trusted Cloudflare Access traffic without a Nest session', async () => {
  process.env.ADMIN_AUTH_DISABLED = 'true';
  process.env.TRUST_CLOUDFLARE_ACCESS = 'true';
  const calls: QueryCall[] = [];
  const guard = new AdminSessionGuard(fakePool({ existingAdmin: true, calls }) as never);
  const { ctx } = makeContext({
    cloudflareAssertion: 'opaque-cloudflare-assertion',
    requiredRoles: ['super_admin'],
  });

  await expect(guard.canActivate(ctx)).resolves.toBe(true);
});

it('rejects trusted bypass traffic when the Cloudflare assertion is missing', async () => {
  process.env.ADMIN_AUTH_DISABLED = 'true';
  process.env.TRUST_CLOUDFLARE_ACCESS = 'true';
  const calls: QueryCall[] = [];
  const guard = new AdminSessionGuard(fakePool({ existingAdmin: true, calls }) as never);
  const { ctx } = makeContext({ requiredRoles: ['super_admin'] });

  await expect(guard.canActivate(ctx)).rejects.toMatchObject({ status: 401 });
  expect(calls).toHaveLength(0);
});

it('keeps the token-less lab bypass outside Cloudflare trust mode', async () => {
  process.env.ADMIN_AUTH_DISABLED = 'true';
  delete process.env.TRUST_CLOUDFLARE_ACCESS;
  const calls: QueryCall[] = [];
  const guard = new AdminSessionGuard(fakePool({ existingAdmin: true, calls }) as never);
  const { ctx } = makeContext({ requiredRoles: ['super_admin'] });

  await expect(guard.canActivate(ctx)).resolves.toBe(true);
});
```

- [ ] **Step 2: Run the focused guard test and verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npx vitest run src/lessons/authoring/admin-session.guard.auth-disabled-bypass.spec.ts
```

Expected: the missing-assertion case FAILS because the current bypass accepts it.

- [ ] **Step 3: Add the assertion requirement before querying admin users**

Inside the `ADMIN_AUTH_DISABLED === 'true'` branch, before the first database query, add:

```ts
if (process.env.TRUST_CLOUDFLARE_ACCESS === 'true') {
  const assertion = req.headers['cf-access-jwt-assertion'];
  const hasAssertion = Array.isArray(assertion)
    ? assertion.some((value) => value.trim().length > 0)
    : typeof assertion === 'string' && assertion.trim().length > 0;
  if (!hasAssertion) {
    throw new UnauthorizedException({
      code: 'CLOUDFLARE_ACCESS_REQUIRED',
      message: 'Missing Cloudflare Access assertion',
      retryable: false,
    });
  }
}
```

Do not log or attach the assertion value.

- [ ] **Step 4: Run guard and posture regression tests**

Run:

```bash
npx vitest run \
  src/lessons/authoring/admin-session.guard.auth-disabled-bypass.spec.ts \
  src/lessons/authoring/admin-session.guard.spec.ts \
  tests/prod-posture.spec.ts
```

Expected: all selected tests PASS.

- [ ] **Step 5: Commit the backend guard change**

```bash
git add src/lessons/authoring/admin-session.guard.ts \
  src/lessons/authoring/admin-session.guard.auth-disabled-bypass.spec.ts
git commit -m "feat(auth): require Cloudflare assertion for admin bypass"
```

### Task 3: Frontend Nest Auth Mode

**Files:**
- Create: `main/manager-web/src/utils/nestAuthModeCore.mjs`
- Create: `main/manager-web/scripts/check-nest-auth-mode.mjs`
- Modify: `main/manager-web/src/apis/nestHttp.js`
- Modify: `main/manager-web/src/App.vue`
- Modify: `main/manager-web/package.json`

- [ ] **Step 1: Write the failing executable contract test**

Create `scripts/check-nest-auth-mode.mjs` with behavioral assertions and source contracts:

```js
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {
  isNestAuthDisabled,
  shouldPromptForNestAuth,
  shouldSendNestSessionToken,
} from '../src/utils/nestAuthModeCore.mjs';

assert.equal(isNestAuthDisabled('true'), true);
assert.equal(isNestAuthDisabled('false'), false);
assert.equal(isNestAuthDisabled(undefined), false);
assert.equal(shouldPromptForNestAuth({ disabled: false, status: 401 }), true);
assert.equal(shouldPromptForNestAuth({ disabled: true, status: 401 }), false);
assert.equal(shouldSendNestSessionToken({ disabled: true, token: 'secret' }), false);
assert.equal(shouldSendNestSessionToken({ disabled: false, token: 'secret' }), true);

const root = path.resolve(import.meta.dirname, '..');
const app = fs.readFileSync(path.join(root, 'src/App.vue'), 'utf8');
const http = fs.readFileSync(path.join(root, 'src/apis/nestHttp.js'), 'utf8');
assert.match(app, /v-if="!nestAuthDisabled"/);
assert.match(app, /if \(!this\.nestAuthDisabled\).*addEventListener/s);
assert.match(http, /shouldPromptForNestAuth/);
assert.match(http, /shouldSendNestSessionToken/);

console.log('Nest auth mode contracts PASS');
```

Add the package command:

```json
"test:nest-auth-mode": "node scripts/check-nest-auth-mode.mjs"
```

and prepend it to `test:lesson-studio`.

- [ ] **Step 2: Run the focused frontend test and verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
npm run test:nest-auth-mode
```

Expected: FAIL because `nestAuthModeCore.mjs` and the bypass integrations do not exist.

- [ ] **Step 3: Add the pure mode helper**

Create `src/utils/nestAuthModeCore.mjs`:

```js
export function isNestAuthDisabled(value = process.env.VUE_APP_NEST_AUTH_DISABLED) {
  return value === 'true';
}

export function shouldPromptForNestAuth({ disabled, status }) {
  return !disabled && Number(status) === 401;
}

export function shouldSendNestSessionToken({ disabled, token }) {
  return !disabled && typeof token === 'string' && token.length > 0;
}
```

- [ ] **Step 4: Integrate bypass decisions into Nest HTTP requests**

Import the helper functions into `src/apis/nestHttp.js`, define:

```js
const nestAuthDisabled = isNestAuthDisabled();
```

Change `clearNestSession(status = 401)` so it always clears stale local storage,
but dispatches `tbot:nest-auth-required` only when:

```js
if (shouldPromptForNestAuth({ disabled: nestAuthDisabled, status })) {
  window.dispatchEvent(new CustomEvent('tbot:nest-auth-required'));
}
```

Change `nestTokenHeader()` to return `{}` unless:

```js
shouldSendNestSessionToken({ disabled: nestAuthDisabled, token })
```

Keep the existing 401 callback/error propagation so bypass-mode configuration
failures remain visible to the page.

- [ ] **Step 5: Disable the dialog lifecycle in App.vue**

Use the helper in `App.vue`:

```vue
<nest-login-dialog
  v-if="!nestAuthDisabled"
  ref="nestLogin"
  @logged-in="onNestLoggedIn"
/>
```

```js
import { isNestAuthDisabled } from '@/utils/nestAuthModeCore.mjs';

data() {
  return {
    nestAuthDisabled: isNestAuthDisabled(),
    // existing fields
  };
},
mounted() {
  if (!this.nestAuthDisabled) {
    window.addEventListener('tbot:nest-auth-required', this.openNestLogin);
  }
  // existing mounted behavior
},
beforeDestroy() {
  if (!this.nestAuthDisabled) {
    window.removeEventListener('tbot:nest-auth-required', this.openNestLogin);
  }
  // existing cleanup
},
openNestLogin() {
  if (this.nestAuthDisabled) return;
  // existing ref/open logic
}
```

- [ ] **Step 6: Run focused frontend tests and build**

Run:

```bash
npm run test:nest-auth-mode
node scripts/check-lesson-rollout-capabilities.mjs
VUE_APP_NEST_AUTH_DISABLED=true npm run build
```

Expected: both contract tests PASS and the production Vue build completes.

- [ ] **Step 7: Commit the frontend behavior**

```bash
git add main/manager-web/src/utils/nestAuthModeCore.mjs \
  main/manager-web/scripts/check-nest-auth-mode.mjs \
  main/manager-web/src/apis/nestHttp.js \
  main/manager-web/src/App.vue \
  main/manager-web/package.json
git commit -m "feat(manager-web): skip Nest login behind Cloudflare Access"
```

### Task 4: Docker and Proxy Deployment Wiring

**Files:**
- Modify: `Dockerfile-web`
- Modify: `main/tbot-server/docker-compose_all.yml`
- Modify: `main/manager-web/scripts/check-web-cache-policy.mjs`

- [ ] **Step 1: Add failing static deployment checks**

Extend `check-web-cache-policy.mjs` with:

```js
expectContains(
  'docs/docker/nginx.conf',
  'proxy_set_header Cf-Access-Jwt-Assertion $http_cf_access_jwt_assertion;',
  'the Cloudflare assertion must be explicitly forwarded to NestJS',
);
expectRegex(
  'Dockerfile-web',
  /ARG VUE_APP_NEST_AUTH_DISABLED=false[\s\S]*ENV VUE_APP_NEST_AUTH_DISABLED=\$VUE_APP_NEST_AUTH_DISABLED[\s\S]*RUN npm run build/,
  'the Vue production build must receive the Nest auth bypass flag',
);
expectRegex(
  'main/tbot-server/docker-compose_all.yml',
  /args:[\s\S]*VUE_APP_NEST_AUTH_DISABLED:\s*\$\{VUE_APP_NEST_AUTH_DISABLED:-false\}/,
  'Compose must expose the bypass build arg with a safe default',
);
```

- [ ] **Step 2: Run the deployment check and verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
npm run test:web-cache-policy
```

Expected: FAIL because Docker and nginx do not yet contain the explicit wiring.

- [ ] **Step 3: Forward the Cloudflare assertion explicitly**

In `docs/docker/nginx.conf`, add inside `location /nestjs/`:

```nginx
proxy_set_header Cf-Access-Jwt-Assertion $http_cf_access_jwt_assertion;
```

Keep the existing `Authorization`, client IP, and forwarded-for headers.

- [ ] **Step 4: Pass the Vue build flag through Docker**

In the `web-builder` stage of `Dockerfile-web`, before `RUN npm run build`, add:

```dockerfile
ARG VUE_APP_NEST_AUTH_DISABLED=false
ENV VUE_APP_NEST_AUTH_DISABLED=$VUE_APP_NEST_AUTH_DISABLED
```

In the `tbot-esp32-server-web` build block of
`main/tbot-server/docker-compose_all.yml`, add:

```yaml
build:
  context: ../..
  dockerfile: Dockerfile-web
  args:
    VUE_APP_NEST_AUTH_DISABLED: ${VUE_APP_NEST_AUTH_DISABLED:-false}
```

- [ ] **Step 5: Run deployment and frontend regressions**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
npm run test:web-cache-policy
npm run test:nest-auth-mode
```

Expected: both checks PASS.

- [ ] **Step 6: Commit deployment wiring**

```bash
git add Dockerfile-web docs/docker/nginx.conf \
  main/tbot-server/docker-compose_all.yml \
  main/manager-web/scripts/check-web-cache-policy.mjs
git commit -m "chore(deploy): wire Cloudflare Nest auth bypass"
```

### Task 5: Full Verification

**Files:**
- Verify only

- [ ] **Step 1: Run backend verification**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npx vitest run \
  tests/prod-posture.spec.ts \
  src/lessons/authoring/admin-session.guard.auth-disabled-bypass.spec.ts \
  src/lessons/authoring/admin-session.guard.spec.ts
npm run typecheck
npm run build
```

Expected: selected tests, TypeScript, and Nest build all PASS.

- [ ] **Step 2: Run manager-web verification**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
npm run test:nest-auth-mode
npm run test:web-cache-policy
node scripts/check-lesson-rollout-capabilities.mjs
VUE_APP_NEST_AUTH_DISABLED=true npm run build
```

Expected: all contract checks and the bypass-mode production build PASS.

- [ ] **Step 3: Review diffs for scope and secret safety**

Run in both repositories:

```bash
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors, no assertion/token values committed, and no
unrelated user changes included in feature commits.

- [ ] **Step 4: Record VPS activation values for handoff**

Provide these exact operator settings without storing secrets in the repository:

```text
tbot-backend:
  ADMIN_AUTH_DISABLED=true
  TRUST_CLOUDFLARE_ACCESS=true

esp32-server image build:
  VUE_APP_NEST_AUTH_DISABLED=true

manager-web runtime:
  NESTJS_TOKEN=
```

Also require external verification that direct origin access is blocked before
enabling the two backend flags.
