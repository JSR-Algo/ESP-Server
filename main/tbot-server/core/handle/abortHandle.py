import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
TAG = __name__


async def handleAbortMessage(conn: "ConnectionHandler"):
    conn.logger.bind(tag=TAG).info("Abort message received")
    # NOTE: grace-gate attempt removed — server-only reject caused device
    # state desync (device locally transitioned away from Speaking but
    # server kept sending audio → stuck in "Đang lắng nghe"). Echo-induced
    # wake-word false-positives need to be fixed firmware-side instead.
    # Set to interrupt state, will auto interruptllm,ttsTask
    conn.close_after_chat = False
    conn.client_abort = True
    if getattr(conn, "voice_provider", None) is not None:
        try:
            await conn.voice_provider.interrupt()
        except Exception as exc:
            conn.logger.bind(tag=TAG).warning(
                f"Abort voice-provider interrupt failed: {exc}"
            )
    conn.clear_queues()
    # Interrupt client speaking state
    await conn.websocket.send(
        json.dumps({"type": "tts", "state": "stop", "session_id": conn.session_id})
    )
    conn.clearSpeakStatus()
    conn.logger.bind(tag=TAG).info("Abort message received-end")
