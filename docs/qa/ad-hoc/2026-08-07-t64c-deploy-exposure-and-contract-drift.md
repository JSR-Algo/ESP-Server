# F-T64-05 + F-T64-08 — deploy exposure, and deploy-contract tests that had drifted

**Repo:** `robot/esp32-server`
**Date:** 2026-08-07
**Branch:** `lesson-prod/t64c-nginx`
**Follows:** T6.4 (`docs/qa/ad-hoc/2026-08-07-t64-security.md`)
**Repro:** `lesson-prod/repros/t64c.sh`

## F-T64-05 — every published port was on 0.0.0.0

`deploy/docker-compose.prod.yml` published `8000`, `8003` and `8002` with no
`127.0.0.1:` prefix, so Docker bound them on all interfaces. On a VPS without a
host firewall that puts the whole ESP HTTP surface — **including `/internal/*`,
which T6.4 had just finished authenticating** — directly on the internet, around
both cloudflared and Nginx.

**Nothing legitimate needed the wildcard bind.** Established before changing it,
because getting this wrong takes the lesson pipeline down:

- `deploy/cloudflared/config.yml.example` routes every public hostname to
  `http://127.0.0.1:8003`, `:8000` and `:8002` — cloudflared runs as a **host**
  systemd service (`/etc/cloudflared/config.yml`, per `deploy/README.md`), so it
  reaches the containers over host loopback.
- `deploy/nginx/tjbot.vn.conf` proxies to `127.0.0.1:8002` / `:8003` likewise.
- The backend on Render reaches the ESP through the **tunnel**
  (`TBOT_ESP_SERVER_URL`, `sync: false`, documented as "a named Cloudflare
  Tunnel / managed host"), not by dialling the port directly.

Fixed by prefixing all three publishes with `127.0.0.1:`, plus
`test_prod_compose_publishes_every_port_on_loopback_only`, which walks every
service's `ports:` rather than grepping for known numbers, so a newly-added
service cannot reintroduce the hole. Verified the guard discriminates: 0
offenders as shipped, all 3 flagged when the prefix is stripped.

Worth stating plainly: this is also **why the T6.4 handler fix mattered**. The
tunnel ingress maps `^/internal(?:/.*)?$` straight to `:8003` with no auth layer
of its own, so those routes are internet-reachable by design and the mint-secret
gate is the only thing in front of them.

### Still not fixed here

`/tbot/assign/` remains reachable without auth (tunnel catch-all → `:8003`,
and Nginx `location /tbot/` has no `auth_request`). T6.4 gated the *inventory* it
used to publish, so the page now leaks nothing, but gating the **page** needs a
session or Cloudflare Access design that is deploy-side. Left with F-T64-05's
recommendation for T7.3.

## F-T64-08 — five deploy-contract tests were red on main

All five asserted config text that had since moved, so they were failing on main
and had stopped guarding anything.

**Two in `test_http_server.py`** pinned the pre-`d6536973` public-index topology:
`proxy_pass …:3003` twice, plus `proxy_cache lesson_generation`. `d6536973`
("harden public CMS proxy") re-pointed the route to the local CMS on `:8002` and
removed the cache deliberately — `proxy_cache_path` needs writable host storage,
and the conf now says keeping the host layer storage-free is what stops a full
root filesystem turning these reads into 502s. Updated to the shipped topology,
with the cache pinned as an explicit **absence** so re-adding one is a conscious
act (the old `proxy_cache_key "lesson-assets-latest"` was a fixed key across two
server blocks — exactly the shape that serves one caller's body to another).

**Three in `test_scaleout_deploy_topology.py`** matched an exact contiguous
haproxy block:

```text
expected: backend tbot_ws_backend\n    balance hdr(...)\n    hash-type consistent
actual:   backend tbot_ws_backend\n    timeout check 10s\n    balance hdr(...)\n    hash-type consistent
```

`timeout check 10s` had been inserted into the block, and `inter 5s fall 5 rise 1`
into the `server-template` line. Every property the tests care about still held;
only adjacency changed. Replaced the string matching with `_haproxy_backend()`,
which parses the block and asserts its **contents**, so tuning knobs no longer
produce false failures.

## A sixth failure, found while fixing the fifth

`test_nginx_generation_cache_runtime.py` stands real nginx up in Docker against
the conf. It was red for the same root cause (it rewrote `:3003` to a fake
upstream, but the route now points at `:8002`, so every request 502'd), and
fixing the routing exposed that the rest of the test asserted **cache** behaviour
that no longer exists:

- `upstream.request_count == 1` after 96 Host/Origin-rotated requests — that was
  the cache collapsing them. Uncached, each egresses once.
- a HEAD that used to be served from cache now reached the fake upstream, which
  only implemented `do_GET` and answered `501`.
- a `find /var/cache/nginx/lesson-generation` expecting exactly one cache file.

Rewritten to assert the contract that actually holds now, keeping the
security-relevant half intact: nginx strips `Origin` before egress, never
`Vary`s, always answers `*`, never amplifies one client request into several
upstream reads, and — the assertion that replaced the cache-collapse one —
**every throttled request is rejected at the edge and reaches the upstream zero
times**. Renamed to `test_public_generation_reads_are_uncached_bounded_and_origin_isolated`.

It also had a **latent race**: the readiness poll only slept on `OSError`, so a
502 (nginx bound, upstream still starting) burned all 50 attempts in
milliseconds. Observed as an intermittent 502 on an otherwise passing test. Now
sleeps on any non-200; 5/5 consecutive runs green.

## Results

```text
$ python3 -m pytest -q            # full ESP suite, at tip
7 failed, 3755 passed, 9 skipped

$ python3 -m pytest -q            # unmodified main (baseline, measured earlier)
13 failed, 3741 passed, 7 skipped
```

Six failures fixed. `test_http_server.py` (55/55) and
`test_scaleout_deploy_topology.py` (38/38) are fully green for the first time.

### The 7 that remain — deliberately not touched

| Failing suite | Why not here |
| --- | --- |
| `test_benchmark_google_live_audio_runtime` (1), `test_google_live_client` (1) | Google Live path. Ground rule 1 keeps it separate from the lesson flow, **and** another lane has `core/voice/session_provider/google_live.py` checked out dirty right now — editing it would collide |
| `test_lesson_studio_e2e_compose` (1) | Asserts `docs/docker/lesson-studio-e2e/docker-compose.yml`, the T5.3 stack — which is **currently running** (`tbot-ls-e2e-*`) and owned by an active T5.3 lane |
| `test_tvideo_farm_cross_repo_fixture` (4) | Cross-repo tvideo fixture provenance (checksum drift + a missing `compatibilityMetadata` key). Renderer territory, unrelated to the lesson security surface |

Routed rather than fixed, per ground rule 5.
