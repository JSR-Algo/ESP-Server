# ESP-Server Master Build Plan
**Generated:** 2026-05-27  
**Source:** AUDIT_REPORT_2026-05-27.md  
**Goal:** Remediate all CRITICAL + HIGH findings via parallel agent workstreams.  
**Constraint:** Overnight delivery (max 1 autonomous cycle). Must be parallelizable and merge-safe.

---

## Strategic Decisions Required (Answer Before Dispatch)

### Q1. Web Frontend Strategy
The audit found `manager-web` runs **Vue 2.6.14 (EOL)** with zero TypeScript, zero tests, fake Chinese i18n, and severe security issues (localStorage tokens, no ESLint).

| Option | Scope | Files Touched | Risk |
|--------|-------|---------------|------|
| **A. Patch in Place** (Recommended for overnight) | Fix i18n stubs, add ESLint + Prettier, patch security (localStorage wrapper, XSS guard), fix router guards, fix `sideEffects`, fix `JSON.parse` crashes. Keep Vue 2. | ~25 files | Low |
| **B. Vue 3 Migration** | Rewrite to Vue 3 + Vite + TypeScript + Pinia. Full component migration. | ~100+ files | High — not overnight-safe |
| **C. Deprecate + Redirect** | Strip admin features from web, redirect to mobile PWA. | ~50 files | Medium — product decision needed |

### Q2. Mobile Feature Scope
The audit found mobile lacks ~60% of web admin features (knowledge base, voice clone, OTA, model config, etc.).

| Option | Scope |
|--------|-------|
| **A. Lite Admin** (Recommended for overnight) | Keep current scope. Fix hardcoded URLs, enable ESLint, fix auth refresh TODO, clean up types. Do NOT add new admin screens. | 
| **B. Full Parity Sprint** | Add missing API modules + screens. ~15 new views + API wrappers. Not overnight-safe. |
| **C. Hybrid PWA** | Expose missing features via responsive web wrapper inside mobile. Requires web changes too. |

### Q3. Java Package Consolidation Safety
The `xiaozhi.*` vs `tbot.*` duplication is 600+ files. The POM points to `tbot.AdminApplication`. 
- **Can I assume `tbot.*` is the canonical namespace?**
- **Are there any active deployments running `xiaozhi.*` classes?**
- **Do you have a test DB I can validate migrations against?**

---

## Workstream Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    FOUNDATION LAYER (Sequential)                     │
│  1. Branch/Worktree Setup   →   2. Secret Pattern Definition         │
└─────────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────────┐
│  WS1: Java    │   │  WS2: Docker  │   │  WS3: Python      │
│  Backend      │   │  & Infra      │   │  Voice Server     │
│  Structural   │   │               │   │                   │
└───────────────┘   └───────────────┘   └───────────────────┘
        │                     │                     │
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────────┐
│  WS4: Java    │   │  WS5: CI/CD   │   │  WS6: Frontend    │
│  Security &   │   │  & Quality    │   │  Web (Patch A)    │
│  API Hardening│   │  Gates        │   │                   │
└───────────────┘   └───────────────┘   └───────────────────┘
        │                     │                     │
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────────┐
│  WS7: Frontend│   │  WS8: Cross-  │   │  WS9: Observ-     │
│  Mobile (Opt A)│  │  Cutting      │   │  ability & Ops    │
│               │   │  Security     │   │                   │
└───────────────┘   └───────────────┘   └───────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    MERGE LAYER (Sequential)                          │
│  Integration → Smoke Tests → Final Report                            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Workstream Definitions

### WS1: Java Backend — Structural Debt
**Owner:** Java Agent  
**Scope:** Package consolidation, test scaffolding, build hygiene  
**Files:** ~320 files (deletions + imports)  
**Dependencies:** None (foundation)  
**Conflicts With:** WS4 (touches same Java files) → **WS1 must complete before WS4**

