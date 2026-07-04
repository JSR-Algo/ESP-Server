import json
import time
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from core.utils import textUtils
from core.utils.util import audio_to_data
from core.providers.tts.dto.dto import SentenceType
from core.utils.audioRateController import AudioRateController

TAG = __name__
# Audio frame duration (ms)
AUDIO_FRAME_DURATION = 60
# Pre-buffer PacketQuantitysend directly to reduce latency
PRE_BUFFER_COUNT = 5


async def sendAudioMessage(conn: "ConnectionHandler", sentenceType, audios, text, sentence_id=None):
    # Skip old sentence leftover audio
    if sentence_id is not None and sentence_id != conn.sentence_id:
        return

    if _is_google_live_connection(conn):
        if sentenceType == SentenceType.LAST:
            extra_fields = None
            if getattr(conn, "lesson_continue_listening_after_tts_stop", False):
                conn.lesson_continue_listening_after_tts_stop = False
                extra_fields = {
                    "continue_listening": True,
                    "listen_mode": "realtime",
                }
            await send_tts_message(conn, "stop", None, extra_fields=extra_fields)
            if conn.close_after_chat:
                await conn.close()
        return

    has_audio = audios is not None and len(audios) > 0

    if conn.tts.tts_audio_first_sentence:
        conn.logger.bind(tag=TAG).info(f"Send first voice segment: {text}")
        conn.tts.tts_audio_first_sentence = False

    if sentenceType == SentenceType.FIRST:
        if has_audio and not getattr(conn, "client_is_speaking", False):
            await send_tts_message(conn, "start")
        # Subsequent of same sentenceMessageAdd to flow control queue, send immediately otherwise
        if (
            hasattr(conn, "audio_rate_controller")
            and conn.audio_rate_controller
            and getattr(conn, "audio_flow_control", {}).get("sentence_id")
            == conn.sentence_id
        ):
            conn.audio_rate_controller.add_message(
                lambda: send_tts_message(conn, "sentence_start", text)
            )
        else:
            # New sentence or flow controller not initialized, send immediately
            await send_tts_message(conn, "sentence_start", text)
    elif has_audio and not getattr(conn, "client_is_speaking", False):
        await send_tts_message(conn, "start")

    await sendAudio(conn, audios)
    # Send sentence startMessage
    if sentenceType is not SentenceType.MIDDLE:
        conn.logger.bind(tag=TAG).info(f"Send audio message: {sentenceType}, {text}")

    # Send EndMessage(if last text)
    if sentenceType == SentenceType.LAST:
        extra_fields = None
        if getattr(conn, "lesson_continue_listening_after_tts_stop", False):
            conn.lesson_continue_listening_after_tts_stop = False
            extra_fields = {
                "continue_listening": True,
                "listen_mode": "realtime",
            }
        await send_tts_message(conn, "stop", None, extra_fields=extra_fields)
        if conn.close_after_chat:
            await conn.close()


async def _wait_for_audio_completion(conn: "ConnectionHandler"):
    """
    Wait for audio queue to empty and wait for prebuffered packets to finish playing

    Args:
        conn: connection object
    """
    if hasattr(conn, "audio_rate_controller") and conn.audio_rate_controller:
        rate_controller = conn.audio_rate_controller
        conn.logger.bind(tag=TAG).debug(
            f"Waiting for audio sending to finish, {len(rate_controller.queue)} packets still in queue"
        )
        await rate_controller.queue_empty_event.wait()

        # Wait for pre-buffered packets playback complete
        # beforeNSend packets directly, add2network jitter packets, need extra wait for them to finish playing on client
        frame_duration_ms = rate_controller.frame_duration
        pre_buffer_playback_time = (PRE_BUFFER_COUNT + 2) * frame_duration_ms / 1000.0
        await asyncio.sleep(pre_buffer_playback_time)

        conn.logger.bind(tag=TAG).debug("Audio sending complete")


async def _send_to_mqtt_gateway(
    conn: "ConnectionHandler", opus_packet, timestamp, sequence
):
    """
    Send opus data packet with 16-byte header to mqtt_gateway
    Args:
        conn: connection object
        opus_packet: opus data packet
        timestamp: timestamp
        sequence: sequence number
    """
    # foropusAdd data packet16Byte Header
    header = bytearray(16)
    header[0] = 1  # type
    header[2:4] = len(opus_packet).to_bytes(2, "big")  # payload length
    header[4:8] = sequence.to_bytes(4, "big")  # sequence
    header[8:12] = timestamp.to_bytes(4, "big")  # Timestamp
    header[12:16] = len(opus_packet).to_bytes(4, "big")  # opusLength

    # Send complete data packet with header
    complete_packet = bytes(header) + opus_packet
    await conn.websocket.send(complete_packet)


