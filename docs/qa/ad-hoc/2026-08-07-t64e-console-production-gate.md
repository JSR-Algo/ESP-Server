# F-T64-05 (remainder) — the operator console was still anonymously callable

**Repo:** `robot/esp32-server`
**Date:** 2026-08-07
**Branch:** `lesson-prod/t64e-console`
**Closes:** T6.4 deep-dive **box 5** — "ESP admin/API handlers (lesson_sd_*, nudge,
console) require auth — none anonymously callable"
**Repro:** `lesson-prod/repros/t64e.sh`

## What was left open

T6.4 closed the `lesson_sd_*` and nudge halves of box 5, and stopped the console
publishing the connected-robot inventory. The console **page** was still served
to anyone who asked. The follow-up (`t64c`) then loopback-bound the published
ports, which removes the direct-to-port route — but not this one, because
cloudflared routes the `esp.tjbot.vn` **catch-all** straight to
`http://127.0.0.1:8003`:

```yaml
  - hostname: esp.tjbot.vn
    service: http://127.0.0.1:8003        # deploy/cloudflared/config.yml.example
```

So `https://esp.tjbot.vn/tbot/assign/` reaches the ESP server directly from the
internet. That is why the two obvious gates do not work here:

- **Not a header.** An operator opens the page in a browser to paste a parent
  JWT; a browser cannot send `X-Mint-Secret`. (This is exactly why T6.4 gated the
  *inventory* and not the page.)
- **Not Nginx.** `location /tbot/` has no `auth_request`, but adding one would
  not help: the tunnel never traverses Nginx for this hostname.

I previously recorded this as needing a session or Cloudflare Access design.
That is true only if the page must stay publicly reachable *and* authenticated.
It does not — so the simpler answer closes it.

## Fix

The console is **not served in production** unless someone deliberately turns it
on:

```text
_console_served() = True                       when not a production runtime
                  = LESSON_ASSIGN_CONSOLE_ENABLED == "true"   in production
```

- Returns **404**, not 403 — a 403 would confirm the route exists on every
  production robot server to anyone probing for it.
- Production is decided by the existing `_production_environment()` helper,
  **imported** from `lesson_sd_fanout_handler` rather than reimplemented. A
  second predicate that drifts from the first is precisely the F-T64-03 failure
  mode (`auth.guard` and `auth.service` disagreeing about "production" is what
  let a git-committed key sign tokens). It reads `ENV`, `APP_ENV`, `PYTHON_ENV`
  and `NODE_ENV`; a gate checking only `NODE_ENV` would have left three open,
  which the repro asserts against directly.
- The flag must be exactly `"true"`. `1`, `yes`, `on` do **not** enable it — a
  half-remembered truthy value should fail closed, not open.
- Outside production nothing changes, so local operator and e2e workflows keep
  the console.
- Wired into `deploy/docker-compose.prod.yml` (`:-false`) and
  `deploy/.env.example` so it is discoverable rather than folklore.

Checked before changing: nothing automated depends on the route — no script,
spec, compose file or e2e references `/tbot/assign` — and the running T5.3 sim
sets no production env var, so it is unaffected.

## Results

```text
$ python3 -m pytest tests/test_lesson_assignment_console.py -q
17 passed          # 5 added

$ python3 -m pytest -q                    # full ESP suite
7 failed, 3762 passed, 9 skipped
# the same 7 routed as F-T64-09 (Google Live, the running T5.3 stack, tvideo
# fixtures) — all owned by other active lanes. +7 passing vs the previous run.

$ bash lesson-prod/repros/t64e.sh
6 passed
```

## Box 5 now reads, end to end

| Route | Anonymous |
| --- | --- |
| `/internal/devices/*/lesson-nudge`, `lesson-child-response`, `evict-cache-key`, `mcp-call` | 401 |
| `/internal/lesson-assets/{generation/retry,sd-fanout,materialize,sd-fanout/pending}` | 401 |
| `/internal/lesson-runtime/{preload-voice-alarm,metrics}` + `/reset` | 401 (fixed in T6.4) |
| `/tbot/assign/` | **404 in production** (this change); inventory gated by mint secret; script block escaped |
