# NestJS Admin Proxy Key Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Authenticate every NestJS `AdminSessionGuard` route through a server-only nginx proxy key so manager-web can use the admin services without a second login.

**Architecture:** A focused backend helper validates and constant-time compares current/previous proxy keys, while `AdminSessionGuard` maps a matching key to the existing super-admin placeholder principal and preserves normal session login when no key is presented. manager-web nginx injects the key at runtime from VPS environment; the Vue bundle receives only the existing auth-disabled UI build flag and never receives the key.

**Tech Stack:** NestJS 11, TypeScript, Vitest, Vue 2, nginx, Bash, Docker Compose, Docker

---

## File Structure

- Create `../../tbot-backend/src/lessons/authoring/admin-proxy-key.ts`: key parsing, validation, and constant-time matching.
- Create `../../tbot-backend/src/lessons/authoring/admin-proxy-key.spec.ts`: pure key validation and comparison tests.
- Modify `../../tbot-backend/src/lessons/authoring/admin-session.guard.ts`: proxy-key authentication branch and shared trusted principal helper.
- Create `../../tbot-backend/src/lessons/authoring/admin-session.guard.proxy-key.spec.ts`: guard behavior for current, previous, wrong, and missing keys.
- Modify `../../tbot-backend/src/prod-posture.ts`: validate configured proxy keys during production startup.
- Modify `../../tbot-backend/tests/prod-posture.spec.ts`: production key acceptance/rejection coverage.
- Modify `docs/docker/start.sh`: render the runtime-only proxy key into nginx safely.
- Modify `docs/docker/nginx.conf`: overwrite `X-TBOT-Admin-Key` for `/nestjs/` upstream requests.
- Modify `deploy/docker-compose.prod.yml`: pass the key from `/opt/tbot/.env` into manager-web.
- Modify `deploy/.env.example`: document the runtime key variable.
- Modify `deploy/redeploy-web.sh`: preserve the key during web-only container replacement.
- Create `main/manager-web/scripts/check-admin-proxy-key-wiring.mjs`: deployment wiring and browser-bundle secret-exclusion contracts.
- Modify `main/manager-web/package.json`: expose the focused contract test.
- Modify `main/manager-web/scripts/check-web-cache-policy.mjs`: retain the nginx/header contract in the existing release gate.

### Task 1: Proxy Key Validation and Constant-Time Matching

**Files:**
- Create: `../../tbot-backend/src/lessons/authoring/admin-proxy-key.ts`
- Create: `../../tbot-backend/src/lessons/authoring/admin-proxy-key.spec.ts`

- [ ] **Step 1: Write the failing pure tests**

Create `admin-proxy-key.spec.ts`:

```ts
import { describe, expect, it } from 'vitest';
import {
  loadAdminProxyKeys,
  matchesAdminProxyKey,
} from './admin-proxy-key';

describe('admin proxy key configuration', () => {
  it('returns no keys when the feature is unconfigured', () => {
    expect(loadAdminProxyKeys({})).toEqual([]);
  });

  it('loads the current key followed by the temporary previous key', () => {
    expect(loadAdminProxyKeys({
      TBOT_ADMIN_PROXY_KEY: 'a'.repeat(48),
      TBOT_ADMIN_PROXY_KEY_PREVIOUS: 'b'.repeat(48),
    })).toEqual(['a'.repeat(48), 'b'.repeat(48)]);
  });

  it.each([
    ['short', 'a'.repeat(31)],
    ['leading whitespace', ` ${'a'.repeat(48)}`],
    ['trailing whitespace', `${'a'.repeat(48)} `],
    ['placeholder', 'change-me'],
    ['placeholder secret', 'secret'],
    ['placeholder password', 'password'],
  ])('rejects %s key material', (_name, value) => {
    expect(() => loadAdminProxyKeys({ TBOT_ADMIN_PROXY_KEY: value })).toThrow(
      /TBOT_ADMIN_PROXY_KEY/,
    );
  });
});

describe('matchesAdminProxyKey', () => {
  const current = 'a'.repeat(48);
  const previous = 'b'.repeat(48);

  it('accepts current and previous keys', () => {
    expect(matchesAdminProxyKey(current, [current, previous])).toBe(true);
    expect(matchesAdminProxyKey(previous, [current, previous])).toBe(true);
  });

  it('rejects equal-length and unequal-length wrong keys', () => {
    expect(matchesAdminProxyKey('c'.repeat(48), [current, previous])).toBe(false);
    expect(matchesAdminProxyKey('short', [current, previous])).toBe(false);
  });
});
```

