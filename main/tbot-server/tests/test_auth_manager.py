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

    def test_verify_token_returns_false_for_malformed_token(self):
        auth = AuthManager("secret")

        self.assertFalse(auth.verify_token("not-a-token", "client-1", "device-1"))


if __name__ == "__main__":
    unittest.main()
