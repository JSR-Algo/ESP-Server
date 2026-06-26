"""Close the OSError sentinel branch in ``AssetCache._materialized_total_bytes``.

``--cov-branch`` flags 358-359: during the per-asset on-disk size summation, if
``os.path.getsize`` raises ``OSError``/``ValueError`` (file vanished/raced/unreadable
between the ``os.path.exists`` check and the size read), the method abandons the
running total and returns the ``_max_total_asset_bytes + 1`` sentinel. That sentinel
is deliberately > the budget so the caller (is_ready / readiness math at line ~248)
treats the cache as OVER budget and NOT ready, rather than under-counting bytes and
falsely declaring readiness on an unreadable cache.

Existing asset-cache tests only summation against fully-readable files, so the except
edge is never taken. We materialize one real asset on disk, then mock ``os.path.getsize``
to raise OSError, and assert the sentinel is returned -- a real raced-file path, not an
environment-only branch.
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from core.lesson.asset_cache import AssetCache, AssetState


BASE = "http://assets.test"


class MaterializedTotalBytesOSErrorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lesson-cache-bytes-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cache(self, assets, **kw):
        return AssetCache(
            assets=assets,
            profile="espTft",
            asset_origin_base=BASE,
            cache_root=self.tmp,
            lesson_key="w01-d01-barn-say-it",
            **kw,
        )

    def test_total_bytes_returns_over_budget_sentinel_when_getsize_raises(self):
        asset_spec = {
            "key": "teachingObject.barn",
            "path": "barn.png",
            "sha256": "ab" * 32,
            "critical": True,
        }
        # Tiny budget so the sentinel (max+1) is unambiguously distinguishable and
        # clearly over budget.
        cache = self._cache([asset_spec], max_total_asset_bytes=1024)
        asset = AssetState(asset_spec)

        # Materialize the asset's final path on disk so the ``os.path.exists`` guard
        # passes and execution reaches the ``os.path.getsize`` call.
        final_path = cache._final_path(asset)
        os.makedirs(os.path.dirname(final_path), exist_ok=True)
        with open(final_path, "wb") as fh:
            fh.write(b"\x00" * 16)
        self.assertTrue(os.path.exists(final_path))

        # Sanity: a clean read sums the real on-disk size (the happy edge).
        self.assertEqual(cache._materialized_total_bytes([asset]), 16)

        # Now race the file out from under the size read: exists() still True (already
        # passed), but getsize raises OSError -> the except returns the over-budget
        # sentinel instead of a partial/under-count.
        with mock.patch(
            "core.lesson.asset_cache.os.path.getsize",
            side_effect=OSError("stat: no such file"),
        ):
            total = cache._materialized_total_bytes([asset])

        self.assertEqual(total, cache._max_total_asset_bytes + 1)
        self.assertGreater(total, cache._max_total_asset_bytes)


if __name__ == "__main__":
    unittest.main()