- [ ] **Step 2: Run the pure test and verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npx vitest run src/lessons/authoring/admin-proxy-key.spec.ts
```

Expected: FAIL because `admin-proxy-key.ts` does not exist.

- [ ] **Step 3: Implement the pure helper**

Create `admin-proxy-key.ts`:

```ts
import { timingSafeEqual } from 'node:crypto';

type AdminProxyKeyEnvironment = Partial<Record<
  'TBOT_ADMIN_PROXY_KEY' | 'TBOT_ADMIN_PROXY_KEY_PREVIOUS',
  string | undefined
>>;

const PLACEHOLDERS = new Set(['change-me', 'secret', 'password']);

function validate(name: string, value: string | undefined): string | undefined {
  if (value === undefined || value === '') return undefined;
  if (value !== value.trim() || value.length < 32 || PLACEHOLDERS.has(value.toLowerCase())) {
    throw new Error(`${name} must be at least 32 characters, trimmed, and non-placeholder`);
  }
  return value;
}

export function loadAdminProxyKeys(env: AdminProxyKeyEnvironment): string[] {
  return [
    validate('TBOT_ADMIN_PROXY_KEY', env.TBOT_ADMIN_PROXY_KEY),
    validate('TBOT_ADMIN_PROXY_KEY_PREVIOUS', env.TBOT_ADMIN_PROXY_KEY_PREVIOUS),
  ].filter((value): value is string => value !== undefined);
}

export function matchesAdminProxyKey(presented: string, configured: readonly string[]): boolean {
  const candidate = Buffer.from(presented);
  return configured.some((key) => {
    const expected = Buffer.from(key);
    return candidate.length === expected.length && timingSafeEqual(candidate, expected);
  });
}
```

- [ ] **Step 4: Run the pure test and verify GREEN**

Run `npx vitest run src/lessons/authoring/admin-proxy-key.spec.ts`.

Expected: all tests PASS.

- [ ] **Step 5: Commit the helper**

```bash
git add src/lessons/authoring/admin-proxy-key.ts \
  src/lessons/authoring/admin-proxy-key.spec.ts
git commit -m "feat(auth): validate admin proxy keys"
```

### Task 2: AdminSessionGuard Proxy Authentication

**Files:**
- Modify: `../../tbot-backend/src/lessons/authoring/admin-session.guard.ts`
- Create: `../../tbot-backend/src/lessons/authoring/admin-session.guard.proxy-key.spec.ts`

- [ ] **Step 1: Write the failing guard tests**

Create a fake-pool harness matching the existing bypass spec and cover:

```ts
it('authenticates the current proxy key as super_admin', async () => {
  process.env.TBOT_ADMIN_PROXY_KEY = CURRENT_KEY;
  const { guard, context, request, calls } = harness({
    headers: { 'x-tbot-admin-key': CURRENT_KEY },
  });

  await expect(guard.canActivate(context)).resolves.toBe(true);
  expect(request.admin).toMatchObject({
    role: 'super_admin',
    sessionId: 'admin-proxy-key',
    canAuthorLessons: true,
  });
  expect(calls.some((sql) => sql.includes('FROM admin_sessions'))).toBe(false);
});

it('accepts the temporary previous key during rotation', async () => {
  process.env.TBOT_ADMIN_PROXY_KEY = CURRENT_KEY;
  process.env.TBOT_ADMIN_PROXY_KEY_PREVIOUS = PREVIOUS_KEY;
  const { guard, context } = harness({
    headers: { 'x-tbot-admin-key': PREVIOUS_KEY },
  });

  await expect(guard.canActivate(context)).resolves.toBe(true);
});

it('rejects a presented incorrect key without session fallback', async () => {
  process.env.TBOT_ADMIN_PROXY_KEY = CURRENT_KEY;
  const { guard, context, calls } = harness({
    headers: { 'x-tbot-admin-key': 'c'.repeat(48) },
  });

  await expect(guard.canActivate(context)).rejects.toMatchObject({ status: 401 });
  expect(calls).toHaveLength(0);
});

