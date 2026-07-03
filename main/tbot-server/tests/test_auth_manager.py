import unittest
from unittest.mock import patch

from core.auth import AuthManager


class AuthManagerTest(unittest.TestCase):
    def test_custom_expiry_is_preserved_and_token_round_trips(self):
        auth = AuthManager("secret", expire_seconds=10)

        with patch("core.auth.time.time", return_value=1000):
            token = auth.generate_token("client-1", "device-1")
        with patch("core.auth.time.time", return_value=1005):
            self.assertTrue(auth.verify_token(token, "client-1", "device-1"))

        self.assertEqual(auth.expire_seconds, 10)

    def test_generated_token_carries_nonce(self):
        auth = AuthManager("secret", expire_seconds=10)

        with patch("core.auth.time.time", return_value=1000):
            token = auth.generate_token("client-1", "device-1")

        self.assertEqual(len(token.split(".")), 3)
        with patch("core.auth.time.time", return_value=1001):
            self.assertTrue(auth.verify_token(token, "client-1", "device-1"))

    def test_revoke_token_invalidates_only_that_device_token(self):
        auth = AuthManager("secret", expire_seconds=10)

        with patch("core.auth.time.time", return_value=1000):
            token = auth.generate_token("client-1", "device-1")
            other = auth.generate_token("client-1", "device-2")

        with patch("core.auth.time.time", return_value=1001):
            self.assertTrue(auth.revoke_token(token, "client-1", "device-1"))
        with patch("core.auth.time.time", return_value=1001):
            self.assertFalse(auth.verify_token(token, "client-1", "device-1"))
            self.assertTrue(auth.verify_token(other, "client-1", "device-2"))

    def test_revoke_device_tokens_invalidates_existing_tokens_for_one_device(self):
        auth = AuthManager("secret", expire_seconds=10)

        with patch("core.auth.time.time", return_value=1000):
            old_token = auth.generate_token("client-1", "device-1")
            other_device = auth.generate_token("client-1", "device-2")

        with patch("core.auth.time.time", return_value=1001):
            auth.revoke_device_tokens("device-1")

        with patch("core.auth.time.time", return_value=1002):
            fresh_token = auth.generate_token("client-1", "device-1")

        with patch("core.auth.time.time", return_value=1003):
            self.assertFalse(auth.verify_token(old_token, "client-1", "device-1"))
            self.assertTrue(auth.verify_token(fresh_token, "client-1", "device-1"))
            self.assertTrue(auth.verify_token(other_device, "client-1", "device-2"))

    def test_verify_token_returns_false_for_malformed_token(self):
        auth = AuthManager("secret")

        self.assertFalse(auth.verify_token("not-a-token", "client-1", "device-1"))


if __name__ == "__main__":
    unittest.main()
