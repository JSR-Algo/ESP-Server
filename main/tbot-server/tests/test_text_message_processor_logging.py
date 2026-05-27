import json
import unittest

from core.handle.textMessageProcessor import (
    MAX_LOG_VALUE_LEN,
    TextMessageProcessor,
    _message_log_summary,
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


class FakeConn:
    def __init__(self):
        self.logger = FakeLogger()


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


class MessageLogSummaryTests(unittest.TestCase):
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