async def sendAudio(
    conn: "ConnectionHandler", audios, frame_duration=AUDIO_FRAME_DURATION
):
    """
    Send audio packets, using AudioRateController for precise flow control

    Args:
        conn: Connection object
        audios: Single opus packet (bytes) or opus packet list
        frame_duration: Frame duration (milliseconds), defaults to global constant AUDIO_FRAME_DURATION
    """
    if audios is None or len(audios) == 0:
        return

    send_delay = conn.config.get("tts_audio_send_delay", -1) / 1000.0
    is_single_packet = isinstance(audios, bytes)

    # Initialize or get RateController
    rate_controller, flow_control = _get_or_create_rate_controller(
        conn, frame_duration, is_single_packet
    )

    # Convert uniformly to list processing
    audio_list = [audios] if is_single_packet else audios

    # Send audio packet
    await _send_audio_with_rate_control(
        conn, audio_list, rate_controller, flow_control, send_delay
    )


def _get_or_create_rate_controller(
    conn: "ConnectionHandler", frame_duration, is_single_packet
):
    """
    Get or create RateController and flow_control

    Args:
        conn: Connection Object
        frame_duration: Frame duration
        is_single_packet: Whether single-packet mode (True: TTSStreaming Single Packet, False: Batch Packets)

    Returns:
        (rate_controller, flow_control)
    """
    # Check whether need reset controller
    need_reset = False

    if not hasattr(conn, "audio_rate_controller"):
        # Controller does not exist, need create
        need_reset = True
    else:
        rate_controller = conn.audio_rate_controller

        # Background send task stopped, then need reset
        if (
            not rate_controller.pending_send_task
            or rate_controller.pending_send_task.done()
        ):
            need_reset = True
        # whensentence_id Changed, need reset
        elif (
            getattr(conn, "audio_flow_control", {}).get("sentence_id")
            != conn.sentence_id
        ):
            need_reset = True

    if need_reset:
        # Create or get rate_controller
        if not hasattr(conn, "audio_rate_controller"):
            conn.audio_rate_controller = AudioRateController(frame_duration)
        else:
            conn.audio_rate_controller.reset()

        # Initialize flow_control
        conn.audio_flow_control = {
            "packet_count": 0,
            "sequence": 0,
            "sentence_id": conn.sentence_id,
        }

        # Start background send loop
        _start_background_sender(
            conn, conn.audio_rate_controller, conn.audio_flow_control
        )

    return conn.audio_rate_controller, conn.audio_flow_control


def _start_background_sender(conn: "ConnectionHandler", rate_controller, flow_control):
    """
    Start background send loop task

    Args:
        conn: connection object
        rate_controller: rate controller
        flow_control: flow control state
    """

    async def send_callback(packet):
        # Check whether should abort
        if conn.client_abort:
            raise asyncio.CancelledError("Client aborted")

        conn.last_activity_time = time.time() * 1000
        await _do_send_audio(conn, packet, flow_control)

    # Use start_sending Start background loop
    rate_controller.start_sending(send_callback)


async def _send_audio_with_rate_control(
    conn: "ConnectionHandler", audio_list, rate_controller, flow_control, send_delay
):
    """
    Send audio packets using rate_controller

    Args:
        conn: Connection object
        audio_list: Audio packet list
        rate_controller: Rate controller
        flow_control: Flow control state
        send_delay: Fixed delay (seconds), -1 means use dynamic flow control
    """
    for packet in audio_list:
        if conn.client_abort:
            return

        conn.last_activity_time = time.time() * 1000

        # Pre-buffer: beforeNpackets send directly
        if flow_control["packet_count"] < PRE_BUFFER_COUNT:
            await _do_send_audio(conn, packet, flow_control)
        elif send_delay > 0:
            # Fixed delay mode
            await asyncio.sleep(send_delay)
            await _do_send_audio(conn, packet, flow_control)
        else:
            # Dynamic flow control mode: only add to queue, background loop handles sending
            rate_controller.add_audio(packet)


async def _do_send_audio(conn: "ConnectionHandler", opus_packet, flow_control):
    """
    Execute actual audio sending
    """
    packet_index = flow_control.get("packet_count", 0)
    sequence = flow_control.get("sequence", 0)

    if conn.conn_from_mqtt_gateway:
        # CalculateTimestamp(based on playback position)
        start_time = time.time()
        timestamp = int(start_time * 1000) % (2**32)
        await _send_to_mqtt_gateway(conn, opus_packet, timestamp, sequence)
    else:
        # Send DirectlyopusData packet
        await conn.websocket.send(opus_packet)

    # Update Flow ControlStatus
    flow_control["packet_count"] = packet_index + 1
    flow_control["sequence"] = sequence + 1


