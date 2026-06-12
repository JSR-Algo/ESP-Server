import time
import json
import uuid
import random
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from core.utils.dialogue import Message
from core.utils.util import audio_to_data
from core.providers.tts.dto.dto import SentenceType
from core.utils.wakeup_word import WakeupWordsConfig
from core.handle.sendAudioHandle import sendAudioMessage, send_tts_message
from core.utils.util import remove_punctuation_and_length, opus_datas_to_wav_bytes
from core.providers.tools.device_mcp import MCPClient, send_mcp_initialize_message

TAG = __name__

WAKEUP_CONFIG = {
    "refresh_time": 10,
    "responses": [
        "I am always here, please speak.",
        "Here, ready for your command anytime.",
        "Here I am, please tell me.",
        "Please speak, I'm listening.",
        "Please speak, I am ready.",
        "Please say command.",
        "I am listening carefully, please speak.",
        "How can I help you?",
        "I am here, waiting for your command.",
    ],
}

# Create global wake word config manager
wakeup_words_config = WakeupWordsConfig()

# Used to prevent concurrent callswakeupWordsResponseLock of
_wakeup_response_lock = asyncio.Lock()


async def handleHelloMessage(conn: "ConnectionHandler", msg_json):
    """Handle hello message"""
    audio_params = msg_json.get("audio_params")
    if audio_params:
        format = audio_params.get("format")
        conn.logger.bind(tag=TAG).debug(f"Client audio format: {format}")
        conn.audio_format = format
        conn.welcome_msg["audio_params"] = audio_params
        sample_rate = audio_params.get("sample_rate")
        if sample_rate:
            conn.sample_rate = int(sample_rate)
            conn.logger.bind(tag=TAG).info(
                f"Set output audio sample rate from client hello to: {conn.sample_rate}"
            )
    features = msg_json.get("features")
    if features:
        conn.logger.bind(tag=TAG).debug(f"Client features: {features}")
        conn.features = features
        if features.get("mcp"):
            conn.logger.bind(tag=TAG).debug("Client supports MCP")
            conn.mcp_client = MCPClient()
            # Send initialization
            asyncio.create_task(send_mcp_initialize_message(conn))

    await conn.websocket.send(json.dumps(conn.welcome_msg))


async def checkWakeupWords(conn: "ConnectionHandler", text):
    enable_wakeup_words_response_cache = conn.config[
        "enable_wakeup_words_response_cache"
    ]

    # WaitttsInitialize, wait max3seconds
    start_time = time.time()
    while time.time() - start_time < 3:
        if conn.tts:
            break
        await asyncio.sleep(0.1)
    else:
        return False

    if not enable_wakeup_words_response_cache:
        return False

    _, filtered_text = remove_punctuation_and_length(text)
    if filtered_text not in conn.config.get("wakeup_words"):
        return False

    conn.just_woken_up = True
    tts_stopped = False

    try:
        # Get current voice
        voice = getattr(conn.tts, "voice", "default")
        if not voice:
            voice = "default"

        # Get wake-word reply config
        response = wakeup_words_config.get_wakeup_response(voice)
        if not response or not response.get("file_path"):
            response = {
                "voice": "default",
                "file_path": "config/assets/wakeup_words_short.wav",
                "time": 0,
                "text": "I'm here!",
            }

        # Get audio data
        opus_packets = await audio_to_data(response.get("file_path"), use_cache=False)
        # Play wake word reply
        conn.client_abort = False

        # Treat wake word reply as new session, generate new sentence_id, ensure flow controller reset
        conn.sentence_id = str(uuid.uuid4().hex)

        conn.logger.bind(tag=TAG).info(f"Play wake word reply: {response.get('text')}")
        await sendAudioMessage(conn, SentenceType.FIRST, opus_packets, response.get("text"))
        await sendAudioMessage(conn, SentenceType.LAST, [], None)
        tts_stopped = True

        # Supplement Dialogue
        conn.dialogue.put(Message(role="assistant", content=response.get("text")))

        # Check whether wake word reply needs update
        if time.time() - response.get("time", 0) > WAKEUP_CONFIG["refresh_time"]:
            if not _wakeup_response_lock.locked():
                asyncio.create_task(wakeupWordsResponse(conn))
    except Exception as e:
        conn.logger.bind(tag=TAG).warning(f"Wake word reply failed: {e}")
    finally:
        if getattr(conn, "client_is_speaking", False) and not tts_stopped:
            await send_tts_message(conn, "stop", None)
    return True


async def wakeupWordsResponse(conn: "ConnectionHandler"):
    if not conn.tts:
        return

    try:
        # Try get lock, return if cannot get
        if not await _wakeup_response_lock.acquire():
            return

        # Randomly choose reply from predefined reply list
        result = random.choice(WAKEUP_CONFIG["responses"])
        if not result or len(result) == 0:
            return

        # GenerateTTSAudio
        tts_result = await asyncio.to_thread(conn.tts.to_tts, result)
        if not tts_result:
            return

        # Get current voice
        voice = getattr(conn.tts, "voice", "default")

        # Use linkedsample_rate
        wav_bytes = opus_datas_to_wav_bytes(tts_result, sample_rate=conn.sample_rate)
        file_path = wakeup_words_config.generate_file_path(voice)
        with open(file_path, "wb") as f:
            f.write(wav_bytes)
        # Update config
        wakeup_words_config.update_wakeup_response(voice, file_path, result)
    finally:
        # Ensure lock released in any case
        if _wakeup_response_lock.locked():
            _wakeup_response_lock.release()
