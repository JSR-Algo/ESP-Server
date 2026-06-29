"""Built-in SAMPLE lesson — plays on the spoken ``start_lesson`` trigger with NO
backend assignment ("không quan tâm assign trước").

This is a DEMO path, gated by ``lesson.sample_lesson`` (env ``LESSON_SAMPLE_ENABLED``),
default ON in the robot config. When ON, the ``start_lesson`` tool loads THIS canned lesson instead of
pulling the device's assigned lesson — so a robot with no assignment (or no backend at
all) can still demo the full lesson flow: background + 3 layers, Google Live
child-response, several steps, then the happy face + return to conversation owned by
``ConnectionHandler.finish_lesson_mode``.

DEFAULT INTERACTION MODE: the spoken demo defaults to ``interactive`` so the child
practises repeat + recall turns through Google Live's lesson child-response window
before the lesson completes. ``lesson.sample_mode: passive`` remains available for
no-mic smoke tests and auto-advances every step on render ack.

SELF-CONTAINED ASSET DELIVERY: a ``SampleAssetCache`` passthrough skips the sha256
download/verify gate the real ``AssetCache`` enforces — the sample manifest has no
hosted, checksummed asset pack. The three layer image ``src`` values are passed to the
firmware verbatim (optionally prefixed with ``lesson.sample_asset_base_url``). The
runtime's blank-src guard (``_invalid_lesson_step_scene_reason``) STILL runs on every
step; it passes only because the passthrough returns each ``src`` unchanged (non-blank),
so every step MUST carry non-blank layer srcs (validated at module load). If the bytes
are unreachable the firmware degrades each layer to its caption-only fallback and STILL
renders + acks + advances, so the lesson always completes. HTTP delivery only — the
sample refuses to start under ``asset_delivery_mode == "sd_pack"``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

TAG = "SampleLesson"

SAMPLE_LESSON_ID = "sample-barn-say-it"
SAMPLE_ASSIGNMENT_ID = "sample-lesson"

# Bare authored filenames (reused from the seed "barn say-it" theme). Resolved against
# lesson.sample_asset_base_url when configured; otherwise shipped verbatim (firmware
# falls back to caption-only, lesson still completes).
_POSTER_FILE = "barn-round-field-poster.jpg"
_OBJECT_FILE = "barn.png"
_OVERLAY_TEACH_FILE = "bright-teach.png"
_OVERLAY_CELEBRATE_FILE = "bright-celebrate.png"


def _resolve_src(filename: str, asset_base: str) -> str:
    base = (asset_base or "").rstrip("/")
    return f"{base}/{filename}" if base else filename


def _scene(asset_base: str, *, expression: str, overlay_file: str, primary_word: str,
           glyph: str) -> Dict[str, Any]:
    """The frozen 3-layer scene shape (backgroundScene + teachingObject + robotOverlay),
    structurally identical to the renderer-v1 fixture so it passes BOTH the server-side
    ``_invalid_lesson_step_scene_reason`` gate and the firmware three-layer gate."""
    return {
        "backgroundScene": {
            "mode": "poster",
            "poster": {
                "key": "backgroundScene.poster",
                "src": _resolve_src(_POSTER_FILE, asset_base),
                "fit": "cover",
            },
            "video": None,
            "altCaption": "A red barn in a round green field",
        },
        "teachingObject": {
            "primaryWord": primary_word,
            "supportWords": ["farm", "hay"],
            "placement": {"anchor": "center", "paddingTopPercent": 8},
            "asset": {
                "key": "teachingObject.sample",
                "src": _resolve_src(_OBJECT_FILE, asset_base),
            },
            "primitiveFallbackCard": {
                "glyph": glyph,
                "label": primary_word,
                "normalPlacement": {"anchor": "center", "paddingTopPercent": 8},
            },
        },
        "robotOverlay": {
            "robotState": "talking",
            "pose": "teach",
            "expression": expression,
            "anchor": "bottomLeft",
            "pivot": {"x": 0.5, "y": 1.0},
            "asset": {
                "key": f"robotOverlay.{expression}",
                "src": _resolve_src(overlay_file, asset_base),
            },
            "atlas": {"image": _resolve_src(overlay_file, asset_base), "cell": 0},
            "pointerEvents": "none",
        },
    }


DEFAULT_SAMPLE_STEP_DWELL_SEC = 5.0
DEFAULT_SAMPLE_STEP_TIMEOUT_SEC = 75.0


def _step(step_id: str, step_type: str, prompt: str, scene: Dict[str, Any],
          timeout_sec: float = DEFAULT_SAMPLE_STEP_TIMEOUT_SEC,
          dwell_sec: float = DEFAULT_SAMPLE_STEP_DWELL_SEC,
          completion_class: str = "passive",
          expected_responses: Optional[List[str]] = None,
          retry_prompt: Optional[str] = None,
          response_timeout_sec: Optional[float] = None,
          max_no_answer_attempts: Optional[int] = None) -> Dict[str, Any]:
    # completionClass is the AUTHORITATIVE classifier. A PASSIVE step auto-advances on
    # its render ack (no child interaction). An INTERACTIVE step instead opens a
    # child-response window (provider.open_lesson_child_response_window) and waits for the
    # child's spoken answer (runtime.on_child_response via Live STT) before advancing.
    # dwellSec only applies to passive steps: it keeps the rendered scene on screen for N
    # seconds before auto-advancing so the child can see/hear it (runtime _passive_dwell_sec).
    step: Dict[str, Any] = {
        "id": step_id,
        "type": step_type,
        "completionClass": completion_class,
        "prompt": prompt,
        "audio": {"via": "tts"},
        "timeoutSec": timeout_sec,
        "scene": scene,
    }
    if completion_class == "passive" and dwell_sec and dwell_sec > 0:
        step["dwellSec"] = float(dwell_sec)
    if expected_responses:
        step["expectedResponses"] = [str(item).strip() for item in expected_responses if str(item).strip()]
    if retry_prompt and retry_prompt.strip():
        step["retryPrompt"] = retry_prompt.strip()
    if response_timeout_sec is not None:
        try:
            if float(response_timeout_sec) > 0:
                step["responseTimeoutSec"] = float(response_timeout_sec)
        except (TypeError, ValueError):
            pass
    if max_no_answer_attempts is not None:
        try:
            if int(max_no_answer_attempts) > 0:
                step["maxNoAnswerAttempts"] = int(max_no_answer_attempts)
        except (TypeError, ValueError):
            pass
    return step


def build_sample_manifest(asset_base: str = "", dwell_sec: float = DEFAULT_SAMPLE_STEP_DWELL_SEC) -> Dict[str, Any]:
    """A short all-passive 'barn say-it' demo: greeting → focus → review → celebrate.
    Each step carries the full 3-layer scene + a spoken prompt; the robot narrates and
    auto-advances, then the celebrate step's happy ending hands off to finish_lesson_mode."""
    steps: List[Dict[str, Any]] = [
        _step(
            "s1", "greeting",
            "Xin chào! Mình là TeeBot. Hôm nay mình cùng nhau học một bài mẫu nhé!",
            _scene(asset_base, expression="teaching", overlay_file=_OVERLAY_TEACH_FILE,
                   primary_word="barn", glyph="🏠"),
            dwell_sec=dwell_sec,
        ),
        _step(
            "s2", "focus",
            "Đây là cái kho — barn. Con nhìn ngôi nhà đỏ trên cánh đồng xanh nhé!",
            _scene(asset_base, expression="teaching", overlay_file=_OVERLAY_TEACH_FILE,
                   primary_word="barn", glyph="🏠"),
            dwell_sec=dwell_sec,
        ),
        _step(
            "s3", "review",
            "Giỏi lắm! Mình vừa học từ mới: barn. Con nhớ chưa nào?",
            _scene(asset_base, expression="thinking", overlay_file=_OVERLAY_TEACH_FILE,
                   primary_word="barn", glyph="🏠"),
            dwell_sec=dwell_sec,
        ),
        _step(
            "s4", "celebrate",
            "Tuyệt vời! Con đã hoàn thành bài học mẫu. Hoan hô con!",
            _scene(asset_base, expression="celebrating", overlay_file=_OVERLAY_CELEBRATE_FILE,
                   primary_word="barn", glyph="🎉"),
            dwell_sec=dwell_sec,
        ),
    ]
    manifest = {
        "manifestVersion": "teebot-lesson-renderer.v1",
        "lessonId": SAMPLE_LESSON_ID,
        "lessonVersion": 1,
        "profile": "espTft",
        "assets": [],  # passthrough cache verifies nothing; firmware fetches srcs directly
        "steps": steps,
    }
    _assert_renderable(manifest)
    return manifest


