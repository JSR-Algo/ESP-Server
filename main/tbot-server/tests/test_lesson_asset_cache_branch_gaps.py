"""Close the remaining *branch* gaps in ``core.lesson.asset_cache``.

``--cov-branch`` flags two partial branches whose false-edge is never taken:

  * 317->322 -- ``_source_aliases``: an asset with NO ``path`` but a ``url`` ->
                the ``if asset.path:`` block is skipped and only the url alias is
                produced. Every existing alias asset carries a path.
  * 462->464 -- ``evict``: the cache_dir does NOT exist on disk (the lesson was
                never preloaded) -> the ``if os.path.isdir(self.cache_dir):`` rmtree
                is skipped and eviction proceeds to the pack-dir cleanup. Existing
                evict tests always preload first, so the dir always exists.

Mirrors ``test_lesson_asset_cache.py``: real ``AssetCache`` / ``AssetState`` against
a tmp cache root; asserts on real alias lists and real on-disk state.
"""

import os
import shutil
import tempfile
import unittest

from core.lesson.asset_cache import AssetCache, AssetState, EVICTED

BASE = "http://assets.test"


class AssetCacheBranchGapTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lesson-cache-branch-")

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

    # ── 317->322: url-only asset has no path, so the path-alias block is skipped ──
    def test_source_aliases_for_url_only_asset_skips_path_block(self):
        """An asset with only a url (no path) -> _source_aliases skips the
        ``if asset.path:`` block (branch 317->322) and returns just the url alias.
        Mutation guard: if the source dropped the path guard, basename(None) /
        f-string on a None path would raise instead of returning the clean url list."""
        cache = self._cache(
            [{"key": "url-only", "url": "https://cdn.test/a.png", "critical": False}]
        )
        url_only = AssetState({"key": "url-only", "url": "https://cdn.test/a.png"})

        aliases = cache._source_aliases(url_only)

        # No path -> no path / basename / origin-joined aliases; just the url.
        assert aliases == ["https://cdn.test/a.png"]

    # ── 462->464: evict when cache_dir was never created on disk ──────────────────
    async def test_evict_without_preloaded_dir_skips_rmtree(self):
        """evict() on a cache whose version dir was never materialized -> the
        ``if os.path.isdir(self.cache_dir):`` rmtree is skipped (branch 462->464),
        yet assets are still flipped to EVICTED and the call is a clean no-op on
        the missing dir (proving evict is safe to call during teardown before any
        preload)."""
        cache = self._cache(
            [
                {
                    "key": "teachingObject.barn",
                    "path": "barn.png",
                    "sha256": "ab" * 32,
                    "critical": True,
                }
            ],
            lesson_version=7,
            manifest_checksum="abcdef0123456789",
        )
        # Never preloaded -> the version-scoped cache dir does not exist.
        assert not os.path.isdir(cache.cache_dir)

        await cache.evict()

        # rmtree skipped, but the asset bookkeeping still ran.
        assert all(a.state == EVICTED for a in cache.assets)
        assert not os.path.isdir(cache.cache_dir)


if __name__ == "__main__":
    unittest.main()
