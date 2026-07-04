import os
import time
from typing import Optional

import httpx

from config.device_token_client import resolve_device_identity


class VoiceConsentClient:
    """Backend-backed parental consent gate for AI voice.

    The gate fails closed: missing device id, missing backend URL, missing shared
    secret, network errors, and inactive/withdrawn consent all deny Live audio.
    """

    def __init__(self, client=None):
        self._client = client
        self._cache = {}

    async def ensure_voice_allowed(self, conn) -> bool:
        if os.environ.get("TBOT_BYPASS_VOICE_CONSENT", "").lower() == "true":
            cache_key = "env:bypass_voice_consent"
            if self._cached_allowed(cache_key):
                return True
            self._log(conn, "warning", "voice consent bypass enabled by TBOT_BYPASS_VOICE_CONSENT")
            self._remember(cache_key, True)
            return True

        device_id = getattr(conn, "device_id", None)
        if self._is_factory_test_claimed_device(conn, device_id):
            cache_key = f"factory_test_claimed:{str(device_id).strip().lower()}"
            if self._cached_allowed(cache_key):
                return True
            self._log(conn, "warning", "voice consent bypass enabled for factory test claimed device")
            self._remember(cache_key, True)
            return True

        bypass_key = self._voice_consent_bypass_key(conn, device_id)
        if bypass_key:
            cache_key = f"voice_consent_bypass:{bypass_key}"
            if self._cached_allowed(cache_key):
                return True
            self._log(conn, "warning", "voice consent bypass enabled for configured device")
            self._remember(cache_key, True)
            return True

        base_url = self._base_url(conn)
        secret = os.environ.get("TBOT_DEVICE_MINT_SECRET", "")
        if not device_id or not base_url or not secret:
            self._log(conn, "warning", "voice consent denied: missing device/backend/secret")
            return False

        cache_key = f"{base_url}:{device_id}"
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and now < cached["expires_at"]:
            return bool(cached["allowed"])

        close_client = False
        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=5.0)
            close_client = True
        try:
            backend_device_id, _token = await resolve_device_identity(
                client,
                base_url,
                device_id,
                logger=getattr(conn, "logger", None),
            )
            if not backend_device_id:
                self._log(conn, "warning", "voice consent denied: device identity mint unavailable")
                self._remember(cache_key, False)
                return False

            url = f"{base_url}/internal/devices/{backend_device_id}/ai-voice-consent"
            response = await client.get(
                url,
                headers={"X-Mint-Secret": secret, "Authorization": f"Bearer {secret}"},
            )
            response.raise_for_status()
            payload = response.json() or {}
            data = payload.get("data") if isinstance(payload, dict) else None
            allowed = bool(isinstance(data, dict) and data.get("active") is True)
            self._remember(cache_key, allowed)
            return allowed
        except Exception as exc:  # noqa: BLE001 - fail-closed consent boundary
            if self._is_rate_limited(exc) and cached and cached.get("allowed") is True:
                stale_until = float(cached.get("stale_until", 0.0))
                if now < stale_until:
                    self._log(conn, "warning", "voice consent rate-limited; using recent active cache")
                    return True
            self._log(conn, "warning", f"voice consent denied: {type(exc).__name__}: {exc}")
            self._remember(cache_key, False)
            return False
        finally:
            if close_client:
                await client.aclose()

    def _cached_allowed(self, cache_key: str) -> bool:
        cached = self._cache.get(cache_key)
        if not cached:
            return False
        return bool(cached.get("allowed")) and time.monotonic() < float(cached.get("expires_at", 0.0))

    def _base_url(self, conn) -> str:
        config = getattr(conn, "config", {})
        if not isinstance(config, dict):
            return ""
        server = config.get("server", {})
        if not isinstance(server, dict):
            return ""
        return str(server.get("api_url") or "").rstrip("/")

    def _is_factory_test_claimed_device(self, conn, device_id) -> bool:
        if not device_id:
            return False
        config = getattr(conn, "config", {})
        if not isinstance(config, dict):
            return False
        server = config.get("server", {})
        if not isinstance(server, dict):
            return False
        # Mirror OTAHandler: the global bench/dev switch marks every device
        # factory-test-claimed, so consent is bypassed for all of them too.
        if server.get("factory_test_claimed_all"):
            return True
        configured = server.get("factory_test_claimed_devices") or []
        if isinstance(configured, str):
            configured = [item.strip() for item in configured.split(",")]
        normalized = str(device_id).strip().lower()
        return normalized in {str(item).strip().lower() for item in configured if item}

    def _voice_consent_bypass_key(self, conn, device_id) -> str:
        config = getattr(conn, "config", {})
        if not isinstance(config, dict):
            return ""
        server = config.get("server", {})
        if not isinstance(server, dict):
            return ""
        configured = server.get("voice_consent_bypass_devices") or []
        if isinstance(configured, dict):
            configured = [configured]
        elif isinstance(configured, str):
            configured = [{"mac": item.strip()} for item in configured.split(",")]
        device_id = str(device_id or "").strip().lower()
        client_ip = str(getattr(conn, "client_ip", "") or "").strip().lower()
        for item in configured:
            if not isinstance(item, dict):
                continue
            mac = str(item.get("mac") or "").strip().lower()
            ip = str(item.get("ip") or "").strip().lower()
            if mac and device_id and mac == device_id:
                return f"mac:{mac}"
            if ip and client_ip and ip == client_ip:
                return f"ip:{ip}"
        return ""

    def _remember(self, cache_key: str, allowed: bool) -> None:
        now = time.monotonic()
        ttl = self._env_float(
            "TBOT_VOICE_CONSENT_CACHE_TTL_SECONDS" if allowed else "TBOT_VOICE_CONSENT_NEGATIVE_CACHE_TTL_SECONDS",
            60.0 if allowed else 5.0,
        )
        stale_ttl = self._env_float("TBOT_VOICE_CONSENT_STALE_ON_429_TTL_SECONDS", 300.0)
        expires_at = now + max(0.0, ttl)
        self._cache[cache_key] = {
            "allowed": bool(allowed),
            "expires_at": expires_at,
            "stale_until": now + max(ttl, stale_ttl) if allowed else expires_at,
        }

    def _is_rate_limited(self, exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        return isinstance(exc, httpx.HTTPStatusError) and getattr(response, "status_code", None) == 429

    def _env_float(self, name: str, fallback: float) -> float:
        raw = os.environ.get(name)
        if raw in (None, ""):
            return fallback
        try:
            return float(raw)
        except ValueError:
            return fallback

    def _log(self, conn, level: str, message: str) -> None:
        logger = getattr(conn, "logger", None)
        if logger is None:
            return
        try:
            getattr(logger.bind(tag="VoiceConsent"), level)(message)
        except Exception:
            return


_client: Optional[VoiceConsentClient] = None


def get_voice_consent_client() -> VoiceConsentClient:
    global _client
    if _client is None:
        _client = VoiceConsentClient()
    return _client