**Tasks:**
1. Delete entire `src/main/java/xiaozhi/` tree.
2. Delete entire `src/test/java/xiaozhi/` tree.
3. Verify `pom.xml` `<mainClass>` is `tbot.AdminApplication`.
4. Remove `<skipTests>true</skipTests>` from `pom.xml`.
5. Add JaCoCo plugin to `pom.xml` (coverage reporting, not enforcement yet).
6. Verify `application.yml` scans `tbot.modules.*.entity` only.
7. Run `mvn compile` to confirm clean build.

**Success Criteria:**
- `find src/main/java -path "*/xiaozhi/*" -type f | wc -l` returns 0
- `mvn compile` passes
- No `xiaozhi` references in `pom.xml`

---

### WS2: Docker & Infrastructure
**Owner:** DevOps Agent  
**Scope:** Dockerfiles, compose files, deploy scripts, rootless containers  
**Files:** ~12 files  
**Dependencies:** None (foundation)  
**Conflicts With:** WS8 (secrets patterns), WS9 (health endpoints) → Coordinate env vars

**Tasks:**
1. **Restore missing assets:** Create `docs/docker/nginx.conf` and `docs/docker/start.sh` (or inline into Dockerfiles).
2. **Rootless containers:** Add `USER` directive to all Dockerfiles (`Dockerfile-server`, `Dockerfile-server-base`, `Dockerfile-web`, `Dockerfile-web-runtime`).
3. **Remove `seccomp:unconfined`** from all compose files.
4. **Pin MySQL** to `mysql:8.4` in `docker-compose_all.yml` (replace `mysql:latest`).
5. **Resource limits:** Add `deploy.resources.limits` and `reservations` to all compose services.
6. **Restart policy:** Standardize on `unless-stopped` everywhere.
7. **Health checks:** Add `healthcheck` blocks for `tbot-esp32-server` and `tbot-esp32-server-web` (TCP/HTTP probes as placeholders; WS8/WS9 will wire real endpoints).
8. **Log rotation:** Add `logging` driver options (`max-size`, `max-file`) to compose.
9. **MySQL healthcheck:** Remove password from command line; use `.my.cnf` or `HEALTHCHECK` in custom image.
10. **Delete fallback compose generator** in `package-release.sh`; require canonical `docker-compose.prod.yml`.
11. **Timezone:** Standardize on `UTC` for all services; drive display TZ from `.env`.

**Success Criteria:**
- `docker-compose -f deploy/docker-compose.prod.yml config` validates
- All Dockerfiles build without error
- No `seccomp:unconfined` anywhere
- `mysql:latest` replaced with pinned tag

---

### WS3: Python Voice Server — Config & Core
**Owner:** Python Agent  
**Scope:** Config refactor, auth defaults, queue bounds, signal handling  
**Files:** ~30 files  
**Dependencies:** None (foundation)  
**Conflicts With:** WS8 (auth patterns), WS9 (health/metrics)

**Tasks:**
1. **Config split:** Break `config.yaml` into:
   - `config/server.yaml` (ports, auth, logging)
   - `config/voice.yaml` (Google Live, VAD, ASR, TTS defaults)
   - `config/plugins.yaml` (weather, news, HA, music)
   - `secrets.env` (all API keys, loaded via `${ENV_VAR}`)
2. **Pydantic validation:** Add `pydantic-settings` model that validates on startup and fails fast on missing required secrets.
3. **Auth default:** Change `server.auth.enabled` default to `true`.
4. **Remove example MAC** from `allowed_devices` whitelist.
5. **Queue bounds:** Add `maxsize` to all `queue.Queue()` instances in `core/connection.py` and TTS base with drop policy.
6. **Graceful shutdown:** Add `SIGTERM`/`SIGINT` handlers in `app.py` / `websocket_server.py`.
7. **Placeholder detection:** Replace naive substring check with explicit sentinel values (`__REPLACE_ME__`).
8. **Remove committed QWeather key** from `config.yaml` and `get_weather.py`.
9. **Heartbeat default:** Change `enable_websocket_ping` to `true`.
10. **Remove `test/` directory** from production builds (add to `.dockerignore`).

