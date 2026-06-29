import base64
import os

from aiohttp import web

from core.api.base_handler import BaseHandler

TAG = __name__


class LessonAssetHandler(BaseHandler):
    """Serve ESP-verified lesson assets from the local preload cache."""

    def __init__(self, config: dict):
        super().__init__(config)
        lesson_cfg = config.get("lesson", {}) or {}
        cache_root = lesson_cfg.get("asset_cache_root") or "data/lesson_assets"
        self.cache_root = os.path.realpath(cache_root)

    async def handle_get(self, request):
        token = request.match_info.get("cacheToken", "")
        asset_key = request.match_info.get("assetKey", "")
        cache_key = self._decode_cache_token(token)
        if not cache_key or not asset_key:
            return web.Response(text="bad lesson asset path", status=400)

        safe_key = asset_key.replace("/", "_").replace("..", "_")
        file_path = os.path.realpath(os.path.join(self.cache_root, cache_key, safe_key))
        if not file_path.startswith(self.cache_root + os.sep):
            return web.Response(text="bad lesson asset path", status=400)
        render_safe_path = f"{file_path}.render.jpg"
        if os.path.isfile(render_safe_path):
            file_path = render_safe_path
        if not os.path.isfile(file_path):
            return web.Response(text="lesson asset not found", status=404)

        resp = web.FileResponse(path=file_path)
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp

    @staticmethod
    def _decode_cache_token(token: str) -> str:
        try:
            padded = token + "=" * (-len(token) % 4)
            return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        except Exception:
            return ""
