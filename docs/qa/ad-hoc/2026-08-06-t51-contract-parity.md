# T5.1 Backend ↔ ESP contract parity evidence (robot half)

**Repo:** `robot/esp32-server`
**Date:** 2026-08-06
**Branch:** `lesson-prod/t51-contract-parity`
**Backend half:** `tbot-backend/docs/qa/ad-hoc/2026-08-06-t51-contract-parity.md`
(cross-language divergence table, golden-vector design, CI enforcement, and the
honest limitation of the two-repo sha anchor live there and are not repeated).

## Reproduction

The cross-language divergences are in the backend evidence. What this repo added
on its own is **four mutually inconsistent copies of the same contract**, so two
ESP ingress paths disagreed with each other independently of the backend:

```text
core/lesson/cache_key_contract.py       quote(key, safe="")            key cap 205
core/lesson/sd_pack_materializer.py     quote(key, safe="")            basename cap 200, no reserved names
core/lesson/sd_pack_mcp_payload.py      quote(key, safe="")            basename cap 255, reserved names, no FAT device names
core/lesson/global_generation_poller.py quote(key, safe="-_.!~*'()")   key cap 200, basename cap 200
core/lesson/shared_asset_store.py       quote(key, safe="")            <- writes the actual file
core/lesson/sd_pack_evict.py            byte-for-byte COPY of validate_cache_key + its own CacheEvictionRefused class
```

Consequences, each proved by the committed repro `lesson-prod/repros/t51.sh`
(**11 of 12 FAIL** at the pre-patch base `de6471b7`, **12 PASS** at the tip):

1. **The CMS poller expected a filename the pack store never writes.** The poller
   used `safe="-_.!~*'()"` — JavaScript `encodeURIComponent` semantics — while
   `SharedAssetStore._pack_asset_name` writes `quote(key, safe="")`. For any key
   containing `! ' ( ) *` the poller accepted a pack whose `sdPath` the
   materializer then rejected as `INVALID_SD_PATH`. A test,
   `test_sd_path_uses_javascript_encode_uri_component_and_200_byte_boundary`,
   *asserted* the divergent encoding by name.
2. **The poller capped the cache key at 200 bytes; the canonical cap is 205.** A
   maximal-but-legal key (128-byte slug + 10-digit version) was rejected as
   `cms_cache_key_too_long` while the materializer accepted it.
3. **Two `CacheEvictionRefused` classes.** `sd_pack_mcp_payload` imports the one
   from `sd_pack_evict`; `sd_pack_materializer` raises the one from
   `cache_key_contract`. `except CacheEvictionRefused` in the first could never
   catch a raise from the second.
4. **`pack.json` and `READY` were accepted as asset keys.**
   `SharedAssetStore.commit_pack` hard-links the assets and *then* writes
   `pack.json` and `READY` into the same staging directory, so such an asset is
   silently overwritten by the store's own control file. `_is_pack_ready` then
   hashes the control file against the asset digest, mismatches, and the pack can
   **never** reach READY — a permanent, undiagnosable preload failure.
5. **The materialize endpoint keyed its error code as `error`, not `code`**,
   diverging from the canonical `{code,message,retryable}` envelope that
   `LessonError.to_body` and the backend `AppError` both use.

## Fix diff summary

- **`core/lesson/cache_key_contract.py` is now THE Python contract.** Adds
  `compose_cache_key` (validating, never sanitizing), `encode_asset_basename`,
  `compose_asset_sd_path`, `AssetBasenameRefused`, `MAX_ENCODED_BASENAME_BYTES`,
  the reserved-basename set (now including `pack.json` and `ready`), reserved
  suffixes, FAT device names and FAT-forbidden characters — mirroring
  `lesson-cache-key.contract.ts` field for field.
- **`sd_pack_materializer.py`**: uses the shared encoder and composer; the
  duplicated key/basename logic and the local `MAX_ENCODED_BASENAME_BYTES` are
  gone; `_safe_version` enforces the 10-digit cap so an over-long version is
  reported as `INVALID_LESSON_VERSION` rather than escaping as a 500; the staging
  filename and the basename-collision key both use the canonical encoder;
  `to_response()` emits `code` **and** keeps `error` as an additive alias.
- **`global_generation_poller.py`**: delegates to the shared encoder (dropping
  the `encodeURIComponent` emulation) and takes both caps from the contract, so
  `MAX_CACHE_KEY_BYTES` moves 200 → 205.
- **`sd_pack_mcp_payload.py`**: `_encoded_basename` delegates to the shared
  encoder. Strictly tightening, and every key reaching it has already passed the
  materializer, so no real pack can be affected.
- **`sd_pack_sync.py`**: SD-path comparison and staging filename use the shared
  encoder.
- **`sd_pack_evict.py`**: its copy of the validator, the constants and the
  exception class deleted; all six names re-exported from `cache_key_contract`
  so existing importers keep working against ONE class object.
- **`core/api/lesson_sd_materialize_handler.py`**: the two inline error
  envelopes also carry `code`.
- **New** `core/lesson/contract_vectors.py` (loader + frozen sha256 anchor),
  `contracts/lesson-cache-key.vectors.json` (vendored byte-for-byte from
  tbot-backend), `tests/test_lesson_contract_vectors_parity.py` (128 tests).
- **`.github/workflows/ci.yml`**: an explicit blocking parity step before the
  full suite, so a contract divergence is not buried in 3 700 lines of output.

