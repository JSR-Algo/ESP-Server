import hmac
import base64
import hashlib
import secrets
import time


class AuthenticationError(Exception):
    """Authentication exception"""

    pass


class AuthManager:
    """
    Unified authorization auth manager
    Generate and verify client_id device_id token(HMAC-SHA256)authentication triplet
    token does not contain plaintext client_id/device_id,only carries signature + Timestamp; client_id/device_idPass during connection
    in MQTT mid client_id: client_id, username: device_id, password: token
    in Websocket in,header:{Device-ID: device_id, Client-ID: client_id, Authorization: Bearer token, ......}
    """

    def __init__(
        self,
        secret_key: str,
        expire_seconds: int = 60 * 60 * 24 * 30,
        device_revoked_after: dict = None,
    ):
        if not expire_seconds or expire_seconds < 0:
            self.expire_seconds = 60 * 60 * 24 * 30
        else:
            self.expire_seconds = expire_seconds
        self.secret_key = secret_key
        self._revoked_tokens = {}
        self._device_revoked_after = self._normalize_device_revocations(
            device_revoked_after or {}
        )

    @staticmethod
    def _normalize_device_revocations(device_revoked_after: dict) -> dict:
        revocations = {}
        if not isinstance(device_revoked_after, dict):
            return revocations
        for device_id, cutoff in device_revoked_after.items():
            try:
                revocations[str(device_id)] = int(cutoff)
            except (TypeError, ValueError):
                continue
        return revocations

    def _sign(self, content: str) -> str:
        """HMAC-SHA256 signature and Base64 encoding"""
        sig = hmac.new(
            self.secret_key.encode("utf-8"), content.encode("utf-8"), hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")

    @staticmethod
    def _new_nonce() -> str:
        return secrets.token_urlsafe(18)

    @staticmethod
    def _token_content(client_id: str, username: str, ts: int, nonce: str) -> str:
        return f"{client_id}|{username}|{ts}|{nonce}"

    @staticmethod
    def _revocation_key(client_id: str, username: str, nonce: str) -> str:
        return f"{client_id}|{username}|{nonce}"

    def _cleanup_revocations(self, now: int) -> None:
        expired = [
            key for key, expires_at in self._revoked_tokens.items()
            if expires_at <= now
        ]
        for key in expired:
            self._revoked_tokens.pop(key, None)

    def generate_token(self, client_id: str, username: str) -> str:
        """
        Generate token
        Args:
            client_id: device connection ID
            username: device username (usually deviceId)
        Returns:
            str: token string
        """
        ts = int(time.time())
        nonce = self._new_nonce()
        content = self._token_content(client_id, username, ts, nonce)
        signature = self._sign(content)
        # tokenOnly contains signature, Timestamp, and nonce; no plaintext device info.
        token = f"{signature}.{ts}.{nonce}"
        return token

    def verify_token(self, token: str, client_id: str, username: str) -> bool:
        """
        Verify token validity
        Args:
            token: token passed by client
            client_id: client_id used by connection
            username: username used by connection
        """
        now = int(time.time())
        try:
            sig_part, ts_str, nonce = token.split(".")
            ts = int(ts_str)
            if now - ts > self.expire_seconds:
                return False  # Expire

            expected_sig = self._sign(self._token_content(client_id, username, ts, nonce))
            if not hmac.compare_digest(sig_part, expected_sig):
                return False

            self._cleanup_revocations(now)
            if ts <= self._device_revoked_after.get(username, 0):
                return False
            if self._revocation_key(client_id, username, nonce) in self._revoked_tokens:
                return False

            return True
        except Exception:
            return False

    def revoke_token(self, token: str, client_id: str, username: str) -> bool:
        """Invalidate one still-valid token without rotating the shared secret."""
        now = int(time.time())
        try:
            sig_part, ts_str, nonce = token.split(".")
            ts = int(ts_str)
            if now - ts > self.expire_seconds:
                return False
            expected_sig = self._sign(self._token_content(client_id, username, ts, nonce))
            if not hmac.compare_digest(sig_part, expected_sig):
                return False
            self._cleanup_revocations(now)
            expires_at = ts + self.expire_seconds + 1
            self._revoked_tokens[self._revocation_key(client_id, username, nonce)] = expires_at
            return True
        except Exception:
            return False

    def revoke_device_tokens(self, username: str, revoked_at: int = None) -> None:
        """Invalidate existing tokens for one device without affecting others."""
        cutoff = int(time.time()) if revoked_at is None else int(revoked_at)
        self._device_revoked_after[username] = max(
            cutoff,
            self._device_revoked_after.get(username, 0),
        )