it('keeps normal missing-session behavior when no proxy header is presented', async () => {
  process.env.TBOT_ADMIN_PROXY_KEY = CURRENT_KEY;
  const { guard, context, calls } = harness({ headers: {} });

  await expect(guard.canActivate(context)).rejects.toMatchObject({ status: 401 });
  expect(calls).toHaveLength(0);
});
```

Restore `TBOT_ADMIN_PROXY_KEY`, `TBOT_ADMIN_PROXY_KEY_PREVIOUS`,
`ADMIN_AUTH_DISABLED`, and `TRUST_CLOUDFLARE_ACCESS` after every test.

- [ ] **Step 2: Run the guard test and verify RED**

Run:

```bash
npx vitest run src/lessons/authoring/admin-session.guard.proxy-key.spec.ts
```

Expected: current/previous key cases FAIL because the guard only supports
session tokens and the older bypass mechanisms.

- [ ] **Step 3: Integrate key loading and header validation**

In `AdminSessionGuard`:

```ts
import { loadAdminProxyKeys, matchesAdminProxyKey } from './admin-proxy-key';

private readonly adminProxyKeys: string[];

constructor(@Inject('PG_POOL') private readonly pool: Pool) {
  this.adminProxyKeys = loadAdminProxyKeys(process.env);
}
```

Before the `ADMIN_AUTH_DISABLED` branch:

```ts
const presentedProxyKey = this.headerValue(req, 'x-tbot-admin-key');
if (presentedProxyKey !== undefined) {
  if (!matchesAdminProxyKey(presentedProxyKey, this.adminProxyKeys)) {
    throw new UnauthorizedException({
      code: 'ADMIN_PROXY_KEY_INVALID',
      message: 'Invalid admin proxy key',
      retryable: false,
    });
  }
  return this.attachTrustedPrincipal(req, 'admin-proxy-key');
}
```

Extract the repeated placeholder-admin query from the bypass branch:

```ts
private async attachTrustedPrincipal(
  req: AdminAuthedRequest,
  sessionId: string,
): Promise<true> {
  let anyAdmin = await this.pool.query<{ id: string }>(
    `SELECT id FROM admin_users WHERE status = 'active' ORDER BY created_at LIMIT 1`,
  );
  if ((anyAdmin.rowCount ?? 0) === 0) {
    anyAdmin = await this.pool.query<{ id: string }>(
      `INSERT INTO admin_users (email, password_hash, role, status)
       VALUES ('auth-disabled@internal.local', 'disabled', 'admin', 'active')
       ON CONFLICT (email) DO UPDATE SET status = 'active'
       RETURNING id`,
    );
  }
  req.admin = {
    adminUserId: anyAdmin.rows[0].id,
    role: 'super_admin',
    sessionId,
    canAuthorLessons: true,
  };
  return true;
}
```

Implement `headerValue` so duplicate/array headers fail closed rather than
silently selecting one value.

- [ ] **Step 4: Run guard regressions**

```bash
npx vitest run \
  src/lessons/authoring/admin-proxy-key.spec.ts \
  src/lessons/authoring/admin-session.guard.proxy-key.spec.ts \
  src/lessons/authoring/admin-session.guard.auth-disabled-bypass.spec.ts \
  src/lessons/authoring/admin-session.guard.spec.ts
```

Expected: all selected tests PASS.

- [ ] **Step 5: Commit the guard integration**

```bash
git add src/lessons/authoring/admin-session.guard.ts \
  src/lessons/authoring/admin-session.guard.proxy-key.spec.ts
git commit -m "feat(auth): accept server-side admin proxy key"
```

### Task 3: Production Posture Validation

**Files:**
- Modify: `../../tbot-backend/src/prod-posture.ts`
- Modify: `../../tbot-backend/tests/prod-posture.spec.ts`

- [ ] **Step 1: Add failing production tests**

Add both proxy-key variables to the test environment cleanup list and cover:

```ts
it('accepts a strong admin proxy key without disabling admin auth', () => {
  setProdEnv({ TBOT_ADMIN_PROXY_KEY: 'a'.repeat(48) });
  expect(() => assertProductionPosture()).not.toThrow();
});

