"""Live-mode music plugin with pause/resume and duration support.

Streams local music files directly to the device websocket so it works under
Google Live (which has no TTS module) and the classic pipeline alike.

State machine:
  None  ──play_music──▶  Playing  ──pause──▶  Paused  ──resume──▶  Playing
                              │                  │
                              └──── stop ◀──────┘
"""

import asyncio
import difflib
import json
import os
import random
import time
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

MUSIC_CACHE: dict = {}


play_music_function_desc = {
    "type": "function",
    "function": {
        "name": "play_music",
        "description": (
            "Play a local music track on the device from a fixed library. "
            "The library is small (a few tracks). DO NOT invent titles. "
            "When the user does not name a specific available track, pass "
            "song_name='random' and let the server pick. The track plays "
            "until stopped or the optional duration ends. "
            "Examples: 'bật nhạc' → song_name='random'; "
            "'phát Hai Con Thằn Lằn' → song_name='Hai Con Thằn Lằn'; "
            "'phát bài/đoạn <tên>' → song_name='<tên chính xác người dùng nói>'; "
            "'phát nhạc remix/sôi động/vui' without an available exact title → song_name='random'; "
            "'cho nghe nhạc 30 phút' → song_name='random', duration_minutes=30."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "song_name": {
                    "type": "string",
                    "description": (
                        "Exact title spoken by the user, or 'random'. "
                        "If the user did not name a song, ALWAYS use 'random' — "
                        "style words like remix/sôi động/vui are not song titles; "
                        "do NOT invent a title."
                    ),
                },
                "duration_minutes": {
                    "type": "number",
                    "description": (
                        "Optional. If the user requests a time-bounded playback "
                        "('phát 30 phút', 'cho nghe khoảng 1 tiếng'), set this "
                        "to the number of minutes. Music auto-stops after that. "
                        "Leave unset for indefinite play."
                    ),
                },
                "response_success": {
                    "type": "string",
                    "description": (
                        "Friendly reply. Use {title} for the song title."
                    ),
                },
            },
            "required": ["song_name", "response_success"],
        },
    },
}


stop_music_function_desc = {
    "type": "function",
    "function": {
        "name": "stop_music",
        "description": (
            "Permanently stop and forget the current music playback. "
            "Call when the user asks to stop, turn off, or end music. "
            "Examples: 'tắt nhạc', 'dừng nhạc', 'thôi nhạc đi'. "
            "Use pause_music (not stop_music) when the user just wants a brief break."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "response_success": {
                    "type": "string",
                    "description": "Friendly reply confirming the stop.",
                },
            },
            "required": ["response_success"],
        },
    },
}


pause_music_function_desc = {
    "type": "function",
    "function": {
        "name": "pause_music",
        "description": (
            "Pause music playback at its current position so it can be "
            "resumed later. Call when the user asks to pause briefly. "
            "Examples: 'tạm dừng nhạc', 'ngắt nhạc xíu', 'pause nhạc'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "response_success": {
                    "type": "string",
                    "description": "Friendly reply confirming the pause.",
                },
            },
            "required": ["response_success"],
        },
    },
}


resume_music_function_desc = {
    "type": "function",
    "function": {
        "name": "resume_music",
        "description": (
            "Resume the previously paused music from where it left off. "
            "Call when the user asks to continue music. "
            "Examples: 'tiếp tục phát nhạc', 'phát tiếp', 'mở lại nhạc đi', 'nghe tiếp đi'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "response_success": {
                    "type": "string",
                    "description": "Friendly reply confirming the resume.",
                },
            },
            "required": ["response_success"],
        },
    },
}


def _scan_music_files(music_dir: str, music_ext):
    paths = []
    if not os.path.isdir(music_dir):
        return paths
    for file in Path(music_dir).rglob("*"):
        if file.is_file() and file.suffix.lower() in music_ext:
            paths.append(str(file.relative_to(music_dir)))
    return paths


def _ensure_music_cache(conn) -> dict:
    if MUSIC_CACHE:
        return MUSIC_CACHE
    plugins_cfg = conn.config.get("plugins", {}) if isinstance(conn.config, dict) else {}
    plugin_cfg = plugins_cfg.get("play_music", {}) if isinstance(plugins_cfg, dict) else {}
    music_dir = os.path.abspath(plugin_cfg.get("music_dir") or "./music")
    raw_exts = plugin_cfg.get("music_ext") or [".mp3", ".wav", ".p3"]
    music_ext = tuple(ext.lower() for ext in raw_exts)
    MUSIC_CACHE.update(
        {
            "music_dir": music_dir,
            "music_ext": music_ext,
            "music_files": _scan_music_files(music_dir, music_ext),
        }
    )
    return MUSIC_CACHE