**Success Criteria:**
- `python -c "from config_loader import load_config; load_config()"` validates without errors
- `server.auth.enabled` defaults to `true`
- No hardcoded API keys in committed YAML
- Graceful shutdown handler registered

---

### WS4: Java Backend — Security & API Hardening
**Owner:** Java Security Agent  
**Scope:** Crypto, auth, rate limiting, CORS, headers, SQL injection, XSS  
**Files:** ~45 files  
**Dependencies:** WS1 (package consolidation must be done first)  
**Conflicts With:** WS1 (sequential dependency)

**Tasks:**
1. **Token generation:** Replace MD5 in `TokenGenerator.java` with `SecureRandom` (32-byte hex).
2. **Encryption:** Replace AES-ECB with AES-GCM in `AESUtils.java`. Add PBKDF2 key derivation.
3. **CORS:** Restrict `WebMvcConfig.java` to exact origin whitelist (driven by env var `TBOT_CORS_ORIGINS`). Remove `allowedOriginPatterns("*")`.
4. **Rate limiting:** Add Bucket4j or Redis sliding window on:
   - `/user/login`
   - `/user/register`
   - `/user/captcha`
   - `/user/smsVerification`
   - `/device/register`
5. **Account lockout:** Add Redis-backed failed-login counter with progressive lockout.
6. **Password policy:** Enforce minimum 12 chars + special characters in `isStrongPassword()`.
7. **SQL filter:** Remove blacklist-based `SqlFilter.java`; rely on MyBatis `#{}`.
8. **XSS whitelist:** Strip `embed`, `object`, `param` from `XssUtils.java`.
9. **knife4j:** Set `knife4j.enable: false` in `application.yml`. Enable only in `application-dev.yml`.
10. **Profile default:** Remove `spring.profiles.active: dev` from committed `application.yml`.
11. **Soft deletes + auditing:** Add `deleted`, `updater`, `updateDate`, `version` to `BaseEntity`. Enable `@TableLogic` and `@Version`.
12. **N+1 fix:** Rewrite `AgentServiceImpl.buildAgentDTO()` with JOIN or batch `IN` fetch.
13. **Raw thread fix:** Replace `new Thread()` in `AgentController` with `@Async` on service method.
14. **Redis TTLs:** Add expiration to activation code keys and device cache keys.
15. **Health endpoint:** Add `spring-boot-starter-actuator` with `/actuator/health` exposed.

**Success Criteria:**
- `mvn test` passes (at least compiles; tests may be minimal)
- No MD5 references in token generation
- No `allowedOriginPatterns("*")` with credentials
- `knife4j.enable: false` in base profile
- `BaseEntity` has `deleted`, `version` columns

---

### WS5: CI/CD & Quality Gates
**Owner:** DevOps Agent 2  
**Scope:** GitHub Actions, linting, scanning, test gating  
**Files:** ~5 files  
**Dependencies:** WS1 (tests must be runnable), WS2 (Docker builds must work)  
**Conflicts With:** None (additive)

**Tasks:**
1. **New `ci.yml` workflow:**
   - Run on PR + push to main
   - Job 1: `mvn test` + JaCoCo report
   - Job 2: `pytest` + `pytest-cov`
   - Job 3: ESLint (mobile + web)
   - Job 4: `ruff` + `mypy` (Python)
   - Job 5: `npm audit` + `pip-audit`
   - Job 6: Docker build all images
   - Job 7: Trivy/Grype scan on built images
2. **Dependabot:** Add `maven` and `npm` ecosystems to `.github/dependabot.yml`.
3. **Pre-commit hooks:** Add Husky + lint-staged for mobile (web hooks optional if patching).
4. **Security scanning:** Add `dependency-check-maven` to `pom.xml`.

