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
download/verify gate the real ``AssetCache`` enforces. In normal mode the three layer
image ``src`` values are passed to the firmware as canonical seed paths, optionally
prefixed with ``lesson.sample_asset_base_url`` or ``lesson.asset_origin_base``. In
``asset_delivery_mode == "sd_pack"`` those source paths are rewritten to the fixed
sample SD directory populated by
``self.lesson_assets.sync_sample_to_sd``, so lesson rendering is local and stable.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any, Dict, List, Optional

TAG = "SampleLesson"

SAMPLE_LESSON_ID = "sample-barn-say-it"
SAMPLE_ASSIGNMENT_ID = "sample-lesson"

# Canonical seed asset paths reused from the "barn say-it" theme.
_POSTER_FILE = "assets/background/barn-round-field-poster.jpg"
_OBJECT_FILE = "assets/objects/barn.png"
_OVERLAY_TEACH_FILE = "assets/robot/poses/bright-teach.png"
_OVERLAY_LISTEN_FILE = "assets/robot/poses/bright-listening.png"
_OVERLAY_THINK_FILE = "assets/robot/poses/bright-thinking.png"
_OVERLAY_CELEBRATE_FILE = "assets/robot/poses/bright-celebrate.png"
_SAMPLE_SD_CACHE_KEY = "sample"
_SAMPLE_SD_ROOT = "sd://tbot/lesson-assets/sample-barn"
_SAMPLE_ASSET_RECORDS = [
    {
        "key": "backgroundScene.poster",
        "path": _POSTER_FILE,
        "sha256": "d5cdaba9f9086ef56a5f41c5fddf2e32b91ecfe141cc346f3221c7b221a3a357",
        "size": 18482,
        "mediaType": "image/jpeg",
        "critical": True,
        "layer": "backgroundScene",
        "role": "poster",
    },
    {
        "key": "teachingObject.sample",
        "path": _OBJECT_FILE,
        "sha256": "bf3d88d17867f02872e3e6aff31b5d4d0a94977a5efa4214b7e831122938511b",
        "size": 42107,
        "mediaType": "image/png",
        "critical": True,
        "layer": "teachingObject",
        "role": "primarySubject",
    },
    {
        "key": "robotOverlay.teaching",
        "path": _OVERLAY_TEACH_FILE,
        "sha256": "576d86a75686f6eab606295529593da14b01554e21e0601c8f29aedbc1ba4965",
        "size": 45408,
        "mediaType": "image/png",
        "critical": False,
        "layer": "robotOverlay",
        "role": "pose",
    },
    {
        "key": "robotOverlay.listening",
        "path": _OVERLAY_LISTEN_FILE,
        "sha256": "572a61f140eca17968a85f61704967d03a1a3311222335e32b94b1ab370e2419",
        "size": 43615,
        "mediaType": "image/png",
        "critical": False,
        "layer": "robotOverlay",
        "role": "pose",
    },
    {
        "key": "robotOverlay.thinking",
        "path": _OVERLAY_THINK_FILE,
        "sha256": "3d3d3c3a7c6993ad2346e11cfee72a15750d0b5f35642b8d949902a8745352c8",
        "size": 38733,
        "mediaType": "image/png",
        "critical": False,
        "layer": "robotOverlay",
        "role": "pose",
    },
    {
        "key": "robotOverlay.celebrating",
        "path": _OVERLAY_CELEBRATE_FILE,
        "sha256": "8392fb31c53030147d27fbd96c5b2dd1a4e5c33efd35f8727bee6dabda62605d",
        "size": 44193,
        "mediaType": "image/png",
        "critical": False,
        "layer": "robotOverlay",
        "role": "pose",
    },
]
_SAMPLE_ASSET_BY_BASENAME = {
    record["path"].rsplit("/", 1)[-1]: record for record in _SAMPLE_ASSET_RECORDS
}


def _resolve_src(filename: str, asset_base: str) -> str:
    base = str(asset_base or "").strip().rstrip("/")
    return f"{base}/{filename}" if base else filename