it.each([
  ['short current', { TBOT_ADMIN_PROXY_KEY: 'short' }],
  ['padded current', { TBOT_ADMIN_PROXY_KEY: ` ${'a'.repeat(48)}` }],
  ['placeholder current', { TBOT_ADMIN_PROXY_KEY: 'change-me' }],
  ['short previous', {
    TBOT_ADMIN_PROXY_KEY: 'a'.repeat(48),
    TBOT_ADMIN_PROXY_KEY_PREVIOUS: 'short',
  }],
])('rejects %s admin proxy key configuration', (_name, overrides) => {
  setProdEnv(overrides);
  expect(() => assertProductionPosture()).toThrow(/TBOT_ADMIN_PROXY_KEY/);
});
```

- [ ] **Step 2: Run and verify RED**

Run `npx vitest run tests/prod-posture.spec.ts`.

Expected: invalid key cases do not yet fail.

- [ ] **Step 3: Reuse the key loader in production posture**

At the start of `assertProductionPosture`, after the production runtime check:

```ts
try {
  loadAdminProxyKeys(process.env);
} catch (error) {
  failures.push(error instanceof Error ? error.message : 'admin proxy key is invalid');
}
```

Keep `ADMIN_AUTH_DISABLED`/Cloudflare pairing validation unchanged; proxy-key
authentication does not require either flag.

- [ ] **Step 4: Run and verify GREEN**

Run `npx vitest run tests/prod-posture.spec.ts`.

Expected: all tests PASS.

- [ ] **Step 5: Commit posture validation**

```bash
git add src/prod-posture.ts tests/prod-posture.spec.ts
git commit -m "feat(auth): validate production admin proxy key"
```

### Task 4: nginx Runtime Injection

**Files:**
- Modify: `docs/docker/start.sh`
- Modify: `docs/docker/nginx.conf`
- Modify: `deploy/docker-compose.prod.yml`
- Modify: `deploy/.env.example`
- Modify: `deploy/redeploy-web.sh`
- Create: `main/manager-web/scripts/check-admin-proxy-key-wiring.mjs`
- Modify: `main/manager-web/package.json`
- Modify: `main/manager-web/scripts/check-web-cache-policy.mjs`

- [ ] **Step 1: Write the failing wiring contract**

Create `check-admin-proxy-key-wiring.mjs` that reads repository files and asserts:

```js
expectContains('docs/docker/start.sh', ': "${NESTJS_ADMIN_PROXY_KEY:=}"');
expectContains('docs/docker/start.sh', 'NESTJS_ADMIN_PROXY_KEY_ESCAPED=');
expectContains('docs/docker/start.sh', '__NESTJS_ADMIN_PROXY_KEY__');
expectContains(
  'docs/docker/nginx.conf',
  'proxy_set_header X-TBOT-Admin-Key "__NESTJS_ADMIN_PROXY_KEY__";',
);
expectContains(
  'deploy/docker-compose.prod.yml',
  'NESTJS_ADMIN_PROXY_KEY: ${NESTJS_ADMIN_PROXY_KEY:-}',
);
expectContains('deploy/.env.example', 'NESTJS_ADMIN_PROXY_KEY=');
expectContains('deploy/redeploy-web.sh', 'NESTJS_ADMIN_PROXY_KEY');
expectNotContains('Dockerfile-web', 'NESTJS_ADMIN_PROXY_KEY');
expectNotContains('main/manager-web/src', 'NESTJS_ADMIN_PROXY_KEY');
```

Add:

```json
"test:admin-proxy-key-wiring": "node scripts/check-admin-proxy-key-wiring.mjs"
```

- [ ] **Step 2: Run and verify RED**

Run `npm run test:admin-proxy-key-wiring` from `main/manager-web`.

Expected: FAIL because the runtime placeholders and Compose wiring are missing.

- [ ] **Step 3: Render the key in start.sh**

Add:

```bash
: "${NESTJS_ADMIN_PROXY_KEY:=}"
NESTJS_ADMIN_PROXY_KEY_ESCAPED=$(printf '%s' "${NESTJS_ADMIN_PROXY_KEY}" \
  | sed -e 's/[&|\\]/\\&/g')