INTERACTIVE_SAMPLE_LESSON_ID = "sample-barn-say-it-interactive"


def build_interactive_sample_manifest(asset_base: str = "",
                                      dwell_sec: float = DEFAULT_SAMPLE_STEP_DWELL_SEC) -> Dict[str, Any]:
    """A short SPEAKING-practice demo: greeting → focus → SAY-IT (interactive)
    → recall (interactive) → celebrate.

    The interactive steps narrate the question, open a child-response window over Google
    Live (the provider waits for narration TTS to finish, then opens the mic), and only
    advance once the child says the target word (``runtime.on_child_response`` via the
    recognised Live transcript). Wrong attempts stay on the same step and get a retry
    prompt so a child with no backend assignment can still practise vocabulary and reach
    the happy ending after saying the word."""
    steps: List[Dict[str, Any]] = [
        _step(
            "s1", "greeting",
            "Chào con! Hôm nay mình cùng tập nói một từ mới nhé.",
            _scene(asset_base, expression="teaching", overlay_file=_OVERLAY_TEACH_FILE,
                   primary_word="barn", glyph="🏠"),
            dwell_sec=dwell_sec,
        ),
        _step(
            "s2", "focus",
            "Đây là cái kho — barn. Con nhìn ngôi nhà đỏ trên cánh đồng xanh nhé!",
            _scene(asset_base, expression="teaching", overlay_file=_OVERLAY_TEACH_FILE,
                   primary_word="barn", glyph="🏠"),
            dwell_sec=dwell_sec,
        ),
        _step(
            "s3", "repeat",
            "Bây giờ con hãy nói theo mình nào: barn!",
            _scene(asset_base, expression="thinking", overlay_file=_OVERLAY_TEACH_FILE,
                   primary_word="barn", glyph="🗣️"),
            completion_class="interactive",
            expected_responses=["barn"],
            retry_prompt="Con thử nói lại nhé: barn.",
            response_timeout_sec=30.0,
            max_no_answer_attempts=3,
        ),
        _step(
            "s4", "recall",
            "Giỏi lắm. Con thấy gì trong hình? Con thử nói: barn.",
            _scene(asset_base, expression="thinking", overlay_file=_OVERLAY_TEACH_FILE,
                   primary_word="barn", glyph="🏠"),
            completion_class="interactive",
            expected_responses=["barn"],
            retry_prompt="Con thử nói lại nhé: barn.",
            response_timeout_sec=30.0,
            max_no_answer_attempts=3,
        ),
        _step(
            "s5", "celebrate",
            "Tuyệt vời! Con vừa nói được từ barn và đã hoàn thành bài học mẫu. Hoan hô con!",
            _scene(asset_base, expression="celebrating", overlay_file=_OVERLAY_CELEBRATE_FILE,
                   primary_word="barn", glyph="🎉"),
            dwell_sec=dwell_sec,
        ),
    ]
    manifest = {
        "manifestVersion": "teebot-lesson-renderer.v1",
        "lessonId": INTERACTIVE_SAMPLE_LESSON_ID,
        "lessonVersion": 1,
        "profile": "espTft",
        "assets": [],
        "steps": steps,
    }
    _assert_renderable(manifest, allow_interactive=True)
    return manifest


