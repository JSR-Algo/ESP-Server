import unittest

from core.handle.textMessageHandler import TextMessageHandler


class _ConcreteTextMessageHandler(TextMessageHandler):
    async def handle(self, conn, msg_json):
        return await super().handle(conn, msg_json)

    @property
    def message_type(self):
        return super().message_type


class TextMessageHandlerBaseTest(unittest.IsolatedAsyncioTestCase):
    async def test_base_abstract_methods_are_noop_when_called_by_concrete_subclass(self):
        handler = _ConcreteTextMessageHandler()

        self.assertIsNone(await handler.handle(None, {}))
        self.assertIsNone(handler.message_type)


if __name__ == "__main__":
    unittest.main()