**Success Criteria:**
- `.github/workflows/ci.yml` exists and validates with `actionlint`
- All jobs defined (may fail initially due to missing tests, but structure is correct)

---

### WS6: Frontend Web — Patch in Place (Option A)
**Owner:** Frontend Agent 1  
**Scope:** i18n, security patches, linting, build fixes  
**Files:** ~30 files  
**Dependencies:** None  
**Conflicts With:** WS7 (if mobile API changes), WS8 (security patterns)

**Tasks:**
1. **i18n fix:** Translate `zh_CN.js` and `zh_TW.js` to real Chinese (use a translation pass or at minimum copy from mobile's `zh_CN.ts` and adapt keys).
2. **localStorage security:** Wrap all `JSON.parse(localStorage.getItem(...))` in `try/catch`. Add `safeParse` utility.
3. **Token handling:** Store token as plain string (not JSON-wrapped) or wrap consistently. Fix `httpRequest.js` crash on bad token.
4. **Router guards:** Replace stringly-typed `protectedRoutes` array with `meta.requiresAuth` check on all routes.
5. **sideEffects:** Remove `"*.vue"` from `package.json` `sideEffects`.
6. **ESLint + Prettier setup:** Add `.eslintrc.js` for Vue 2 + recommended rules. Add `prettier` config.
7. **GET-with-body fix:** Fix `model.js` `getModelNames` to use query params instead of body.
8. **Self-dependency:** Remove `"tbot": "file:"` from `package.json`.
9. **NavigationDuplicated:** Replace `window.location.reload()` with graceful ignore.
10. **Global error handler:** Add `Vue.config.errorHandler`.

**Success Criteria:**
- `npm run lint` passes (or is configured)
- `zh_CN.js` contains real Chinese translations
- No bare `JSON.parse` on localStorage without try/catch
- `sideEffects` no longer contains `"*.vue"`

---

### WS7: Frontend Mobile — Lite Admin Cleanup
**Owner:** Frontend Agent 2  
**Scope:** Config cleanup, lint enable, hardcoded URL removal  
**Files:** ~20 files  
**Dependencies:** None  
**Conflicts With:** WS6 (if shared i18n strategy), WS8 (security patterns)

**Tasks:**
1. **Hardcoded URLs:** Move all `VITE_SERVER_BASEURL__*` and avatar URLs to `.env` files.
2. **ESLint enable:** Re-enable `no-console`, `unused-imports/no-unused-vars`, `vue/no-unused-refs`.
3. **Auth refresh:** Implement real refresh token flow or remove no-op handler and document.
4. **Module type:** Fix `package.json` `"type": "commonjs"` → remove or set `"module"`.
5. **Timeout:** Increase alova timeout from 5s to 15s for mobile networks.
6. **Type cleanup:** Reduce `any` usage in store/API layers where types are known.

**Success Criteria:**
- No hardcoded URLs in `src/utils/index.ts`
- ESLint rules re-enabled
- `timeout` increased to 15000ms

---

### WS8: Cross-Cutting Security
**Owner:** Security Agent  
**Scope:** Secret patterns, CORS, headers, auth, WS tokens  
**Files:** ~25 files  
**Dependencies:** WS3 (Python auth defaults), WS4 (Java auth/crypto)  
**Conflicts With:** WS2 (env var patterns must align)

**Tasks:**
1. **Secret pattern standardization:** Replace all placeholder secrets with `${ENV_VAR}` or `__REPLACE_ME__` sentinel.
2. **CORS alignment:** Ensure Java `WebMvcConfig` and Python CORS (if any) use same origin whitelist from env.
3. **Security headers:** Add `X-Frame-Options`, `X-Content-Type-Options`, `Strict-Transport-Security`, `Referrer-Policy` via Spring filter.
4. **WebSocket token expiry:** Reduce Python `expire_seconds` from 30 days to 24 hours.
5. **Token revocation:** Add Redis-based token blacklist (optional for P0; can be P1).
6. **File upload validation:** Add magic byte validation to `OTAMagController`.
7. **Device ID in query params:** Enforce header-only `Device-ID` in `websocket_server.py`.
8. **MCP security:** Enforce `wss://` for MCP endpoints in production. Validate tool args against JSON schema.

**Success Criteria:**
- No hardcoded secrets in committed configs
- Security headers present on all HTTP responses
- WS token lifetime ≤ 24h

---

### WS9: Observability & Ops
**Owner:** SRE Agent  
**Scope:** Metrics, logging, health, backups  
**Files:** ~15 files  
**Dependencies:** WS2 (compose health checks), WS4 (Java actuator), WS3 (Python health)  
**Conflicts With:** None (additive)

**Tasks:**
1. **Java metrics:** Expose `/actuator/health`, `/actuator/info`, `/actuator/metrics` via Actuator.
2. **Python metrics:** Add `/metrics` endpoint with Prometheus text format (connections active, queue depth, ASR errors).
3. **Structured logging:** Add `trace_id` / `correlation_id` to both services via MDC (Java) and `contextvars` (Python).
4. **Log shipping:** Configure Docker `logging` driver to JSON with rotation. Document stdout/stderr as 12-factor.
5. **Backup script:** Add `deploy/backup-db.sh` using `mysqldump` with retention.
6. **Smoke test:** Enhance `deploy/smoke-vps.sh` to hit health endpoints post-deploy.

**Success Criteria:**
- `/actuator/health` returns UP on Java service
- `/health` returns 200 on Python service
- `/metrics` exposes `tbot_connections_active`
- `backup-db.sh` exists and is executable

---

## Conflict Matrix

| | WS1 | WS2 | WS3 | WS4 | WS5 | WS6 | WS7 | WS8 | WS9 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **WS1** | — | ✓ | ✓ | ⚠️ SEQ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **WS2** | ✓ | — | ✓ | ✓ | ⚠️ SEQ | ✓ | ✓ | ⚠️ ENV | ⚠️ HEALTH |
| **WS3** | ✓ | ✓ | — | ✓ | ✓ | ✓ | ✓ | ⚠️ AUTH | ⚠️ HEALTH |
| **WS4** | ⚠️ SEQ | ✓ | ✓ | — | ✓ | ✓ | ✓ | ⚠️ AUTH | ⚠️ HEALTH |
| **WS5** | ✓ | ⚠️ SEQ | ✓ | ✓ | — | ✓ | ✓ | ✓ | ✓ |
| **WS6** | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ |
| **WS7** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ |
| **WS8** | ✓ | ⚠️ ENV | ⚠️ AUTH | ⚠️ AUTH | ✓ | ✓ | ✓ | — | ✓ |
| **WS9** | ✓ | ⚠️ HEALTH | ⚠️ HEALTH | ⚠️ HEALTH | ✓ | ✓ | ✓ | ✓ | — |

**Legend:**
- ✓ = Parallel-safe (different files)
- ⚠️ = Needs coordination (shared env vars, auth patterns, health endpoints)
- SEQ = Sequential dependency (WS4 must wait for WS1)

---

## Master Orchestration Prompt Template

```markdown
You are the ESP-Server Build Orchestrator. Your mission is to remediate all CRITICAL and HIGH findings from the audit report by dispatching parallel agents across 9 workstreams.

## Context
- Repository: /Users/thuanle/Documents/TamTMV/TbotREAL/ESP-Server
- Audit report: ESP-Server/AUDIT_REPORT_2026-05-27.md
- Master plan: ESP-Server/.planning/MASTER_BUILD_PLAN.md
- Strategic decisions: [User answers Q1-Q3 here]

## Execution Order
### Phase 1: Foundation (Sequential)
1. Create feature branch `feat/security-hardening-2026-05-27`
2. Run WS1 (Java Structural) alone
3. Verify `mvn compile` passes

### Phase 2: Parallel Build (Dispatch all at once)
Dispatch these agents simultaneously:
- WS2: Docker & Infra
- WS3: Python Voice Server
- WS4: Java Security & API (waits for WS1 signal)
- WS5: CI/CD & Quality Gates
- WS6: Frontend Web Patch
- WS7: Frontend Mobile Cleanup
- WS8: Cross-Cutting Security
- WS9: Observability & Ops

### Phase 3: Integration (Sequential)
1. Run `mvn test` (expect compilation pass; tests may be minimal)
2. Run `npm run lint` in both frontends
3. Run `ruff check` on Python
4. Run `docker-compose -f deploy/docker-compose.prod.yml config`
5. Run smoke test script
6. Generate final diff report

## Rules
- Each agent MUST stay in its workstream boundary.
- Agents MUST NOT touch files outside their scope without escalation.
- If an agent finds a blocker, it MUST report to the orchestrator, not fix ad-hoc.
- All changes MUST be minimal and targeted. No refactoring for refactoring's sake.
- After each agent completes, run its success criteria.
- The orchestrator collects all results and produces a final report.

## Success Criteria for Overnight Build
- [ ] All CRITICAL findings resolved
- [ ] All HIGH findings resolved or mitigated with documented exceptions
- [ ] `mvn compile` passes
- [ ] Docker builds pass
- [ ] ESLint configured on both frontends
- [ ] CI workflow exists with all quality gates defined
- [ ] Health endpoints exist on both backend services
- [ ] No hardcoded secrets in committed configs
- [ ] `seccomp:unconfined` removed everywhere
- [ ] Auth enabled by default on WebSocket
- [ ] CORS restricted to exact origins
```

---

## Effort Estimates (Overnight = ~8-12 hours)

| Workstream | Files | Estimated Agent Time | Overnight Feasible? |
|------------|-------|---------------------|---------------------|
| WS1: Java Structural | ~320 | 2-3 hours | ✅ Yes |
| WS2: Docker & Infra | ~12 | 2 hours | ✅ Yes |
| WS3: Python Core | ~30 | 3-4 hours | ✅ Yes |
| WS4: Java Security | ~45 | 4-5 hours | ✅ Yes |
| WS5: CI/CD | ~5 | 1-2 hours | ✅ Yes |
| WS6: Web Patch | ~30 | 3-4 hours | ✅ Yes |
| WS7: Mobile Cleanup | ~20 | 2 hours | ✅ Yes |
| WS8: Cross Security | ~25 | 2-3 hours | ✅ Yes |
| WS9: Observability | ~15 | 2 hours | ✅ Yes |
| **Total** | **~502** | **~20-25 agent-hours** | **✅ Parallelizable to ~6-8 wall-clock hours** |

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| `xiaozhi` deletion breaks hidden dependency | Medium | High | Full `mvn compile` + `grep -r "xiaozhi"` before commit |
| Vue 2 ESLint upgrade reveals hundreds of errors | High | Medium | Start with warning-level rules; enforce on new files only |
| Config split breaks existing deployments | Medium | High | Maintain backward-compatible `config.yaml` loader for 1 release |
| AES-GCM migration breaks existing encrypted data | Low | High | Document as breaking change; provide migration script |
| Mobile hardcoded URLs are actually required by build | Medium | Low | Move to `.env` with same default values |
| Parallel agents touch same file (e.g., `docker-compose.prod.yml`) | Medium | Medium | Orchestrator merges changes; agents edit different sections |

---

## What the User Must Provide Before Dispatch

1. **Answer Q1-Q3** above (Web strategy, Mobile scope, Java namespace safety).
2. **Confirm `tbot.*` is canonical** for Java package consolidation.
3. **Provide placeholder patterns** for secrets (e.g., `YOUR_API_KEY`, `__REPLACE_ME__`).
4. **Provide CORS origin whitelist** for production (e.g., `https://admin.tbot.com`).
5. **Confirm test environment** availability (MySQL, Redis, Docker) for validation.
6. **Approve the overnight scope** — all CRITICAL + HIGH, or subset?
