# TJBot Production Domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configure the existing VPS deployment source for `admin.tjbot.vn`, OTA, and WSS production endpoints.

**Architecture:** Keep Docker service ports unchanged and add a host-level Nginx reverse proxy. Make production URLs explicit in env examples, SQL params, docs, and smoke commands.

**Tech Stack:** Docker Compose, Nginx, Certbot, Bash, MySQL SQL params.

---

### Task 1: Production Endpoint Artifacts

**Files:**
- Create: `deploy/nginx/tjbot.vn.conf`
- Create: `deploy/tjbot-prod-sys-params.sql`
- Modify: `deploy/.env.example`
- Modify: `deploy/CURRENT_ENDPOINTS.md`
- Modify: `deploy/package-release.sh`

- [ ] **Step 1: Add the Nginx vhost**

Create `deploy/nginx/tjbot.vn.conf` with host rules for `admin.tjbot.vn` and `esp.tjbot.vn`. Proxy admin to `127.0.0.1:8002`, OTA/internal/MCP HTTP paths to `127.0.0.1:8003`, and WSS path `/tbot/v1/` to `127.0.0.1:8000` with upgrade headers.

- [ ] **Step 2: Add production SQL params**

Create `deploy/tjbot-prod-sys-params.sql` that sets:

```sql
server.fronted_url = https://admin.tjbot.vn
server.websocket = wss://esp.tjbot.vn/tbot/v1/
server.ota = https://esp.tjbot.vn/tbot/ota/
```

- [ ] **Step 3: Update env examples**

Set `TBOT_PUBLIC_WEBSOCKET_URL=wss://esp.tjbot.vn/tbot/v1/` in `deploy/.env.example` and update comments that still mention legacy placeholder domains.

- [ ] **Step 4: Document current production endpoints**

Update `deploy/CURRENT_ENDPOINTS.md` with a production section for `tjbot.vn` while preserving the quick-tunnel lab section.

- [ ] **Step 5: Include support artifacts in release bundles**

Update `deploy/package-release.sh` so packaged VPS releases copy `deploy/nginx/*.conf` into `dist/deploy/<tag>/nginx/` and copy `deploy/tjbot-prod-sys-params.sql` into `dist/deploy/<tag>/`.

### Task 2: Operator Docs and Verification

**Files:**
- Modify: `deploy/README.md`
- Modify: `deploy/smoke-vps.sh`

- [ ] **Step 1: Add VPS Nginx instructions**

Document copying `deploy/nginx/tjbot.vn.conf` to `/etc/nginx/conf.d/tjbot.vn.conf`, running `sudo nginx -t`, reloading Nginx, and issuing certificates with `sudo certbot --nginx -d admin.tjbot.vn -d esp.tjbot.vn`.

- [ ] **Step 2: Add smoke command for production domains**

Document `deploy/smoke-vps.sh --admin-url https://admin.tjbot.vn/ --ota-url https://esp.tjbot.vn/tbot/ota/ --expected-ws-host esp.tjbot.vn`.

- [ ] **Step 3: Verify changed scripts**

Run:

```bash
bash -n deploy/smoke-vps.sh
```

Expected: exit code `0`.

- [ ] **Step 4: Verify docs contain production domains**

Run:

```bash
rg -n "admin\\.tjbot\\.vn|esp\\.tjbot\\.vn|trycloudflare|example\\.com" deploy docs/superpowers/specs/2026-06-30-tjbot-production-domain-design.md docs/superpowers/plans/2026-06-30-tjbot-production-domain.md
```

Expected: production docs mention `tjbot.vn`; quick-tunnel files may still mention `trycloudflare`; no production instructions should require placeholder domains.
