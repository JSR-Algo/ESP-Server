import json
import unittest

from core.handle.textMessageProcessor import (
    MAX_LOG_VALUE_LEN,
    TextMessageProcessor,
    _message_log_summary,
    _redact_for_log,
)


class FakeLogger:
    def __init__(self):
        self.messages = []

    def bind(self, **kwargs):
        return self

    def info(self, message):
        self.messages.append(("info", message))

    def error(self, message):
        self.messages.append(("error", message))


class FakeHandler:
    def __init__(self):
        self.handled = None

    async def handle(self, conn, msg_json):
        self.handled = msg_json


class FakeRegistry:
    def __init__(self, handler):
        self.handler = handler

    def get_handler(self, message_type):
        return self.handler if message_type == "mcp" else None


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


class FakeConn:
    def __init__(self):
        self.logger = FakeLogger()
        self.websocket = FakeWebSocket()


class TextMessageProcessorLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_message_logging_summarizes_large_payload_and_keeps_handler_input(self):
        tools = [
            {
                "name": f"tool_{index}",
                "description": "x" * (MAX_LOG_VALUE_LEN + 20),
                "inputSchema": {"type": "object"},
            }
            for index in range(20)
        ]
        msg_json = {
            "type": "mcp",
            "payload": {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"tools": tools, "api_key": "secret-value"},
            },
        }
        raw_message = json.dumps(msg_json)
        handler = FakeHandler()
        conn = FakeConn()

        await TextMessageProcessor(FakeRegistry(handler)).process_message(
            conn, raw_message
        )

        self.assertEqual(handler.handled, msg_json)
        log_message = conn.logger.messages[0][1]
        self.assertIn("Received mcp message:", log_message)
        self.assertIn('"result_keys":["api_key","tools"]', log_message)
        self.assertNotIn("secret-value", log_message)
        self.assertNotIn("x" * MAX_LOG_VALUE_LEN, log_message)
        self.assertLess(len(log_message), 512)

    async def test_unknown_message_type_logs_sanitized_summary(self):
        conn = FakeConn()
        payload = {"type": "unknown", "payload": {"password": "secret"}}

        await TextMessageProcessor(FakeRegistry(None)).process_message(
            conn, json.dumps(payload)
        )

        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(conn.logger.messages[0][0], "info")
        self.assertEqual(conn.logger.messages[1][0], "error")
        self.assertIn("Received unknown message type", conn.logger.messages[1][1])
        self.assertIn("<redacted>", conn.logger.messages[1][1])
        self.assertNotIn("secret", conn.logger.messages[1][1])

    async def test_numeric_message_is_echoed_to_websocket(self):
        conn = FakeConn()

        await TextMessageProcessor(FakeRegistry(None)).process_message(conn, "123")

        self.assertEqual(conn.websocket.sent, ["123"])
        self.assertEqual(
            conn.logger.messages,
            [("info", "Received numeric message: 123")],
        )

    async def test_invalid_json_message_is_forwarded_to_websocket(self):
        conn = FakeConn()

        await TextMessageProcessor(FakeRegistry(None)).process_message(conn, "hello")

        self.assertEqual(conn.websocket.sent, ["hello"])
        self.assertEqual(
            conn.logger.messages,
            [("error", "Parsed invalid message: hello")],
        )


class MessageLogSummaryTests(unittest.TestCase):
    def test_redaction_limits_depth_list_size_and_long_strings(self):
        deep_value = {"a": {"b": {"c": {"d": {"e": "hidden"}}}}}
        long_value = "x" * (MAX_LOG_VALUE_LEN + 3)

        redacted = _redact_for_log(
            {"items": list(range(10)), "long": long_value, "deep": deep_value}
        )

        self.assertEqual(redacted["items"][-1], "<2 more items>")
        self.assertEqual(len(redacted["items"]), 9)
        self.assertTrue(redacted["long"].endswith("...<truncated 3 chars>"))
        self.assertEqual(redacted["deep"]["a"]["b"]["c"]["d"], "<truncated>")

    def test_summary_handles_non_dict_and_scalar_payloads(self):
        self.assertEqual(_message_log_summary(["raw"]), "['raw']")

        summary = _message_log_summary({"type": "listen", "payload": "x" * 300})

        self.assertIn('"type":"listen"', summary)
        self.assertIn("<truncated 44 chars>", summary)

    def test_summary_redacts_direct_payload_secret(self):
        summary = _message_log_summary(
            {
                "type": "listen",
                "payload": {
                    "access_token": "access-secret",
                    "authorization": "Bearer secret-token",
                    "text": "xin chao",
                },
            }
        )

        self.assertIn("<redacted>", summary)
        self.assertNotIn("secret-token", summary)
        self.assertNotIn("access-secret", summary)


if __name__ == "__main__":
    unittest.main()
