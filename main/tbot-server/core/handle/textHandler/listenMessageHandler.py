import time
import asyncio
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

from core.handle.receiveAudioHandle import startToChat
from core.handle.reportHandle import enqueue_asr_report
from core.handle.sendAudioHandle import send_stt_message, send_tts_message
from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType
from core.utils.util import remove_punctuation_and_length
from core.providers.asr.dto.dto import InterfaceType

TAG = __name__

class ListenTextMessageHandler(TextMessageHandler):
    """Listen message handler"""

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.LISTEN

    async def handle(self, conn: "ConnectionHandler", msg_json: Dict[str, Any]) -> None:
        if "mode" in msg_json:
            conn.client_listen_mode = msg_json["mode"]
            conn.logger.bind(tag=TAG).debug(
                f"Client listening mode: {conn.client_listen_mode}"
            )
        if msg_json["state"] == "start":
            if (
                getattr(conn, "voice_provider", None) is not None
                and conn.client_is_speaking
                and conn.client_listen_mode != "manual"
            ):
                await conn.voice_provider.interrupt()
            # Device switches from playback mode back to recording mode,Clear all audioStatusand Buffer
            conn.reset_audio_states()
        elif msg_json["state"] == "stop":
            conn.client_voice_stop = True
            if conn.asr.interface_type == InterfaceType.STREAM:
                # In streaming mode, send end request
                asyncio.create_task(conn.asr._send_stop_request())
            else:
                # Non-streaming mode: trigger directlyASRRecognize
                if len(conn.asr_audio) > 0:
                    asr_audio_task = conn.asr_audio.copy()
                    conn.reset_audio_states()

                    if len(asr_audio_task) > 0:
                        await conn.asr.handle_voice_stop(conn, asr_audio_task)
        elif msg_json["state"] == "detect":
            conn.client_have_voice = False
            conn.reset_audio_states()
            if "text" in msg_json:
                conn.last_activity_time = time.time() * 1000
                original_text = msg_json["text"]  # Keep original text
                if _is_google_live_connection(conn):
                    return
                filtered_len, filtered_text = remove_punctuation_and_length(
                    original_text
                )

                # Recognize whether wake word
                is_wakeup_words = filtered_text in conn.config.get("wakeup_words")

                if is_wakeup_words:
                    # Wake word only opens listening; do not synthesize a greeting turn.
                    await send_stt_message(conn, original_text)
                    await send_tts_message(conn, "stop", None)
                    conn.client_is_speaking = False
                else:
                    conn.just_woken_up = True
                    # Report plain text data (reuseASRReport function, but do not provide audio data)
                    enqueue_asr_report(conn, original_text, [])
                    # Otherwise NeedLLMFor textContentReply
                    await startToChat(conn, original_text)

def _is_google_live_connection(conn: "ConnectionHandler"):
    config = getattr(conn, "config", {}) or {}
    voice_mode = config.get("voice_mode") if isinstance(config, dict) else {}
    return isinstance(voice_mode, dict) and voice_mode.get("type") == "google_live"