def _assert_renderable(manifest: Dict[str, Any], *, allow_interactive: bool = False) -> None:
    """Module-load guard: the passthrough cache returns each src unchanged, so the
    runtime's blank-src invalidation only passes when EVERY step carries non-blank
    background/teachingObject/robotOverlay srcs — otherwise the firmware (and the
    runtime) reject the frame as LESSON_FRAME_INVALID.

    The all-passive variant additionally requires every step be passive (auto-advance to
    completion with no backend). The interactive "say the word" variant permits
    ``completionClass == "interactive"`` steps (each waits on the child's spoken answer
    via the Live child-response window) but still requires non-blank layer srcs."""
    steps = manifest.get("steps") or []
    if not steps:
        raise ValueError("sample manifest has no steps")
    allowed = {"passive", "interactive"} if allow_interactive else {"passive"}
    for step in steps:
        if step.get("completionClass") not in allowed:
            raise ValueError(
                f"sample step {step.get('id')} completionClass "
                f"{step.get('completionClass')!r} not in {sorted(allowed)}"
            )
        scene = step.get("scene") or {}
        srcs = [
            ((scene.get("backgroundScene") or {}).get("poster") or {}).get("src"),
            ((scene.get("teachingObject") or {}).get("asset") or {}).get("src"),
            ((scene.get("robotOverlay") or {}).get("asset") or {}).get("src"),
        ]
        if any(not (isinstance(s, str) and s.strip()) for s in srcs):
            raise ValueError(f"sample step {step.get('id')} has a blank layer src")