def _pick_song(song_name: str, cache: dict):
    """Return (file_relative_path, match_status). Status is 'random', 'matched', or 'not_found'."""
    files = cache.get("music_files") or []
    if not files:
        return None, "empty_library"
    normalized = (song_name or "").strip().lower()
    random_markers = {"random", "ngẫu nhiên", "ngau nhien", "bất kỳ", "bat ky"}
    generic_music_markers = {
        "nhạc",
        "nhac",
        "music",
        "remix",
        "sôi động",
        "soi dong",
        "vui",
    }
    if (
        not normalized
        or normalized in random_markers
        or any(marker in normalized for marker in random_markers)
        or normalized in generic_music_markers
    ):
        return random.choice(files), "random"
    best_match = None
    best_ratio = 0.0
    for file in files:
        title = os.path.splitext(os.path.basename(file))[0].lower()
        ratio = difflib.SequenceMatcher(None, normalized, title).ratio()
        # Also boost when title contains the request as substring
        if normalized in title or title in normalized:
            ratio = max(ratio, 0.7)
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = file
    # Strict threshold: only accept clearly-similar matches.
    if best_ratio >= 0.55:
        return best_match, "matched"
    return None, "not_found"


def _list_titles(cache: dict, limit: int = 12) -> list:
    files = cache.get("music_files") or []
    titles = [os.path.splitext(os.path.basename(f))[0] for f in files]
    return titles[:limit]


class _MusicSession:
    """Per-connection playback state."""

    def __init__(self, conn, music_path: str, sample_rate: int):
        self.conn = conn
        self.music_path = music_path
        self.title = os.path.splitext(os.path.basename(music_path))[0]
        self.sample_rate = sample_rate
        self.frames: List[bytes] = []  # full opus stream cached for resume
        self.frame_index = 0
        self.pause_event = asyncio.Event()
        self.pause_event.set()  # not paused initially
        self.stop_event = asyncio.Event()
        self.stream_task: Optional[asyncio.Task] = None
        self.deadline: Optional[float] = None  # wall-clock seconds, monotonic
        self.encode_done = False
        self.sequence = 0

    def is_paused(self) -> bool:
        return not self.pause_event.is_set()

    def pause(self):
        self.pause_event.clear()

    def resume(self):
        self.pause_event.set()

    def stop(self):
        self.stop_event.set()
        self.pause_event.set()  # unblock any awaiters


def _get_session(conn) -> Optional[_MusicSession]:
    return getattr(conn, "_music_session", None)


def _clear_session(conn, session: _MusicSession):
    if getattr(conn, "_music_session", None) is session:
        conn._music_session = None