```

and a sed replacement:

```bash
-e "s|__NESTJS_ADMIN_PROXY_KEY__|${NESTJS_ADMIN_PROXY_KEY_ESCAPED}|g"
```

Do not echo the key or rendered nginx config.

- [ ] **Step 4: Overwrite the upstream header in nginx**

Inside `/nestjs/`, add:

```nginx
proxy_set_header X-TBOT-Admin-Key "__NESTJS_ADMIN_PROXY_KEY__";
```

Do not copy `$http_x_tbot_admin_key`; nginx must discard browser-provided values.

- [ ] **Step 5: Wire runtime deploy inputs**

Add the variable to `deploy/.env.example`, the web service environment in
`deploy/docker-compose.prod.yml`, and the `docker run` environment list in
`deploy/redeploy-web.sh`:

```yaml
NESTJS_ADMIN_PROXY_KEY: ${NESTJS_ADMIN_PROXY_KEY:-}
```

```bash
-e "NESTJS_ADMIN_PROXY_KEY=${NESTJS_ADMIN_PROXY_KEY}" \
```

The value is runtime-only and must not be added to `Dockerfile-web` arguments.

- [ ] **Step 6: Run wiring and existing frontend gates**

```bash
npm run test:admin-proxy-key-wiring
npm run test:nest-auth-mode
npm run test:web-cache-policy
bash -n ../../docs/docker/start.sh
bash -n ../../deploy/redeploy-web.sh
```

Expected: all checks PASS.

- [ ] **Step 7: Commit nginx/deployment wiring**

```bash
git add docs/docker/start.sh docs/docker/nginx.conf deploy/docker-compose.prod.yml \
  deploy/.env.example deploy/redeploy-web.sh main/manager-web/package.json \
  main/manager-web/scripts/check-admin-proxy-key-wiring.mjs \
  main/manager-web/scripts/check-web-cache-policy.mjs
git commit -m "feat(deploy): inject NestJS admin proxy key"
```

### Task 5: Secret Exclusion and Build Verification

**Files:**
- Verify only

- [ ] **Step 1: Build manager-web with a sentinel runtime key**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
NESTJS_ADMIN_PROXY_KEY='sentinel-admin-proxy-key-never-in-browser-123456789' \
VUE_APP_NEST_AUTH_DISABLED=true npm run build
```

Expected: build succeeds with only existing asset-size warnings.

- [ ] **Step 2: Prove the key is absent from browser artifacts**

```bash
if rg -F 'sentinel-admin-proxy-key-never-in-browser-123456789' dist; then
  echo 'proxy key leaked into browser artifacts' >&2
  exit 1
fi
```

Expected: no matches, exit 0.

- [ ] **Step 3: Run backend verification**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npx vitest run \
  src/lessons/authoring/admin-proxy-key.spec.ts \
  src/lessons/authoring/admin-session.guard.proxy-key.spec.ts \
  src/lessons/authoring/admin-session.guard.auth-disabled-bypass.spec.ts \
  src/lessons/authoring/admin-session.guard.spec.ts \
  tests/prod-posture.spec.ts
npm run typecheck
npm run build
```

Expected: tests, typecheck, and build PASS.

- [ ] **Step 4: Run deployment verification**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
npm run test:admin-proxy-key-wiring
npm run test:nest-auth-mode
npm run test:web-cache-policy
cd ../..
VUE_APP_NEST_AUTH_DISABLED=true docker compose \
  -f deploy/docker-compose.prod.yml config --quiet
```

Expected: checks and Compose validation PASS.

### Task 6: VPS Deployment and Rollback Proof

**Files:**
- Remote: `/opt/tbot/.env`
- Remote: `/opt/tbot/tbot-cms-api.env`
- Remote Docker images/containers only

- [ ] **Step 1: Capture the current rollback state without printing secrets**

Record:

```bash
ssh -i ~/.ssh/tbot_vps_ed25519 -p 22701 root@160.187.240.56 '
  docker inspect tbot-esp32-server-web --format "web={{.Config.Image}}";
  docker inspect tbot-cms-api --format "cms={{.Config.Image}}";
  grep -E "^(TBOT_WEB_IMAGE|NESTJS_UPSTREAM_HOST|NESTJS_UPSTREAM_SCHEME)=" /opt/tbot/.env
'
```