class SampleAssetCache:
    """Passthrough cache for the self-contained sample: no download, no sha256 verify.
    Implements exactly the surface the non-sd LessonRuntime happy path touches
    (preload/synthesize_preload_status/assert_profile_renderable/public_url_for_source/
    aclose/preload_timeout_sec) plus harmless sd-path attrs."""

    def __init__(self, preload_timeout_sec: float = 30.0) -> None:
        self.preload_timeout_sec = float(preload_timeout_sec)
        self.cache_key = "sample"
        self.asset_pack_local_root = "sd://tbot/lesson-assets"

    async def preload(self) -> bool:
        return True

    def synthesize_preload_status(self, assignment_version: int) -> Dict[str, Any]:
        return {"ready": True, "assignmentVersion": assignment_version, "assets": []}

    def assert_profile_renderable(self) -> None:
        return None

    def public_url_for_source(self, source: str) -> Optional[str]:
        # Pass the authored src straight to the firmware (it fetches over HTTP, or
        # degrades to its caption fallback). Returning the source unchanged keeps the
        # layer src non-blank so the structural gates pass.
        return source

    async def aclose(self) -> None:
        return None


class NoOpLessonForwarder:
    """Drops lesson progress events — the sample has no backend assignment to report to."""

    def enqueue(self, batch: Dict[str, Any]) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def replay_pending_terminal_event(self) -> bool:
        return False


def _sample_asset_base(conn: Any) -> str:
    config = getattr(conn, "config", {}) or {}
    lesson_cfg = config.get("lesson", {}) if isinstance(config, dict) else {}
    return str(lesson_cfg.get("sample_asset_base_url") or "").strip()


def _sample_step_dwell_sec(conn: Any) -> float:
    config = getattr(conn, "config", {}) or {}
    lesson_cfg = config.get("lesson", {}) if isinstance(config, dict) else {}
    raw = lesson_cfg.get("sample_step_dwell_sec")
    if raw is None:
        return DEFAULT_SAMPLE_STEP_DWELL_SEC
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_SAMPLE_STEP_DWELL_SEC
    return value if value >= 0 else DEFAULT_SAMPLE_STEP_DWELL_SEC


def _sample_mode(conn: Any) -> str:
    """``interactive`` (default speaking drill) or ``passive`` (all-passive narration demo).
    Sourced from ``lesson.sample_mode`` (env ``LESSON_SAMPLE_MODE``)."""
    config = getattr(conn, "config", {}) or {}
    lesson_cfg = config.get("lesson", {}) if isinstance(config, dict) else {}
    mode = str(lesson_cfg.get("sample_mode") or "interactive").strip().lower()
    return "passive" if mode == "passive" else "interactive"


def _sd_pack_enabled(conn: Any) -> bool:
    config = getattr(conn, "config", {}) or {}
    lesson_cfg = config.get("lesson", {}) if isinstance(config, dict) else {}
    mode = str(lesson_cfg.get("asset_delivery_mode") or "").strip().lower()
    return mode == "sd_pack" or lesson_cfg.get("sd_asset_pack_enabled") is True


def _set_status(conn: Any, code: str, message: str = "") -> None:
    try:
        conn.lesson_start_status = {"code": code, "message": message}
    except Exception:
        pass

def _is_active_sample_runtime(runtime: Any, lesson_id: str) -> bool:
    if runtime is None:
        return False
    if getattr(runtime, "assignment_id", None) != SAMPLE_ASSIGNMENT_ID:
        return False
    if getattr(runtime, "lesson_id", None) != lesson_id:
        return False
    return getattr(runtime, "state", None) not in {"FAILED", "PAUSED", "COMPLETED"}


