#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
#
# T2.2 — ESP SD pack lifecycle (materialize / GC / evict / retry).
# Six defects, one repro. The promoted lifecycle suite is tracked in this
# repository, so shallow CI checkouts do not need historical Git objects.
#
#   1. GC picked its LRU victim from a snapshot and never re-checked protection
#      at deletion time, so a pack that won the activation race in between was
#      deleted out from under the lesson that had just claimed it.
#   2. GC protection came only from the CALLING connection's runtime keys, while
#      one activation record serves every robot on the server — robot B's GC
#      could delete robot A's current pack.
#   3. SharedAssetStore.delete_pack accepted protected_cache_keys but applied
#      them only to the CAS sweep: the protected pack itself was still deleted.
#   4. A truncated pending record (crash mid-write) was a poison pill: load()
#      returned None while claim_due only drops members whose value key is
#      ABSENT, so the worker re-leased it every cycle for the whole 30-day TTL.
#   5. ENOSPC during a CAS write stranded the partial .part file — exactly the
#      bytes the full card could not spare — until some later process re-ran
#      cleanup_parts; and a local disk-full mid-materialize was reported as
#      DOWNLOAD_FAILED, sending the backend retrying against a healthy origin.
#   6. A crashed materialize left its .materialize-* staging directory (holding
#      partially downloaded assets) behind forever; nothing swept staging dirs.
#   7. Evicting a cache key left its queued fanout work in the pending store, so
#      the retry worker re-pushed the pack the operator had just evicted.
set -euo pipefail

cd main/tbot-server

exec python3 -m pytest -q --no-header -p no:cacheprovider \
  tests/test_lesson_sd_pack_lifecycle.py
