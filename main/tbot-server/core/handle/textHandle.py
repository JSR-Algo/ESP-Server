from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from core.handle.textMessageHandlerRegistry import TextMessageHandlerRegistry
from core.handle.textMessageProcessor import TextMessageProcessor

TAG = __name__

# Global handlerRegistertable
message_registry = TextMessageHandlerRegistry()

# Create GlobalMessageHandler instance
message_processor = TextMessageProcessor(message_registry)


async def handleTextMessage(conn: "ConnectionHandler", message):
    """Handle text message"""
    await message_processor.process_message(conn, message)
