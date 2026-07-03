import time
import json
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from core.utils.util import audio_to_data
from core.handle.abortHandle import handleAbortMessage
from core.handle.intentHandler import handle_user_intent
from core.utils.output_counter import check_device_output_limit
from core.handle.sendAudioHandle import send_stt_message, SentenceType

TAG = __name__


async def handleAudioMessage(conn: "ConnectionHandler", audio):
    # Whether current segment has speech
    have_voice = conn.vad.is_vad(conn, audio)
    # If DeviceJust nowWoken, briefly ignoreVADDetect
    if hasattr(conn, "just_woken_up") and conn.just_woken_up:
        have_voice = False
        # Set short delay then restoreVADDetect
        if not hasattr(conn, "vad_resume_task") or conn.vad_resume_task.done():
            conn.vad_resume_task = asyncio.create_task(resume_vad_detection(conn))
        return
    # Long device idle detection, used forsay goodbye
    await no_voice_close_connect(conn, have_voice)
    # Receive Audio
    await conn.asr.receive_audio(conn, audio, have_voice)


async def resume_vad_detection(conn: "ConnectionHandler"):
    # Wait2Recover After SecondsVADDetect
    await asyncio.sleep(2)
    conn.just_woken_up = False


async def startToChat(conn: "ConnectionHandler", text):
    if _is_google_live_connection(conn):
        return

    # Check whether input isJSONFormat (include speakerInfo)
    speaker_name = None
    language_tag = None
    actual_text = text

    try:
        # Try ParseJSONformat input
        if text.strip().startswith("{") and text.strip().endswith("}"):
            data = json.loads(text)
            if "speaker" in data and "content" in data:
                speaker_name = data["speaker"]
                language_tag = data["language"]
                actual_text = data["content"]
                conn.logger.bind(tag=TAG).info(f"Parsed speaker info: {speaker_name}")

                # Use DirectlyJSONFormatted text, do not parse
                actual_text = text
    except (json.JSONDecodeError, KeyError):
        # If parse fails, continue using original text
        pass

    # SaveSpeakerInfoto connection object
    if speaker_name:
        conn.current_speaker = speaker_name
    else:
        conn.current_speaker = None

    if conn.need_bind:
        await check_bind_device(conn)
        return

    # If today's output word count exceeds limit
    if conn.max_output_size > 0:
        if check_device_output_limit(
            conn.headers.get("device-id"), conn.max_output_size
        ):
            await max_out_size(conn)
            return

    # manual In mode, do not interrupt playingContent
    if conn.client_is_speaking and conn.client_listen_mode != "manual":
        await handleAbortMessage(conn)

    # First perform intent analysis, use actual textContent
    intent_handled = await handle_user_intent(conn, actual_text)

    if intent_handled:
        # If intent already handled, no more chat
        return

    # Intent not handled. Continue regular chat flow, use actual textContent
    await send_stt_message(conn, actual_text)

    # Prepare start new session
    conn.client_abort = False

    conn.executor.submit(conn.chat, actual_text)


async def no_voice_close_connect(conn: "ConnectionHandler", have_voice):
    if have_voice:
        conn.last_activity_time = time.time() * 1000
        return
    # Only after initializedTimestamponly when performing timeout check
    if conn.last_activity_time > 0.0:
        no_voice_time = time.time() * 1000 - conn.last_activity_time
        close_connection_no_voice_time = int(
            conn.config.get("close_connection_no_voice_time", 120)
        )
        if (
            not conn.close_after_chat
            and no_voice_time > 1000 * close_connection_no_voice_time
        ):
            conn.close_after_chat = True
            conn.client_abort = False
            end_prompt = conn.config.get("end_prompt", {})
            if end_prompt and end_prompt.get("enable", True) is False:
                conn.logger.bind(tag=TAG).info("End conversation, no need to send end prompt")
                await conn.close()
                return
            prompt = end_prompt.get("prompt")
            if not prompt:
                prompt = "pleaseYouto```Time flies```future head, end this dialog with emotional, reluctant words.!"
            await startToChat(conn, prompt)


def _is_google_live_connection(conn: "ConnectionHandler"):
    config = getattr(conn, "config", {}) or {}
    voice_mode = config.get("voice_mode") if isinstance(config, dict) else {}
    return isinstance(voice_mode, dict) and voice_mode.get("type") == "google_live"

async def max_out_size(conn: "ConnectionHandler"):
    if _is_google_live_connection(conn):
        return

    # Play prompt for exceeding maximum output word count
    conn.client_abort = False
    text = "Sorry, I have something to do now. Let’s chat tomorrow at this time. Deal! See you tomorrow,Bye!"
    await send_stt_message(conn, text)
    file_path = "config/assets/max_output_size.wav"
    opus_packets = await audio_to_data(file_path)
    conn.tts.tts_audio_queue.put((SentenceType.LAST, opus_packets, text))
    conn.close_after_chat = True


async def check_bind_device(conn: "ConnectionHandler"):
    if _is_google_live_connection(conn):
        return

    if conn.bind_code:
        # Ensurebind_codeis6Digits
        if len(conn.bind_code) != 6:
            conn.logger.bind(tag=TAG).error(f"Invalid binding code format: {conn.bind_code}")
            text = "Binding code formatErrorPlease check config."
            await send_stt_message(conn, text)
            return

        text = f"pleaseLoginControl panel, enter{conn.bind_code},Bind device."
        await send_stt_message(conn, text)

        # PlayPromptsound
        music_path = "config/assets/bind_code.wav"
        opus_packets = await audio_to_data(music_path)
        conn.tts.tts_audio_queue.put((SentenceType.FIRST, opus_packets, text))

        # Play digits one by one
        for i in range(6):  # Ensure only play6Digits
            try:
                digit = conn.bind_code[i]
                num_path = f"config/assets/bind_code/{digit}.wav"
                num_packets = await audio_to_data(num_path)
                conn.tts.tts_audio_queue.put((SentenceType.MIDDLE, num_packets, None))
            except Exception as e:
                conn.logger.bind(tag=TAG).error(f"Play number audio failed: {e}")
                continue
        conn.tts.tts_audio_queue.put((SentenceType.LAST, [], None))
    else:
        # Play unboundPrompt
        conn.client_abort = False
        text = f"No version found for this deviceInfo,configure correctly OTAAddress, then recompile firmware."
        await send_stt_message(conn, text)
        music_path = "config/assets/bind_not_found.wav"
        opus_packets = await audio_to_data(music_path)
        conn.tts.tts_audio_queue.put((SentenceType.LAST, opus_packets, text))