def _scene(asset_base: str, *, expression: str, overlay_file: str, primary_word: str,
           glyph: str, robot_state: str = "talking", pose: str = "teach") -> Dict[str, Any]:
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
            "altCaption": "Hình cái kho màu đỏ trên cánh đồng xanh",
        },
        "teachingObject": {
            "primaryWord": primary_word,
            "supportWords": ["cái kho", "farm", "mái đỏ"],
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
            "robotState": robot_state,
            "pose": pose,
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


DEFAULT_SAMPLE_STEP_DWELL_SEC = 0.3
DEFAULT_SAMPLE_STEP_TIMEOUT_SEC = 75.0


def _step(step_id: str, step_type: str, prompt: str, scene: Dict[str, Any],
          timeout_sec: float = DEFAULT_SAMPLE_STEP_TIMEOUT_SEC,
          dwell_sec: float = DEFAULT_SAMPLE_STEP_DWELL_SEC,
          completion_class: str = "passive",
          expected_responses: Optional[List[str]] = None,
          retry_prompt: Optional[str] = None,
          success_prompt: Optional[str] = None,
          interaction_prompts: Optional[Dict[str, str]] = None,
          story_beat: Optional[Dict[str, Any]] = None,
          response_timeout_sec: Optional[float] = None,
          max_no_answer_attempts: Optional[int] = None) -> Dict[str, Any]:
    # completionClass is the AUTHORITATIVE classifier. A PASSIVE step auto-advances on
    # its render ack (no child interaction). An INTERACTIVE step instead opens a
    # child-response window (provider.open_lesson_child_response_window) and waits for the
    # child's spoken answer (runtime.on_child_response via Live STT) before advancing.
    # dwellSec only applies to passive steps: it keeps the rendered scene on screen for N
    # seconds before auto-advancing so the child can see/hear it (runtime _passive_dwell_sec).
    try:
        timeout_value = float(timeout_sec)
        if not math.isfinite(timeout_value) or timeout_value <= 0:
            timeout_value = DEFAULT_SAMPLE_STEP_TIMEOUT_SEC
    except (TypeError, ValueError):
        timeout_value = DEFAULT_SAMPLE_STEP_TIMEOUT_SEC
    step: Dict[str, Any] = {
        "id": step_id,
        "type": step_type,
        "completionClass": completion_class,
        "prompt": prompt,
        "audio": {"via": "tts"},
        "timeoutSec": timeout_value,
        "scene": scene,
    }
    if completion_class == "passive" and dwell_sec:
        try:
            dwell_value = float(dwell_sec)
            if math.isfinite(dwell_value) and dwell_value > 0:
                step["dwellSec"] = dwell_value
        except (TypeError, ValueError):
            pass
    if expected_responses:
        step["expectedResponses"] = [str(item).strip() for item in expected_responses if str(item).strip()]
    if retry_prompt and retry_prompt.strip():
        step["retryPrompt"] = retry_prompt.strip()
    if success_prompt and success_prompt.strip():
        step["successPrompt"] = success_prompt.strip()
    if interaction_prompts:
        prompts = {
            str(key): str(value).strip()
            for key, value in interaction_prompts.items()
            if str(key).strip() and str(value).strip()
        }
        if prompts:
            step["interactionPrompts"] = prompts
    if story_beat:
        ask = str(story_beat.get("ask") or "").strip()
        if ask:
            step["storyBeat"] = {"ask": ask, "waitForChild": story_beat.get("waitForChild") is True}
    if response_timeout_sec is not None:
        try:
            response_timeout_value = float(response_timeout_sec)
            if math.isfinite(response_timeout_value) and response_timeout_value > 0:
                step["responseTimeoutSec"] = response_timeout_value
        except (TypeError, ValueError):
            pass
    if max_no_answer_attempts is not None:
        try:
            if int(max_no_answer_attempts) > 0:
                step["maxNoAnswerAttempts"] = int(max_no_answer_attempts)
        except (TypeError, ValueError, OverflowError):
            pass
    return step


def build_sample_manifest(asset_base: str = "", dwell_sec: float = DEFAULT_SAMPLE_STEP_DWELL_SEC) -> Dict[str, Any]:
    """A short all-passive 'barn say-it' demo: greeting → focus → review → celebrate.
    Each step carries the full 3-layer scene + a spoken prompt; the robot narrates and
    auto-advances, then the celebrate step's happy ending hands off to finish_lesson_mode."""
    steps: List[Dict[str, Any]] = [
        _step(
            "s1", "greeting",
            "Xin chào! Mình là TeeBot. Hôm nay mình nghe chậm, nói rõ cùng nhau nhé!",
            _scene(asset_base, expression="teaching", overlay_file=_OVERLAY_TEACH_FILE,
                   primary_word="barn", glyph="🏠"),
            dwell_sec=dwell_sec,
        ),
        _step(
            "s2", "focus",
            "Đây là cái kho — barn. Nghe chậm: barn. barn. Con nhìn nhà đỏ nhé!",
            _scene(asset_base, expression="teaching", overlay_file=_OVERLAY_TEACH_FILE,
                   primary_word="barn", glyph="🏠"),
            dwell_sec=dwell_sec,
        ),
        _step(
            "s3", "review",
            "Giỏi lắm! Từ mới là barn. Con thì thầm barn theo mình nhé!",
            _scene(asset_base, expression="thinking", overlay_file=_OVERLAY_THINK_FILE,
                   primary_word="barn", glyph="🏠"),
            dwell_sec=dwell_sec,
        ),
        _step(
            "s4", "celebrate",
            "Tuyệt vời! Con đã nghe và nói barn rõ hơn. Hoàn thành bài học mẫu!",
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
    → recall (interactive) → complete.

    Pedagogy (age ~3–6, low pressure):
    1. Warm greeting sets the listen-first rule.
    2. Focus double-models the target word slowly so the child hears the shape twice.
    3. Repeat: TeeBot models once more, then invites the child to shadow.
    4. Recall: child retrieves the English name without the word in the prompt.

    Child-answer feedback is NOT canned sample lines. After each spoken attempt the
    runtime builds short adaptive coaching from the child's intent (help, L1 label,
    near-miss, wrong, correct) and always remodels the target word — never raw-echoes
    free-form child speech. Mid/final ``successPrompt`` only covers ceremony
    (advance / lesson complete).

    The interactive steps narrate the question, open a child-response window over Google
    Live (the provider waits for narration TTS to finish, then opens the mic), and only
    advance once the child says the target word (``runtime.on_child_response`` via the
    recognised Live transcript). Wrong attempts stay on the same step and get adaptive
    coaching so a child with no backend assignment can still practise vocabulary and hear
    the happy ending after saying the word. The final completion announcement is a Live
    text successPrompt on the last interactive step rather than a separate passive
    ``celebrate`` render frame, keeping production devices out of the crash-prone final
    render path while preserving the spoken celebration."""
    steps: List[Dict[str, Any]] = [
        _step(
            "s1", "greeting",
            "Chào con! Nhìn hình, nghe TeeBot nói chậm, rồi nói khi mình mời nhé.",
            _scene(asset_base, expression="teaching", overlay_file=_OVERLAY_TEACH_FILE,
                   primary_word="barn", glyph="🏠"),
            dwell_sec=dwell_sec,
        ),
        _step(
            "s2", "focus",
            "Đây là cái kho. Nghe chậm: barn. barn. Con nhìn nhà đỏ nhé!",
            _scene(asset_base, expression="teaching", overlay_file=_OVERLAY_TEACH_FILE,
                   primary_word="barn", glyph="🏠"),
            dwell_sec=dwell_sec,
        ),
        _step(
            "s3", "repeat",
            "Mình nói chậm: barn. Con nói theo mình: barn!",
            _scene(asset_base, expression="listening", overlay_file=_OVERLAY_LISTEN_FILE,
                   primary_word="barn", glyph="🗣️", robot_state="listening", pose="listen"),
            completion_class="interactive",
            expected_responses=["barn"],
            # Ceremony only — wrong/help/L1 feedback is runtime-adaptive, not these lines.
            success_prompt="Giỏi lắm! Con nói được barn. Tiếp theo nhé!",
            story_beat={
                "ask": "Mình nói chậm: barn. Con nói theo mình: barn!",
                "waitForChild": True,
            },
            response_timeout_sec=10.0,
            max_no_answer_attempts=1,
        ),
        _step(
            "s4", "recall",
            "Giỏi lắm! Con thấy gì? Nói rõ tên tiếng Anh của cái kho.",
            _scene(asset_base, expression="listening", overlay_file=_OVERLAY_LISTEN_FILE,
                   primary_word="barn", glyph="🏠", robot_state="listening", pose="listen"),
            completion_class="interactive",
            expected_responses=["barn"],
            story_beat={
                "ask": "Giỏi lắm! Con thấy gì? Nói rõ tên tiếng Anh của cái kho.",
                "waitForChild": True,
            },
            success_prompt="Hay quá! Con nói barn rất rõ và hoàn thành bài học mẫu.",
            response_timeout_sec=10.0,
            max_no_answer_attempts=1,
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

    def __init__(self, preload_timeout_sec: float = 30.0, *, sd_pack: bool = False, asset_base: str = "") -> None:
        try:
            timeout = float(preload_timeout_sec)
        except (TypeError, ValueError):
            timeout = 30.0
        self.preload_timeout_sec = timeout if math.isfinite(timeout) and timeout > 0 else 30.0
        self.cache_key = _SAMPLE_SD_CACHE_KEY
        self.asset_pack_local_root = "sd://tbot/lesson-assets"
        self.sd_pack = bool(sd_pack)
        self.asset_base = str(asset_base or "").strip().rstrip("/")

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

    def local_pack_url_for_source(self, source: str) -> Optional[str]:
        if not self.sd_pack or not source:
            return None
        filename = str(source).strip().rsplit("/", 1)[-1]
        if filename not in _SAMPLE_ASSET_BY_BASENAME:
            return None
        return f"{_SAMPLE_SD_ROOT}/{filename}"

    def asset_pack_manifest(
        self,
        *,
        assignment_version: int,
        lesson_id: str,
        lesson_version: int,
        manifest_checksum: str,
    ) -> Dict[str, Any]:
        def _basename(path: str) -> str:
            return path.rsplit("/", 1)[-1]

        return {
            "assignmentVersion": assignment_version,
            "lessonId": lesson_id,
            "lessonVersion": lesson_version,
            "manifestChecksum": manifest_checksum,
            "cacheKey": self.cache_key,
            "localRoot": _SAMPLE_SD_ROOT,
            "ready": True,
            "assets": [
                {
                    "key": record["key"],
                    "path": _basename(record["path"]),
                    "url": _resolve_src(record["path"], self.asset_base),
                    "sha256": record["sha256"],
                    "size": record["size"],
                    "mediaType": record["mediaType"],
                    "critical": record["critical"],
                    "layer": record["layer"],
                    "role": record["role"],
                    "state": "READY",
                    "checksumOk": True,
                    "localPath": f"{_SAMPLE_SD_ROOT}/{_basename(record['path'])}",
                }
                for record in _SAMPLE_ASSET_RECORDS
            ],
        }

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
    lesson_cfg = _lesson_config(conn)
    return str(
        lesson_cfg.get("sample_asset_base_url")
        or lesson_cfg.get("asset_origin_base")
        or ""
    ).strip().rstrip("/")


def _lesson_config(conn: Any) -> Dict[str, Any]:
    config = getattr(conn, "config", {}) or {}
    lesson_cfg = config.get("lesson", {}) if isinstance(config, dict) else {}
    return lesson_cfg if isinstance(lesson_cfg, dict) else {}

def _sample_step_dwell_sec(conn: Any) -> float:
    lesson_cfg = _lesson_config(conn)
    raw = lesson_cfg.get("sample_step_dwell_sec")
    if raw is None:
        return DEFAULT_SAMPLE_STEP_DWELL_SEC
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_SAMPLE_STEP_DWELL_SEC
    return value if math.isfinite(value) and value >= 0 else DEFAULT_SAMPLE_STEP_DWELL_SEC


def _sample_mode(conn: Any) -> str:
    """``interactive`` (default speaking drill) or ``passive`` (all-passive narration demo).
    Sourced from ``lesson.sample_mode`` (env ``LESSON_SAMPLE_MODE``)."""
    lesson_cfg = _lesson_config(conn)
    mode = str(lesson_cfg.get("sample_mode") or "interactive").strip().lower()
    return "passive" if mode == "passive" else "interactive"


def _sd_pack_enabled(conn: Any) -> bool:
    lesson_cfg = _lesson_config(conn)
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

    sd_pack = _sd_pack_enabled(conn)
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
        _log("info", f"sample lesson restart requested; replacing active lessonId={lesson_id}")

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
        asset_cache=SampleAssetCache(sd_pack=sd_pack, asset_base=asset_base),
        forwarder=NoOpLessonForwarder(),
        manifest_checksum="sample",
        # Reuse the connection's CP-8 voice-latency-during-preload alarm if one was
        # already created (by an earlier assignment pull) so the auto-disable still
        # brackets the sample's preload window; None when no alarm exists yet.
        alarm=getattr(conn, "lesson_voice_alarm", None),
        # Demo-friendly: a silent spectator child through an interactive step must not
        # end the demo on a sad abandon. TeeBot models the word and advances to the
        # happy ending. Only the interactive sample opts in (real lessons abandon so the
        # backend learns the child disengaged).
        graceful_inactivity_finish=interactive,
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
        prepare_voice = getattr(
            getattr(conn, "voice_provider", None),
            "prepare_for_sample_lesson",
            None,
        )
        if callable(prepare_voice):
            try:
                _log("info", "sample lesson preparing voice provider")
                prepared = prepare_voice()
                if asyncio.iscoroutine(prepared) or hasattr(prepared, "__await__"):
                    await prepared
            except Exception as exc:
                _log("warning", f"sample lesson voice prepare failed: {type(exc).__name__}")
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