async def _stream_music_loop(session: _MusicSession):
    """Background task: encode → enqueue → watch pause/stop/deadline.

    Pacing is delegated to the connection's AudioRateController; we just
    enqueue and watch for state changes. To pause: drop the controller
    queue, remember how many frames were never delivered, re-enqueue
    from that frame index on resume.
    """
    from core.utils.opus_encoder_utils import OpusEncoderUtils
    from core.utils.util import audio_to_data_stream

    conn = session.conn
    loop = asyncio.get_running_loop()
    encoder = OpusEncoderUtils(sample_rate=session.sample_rate, channels=1, frame_size_ms=60)

    # --- Encode phase (blocking, in executor) ---
    encode_done_event = asyncio.Event()

    def encode_callback(packet: bytes):
        if session.stop_event.is_set():
            raise InterruptedError("music stopped during encode")
        session.frames.append(packet)

    def encode_blocking():
        try:
            audio_to_data_stream(
                session.music_path,
                is_opus=True,
                callback=encode_callback,
                sample_rate=session.sample_rate,
                opus_encoder=encoder,
            )
        except InterruptedError:
            pass
        finally:
            loop.call_soon_threadsafe(encode_done_event.set)

    encode_future = loop.run_in_executor(None, encode_blocking)

    FRAME_MS = 60

    try:
        await _wait_for_voice_output_to_settle(session)
        if session.stop_event.is_set():
            return

        await encode_done_event.wait()
        if session.stop_event.is_set() or not session.frames:
            return

        await _send_music_playback_state(session, "start")
        logger.bind(tag=TAG).info(
            f"music session started title={session.title} frames={len(session.frames)} "
            f"duration_deadline={session.deadline}"
        )

        async def _send_from(start_idx: int):
            """Send music frames with an independent pacer, outside TTS/Live queues."""
            i = start_idx
            while i < len(session.frames):
                if session.stop_event.is_set() or session.is_paused():
                    return i
                await _send_music_frame(session, session.frames[i])
                i += 1
                session.frame_index = i
                await asyncio.sleep(0.06)
            return i

        session.frame_index = await _send_from(session.frame_index)

        # Watch loop: pause, deadline, completion
        while True:
            if session.stop_event.is_set():
                break

            # Duration deadline
            if session.deadline is not None and time.monotonic() >= session.deadline:
                logger.bind(tag=TAG).info(
                    f"music duration deadline reached, stopping title={session.title}"
                )
                break

            if session.is_paused():
                logger.bind(tag=TAG).info(
                    f"music paused at frame={session.frame_index}/{len(session.frames)}"
                )

                await session.pause_event.wait()
                if session.stop_event.is_set():
                    break
                logger.bind(tag=TAG).info(
                    f"music resumed at frame={session.frame_index}"
                )
                session.frame_index = await _send_from(session.frame_index)
                continue

            if session.frame_index >= len(session.frames):
                break

            # Otherwise wait a bit and re-check
            await asyncio.sleep(0.1)

    except asyncio.CancelledError:
        session.stop_event.set()
        raise
    except Exception as exc:
        logger.bind(tag=TAG).warning(f"music stream loop error: {exc}")
    finally:
        if not encode_future.done():
            session.stop_event.set()
            try:
                await asyncio.wait_for(encode_future, timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass
        logger.bind(tag=TAG).info(
            f"music session ended title={session.title}"
        )
        await _send_music_playback_state(session, "stop")
        _clear_session(conn, session)

async def _wait_for_voice_output_to_settle(session: _MusicSession):
    conn = session.conn
    try:
        max_wait = float(conn.config.get("live_music_start_delay_sec", 2.0))
    except (TypeError, ValueError):
        max_wait = 2.0
    deadline = time.monotonic() + max(0.0, max_wait)
    while time.monotonic() < deadline:
        if session.stop_event.is_set():
            return
        if (
            getattr(conn, "google_live_audio_out_started_at", None) is None
            and not getattr(conn, "client_is_speaking", False)
        ):
            return
        await asyncio.sleep(0.05)

async def _send_music_frame(session: _MusicSession, opus_packet: bytes):
    conn = session.conn
    if getattr(conn, "client_abort", False):
        return
    conn.last_activity_time = time.time() * 1000
    if getattr(conn, "conn_from_mqtt_gateway", False):
        header = bytearray(16)
        header[0] = 1
        header[2:4] = len(opus_packet).to_bytes(2, "big")
        header[4:8] = session.sequence.to_bytes(4, "big")
        timestamp = int(time.time() * 1000) % (2**32)
        header[8:12] = timestamp.to_bytes(4, "big")
        header[12:16] = len(opus_packet).to_bytes(4, "big")
        await conn.websocket.send(bytes(header) + opus_packet)
    else:
        await conn.websocket.send(opus_packet)
    session.sequence += 1


async def _send_music_playback_state(session: _MusicSession, state: str):
    """Signal device playback mode without touching shared TTS queues/state."""
    conn = session.conn
    websocket = getattr(conn, "websocket", None)
    if websocket is None:
        return
    if state == "start":
        conn._music_playback_owner = session
    elif getattr(conn, "_music_playback_owner", None) is not session:
        return
    await websocket.send(
        json.dumps(
            {
                "type": "tts",
                "state": state,
                "session_id": conn.session_id,
            }
        )
    )
    logger.bind(tag=TAG).info(f"music playback_state={state} title={session.title}")
    if state == "stop" and getattr(conn, "_music_playback_owner", None) is session:
        conn._music_playback_owner = None


def _format_reply(template: str, **fields) -> str:
    if not template:
        return ""
    result = template
    for key, value in fields.items():
        result = result.replace("{" + key + "}", str(value))
    return result


@register_function("play_music", play_music_function_desc, ToolType.SYSTEM_CTL)
def play_music(
    conn: "ConnectionHandler",
    song_name: str,
    duration_minutes: Optional[float] = None,
    response_success: str = "",
):
    loop = getattr(conn, "loop", None)
    if loop is None or not loop.is_running():
        return ActionResponse(action=Action.ERROR, response="Hệ thống đang bận, thử lại sau.")

    cache = _ensure_music_cache(conn)
    if not cache.get("music_files"):
        return ActionResponse(
            action=Action.ERROR,
            response=f"Thư mục nhạc trống ({cache.get('music_dir')}).",
        )

    selected, status = _pick_song(song_name, cache)
    if status == "empty_library":
        return ActionResponse(
            action=Action.ERROR,
            response="Hiện chưa có bài hát nào trong thư viện.",
        )
    if status == "not_found":
        titles = _list_titles(cache)
        titles_str = ", ".join(titles) if titles else ""
        suggestion = (
            f"Bài '{song_name}' chưa có trong thư viện. "
            f"Mình có thể phát các bài: {titles_str}. Bạn chọn bài nào?"
            if titles_str
            else f"Bài '{song_name}' chưa có trong thư viện."
        )
        return ActionResponse(action=Action.ERROR, response=suggestion)
    music_path = os.path.join(cache["music_dir"], selected)
    title = os.path.splitext(os.path.basename(selected))[0]

    # Replace any existing session
    existing = _get_session(conn)
    if existing is not None:
        existing.stop()

    sample_rate = int(getattr(conn, "sample_rate", 24000))
    session = _MusicSession(conn, music_path, sample_rate)
    if duration_minutes is not None:
        try:
            minutes = float(duration_minutes)
            if minutes > 0:
                session.deadline = time.monotonic() + minutes * 60.0
        except (TypeError, ValueError):
            pass

    conn._music_session = session
    session.stream_task = loop.create_task(_stream_music_loop(session))

    base_reply = response_success or f"Đang phát {title} cho bạn."
    reply = _format_reply(base_reply, title=title, song=title)
    logger.bind(tag=TAG).info(
        f"play_music invoked title={title} duration_minutes={duration_minutes}"
    )
    return ActionResponse(action=Action.RESPONSE, result=title, response=reply)


@register_function("stop_music", stop_music_function_desc, ToolType.SYSTEM_CTL)
def stop_music(conn: "ConnectionHandler", response_success: str = ""):
    session = _get_session(conn)
    if session is None:
        reply = response_success or "Hiện tại không có nhạc đang phát."
        return ActionResponse(action=Action.RESPONSE, result="not_playing", response=reply)
    session.stop()
    reply = response_success or "Đã tắt nhạc."
    logger.bind(tag=TAG).info("stop_music invoked")
    return ActionResponse(action=Action.RESPONSE, result="stopped", response=reply)


@register_function("pause_music", pause_music_function_desc, ToolType.SYSTEM_CTL)
def pause_music(conn: "ConnectionHandler", response_success: str = ""):
    session = _get_session(conn)
    if session is None:
        reply = response_success or "Hiện tại không có nhạc đang phát."
        return ActionResponse(action=Action.RESPONSE, result="not_playing", response=reply)
    if session.is_paused():
        reply = response_success or "Nhạc đang tạm dừng rồi."
        return ActionResponse(action=Action.RESPONSE, result="already_paused", response=reply)
    session.pause()
    reply = response_success or "Đã tạm dừng nhạc."
    logger.bind(tag=TAG).info(f"pause_music invoked at frame={session.frame_index}")
    return ActionResponse(action=Action.RESPONSE, result="paused", response=reply)


@register_function("resume_music", resume_music_function_desc, ToolType.SYSTEM_CTL)
def resume_music(conn: "ConnectionHandler", response_success: str = ""):
    session = _get_session(conn)
    if session is None:
        reply = response_success or "Chưa có bài nhạc nào để phát tiếp."
        return ActionResponse(action=Action.RESPONSE, result="no_session", response=reply)
    if not session.is_paused():
        reply = response_success or "Nhạc đang phát rồi."
        return ActionResponse(action=Action.RESPONSE, result="already_playing", response=reply)
    session.resume()
    reply = response_success or f"Phát tiếp {session.title} cho bạn."
    reply = _format_reply(reply, title=session.title, song=session.title)
    logger.bind(tag=TAG).info(f"resume_music invoked at frame={session.frame_index}")
    return ActionResponse(action=Action.RESPONSE, result="resumed", response=reply)
