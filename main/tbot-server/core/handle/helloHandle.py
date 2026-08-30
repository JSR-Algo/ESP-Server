import time
import json
import uuid
import random
import asyncio
import hashlib
import re
from datetime import datetime, timezone
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
SAFE_EVIDENCE_JOURNEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _evidence_peer_identity_hash(conn):
    device_id = str(getattr(conn, "device_id", "") or "")
    client_id = str(getattr(conn, "client_id", "") or "")
    digest = hashlib.sha256(f"{device_id}\0{client_id}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"

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

def _new_asyncio_lock():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    return asyncio.Lock()

# Used to prevent concurrent callswakeupWordsResponseLock of
_wakeup_response_lock = _new_asyncio_lock()

def _to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _is_google_live_connection(conn: "ConnectionHandler"):
    config = getattr(conn, "config", None)
    if not isinstance(config, dict):
        return False
    voice_mode = config.get("voice_mode")
    return isinstance(voice_mode, dict) and voice_mode.get("type") == "google_live"

def _google_live_output_sample_rate(conn: "ConnectionHandler"):
    config = getattr(conn, "config", None)
    if isinstance(config, dict):
        google_live = config.get("google_live")
        if isinstance(google_live, dict):
            sample_rate = _to_int(google_live.get("output_sample_rate"))
            if sample_rate:
                return sample_rate
    welcome_audio = getattr(conn, "welcome_msg", {}).get("audio_params", {})
    return _to_int(welcome_audio.get("sample_rate"), getattr(conn, "sample_rate", 24000))

async def handleHelloMessage(conn: "ConnectionHandler", msg_json):
    """Handle hello message"""
    send_mcp_initialize = False
    evidence_journey_id = msg_json.get("evidence_journey_id")
    conn.google_live_evidence_journey_id = (
        evidence_journey_id
        if _is_google_live_connection(conn)
        and isinstance(evidence_journey_id, str)
        and SAFE_EVIDENCE_JOURNEY_RE.fullmatch(evidence_journey_id)
        else None
    )
    conn.google_live_evidence_scope = None
    audio_params = msg_json.get("audio_params")
    if audio_params:
        format = audio_params.get("format")
        conn.logger.bind(tag=TAG).debug(f"Client audio format: {format}")
        conn.audio_format = format
        sample_rate = audio_params.get("sample_rate")
        server_audio_params = dict(audio_params)
        if sample_rate:
            client_sample_rate = int(sample_rate)
            conn.input_sample_rate = client_sample_rate
            if _is_google_live_connection(conn):
                conn.sample_rate = _google_live_output_sample_rate(conn)
                server_audio_params["sample_rate"] = conn.sample_rate
                conn.logger.bind(tag=TAG).info(
                    "Set Google Live audio sample rates from client hello: "
                    f"input={client_sample_rate} output={conn.sample_rate}"
                )
            else:
                conn.sample_rate = client_sample_rate
                conn.logger.bind(tag=TAG).info(
                    f"Set output audio sample rate from client hello to: {conn.sample_rate}"
                )
        conn.welcome_msg["audio_params"] = server_audio_params
    features = msg_json.get("features")
    if features:
        conn.logger.bind(tag=TAG).debug(f"Client features: {features}")
        conn.features = features
        if features.get("mcp"):
            conn.logger.bind(tag=TAG).debug("Client supports MCP")
            conn.mcp_client = MCPClient()
            send_mcp_initialize = True

    hello_ack = dict(conn.welcome_msg)
    if conn.google_live_evidence_journey_id is not None:
        server_start_utc = _utc_now_iso()
        provider = getattr(conn, "voice_provider", None)
        prepare_scope = getattr(provider, "prepare_evidence_scope", None)
        live_connection_id = (
            await prepare_scope() if callable(prepare_scope) else None
        )
        if isinstance(live_connection_id, str) and live_connection_id:
            scope = {
                "journeyId": conn.google_live_evidence_journey_id,
                "connectionId": str(conn.session_id),
                "liveConnectionId": live_connection_id,
                "peerIdentityHash": _evidence_peer_identity_hash(conn),
                "serverStartUtc": server_start_utc,
            }
            conn.google_live_evidence_scope = scope
            hello_ack["evidenceScope"] = scope
        else:
            conn.google_live_evidence_journey_id = None
            hello_ack["evidenceScope"] = {
                "status": "FAIL",
                "failureCode": "LIVE_SCOPE_UNAVAILABLE",
            }

    await conn.websocket.send(json.dumps(hello_ack))
    if send_mcp_initialize:
        conn.schedule_mcp_background_task(send_mcp_initialize_message(conn))


async def checkWakeupWords(conn: "ConnectionHandler", text):
    if _is_google_live_connection(conn):
        return False

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
