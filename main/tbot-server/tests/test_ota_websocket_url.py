import unittest
import sys

for module_name, required_attr in (
    ("core.auth", "AuthManager"),
    ("core.utils.util", "get_local_ip"),
):
    if module_name in sys.modules and not hasattr(sys.modules[module_name], required_attr):
        del sys.modules[module_name]
from core.api.ota_handler import OTAHandler, is_placeholder_websocket_url


def _config(websocket):
    return {
        "server": {
            "auth_key": "test-secret",
            "auth": {"enabled": False},
            "websocket": websocket,
        }
    }


def _handler(websocket):
    handler = OTAHandler.__new__(OTAHandler)
    handler.config = _config(websocket)
    return handler


class OTAWebsocketUrlTest(unittest.TestCase):
    def test_chinese_placeholder_falls_back_to_local_websocket_url(self):
        handler = _handler("ws://你的ip或者域名:端口号/tbot/v1/")

        self.assertEqual(
            handler._get_websocket_url("192.168.100.154", 8000),
            "ws://192.168.100.154:8000/tbot/v1/",
        )

    def test_explicit_websocket_url_is_preserved(self):
        handler = _handler("ws://192.168.100.154:8000/tbot/v1/")

        self.assertEqual(
            handler._get_websocket_url("10.0.0.5", 9000),
            "ws://192.168.100.154:8000/tbot/v1/",
        )

    def test_placeholder_detection_covers_chinese_and_english_templates(self):
        self.assertTrue(is_placeholder_websocket_url("ws://你的ip或者域名:端口号/tbot/v1/"))
        self.assertTrue(is_placeholder_websocket_url("ws://Your_IP:8000/tbot/v1/"))
        self.assertTrue(is_placeholder_websocket_url("ws://You_IP:8000/tbot/v1/"))
        self.assertFalse(is_placeholder_websocket_url("ws://192.168.100.154:8000/tbot/v1/"))