Never output the current `NESTJS_TOKEN`, database URL, JWT keys, or new proxy key.

- [ ] **Step 2: Build transferable amd64 images locally**

Use one release tag:

```bash
TAG="admin-proxy-key-$(date +%Y%m%d%H%M%S)"

cd /Users/manhhodinh/Documents/TBOT/tbot-backend
docker build --platform linux/amd64 -t "local/tbot-backend:$TAG" .

cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
VUE_APP_NEST_AUTH_DISABLED=true ./deploy/build-local.sh \
  --tag "$TAG" --platform linux/amd64 --only web
```

Expected: both images build successfully.

- [ ] **Step 3: Transfer and load images**

```bash
docker save "local/tbot-backend:$TAG" | gzip > "/tmp/tbot-backend-$TAG.tar.gz"
docker save "local/tbot-server-web:$TAG" | gzip > "/tmp/tbot-web-$TAG.tar.gz"
scp -i ~/.ssh/tbot_vps_ed25519 -P 22701 \
  "/tmp/tbot-backend-$TAG.tar.gz" "/tmp/tbot-web-$TAG.tar.gz" \
  root@160.187.240.56:/opt/tbot/releases/
ssh -i ~/.ssh/tbot_vps_ed25519 -p 22701 root@160.187.240.56 \
  "docker load < /opt/tbot/releases/tbot-backend-$TAG.tar.gz && \
   docker load < /opt/tbot/releases/tbot-web-$TAG.tar.gz"
```

- [ ] **Step 4: Generate and install the matching server-side key**

Generate a 48-byte random Base64 key locally without printing it. Send it over
SSH stdin to a root-only remote update command that:

- writes `TBOT_ADMIN_PROXY_KEY` in `/opt/tbot/tbot-cms-api.env`;
- writes `NESTJS_ADMIN_PROXY_KEY` in `/opt/tbot/.env`;
- removes `ADMIN_AUTH_DISABLED`, `TRUST_CLOUDFLARE_ACCESS`,
  `TBOT_ADMIN_PROXY_KEY_PREVIOUS`, and the obsolete `NESTJS_TOKEN` value;
- sets both files to mode `600`.

The command output reports only key length and SHA-256 prefix, never the key.

- [ ] **Step 5: Recreate the private CMS backend**

Before replacing the container, export its current environment directly to the
root-only env file if that file does not exist. Recreate `tbot-cms-api` with:

```text
image: local/tbot-backend:<TAG>
network: tbot
container name: tbot-cms-api
restart: unless-stopped
env-file: /opt/tbot/tbot-cms-api.env
no published host port
```

Preserve the existing command, mounts, labels, and network aliases from
`docker inspect`. Stop only after the replacement command is fully prepared.

- [ ] **Step 6: Point manager-web at the private backend and redeploy web**

Update `/opt/tbot/.env` without printing secrets:

```text
TBOT_WEB_IMAGE=local/tbot-server-web:<TAG>
NESTJS_UPSTREAM_HOST=tbot-cms-api:3000
NESTJS_UPSTREAM_SCHEME=http
NESTJS_TOKEN=
```

Run `deploy/redeploy-web.sh` or the canonical Compose web service replacement so
the existing database, Redis, mounts, and healthcheck are preserved.

- [ ] **Step 7: Verify authentication boundaries**

Prove all of these:

```text
GET https://admin.tjbot.vn/                                  -> 200
GET https://admin.tjbot.vn/nestjs/v1/admin/courses          -> 200 through nginx
GET private CMS /v1/admin/courses without X-TBOT-Admin-Key  -> 401
GET private CMS /v1/admin/courses with an incorrect key     -> 401
GET private CMS /v1/health                                  -> 200
```

Use an ephemeral Docker container on network `tbot` for private CMS probes and
never print the valid key.

Open Safari at `https://admin.tjbot.vn/#/course-management`; verify the page
loads without `NestLoginDialog` and can read at least one admin endpoint.

- [ ] **Step 8: Roll back on any failed gate**

Restore the recorded prior CMS/web image tags and the prior upstream host/scheme,
remove the proxy-key variables, recreate both containers, and rerun health/smoke
checks. Do not leave a half-configured key or a public backend bypass enabled.
