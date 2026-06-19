import os
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

    async def ensure_voice_allowed(self, conn) -> bool:
        if os.environ.get("TBOT_BYPASS_VOICE_CONSENT", "").lower() == "true":
            self._log(conn, "warning", "voice consent bypass enabled by TBOT_BYPASS_VOICE_CONSENT")
            return True

        device_id = getattr(conn, "device_id", None)
        base_url = self._base_url(conn)
        secret = os.environ.get("TBOT_DEVICE_MINT_SECRET", "")
        if not device_id or not base_url or not secret:
            self._log(conn, "warning", "voice consent denied: missing device/backend/secret")
            return False

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
                return False

            url = f"{base_url}/internal/devices/{backend_device_id}/ai-voice-consent"
            response = await client.get(
                url,
                headers={"X-Mint-Secret": secret, "Authorization": f"Bearer {secret}"},
            )
            response.raise_for_status()
            payload = response.json() or {}
            data = payload.get("data") if isinstance(payload, dict) else None
            return bool(isinstance(data, dict) and data.get("active") is True)
        except Exception as exc:  # noqa: BLE001 - fail-closed consent boundary
            self._log(conn, "warning", f"voice consent denied: {type(exc).__name__}: {exc}")
            return False
        finally:
            if close_client:
                await client.aclose()

    def _base_url(self, conn) -> str:
        config = getattr(conn, "config", {})
        if not isinstance(config, dict):
            return ""
        server = config.get("server", {})
        if not isinstance(server, dict):
            return ""
        return str(server.get("api_url") or "").rstrip("/")

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