async def send_tts_message(conn: "ConnectionHandler", state, text=None, extra_fields=None):
    """Send TTS status message"""
    if text is None and state == "sentence_start":
        return
    message = {"type": "tts", "state": state, "session_id": conn.session_id}
    if text is not None:
        message["text"] = textUtils.check_emoji(text)
    if state == "stop" and _is_google_live_connection(conn) and not _is_lesson_session(conn):
        message["continue_listening"] = True
        message["listen_mode"] = "realtime"
    if extra_fields:
        message.update(extra_fields)
    if state == "sentence_start":
        child_name = _child_name_for_tts_state(conn)
        if child_name:
            message["child_name"] = child_name
            message["childName"] = child_name

    # TTSPlayback End
    if state == "stop":
        # SaveCurrent sentence_idUsed for later determining whether current round
        current_sentence_id = conn.sentence_id
        # PlayPromptsound
        tts_notify = conn.config.get("enable_stop_tts_notify", False) and not _is_google_live_connection(conn)
        if tts_notify:
            stop_tts_notify_voice = conn.config.get(
                "stop_tts_notify_voice", "config/assets/tts_notify.mp3"
            )
            audios = await audio_to_data(stop_tts_notify_voice, is_opus=True)
            await sendAudio(conn, audios)
        # Wait until all audio packets sent
        await _wait_for_audio_completion(conn)

        # Check if current round. If the sentence changed but no new audio flow
        # has started, still send stop so the device does not stay in Speaking.
        flow_sentence_id = getattr(conn, "audio_flow_control", {}).get("sentence_id")
        if current_sentence_id != conn.sentence_id and flow_sentence_id == conn.sentence_id:
            return

        # Stop audio sending loop (only call when flow controller initialized)
        if hasattr(conn, "audio_rate_controller") and conn.audio_rate_controller:
            conn.audio_rate_controller.stop_sending()
        conn.clearSpeakStatus()

    # SendMessageto Client
    await conn.websocket.send(json.dumps(message))
    if state == "start":
        conn.client_is_speaking = True


def _child_name_for_tts_state(conn: "ConnectionHandler"):
    config = getattr(conn, "config", {}) or {}
    if not isinstance(config, dict):
        return None
    child_profile = config.get("child_profile") or {}
    if not isinstance(child_profile, dict):
        return None
    raw_name = child_profile.get("child_name") or child_profile.get("childName")
    if not isinstance(raw_name, str):
        return None
    child_name = raw_name.strip()
    return child_name or None

def _is_google_live_connection(conn: "ConnectionHandler"):
    config = getattr(conn, "config", {}) or {}
    if isinstance(config, dict):
        voice_mode = config.get("voice_mode") or {}
        if isinstance(voice_mode, dict) and voice_mode.get("type") == "google_live":
            return True
    provider = getattr(conn, "voice_provider", None)
    provider_name = provider.__class__.__name__ if provider is not None else ""
    return "GoogleLive" in provider_name

def _is_lesson_session(conn: "ConnectionHandler"):
    try:
        from core.voice.session_orchestrator import SessionMode, normalize_session_mode

        return normalize_session_mode(getattr(conn, "session_mode", None)) == SessionMode.LESSON
    except Exception:
        return str(getattr(conn, "session_mode", "")).upper() == "LESSON"

async def send_stt_message(conn: "ConnectionHandler", text):
    """Send STT status message"""
    config = getattr(conn, "config", {}) or {}
    end_prompt = config.get("end_prompt", {}) if isinstance(config, dict) else {}
    if not isinstance(end_prompt, dict):
        end_prompt = {}
    end_prompt_str = end_prompt.get("prompt")
    if end_prompt_str and end_prompt_str == text:
        return

    # ParseJSONFormat, extract actual user speechContent
    display_text = text
    try:
        # Try ParseJSONFormat
        if text.strip().startswith("{") and text.strip().endswith("}"):
            parsed_data = json.loads(text)
            if isinstance(parsed_data, dict) and "content" in parsed_data:
                # If contains speakerInfoofJSONformat, show onlycontentPart
                display_text = parsed_data["content"]
                # SaveSpeakerInfotoconnObject
                if "speaker" in parsed_data:
                    conn.current_speaker = parsed_data["speaker"]
    except (json.JSONDecodeError, TypeError):
        # If notJSONformat, use raw text directly
        display_text = text
    stt_text = textUtils.get_string_no_punctuation_or_emoji(display_text)
    await conn.websocket.send(
        json.dumps({"type": "stt", "text": stt_text, "session_id": conn.session_id})
    )


async def send_display_message(conn: "ConnectionHandler", text):
    """Send display-only message"""
    message = {
        "type": "stt",
        "text": text,
        "session_id": conn.session_id
    }
    await conn.websocket.send(json.dumps(message))