async def start_sample_lesson(conn: Any) -> Optional[Any]:
    """Start the built-in sample lesson on the live connection, ignoring any backend
    assignment. Serialized against the connect-time assignment pull via the SAME
    per-connection ``_lesson_pull_lock`` maybe_start_lesson_on_connect uses, so a
    connect pull and a spoken sample start can never create two runtimes / emit
    duplicate lesson_prepare. The lazy-init is atomic under asyncio (no await between
    the getattr and the assignment), so racers share one lock."""
    lock = getattr(conn, "_lesson_pull_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        conn._lesson_pull_lock = lock
    async with lock:
        return await _start_sample_lesson_impl(conn)


async def _start_sample_lesson_impl(conn: Any) -> Optional[Any]:
    """Best-effort: every failure is swallowed/logged so the voice path is never
    broken. Mirrors the runtime-wiring tail of maybe_start_lesson_on_connect."""
    from core.lesson.runtime import LessonRuntime

    logger = getattr(conn, "logger", None)

    def _log(level: str, message: str) -> None:
        if logger is None:
            return
        try:
            getattr(logger.bind(tag=TAG), level)(message)
        except Exception:
            pass

    _set_status(conn, "CHECKING_ASSIGNMENT")

    if _sd_pack_enabled(conn):
        _set_status(conn, "SAMPLE_SD_PACK_UNSUPPORTED",
                    "Bài học mẫu chỉ hỗ trợ tải ảnh trực tiếp, không dùng thẻ nhớ.")
        _log("warning", "sample lesson skipped: sd_pack asset delivery is not supported")
        return None

    asset_base = _sample_asset_base(conn)
    interactive = _sample_mode(conn) == "interactive"
    if interactive:
        manifest = build_interactive_sample_manifest(asset_base, dwell_sec=_sample_step_dwell_sec(conn))
        lesson_id = INTERACTIVE_SAMPLE_LESSON_ID
    else:
        manifest = build_sample_manifest(asset_base, dwell_sec=_sample_step_dwell_sec(conn))
        lesson_id = SAMPLE_LESSON_ID
    _log("info", f"sample lesson mode={'interactive' if interactive else 'passive'} lessonId={lesson_id}")

    existing = getattr(conn, "lesson_runtime", None)
    if _is_active_sample_runtime(existing, lesson_id):
        _set_status(conn, "STARTED")
        _log("info", f"sample lesson already active; reusing lessonId={lesson_id}")
        return existing

    assignment = {
        "assignmentId": SAMPLE_ASSIGNMENT_ID,
        "assignmentVersion": 1,
        "lessonId": lesson_id,
        "lessonVersion": 1,
        "profile": "espTft",
        "sessionId": getattr(conn, "session_id", None),
        "manifestChecksum": "sample",
    }

    runtime = LessonRuntime(
        conn,
        assignment=assignment,
        manifest=manifest,
        asset_cache=SampleAssetCache(),
        forwarder=NoOpLessonForwarder(),
        manifest_checksum="sample",
        # Reuse the connection's CP-8 voice-latency-during-preload alarm if one was
        # already created (by an earlier assignment pull) so the auto-disable still
        # brackets the sample's preload window; None when no alarm exists yet.
        alarm=getattr(conn, "lesson_voice_alarm", None),
    )

    # Supersede + tear down any prior runtime so its forwarder/asset client never leak
    # (mirrors maybe_start_lesson_on_connect's prior-runtime guard).
    prior = getattr(conn, "lesson_runtime", None)
    if prior is not None and prior is not runtime:
        try:
            await prior.close()
        except Exception as exc:  # pragma: no cover - teardown is best-effort
            _log("warning", f"prior lesson runtime teardown failed: {type(exc).__name__}")
    conn.lesson_runtime = runtime

    try:
        enter_lesson = getattr(conn, "enter_lesson_mode", None)
        if callable(enter_lesson):
            _log("info", "sample lesson entering lesson mode")
            await enter_lesson(reason="sample_lesson_start")
            _log("info", "sample lesson entered lesson mode")
        _log("info", "sample lesson runtime start begin")
        await runtime.start()
        _log("info", "sample lesson runtime start returned")
        if getattr(runtime, "state", None) == "FAILED":
            _set_status(conn, "START_REFUSED", "Robot chưa hiển thị được bài học mẫu.")
            if getattr(conn, "lesson_runtime", None) is runtime:
                conn.lesson_runtime = None
            try:
                await runtime.close()
            except Exception as exc:  # pragma: no cover - teardown is best-effort
                _log("warning", f"failed sample runtime teardown failed: {type(exc).__name__}")
            return None
        _set_status(conn, "STARTED")
        _log("info", "sample lesson started on live connection")
        return runtime
    except Exception as exc:  # pragma: no cover - lesson layer must never break voice
        _set_status(conn, "START_REFUSED", "Robot chưa hiển thị được bài học mẫu.")
        _log("warning", f"sample lesson start failed: {type(exc).__name__}: {exc}")
        release = getattr(conn, "release_lesson_mode", None)
        if callable(release):
            try:
                await release(reason="sample_lesson_refused")
            except Exception:
                pass
        if getattr(conn, "lesson_runtime", None) is runtime:
            conn.lesson_runtime = None
        try:
            await runtime.close()
        except Exception:
            pass
        return None