### Tests corrected (they encoded the divergence)

- `test_global_generation_poller.py::test_sd_path_uses_javascript_encode_uri_component_and_200_byte_boundary`
  → `…_uses_rfc3986_strict_encoding_and_the_200_byte_boundary`, expecting
  `bang%21~%27%28%29` instead of `bang!~'()`.
- `…::test_cache_key_over_200_bytes_fails_with_stable_sanitized_code` →
  `…_over_the_canonical_cap_…`: the 205-byte key is now asserted **accepted**,
  and the 11-digit version that pushes it to 206 is the rejection boundary.
- `test_lesson_sd_pack_materializer.py`: the envelope assertion now expects
  `code` alongside `error`.

## Sealed container — derive-path inventory

Every Python site that touches a cache key or an asset basename, and its
disposition. "Derive" = invents a key from parts; "validate" = parses or
re-derives solely to compare with the backend-supplied value.

| Site | Kind | Disposition |
| --- | --- | --- |
| `cache_key_contract.validate_cache_key` / `compose_cache_key` | validate | **THE contract** — the only formula |
| `cache_key_contract.encode_asset_basename` / `compose_asset_sd_path` | validate | **THE contract** |
| `sd_pack_materializer._validate_manifest` (expected key) | validate | now calls `compose_cache_key` |
| `sd_pack_materializer._validate_asset` (expected sdPath, basename, collision key, staging name) | validate | now calls the shared encoder |
| `sd_pack_materializer._safe_lesson_id` | validate | now probes via `compose_cache_key` |
| `global_generation_poller._validate_pack` / `_validate_asset` | validate | now calls the shared composer/encoder |
| `sd_pack_sync` (sdPath compare, staging name) | validate | now calls the shared encoder |
| `sd_pack_mcp_payload._encoded_basename` | validate | delegates to the shared encoder |
| `sd_pack_evict` validator + constants + exception | duplicate | **deleted**, re-exported from the contract |
| `shared_asset_store._pack_asset_name` | writer | `quote(key, safe="")` — the on-disk truth the contract is defined to match. Left as-is deliberately: it is the reference, and it is `sd_pack_*` (T2.2) territory |
| **`asset_cache._compose_cache_key`** (`asset_cache.py:306`) | **DERIVE + sanitize** | **NOT fixed — routed to §5.** Builds `<lesson>/v<n>-<sum>` from parts and *sanitizes* illegal characters to `_` instead of refusing, and keeps a legacy bare-`<lesson_key>` directory when version/checksum are absent. Deployed robots' on-disk cache directories depend on that naming, so replacing it with "validate the backend-supplied `cacheKey`" is a migration, not a parity fix |
| `asset_cache.__init__` (`:249`) | caller of the above | routed to §5 |
| `runtime.py:6519` (preload candidate identity) | caller of the above | routed to §5 — the assignment payload already carries `cacheKey`; this re-derives it |
| `runtime.py:6884` (activation rollback identity) | caller of the above | routed to §5 — same |
| `asset_cache.py:441` download URL uses `quote(key, safe='@')` | DERIVE (URL, not SD path) | routed to §5 — a *third* encoding, leaving `@` literal, so `shared@v1` becomes `shared@v1` not `shared%40v1`. Affects the ESP's own HTTP download URL, not the pack filename |
| `scripts/project_tvideo_farm_firmware_fixture.py:30` | DERIVE | fixture-generation script, not runtime. Left as-is |

So the sealed-container phase **landed for every site this task names** and is
explicitly scoped as a follow-up for the `asset_cache`/`runtime` pair, whose
files belong to T2.1/T2.2 and whose change carries an on-disk migration.

## Passing re-run

```text
$ python3 -m pytest -q tests/test_lesson_contract_vectors_parity.py
127 passed, 1 skipped in 0.19s
  (the 1 skip is the cross-repo byte comparison; it runs green when the backend
   checkout is present — see the backend evidence)

$ python3 -m pytest -q          # full suite, branch tip
13 failed, 3712 passed, 10 skipped in 112.80s

$ python3 -m pytest -q          # full suite, main (de6471b7) baseline
13 failed, 3587 passed, 7 skipped in 113.42s
```

**Same 13 failures on both**, all pre-existing and all already filed
(`test_scaleout_deploy_topology` ×3 and `test_benchmark_google_live_audio_runtime`
are the post-baseline regressions on main recorded in §5 under T7.3;
`test_tvideo_farm_cross_repo_fixture` ×4, `test_http_server` ×2,
`test_nginx_generation_cache_runtime`, `test_flattened_cinematic_contract` and
`test_google_live_client` are likewise unchanged from the base run). **+125
passing tests, zero new failures.**

```text
$ bash lesson-prod/repros/t51.sh
12 passed in 0.07s
REPRO PASS: T5.1 ESP cache-key/basename contract is single-sourced and consistent.
```

At the pre-patch base `de6471b7`: **11 failed, 1 passed** (the 201-byte case
already failed correctly there, since the materializer alone had the 200 cap).

## Deploy safety

No behavior changes for any pack that works today; see the backend evidence.
Everything newly rejected here was already unusable end to end, and the poller's
newly *accepted* 201–205-byte cache key was already accepted by the materializer.
VPS deploy stays deferred to T7.3 under the standing operator decision (as
T2.2/T2.3/T2.4/T4.1/T4.2).
