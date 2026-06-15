"""Device-token mint client — the D-RUNTOKEN bridge between the two device worlds.

The esp-server knows a robot only by its Wi-Fi MAC; the NestJS lesson backend
identifies devices by UUID and requires a device-scoped JWT on the assignment
pull. This module calls the trusted server-to-server endpoint
``POST {base}/internal/devices/mint-token`` (authenticated by the shared secret
``TBOT_DEVICE_MINT_SECRET``) to resolve a MAC -> (deviceUuid, token), caching the
result per MAC until shortly before the token expires so the realtime path never
blocks on a re-mint.

Design notes:
- The whole server runs on a single asyncio event loop (one process), so the
  module-level cache needs no lock.
- ``resolve_device_identity`` NEVER raises: any failure returns ``(None, None)``
  and the caller falls back to the legacy (MAC, no-token) pull — which 401s
  harmlessly. The voice path is completely unaffected by anything here.
- The shared secret is read from the environment ONLY; it is never logged.
"""

import os
import time

# {mac: (device_uuid, token, cached_at_epoch)} — cache for ~14 min (token TTL 15m).
_cache = {}
_CACHE_TTL_S = 14 * 60


def _secret():
    return os.environ.get("TBOT_DEVICE_MINT_SECRET", "")


def _log(logger, level, message):
    if logger is None:
        return
    try:
        getattr(logger.bind(tag="DeviceToken"), level)(message)
    except Exception:
        pass


async def resolve_device_identity(client, base_url, mac, *, logger=None):
    """Resolve a robot MAC -> (device_uuid, device_jwt), or (None, None).

    ``client`` is an httpx.AsyncClient already owned by the caller. Returns the
    backend device UUID + a short-lived device-scoped JWT to use for the lesson
    pull/manifest/event-forward legs. Best-effort: returns (None, None) on any
    failure so the caller keeps the legacy behaviour.
    """
    if not mac or not base_url:
        return None, None
    secret = _secret()
    if not secret:
        _log(logger, "info", "mint skipped: TBOT_DEVICE_MINT_SECRET not set")
        return None, None

    now = time.time()
    cached = _cache.get(mac)
    if cached is not None and (now - cached[2]) < _CACHE_TTL_S:
        return cached[0], cached[1]

    url = base_url.rstrip("/") + "/internal/devices/mint-token"
    try:
        resp = await client.post(
            url, json={"mac": mac}, headers={"X-Mint-Secret": secret}
        )
        resp.raise_for_status()
        data = (resp.json() or {}).get("data") or {}
        device_uuid = data.get("deviceUuid")
        token = data.get("token")
        if device_uuid and token:
            _cache[mac] = (device_uuid, token, now)
            _log(logger, "info", f"minted device token for {mac} -> {device_uuid}")
            return device_uuid, token
        _log(logger, "warning", f"mint response missing fields for {mac}")
        return None, None
    except Exception as exc:
        # 404 DEVICE_NOT_LINKED / 409 DEVICE_NOT_CLAIMED / network -> fall back.
        _log(logger, "warning", f"mint failed for {mac}: {type(exc).__name__}: {exc}")
        return None, None
