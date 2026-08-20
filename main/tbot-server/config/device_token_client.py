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
- ``resolve_device_identity`` NEVER raises: any failure returns ``(None, None)``.
  The caller surfaces that locally and skips the backend pull; a MAC/no-token
  request would only produce a backend 401 and hide the real minting failure.
- The shared secret is read from the environment ONLY; it is never logged.
"""

import asyncio
import os
import time
from contextlib import suppress

import httpx

# Backend device JWTs are valid for 15 minutes. Keep a 5-minute validity
# reserve so one resolved token can cover a whole lesson/readback path.
_BACKEND_TOKEN_TTL_S = 15 * 60
_RESERVED_MIN_VALIDITY_S = 5 * 60
_CACHE_TTL_S = _BACKEND_TOKEN_TTL_S - _RESERVED_MIN_VALIDITY_S

# {mac: (device_uuid, token, cached_at_monotonic_s)}
_cache = {}


def _secret():
    return os.environ.get("TBOT_DEVICE_MINT_SECRET", "")


def _cache_age_seconds(cached_at):
    """Age a monotonic entry, with compatibility for pre-migration epoch entries."""
    monotonic_age = time.monotonic() - cached_at
    if monotonic_age < -_CACHE_TTL_S:
        wall_age = time.time() - cached_at
        if wall_age >= 0:
            return wall_age
    return monotonic_age


def cached_device_uuid(mac):
    """Backend device UUID already minted for ``mac``, or None.

    Read-only view of the mint cache for callers that must not perform network
    I/O (the operator console renders synchronously). A live lesson connection has
    already minted on its pull leg, so this is populated in practice; when it is
    not, the caller must say so rather than offer the MAC as if it were a UUID.
    """
    entry = _cache.get(mac)
    if not entry:
        return None
    if _cache_age_seconds(entry[2]) > _CACHE_TTL_S:
        return None
    return entry[0]


def _log(logger, level, message):
    if logger is None:
        return
    with suppress(Exception):
        getattr(logger.bind(tag="DeviceToken"), level)(message)


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

    cached = _cache.get(mac)
    if cached is not None and _cache_age_seconds(cached[2]) <= _CACHE_TTL_S:
        return cached[0], cached[1]

    url = base_url.rstrip("/") + "/internal/devices/mint-token"
    for attempt in range(2):
        try:
            resp = await client.post(
                url,
                json={"mac": mac},
                headers={"X-Mint-Secret": secret, "Authorization": f"Bearer {secret}"},
                follow_redirects=False,
            )
            resp.raise_for_status()
            data = (resp.json() or {}).get("data") or {}
            device_uuid = data.get("deviceUuid")
            token = data.get("token")
            if device_uuid and token:
                _cache[mac] = (device_uuid, token, time.monotonic())
                _log(logger, "info", f"minted device token for {mac} -> {device_uuid}")
                return device_uuid, token
            _log(logger, "warning", f"mint response missing fields for {mac}")
            return None, None
        except (httpx.TransportError, TimeoutError) as exc:
            if attempt == 0:
                _log(logger, "warning", f"mint transient failure for {mac}; retrying")
                await asyncio.sleep(0.25)
                continue
            _log(logger, "warning", f"mint failed for {mac}: {type(exc).__name__}: {exc}")
            return None, None
        except Exception as exc:
            # 404 DEVICE_NOT_LINKED / 409 DEVICE_NOT_CLAIMED -> fail closed.
            _log(logger, "warning", f"mint failed for {mac}: {type(exc).__name__}: {exc}")
            return None, None
    return None, None
