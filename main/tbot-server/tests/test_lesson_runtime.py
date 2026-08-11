"""S8 / S9 — LessonRuntime state machine + the single result->outcome rename.

Drives ``core.lesson.runtime.LessonRuntime`` against the FROZEN S2 wire fixture
``fixtures/lesson-protocol.v1.json`` (byte-consistent prepare/start/step/stop) and
exercises the P0 ack-correlation contract (body.acks, never envelope.sequence /
ackFor), the ready gate, the capability + protocol-version gates, STEP_TIMEOUT vs
PROTOCOL_SEQUENCE_ERROR distinctness, and the dedicated progress-forward path.

Async tests use ``unittest.IsolatedAsyncioTestCase`` (this repo does NOT use
pytest-asyncio markers). The REAL ``config.manage_api_client`` is loaded via an
``importlib`` spec because ``tests/conftest.py`` installs a stub for it.
"""

import asyncio
import copy
import importlib.util
import json
import os
import re
import shutil
import tempfile
import unittest
import uuid
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

from core.activity_lease import ActivityLeaseCoordinator, ActivityOperation, ExclusiveDisposition
from core.lesson.asset_cache import AssetCache, FAILED, READY
from core.lesson.errors import LessonError
from core.lesson.runtime import (
    _child_response_coaching_prompt,
    _child_response_success_prompt,
    _classify_child_response_intent,
    _manifest_steps_log_summary,
)
from core.lesson.sample import SampleAssetCache
from core.lesson.sd_pack_sync import request_sd_pack_sync


# ── frozen wire fixture ─────────────────────────────────────────────────────────

def _robot_repo_candidates():
    candidates = []
    configured = os.environ.get("TBOT_ROBOT_REPO")
    if configured:
        candidates.append(os.path.abspath(configured))
    worktree_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    candidates.extend([worktree_root, os.path.dirname(worktree_root)])
    git_file = os.path.join(worktree_root, ".git")
    if os.path.isfile(git_file):
        with open(git_file) as fh:
            gitdir = fh.read().strip().removeprefix("gitdir:").strip()
        marker = os.sep + "esp32-server" + os.sep + ".git" + os.sep
        if marker in gitdir:
            esp_repo = gitdir.split(marker, 1)[0] + os.sep + "esp32-server"
            candidates.append(os.path.dirname(esp_repo))
    return list(dict.fromkeys(candidates))


def _resolve_robot_fixture(relative_path):
    tried = [os.path.join(repo, relative_path) for repo in _robot_repo_candidates()]
    resolved = next((path for path in tried if os.path.isfile(path)), None)
    if resolved is None:
        # The fixture lives in the sibling robot/docs checkout. A single-repo CI
        # checkout (ESP-Server alone) won't have it — skip cleanly rather than
        # hard-failing collection. Set TBOT_ROBOT_REPO to run these locally.
        import pytest

        pytest.skip(
            "robot fixture unavailable (single-repo checkout); searched: "
            + ", ".join(tried),
            allow_module_level=True,
        )
    return resolved


FIXTURE_PATH = _resolve_robot_fixture(
    os.path.join("docs", "stories", "US-006-learning-course-runtime", "fixtures", "lesson-protocol.v1.json")
)

FIX = json.load(open(FIXTURE_PATH))

SEED_LESSON_PATH = _resolve_robot_fixture(
    os.path.join("TBOT-Firmware", "lesson", "lesson.json")
)

SEED_LESSON = json.load(open(SEED_LESSON_PATH))["lesson"]

_BACKEND_CANONICAL_RELATIVE_PATH = os.path.join(
    "scripts", "seed", "076_canonical-manifest.espTft.json"
)
_LEGACY_BACKEND_REPO_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "..",
    "..",
    "..",
    "tbot-backend",
))


def _backend_canonical_manifest_candidates():
    explicit_manifest = os.environ.get("TBOT_BACKEND_CANONICAL_MANIFEST")
    configured_repo = os.environ.get("TBOT_BACKEND_REPO")
    candidates = []
    if explicit_manifest:
        candidates.append(os.path.abspath(explicit_manifest))
    if configured_repo:
        candidates.append(os.path.join(os.path.abspath(configured_repo), _BACKEND_CANONICAL_RELATIVE_PATH))
    backend_roots = []
    worktree_name = os.path.basename(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    )
    for robot_repo in _robot_repo_candidates():
        if os.path.basename(robot_repo) != "robot":
            continue
        workspace = os.path.dirname(robot_repo)
        backend_roots.extend(
            [
                os.path.join(workspace, ".worktrees", f"backend-{worktree_name}"),
                os.path.join(workspace, "tbot-backend"),
            ]
        )
    backend_roots.append(_LEGACY_BACKEND_REPO_PATH)
    for backend_root in backend_roots:
        candidates.extend(
            [
                os.path.join(backend_root, _BACKEND_CANONICAL_RELATIVE_PATH),
                os.path.join(
                    backend_root,
                    "production-lesson-studio",
                    _BACKEND_CANONICAL_RELATIVE_PATH,
                ),
            ]
        )
    return list(dict.fromkeys(candidates))


def _resolve_backend_canonical_manifest_path():
    return next(
        (path for path in _backend_canonical_manifest_candidates() if os.path.isfile(path)),
        None,
    )


BACKEND_CANONICAL_MANIFEST_PATH = _resolve_backend_canonical_manifest_path()

GUIDED_SPEAKING_FORBIDDEN_PROMPT_TERMS = (
    "watch my mouth",
    "make a strong",
    "ending sound",
    "final consonant",
    "pronunciation",
    "score",
    "evaluate",
    "assess",
    "correct",
    "clearly",
)

GUIDED_SPEAKING_FORBIDDEN_EVENT_KEYS = {
    "score",
    # All five hard-constrained pronunciation/phoneme keys (stored normalized:
    # lower-cased, hyphens -> underscores) must be rejected outright, not only
    # the *score-suffixed* variants. A bare ``pronunciation`` blob, a
    # ``phonemeAssessment`` payload, or ``phoneme_assessment`` is exactly the
    # immediate-evaluation leak the guided-speaking contract forbids.
    "pronunciation",
    "pronunciationscore",
    "pronunciation_score",
    "pronunciationassessment",
    "pronunciation_assessment",
    "phoneme",
    "phonemescore",
    "phoneme_score",
    "phonemeassessment",
    "phoneme_assessment",
    "accuracy",
    "correction",
    "correctedtext",
    "corrected_text",
    "verdict",
}

def _assert_guided_speaking_practice_prompt(testcase, prompt: str) -> None:
    lowered = prompt.lower()
    offenders = [term for term in GUIDED_SPEAKING_FORBIDDEN_PROMPT_TERMS if term in lowered]
    testcase.assertEqual(
        offenders,
        [],
        f"early speaking-practice prompts must guide and wait, not assess pronunciation: {prompt!r}",
    )


def _assert_no_inline_media_payload(testcase, value, *, path="frame") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered_key = str(key).lower()
            testcase.assertNotIn(
                lowered_key,
                {"bytes", "bytearray", "base64", "payload", "imagebytes", "imagedata"},
                f"lesson wire payload must reference media by URL/path, not inline it at {path}.{key}",
            )
            _assert_no_inline_media_payload(testcase, child, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_inline_media_payload(testcase, child, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.strip().lower()
        testcase.assertFalse(lowered.startswith("data:"), f"inline data URI at {path}")
        testcase.assertLessEqual(len(value), 2048, f"oversized inline media-like string at {path}")

class ChildResponseIntentClassifierTest(unittest.TestCase):
    def test_vietnamese_asr_alias_for_barn_is_accepted(self):
        self.assertEqual(
            _classify_child_response_intent("bóng bóng bóng", ["barn"]),
            "correct",
        )
        self.assertEqual(
            _classify_child_response_intent("con nói bóng", ["barn"]),
            "correct",
        )
        self.assertEqual(
            _classify_child_response_intent(
                "bâng bâng bâng bâng bâng bâng", ["barn"]
            ),
            "correct",
        )
        self.assertEqual(
            _classify_child_response_intent("nóng nóng nóng", ["barn"]),
            "correct",
        )
        self.assertEqual(
            _classify_child_response_intent("darn darn darn", ["barn"]),
            "correct",
        )
        self.assertEqual(
            _classify_child_response_intent("Bòn bòn bon", ["barn"]),
            "correct",
        )

    def test_near_child_pronunciation_for_short_word_is_accepted(self):
        for response in ("con nói ban", "bar", "born", "burn"):
            self.assertEqual(
                _classify_child_response_intent(response, ["barn"]),
                "correct",
                response,
            )
        for response in ("cat", "car", "farm"):
            self.assertEqual(
                _classify_child_response_intent(response, ["barn"]),
                "wrong",
                response,
            )

    def test_vietnamese_object_word_is_not_confused_with_frustration_after_accent_stripping(self):
        self.assertEqual(
            _classify_child_response_intent("con thấy cái kho", ["barn"]),
            "vietnamese_object",
        )
        self.assertEqual(
            _classify_child_response_intent("cái kho", ["barn"]),
            "vietnamese_object",
        )
        self.assertEqual(
            _classify_child_response_intent("khó quá", ["barn"]),
            "unknown_or_frustrated",
        )

    def test_near_miss_pronunciation_is_coached_not_accepted(self):
        # edit-distance 2, same first letter → coach, do not advance
        self.assertEqual(
            _classify_child_response_intent("ball", ["barn"]),
            "near_miss",
        )
        self.assertEqual(
            _classify_child_response_intent("band", ["barn"]),
            "near_miss",
        )
        # edit-distance 1 stays accepted as correct (low-pressure aliases)
        self.assertEqual(
            _classify_child_response_intent("bark", ["barn"]),
            "correct",
        )
        # different first letter is wrong, not a near-miss
        self.assertEqual(
            _classify_child_response_intent("farm", ["barn"]),
            "wrong",
        )

    def test_adaptive_vocab_coaching_reacts_to_child_intent_not_canned_sample(self):
        step = {
            "expectedResponses": ["barn"],
            # Authored sample lines must NOT be spoken — adaptive coaching wins.
            "retryPrompt": "Câu mẫu retry không được đọc.",
            "interactionPrompts": {
                "helpOrRepeat": "Câu mẫu help không được đọc.",
                "unknownOrFrustrated": "Câu mẫu unknown không được đọc.",
                "vietnameseObject": "Câu mẫu vietnamese không được đọc.",
                "alreadyInLesson": "Câu mẫu already không được đọc.",
            },
        }
        cases = [
            ("help_or_repeat", "nói lại đi", ["mình nhắc lại", "từ mới là", "barn"]),
            ("unknown_or_frustrated", "con không biết", ["không sao", "tiếng anh là", "barn"]),
            ("vietnamese_object", "cái kho", ["cái kho", "tiếng anh là", "barn"]),
            ("already_in_lesson", "bắt đầu bài học", ["đang học", "barn"]),
            ("near_miss", "ball", ["gần đúng", "nói chậm", "barn"]),
            ("wrong", "cat", ["mình nghe rồi", "từ mình học là", "barn", "nói chậm"]),
        ]
        for intent, child_said, must_include in cases:
            self.assertEqual(
                _classify_child_response_intent(child_said, ["barn"]),
                intent,
                child_said,
            )
            spoken = _child_response_coaching_prompt(
                step, ["barn"], child_said, intent
            ).lower()
            for needle in must_include:
                self.assertIn(needle, spoken, (intent, spoken))
            # Safety: no raw free-form wrong answer echo, no canned sample line.
            self.assertNotIn("cat", spoken)
            self.assertNotIn("câu mẫu", spoken)
            self.assertNotIn("chưa đúng", spoken)

    def test_success_prompt_names_target_word_when_no_ceremony_line(self):
        self.assertEqual(
            _child_response_success_prompt({"expectedResponses": ["barn"]}, ["barn"]),
            "Đúng rồi! barn!",
        )
        self.assertEqual(
            _child_response_success_prompt(
                {"successPrompt": "  Hoàn thành bài học mẫu.  "},
                ["barn"],
            ),
            "Hoàn thành bài học mẫu.",
        )

def _assert_no_pronunciation_scoring_payload(testcase, value, *, path="event") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(key).replace("-", "_").lower()
            testcase.assertNotIn(
                normalized_key,
                GUIDED_SPEAKING_FORBIDDEN_EVENT_KEYS,
                f"guided speaking must not score/correct pronunciation immediately at {path}.{key}",
            )
            _assert_no_pronunciation_scoring_payload(testcase, child, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_pronunciation_scoring_payload(testcase, child, path=f"{path}[{index}]")


class PronunciationGuardCoverageTest(unittest.TestCase):
    """Regression lock on ``_assert_no_pronunciation_scoring_payload`` itself:
    the guided-speaking contract forbids *all five* pronunciation/phoneme keys
    from ever appearing in an outbound lesson event/result. This pins the guard
    so a future edit cannot silently narrow it back to only the score-suffixed
    variants."""

    # The five hard-constrained keys, each in the casings real producers emit.
    FORBIDDEN_KEY_CASINGS = (
        ("pronunciation", "pronunciation", "Pronunciation", "PRONUNCIATION"),
        ("phonemeScore", "phonemeScore", "phoneme_score", "phoneme-score"),
        ("phoneme_score", "phoneme_score", "PhonemeScore", "phoneme-score"),
        (
            "phonemeAssessment",
            "phonemeAssessment",
            "phoneme_assessment",
            "phoneme-assessment",
        ),
        (
            "phoneme_assessment",
            "phoneme_assessment",
            "phonemeAssessment",
            "phoneme-assessment",
        ),
    )

    def test_guard_rejects_each_forbidden_key_in_every_casing(self):
        for group in self.FORBIDDEN_KEY_CASINGS:
            for key in group:
                payload = {"stepId": "s1", "detail": {key: {"value": 0.9}}}
                with self.subTest(key=key):
                    with self.assertRaises(AssertionError):
                        _assert_no_pronunciation_scoring_payload(self, payload)

    def test_guard_passes_a_clean_guided_speaking_event(self):
        clean = {
            "type": "lesson_step",
            "stepId": "s1",
            "detail": {"prompt": "Say the word with me", "waitForChild": True},
            "children": [{"prompt": "Great, let's try together"}],
        }
        # Must not raise.
        _assert_no_pronunciation_scoring_payload(self, clean)

def _load_real_manage_api_client():
    """Load a fresh, isolated copy of the real ``config.manage_api_client`` from
    disk — immune to any monkeypatching other tests apply to the shared module
    instance. (conftest does NOT stub this module; it only filters warnings.)"""
    spec = importlib.util.spec_from_file_location(
        "config._mac_real_for_test",
        os.path.join(os.path.dirname(__file__), "..", "config", "manage_api_client.py"),
    )
    mac = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mac)
    return mac


# ── fakes ───────────────────────────────────────────────────────────────────────


class _DummyLogger:
    def bind(self, **kwargs):
        return self

    def info(self, *a, **k):
        return None

    def debug(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None

    def error(self, *a, **k):
        return None


class _CapturingLogger(_DummyLogger):
    def __init__(self):
        self.events = []

    def _capture(self, level, message, *args, **kwargs):
        self.events.append((level, str(message)))

    def info(self, message, *args, **kwargs):
        self._capture("info", message, *args, **kwargs)

    def debug(self, message, *args, **kwargs):
        self._capture("debug", message, *args, **kwargs)

    def warning(self, message, *args, **kwargs):
        self._capture("warning", message, *args, **kwargs)

    def error(self, message, *args, **kwargs):
        self._capture("error", message, *args, **kwargs)


class _FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class _FakeConn:
    def __init__(self, features=None, session_id=None, headers=None):
        self.logger = _DummyLogger()
        self.websocket = _FakeWebSocket()
        self.session_id = session_id or FIX["frames"]["lesson_prepare"]["sessionId"]
        self.headers = headers or {}
        self.features = (
            {"lesson": True, "renderer": "teebot-lesson-renderer.v1"}
            if features is None
            else features
        )
        self.config = {}

    def is_realtime_busy(self):
        return False

class _RecordingLessonVoiceProvider:
    def __init__(self):
        self.prompts = []
        self.prompt_continue_listening = []
        self.child_response_windows = []
        self.closed_child_response_windows = 0

    async def speak_lesson_step_prompt(self, text, *, continue_listening=False):
        self.prompts.append(text)
        self.prompt_continue_listening.append(bool(continue_listening))
        return True

    async def open_lesson_child_response_window(self):
        self.child_response_windows.append(True)
        return True

    def close_lesson_child_response_window(self):
        self.closed_child_response_windows += 1


class _FakeAssetCache:
    """Injectable preload outcome; never touches the network or disk."""

    def __init__(
        self,
        *,
        ready=True,
        preload_error=None,
        profile_error=None,
        local_urls=None,
    ):
        self.preload_timeout_sec = 90
        self.cache_key = "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3"
        self.asset_pack_local_root = "sd://sdcard/tbot/lesson-assets"
        self._ready = ready
        self._preload_error = preload_error
        self._profile_error = profile_error
        self._local_urls = local_urls
        self.closed = False

    def assert_profile_renderable(self):
        if self._profile_error is not None:
            raise self._profile_error

    async def preload(self):
        if self._preload_error is not None:
            raise self._preload_error
        return self._ready

    def synthesize_preload_status(self, assignment_version):
        return {
            "assignmentVersion": assignment_version,
            "ready": self._ready,
            "criticalTotal": 2,
            "criticalReady": 2 if self._ready else 0,
            "assets": [],
        }

    def public_url_for_source(self, source):
        if self._local_urls is None:
            return source
        return self._local_urls.get(source)

    def local_pack_url_for_source(self, source):
        if self._local_urls is None:
            return source
        return self._local_urls.get(source)

    def asset_pack_manifest(self, *, assignment_version, lesson_id, lesson_version, manifest_checksum):
        local_root = f"{self.asset_pack_local_root}/{self.cache_key}"
        source_assets = [
            (
                "backgroundScene.poster",
                "barn-round-field-poster.jpg",
                "image/jpeg",
                True,
                "backgroundScene",
                "poster",
            ),
            (
                "teachingObject.barn",
                "barn.png",
                "image/png",
                True,
                "teachingObject",
                "object",
            ),
            (
                "robotOverlay.teach",
                "bright-teach.png",
                "image/png",
                False,
                "robotOverlay",
                "pose",
            ),
        ]
        assets = []
        if self._local_urls is not None:
            for key, source, media_type, critical, layer, role in source_assets:
                local_path = self._local_urls.get(source)
                if not local_path:
                    continue
                assets.append(
                    {
                        "key": key,
                        "path": source,
                        "url": f"https://assets.example/{source}",
                        "sha256": "a" * 64,
                        "mediaType": media_type,
                        "critical": critical,
                        "layer": layer,
                        "role": role,
                        "localPath": local_path,
                        "state": "READY" if self._ready else "PENDING",
                        "checksumOk": self._ready,
                    }
                )
        if not assets:
            assets = [
                {
                    "key": key,
                    "path": source,
                    "url": f"https://assets.example/{source}",
                    "sha256": "a" * 64,
                    "mediaType": media_type,
                    "critical": critical,
                    "layer": layer,
                    "role": role,
                    "localPath": f"{local_root}/{key}",
                    "state": "READY" if self._ready else "PENDING",
                    "checksumOk": self._ready,
                }
                for key, source, media_type, critical, layer, role in source_assets
            ]
        return {
            "assignmentVersion": assignment_version,
            "lessonId": lesson_id,
            "lessonVersion": lesson_version,
            "manifestChecksum": manifest_checksum,
            "cacheKey": self.cache_key,
            "localRoot": local_root,
            "ready": self._ready,
            "assets": assets,
        }

    async def aclose(self):
        self.closed = True


class _FirmwareSyncAssetCache(_FakeAssetCache):
    """Fake cache that matches the strict firmware MCP sync contract."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.asset_pack_local_root = "sd://tbot/lesson-assets"

    def asset_pack_manifest(self, **kwargs):
        pack = super().asset_pack_manifest(**kwargs)
        for asset in pack["assets"]:
            asset["size"] = 1024
        return pack

class _ReadyLessonAssetMcpClient:
    def __init__(self):
        self.ready = True
        self.tools = {"self_lesson_assets_sync_to_sd": {}}

    async def is_ready(self):
        return self.ready

    def has_tool(self, name):
        return name in self.tools

class _NotReadyLessonAssetMcpClient(_ReadyLessonAssetMcpClient):
    def __init__(self):
        super().__init__()
        self.ready = False

class _MissingLessonAssetToolMcpClient(_ReadyLessonAssetMcpClient):
    def __init__(self):
        super().__init__()
        self.tools = {}


class _FakeForwarder:
    def __init__(self):
        self.batches = []
        self.closed = False

    def enqueue(self, batch):
        self.batches.append(batch)

    async def aclose(self):
        self.closed = True


class _FailingChildResponseWindowProvider:
    def __init__(self):
        self.calls = 0

    async def open_lesson_child_response_window(self):
        self.calls += 1
        raise RuntimeError("listener unavailable")

class _SequenceChildResponseWindowProvider:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    async def open_lesson_child_response_window(self):
        self.calls += 1
        if not self.results:
            return True
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

class _GatedSleep:
    def __init__(self):
        self.calls = []

    async def __call__(self, timeout_sec):
        gate = asyncio.Event()
        entry = (timeout_sec, gate)
        self.calls.append(entry)
        try:
            await gate.wait()
        except asyncio.CancelledError:
            if entry in self.calls:
                self.calls.remove(entry)
            raise

    def release_next(self):
        if not self.calls:
            raise AssertionError("no pending sleep gate")
        self.calls.pop(0)[1].set()

    def release_latest(self):
        if not self.calls:
            raise AssertionError("no pending sleep gate")
        self.calls.pop()[1].set()


# ── manifest / assignment derived FROM the fixture (round-trips to the frames) ───


def _build_manifest():
    prep = FIX["frames"]["lesson_prepare"]
    step = FIX["frames"]["lesson_step"]
    step_body = step["body"]
    assets = [
        {
            "id": a["key"],
            "layer": a["layer"],
            "role": a["role"],
            "mediaType": a["mediaType"],
            "path": a["path"],
            "url": "http://assets.test/" + a["path"],
            "sha256": a["sha256"],
            "critical": a["critical"],
        }
        for a in prep["body"]["criticalAssets"]
    ]
    steps = [
        {
            "id": step["stepId"],
            "type": step_body["stepType"],
            "scene": copy.deepcopy(step_body["scene"]),
            "audio": step_body["audio"],
            "timeoutSec": step_body["timeoutSec"],
            "prompt": step_body.get("prompt"),
            "subject": step_body.get("subject"),
            "storyText": step_body.get("storyText"),
            "storyBeat": copy.deepcopy(step_body.get("storyBeat")),
            "robotState": "modeling",
            "pose": "teach",
            "expression": "teaching",
            "phase": "model",
            "entrance": "none",
        }
    ]
    return {
        "manifestVersion": "teebot-lesson-renderer.v1",
        "lessonId": prep["lessonId"],
        "lessonVersion": prep["lessonVersion"],
        "profile": "espTft",
        "assets": assets,
        "steps": steps,
    }


def _build_multistep_manifest():
    """P5: a 2-step manifest projecting s4 (frames.lesson_step) THEN s5
    (multiStep.frames.lesson_step_s5) in manifest order. Both step frames are taken
    verbatim from the fixture so the emitted lesson_step bodies round-trip back to
    their frozen wire shapes."""
    base = _build_manifest()
    s4 = FIX["frames"]["lesson_step"]
    s5 = FIX["multiStep"]["frames"]["lesson_step_s5"]
    base["steps"] = [
        {
            "id": s4["stepId"],
            "type": s4["body"]["stepType"],
            "prompt": s4["body"].get("prompt"),
            "subject": s4["body"].get("subject"),
            "storyText": s4["body"].get("storyText"),
            "storyBeat": copy.deepcopy(s4["body"].get("storyBeat")),
            "scene": copy.deepcopy(s4["body"]["scene"]),
            "audio": s4["body"]["audio"],
            "timeoutSec": s4["body"]["timeoutSec"],
        },
        {
            "id": s5["stepId"],
            "type": s5["body"]["stepType"],
            "prompt": s5["body"].get("prompt"),
            "storyText": s5["body"].get("storyText"),
            "storyBeat": copy.deepcopy(s5["body"].get("storyBeat")),
            "completionClass": s5["body"].get("completionClass"),
            "scene": copy.deepcopy(s5["body"]["scene"]),
            "audio": s5["body"]["audio"],
            "timeoutSec": s5["body"]["timeoutSec"],
        },
    ]
    return base


def _build_two_interactive_step_manifest():
    """A 2-step manifest where BOTH steps are INTERACTIVE (model s4 -> listen s5),
    so each gates on its own ack + child response/progress evidence. Used to test
    latch-reset between steps without the passive auto-advance shortcut."""
    base = _build_manifest()
    step = FIX["frames"]["lesson_step"]
    base["steps"] = [
        {
            "id": "s4",
            "type": "model",
            "scene": copy.deepcopy(step["body"]["scene"]),
            "audio": step["body"]["audio"],
            "timeoutSec": step["body"]["timeoutSec"],
        },
        {
            "id": "s5",
            "type": "listen",  # INTERACTIVE: still waits for step_completed.
            "scene": copy.deepcopy(step["body"]["scene"]),
            "audio": step["body"]["audio"],
            "timeoutSec": step["body"]["timeoutSec"],
        },
    ]
    return base


def _build_steps_manifest(step_specs):
    """Build a manifest with arbitrary (id, type) steps reusing the frozen s4 scene/
    audio so each emitted lesson_step is a valid §5.6 body. ``step_specs`` is a list
    of (stepId, stepType) tuples in authored/manifest order."""
    base = _build_manifest()
    step = FIX["frames"]["lesson_step"]
    base["steps"] = [
        {
            "id": sid,
            "type": stype,
            "scene": copy.deepcopy(step["body"]["scene"]),
            "audio": step["body"]["audio"],
            "timeoutSec": step["body"]["timeoutSec"],
        }
        for sid, stype in step_specs
    ]
    return base


def _build_class_steps_manifest(step_specs):
    """L3 P1: a manifest of (id, type, completionClass) steps reusing the frozen s4
    scene/audio. ``completionClass`` is either 'passive'/'interactive' (the explicit
    authoritative classifier) or None (omit the field entirely -> v1 fallback path).
    The step ``type`` can be an AUTHOR-DEFINED kind unknown to PASSIVE_STEP_TYPES."""
    base = _build_manifest()
    step = FIX["frames"]["lesson_step"]
    out = []
    for sid, stype, completion_class in step_specs:
        row = {
            "id": sid,
            "type": stype,
            "scene": copy.deepcopy(step["body"]["scene"]),
            "audio": step["body"]["audio"],
            "timeoutSec": step["body"]["timeoutSec"],
        }
        if completion_class is not None:
            row["completionClass"] = completion_class
        out.append(row)
    base["steps"] = out
    return base

def _build_full_seed_story_manifest():
    """Backend-shaped 9-step seed story: passive narration around four guided
    speaking steps. Scenes keep the authored robotOverlay pose per step so the
    firmware receives a real three-layer image stack throughout the lesson."""
    base = _build_manifest()
    frame_step = FIX["frames"]["lesson_step"]
    backend = _load_backend_canonical_manifest_for_test()

    story = [
        {"id": "s1", "type": "greeting", "completionClass": "passive", "pose": "teach"},
        {"id": "s2", "type": "review", "completionClass": "passive", "pose": "teach"},
        {"id": "s3", "type": "focus", "completionClass": "passive", "pose": "teach"},
        {"id": "s4", "type": "model", "completionClass": "interactive", "pose": "teach"},
        {"id": "s5", "type": "listen", "completionClass": "interactive", "pose": "listening"},
        {"id": "s6", "type": "repeat", "completionClass": "interactive", "pose": "listening"},
        {"id": "s7", "type": "fillBlank", "completionClass": "interactive", "pose": "thinking"},
        {"id": "s8", "type": "feedback", "completionClass": "passive", "pose": "teach"},
        {"id": "s9", "type": "celebrate", "completionClass": "passive", "pose": "celebrate"},
    ]
    assets = []
    for asset in backend["assets"]:
        row = copy.deepcopy(asset)
        row.setdefault("url", "http://assets.test/" + row["path"])
        assets.append(row)
    base["assets"] = assets
    story_by_id = {row["id"]: row for row in story}
    story = [
        {
            **copy.deepcopy(step),
            "completionClass": story_by_id[step["id"]]["completionClass"],
        }
        for step in backend["steps"]
    ]
    expression_by_pose = {
        "teach": "teaching",
        "listening": "listening",
        "thinking": "thinking",
        "celebrate": "celebrating",
    }
    asset_by_id = {asset["id"]: asset for asset in base["assets"]}
    background = asset_by_id.get("backgroundScene.poster")
    teaching = asset_by_id.get("teachingObject.barn")
    steps = []
    for authored in story:
        sid = authored["id"]
        pose = authored["pose"]
        overlay = asset_by_id.get(f"robotOverlay.{pose}")
        scene = copy.deepcopy(frame_step["body"]["scene"])
        if background is not None:
            scene["backgroundScene"]["poster"] = {
                "key": background["id"],
                "src": os.path.basename(background["path"]),
                "fit": "cover",
                "sha256": background["sha256"],
            }
        if teaching is not None:
            scene["teachingObject"]["asset"] = {
                "key": teaching["id"],
                "src": os.path.basename(teaching["path"]),
                "sha256": teaching["sha256"],
            }
        scene["robotOverlay"] = {
            "robotState": "celebrating" if pose == "celebrate" else ("listening" if pose == "listening" else ("thinking" if pose == "thinking" else "talking")),
            "pose": pose,
            "expression": expression_by_pose[pose],
            "anchor": "bottomLeft",
            "asset": {
                "key": overlay["id"] if overlay else f"robotOverlay.{pose}",
                "src": os.path.basename(overlay["path"]) if overlay else f"bright-{pose}.png",
                "sha256": overlay["sha256"] if overlay else f"sha-{pose}",
            },
        }
        row = {
            "id": sid,
            "type": authored["type"],
            "prompt": authored.get("prompt"),
            "completionClass": authored["completionClass"],
            "phase": authored.get("phase"),
            "subject": authored.get("subject", "barn"),
            "robotState": authored.get("robotState"),
            "pose": pose,
            "expression": authored.get("expression", expression_by_pose[pose]),
            "entrance": authored.get("entrance", "none"),
            "scene": scene,
            "audio": frame_step["body"]["audio"],
            "timeoutSec": frame_step["body"]["timeoutSec"],
        }
        for key in ("helperText", "l1TransferHint", "choices"):
            if authored.get(key) is not None:
                row[key] = copy.deepcopy(authored[key])
        steps.append(row)
    base["steps"] = steps
    return base

def _load_backend_canonical_manifest_for_test():
    if BACKEND_CANONICAL_MANIFEST_PATH is None:
        raise AssertionError(
            "backend canonical espTft manifest is required; searched: "
            + ", ".join(_backend_canonical_manifest_candidates())
        )
    with open(BACKEND_CANONICAL_MANIFEST_PATH) as fh:
        return json.load(fh)


def _build_assignment():
    prep = FIX["frames"]["lesson_prepare"]
    return {
        "assignmentId": prep["assignmentId"],
        "assignmentVersion": prep["body"]["assignmentVersion"],
        "lessonId": prep["lessonId"],
        "lessonVersion": prep["lessonVersion"],
        "manifestChecksum": _manifest_checksum(),
        "profile": "espTft",
        "state": "ASSIGNED",
    }


def _manifest_checksum():
    return FIX["frames"]["lesson_prepare"]["body"]["manifestRef"]["manifestChecksum"]


def _ack(acks, env_seq, *, step_id=None, extra=None):
    body = {"acks": acks, "rendered": True, "degraded": False}
    if extra is not None:
        body = dict(extra)
    prep = FIX["frames"]["lesson_prepare"]
    return {
        "type": "lesson_ack",
        "protocolVersion": "teebot-lesson-renderer.v1",
        "assignmentId": prep["assignmentId"],
        "sessionId": prep["sessionId"],
        "lessonId": "w01-d01-barn-say-it",
        "lessonVersion": 3,
        "stepId": step_id,
        "sequence": env_seq,
        "timestamp": 1,
        "body": body,
    }


def _progress(env_seq, body, *, step_id="s4"):
    prep = FIX["frames"]["lesson_prepare"]
    return {
        "type": "lesson_progress",
        "protocolVersion": "teebot-lesson-renderer.v1",
        "assignmentId": prep["assignmentId"],
        "sessionId": prep["sessionId"],
        "lessonId": "w01-d01-barn-say-it",
        "lessonVersion": 3,
        "stepId": step_id,
        "sequence": env_seq,
        "timestamp": 1,
        "body": body,
    }


class _RecordingAlarm:
    """S13 alarm stand-in: records the preload-window bracket calls (plan §11.2)."""

    def __init__(self):
        self.events = []  # ("active", bool) in call order
        self.depth = 0
        self.max_depth = 0

    def set_preload_active(self, active):
        self.events.append(("active", bool(active)))
        self.depth += 1 if active else -1
        self.max_depth = max(self.max_depth, self.depth)


class LessonRuntimeTest(unittest.IsolatedAsyncioTestCase):
    def test_robot_fixture_resolver_finds_docs_and_firmware_from_feature_worktree(self):
        self.assertTrue(os.path.isfile(FIXTURE_PATH))
        self.assertTrue(os.path.isfile(SEED_LESSON_PATH))
        self.assertIn(os.path.join("robot", "docs"), FIXTURE_PATH)
        self.assertIn(os.path.join("robot", "TBOT-Firmware"), SEED_LESSON_PATH)

    def test_backend_canonical_fixture_resolver_finds_complete_worktree_manifest(self):
        self.assertIsNotNone(BACKEND_CANONICAL_MANIFEST_PATH)
        self.assertTrue(os.path.isfile(BACKEND_CANONICAL_MANIFEST_PATH))
        backend = _load_backend_canonical_manifest_for_test()
        self.assertEqual(len(backend["steps"]), 9)
        self.assertTrue(all(step.get("prompt") for step in backend["steps"]))

    def test_full_seed_story_fixture_stays_in_parity_with_backend_canonical_manifest(self):
        backend = _load_backend_canonical_manifest_for_test()
        manifest = _build_full_seed_story_manifest()

        self.assertEqual(manifest["manifestVersion"], backend["manifestVersion"])
        self.assertEqual(manifest["lessonId"], backend["lessonId"])
        self.assertEqual(manifest["profile"], backend["profile"])
        self.assertEqual(
            [asset["id"] for asset in manifest["assets"]],
            [asset["id"] for asset in backend["assets"]],
        )
        self.assertEqual(
            [(asset["id"], asset["layer"], asset["role"], asset["path"], asset["critical"]) for asset in manifest["assets"]],
            [(asset["id"], asset["layer"], asset["role"], asset["path"], asset["critical"]) for asset in backend["assets"]],
        )

        expected_story = [
            (
                step["id"],
                step["type"],
                "interactive" if step["id"] in {"s4", "s5", "s6", "s7"} else "passive",
                step["phase"],
                step["pose"],
                step["prompt"],
                step.get("helperText"),
                step.get("choices"),
            )
            for step in backend["steps"]
        ]
        actual_story = [
            (
                step["id"],
                step["type"],
                step["completionClass"],
                step.get("phase"),
                step["scene"]["robotOverlay"]["pose"],
                step.get("prompt"),
                step.get("helperText"),
                step.get("choices"),
            )
            for step in manifest["steps"]
        ]
        self.assertEqual(actual_story, expected_story)

        interactive = [step for step in manifest["steps"] if step["completionClass"] == "interactive"]
        self.assertEqual([step["id"] for step in interactive], ["s4", "s5", "s6", "s7"])
        for step in interactive:
            _assert_guided_speaking_practice_prompt(self, step["prompt"])
        self.assertEqual(
            next(step for step in manifest["steps"] if step["id"] == "s7")["helperText"],
            "TeeBot waits for your voice.",
        )

    def _runtime(
        self, conn=None, manifest=None, asset_cache=None, forwarder=None, alarm=None, **runtime_kwargs
    ):
        from core.lesson.runtime import LessonRuntime

        prep = FIX["frames"]["lesson_prepare"]
        conn = conn or _FakeConn(session_id=prep["sessionId"])
        with patch("core.lesson.runtime.uuid.uuid4", return_value=prep["sessionId"]):
            return LessonRuntime(
                conn,
                assignment=_build_assignment(),
                manifest=manifest or _build_manifest(),
                asset_cache=asset_cache or _FakeAssetCache(ready=True),
                forwarder=forwarder or _FakeForwarder(),
                manifest_checksum=_manifest_checksum(),
                alarm=alarm,
                **runtime_kwargs,
            )

    def _sent_frames(self, conn):
        return [json.loads(p) for p in conn.websocket.sent]

    async def test_lesson_step_ack_forwards_bounded_operations_telemetry(self):
        from unittest.mock import AsyncMock

        forwarder = _FakeForwarder()
        rt = self._runtime(forwarder=forwarder)
        rt._outstanding[9] = {
            "type": "lesson_step",
            "stepId": "s4",
            "body": {"motion": {"present": "teach"}},
            "retryCount": 2,
        }
        rt.conn.device_id = "robot-01"
        rt.conn.config = {
            "lesson": {
                "motion_presets_enabled": True,
                "rollout_device_allowlist": ["robot-01"],
            }
        }
        rt._on_frame_acked = AsyncMock()

        await rt.on_lesson_ack(
            _ack(
                9,
                1,
                step_id="s4",
                extra={
                    "acks": 9,
                    "rendered": True,
                    "degraded": True,
                    "telemetry": {
                        "internalMinimumFreeBytes": 24_576,
                        "psramFreeBytes": 1_500_000,
                        "renderElapsedMs": 321,
                        "degradedReason": "motionPreset",
                        "motionDispatch": "failed",
                    },
                },
            )
        )

        self.assertEqual(
            forwarder.batches,
            [
                {
                    "assignmentId": rt.assignment_id,
                    "lessonId": rt.lesson_id,
                    "lessonVersion": rt.lesson_version,
                    "sessionId": rt.session_id,
                    "events": [
                        {
                            "type": "step_started",
                            "sequence": 1,
                            "stepId": "s4",
                            "sramFreeBytes": 24_576,
                            "psramFreeBytes": 1_500_000,
                            "retryCount": 2,
                            "motionDispatch": "failed",
                        }
                    ],
                }
            ],
        )

    async def test_lesson_step_ack_telemetry_rejects_invalid_and_unapproved_fields(self):
        from unittest.mock import AsyncMock

        forwarder = _FakeForwarder()
        rt = self._runtime(forwarder=forwarder)
        rt._outstanding[4] = {
            "type": "lesson_step",
            "stepId": "s4",
            "body": {},
            "retryCount": -3,
        }
        rt._on_frame_acked = AsyncMock()

        await rt.on_lesson_ack(
            _ack(
                4,
                1,
                step_id="s4",
                extra={
                    "acks": 4,
                    "degraded": "yes",
                    "motionDispatch": "success",
                    "transcript": "secret child speech",
                    "telemetry": {
                        "internalMinimumFreeBytes": -1,
                        "psramFreeBytes": True,
                        "motionDispatch": "applied",
                        "authorization": "Bearer secret",
                        "childResponse": "secret child speech",
                    },
                },
            )
        )

        self.assertEqual(
            forwarder.batches[0]["events"],
            [{"type": "step_started", "sequence": 1, "stepId": "s4", "retryCount": 0}],
        )
        serialized = json.dumps(forwarder.batches[0])
        self.assertNotIn("secret", serialized)
        self.assertNotIn("robot-01", serialized)

    async def test_lesson_step_ack_maps_only_firmware_motion_dispatch_enum(self):
        from unittest.mock import AsyncMock

        for firmware_value in ("success", "failed", "skipped"):
            with self.subTest(firmware_value=firmware_value):
                forwarder = _FakeForwarder()
                rt = self._runtime(forwarder=forwarder)
                rt.conn.device_id = "robot-01"
                rt.conn.config = {
                    "lesson": {
                        "motion_presets_enabled": True,
                        "rollout_device_allowlist": ["robot-01"],
                    }
                }
                rt._outstanding[3] = {
                    "type": "lesson_step",
                    "stepId": "s4",
                    "body": {"motion": {"present": "teach"}},
                    "retryCount": 0,
                }
                rt._on_frame_acked = AsyncMock()

                await rt.on_lesson_ack(
                    _ack(
                        3,
                        1,
                        step_id="s4",
                        extra={
                            "acks": 3,
                            "rendered": True,
                            "degraded": False,
                            "telemetry": {
                                "internalMinimumFreeBytes": 30_000,
                                "psramFreeBytes": 2_000_000,
                                "degradedReason": "",
                                "motionDispatch": firmware_value,
                            },
                        },
                    )
                )

                self.assertEqual(
                    forwarder.batches[0]["events"][0]["motionDispatch"],
                    firmware_value,
                )

    async def test_lesson_step_ack_does_not_infer_motion_success_when_enum_is_missing(self):
        from unittest.mock import AsyncMock

        forwarder = _FakeForwarder()
        rt = self._runtime(forwarder=forwarder)
        rt._outstanding[3] = {
            "type": "lesson_step",
            "stepId": "s4",
            "body": {"motion": {"present": "teach"}},
            "retryCount": 0,
        }
        rt._on_frame_acked = AsyncMock()

        await rt.on_lesson_ack(
            _ack(
                3,
                1,
                step_id="s4",
                extra={
                    "acks": 3,
                    "rendered": True,
                    "degraded": False,
                    "telemetry": {"degradedReason": ""},
                },
            )
        )

        self.assertNotIn("motionDispatch", forwarder.batches[0]["events"][0])

    async def test_lesson_step_ack_marks_visual_degradation_from_firmware_reason(self):
        from unittest.mock import AsyncMock

        forwarder = _FakeForwarder()
        rt = self._runtime(forwarder=forwarder)
        rt._outstanding[3] = {
            "type": "lesson_step",
            "stepId": "s4",
            "body": {},
            "retryCount": 0,
        }
        rt._on_frame_acked = AsyncMock()

        await rt.on_lesson_ack(
            _ack(
                3,
                1,
                step_id="s4",
                extra={
                    "acks": 3,
                    "rendered": True,
                    "degraded": True,
                    "telemetry": {"degradedReason": "objectUnavailable"},
                },
            )
        )

        self.assertIs(forwarder.batches[0]["events"][0]["renderDegraded"], True)

    async def test_lesson_step_ack_prefers_explicit_firmware_render_degraded_boolean(self):
        from unittest.mock import AsyncMock

        for render_degraded in (True, False):
            with self.subTest(render_degraded=render_degraded):
                forwarder = _FakeForwarder()
                rt = self._runtime(forwarder=forwarder)
                rt._outstanding[3] = {
                    "type": "lesson_step",
                    "stepId": "s4",
                    "body": {"motion": {"present": "teach"}},
                    "retryCount": 0,
                }
                rt._on_frame_acked = AsyncMock()

                await rt.on_lesson_ack(
                    _ack(
                        3,
                        1,
                        step_id="s4",
                        extra={
                            "acks": 3,
                            "rendered": True,
                            "degraded": True,
                            "telemetry": {
                                "degradedReason": "motionPreset",
                                "motionDispatch": "failed",
                                "renderDegraded": render_degraded,
                            },
                        },
                    )
                )

                self.assertIs(
                    forwarder.batches[0]["events"][0]["renderDegraded"],
                    render_degraded,
                )

    async def test_lesson_step_ack_omits_legacy_motion_reason_that_may_mask_visual_failure(self):
        from unittest.mock import AsyncMock

        forwarder = _FakeForwarder()
        rt = self._runtime(forwarder=forwarder)
        rt._outstanding[3] = {
            "type": "lesson_step",
            "stepId": "s4",
            "body": {"motion": {"present": "teach"}},
            "retryCount": 0,
        }
        rt._on_frame_acked = AsyncMock()

        await rt.on_lesson_ack(
            _ack(
                3,
                1,
                step_id="s4",
                extra={
                    "acks": 3,
                    "rendered": True,
                    "degraded": True,
                    "telemetry": {
                        "degradedReason": "motionPreset",
                        "motionDispatch": "failed",
                    },
                },
            )
        )

        self.assertNotIn("renderDegraded", forwarder.batches[0]["events"][0])

    async def test_lesson_step_ack_omits_render_metric_for_ambiguous_degraded_ack(self):
        from unittest.mock import AsyncMock

        forwarder = _FakeForwarder()
        rt = self._runtime(forwarder=forwarder)
        rt._outstanding[3] = {
            "type": "lesson_step",
            "stepId": "s4",
            "body": {},
            "retryCount": 0,
        }
        rt._on_frame_acked = AsyncMock()

        await rt.on_lesson_ack(
            _ack(
                3,
                1,
                step_id="s4",
                extra={
                    "acks": 3,
                    "rendered": True,
                    "degraded": True,
                    "telemetry": {"degradedReason": ""},
                },
            )
        )

        self.assertNotIn("renderDegraded", forwarder.batches[0]["events"][0])

    async def test_preload_reports_ready_and_failed_critical_assets_to_backend(self):
        class _PreloadStatusCache(_FakeAssetCache):
            def synthesize_preload_status(self, assignment_version):
                return {
                    "assignmentVersion": assignment_version,
                    "ready": False,
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "critical": True,
                            "state": "READY",
                            "checksumOk": True,
                        },
                        {
                            "key": "teachingObject.barn",
                            "critical": True,
                            "state": "FAILED",
                            "checksumOk": False,
                        },
                        {
                            "key": "robotOverlay.teach",
                            "critical": True,
                            "state": "PENDING",
                        },
                        {
                            "key": "robotOverlay.listen",
                            "critical": False,
                            "state": "READY",
                            "checksumOk": True,
                        },
                    ],
                }

        reports = []

        async def _report(report):
            reports.append(dict(report))

        conn = _FakeConn()
        rt = self._runtime(
            conn=conn,
            asset_cache=_PreloadStatusCache(ready=False),
            preload_status_reporter=_report,
        )

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task

        self.assertEqual(
            reports,
            [
                {
                    "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
                    "assetId": "backgroundScene.poster",
                    "state": "READY",
                    "checksumOk": True,
                },
                {
                    "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
                    "assetId": "teachingObject.barn",
                    "state": "FAILED",
                    "checksumOk": False,
                },
            ],
        )
        self.assertEqual([frame["type"] for frame in self._sent_frames(conn)], ["lesson_prepare"])

    async def test_preload_reports_real_synthesized_critical_asset_statuses_to_backend(self):
        class _SynthesizingAssetCache(AssetCache):
            async def preload(self):
                self._by_key["backgroundScene.poster"].state = READY
                self._by_key["backgroundScene.poster"].checksum_ok = True
                self._by_key["teachingObject.barn"].state = FAILED
                self._by_key["teachingObject.barn"].checksum_ok = False
                self._by_key["robotOverlay.teach"].state = READY
                self._by_key["robotOverlay.teach"].checksum_ok = True
                return False

        cache_root = tempfile.mkdtemp(prefix="lesson-runtime-cache-")
        self.addCleanup(shutil.rmtree, cache_root, True)
        cache = _SynthesizingAssetCache(
            assets=[
                {
                    "key": "backgroundScene.poster",
                    "path": "poster.jpg",
                    "sha256": "a" * 64,
                    "critical": True,
                    "layer": "backgroundScene",
                    "role": "poster",
                    "mediaType": "image/jpeg",
                },
                {
                    "key": "teachingObject.barn",
                    "path": "barn.png",
                    "sha256": "b" * 64,
                    "critical": True,
                    "layer": "teachingObject",
                    "role": "primarySubject",
                    "mediaType": "image/png",
                },
                {
                    "key": "robotOverlay.teach",
                    "path": "teach.png",
                    "sha256": "c" * 64,
                    "critical": False,
                    "layer": "robotOverlay",
                    "role": "pose",
                    "mediaType": "image/png",
                },
            ],
            profile="espTft",
            cache_root=cache_root,
        )
        reports = []

        async def _report(report):
            reports.append(dict(report))

        conn = _FakeConn()
        rt = self._runtime(
            conn=conn,
            asset_cache=cache,
            preload_status_reporter=_report,
        )

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task

        self.assertEqual(
            reports,
            [
                {
                    "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
                    "assetId": "backgroundScene.poster",
                    "state": "READY",
                    "checksumOk": True,
                },
                {
                    "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
                    "assetId": "teachingObject.barn",
                    "state": "FAILED",
                    "checksumOk": False,
                },
            ],
        )
        self.assertEqual([frame["type"] for frame in self._sent_frames(conn)], ["lesson_prepare"])

    async def test_preload_status_post_hang_does_not_block_lesson_start(self):
        class _ReadyAssetCache(_FakeAssetCache):
            def synthesize_preload_status(self, assignment_version):
                return {
                    "assignmentVersion": assignment_version,
                    "ready": True,
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "critical": True,
                            "state": "READY",
                            "checksumOk": True,
                        },
                    ],
                }

        reporter_started = asyncio.Event()
        reporter_cancelled = asyncio.Event()

        async def _hung_reporter(report):
            reporter_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                reporter_cancelled.set()
                raise

        conn = _FakeConn()
        rt = self._runtime(
            conn=conn,
            asset_cache=_ReadyAssetCache(ready=True),
            preload_status_reporter=_hung_reporter,
        )

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await asyncio.wait_for(rt._preload_task, timeout=0.5)
        await asyncio.wait_for(reporter_started.wait(), timeout=0.5)

        self.assertEqual(rt.state, "READY")
        self.assertEqual(
            [frame["type"] for frame in self._sent_frames(conn)],
            ["lesson_prepare", "lesson_start"],
        )

        await rt.close()
        await asyncio.wait_for(reporter_cancelled.wait(), timeout=0.5)

    def test_forwarded_progress_batch_preserves_ws_trace_context(self):
        forwarder = _FakeForwarder()
        conn = _FakeConn(
            headers={
                "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
                "tracestate": "rojo=00f067aa0ba902b7",
            }
        )
        rt = self._runtime(conn=conn, forwarder=forwarder)

        rt._forward({"type": "lesson_started"})

        self.assertEqual(
            forwarder.batches[0]["traceparent"],
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        )
        self.assertEqual(forwarder.batches[0]["tracestate"], "rojo=00f067aa0ba902b7")

    def test_step_body_preserves_safe_story_and_vocab_metadata(self):
        rt = self._runtime()
        step = copy.deepcopy(_build_manifest()["steps"][0])
        step.update(
            {
                "story": {"beatId": "intro", "text": "TeeBot and the child visit a barn."},
                "storyText": "TeeBot and the child visit a barn.",
                "storyBeat": {"ask": "What animal do you see?", "waitForChild": True},
                "vocab": {"word": "barn", "partOfSpeech": "noun"},
            }
        )

        body = rt._step_body(step)

        self.assertEqual(body["story"], {"beatId": "intro", "text": "TeeBot and the child visit a barn."})
        self.assertEqual(body["storyText"], "TeeBot and the child visit a barn.")
        self.assertEqual(body["storyBeat"], {"ask": "What animal do you see?", "waitForChild": True})
        self.assertEqual(body["vocab"], {"word": "barn", "partOfSpeech": "noun"})

    def test_step_body_forwards_named_tvideo_projection_without_editor_geometry(self):
        rt = self._runtime()
        step = copy.deepcopy(_build_manifest()["steps"][0])
        step["templateProjection"] = {
            "templateId": "tvideoFlyWalk",
            "templateVersion": 1,
            "layoutPreset": "centerRoad",
            "geometryVersion": 1,
            "phases": [
                {"name": "hidden", "durationMs": 100}, {"name": "flyIn", "durationMs": 1200},
                {"name": "landFar", "durationMs": 700}, {"name": "settle", "durationMs": 350},
                {"name": "walkToward", "durationMs": 1800}, {"name": "arriveNear", "durationMs": 250},
                {"name": "greetIdle", "durationMs": 650}, {"name": "revealTeachingContent", "durationMs": 100},
            ],
            "revealPhase": "revealTeachingContent",
            "fallbackPolicy": "snapToArriveNearAndReveal",
            "background": {"versionId": "backgroundScene.poster", "sha256": "b" * 64, "bytes": 1200, "mediaType": "image/jpeg"},
            "arrivedPose": {"versionId": "robotOverlay.arrived", "sha256": "a" * 64, "bytes": 800, "mediaType": "image/png"},
        }

        body = rt._step_body(step)

        self.assertEqual(body["templateProjection"]["templateId"], "tvideoFlyWalk")
        self.assertNotIn("x", body["templateProjection"])
        self.assertNotIn("servoCommand", body["templateProjection"])

    def test_step_body_rejects_forged_or_unbounded_tvideo_projection(self):
        rt = self._runtime()
        base = {
            "templateId": "tvideoFlyWalk", "templateVersion": 1, "layoutPreset": "centerRoad", "geometryVersion": 1,
            "phases": [
                {"name": "hidden", "durationMs": 100}, {"name": "flyIn", "durationMs": 1200},
                {"name": "landFar", "durationMs": 700}, {"name": "settle", "durationMs": 350},
                {"name": "walkToward", "durationMs": 1800}, {"name": "arriveNear", "durationMs": 250},
                {"name": "greetIdle", "durationMs": 650}, {"name": "revealTeachingContent", "durationMs": 100},
            ],
            "revealPhase": "revealTeachingContent", "fallbackPolicy": "snapToArriveNearAndReveal",
            "background": {"versionId": "bg", "sha256": "b" * 64, "bytes": 1200, "mediaType": "image/jpeg"},
            "arrivedPose": {"versionId": "pose", "sha256": "a" * 64, "bytes": 800, "mediaType": "image/png"},
        }
        forged = [
            {**base, "servoCommands": [1]},
            {**base, "phases": [{"name": "flyIn", "durationMs": 999999999}]},
            {**base, "atlas": {"versionId": "movie.mov", "sha256": "c" * 64, "bytes": 10, "mediaType": "video/quicktime"}},
            {**base, "left": 10, "top": 20, "width": 30, "height": 40},
        ]
        for projection in forged:
            step = copy.deepcopy(_build_manifest()["steps"][0])
            step["templateProjection"] = projection
            self.assertNotIn("templateProjection", rt._step_body(step))

    async def test_runtime_close_and_replay_helpers_are_defensive(self):
        class _ReplayForwarder(_FakeForwarder):
            async def replay_pending_terminal_event(self):
                return True

        asset_cache = _FakeAssetCache(ready=True)
        forwarder = _ReplayForwarder()
        rt = self._runtime(asset_cache=asset_cache, forwarder=forwarder)

        self.assertTrue(await rt.replay_pending_terminal_event())
        rt.forwarder = _FakeForwarder()
        self.assertFalse(await rt.replay_pending_terminal_event())

        rt.forwarder = forwarder
        await rt.close()

        self.assertTrue(forwarder.closed)
        self.assertTrue(asset_cache.closed)

    async def test_runtime_defensive_private_branches_cover_empty_steps_and_sd_pack_edges(self):
        class _CacheWithoutResolver:
            cache_key = "current-cache"
            asset_pack_local_root = ""

        manifest = {**_build_manifest(), "steps": []}
        rt = self._runtime(manifest=manifest, asset_cache=_FakeAssetCache(ready=True))

        await rt._emit_step()

        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "LESSON_STEP_MISSING")

        rt.asset_cache = _CacheWithoutResolver()
        rt.conn.config = {"lesson": {"asset_pack_local_root": []}}
        self.assertFalse(rt._is_sd_asset_pack_source("sd://sdcard/tbot/lesson-assets/x"))
        rt._rewrite_required_http_layer_sources({"src": "http://assets.test/barn.png"})
        self.assertFalse(rt._ack_reports_asset_pack_ready({"assetPack": {"ready": True, "cacheKey": ""}}))

    async def test_close_cancels_active_preload_task(self):
        import asyncio

        class _SlowAssetCache(_FakeAssetCache):
            async def preload(self):
                await asyncio.sleep(10)
                return True

        rt = self._runtime(asset_cache=_SlowAssetCache(ready=True))
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        self.assertIsNotNone(rt._preload_task)

        await rt.close()
        await asyncio.sleep(0)

        self.assertTrue(rt._preload_task.cancelled() or rt._preload_task.done())

    async def test_inbound_lesson_error_fails_running_lesson_and_releases_mode(self):
        conn = _FakeConn()
        released = []

        async def release_lesson_mode(*, reason):
            released.append(reason)

        conn.release_lesson_mode = release_lesson_mode
        rt = self._runtime(conn=conn)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        await rt.on_lesson_error(
            {
                "assignmentId": rt.assignment_id,
                "sessionId": rt.session_id,
                "sequence": 3,
                "body": {
                    # NON-retryable: this test asserts the TERMINAL path. A
                    # retryable inbound error is deferred to the in-flight
                    # bounded recovery timer instead (T2.1 — see
                    # test_lesson_runtime_state_machine_t21.py).
                    "code": "DISPLAY_FAULT",
                    "message": "renderer stopped",
                    "retryable": False,
                },
            }
        )

        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "DISPLAY_FAULT")
        self.assertFalse(rt.last_error.retryable)
        self.assertEqual(released, ["lesson_error"])

    async def test_successful_completion_routes_to_finish_lesson_mode(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        finished = []
        released = []

        async def finish_lesson_mode(*, reason):
            finished.append(reason)

        async def release_lesson_mode(*, reason):
            released.append(reason)

        conn.finish_lesson_mode = finish_lesson_mode
        conn.release_lesson_mode = release_lesson_mode
        rt = self._runtime(conn=conn)

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))  # prepare-ack
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))  # step-ack
        await rt.on_lesson_progress(
            _progress(
                4,
                {
                    "event": "step_completed",
                    "stepType": "model",
                    "result": "success",
                    "detail": {"utterance": "Yes! barn!"},
                },
            )
        )
        await rt.on_lesson_ack(_ack(4, 5))  # stop-ack -> COMPLETED

        self.assertEqual(rt.state, "COMPLETED")
        # Success goes to the happy-face + conversation handler, NOT the dormant release.
        self.assertEqual(finished, ["lesson_completed"])
        self.assertEqual(released, [])

    async def test_completion_falls_back_to_release_when_finish_absent(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        released = []

        async def release_lesson_mode(*, reason):
            released.append(reason)

        conn.release_lesson_mode = release_lesson_mode  # no finish_lesson_mode on conn
        rt = self._runtime(conn=conn)

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        await rt.on_lesson_progress(
            _progress(
                4,
                {"event": "step_completed", "stepType": "model", "result": "success",
                 "detail": {"utterance": "Yes! barn!"}},
            )
        )
        await rt.on_lesson_ack(_ack(4, 5))

        self.assertEqual(rt.state, "COMPLETED")
        # No finish hook -> backward-compatible release path still runs.
        self.assertEqual(released, ["lesson_completed"])

    async def test_failed_terminal_routes_to_finish_lesson_mode_when_available(self):
        conn = _FakeConn()
        finished = []
        released = []

        async def finish_lesson_mode(*, reason):
            finished.append(reason)

        async def release_lesson_mode(*, reason):
            released.append(reason)

        conn.finish_lesson_mode = finish_lesson_mode
        conn.release_lesson_mode = release_lesson_mode
        rt = self._runtime(conn=conn)

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_error(
            {
                "assignmentId": rt.assignment_id,
                "sessionId": rt.session_id,
                "sequence": 3,
                # NON-retryable: this test asserts the TERMINAL path (T2.1).
                "body": {"code": "DISPLAY_FAULT", "message": "renderer stopped", "retryable": False},
            }
        )

        self.assertEqual(rt.state, "FAILED")
        # Failure should still leave the child with a spoken/visible terminal transition
        # instead of dropping silently into dormant mode.
        self.assertEqual(finished, ["lesson_error"])
        self.assertEqual(released, [])

    async def test_terminal_state_absorbs_late_ack_progress_and_error(self):
        conn = _FakeConn()
        rt = self._runtime(conn=conn)
        rt.state = "COMPLETED"

        await rt.on_lesson_ack(_ack(1, 1))
        await rt.on_lesson_progress(
            _progress(1, {"event": "step_completed", "detail": {"recognizedText": "barn"}})
        )
        await rt.on_lesson_error({"sequence": 1, "body": {"code": "LATE_ERROR"}})

        self.assertEqual(rt.state, "COMPLETED")
        self.assertIsNone(rt.last_error)

    async def test_stale_assignment_or_session_frames_cannot_ack_or_complete_current_runtime(self):
        conn = _FakeConn()
        rt = self._runtime(conn=conn)
        await rt.start()
        sent_before = list(conn.websocket.sent)

        stale_ack = _ack(1, 1)
        stale_ack["assignmentId"] = "old-assignment"
        stale_ack["sessionId"] = "old-session"
        await rt.on_lesson_ack(stale_ack)

        self.assertEqual(conn.websocket.sent, sent_before)
        self.assertIn(1, rt._outstanding)
        self.assertEqual(rt.state, "PRELOADING")

        await rt.on_lesson_error(
            {
                "type": "lesson_error",
                "assignmentId": "old-assignment",
                "sessionId": "old-session",
                "sequence": 1,
                "body": {"code": "OLD_DISPLAY_FAULT", "message": "old frame"},
            }
        )
        self.assertIsNone(rt.last_error)
        self.assertEqual(rt.state, "PRELOADING")

        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        frame = self._sent_frames(conn)[-1]
        self.assertEqual(frame["type"], "lesson_step")

        stale_progress = _progress(
            3,
            {"event": "step_completed", "stepType": frame["body"]["stepType"], "result": "success", "detail": {"recognizedText": "barn"}},
            step_id=frame["stepId"],
        )
        stale_progress["assignmentId"] = "old-assignment"
        stale_progress["sessionId"] = "old-session"
        await rt.on_lesson_progress(stale_progress)

        self.assertFalse(rt._step_completed)
        self.assertEqual(rt._steps_completed, 0)
        self.assertEqual([f["type"] for f in self._sent_frames(conn)], ["lesson_prepare", "lesson_start", "lesson_step"])

    async def test_duplicate_ack_sequence_is_ignored_before_correlation(self):
        conn = _FakeConn()
        rt = self._runtime(conn=conn)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt.on_lesson_ack(_ack(1, 1))

        await rt._preload_task

        self.assertEqual(rt.state, "READY")
        self.assertEqual([f["type"] for f in self._sent_frames(conn)], ["lesson_prepare", "lesson_start"])

    async def test_timeout_task_returns_when_runtime_is_no_longer_running(self):
        import asyncio

        gate = asyncio.Event()

        async def gated_sleep(_timeout_sec):
            await gate.wait()

        rt = self._runtime(sleep=gated_sleep)
        rt.state = "COMPLETED"
        rt._start_step_timeout(3, "s4", 1.0)
        timeout_task = rt._step_timeout_task

        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(rt.state, "COMPLETED")
        self.assertIsNone(rt.last_error)
        self.assertTrue(timeout_task.done())

    async def test_child_response_timeout_task_returns_when_runtime_is_no_longer_running(self):
        import asyncio

        sleeper = _GatedSleep()
        rt = self._runtime(sleep=sleeper)
        rt.state = "RUNNING"
        rt._step = {"id": "s4", "type": "listen", "completionClass": "interactive"}
        rt._step_id = "s4"
        rt._step_passive = False
        rt._step_acked = True
        rt._step_completed = False
        rt._start_child_response_timeout()
        await asyncio.sleep(0)
        timeout_task = rt._child_response_timeout_task

        rt.state = "COMPLETED"
        sleeper.release_latest()
        await asyncio.sleep(0)

        self.assertEqual(rt.state, "COMPLETED")
        self.assertIsNone(rt.last_error)
        self.assertTrue(timeout_task.done())

    def test_child_response_timeout_config_edges_are_stable(self):
        conn = _FakeConn()
        conn.config = {"lesson": {"child_response_timeout_sec": "bad", "max_no_answer_attempts": "bad"}}
        rt = self._runtime(conn=conn)
        rt._step = {}

        self.assertEqual(rt._child_response_timeout_sec(), 12.0)
        self.assertEqual(rt._max_child_response_timeouts(), 2)

        conn.config = {"lesson": {"child_response_timeout_sec": "0", "max_no_answer_attempts": "0"}}
        self.assertEqual(rt._child_response_timeout_sec(), 12.0)
        self.assertEqual(rt._max_child_response_timeouts(), 1)

        conn.config = {"lesson": {"child_response_timeout_sec": "inf"}}
        self.assertEqual(rt._child_response_timeout_sec(), 12.0)

        rt._step = {"responseTimeoutSec": "inf"}
        self.assertEqual(rt._child_response_timeout_sec(), 12.0)

        rt._step = {"maxNoAnswerAttempts": float("inf")}
        self.assertEqual(rt._max_child_response_timeouts(), 2)

        rt._step = {"childResponseTimeoutSec": "5", "maxNoAnswerAttempts": "3"}
        self.assertEqual(rt._child_response_timeout_sec(), 5.0)
        self.assertEqual(rt._max_child_response_timeouts(), 3)

    def test_passive_dwell_rejects_infinite_step_and_config_values(self):
        conn = _FakeConn()
        conn.config = {"lesson": {"passive_step_dwell_sec": "inf"}}
        rt = self._runtime(conn=conn)
        rt._step = {}

        self.assertEqual(rt._passive_dwell_sec(), 0.0)

        rt._step = {"dwellSec": "inf"}
        self.assertEqual(rt._passive_dwell_sec(), 0.0)

        rt._step = {"dwellSec": "0.25"}
        self.assertEqual(rt._passive_dwell_sec(), 0.25)

    def test_frame_ack_timeout_rejects_infinite_config_values(self):
        conn = _FakeConn()
        rt = self._runtime(conn=conn)

        conn.config = {"lesson": {"frame_ack_timeout_sec": "inf"}}
        self.assertEqual(rt._frame_ack_timeout_sec(), 12.0)

        conn.config = {"lesson": {"ack_timeout_sec": "inf"}}
        self.assertEqual(rt._frame_ack_timeout_sec(), 12.0)

        conn.config = {"lesson": {"frame_ack_timeout_sec": "0"}}
        self.assertEqual(rt._frame_ack_timeout_sec(), 0.0)

        conn.config = {"lesson": {"frame_ack_timeout_sec": "0.25"}}
        self.assertEqual(rt._frame_ack_timeout_sec(), 0.25)

        conn.config = {"lesson": {"frame_ack_max_retries": float("inf")}}
        self.assertEqual(rt._frame_ack_max_retries(), 1)

    def test_runtime_helper_edges_are_stable(self):
        from core.lesson.runtime import (
            _coerce_ack_seq,
            _assignment_metadata_errors,
            _is_passive_step,
            _positive_int,
            _set_lesson_start_status,
            lesson_asset_public_base_url,
            parse_manifest_checksum,
        )

        class _RejectingStatusConn:
            @property
            def lesson_start_status(self):
                return None

            @lesson_start_status.setter
            def lesson_start_status(self, _value):
                raise RuntimeError("status unavailable")

        self.assertFalse(_is_passive_step(None))
        self.assertIsNone(_coerce_ack_seq(True))
        self.assertEqual(_coerce_ack_seq(" 3 "), 3)
        self.assertIsNone(_coerce_ack_seq("not-a-seq"))
        self.assertEqual(parse_manifest_checksum(None), "")
        self.assertEqual(parse_manifest_checksum('"lesson-7-espTft-9b1f7c2a"'), "9b1f7c2a")
        self.assertEqual(parse_manifest_checksum('"lesson-7-pi-tft-9b1f7c2a"'), "9b1f7c2a")
        self.assertEqual(parse_manifest_checksum("malformed"), "")
        self.assertIsNone(_positive_int(True))
        self.assertEqual(_positive_int(" 8 "), 8)
        self.assertIsNone(_positive_int("not-a-version"))
        self.assertIsNone(_positive_int("0"))
        self.assertEqual(
            _assignment_metadata_errors(
                {
                    "assignmentId": " ",
                    "lessonId": 17,
                    "profile": "",
                    "manifestChecksum": "",
                    "assignmentVersion": False,
                    "lessonVersion": "bad",
                }
            ),
            ["assignmentId", "lessonId", "profile", "manifestChecksum", "assignmentVersion", "lessonVersion"],
        )
        _set_lesson_start_status(_RejectingStatusConn(), "IGNORED")
        self.assertEqual(
            lesson_asset_public_base_url(
                {"lesson": {"asset_public_base_url": "https://cdn.test/root/"}}
            ),
            "https://cdn.test/root",
        )
        self.assertEqual(
            lesson_asset_public_base_url(
                {"server": {"vision_explain": "http://robot.local/mcp/vision/explain"}}
            ),
            "http://robot.local",
        )
        self.assertEqual(lesson_asset_public_base_url({"server": "bad"}), "")

        rt = self._runtime()
        self.assertEqual(rt._invalid_lesson_step_scene_reason(None), "scene")
        self.assertEqual(
            rt._invalid_lesson_step_scene_reason(
                {"backgroundScene": None, "teachingObject": {}, "robotOverlay": {}}
            ),
            "backgroundScene",
        )
        self.assertEqual(
            rt._invalid_lesson_step_scene_reason(
                {"backgroundScene": {}, "teachingObject": None, "robotOverlay": {}}
            ),
            "teachingObject",
        )
        self.assertEqual(
            rt._invalid_lesson_step_scene_reason(
                {"backgroundScene": {}, "teachingObject": {}, "robotOverlay": None}
            ),
            "robotOverlay",
        )
        self.assertEqual(
            rt._invalid_lesson_step_scene_reason(
                {"backgroundScene": {"poster": {}}, "teachingObject": {}, "robotOverlay": {}}
            ),
            "backgroundScene.poster.src",
        )
        self.assertIsNone(
            rt._invalid_lesson_step_scene_reason(
                {
                    "backgroundScene": {"poster": {"src": "p.jpg"}},
                    "teachingObject": {"asset": {"src": "barn.png"}},
                    "robotOverlay": {"asset": {"src": "robot.png"}},
                }
            )
        )

        rt._ensure_robot_overlay_asset_source(None)
        scene_without_atlas = {"robotOverlay": {}}
        rt._ensure_robot_overlay_asset_source(scene_without_atlas)
        self.assertEqual(scene_without_atlas, {"robotOverlay": {}})
        scene_with_atlas = {"robotOverlay": {"atlas": {"image": "bright-teach.png"}}}
        rt._ensure_robot_overlay_asset_source(scene_with_atlas)
        self.assertEqual(
            scene_with_atlas["robotOverlay"]["asset"],
            {"key": "robotOverlay.asset", "src": "bright-teach.png"},
        )

    async def test_runtime_edge_inputs_do_not_break_lesson_progression(self):
        conn = _FakeConn()
        manifest = _build_manifest()
        manifest["assets"] = [{"id": "unused", "critical": False}]
        manifest["steps"][0]["timeoutSec"] = {"bad": "timeout"}
        manifest["steps"][0]["scene"] = None
        rt = self._runtime(
            conn=conn,
            manifest=manifest,
            min_step_timeout_sec="bad-floor",
        )

        self.assertEqual(rt._critical_assets_payload(), [])
        self.assertIsNone(rt._scene_with_cached_asset_urls(None))
        self.assertEqual(await rt._accept_inbound(None), "ok")
        self.assertEqual(await rt._accept_inbound(1), "ok")
        self.assertEqual(await rt._accept_inbound(1), "duplicate")
        manifest["steps"][0]["scene"] = _build_manifest()["steps"][0]["scene"]

        legacy_manifest = _build_manifest()
        legacy_manifest["steps"] = [{"id": "s4", "prompt": "legacy prompt"}]
        legacy_rt = self._runtime(manifest=legacy_manifest)
        self.assertEqual(legacy_rt._select_steps(), legacy_manifest["steps"])

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 2))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 3))

        step = [f for f in self._sent_frames(conn) if f["type"] == "lesson_step"][-1]
        self.assertIsInstance(step["body"]["scene"], dict)
        self.assertEqual(rt.state, "RUNNING")

    def test_sd_pack_source_validation_helper_edges(self):
        conn = _FakeConn()
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        rt = self._runtime(conn=conn)

        self.assertFalse(rt._is_sd_asset_pack_source(None))
        self.assertFalse(rt._is_sd_asset_pack_source("   "))
        self.assertFalse(rt._is_sd_asset_pack_source("sd://other-root/asset.png"))
        self.assertEqual(rt._required_lesson_step_asset_nodes(None), [])
        self.assertEqual(
            rt._invalid_lesson_step_scene_reason(
                {
                    "backgroundScene": {"poster": {"src": 123}},
                    "teachingObject": {"asset": {"src": "sd://sdcard/tbot/lesson-assets/cache/object"}},
                    "robotOverlay": {"asset": {"src": "sd://sdcard/tbot/lesson-assets/cache/overlay"}},
                }
            ),
            "backgroundScene.poster.src",
        )
        self.assertEqual(
            rt._invalid_lesson_step_scene_reason(
                {
                    "backgroundScene": {"poster": {"src": "sd://other-root/cache/poster"}},
                    "teachingObject": {"asset": {"src": "sd://sdcard/tbot/lesson-assets/cache/object"}},
                    "robotOverlay": {"asset": {"src": "sd://sdcard/tbot/lesson-assets/cache/overlay"}},
                }
            ),
            "backgroundScene.poster.src",
        )

        class _NoResolverAssetCache(_FakeAssetCache):
            local_pack_url_for_source = None

        no_resolver_rt = self._runtime(conn=conn, asset_cache=_NoResolverAssetCache(ready=True))
        scene = {"backgroundScene": {}, "teachingObject": {}, "robotOverlay": {}}
        no_resolver_rt._rewrite_required_sd_pack_layer_sources(scene)
        self.assertEqual(scene, {"backgroundScene": {}, "teachingObject": {}, "robotOverlay": {}})

    async def test_oversized_lesson_step_frame_fails_before_sending_to_firmware(self):
        conn = _FakeConn()
        conn.voice_provider = _RecordingLessonVoiceProvider()
        manifest = _build_manifest()
        manifest["steps"][0]["prompt"] = "barn " * 5000
        rt = self._runtime(conn=conn, manifest=manifest)

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        sent = self._sent_frames(conn)
        self.assertEqual([f["type"] for f in sent], ["lesson_prepare", "lesson_start", "lesson_error"])
        self.assertEqual(sent[-1]["body"]["code"], "LESSON_FRAME_TOO_LARGE")
        self.assertLess(len(conn.websocket.sent[-1].encode("utf-8")), 2048)
        self.assertEqual(conn.voice_provider.prompts, [])
        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "LESSON_FRAME_TOO_LARGE")

    async def test_oversized_story_metadata_fails_before_sending_to_firmware(self):
        conn = _FakeConn()
        conn.voice_provider = _RecordingLessonVoiceProvider()
        manifest = _build_manifest()
        manifest["steps"][0]["storyText"] = "TeeBot story " * 2000
        rt = self._runtime(conn=conn, manifest=manifest)

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        sent = self._sent_frames(conn)
        self.assertEqual([f["type"] for f in sent], ["lesson_prepare", "lesson_start", "lesson_error"])
        self.assertEqual(sent[-1]["body"]["code"], "LESSON_FRAME_TOO_LARGE")
        self.assertEqual(sent[-1]["body"]["context"], {"frameType": "lesson_step", "stepId": "s4", "maxBytes": 16384})
        self.assertLess(len(conn.websocket.sent[-1].encode("utf-8")), 2048)
        self.assertEqual(conn.voice_provider.prompts, [])
        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "LESSON_FRAME_TOO_LARGE")

    async def test_oversized_lesson_prepare_asset_pack_fails_before_sending_to_firmware(self):
        class _HugeAssetPackCache(_FakeAssetCache):
            def asset_pack_manifest(self, *, assignment_version, lesson_id, lesson_version, manifest_checksum):
                pack = super().asset_pack_manifest(
                    assignment_version=assignment_version,
                    lesson_id=lesson_id,
                    lesson_version=lesson_version,
                    manifest_checksum=manifest_checksum,
                )
                pack["assets"] = [
                    {
                        "key": f"teachingObject.{'extra' * 50}{i}",
                        "path": f"objects/extra-{i}.png",
                        "sha256": "a" * 64,
                        "mediaType": "image/png",
                        "critical": False,
                        "localPath": "sd://sdcard/tbot/lesson-assets/" + ("x" * 256) + f"/{i}.png",
                        "state": "READY",
                        "checksumOk": True,
                        "size": 1024,
                    }
                    for i in range(80)
                ]
                return pack

        conn = _FakeConn()
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        rt = self._runtime(conn=conn, asset_cache=_HugeAssetPackCache(ready=True))

        await rt.start()

        sent = self._sent_frames(conn)
        self.assertEqual([f["type"] for f in sent], ["lesson_error"])
        self.assertEqual(sent[0]["body"]["code"], "LESSON_FRAME_TOO_LARGE")
        self.assertEqual(sent[0]["body"]["context"]["frameType"], "lesson_prepare")
        self.assertLess(len(conn.websocket.sent[0].encode("utf-8")), 2048)
        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "LESSON_FRAME_TOO_LARGE")

    async def test_oversized_lesson_prepare_critical_assets_fails_before_sending_to_firmware(self):
        manifest = _build_manifest()
        base_asset = manifest["assets"][0]
        manifest["assets"] = [
            {
                **base_asset,
                "id": f"backgroundScene.extra{i}",
                "path": "assets/background/" + ("long-name-" * 20) + f"{i}.jpg",
                "critical": True,
            }
            for i in range(90)
        ]
        conn = _FakeConn()
        rt = self._runtime(conn=conn, manifest=manifest)

        await rt.start()

        sent = self._sent_frames(conn)
        self.assertEqual([f["type"] for f in sent], ["lesson_error"])
        self.assertEqual(sent[0]["body"]["code"], "LESSON_FRAME_TOO_LARGE")
        self.assertEqual(sent[0]["body"]["context"]["frameType"], "lesson_prepare")
        self.assertLess(len(conn.websocket.sent[0].encode("utf-8")), 2048)
        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "LESSON_FRAME_TOO_LARGE")

    async def test_missing_required_three_layer_sources_fail_before_sending_lesson_step(self):
        conn = _FakeConn()
        conn.voice_provider = _RecordingLessonVoiceProvider()
        manifest = _build_manifest()
        del manifest["steps"][0]["scene"]["robotOverlay"]
        rt = self._runtime(conn=conn, manifest=manifest)

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        sent = self._sent_frames(conn)
        self.assertEqual([f["type"] for f in sent], ["lesson_prepare", "lesson_start", "lesson_error"])
        self.assertEqual(sent[-1]["body"]["code"], "LESSON_FRAME_INVALID")
        self.assertEqual(sent[-1]["body"]["context"]["stepId"], "s4")
        self.assertEqual(conn.voice_provider.prompts, [])
        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "LESSON_FRAME_INVALID")

    async def test_http_missing_verified_layer_mapping_fails_before_sending_lesson_step(self):
        conn = _FakeConn()
        conn.voice_provider = _RecordingLessonVoiceProvider()
        manifest = _build_manifest()
        scene = manifest["steps"][0]["scene"]
        local_urls = {
            scene["backgroundScene"]["poster"]["src"]: "http://robot.local/cache/backgroundScene.poster",
            scene["teachingObject"]["asset"]["src"]: "http://robot.local/cache/teachingObject.barn",
        }
        rt = self._runtime(conn=conn, manifest=manifest, asset_cache=_FakeAssetCache(ready=True, local_urls=local_urls))

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        sent = self._sent_frames(conn)
        self.assertEqual([f["type"] for f in sent], ["lesson_prepare", "lesson_start", "lesson_error"])
        self.assertEqual(sent[-1]["body"]["code"], "LESSON_FRAME_INVALID")
        self.assertEqual(sent[-1]["body"]["context"]["reason"], "robotOverlay.asset.src")
        self.assertEqual(conn.voice_provider.prompts, [])
        self.assertEqual(rt.state, "FAILED")

    async def test_sd_pack_missing_verified_layer_mapping_fails_before_sending_lesson_step(self):
        conn = _FakeConn()
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        conn.voice_provider = _RecordingLessonVoiceProvider()
        manifest = _build_manifest()
        scene = manifest["steps"][0]["scene"]
        local_urls = {
            scene["backgroundScene"]["poster"]["src"]: "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/backgroundScene.poster",
            scene["teachingObject"]["asset"]["src"]: "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/teachingObject.barn",
        }
        rt = self._runtime(conn=conn, manifest=manifest, asset_cache=_FakeAssetCache(ready=True, local_urls=local_urls))

        await rt.start()
        await rt.on_lesson_ack(
            _ack(
                1,
                1,
                extra={
                    "acks": 1,
                    "rendered": True,
                    "degraded": False,
                    "assetPack": {"ready": True, "cacheKey": rt.asset_cache.cache_key},
                },
            )
        )
        await rt.on_lesson_ack(_ack(2, 2))

        sent = self._sent_frames(conn)
        self.assertEqual([f["type"] for f in sent], ["lesson_prepare", "lesson_start", "lesson_error"])
        self.assertEqual(sent[-1]["body"]["code"], "LESSON_FRAME_INVALID")
        self.assertEqual(sent[-1]["body"]["context"]["reason"], "robotOverlay.asset.src")
        self.assertEqual(conn.voice_provider.prompts, [])
        self.assertEqual(rt.state, "FAILED")

    async def test_sd_pack_rejects_manifest_sd_layer_source_without_cache_attestation(self):
        conn = _FakeConn()
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        conn.voice_provider = _RecordingLessonVoiceProvider()
        manifest = _build_manifest()
        scene = manifest["steps"][0]["scene"]
        local_urls = {
            scene["backgroundScene"]["poster"]["src"]: "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/backgroundScene.poster",
            scene["teachingObject"]["asset"]["src"]: "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/teachingObject.barn",
        }
        fake_overlay_local = (
            "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/robotOverlay.teach"
        )
        scene["robotOverlay"]["atlas"]["image"] = fake_overlay_local
        scene["robotOverlay"]["asset"]["src"] = fake_overlay_local
        rt = self._runtime(conn=conn, manifest=manifest, asset_cache=_FakeAssetCache(ready=True, local_urls=local_urls))

        await rt.start()
        await rt.on_lesson_ack(
            _ack(
                1,
                1,
                extra={
                    "acks": 1,
                    "rendered": True,
                    "degraded": False,
                    "assetPack": {"ready": True, "cacheKey": rt.asset_cache.cache_key},
                },
            )
        )
        await rt.on_lesson_ack(_ack(2, 2))

        sent = self._sent_frames(conn)
        self.assertEqual([f["type"] for f in sent], ["lesson_prepare", "lesson_start", "lesson_error"])
        self.assertEqual(sent[-1]["body"]["code"], "LESSON_FRAME_INVALID")
        self.assertEqual(sent[-1]["body"]["context"]["reason"], "robotOverlay.asset.src")
        self.assertEqual(conn.voice_provider.prompts, [])
        self.assertEqual(rt.state, "FAILED")

    async def test_oversized_non_step_frame_is_rejected_by_emit_guard(self):
        conn = _FakeConn()
        rt = self._runtime(conn=conn)

        await rt._emit("lesson_stop", body={"reason": "x" * 20000})

        sent = self._sent_frames(conn)
        self.assertEqual([f["type"] for f in sent], ["lesson_error"])
        self.assertEqual(sent[0]["body"]["code"], "LESSON_FRAME_TOO_LARGE")
        self.assertEqual(rt.state, "FAILED")

    async def test_emit_step_does_not_start_timeout_if_emit_fails_defensively(self):
        rt = self._runtime()
        timeouts = []

        async def fail_emit(*_args, **_kwargs):
            rt.state = "FAILED"
            return 99

        def record_timeout(*args):
            timeouts.append(args)

        rt._emit = fail_emit
        rt._start_step_timeout = record_timeout

        await rt._emit_step()

        self.assertEqual(timeouts, [])

    async def test_emit_step_rejects_infinite_step_timeout_values(self):
        manifest = _build_manifest()
        manifest["steps"][0]["timeoutSec"] = "inf"
        rt = self._runtime(manifest=manifest)
        timeouts = []

        def record_timeout(_seq, _step_id, timeout_sec):
            timeouts.append(timeout_sec)

        rt._start_step_timeout = record_timeout

        await rt._emit_step()

        self.assertEqual(timeouts, [12.0])

    async def test_emit_step_sends_sanitized_step_timeout(self):
        conn = _FakeConn()
        manifest = _build_manifest()
        manifest["steps"][0]["timeoutSec"] = float("inf")
        rt = self._runtime(conn=conn, manifest=manifest)
        rt._start_step_timeout = lambda *_args: None

        await rt._emit_step()

        frame = json.loads(conn.websocket.sent[-1])
        json.dumps(frame, allow_nan=False)
        self.assertEqual(frame["body"]["timeoutSec"], 12.0)

    async def test_emit_step_rejects_infinite_step_timeout_floor(self):
        rt = self._runtime(min_step_timeout_sec="inf")
        timeouts = []

        def record_timeout(_seq, _step_id, timeout_sec):
            timeouts.append(timeout_sec)

        rt._start_step_timeout = record_timeout

        await rt._emit_step()

        self.assertEqual(timeouts, [12.0])

    def test_no_forwarder_and_missing_logger_are_noops(self):
        class _BadLogger:
            def bind(self, **_kwargs):
                raise RuntimeError("logger unavailable")

        rt = self._runtime()
        rt.forwarder = None
        rt._forward({"type": "step_completed"})
        rt.logger = None
        rt._log("info", "ignored")
        rt.logger = _BadLogger()
        rt._log("info", "ignored")

    # 1) happy thread byte-consistent with the fixture ---------------------------

    async def test_lesson_prepare_manifest_ref_pins_assignment_lesson_version(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        rt = self._runtime(conn=conn)

        await rt.start()

        prepare = self._sent_frames(conn)[0]
        manifest_ref = prepare["body"]["manifestRef"]
        self.assertEqual(manifest_ref["lessonVersion"], 3)
        self.assertIn("version=3", manifest_ref["url"])
        self.assertIn("profile=espTft", manifest_ref["url"])

    async def test_child_response_before_interactive_step_ack_is_ignored_until_robot_is_ready(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.voice_provider = _RecordingLessonVoiceProvider()
        forwarder = _FakeForwarder()
        rt = self._runtime(conn=conn, forwarder=forwarder)

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        self.assertIsNotNone(rt._preload_task)
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        step = [f for f in self._sent_frames(conn) if f["type"] == "lesson_step"][-1]
        self.assertEqual(step["stepId"], "s4")

        accepted_early = await rt.on_child_response("barn", source="voice_transcript")

        self.assertFalse(accepted_early)
        self.assertEqual(conn.voice_provider.child_response_windows, [])
        self.assertEqual(rt._steps_completed, 0)
        self.assertFalse(
            any(
                batch["events"] and batch["events"][0].get("type") == "step_completed"
                for batch in forwarder.batches
            )
        )

        await rt.on_lesson_ack(_ack(step["sequence"], 3, step_id="s4"))
        self.assertEqual(conn.voice_provider.child_response_windows, [True])
        accepted_after_ack = await rt.on_child_response("barn", source="voice_transcript")

        self.assertTrue(accepted_after_ack)
        self.assertEqual(rt._steps_completed, 1)

    async def test_happy_thread_frames_byte_consistent_with_fixture(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        forwarder = _FakeForwarder()
        rt = self._runtime(conn=conn, forwarder=forwarder)

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))  # prepare-ack
        self.assertIsNotNone(rt._preload_task)
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))  # step-ack
        await rt.on_lesson_progress(
            _progress(
                4,
                {
                    "event": "step_completed",
                    "stepType": "model",
                    "result": "success",
                    "detail": {"tapTargetHit": True, "utterance": "Yes! barn!"},
                },
            )
        )
        await rt.on_lesson_ack(_ack(4, 5))  # stop-ack (env seq 5, body.acks 4)

        sent = self._sent_frames(conn)
        by_type = {f["type"]: f for f in sent}
        self.assertEqual(
            set(by_type),
            {"lesson_prepare", "lesson_start", "lesson_step", "lesson_stop"},
        )

        for ftype in ("lesson_prepare", "lesson_start", "lesson_step", "lesson_stop"):
            got = dict(by_type[ftype])
            got.pop("timestamp")
            want = copy.deepcopy(FIX["frames"][ftype])
            want.pop("timestamp")
            self.assertEqual(got, want, f"{ftype} not byte-consistent with fixture")

        self.assertEqual(rt.state, "COMPLETED")

    async def test_step_body_rewrites_scene_sources_to_preloaded_asset_cache(self):
        manifest = _build_manifest()
        scene = manifest["steps"][0]["scene"]
        poster_source = scene["backgroundScene"]["poster"]["src"]
        object_source = scene["teachingObject"]["asset"]["src"]
        overlay_source = scene["robotOverlay"]["atlas"]["image"]
        cached = {
            poster_source: "https://ota.test/tbot/lesson-assets/cache-token/poster",
            object_source: "https://ota.test/tbot/lesson-assets/cache-token/object",
            overlay_source: "https://ota.test/tbot/lesson-assets/cache-token/overlay",
        }
        rt = self._runtime(
            manifest=manifest,
            asset_cache=_FakeAssetCache(local_urls=cached),
        )

        body = rt._step_body(manifest["steps"][0])

        self.assertEqual(
            body["scene"]["backgroundScene"]["poster"]["src"],
            cached[poster_source],
        )
        self.assertEqual(
            body["scene"]["teachingObject"]["asset"]["src"],
            cached[object_source],
        )
        self.assertEqual(
            body["scene"]["robotOverlay"]["asset"]["src"],
            cached[overlay_source],
        )
        self.assertEqual(
            manifest["steps"][0]["scene"]["backgroundScene"]["poster"]["src"],
            poster_source,
        )

    async def test_sd_asset_pack_end_to_end_prepare_gate_and_step_local_paths(self):
        manifest = _build_manifest()
        scene = manifest["steps"][0]["scene"]
        poster_source = scene["backgroundScene"]["poster"]["src"]
        object_source = scene["teachingObject"]["asset"]["src"]
        overlay_source = scene["robotOverlay"]["atlas"]["image"]
        local_urls = {
            poster_source: "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/backgroundScene.poster",
            object_source: "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/teachingObject.barn",
            overlay_source: "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/robotOverlay.teach",
        }
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        rt = self._runtime(
            conn=conn,
            manifest=manifest,
            asset_cache=_FakeAssetCache(ready=True, local_urls=local_urls),
        )

        await rt.start()
        prepare = self._sent_frames(conn)[-1]
        self.assertEqual(prepare["type"], "lesson_prepare")
        self.assertEqual(prepare["body"]["assetPack"]["cacheKey"], "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3")
        pack_assets = {asset["key"]: asset for asset in prepare["body"]["assetPack"]["assets"]}
        self.assertEqual(
            set(pack_assets),
            {"backgroundScene.poster", "teachingObject.barn", "robotOverlay.teach"},
        )
        self.assertNotIn("localPath", pack_assets["backgroundScene.poster"])
        self.assertEqual(
            prepare["body"]["assetPack"]["localRoot"],
            "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
        )

        await rt.on_lesson_ack(
            _ack(
                1,
                1,
                extra={
                    "acks": 1,
                    "rendered": True,
                    "assetPack": {"ready": True, "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3"},
                },
            )
        )
        self.assertIsNone(rt._preload_task)

        self.assertEqual(self._sent_frames(conn)[-1]["type"], "lesson_start")
        await rt.on_lesson_ack(_ack(2, 2))
        step = self._sent_frames(conn)[-1]

        self.assertEqual(step["type"], "lesson_step")
        self.assertEqual(step["body"]["scene"]["backgroundScene"]["poster"]["src"], local_urls[poster_source])
        self.assertEqual(step["body"]["scene"]["teachingObject"]["asset"]["src"], local_urls[object_source])
        self.assertEqual(step["body"]["scene"]["robotOverlay"]["asset"]["src"], local_urls[overlay_source])

        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        await rt.on_lesson_progress(
            _progress(
                4,
                {
                    "event": "step_completed",
                    "stepType": "model",
                    "result": "success",
                    "detail": {"recognizedText": "barn"},
                },
            )
        )
        await rt.on_lesson_ack(_ack(4, 5))
        self.assertEqual(rt.state, "COMPLETED")

    def test_prepare_asset_pack_preserves_media_type_for_firmware_file_limits(self):
        from core.lesson.runtime import LessonRuntime

        payload = LessonRuntime._prepare_asset_pack_payload({
            "cacheKey": "lesson/v1-checksum",
            "localRoot": "sd://sdcard/tbot/lesson-assets/lesson/v1-checksum",
            "assets": [{
                "key": "flattenedCinematic.barn-opening",
                "state": "READY",
                "checksumOk": True,
                "size": 2_904_507,
                "mediaType": "video/mp4",
            }],
        })

        self.assertEqual(payload["assets"][0]["mediaType"], "video/mp4")

    async def test_sd_asset_pack_prepare_compacts_verbose_live_pack_under_frame_limit(self):
        class _VerboseLiveAssetPackCache(_FirmwareSyncAssetCache):
            def asset_pack_manifest(
                self, *, assignment_version, lesson_id, lesson_version, manifest_checksum
            ):
                pack = super().asset_pack_manifest(
                    assignment_version=assignment_version,
                    lesson_id=lesson_id,
                    lesson_version=lesson_version,
                    manifest_checksum=manifest_checksum,
                )
                pack["assets"] = [
                    {
                        "key": f"robotOverlay.lesson-asset-{index:02d}@v1",
                        "path": f"lesson-assets/{'a' * 64}",
                        "url": (
                            "http://192.168.1.25:8180/lesson-assets/"
                            + ("b" * 64)
                            + f"?asset={index}"
                        ),
                        "sha256": "c" * 64,
                        "sourceSha256": "d" * 64,
                        "size": 1024 + index,
                        "mediaType": "image/png",
                        "critical": index < 8,
                        "layer": "robotOverlay",
                        "role": "pose",
                        "state": "READY",
                        "checksumOk": True,
                        "localPath": (
                            f"{pack['localRoot']}/"
                            f"robotOverlay.lesson-asset-{index:02d}%40v1"
                        ),
                    }
                    for index in range(20)
                ]
                return pack

        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        rt = self._runtime(conn=conn, asset_cache=_VerboseLiveAssetPackCache(ready=True))

        async def sync_ready(*_args, **_kwargs):
            return {
                "ready": True,
                "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                "manifestChecksum": _manifest_checksum(),
                "downloadedCount": 20,
                "skippedCount": 0,
                "failedCount": 0,
            }

        with patch("core.lesson.runtime.call_mcp_tool", new=sync_ready):
            await rt.start()

        sent = self._sent_frames(conn)
        self.assertEqual([frame["type"] for frame in sent], ["lesson_prepare"])
        self.assertLessEqual(len(conn.websocket.sent[0].encode("utf-8")), 16384)
        self.assertEqual(
            set(sent[0]["body"]["assetPack"]["assets"][0]),
            {"key", "state", "checksumOk", "size", "mediaType"},
        )

    async def test_sd_asset_pack_prepare_supports_publish_budget_maximum_64_assets(self):
        class _MaximumAssetPackCache(_FirmwareSyncAssetCache):
            def asset_pack_manifest(
                self, *, assignment_version, lesson_id, lesson_version, manifest_checksum
            ):
                pack = super().asset_pack_manifest(
                    assignment_version=assignment_version,
                    lesson_id=lesson_id,
                    lesson_version=lesson_version,
                    manifest_checksum=manifest_checksum,
                )
                assets = []
                for index in range(64):
                    key = f"asset-{index:02d}-" + ("k" * 48)
                    local_path = f"{pack['localRoot']}/{quote(key, safe='')}"
                    assets.append(
                        {
                            "key": key,
                            "path": key,
                            "url": f"https://assets.example/{quote(key, safe='')}",
                            "sha256": f"{index:064x}",
                            "size": 1024,
                            "mediaType": "image/png",
                            "critical": True,
                            "state": "READY",
                            "checksumOk": True,
                            "localPath": local_path,
                        }
                    )
                pack["assets"] = assets
                return pack

        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        rt = self._runtime(conn=conn, asset_cache=_MaximumAssetPackCache(ready=True))

        async def sync_ready(*_args, **_kwargs):
            return {
                "ready": True,
                "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                "manifestChecksum": _manifest_checksum(),
                "downloadedCount": 64,
                "skippedCount": 0,
                "failedCount": 0,
            }

        with patch("core.lesson.runtime.call_mcp_tool", new=sync_ready):
            await rt.start()

        self.assertEqual([frame["type"] for frame in self._sent_frames(conn)], ["lesson_prepare"])
        self.assertLessEqual(len(conn.websocket.sent[0].encode("utf-8")), 16384)

    async def test_sd_asset_pack_rewrites_all_layer_image_sources_to_local_paths(self):
        manifest = _build_manifest()
        scene = manifest["steps"][0]["scene"]
        bg_source = scene["backgroundScene"]["poster"]["src"]
        obj_source = scene["teachingObject"]["asset"]["src"]
        scene.setdefault("robotOverlay", {})["asset"] = {
            "key": "robotOverlay.teach",
            "src": "bright-teach.png",
            "sha256": "40f9c095b11a67c023f62847f498cc557e7fcef45762d41787dafffd96a60b34",
        }
        local_urls = {
            bg_source: "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/backgroundScene.poster",
            obj_source: "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/teachingObject.barn",
            "bright-teach.png": "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/robotOverlay.teach",
        }
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        rt = self._runtime(
            conn=conn,
            manifest=manifest,
            asset_cache=_FakeAssetCache(ready=True, local_urls=local_urls),
        )

        body = rt._step_body(manifest["steps"][0])
        rewritten = body["scene"]

        self.assertEqual(rewritten["backgroundScene"]["poster"]["src"], local_urls[bg_source])
        self.assertEqual(rewritten["teachingObject"]["asset"]["src"], local_urls[obj_source])
        self.assertEqual(rewritten["robotOverlay"]["asset"]["src"], local_urls["bright-teach.png"])
        self.assertEqual(rewritten["robotOverlay"]["atlas"]["image"], local_urls["bright-teach.png"])
        _assert_no_inline_media_payload(self, body, path="sd_pack.lesson_step.body")

    async def test_sd_asset_pack_blanks_unresolved_overlay_atlas_image_source(self):
        manifest = _build_manifest()
        scene = manifest["steps"][0]["scene"]
        bg_source = scene["backgroundScene"]["poster"]["src"]
        obj_source = scene["teachingObject"]["asset"]["src"]
        scene["robotOverlay"]["asset"] = {
            "key": "robotOverlay.teach",
            "src": "bright-teach.png",
            "sha256": "40f9c095b11a67c023f62847f498cc557e7fcef45762d41787dafffd96a60b34",
        }
        scene["robotOverlay"]["atlas"] = {"image": "https://cdn.test/poses/bright-teach.png"}
        local_urls = {
            bg_source: "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/backgroundScene.poster",
            obj_source: "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/teachingObject.barn",
            "bright-teach.png": "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/robotOverlay.teach",
        }
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        rt = self._runtime(
            conn=conn,
            manifest=manifest,
            asset_cache=_FakeAssetCache(ready=True, local_urls=local_urls),
        )

        body = rt._step_body(manifest["steps"][0])
        rewritten = body["scene"]

        self.assertEqual(rewritten["robotOverlay"]["asset"]["src"], local_urls["bright-teach.png"])
        self.assertEqual(rewritten["robotOverlay"]["atlas"]["image"], "")
        self.assertNotIn("https://cdn.test", json.dumps(body))

    async def test_sd_asset_pack_preloads_and_materializes_before_prepare(self):
        class _MaterializingAssetCache(_FakeAssetCache):
            def __init__(self):
                super().__init__(ready=False)
                self.preload_calls = 0

            async def preload(self):
                self.preload_calls += 1
                self._ready = True
                return True

        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        cache = _MaterializingAssetCache()
        rt = self._runtime(conn=conn, asset_cache=cache)

        await rt.start()

        self.assertEqual(cache.preload_calls, 1)
        prepare = self._sent_frames(conn)[-1]
        self.assertEqual(prepare["type"], "lesson_prepare")
        self.assertTrue(prepare["body"]["assetPack"]["ready"])
        pack_assets = {asset["key"]: asset for asset in prepare["body"]["assetPack"]["assets"]}
        self.assertEqual(
            set(pack_assets),
            {"backgroundScene.poster", "teachingObject.barn", "robotOverlay.teach"},
        )
        self.assertTrue(all(asset["state"] == "READY" for asset in pack_assets.values()))

    async def test_sd_asset_pack_calls_robot_mcp_sync_before_prepare(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {
            "lesson": {
                "asset_delivery_mode": "sd_pack",
                "asset_pack_mount_root": "/opt/tbot-esp32-server/data/lesson-packs",
            }
        }
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        calls = []
        coordinator_calls = []

        async def capture_coordinator(
            conn_arg, cache_key, operation, *, foreground=False
        ):
            coordinator_calls.append((conn_arg, cache_key, foreground))
            return await operation()

        async def capture_call(conn_arg, mcp_client, tool_name, args, timeout=30):
            calls.append(
                {
                    "conn": conn_arg,
                    "mcp_client": mcp_client,
                    "tool_name": tool_name,
                    "args": copy.deepcopy(args),
                    "timeout": timeout,
                    "sent_before_call": list(conn.websocket.sent),
                }
            )
            return json.dumps(
                {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                    "manifestChecksum": _manifest_checksum(),
                    "downloadedCount": 3,
                    "skippedCount": 0,
                    "failedCount": 0,
                }
            )

        asset_cache = _FirmwareSyncAssetCache(ready=True)
        rt = self._runtime(conn=conn, asset_cache=asset_cache)

        with patch("core.lesson.runtime.call_mcp_tool", new=capture_call), patch(
            "core.lesson.runtime.request_sd_pack_sync", new=capture_coordinator
        ):
            await rt.start()

        self.assertEqual(
            coordinator_calls,
            [(conn, asset_cache.cache_key, True)],
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["conn"], conn)
        self.assertEqual(calls[0]["mcp_client"], conn.mcp_client)
        self.assertEqual(calls[0]["tool_name"], "self_lesson_assets_sync_to_sd")
        self.assertEqual(calls[0]["sent_before_call"], [])
        self.assertGreaterEqual(calls[0]["timeout"], 60)
        pack = calls[0]["args"]["assetPack"]
        self.assertEqual(pack["cacheKey"], "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3")
        self.assertTrue(pack["ready"])
        self.assertEqual(
            {asset["key"] for asset in pack["assets"]},
            {"backgroundScene.poster", "teachingObject.barn", "robotOverlay.teach"},
        )
        self.assertTrue(pack["localRoot"].startswith("/sdcard/tbot/lesson-assets/"))
        self.assertTrue(
            all(asset["sdPath"].startswith(pack["localRoot"] + "/") for asset in pack["assets"])
        )
        self.assertTrue(all("localPath" not in asset for asset in pack["assets"]))
        self.assertTrue(all("url" not in asset for asset in pack["assets"]))
        prepare = self._sent_frames(conn)[-1]
        self.assertEqual(prepare["type"], "lesson_prepare")
        self.assertTrue(prepare["body"]["assetPack"]["localRoot"].startswith("sd://"))
        render_root = prepare["body"]["assetPack"]["localRoot"].rstrip("/")
        self.assertTrue(
            all(
                (
                    asset.get("localPath")
                    or f"{render_root}/{quote(asset['key'], safe='')}"
                ).startswith("sd://")
                for asset in prepare["body"]["assetPack"]["assets"]
            )
        )

    async def test_sd_asset_pack_cold_sync_emits_authoritative_attestation_markers(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        conn.logger = _CapturingLogger()

        async def cold_sync(*_args, **_kwargs):
            return {
                "ready": True,
                "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                "manifestChecksum": _manifest_checksum(),
                "downloadedCount": 3,
                "skippedCount": 0,
                "failedCount": 0,
            }

        rt = self._runtime(conn=conn, asset_cache=_FirmwareSyncAssetCache(ready=True))
        with patch("core.lesson.runtime.call_mcp_tool", new=cold_sync):
            self.assertTrue(await rt._sync_sd_asset_pack_to_robot())

        messages = "\n".join(message for _level, message in conn.logger.events)
        self.assertIn("lesson_preload_ready", messages)
        self.assertIn("checksum_verified", messages)
        self.assertIn("downloadedCount=3", messages)
        self.assertIn("skippedCount=0", messages)
        self.assertIn("reusedCount=0", messages)
        self.assertIn("assetCount=3", messages)
        duration_match = re.search(r"durationMs=(\d+)", messages)
        self.assertIsNotNone(duration_match)
        self.assertGreaterEqual(int(duration_match.group(1)), 1)
        self.assertNotIn("asset_cache_hit", messages)

    async def test_sd_asset_pack_accepts_and_logs_reused_attestation_count(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        conn.logger = _CapturingLogger()

        async def reused_sync(*_args, **_kwargs):
            return {
                "ready": True,
                "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                "manifestChecksum": _manifest_checksum(),
                "downloadedCount": 2,
                "skippedCount": 0,
                "reusedCount": 1,
                "failedCount": 0,
            }

        rt = self._runtime(conn=conn, asset_cache=_FirmwareSyncAssetCache(ready=True))
        with patch("core.lesson.runtime.call_mcp_tool", new=reused_sync):
            self.assertTrue(await rt._sync_sd_asset_pack_to_robot())

        messages = "\n".join(message for _level, message in conn.logger.events)
        self.assertIn("lesson_preload_ready", messages)
        self.assertIn("reusedCount=1", messages)

    async def test_sd_sample_uses_fixed_advertised_sync_tool(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack"}}
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        conn.mcp_client.tools["self_lesson_assets_sync_sample_to_sd"] = {}
        cache = SampleAssetCache(sd_pack=True, asset_base="https://cdn.example/sample")
        digests = {
            asset["path"]: asset["sha256"]
            for asset in cache.asset_pack_manifest(
                assignment_version=1,
                lesson_id="sample-barn-say-it",
                lesson_version=1,
                manifest_checksum="a" * 64,
            )["assets"]
        }
        calls = []

        async def capture_call(_conn, _client, tool_name, args, timeout=30):
            calls.append((tool_name, copy.deepcopy(args), timeout))
            return {
                "directory": "/sdcard/tbot/lesson-assets/sample-barn",
                "downloadedCount": 6,
                "files": [
                    {"file": name, "bytes": 1, "sha256": digests[name]}
                    for name in cache.firmware_sample_sync_files()
                ],
            }

        rt = self._runtime(conn=conn, asset_cache=cache)
        with patch("core.lesson.runtime.call_mcp_tool", new=capture_call):
            self.assertTrue(await rt._sync_sd_asset_pack_to_robot())

        self.assertEqual(calls[0][0], "self_lesson_assets_sync_sample_to_sd")
        self.assertEqual(calls[0][1], {"base_url": "https://cdn.example/sample"})

    async def test_sd_sample_uses_fixed_raw_sync_tool_when_unlisted(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack"}}
        conn.mcp_client = _MissingLessonAssetToolMcpClient()
        cache = SampleAssetCache(sd_pack=True, asset_base="https://cdn.example/sample")
        digests = {
            asset["path"]: asset["sha256"]
            for asset in cache.asset_pack_manifest(
                assignment_version=1,
                lesson_id="sample-barn-say-it",
                lesson_version=1,
                manifest_checksum="a" * 64,
            )["assets"]
        }
        calls = []

        async def raw_call(_conn, _client, tool_name, args, timeout=30):
            calls.append((tool_name, copy.deepcopy(args), timeout))
            return json.dumps({
                "directory": "/sdcard/tbot/lesson-assets/sample-barn",
                "downloadedCount": 6,
                "files": [
                    {"file": name, "bytes": 1, "sha256": digests[name]}
                    for name in cache.firmware_sample_sync_files()
                ],
            })

        rt = self._runtime(conn=conn, asset_cache=cache)
        with patch("core.api.device_mcp_admin_handler._call_raw_mcp_tool", new=raw_call):
            self.assertTrue(await rt._sync_sd_asset_pack_to_robot())

        self.assertEqual(calls[0][0], "self.lesson_assets.sync_sample_to_sd")
        self.assertEqual(calls[0][1], {"base_url": "https://cdn.example/sample"})

    async def test_sd_sample_rejects_malformed_fixed_sync_result(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack"}}
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        conn.mcp_client.tools["self_lesson_assets_sync_sample_to_sd"] = {}
        cache = SampleAssetCache(sd_pack=True, asset_base="https://cdn.example/sample")
        rt = self._runtime(conn=conn, asset_cache=cache)

        async def malformed(*_args, **_kwargs):
            return {"directory": "/wrong", "downloadedCount": 6, "files": []}

        with patch("core.lesson.runtime.call_mcp_tool", new=malformed):
            self.assertFalse(await rt._sync_sd_asset_pack_to_robot())

    async def test_sd_sample_rejects_invalid_base_before_any_tool_dispatch(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack"}}
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        conn.mcp_client.tools["self_lesson_assets_sync_sample_to_sd"] = {}
        cache = SampleAssetCache(sd_pack=True, asset_base="file:///tmp/sample")
        calls = []

        async def unexpected_dispatch(*_args, **_kwargs):
            calls.append((_args, _kwargs))
            return {"directory": "/wrong", "downloadedCount": 0, "files": []}

        rt = self._runtime(conn=conn, asset_cache=cache)
        with patch("core.lesson.runtime.call_mcp_tool", new=unexpected_dispatch):
            self.assertFalse(await rt._sync_sd_asset_pack_to_robot())
        self.assertEqual(calls, [])

    async def test_sd_sample_normalizes_only_trailing_slashes_before_dispatch(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack"}}
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        conn.mcp_client.tools["self_lesson_assets_sync_sample_to_sd"] = {}
        cache = SampleAssetCache(sd_pack=True, asset_base="https://cdn.example/sample///")
        digests = {
            asset["path"]: asset["sha256"]
            for asset in cache.asset_pack_manifest(
                assignment_version=1,
                lesson_id="sample-barn-say-it",
                lesson_version=1,
                manifest_checksum="a" * 64,
            )["assets"]
        }
        calls = []

        async def capture_dispatch(_conn, _client, tool_name, args, timeout=30):
            calls.append((tool_name, copy.deepcopy(args), timeout))
            return {
                "directory": "/sdcard/tbot/lesson-assets/sample-barn",
                "downloadedCount": 6,
                "files": [
                    {"file": name, "bytes": 1, "sha256": digests[name]}
                    for name in cache.firmware_sample_sync_files()
                ],
            }

        rt = self._runtime(conn=conn, asset_cache=cache)
        with patch("core.lesson.runtime.call_mcp_tool", new=capture_dispatch):
            self.assertTrue(await rt._sync_sd_asset_pack_to_robot())

        self.assertEqual(calls[0][1], {"base_url": "https://cdn.example/sample"})

    async def test_sd_asset_pack_warm_sync_emits_cache_hit_only_for_all_skipped(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        conn.logger = _CapturingLogger()

        async def warm_sync(*_args, **_kwargs):
            return json.dumps(
                {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                    "packChecksum": _manifest_checksum(),
                    "downloadedCount": 0,
                    "skippedCount": 3,
                    "failedCount": 0,
                }
            )

        rt = self._runtime(conn=conn, asset_cache=_FirmwareSyncAssetCache(ready=True))
        with patch("core.lesson.runtime.call_mcp_tool", new=warm_sync):
            self.assertTrue(await rt._sync_sd_asset_pack_to_robot())

        messages = "\n".join(message for _level, message in conn.logger.events)
        self.assertIn("lesson_preload_ready", messages)
        self.assertIn("checksum_verified", messages)
        self.assertIn("asset_cache_hit", messages)
        self.assertIn("downloadedCount=0", messages)
        self.assertIn("skippedCount=3", messages)

    async def test_sd_asset_pack_rejects_invalid_attestation_without_success_markers(self):
        invalid_results = (
            {
                "ready": False,
                "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                "downloadedCount": 3,
                "skippedCount": 0,
                "failedCount": 0,
            },
            {
                "ready": True,
                "cacheKey": "wrong-cache-key",
                "manifestChecksum": _manifest_checksum(),
                "downloadedCount": 3,
                "skippedCount": 0,
                "failedCount": 0,
            },
            {
                "ready": True,
                "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                "manifestChecksum": _manifest_checksum(),
                "downloadedCount": 2,
                "skippedCount": 0,
                "failedCount": 0,
            },
            {
                "ready": True,
                "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                "manifestChecksum": _manifest_checksum(),
                "downloadedCount": 3,
                "skippedCount": -1,
                "failedCount": 1,
            },
            {
                "ready": True,
                "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                "manifestChecksum": _manifest_checksum(),
                "downloadedCount": 3,
                "skippedCount": 0,
                "reusedCount": -1,
                "failedCount": 0,
            },
            {
                "ready": True,
                "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                "downloadedCount": 3,
                "skippedCount": 0,
                "failedCount": 0,
            },
            {
                "ready": True,
                "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                "manifestChecksum": "forged-checksum",
                "downloadedCount": 3,
                "skippedCount": 0,
                "failedCount": 0,
            },
            {
                "ready": True,
                "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                "manifestChecksum": _manifest_checksum(),
                "packChecksum": "contradictory-checksum",
                "downloadedCount": 3,
                "skippedCount": 0,
                "failedCount": 0,
            },
        )

        for result in invalid_results:
            with self.subTest(result=result):
                conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
                conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
                conn.mcp_client = _ReadyLessonAssetMcpClient()
                conn.logger = _CapturingLogger()
                calls = []

                async def invalid_sync(*_args, _result=result, **_kwargs):
                    calls.append(_result)
                    return _result

                rt = self._runtime(
                    conn=conn, asset_cache=_FirmwareSyncAssetCache(ready=True)
                )
                with patch("core.lesson.runtime.call_mcp_tool", new=invalid_sync):
                    self.assertFalse(await rt._sync_sd_asset_pack_to_robot())

                self.assertEqual(calls, [result])
                messages = "\n".join(message for _level, message in conn.logger.events)
                self.assertIn("robot SD sync returned invalid attestation", messages)
                self.assertIn("assetCount=3", messages)
                self.assertIn("downloadedCount=", messages)
                self.assertIn("skippedCount=", messages)
                self.assertIn("reusedCount=", messages)
                self.assertIn("failedCount=", messages)
                self.assertIn("cacheKeyMatch=", messages)
                self.assertIn("checksumMatch=", messages)
                self.assertNotIn("lesson_preload_ready", messages)
                self.assertNotIn("checksum_verified", messages)
                self.assertNotIn("asset_cache_hit", messages)

    async def test_sd_asset_pack_waits_for_mcp_discovery_before_prepare(self):
        class _EventuallyReadyMcpClient(_ReadyLessonAssetMcpClient):
            def __init__(self):
                super().__init__()
                self.ready_checks = 0

            async def is_ready(self):
                self.ready_checks += 1
                return self.ready_checks >= 2

        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {
            "lesson": {
                "asset_delivery_mode": "sd_pack",
                "asset_pack_mount_root": "/sdcard/tbot/lesson-assets",
                "sd_sync_ready_timeout_sec": 1,
                "sd_sync_ready_poll_sec": 0.001,
            }
        }
        conn.mcp_client = _EventuallyReadyMcpClient()
        calls = []

        async def capture_call(*args, **kwargs):
            calls.append((args, kwargs, list(conn.websocket.sent)))
            return {
                "ready": True,
                "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                "manifestChecksum": _manifest_checksum(),
                "downloadedCount": 3,
                "skippedCount": 0,
                "failedCount": 0,
            }

        rt = self._runtime(conn=conn, asset_cache=_FirmwareSyncAssetCache(ready=True))
        with patch("core.lesson.runtime.call_mcp_tool", new=capture_call):
            await rt.start()

        self.assertGreaterEqual(conn.mcp_client.ready_checks, 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], [])
        self.assertEqual(self._sent_frames(conn)[-1]["type"], "lesson_prepare")

    async def test_sd_asset_pack_recovers_stale_firmware_lesson_before_retrying_sync(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        resets = []
        sync_calls = []

        async def request_lesson_preload_reset(**kwargs):
            resets.append(kwargs)
            return True

        async def sync_after_reset(*args, **kwargs):
            sync_calls.append((args, kwargs))
            if len(sync_calls) == 1:
                raise RuntimeError("MCP tools disabled during lesson")
            return {
                "ready": True,
                "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                "manifestChecksum": _manifest_checksum(),
                "downloadedCount": 3,
                "skippedCount": 0,
                "failedCount": 0,
            }

        conn.request_lesson_preload_reset = request_lesson_preload_reset
        rt = self._runtime(conn=conn, asset_cache=_FirmwareSyncAssetCache(ready=True))

        with patch("core.lesson.runtime.call_mcp_tool", new=sync_after_reset):
            await rt.start()

        self.assertEqual(len(resets), 1)
        self.assertEqual(resets[0]["assignment_id"], rt.assignment_id)
        self.assertEqual(resets[0]["lesson_id"], rt.lesson_id)
        self.assertEqual(len(sync_calls), 2)
        self.assertEqual(self._sent_frames(conn)[-1]["type"], "lesson_prepare")

    async def test_sd_asset_pack_recovery_retry_reports_realtime_busy_timeout(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {
            "lesson": {
                "asset_delivery_mode": "sd_pack",
                "asset_pack_mount_root": "/sdcard/tbot/lesson-assets",
                "sd_sync_foreground_busy_timeout_sec": 0.05,
                "sd_sync_foreground_busy_poll_sec": 0.005,
            }
        }
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        busy = False
        resets = 0
        sync_calls = 0

        async def request_lesson_preload_reset(**_kwargs):
            nonlocal busy, resets
            resets += 1
            busy = True
            return True

        async def stale_lesson_sync(*_args, **_kwargs):
            nonlocal sync_calls
            sync_calls += 1
            raise RuntimeError("MCP tools disabled during lesson")

        conn.request_lesson_preload_reset = request_lesson_preload_reset
        conn.is_realtime_busy = lambda: busy
        rt = self._runtime(conn=conn, asset_cache=_FirmwareSyncAssetCache(ready=True))

        with patch("core.lesson.runtime.call_mcp_tool", new=stale_lesson_sync):
            await asyncio.wait_for(rt.start(), timeout=0.5)

        self.assertEqual(resets, 1)
        self.assertEqual(sync_calls, 1)
        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "SD_SYNC_REALTIME_BUSY_TIMEOUT")
        frames = self._sent_frames(conn)
        self.assertEqual(frames[-1]["type"], "lesson_error")
        self.assertEqual(
            frames[-1]["body"]["code"],
            "SD_SYNC_REALTIME_BUSY_TIMEOUT",
        )

    async def test_sd_asset_pack_foreground_joining_background_times_out_safely(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {
            "lesson": {
                "asset_delivery_mode": "sd_pack",
                "asset_pack_mount_root": "/sdcard/tbot/lesson-assets",
                "sd_sync_foreground_busy_timeout_sec": 0.05,
                "sd_sync_foreground_busy_poll_sec": 0.005,
            }
        }
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        conn.is_realtime_busy = lambda: True
        asset_cache = _FirmwareSyncAssetCache(ready=True)
        rt = self._runtime(conn=conn, asset_cache=asset_cache)
        background_started = asyncio.Event()
        release_background = asyncio.Event()
        mcp_calls = 0

        async def blocking_background_sync():
            nonlocal mcp_calls
            mcp_calls += 1
            background_started.set()
            await release_background.wait()
            return {"ready": True}

        background = asyncio.create_task(
            request_sd_pack_sync(
                conn,
                asset_cache.cache_key,
                blocking_background_sync,
            )
        )
        await background_started.wait()
        try:
            started_at = asyncio.get_running_loop().time()
            await asyncio.wait_for(rt.start(), timeout=0.5)

            self.assertLess(asyncio.get_running_loop().time() - started_at, 0.25)
            self.assertEqual(mcp_calls, 1)
            self.assertEqual(rt.state, "FAILED")
            self.assertEqual(rt.last_error.code, "SD_SYNC_REALTIME_BUSY_TIMEOUT")
            frames = self._sent_frames(conn)
            self.assertEqual(frames[-1]["type"], "lesson_error")
            self.assertEqual(
                frames[-1]["body"]["code"],
                "SD_SYNC_REALTIME_BUSY_TIMEOUT",
            )
        finally:
            release_background.set()
            self.assertEqual(await background, {"ready": True})

    async def test_sd_asset_pack_timed_out_queue_entry_never_calls_mcp_later(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {
            "lesson": {
                "asset_delivery_mode": "sd_pack",
                "asset_pack_mount_root": "/sdcard/tbot/lesson-assets",
                "sd_sync_foreground_busy_timeout_sec": 0.05,
                "sd_sync_foreground_busy_poll_sec": 0.005,
            }
        }
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        conn.is_realtime_busy = lambda: False
        rt = self._runtime(conn=conn, asset_cache=_FirmwareSyncAssetCache(ready=True))
        background_started = asyncio.Event()
        release_background = asyncio.Event()
        mcp_calls = 0

        async def blocking_background_sync():
            background_started.set()
            await release_background.wait()
            return "background-complete"

        async def ghost_mcp_call(*_args, **_kwargs):
            nonlocal mcp_calls
            mcp_calls += 1
            return {"ready": True}

        async def coordinator_probe():
            return "coordinator-usable"

        background = asyncio.create_task(
            request_sd_pack_sync(
                conn,
                "unrelated-background-cache-key",
                blocking_background_sync,
            )
        )
        await background_started.wait()
        try:
            with patch("core.lesson.runtime.call_mcp_tool", new=ghost_mcp_call):
                await asyncio.wait_for(rt.start(), timeout=0.5)

                self.assertEqual(rt.state, "FAILED")
                self.assertEqual(rt.last_error.code, "SD_SYNC_REALTIME_BUSY_TIMEOUT")
                self.assertEqual(
                    self._sent_frames(conn)[-1]["body"]["code"],
                    "SD_SYNC_REALTIME_BUSY_TIMEOUT",
                )
                release_background.set()
                self.assertEqual(await background, "background-complete")
                self.assertEqual(
                    await request_sd_pack_sync(
                        conn,
                        "coordinator-probe-cache-key",
                        coordinator_probe,
                    ),
                    "coordinator-usable",
                )
                self.assertEqual(mcp_calls, 0)
        finally:
            release_background.set()
            if not background.done():
                await background

    async def test_sd_asset_pack_sync_waits_for_voice_idle_before_starting(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        busy = True
        attempts = 0

        def is_realtime_busy():
            return busy

        async def voice_preemptible_sync(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            return {
                "ready": True,
                "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                "manifestChecksum": _manifest_checksum(),
                "downloadedCount": 3,
                "skippedCount": 0,
                "failedCount": 0,
            }

        conn.is_realtime_busy = is_realtime_busy
        rt = self._runtime(conn=conn, asset_cache=_FirmwareSyncAssetCache(ready=True))

        with patch("core.lesson.runtime.call_mcp_tool", new=voice_preemptible_sync):
            sync_task = asyncio.create_task(rt._sync_sd_asset_pack_to_robot())
            await asyncio.sleep(0.08)
            self.assertEqual(attempts, 0)
            busy = False
            self.assertTrue(await asyncio.wait_for(sync_task, timeout=1))

        self.assertEqual(attempts, 1)

    async def test_sd_asset_pack_sync_uses_start_lesson_scoped_busy_guard(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {
            "lesson": {
                "asset_delivery_mode": "sd_pack",
                "asset_pack_mount_root": "/sdcard/tbot/lesson-assets",
                "sd_sync_foreground_busy_timeout_sec": 0.05,
                "sd_sync_foreground_busy_poll_sec": 0.005,
            }
        }
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        conn.is_realtime_busy = lambda: True
        conn.is_lesson_sd_sync_busy = lambda: False
        attempts = 0

        async def scoped_sync(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            return {
                "ready": True,
                "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                "manifestChecksum": _manifest_checksum(),
                "downloadedCount": 3,
                "skippedCount": 0,
                "failedCount": 0,
            }

        rt = self._runtime(conn=conn, asset_cache=_FirmwareSyncAssetCache(ready=True))
        with patch("core.lesson.runtime.call_mcp_tool", new=scoped_sync):
            self.assertTrue(await rt._sync_sd_asset_pack_to_robot())

        self.assertEqual(attempts, 1)

    async def test_sd_asset_pack_sync_preserves_start_lesson_admission_when_worker_predates_it(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {
            "lesson": {
                "asset_delivery_mode": "sd_pack",
                "asset_pack_mount_root": "/sdcard/tbot/lesson-assets",
                "sd_sync_foreground_busy_timeout_sec": 0.2,
                "sd_sync_foreground_busy_poll_sec": 0.005,
            }
        }
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        admission = ContextVar("queued_start_lesson_admission", default=0)
        conn.lesson_start_sd_sync_admission_active = lambda: admission.get() > 0

        def lesson_busy(*, start_lesson_dispatch=None):
            return not bool(start_lesson_dispatch)

        conn.is_lesson_sd_sync_busy = lesson_busy
        asset_cache = _FirmwareSyncAssetCache(ready=True)
        rt = self._runtime(conn=conn, asset_cache=asset_cache)
        background_started = asyncio.Event()
        release_background = asyncio.Event()
        attempts = 0

        async def blocking_background_sync():
            background_started.set()
            await release_background.wait()
            return {"ready": True}

        async def scoped_sync(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            return {
                "ready": True,
                "cacheKey": asset_cache.cache_key,
                "manifestChecksum": _manifest_checksum(),
                "downloadedCount": 3,
                "skippedCount": 0,
                "failedCount": 0,
            }

        background = asyncio.create_task(
            request_sd_pack_sync(conn, "older-background-pack", blocking_background_sync)
        )
        await background_started.wait()
        token = admission.set(1)
        try:
            with patch("core.lesson.runtime.call_mcp_tool", new=scoped_sync):
                foreground = asyncio.create_task(rt._sync_sd_asset_pack_to_robot())
                await asyncio.sleep(0.02)
                release_background.set()
                self.assertTrue(await asyncio.wait_for(foreground, timeout=0.5))
        finally:
            admission.reset(token)
            release_background.set()
            await background

        self.assertEqual(attempts, 1)

    async def test_sd_asset_pack_sync_rejects_stale_start_lesson_admission_after_new_turn(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {
            "lesson": {
                "asset_delivery_mode": "sd_pack",
                "asset_pack_mount_root": "/sdcard/tbot/lesson-assets",
                "sd_sync_foreground_busy_timeout_sec": 0.08,
                "sd_sync_foreground_busy_poll_sec": 0.005,
            }
        }
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        current_generation = {"value": "generation-1"}
        conn.lesson_start_sd_sync_admission_active = lambda: True
        conn.lesson_start_sd_sync_admission_token = lambda: "generation-1"

        def lesson_busy(
            *, start_lesson_dispatch=None, start_lesson_admission=None
        ):
            if isinstance(start_lesson_dispatch, bool):
                return False
            return start_lesson_admission != current_generation["value"]

        conn.is_lesson_sd_sync_busy = lesson_busy
        asset_cache = _FirmwareSyncAssetCache(ready=True)
        rt = self._runtime(conn=conn, asset_cache=asset_cache)
        background_started = asyncio.Event()
        release_background = asyncio.Event()
        attempts = 0

        async def blocking_background_sync():
            background_started.set()
            await release_background.wait()
            return {"ready": True}

        async def sync_must_not_start(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            return {"ready": True}

        background = asyncio.create_task(
            request_sd_pack_sync(conn, "older-background-pack", blocking_background_sync)
        )
        await background_started.wait()
        try:
            with patch("core.lesson.runtime.call_mcp_tool", new=sync_must_not_start):
                foreground = asyncio.create_task(rt._sync_sd_asset_pack_to_robot())
                await asyncio.sleep(0.02)
                current_generation["value"] = "generation-2"
                release_background.set()
                self.assertFalse(await asyncio.wait_for(foreground, timeout=0.5))
        finally:
            release_background.set()
            await background

        self.assertEqual(attempts, 0)
        self.assertEqual(rt.last_error.code, "SD_SYNC_REALTIME_BUSY_TIMEOUT")

    async def test_sd_asset_pack_sync_permanent_realtime_busy_times_out(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {
            "lesson": {
                "asset_delivery_mode": "sd_pack",
                "asset_pack_mount_root": "/sdcard/tbot/lesson-assets",
                "sd_sync_foreground_busy_timeout_sec": 0.05,
                "sd_sync_foreground_busy_poll_sec": 0.005,
            }
        }
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        conn.logger = _CapturingLogger()
        conn.is_realtime_busy = lambda: True
        attempts = 0

        async def sync_must_not_start(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            raise AssertionError("MCP sync must wait for realtime admission")

        rt = self._runtime(conn=conn, asset_cache=_FirmwareSyncAssetCache(ready=True))

        with patch("core.lesson.runtime.call_mcp_tool", new=sync_must_not_start):
            result = await asyncio.wait_for(
                rt._sync_sd_asset_pack_to_robot(),
                timeout=0.5,
            )

        self.assertFalse(result)
        self.assertEqual(attempts, 0)
        self.assertEqual(rt.last_error.code, "SD_SYNC_REALTIME_BUSY_TIMEOUT")
        messages = "\n".join(message for _level, message in conn.logger.events)
        self.assertIn("robot SD sync realtime busy timeout", messages)

    async def test_sd_asset_pack_start_reports_realtime_busy_timeout_code(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {
            "lesson": {
                "asset_delivery_mode": "sd_pack",
                "asset_pack_mount_root": "/sdcard/tbot/lesson-assets",
                "sd_sync_foreground_busy_timeout_sec": 0.05,
                "sd_sync_foreground_busy_poll_sec": 0.005,
            }
        }
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        conn.is_realtime_busy = lambda: True
        rt = self._runtime(conn=conn, asset_cache=_FirmwareSyncAssetCache(ready=True))

        await asyncio.wait_for(rt.start(), timeout=0.5)

        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "SD_SYNC_REALTIME_BUSY_TIMEOUT")
        frames = self._sent_frames(conn)
        self.assertEqual(frames[-1]["type"], "lesson_error")
        self.assertEqual(
            frames[-1]["body"]["code"],
            "SD_SYNC_REALTIME_BUSY_TIMEOUT",
        )

    async def test_sd_asset_pack_raw_syncs_unlisted_internal_robot_tool(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        conn.mcp_client = _MissingLessonAssetToolMcpClient()
        calls = []

        async def capture_raw(conn_arg, mcp_client, tool_name, args, timeout=30):
            calls.append(
                {
                    "conn": conn_arg,
                    "mcp_client": mcp_client,
                    "tool_name": tool_name,
                    "args": copy.deepcopy(args),
                    "timeout": timeout,
                    "sent_before_call": list(conn.websocket.sent),
                }
            )
            return json.dumps(
                {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
                    "manifestChecksum": _manifest_checksum(),
                    "downloadedCount": 3,
                    "skippedCount": 0,
                    "failedCount": 0,
                }
            )

        rt = self._runtime(conn=conn, asset_cache=_FirmwareSyncAssetCache(ready=True))

        with patch("core.api.device_mcp_admin_handler._call_raw_mcp_tool", new=capture_raw):
            await rt.start()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["tool_name"], "self.lesson_assets.sync_to_sd")
        self.assertEqual(calls[0]["sent_before_call"], [])
        self.assertGreaterEqual(calls[0]["timeout"], 60)
        self.assertEqual(calls[0]["args"]["assetPack"]["cacheKey"], "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3")
        self.assertEqual(self._sent_frames(conn)[-1]["type"], "lesson_prepare")

    async def test_sd_asset_pack_inexact_sync_attestation_fails_closed_before_prepare(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        conn.mcp_client = _ReadyLessonAssetMcpClient()
        forwarder = _FakeForwarder()
        rt = self._runtime(
            conn=conn,
            asset_cache=_FakeAssetCache(ready=True),
            forwarder=forwarder,
        )

        async def inexact_attestation(*_args, **_kwargs):
            return {
                "ready": True,
                "cacheKey": "different-pack/v1-" + ("b" * 64),
                "manifestChecksum": _manifest_checksum(),
                "downloadedCount": 3,
                "skippedCount": 0,
                "failedCount": 0,
            }

        with patch("core.lesson.runtime.call_mcp_tool", new=inexact_attestation):
            await rt.start()

        sent = self._sent_frames(conn)
        self.assertEqual([frame["type"] for frame in sent], ["lesson_error"])
        self.assertEqual(sent[0]["body"]["code"], "ASSET_PACK_NOT_READY")
        self.assertTrue(sent[0]["body"]["retryable"])
        self.assertEqual(rt.state, "FAILED")
        self.assertFalse(rt._sd_asset_pack_online_fallback)
        terminal = [
            batch["events"][0]
            for batch in forwarder.batches
            if batch["events"][0]["type"] == "lesson_failed"
        ]
        self.assertEqual(terminal[0]["code"], "ASSET_PACK_NOT_READY")

    async def test_sd_asset_pack_mcp_unavailable_fails_closed_before_prepare(self):
        for mcp_client in (
            None,
            _NotReadyLessonAssetMcpClient(),
            _MissingLessonAssetToolMcpClient(),
        ):
            with self.subTest(mcp_client=type(mcp_client).__name__ if mcp_client else None):
                features = None
                if mcp_client is None:
                    features = {
                        "lesson": True,
                        "renderer": "teebot-lesson-renderer.v1",
                        "mcp": True,
                    }
                conn = _FakeConn(
                    features=features,
                    session_id=FIX["frames"]["lesson_prepare"]["sessionId"],
                )
                conn.config = {
                    "lesson": {
                        "asset_delivery_mode": "sd_pack",
                        "sd_sync_ready_timeout_sec": 0,
                    }
                }
                conn.mcp_client = mcp_client
                rt = self._runtime(
                    conn=conn,
                    asset_cache=_FakeAssetCache(ready=True),
                )

                await rt.start()

                sent = self._sent_frames(conn)
                self.assertEqual([frame["type"] for frame in sent], ["lesson_error"])
                self.assertEqual(sent[0]["body"]["code"], "ASSET_PACK_NOT_READY")
                self.assertTrue(sent[0]["body"]["retryable"])
                self.assertEqual(rt.state, "FAILED")
                self.assertFalse(rt._sd_asset_pack_online_fallback)

    async def test_sd_asset_pack_not_ready_fails_closed_and_reports_retryable_terminal(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        finished = []

        async def finish_lesson_mode(*, reason):
            finished.append(reason)

        conn.finish_lesson_mode = finish_lesson_mode
        conn.session_mode = "lesson"
        forwarder = _FakeForwarder()
        rt = self._runtime(
            conn=conn,
            asset_cache=_FakeAssetCache(ready=False),
            forwarder=forwarder,
        )

        await rt.start()

        sent = self._sent_frames(conn)
        self.assertEqual([frame["type"] for frame in sent], ["lesson_error"])
        self.assertEqual(sent[0]["body"]["code"], "ASSET_PACK_NOT_READY")
        self.assertTrue(sent[0]["body"]["retryable"])
        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "ASSET_PACK_NOT_READY")
        self.assertTrue(rt.last_error.retryable)
        self.assertEqual(finished, ["sd_asset_pack_not_ready"])
        self.assertFalse(rt._sd_asset_pack_online_fallback)
        event_types = [batch["events"][0]["type"] for batch in forwarder.batches]
        self.assertIn("lesson_failed", event_types)

    async def test_sd_asset_pack_robot_sync_attestation_failure_notifies_terminal_safely(self):
        """A missing robot attestation fails the run without preparing online assets."""
        conn = _FakeConn(
            session_id=FIX["frames"]["lesson_prepare"]["sessionId"],
            # Device claims MCP so a missing client is a real sync failure.
            features={"lesson": True, "renderer": "teebot-lesson-renderer.v1", "mcp": True},
        )
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        conn.mcp_client = None
        finished = []

        async def finish_lesson_mode(*, reason):
            finished.append(reason)

        conn.finish_lesson_mode = finish_lesson_mode
        conn.session_mode = "lesson"
        rt = self._runtime(conn=conn, asset_cache=_FakeAssetCache(ready=True))

        await rt.start()

        self.assertEqual(rt.state, "FAILED")
        self.assertFalse(rt._sd_asset_pack_online_fallback)
        self.assertEqual(finished, ["sd_asset_pack_sync_failed"])
        sent = self._sent_frames(conn)
        self.assertEqual([frame["type"] for frame in sent], ["lesson_error"])
        self.assertEqual(sent[0]["body"]["code"], "ASSET_PACK_NOT_READY")
        self.assertTrue(sent[0]["body"]["retryable"])

    async def test_sd_asset_pack_preload_error_fails_before_prepare(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        rt = self._runtime(
            conn=conn,
            asset_cache=_FakeAssetCache(
                preload_error=LessonError("ASSET_CHECKSUM_MISMATCH", "bad asset")
            ),
        )

        await rt.start()

        sent = self._sent_frames(conn)
        self.assertEqual([frame["type"] for frame in sent], ["lesson_error"])
        self.assertEqual(sent[0]["body"]["code"], "ASSET_CHECKSUM_MISMATCH")
        self.assertEqual(rt.state, "FAILED")

    async def test_sd_asset_pack_materialize_crash_emits_retryable_error_before_prepare(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        rt = self._runtime(
            conn=conn,
            asset_cache=_FakeAssetCache(preload_error=OSError("sdcard full")),
        )

        await rt.start()

        sent = self._sent_frames(conn)
        self.assertEqual([frame["type"] for frame in sent], ["lesson_error"])
        self.assertEqual(sent[0]["body"]["code"], "ASSET_PACK_MATERIALIZE_FAILED")
        self.assertTrue(sent[0]["body"]["retryable"])
        self.assertEqual(rt.state, "FAILED")

    async def test_sd_asset_pack_prepare_ack_must_report_ready_before_start(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        rt = self._runtime(conn=conn, asset_cache=_FakeAssetCache(ready=True))

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))

        sent = self._sent_frames(conn)
        self.assertEqual(sent[-1]["type"], "lesson_error")
        self.assertEqual(sent[-1]["body"]["code"], "ASSET_PACK_NOT_READY")
        self.assertIsNone(rt._preload_task)
        self.assertEqual(rt.state, "FAILED")

    async def test_sd_asset_pack_prepare_ack_must_match_current_cache_key(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        rt = self._runtime(conn=conn, asset_cache=_FakeAssetCache(ready=True))

        await rt.start()
        await rt.on_lesson_ack(
            _ack(
                1,
                1,
                extra={
                    "acks": 1,
                    "rendered": True,
                    "assetPack": {"ready": True, "cacheKey": "old-lesson/v2-stale"},
                },
            )
        )

        sent = self._sent_frames(conn)
        self.assertEqual(sent[-1]["type"], "lesson_error")
        self.assertEqual(sent[-1]["body"]["code"], "ASSET_PACK_NOT_READY")
        self.assertIsNone(rt._preload_task)
        self.assertEqual(rt.state, "FAILED")

    async def test_sd_asset_pack_prepare_ack_requires_current_cache_key(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        cache = _FakeAssetCache(ready=True)
        cache.cache_key = ""
        rt = self._runtime(conn=conn, asset_cache=cache)

        await rt.start()
        await rt.on_lesson_ack(
            _ack(
                1,
                1,
                extra={
                    "acks": 1,
                    "rendered": True,
                    "assetPack": {"ready": True, "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3"},
                },
            )
        )

        sent = self._sent_frames(conn)
        self.assertEqual(sent[-1]["type"], "lesson_error")
        self.assertEqual(sent[-1]["body"]["code"], "ASSET_PACK_NOT_READY")
        self.assertIsNone(rt._preload_task)
        self.assertEqual(rt.state, "FAILED")

    async def test_sd_asset_pack_prepare_ack_rejects_non_string_current_cache_key(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        cache = _FakeAssetCache(ready=True)
        cache.cache_key = 123
        rt = self._runtime(conn=conn, asset_cache=cache)

        await rt.start()
        await rt.on_lesson_ack(
            _ack(
                1,
                1,
                extra={
                    "acks": 1,
                    "rendered": True,
                    "assetPack": {"ready": True, "cacheKey": 123},
                },
            )
        )

        sent = self._sent_frames(conn)
        self.assertEqual(sent[-1]["type"], "lesson_error")
        self.assertEqual(sent[-1]["body"]["code"], "ASSET_PACK_NOT_READY")
        self.assertIsNone(rt._preload_task)
        self.assertEqual(rt.state, "FAILED")

    async def test_sd_asset_pack_prepare_ack_rejects_blank_current_cache_key(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack", "asset_pack_mount_root": "/sdcard/tbot/lesson-assets"}}
        cache = _FakeAssetCache(ready=True)
        cache.cache_key = "   "
        rt = self._runtime(conn=conn, asset_cache=cache)

        await rt.start()
        await rt.on_lesson_ack(
            _ack(
                1,
                1,
                extra={
                    "acks": 1,
                    "rendered": True,
                    "assetPack": {"ready": True, "cacheKey": "   "},
                },
            )
        )

        sent = self._sent_frames(conn)
        self.assertEqual(sent[-1]["type"], "lesson_error")
        self.assertEqual(sent[-1]["body"]["code"], "ASSET_PACK_NOT_READY")
        self.assertIsNone(rt._preload_task)
        self.assertEqual(rt.state, "FAILED")

    async def test_each_lesson_step_sends_prompt_to_voice_provider(self):
        conn = _FakeConn()
        conn.voice_provider = _RecordingLessonVoiceProvider()
        manifest = _build_steps_manifest([("s1", "greeting"), ("s4", "model")])
        manifest["steps"][0]["prompt"] = "Welcome to the barn story."
        manifest["steps"][1]["prompt"] = "Now say barn with TeeBot."
        rt = self._runtime(conn=conn, manifest=manifest)

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        self.assertEqual(conn.voice_provider.prompts, [])

        await rt.on_lesson_ack(_ack(3, 3, step_id="s1"))

        self.assertEqual(conn.voice_provider.prompts, ["Welcome to the barn story."])

        step_s4 = [f for f in self._sent_frames(conn) if f["type"] == "lesson_step"][-1]
        self.assertEqual(step_s4["stepId"], "s4")
        await rt.on_lesson_ack(_ack(step_s4["sequence"], 4, step_id="s4"))

        self.assertEqual(
            conn.voice_provider.prompts,
            ["Welcome to the barn story.", "Now say barn with TeeBot."],
        )

    # 2) ack correlation uses body.acks, never envelope.sequence / ackFor --------

    async def test_ack_correlation_uses_body_acks_not_envelope_sequence_and_ignores_ackFor(
        self,
    ):
        # 2a: a prepare-ack carrying ONLY {"ackFor":1} must NOT correlate.
        conn = _FakeConn()
        rt = self._runtime(conn=conn)
        await rt.start()
        await rt.on_lesson_ack(_ack(None, 1, extra={"ackFor": 1}))
        self.assertIsNone(rt._preload_task)  # no correlation -> preload not kicked
        # only lesson_prepare on the wire so far.
        self.assertEqual([f["type"] for f in self._sent_frames(conn)], ["lesson_prepare"])

        # body.acks=1 DOES kick it (envelope sequence here is the F->S counter 2).
        await rt.on_lesson_ack(_ack(1, 2))
        self.assertIsNotNone(rt._preload_task)
        await rt._preload_task
        self.assertIn("lesson_start", [f["type"] for f in self._sent_frames(conn)])

        # 2b: the stop-ack with envelope sequence 5 but body.acks 4 correlates to stop.
        await rt.on_lesson_ack(_ack(2, 3))  # start-ack (F->S seq 3)
        await rt.on_lesson_ack(_ack(3, 4, step_id="s4"))  # step-ack (F->S seq 4)
        await rt.on_lesson_progress(
            _progress(
                5,
                {
                    "event": "step_completed",
                    "stepType": "model",
                    "result": "success",
                    "detail": {"tapTargetHit": True},
                },
            )
        )
        await rt.on_lesson_ack(_ack(4, 6))  # stop-ack: env seq 6, body.acks 4
        self.assertEqual(rt.state, "COMPLETED")

    async def test_legacy_empty_lesson_ack_correlates_when_only_one_frame_is_outstanding(self):
        conn = _FakeConn()
        rt = self._runtime(conn=conn)

        await rt.start()
        await rt.on_lesson_ack(
            {
                "type": "lesson_ack",
                "assignmentId": rt.assignment_id,
                "sessionId": rt.session_id,
                "lessonId": rt.lesson_id,
                "lessonVersion": rt.lesson_version,
            }
        )

        self.assertIsNotNone(rt._preload_task)
        await rt._preload_task
        self.assertIn("lesson_start", [f["type"] for f in self._sent_frames(conn)])

    async def test_legacy_empty_lesson_ack_with_envelope_sequence_correlates(self):
        conn = _FakeConn()
        rt = self._runtime(conn=conn)

        await rt.start()
        await rt.on_lesson_ack(
            {
                "type": "lesson_ack",
                "assignmentId": rt.assignment_id,
                "sessionId": rt.session_id,
                "lessonId": rt.lesson_id,
                "lessonVersion": rt.lesson_version,
                "sequence": 1,
            }
        )

        self.assertIsNotNone(rt._preload_task)
        await rt._preload_task
        self.assertIn("lesson_start", [f["type"] for f in self._sent_frames(conn)])

    async def test_stale_legacy_empty_lesson_ack_without_current_identity_is_ignored(self):
        conn = _FakeConn()
        rt = self._runtime(conn=conn)

        await rt.start()
        await rt.on_lesson_ack({"type": "lesson_ack", "sequence": 1})

        self.assertIsNone(rt._preload_task)
        self.assertEqual([f["type"] for f in self._sent_frames(conn)], ["lesson_prepare"])

    async def test_delayed_legacy_empty_lesson_ack_from_prior_session_is_ignored(self):
        conn = _FakeConn()
        rt = self._runtime(conn=conn)

        await rt.start()
        await rt.on_lesson_ack(
            {
                "type": "lesson_ack",
                "assignmentId": rt.assignment_id,
                "sessionId": "22222222-2222-4222-8222-222222222222",
                "lessonId": rt.lesson_id,
                "lessonVersion": rt.lesson_version,
                "sequence": 1,
            }
        )

        self.assertEqual(set(rt._outstanding), {1})
        self.assertIsNone(rt._preload_task)
        self.assertEqual([f["type"] for f in self._sent_frames(conn)], ["lesson_prepare"])

    # 3) lesson_start gated until ready ------------------------------------------

    async def test_lesson_start_blocked_until_ready(self):
        # Negative: ready=False -> after prepare-ack + preload, only lesson_prepare.
        conn = _FakeConn()
        rt = self._runtime(conn=conn, asset_cache=_FakeAssetCache(ready=False))
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        self.assertEqual(
            [f["type"] for f in self._sent_frames(conn)], ["lesson_prepare"]
        )

        # Positive control: ready=True -> lesson_start IS sent.
        conn2 = _FakeConn()
        rt2 = self._runtime(conn=conn2, asset_cache=_FakeAssetCache(ready=True))
        await rt2.start()
        await rt2.on_lesson_ack(_ack(1, 1))
        await rt2._preload_task
        self.assertIn("lesson_start", [f["type"] for f in self._sent_frames(conn2)])

    # 4) capability gate refuses (no frame on the wire) --------------------------

    async def test_capability_gate_refuses(self):
        for features in ({}, {"lesson": True}):
            conn = _FakeConn(features=features)
            rt = self._runtime(conn=conn)
            with self.assertRaises(LessonError) as ctx:
                await rt.start()
            self.assertEqual(ctx.exception.code, "LESSON_VERSION_UNSUPPORTED")
            self.assertEqual(conn.websocket.sent, [])  # nothing emitted

    # 5) protocol-version gate ---------------------------------------------------

    async def test_protocol_version_gate(self):
        conn = _FakeConn()
        manifest = _build_manifest()
        manifest["manifestVersion"] = "teebot-renderer.v1"
        rt = self._runtime(conn=conn, manifest=manifest)
        with self.assertRaises(LessonError) as ctx:
            await rt.start()
        self.assertEqual(ctx.exception.code, "LESSON_VERSION_UNSUPPORTED")
        self.assertEqual(conn.websocket.sent, [])

    async def test_empty_renderable_manifest_is_rejected_before_prepare(self):
        conn = _FakeConn()
        manifest = _build_manifest()
        manifest["steps"] = []
        rt = self._runtime(conn=conn, manifest=manifest)

        with self.assertRaises(LessonError) as ctx:
            await rt.start()

        self.assertEqual(ctx.exception.code, "LESSON_STEP_MISSING")
        self.assertEqual(conn.websocket.sent, [])

    # 5b) device-renderer profile gate -------------------------------------------

    async def test_profile_gate_rejects_non_esptft_profile(self):
        # FIX#1: a published lesson with a non-espTft profile (piTft/mobile) renders
        # BLANK on espTft-only firmware. With the default supported_profiles (['espTft'],
        # i.e. conn.config has no lesson.supported_profiles) a runtime whose assignment
        # profile is 'piTft' must be REJECTED from start() with LESSON_VERSION_UNSUPPORTED
        # and put NO frame on the wire (skipped, not rendered blank).
        from core.lesson.runtime import LessonRuntime

        conn = _FakeConn()  # config = {} -> supported_profiles defaults to ['espTft']
        assignment = _build_assignment()
        assignment["profile"] = "piTft"
        rt = LessonRuntime(
            conn,
            assignment=assignment,
            manifest=_build_manifest(),
            asset_cache=_FakeAssetCache(ready=True),
            forwarder=_FakeForwarder(),
            manifest_checksum=_manifest_checksum(),
        )
        with self.assertRaises(LessonError) as ctx:
            await rt.start()
        self.assertEqual(ctx.exception.code, "LESSON_VERSION_UNSUPPORTED")
        self.assertEqual(conn.websocket.sent, [])  # nothing emitted

    async def test_profile_gate_passes_esptft_profile(self):
        # The espTft profile is in the default supported set -> the gate passes and the
        # runtime proceeds to emit lesson_prepare (unchanged behaviour for current FW).
        rt = self._runtime()  # _build_assignment() -> profile 'espTft'
        await rt.start()  # must NOT raise
        self.assertEqual(rt.state, "PRELOADING")
        sent_types = [json.loads(p)["type"] for p in rt.conn.websocket.sent]
        self.assertEqual(sent_types, ["lesson_prepare"])

    # 6) STEP_TIMEOUT distinct from PROTOCOL_SEQUENCE_ERROR ----------------------

    async def test_step_timeout_distinct_from_protocol_sequence_error(self):
        import asyncio

        conn = _FakeConn()
        manifest = _build_manifest()
        manifest["steps"][0]["timeoutSec"] = 0.05
        rt = self._runtime(conn=conn, manifest=manifest)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING + emits lesson_step
        self.assertEqual(rt.state, "RUNNING")
        # Do NOT send the step-ack; let the timeout fire.
        await asyncio.sleep(0.15)

        self.assertEqual(rt.state, "FAILED")
        self.assertIsNotNone(rt.last_error)
        self.assertEqual(rt.last_error.code, "STEP_TIMEOUT")

        codes = [
            f.get("body", {}).get("code")
            for f in self._sent_frames(conn)
            if f["type"] == "lesson_error"
        ]
        self.assertIn("STEP_TIMEOUT", codes)
        self.assertNotIn("PROTOCOL_SEQUENCE_ERROR", codes)

    async def test_step_timeout_floor_allows_slow_real_renderer_ack(self):
        import asyncio

        conn = _FakeConn()
        manifest = _build_manifest()
        manifest["steps"][0]["timeoutSec"] = 0.05
        rt = self._runtime(conn=conn, manifest=manifest, min_step_timeout_sec=0.2)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        await asyncio.sleep(0.1)

        self.assertEqual(rt.state, "RUNNING")
        await asyncio.sleep(0.15)
        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "STEP_TIMEOUT")

    async def test_step_timeout_forwards_terminal_lesson_failed_to_backend(self):
        # A FAILED transition (here STEP_TIMEOUT) must forward a durable terminal
        # lesson_failed event so the backend assignment leaves its single-active
        # slot and persists the failure (mirrors lesson_completed/lesson_abandoned).
        import asyncio

        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_manifest()
        manifest["steps"][0]["timeoutSec"] = 0.05
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING + emits lesson_step
        self.assertEqual(rt.state, "RUNNING")
        # Do NOT send the step-ack; let the per-step timeout fire.
        await asyncio.sleep(0.15)

        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "STEP_TIMEOUT")

        event_types = [
            event.get("type")
            for batch in forwarder.batches
            for event in batch.get("events", [])
        ]
        self.assertIn("lesson_failed", event_types)
        failed = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "lesson_failed"
        ]
        # Exactly one terminal failure, carrying the error code for the backend.
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["code"], "STEP_TIMEOUT")

    async def test_critical_preload_failure_forwards_terminal_lesson_failed(self):
        # A preload ASSET_CHECKSUM_MISMATCH also fails the run; it too must forward
        # a terminal lesson_failed (previously forwarded == []).
        from core.lesson.errors import LessonError

        conn = _FakeConn()
        forwarder = _FakeForwarder()
        asset_cache = _FakeAssetCache(ready=True)

        async def _raise_mismatch():
            raise LessonError("ASSET_CHECKSUM_MISMATCH", "bad asset", retryable=False)

        asset_cache.preload = _raise_mismatch
        rt = self._runtime(conn=conn, asset_cache=asset_cache, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))  # prepare-ack -> starts preload task
        await rt._preload_task

        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "ASSET_CHECKSUM_MISMATCH")
        event_types = [
            event.get("type")
            for batch in forwarder.batches
            for event in batch.get("events", [])
        ]
        self.assertIn("lesson_failed", event_types)

    # 7) inbound gap -> PROTOCOL_SEQUENCE_ERROR (distinct from STEP_TIMEOUT) -----

    async def test_inbound_gap_raises_protocol_sequence_error(self):
        conn = _FakeConn()
        rt = self._runtime(conn=conn)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))  # step-ack (last inbound = 3)
        self.assertEqual(rt.state, "RUNNING")

        # Jump the inbound envelope sequence forward (last+3 = 6, expected 4).
        await rt.on_lesson_progress(
            _progress(6, {"event": "step_completed", "result": "success"})
        )

        err_frames = [
            f for f in self._sent_frames(conn) if f["type"] == "lesson_error"
        ]
        self.assertTrue(err_frames)
        seq_errs = [
            f for f in err_frames if f["body"]["code"] == "PROTOCOL_SEQUENCE_ERROR"
        ]
        self.assertTrue(seq_errs)
        self.assertTrue(seq_errs[-1]["body"]["retryable"])
        # Distinct from STEP_TIMEOUT: the gap does NOT fail the run.
        self.assertNotEqual(rt.state, "FAILED")

    # 8) progress forwarded on the dedicated path --------------------------------

    async def test_progress_forwarded_on_dedicated_path(self):
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        rt = self._runtime(conn=conn, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        await rt.on_lesson_progress(
            _progress(
                4,
                {
                    "event": "step_completed",
                    "stepType": "model",
                    "result": "success",
                    "detail": {"tapTargetHit": True, "utterance": "Yes! barn!"},
                },
            )
        )

        completed = [
            b
            for b in forwarder.batches
            if b["events"] and b["events"][0].get("type") == "step_completed"
        ]
        self.assertTrue(completed, "step_completed not forwarded via forwarder.enqueue")
        ev = completed[0]["events"][0]
        self.assertEqual(ev["type"], "step_completed")
        self.assertEqual(ev["result"], "success")  # rename happens at POST, not here

    # 9) post_lesson_event renames result->outcome and strips utterance ----------

    async def test_post_lesson_event_renames_result_to_outcome_and_strips_utterance(
        self,
    ):
        mac = _load_real_manage_api_client()

        class _FakeResp:
            def __init__(self):
                self.status_code = 202
                self.content = b"{}"

            def raise_for_status(self):
                return None

            def json(self):
                return {"data": {}}

            async def aclose(self):
                return None

        class _FakeClient:
            def __init__(self):
                self.calls = []

            async def request(self, method, url, **kwargs):
                self.calls.append((method, url, kwargs))
                return _FakeResp()

        client = _FakeClient()
        batch = {
            "assignmentId": "a",
            "lessonId": "w01-d01-barn-say-it",
            "lessonVersion": 3,
            "sessionId": "s",
            "events": [
                {
                    "type": "step_completed",
                    "sequence": 4,
                    "stepId": "s4",
                    "result": "success",
                    "detail": {"tapTargetHit": True, "utterance": "Yes! barn!"},
                }
            ],
        }

        await mac.post_lesson_event(client, "http://b/v1", "dev1", batch)

        self.assertEqual(len(client.calls), 1)
        method, url, kwargs = client.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/devices/dev1/lesson-events"), url)
        posted = kwargs["json"]
        ev = posted["events"][0]
        self.assertEqual(ev["outcome"], "success")
        self.assertNotIn("result", ev)
        self.assertEqual(ev["detail"], {"tapTargetHit": True})
        self.assertNotIn("utterance", ev["detail"])

    # 10) S13: runtime brackets the preload window for the voice-latency alarm ----

    async def test_preload_window_bracketed_for_alarm(self):
        alarm = _RecordingAlarm()
        conn = _FakeConn()
        rt = self._runtime(conn=conn, asset_cache=_FakeAssetCache(ready=True), alarm=alarm)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))  # prepare-ack -> kicks _run_preload
        await rt._preload_task

        # The window opened then closed exactly once around the download phase.
        self.assertEqual(alarm.events, [("active", True), ("active", False)])
        self.assertEqual(alarm.max_depth, 1)
        self.assertEqual(alarm.depth, 0)

    # 11) S13: the window still closes when preload FAILS (finally) ---------------

    async def test_preload_window_closes_on_preload_failure(self):
        from core.lesson.errors import AssetChecksumMismatch

        alarm = _RecordingAlarm()
        conn = _FakeConn()
        rt = self._runtime(
            conn=conn,
            asset_cache=_FakeAssetCache(preload_error=AssetChecksumMismatch("poster")),
            alarm=alarm,
        )
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task

        self.assertEqual(rt.state, "FAILED")
        # Window opened AND closed despite the failure (finally guarantees it).
        self.assertEqual(alarm.events, [("active", True), ("active", False)])
        self.assertEqual(alarm.depth, 0)

    # 12) S13: the window still closes when preload is CANCELLED (teardown) ------

    async def test_preload_window_closes_on_preload_cancellation(self):
        class _NeverFinishingAssetCache(_FakeAssetCache):
            def __init__(self):
                super().__init__(ready=False)
                self.entered = asyncio.Event()
                self.release = asyncio.Event()

            async def preload(self):
                self.entered.set()
                await self.release.wait()
                return False

        alarm = _RecordingAlarm()
        cache = _NeverFinishingAssetCache()
        conn = _FakeConn()
        rt = self._runtime(conn=conn, asset_cache=cache, alarm=alarm)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await cache.entered.wait()

        rt._preload_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await rt._preload_task

        self.assertEqual(alarm.events, [("active", True), ("active", False)])
        self.assertEqual(alarm.depth, 0)

    # 13) S13: a None alarm is a safe no-op (dark / unwired) ----------------------

    async def test_no_alarm_is_noop(self):
        conn = _FakeConn()
        rt = self._runtime(conn=conn, asset_cache=_FakeAssetCache(ready=True), alarm=None)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task  # must not raise
        self.assertIn("lesson_start", [f["type"] for f in self._sent_frames(conn)])

    # ── P5 multi-step playback ──────────────────────────────────────────────────

    async def _drive_to_running(self, conn, rt):
        """prepare -> prepare-ack -> preload -> start -> start-ack (=> RUNNING + s4)."""
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))  # prepare-ack -> preload
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING + emits lesson_step s4

    async def test_lesson_step_frame_is_sent_before_spoken_step_prompt(self):
        conn = _FakeConn()

        class _PromptOrderProvider(_RecordingLessonVoiceProvider):
            def __init__(self):
                super().__init__()
                self.prompt_saw_step_frame = []

            async def speak_lesson_step_prompt(self, text):
                frames = [json.loads(payload) for payload in conn.websocket.sent]
                self.prompt_saw_step_frame.append(
                    any(frame.get("type") == "lesson_step" and frame.get("stepId") == "s4" for frame in frames)
                )
                return await super().speak_lesson_step_prompt(text)

        provider = _PromptOrderProvider()
        conn.voice_provider = provider
        rt = self._runtime(conn=conn)

        await self._drive_to_running(conn, rt)

        self.assertEqual(provider.prompt_saw_step_frame, [])
        step_frame = next(f for f in self._sent_frames(conn) if f["type"] == "lesson_step")
        await rt.on_lesson_ack(_ack(step_frame["sequence"], 3, step_id=step_frame["stepId"]))

        self.assertEqual(provider.prompt_saw_step_frame, [True])

    async def test_lesson_step_prompt_waits_for_firmware_ack_before_speaking_or_listening(self):
        conn = _FakeConn()

        class _OrderedProvider(_RecordingLessonVoiceProvider):
            def __init__(self):
                super().__init__()
                self.events = []

            async def speak_lesson_step_prompt(self, text):
                self.events.append(("prompt", text))
                return await super().speak_lesson_step_prompt(text)

            async def open_lesson_child_response_window(self):
                self.events.append(("listen_window", None))
                return await super().open_lesson_child_response_window()

        provider = _OrderedProvider()
        conn.voice_provider = provider
        manifest = _build_class_steps_manifest([("s4", "model", "interactive")])
        manifest["steps"][0]["prompt"] = "Con nhìn hình rồi nói cùng TeeBot nhé: barn."
        rt = self._runtime(conn=conn, manifest=manifest)

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        step_frame = next(f for f in self._sent_frames(conn) if f["type"] == "lesson_step")
        self.assertEqual(step_frame["stepId"], "s4")
        self.assertEqual(provider.events, [])
        self.assertEqual(provider.prompts, [])
        self.assertEqual(provider.child_response_windows, [])

        await rt.on_lesson_ack(_ack(step_frame["sequence"], 3, step_id="s4"))

        self.assertEqual(
            provider.events,
            [
                ("prompt", "Con nhìn hình rồi nói cùng TeeBot nhé: barn."),
                ("listen_window", None),
            ],
        )
        self.assertEqual(provider.prompts, ["Con nhìn hình rồi nói cùng TeeBot nhé: barn."])
        self.assertEqual(provider.child_response_windows, [True])

    async def test_lesson_step_prompt_handoff_log_includes_step_id_after_frame(self):
        events = []

        class _CapturingLogger(_DummyLogger):
            def bind(self, **kwargs):
                self.bound = kwargs
                return self

            def info(self, message, *args, **kwargs):
                events.append(("info", str(message)))
                return None

        conn = _FakeConn()
        conn.logger = _CapturingLogger()
        conn.voice_provider = _RecordingLessonVoiceProvider()
        rt = self._runtime(conn=conn)

        await self._drive_to_running(conn, rt)

        step_frame = next(f for f in self._sent_frames(conn) if f["type"] == "lesson_step")
        await rt.on_lesson_ack(_ack(step_frame["sequence"], 3, step_id=step_frame["stepId"]))

        messages = [message for _level, message in events]
        lesson_step_index = next(i for i, message in enumerate(messages) if "emit lesson_step" in message)
        prompt_index = next(i for i, message in enumerate(messages) if "lesson step prompt" in message)
        self.assertGreater(prompt_index, lesson_step_index)
        self.assertIn("stepId=s4", messages[prompt_index])
        self.assertIn("handoff=1", messages[prompt_index])

    async def test_completion_reads_back_the_assignment_state_it_caused(self):
        """The robot must observe the terminal state, not just report it.

        The runtime pulls the assignment on connect and forwards the completion
        afterwards, but never re-reads the state it just caused -- so the backend row
        says COMPLETED while nothing on the device can show it, and the loop is never
        closed from the robot's side. Same on hardware, so this is not a simulation
        artefact.
        """
        import config.manage_api_client as mac

        events = []

        class _CapturingLogger(_DummyLogger):
            def bind(self, **kwargs):
                self.bound = kwargs
                return self

            def info(self, message, *args, **kwargs):
                events.append(("info", str(message)))
                return None

        read_backs = []

        # Mirrors the REAL signature: get_current_assignment returns the assignment
        # dict itself, not a (payload, etag) tuple. An earlier version of this stub
        # returned a tuple, which made the test pass against an interface that does not
        # exist while the production path raised ValueError on every completion.
        async def _get_assignment(client, base_url, device_id, *, token=None,
                                  include_terminal=False):
            # The read-back MUST opt in: without it the backend answers with the ACTIVE
            # assignment only, which is null once the lesson completes -- so a recorded
            # completion and a lost one look identical.
            read_backs.append((device_id, include_terminal))
            return {
                "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
                "state": "COMPLETED",
            }

        # A forwarder that carries the backend handle, as the real one does: the
        # read-back reaches the backend through the same base_url/device/token the
        # completion was forwarded to, rather than opening a second source of truth.
        forwarder = _FakeForwarder()
        forwarder.base_url = "http://backend.test/v1"
        forwarder.device_id = "backend-device-1"
        forwarder.token = "device-token"

        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.logger = _CapturingLogger()
        rt = self._runtime(
            conn=conn, manifest=_build_multistep_manifest(), forwarder=forwarder
        )

        saved = mac.get_current_assignment
        mac.get_current_assignment = _get_assignment
        try:
            # A NATURAL completion: every step answered, so the runtime emits
            # lesson_stop itself. `stop()` is the administrative path and projects
            # lesson_abandoned, which is a different terminal event.
            await self._drive_to_running(conn, rt)
            await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
            await rt.on_lesson_progress(
                _progress(4, {"event": "step_completed", "stepType": "model",
                              "result": "success", "detail": {"recognizedText": "barn"}}, step_id="s4")
            )
            await rt.on_lesson_ack(_ack(4, 5, step_id="s5"))
            await rt.on_lesson_progress(
                _progress(6, {"event": "step_completed", "stepType": "listen",
                              "result": "success", "detail": {"recognizedText": "barn"}}, step_id="s5")
            )
            await rt.on_lesson_ack(_ack(5, 7))
            await rt.drain_terminal_readback()
        finally:
            mac.get_current_assignment = saved

        self.assertEqual(rt.state, "COMPLETED")
        self.assertTrue(read_backs, "runtime never re-read assignment/current")
        self.assertEqual(read_backs[0][1], True, "read-back did not ask for the terminal state")
        line = next(
            (m for _l, m in events
             if "assignment/current read-back completion observed" in m
             and "state=COMPLETED" in m),
            None,
        )
        self.assertIsNotNone(line, "terminal assignment state was never logged")

    async def test_read_back_warns_when_the_completion_never_landed(self):
        """The read-back's real job: catch a completion the backend never recorded.

        F-T53-17 is exactly this failure — a rate-limited terminal batch was discarded,
        the robot finished the lesson, and the assignment sat RUNNING forever with
        nothing anywhere saying so. After a successful completion the slot is released,
        so `assignment/current` answers "no active assignment"; an assignment that is
        STILL ACTIVE after we completed it means our completion did not land, and that
        must be loud rather than logged as a routine observation.
        """
        import config.manage_api_client as mac

        events = []

        class _CapturingLogger(_DummyLogger):
            def bind(self, **kwargs):
                self.bound = kwargs
                return self

            def info(self, message, *args, **kwargs):
                events.append(("info", str(message)))
                return None

            def warning(self, message, *args, **kwargs):
                events.append(("warning", str(message)))
                return None

        forwarder = _FakeForwarder()
        forwarder.base_url = "http://backend.test/v1"
        forwarder.device_id = "backend-device-1"
        forwarder.token = "device-token"

        async def _get_assignment(client, base_url, device_id, *, token=None,
                                  include_terminal=False):
            # Still active: the completion never reached the backend.
            return {
                "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
                "state": "RUNNING",
            }

        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.logger = _CapturingLogger()
        rt = self._runtime(
            conn=conn, manifest=_build_multistep_manifest(), forwarder=forwarder
        )

        saved = mac.get_current_assignment
        mac.get_current_assignment = _get_assignment
        try:
            await self._drive_to_running(conn, rt)
            await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
            await rt.on_lesson_progress(
                _progress(4, {"event": "step_completed", "stepType": "model",
                              "result": "success", "detail": {"recognizedText": "barn"}}, step_id="s4")
            )
            await rt.on_lesson_ack(_ack(4, 5, step_id="s5"))
            await rt.on_lesson_progress(
                _progress(6, {"event": "step_completed", "stepType": "listen",
                              "result": "success", "detail": {"recognizedText": "barn"}}, step_id="s5")
            )
            await rt.on_lesson_ack(_ack(5, 7))
            await rt.drain_terminal_readback()
        finally:
            mac.get_current_assignment = saved

        warnings = [m for level, m in events if level == "warning"]
        self.assertTrue(
            any("completion not observed" in m for m in warnings),
            f"a lost completion was not surfaced; warnings={warnings}",
        )

    async def test_completion_read_back_waits_for_its_own_forward_to_land(self):
        """Reading back immediately races the completion the robot just sent.

        The forward is queued and drained asynchronously, so a read-back fired the
        instant the runtime reaches COMPLETED reaches the backend BEFORE its own
        completion does, and faithfully reports `state=RUNNING` -- worse than not
        reading back at all, because it looks like the completion was rejected.
        Observed on a real simulated run.
        """
        import config.manage_api_client as mac

        events = []

        class _CapturingLogger(_DummyLogger):
            def bind(self, **kwargs):
                self.bound = kwargs
                return self

            def info(self, message, *args, **kwargs):
                events.append(("info", str(message)))
                return None

        # The backend only reports COMPLETED once the completion has been drained --
        # exactly the ordering the production forwarder imposes.
        forwarder = _FakeForwarder()
        forwarder.base_url = "http://backend.test/v1"
        forwarder.device_id = "backend-device-1"
        forwarder.token = "device-token"
        drained = {"done": False}

        async def _drain():
            drained["done"] = True

        forwarder.drain = _drain

        async def _get_assignment(client, base_url, device_id, *, token=None,
                                  include_terminal=False):
            return {
                "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
                "state": "COMPLETED" if drained["done"] else "RUNNING",
            }

        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        conn.logger = _CapturingLogger()
        rt = self._runtime(
            conn=conn, manifest=_build_multistep_manifest(), forwarder=forwarder
        )

        saved = mac.get_current_assignment
        mac.get_current_assignment = _get_assignment
        try:
            await self._drive_to_running(conn, rt)
            await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
            await rt.on_lesson_progress(
                _progress(4, {"event": "step_completed", "stepType": "model",
                              "result": "success", "detail": {"recognizedText": "barn"}}, step_id="s4")
            )
            await rt.on_lesson_ack(_ack(4, 5, step_id="s5"))
            await rt.on_lesson_progress(
                _progress(6, {"event": "step_completed", "stepType": "listen",
                              "result": "success", "detail": {"recognizedText": "barn"}}, step_id="s5")
            )
            await rt.on_lesson_ack(_ack(5, 7))
            await rt.drain_terminal_readback()
        finally:
            mac.get_current_assignment = saved

        states = [m for _l, m in events if "assignment/current read-back" in m]
        self.assertTrue(states, "no read-back was logged")
        self.assertIn("completion observed", states[-1])
        self.assertIn("state=COMPLETED", states[-1])

    async def test_lesson_session_is_joinable_to_the_connection_session(self):
        """One run carries two session identities and nothing recorded the join.

        The hello ack hands the device the CONNECTION session; a lesson run then mints
        its own identity on purpose ("A lesson run owns its protocol/event identity ...
        must not inherit the conversational websocket session"). Both are correct, but
        with no line naming the pair, neither an operator nor the E2E gate can tell that
        two ids describe one run — a capture reads as evidence from two sessions.
        """
        events = []

        class _CapturingLogger(_DummyLogger):
            def bind(self, **kwargs):
                self.bound = kwargs
                return self

            def info(self, message, *args, **kwargs):
                events.append(("info", str(message)))
                return None

        conn = _FakeConn(session_id="conn-session-abc")
        conn.logger = _CapturingLogger()
        rt = self._runtime(conn=conn)

        bridge = next(
            (m for _l, m in events if "lesson session bound" in m),
            None,
        )
        self.assertIsNotNone(bridge, "runtime never recorded the session join")
        self.assertIn(f"sessionId={rt.session_id}", bridge)
        self.assertIn("connectionSessionId=conn-session-abc", bridge)

    async def test_emitted_frame_logs_carry_the_shared_checkpoint_contract(self):
        """Every emitted frame must log the facts the E2E gate reads off it.

        `type=<frame>` is what identifies a line as "the SERVER sent this frame". Without
        it the only send-evidence in a capture is the DEVICE's `serial RX` line, which is
        necessarily later, so an ordered verification credits the send to the receive and
        every server-side event in between (preload_ready, lesson_started) falls behind
        the cursor and reports as missing. `sequence=` is what pairs a step frame with the
        ack that acknowledges it; `media=` records what the renderer was actually pointed
        at, so a frame shipping a placeholder is distinguishable from a device that failed
        to draw. All three were absent, and all three are load-bearing for T5.3/T5.4.
        """
        events = []

        class _CapturingLogger(_DummyLogger):
            def bind(self, **kwargs):
                self.bound = kwargs
                return self

            def info(self, message, *args, **kwargs):
                events.append(("info", str(message)))
                return None

        conn = _FakeConn()
        conn.logger = _CapturingLogger()
        rt = self._runtime(conn=conn)

        await self._drive_to_running(conn, rt)

        messages = [message for _level, message in events]
        prepare = next(m for m in messages if "emit lesson_prepare" in m)
        self.assertIn("type=lesson_prepare", prepare)
        self.assertIn("sequence=", prepare)

        start = next(m for m in messages if "emit lesson_start" in m)
        self.assertIn("type=lesson_start", start)

        step = next(m for m in messages if "emit lesson_step" in m)
        self.assertIn("type=lesson_step", step)
        self.assertIn("sequence=", step)
        self.assertIn("media=", step)
        # Space-separated, never comma-joined: the contract scans for media URLs with a
        # regex, and commas run two URLs together into one unmatchable token.
        self.assertNotIn("media=none", step)
        self.assertNotIn(",sd://", step)

    async def test_manifest_fetch_log_declares_the_full_step_roster(self):
        """`stepCount` says how many steps were served, not which ones.

        Without the roster, "the robot completed every step" is indistinguishable from
        "the robot completed nine of something" — a truncated or reordered manifest is
        then undetectable downstream.
        """
        events = []

        class _CapturingLogger(_DummyLogger):
            def bind(self, **kwargs):
                self.bound = kwargs
                return self

            def info(self, message, *args, **kwargs):
                events.append(("info", str(message)))
                return None

        conn = _FakeConn()
        conn.logger = _CapturingLogger()
        rt = self._runtime(conn=conn)
        await self._drive_to_running(conn, rt)

        summary = _manifest_steps_log_summary(rt.manifest)
        self.assertIn("steps", summary)
        self.assertTrue(summary["steps"])
        for step in summary["steps"]:
            self.assertIn("id", step)
            self.assertIn("order", step)

    async def test_child_response_window_log_includes_step_id_after_prompt_handoff(self):
        events = []

        class _CapturingLogger(_DummyLogger):
            def bind(self, **kwargs):
                self.bound = kwargs
                return self

            def info(self, message, *args, **kwargs):
                events.append(("info", str(message)))
                return None

        conn = _FakeConn()
        conn.logger = _CapturingLogger()
        conn.voice_provider = _RecordingLessonVoiceProvider()
        rt = self._runtime(conn=conn)

        await self._drive_to_running(conn, rt)

        step_frame = next(f for f in self._sent_frames(conn) if f["type"] == "lesson_step")
        await rt.on_lesson_ack(_ack(step_frame["sequence"], 3, step_id=step_frame["stepId"]))

        messages = [message for _level, message in events]
        prompt_index = next(i for i, message in enumerate(messages) if "lesson step prompt" in message)
        window_index = next(i for i, message in enumerate(messages) if "child response window opened" in message)
        self.assertGreater(window_index, prompt_index)
        self.assertIn("stepId=s4", messages[window_index])
        self.assertIn("listening=1", messages[window_index])

    # 13) a 2-step lesson plays ALL steps in order, one lesson_step per step --------

    async def test_multi_step_plays_all_steps_in_order(self):
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        rt = self._runtime(
            conn=conn, manifest=_build_multistep_manifest(), forwarder=forwarder
        )
        await self._drive_to_running(conn, rt)

        # After start-ack, exactly ONE lesson_step (s4) is on the wire; s5 not yet.
        step_frames = [f for f in self._sent_frames(conn) if f["type"] == "lesson_step"]
        self.assertEqual([f["stepId"] for f in step_frames], ["s4"])
        self.assertEqual(rt.state, "RUNNING")

        # Complete the INTERACTIVE s4 ('model'): step-ack (S->F seq 3) + step_completed
        # progress (F->S seq 4) — model waits for BOTH the ack AND step_completed.
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        await rt.on_lesson_progress(
            _progress(4, {"event": "step_completed", "stepType": "model",
                          "result": "success", "detail": {"recognizedText": "barn"}}, step_id="s4")
        )

        # s4 completion advances to s5 — the SECOND lesson_step is emitted now, and
        # the run is NOT stopped yet (no lesson_stop while a step remains).
        types = [f["type"] for f in self._sent_frames(conn)]
        step_frames = [f for f in self._sent_frames(conn) if f["type"] == "lesson_step"]
        self.assertEqual([f["stepId"] for f in step_frames], ["s4", "s5"])
        self.assertNotIn("lesson_stop", types)
        self.assertEqual(rt.state, "RUNNING")

        # s5 lesson_step rides the next S->F sequence (4), preserving monotonicity.
        s5 = step_frames[1]
        self.assertEqual(s5["sequence"], 4)
        # s5 is the INTERACTIVE 'listen' step.
        self.assertEqual(s5["body"]["stepType"], "listen")

        # Complete the INTERACTIVE s5 ('listen') with its ack plus child response
        # evidence. The ack opens the response window; progress closes it.
        await rt.on_lesson_ack(_ack(4, 5, step_id="s5"))
        await rt.on_lesson_progress(
            _progress(6, {"event": "step_completed", "stepType": "listen",
                          "result": "success", "detail": {"recognizedText": "barn"}}, step_id="s5")
        )

        # Last step done after child response -> lesson_stop (S->F seq 5) with the
        # REAL stepsCompleted=2 (interactive s4 + interactive s5).
        sent = self._sent_frames(conn)
        stop = [f for f in sent if f["type"] == "lesson_stop"]
        self.assertEqual(len(stop), 1)
        self.assertEqual(stop[0]["sequence"], 5)
        self.assertEqual(rt.state, "RUNNING")  # COMPLETED only after stop-ack

        # stop-ack (body.acks 5, F->S seq 7) -> COMPLETED + lesson_completed forward.
        await rt.on_lesson_ack(_ack(5, 7))
        self.assertEqual(rt.state, "COMPLETED")

        completed = [
            b for b in forwarder.batches
            if b["events"] and b["events"][0].get("type") == "lesson_completed"
        ]
        self.assertTrue(completed, "lesson_completed not forwarded")
        self.assertEqual(completed[0]["events"][0]["summary"]["stepsCompleted"], 2)

    # 14) the full S->F sequence + per-step acks/progress match the additive thread -

    async def test_multi_step_sequence_matches_additive_fixture_thread(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        rt = self._runtime(conn=conn, manifest=_build_multistep_manifest())
        await self._drive_to_running(conn, rt)
        # Interactive s4: ack + child response evidence.
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        await rt.on_lesson_progress(
            _progress(4, {"event": "step_completed", "stepType": "model",
                          "result": "success", "detail": {"recognizedText": "barn"}}, step_id="s4")
        )
        # Interactive s5 ('listen'): ack opens child response window; child response
        # evidence completes the step.
        await rt.on_lesson_ack(_ack(4, 5, step_id="s5"))
        await rt.on_lesson_progress(
            _progress(6, {"event": "step_completed", "stepType": "listen",
                          "result": "success", "detail": {"recognizedText": "barn"}}, step_id="s5")
        )
        # Stop-ack: F->S seq 7, after s5 child-response progress.
        await rt.on_lesson_ack(_ack(5, 7))

        # The S->F command stream the sender produced, in order.
        sent = self._sent_frames(conn)
        sf = [(f["type"], f["sequence"], f["stepId"]) for f in sent]
        self.assertEqual(
            sf,
            [
                ("lesson_prepare", 1, None),
                ("lesson_start", 2, None),
                ("lesson_step", 3, "s4"),
                ("lesson_step", 4, "s5"),
                ("lesson_stop", 5, None),
            ],
        )

        # The emitted s5 lesson_step is byte-consistent with the additive fixture.
        s5_sent = next(
            f for f in sent if f["type"] == "lesson_step" and f["stepId"] == "s5"
        )
        got = dict(s5_sent)
        got.pop("timestamp")
        want = copy.deepcopy(FIX["multiStep"]["frames"]["lesson_step_s5"])
        want.pop("timestamp")
        self.assertEqual(got, want, "s5 lesson_step not byte-consistent with fixture")

    # 15) per-step STEP_TIMEOUT on step 1 halts the run (does NOT advance to step 2) -

    async def test_per_step_timeout_on_first_step_halts_before_second(self):
        import asyncio

        conn = _FakeConn()
        manifest = _build_multistep_manifest()
        manifest["steps"][0]["timeoutSec"] = 0.05  # s4 times out
        rt = self._runtime(conn=conn, manifest=manifest)
        await self._drive_to_running(conn, rt)
        self.assertEqual(rt.state, "RUNNING")

        # Never ack s4 -> its per-step STEP_TIMEOUT fires -> FAILED, s5 never emitted.
        await asyncio.sleep(0.15)
        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "STEP_TIMEOUT")

        step_ids = [
            f["stepId"] for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        ]
        self.assertEqual(step_ids, ["s4"])  # s5 was never reached
        self.assertNotIn(
            "lesson_stop", [f["type"] for f in self._sent_frames(conn)]
        )
        # Distinctness preserved: STEP_TIMEOUT, not PROTOCOL_SEQUENCE_ERROR.
        codes = [
            f.get("body", {}).get("code")
            for f in self._sent_frames(conn)
            if f["type"] == "lesson_error"
        ]
        self.assertIn("STEP_TIMEOUT", codes)
        self.assertNotIn("PROTOCOL_SEQUENCE_ERROR", codes)

    # 16) a step's completion latch never leaks into the next step ------------------

    async def test_step_latches_reset_between_steps(self):
        # Uses an INTERACTIVE second step (listen) so the "ack alone must not stop"
        # invariant holds — a passive second step would (correctly) auto-advance on
        # its ack, which is covered separately by the passive auto-advance tests.
        conn = _FakeConn()
        manifest = _build_two_interactive_step_manifest()  # model (s4) -> listen (s5)
        rt = self._runtime(conn=conn, manifest=manifest)
        await self._drive_to_running(conn, rt)

        # Complete s4 fully.
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        await rt.on_lesson_progress(
            _progress(
                4,
                {
                    "event": "step_completed",
                    "result": "success",
                    "detail": {"recognizedText": "barn"},
                },
                step_id="s4",
            )
        )
        # s5 is now outstanding with FRESH latches: not acked, not completed.
        self.assertFalse(rt._step_acked)
        self.assertFalse(rt._step_completed)
        self.assertEqual(rt._step_id, "s5")
        # No premature stop: the INTERACTIVE s5's ack alone (without its
        # step_completed) must not stop — it still waits for the progress.
        await rt.on_lesson_ack(_ack(4, 5, step_id="s5"))
        self.assertNotIn(
            "lesson_stop", [f["type"] for f in self._sent_frames(conn)]
        )

    # ── P5 PASSIVE-step auto-advance (playability defect fix) ───────────────────

    # 17) a single PASSIVE step auto-advances on its ack — no hang, no STEP_TIMEOUT --

    async def test_passive_step_auto_advances_and_reports_step_completed(self):
        # A lesson whose ONLY step is passive narration (greeting). The firmware
        # acks the lesson_step but — by contract — NEVER sends a step_completed for
        # it. Pre-fix this hung forever in RUNNING (the per-step timeout is cancelled
        # on ack, so it can no longer fire). It must now auto-advance to lesson_stop
        # on the ack ALONE, with stepsCompleted=1 and NO STEP_TIMEOUT.
        import asyncio

        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_steps_manifest([("s4", "greeting")])
        # A short timeoutSec proves the run does NOT depend on / get failed by it.
        manifest["steps"][0]["timeoutSec"] = 0.05
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))  # prepare-ack -> preload
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING + emits greeting step
        self.assertEqual(rt.state, "RUNNING")

        # Ack the passive step (S->F seq 3). This ALONE must finish it -> lesson_stop.
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        types = [f["type"] for f in self._sent_frames(conn)]
        self.assertIn("lesson_stop", types)
        self.assertEqual(rt._steps_completed, 1)
        passive_completions = [
            event
            for batch in forwarder.batches
            for event in batch["events"]
            if event.get("type") == "step_completed"
            and event.get("detail", {}).get("source") == "passive_runtime"
        ]
        self.assertEqual(passive_completions, [{
            "type": "step_completed",
            "sequence": -3,
            "stepId": "s4",
            "stepType": "greeting",
            "result": "success",
            "detail": {"source": "passive_runtime"},
        }])

        # Let any leftover timeout timer elapse: it must NOT fail an acked passive step.
        await asyncio.sleep(0.1)
        self.assertNotEqual(rt.state, "FAILED")
        codes = [
            f.get("body", {}).get("code")
            for f in self._sent_frames(conn)
            if f["type"] == "lesson_error"
        ]
        self.assertNotIn("STEP_TIMEOUT", codes)

        # stop-ack (body.acks 4, F->S seq 4) -> COMPLETED with stepsCompleted=1.
        await rt.on_lesson_ack(_ack(4, 4))
        self.assertEqual(rt.state, "COMPLETED")
        completed = [
            b for b in forwarder.batches
            if b["events"] and b["events"][0].get("type") == "lesson_completed"
        ]
        self.assertTrue(completed)
        self.assertEqual(completed[0]["events"][0]["summary"]["stepsCompleted"], 1)

    async def test_passive_dwell_reports_completion_once_after_dwell(self):
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_steps_manifest([("s4", "greeting")])
        manifest["steps"][0]["dwellSec"] = 0.01
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))

        def completions():
            return [
                event
                for batch in forwarder.batches
                for event in batch["events"]
                if event.get("type") == "step_completed"
                and event.get("detail", {}).get("source") == "passive_runtime"
            ]

        self.assertEqual(completions(), [])
        await asyncio.sleep(0.03)
        self.assertEqual(len(completions()), 1)
        self.assertFalse(rt._complete_passive_step())
        self.assertEqual(len(completions()), 1)

    async def test_v2_passive_final_step_stops_when_completion_motion_is_missing(self):
        conn = _FakeConn(
            features={
                "lesson": True,
                "renderer": [
                    "teebot-lesson-renderer.v1",
                    "teebot-lesson-renderer.v2",
                ],
            }
        )
        conn.device_id = "robot-01"
        conn.config = {
            "lesson": {
                "renderer_v2_enabled": True,
                "rollout_device_allowlist": ["robot-01"],
            }
        }
        manifest = _build_class_steps_manifest(
            [("s9", "celebrate", "passive")]
        )
        manifest["manifestVersion"] = "teebot-lesson-renderer.v2"
        self.assertNotIn("motion", manifest["steps"][0])

        rt = self._runtime(conn=conn, manifest=manifest)
        await self._drive_to_running(conn, rt)
        rt._step_acked = True
        rt._step_completed = True
        await rt._maybe_finish_step()
        await rt._visual_transition_task

        self.assertIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt._steps_completed, 1)

    # 18) greeting -> model -> celebrate plays to lesson_stop (mixed classes) --------

    async def test_greeting_model_celebrate_plays_to_stop(self):
        # The authored shape that motivated the fix: a passive greeting, an
        # interactive model, then a passive celebrate. Pre-fix the run HUNG on the
        # greeting (passive, no step_completed). It must now play ALL three to
        # lesson_stop with stepsCompleted=3.
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_steps_manifest(
            [("s1", "greeting"), ("s4", "model"), ("s9", "celebrate")]
        )
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))  # prepare-ack
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING + greeting (S->F 3)

        # First lesson_step on the wire is the greeting; nothing else yet.
        step_ids = [
            f["stepId"] for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        ]
        self.assertEqual(step_ids, ["s1"])

        # PASSIVE greeting: ack ALONE auto-advances to the model step (S->F 4).
        await rt.on_lesson_ack(_ack(3, 3, step_id="s1"))
        step_ids = [
            f["stepId"] for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        ]
        self.assertEqual(step_ids, ["s1", "s4"])
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])

        # INTERACTIVE model: requires BOTH ack (S->F 4) AND step_completed (F->S 5).
        await rt.on_lesson_ack(_ack(4, 4, step_id="s4"))
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        await rt.on_lesson_progress(
            _progress(5, {"event": "step_completed", "stepType": "model",
                          "result": "success", "detail": {"recognizedText": "barn"}}, step_id="s4")
        )
        # model done -> celebrate emitted (S->F 5).
        step_ids = [
            f["stepId"] for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        ]
        self.assertEqual(step_ids, ["s1", "s4", "s9"])
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])

        # PASSIVE celebrate: ack ALONE (body.acks 5, F->S seq 6) -> lesson_stop.
        await rt.on_lesson_ack(_ack(5, 6, step_id="s9"))
        stop = [f for f in self._sent_frames(conn) if f["type"] == "lesson_stop"]
        self.assertEqual(len(stop), 1)
        self.assertEqual(rt._steps_completed, 3)

        # stop-ack -> COMPLETED with stepsCompleted=3.
        await rt.on_lesson_ack(_ack(6, 7))
        self.assertEqual(rt.state, "COMPLETED")
        completed = [
            b for b in forwarder.batches
            if b["events"] and b["events"][0].get("type") == "lesson_completed"
        ]
        self.assertTrue(completed)
        self.assertEqual(completed[0]["events"][0]["summary"]["stepsCompleted"], 3)

    async def test_full_seed_story_teaches_all_nine_steps_and_waits_for_child_turns(self):
        conn = _FakeConn()
        conn.voice_provider = _RecordingLessonVoiceProvider()
        forwarder = _FakeForwarder()
        manifest = _build_full_seed_story_manifest()
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)

        await self._drive_to_running(conn, rt)
        inbound_seq = 3

        for expected_step in manifest["steps"]:
            step_frames = [f for f in self._sent_frames(conn) if f["type"] == "lesson_step"]
            current = step_frames[-1]
            self.assertEqual(current["stepId"], expected_step["id"])
            self.assertEqual(current["body"]["prompt"], expected_step["prompt"])
            self.assertEqual(current["body"]["completionClass"], expected_step["completionClass"])
            self.assertEqual(set(current["body"]["scene"]), {"backgroundScene", "teachingObject", "robotOverlay"})
            self.assertEqual(current["body"]["scene"]["robotOverlay"]["asset"]["key"], f"robotOverlay.{expected_step['scene']['robotOverlay']['pose']}")
            _assert_no_inline_media_payload(self, current["body"], path=f"full_story.{expected_step['id']}")

            before_count = len(step_frames)
            await rt.on_lesson_ack(_ack(current["sequence"], inbound_seq, step_id=expected_step["id"]))
            inbound_seq += 1

            if expected_step["completionClass"] == "interactive":
                # Render ack opens the child-response window but must not advance the
                # story until the child actually speaks.
                self.assertEqual(len([f for f in self._sent_frames(conn) if f["type"] == "lesson_step"]), before_count)
                self.assertTrue(
                    await rt.on_child_response(
                        f"con trả lời {expected_step['id']} barn",
                        source="voice_transcript",
                    )
                )
            elif expected_step["id"] != "s9":
                self.assertEqual(len([f for f in self._sent_frames(conn) if f["type"] == "lesson_step"]), before_count + 1)

        step_ids = [f["stepId"] for f in self._sent_frames(conn) if f["type"] == "lesson_step"]
        self.assertEqual(step_ids, [step["id"] for step in manifest["steps"]])
        self.assertEqual(conn.voice_provider.child_response_windows, [True, True, True, True])
        expected_prompts = []
        for step in manifest["steps"]:
            expected_prompts.append(step["prompt"])
            if step["completionClass"] == "interactive":
                expected_prompts.append("Đúng rồi! barn!")
        self.assertEqual(conn.voice_provider.prompts, expected_prompts)
        for prompt in conn.voice_provider.prompts:
            self.assertNotRegex(prompt.lower(), r"\b(wrong|sai|không đúng)\b")
        self.assertEqual(self._sent_frames(conn)[-1]["type"], "lesson_stop")
        self.assertEqual(rt._steps_completed, 9)

        await rt.on_lesson_ack(_ack(self._sent_frames(conn)[-1]["sequence"], inbound_seq))
        self.assertEqual(rt.state, "COMPLETED")
        completed = [
            b for b in forwarder.batches
            if b["events"] and b["events"][0].get("type") == "lesson_completed"
        ]
        self.assertTrue(completed)
        self.assertEqual(completed[0]["events"][0]["summary"]["stepsCompleted"], 9)

    async def test_sd_pack_preserves_authored_robot_overlay_pose_per_step(self):
        class _PoseAssetCache(_FakeAssetCache):
            def asset_pack_manifest(self, *, assignment_version, lesson_id, lesson_version, manifest_checksum):
                pack = super().asset_pack_manifest(
                    assignment_version=assignment_version,
                    lesson_id=lesson_id,
                    lesson_version=lesson_version,
                    manifest_checksum=manifest_checksum,
                )
                pack["assets"] = [
                    {
                        "key": key,
                        "path": path,
                        "sha256": sha,
                        "mediaType": media_type,
                        "critical": critical,
                        "localPath": local_path,
                        "state": "READY",
                        "checksumOk": True,
                    }
                    for key, path, sha, media_type, critical, local_path in [
                        (
                            "backgroundScene.poster",
                            "barn-round-field-poster.jpg",
                            "2e3b77c7ee3c07381e46a6c9f2412c0d39ff14f08a569f42336299baa0502990",
                            "image/jpeg",
                            True,
                            self._local_urls["barn-round-field-poster.jpg"],
                        ),
                        (
                            "teachingObject.barn",
                            "barn.png",
                            "eac30a7ddf3f14df79f27c3eb39f2114f3a780d5670bb11ef62446f5fa5dcbb9",
                            "image/png",
                            True,
                            self._local_urls["barn.png"],
                        ),
                        (
                            "robotOverlay.teach",
                            "bright-teach.png",
                            "40f9c095b11a67c023f62847f498cc557e7fcef45762d41787dafffd96a60b34",
                            "image/png",
                            False,
                            self._local_urls["bright-teach.png"],
                        ),
                        (
                            "robotOverlay.listening",
                            "bright-listening.png",
                            "6f4d2c8f9b0e1a234567890abcdef1234567890abcdef1234567890abcdef12",
                            "image/png",
                            False,
                            self._local_urls["bright-listening.png"],
                        ),
                        (
                            "robotOverlay.thinking",
                            "bright-thinking.png",
                            "7a5e3d9c8b1f0a234567890abcdef1234567890abcdef1234567890abcdef34",
                            "image/png",
                            False,
                            self._local_urls["bright-thinking.png"],
                        ),
                        (
                            "robotOverlay.celebrate",
                            "bright-celebrate.png",
                            "8b6f4e0d9c2a1b34567890abcdef1234567890abcdef1234567890abcdef56",
                            "image/png",
                            False,
                            self._local_urls["bright-celebrate.png"],
                        ),
                    ]
                ]
                return pack

        pose_by_step = {
            "s1": ("teach", "robotOverlay.teach", "bright-teach.png"),
            "s4": ("teach", "robotOverlay.teach", "bright-teach.png"),
            "s5": ("listening", "robotOverlay.listening", "bright-listening.png"),
            "s7": ("thinking", "robotOverlay.thinking", "bright-thinking.png"),
            "s9": ("celebrate", "robotOverlay.celebrate", "bright-celebrate.png"),
        }
        local_urls = {
            "barn-round-field-poster.jpg": "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/backgroundScene.poster",
            "barn.png": "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/teachingObject.barn",
            "bright-teach.png": "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/robotOverlay.teach",
            "bright-listening.png": "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/robotOverlay.listening",
            "bright-thinking.png": "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/robotOverlay.thinking",
            "bright-celebrate.png": "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3/robotOverlay.celebrate",
        }
        local_urls.update(
            {
                f"assets/robot/poses/{name}": local_urls[name]
                for name in (
                    "bright-teach.png",
                    "bright-listening.png",
                    "bright-thinking.png",
                    "bright-celebrate.png",
                )
            }
        )
        manifest = _build_class_steps_manifest(
            [
                ("s1", "greeting", "passive"),
                ("s4", "model", "interactive"),
                ("s5", "listen", "interactive"),
                ("s7", "fillBlank", "interactive"),
                ("s9", "celebrate", "passive"),
            ]
        )
        for step in manifest["steps"]:
            pose, key, src = pose_by_step[step["id"]]
            step["scene"]["robotOverlay"] = {
                "robotState": pose,
                "pose": pose,
                "expression": pose,
                "anchor": "bottomLeft",
                "asset": {"key": key, "src": src, "sha256": "pose-sha"},
            }

        conn = _FakeConn()
        conn.config["lesson"] = {"asset_delivery_mode": "sd_pack"}
        rt = self._runtime(
            conn=conn,
            manifest=manifest,
            asset_cache=_PoseAssetCache(ready=True, local_urls=local_urls),
        )

        await rt.start()
        prepare = self._sent_frames(conn)[-1]
        self.assertEqual(prepare["type"], "lesson_prepare")
        self.assertEqual(
            {asset["key"] for asset in prepare["body"]["assetPack"]["assets"]},
            {
                "backgroundScene.poster",
                "teachingObject.barn",
                "robotOverlay.teach",
                "robotOverlay.listening",
                "robotOverlay.thinking",
                "robotOverlay.celebrate",
            },
        )
        await rt.on_lesson_ack(
            _ack(
                prepare["sequence"],
                1,
                extra={
                    "acks": prepare["sequence"],
                    "rendered": True,
                    "degraded": False,
                    "assetPack": {"ready": True, "cacheKey": "w01-d01-barn-say-it/v3-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3"},
                },
            )
        )
        await rt.on_lesson_ack(_ack(2, 2))

        inbound_seq = 3
        for step in manifest["steps"]:
            frame = self._sent_frames(conn)[-1]
            self.assertEqual(frame["type"], "lesson_step")
            self.assertEqual(frame["stepId"], step["id"])
            scene = frame["body"]["scene"]
            pose, key, source = pose_by_step[step["id"]]
            self.assertEqual(scene["robotOverlay"]["pose"], pose)
            self.assertEqual(scene["robotOverlay"]["asset"]["key"], key)
            self.assertEqual(scene["robotOverlay"]["asset"]["src"], local_urls[source])
            self.assertEqual(scene["backgroundScene"]["poster"]["src"], local_urls["barn-round-field-poster.jpg"])
            self.assertEqual(scene["teachingObject"]["asset"]["src"], local_urls["barn.png"])
            _assert_no_inline_media_payload(self, frame["body"], path=f"pose_story.{frame['stepId']}")

            await rt.on_lesson_ack(_ack(frame["sequence"], inbound_seq, step_id=step["id"]))
            if frame["body"]["completionClass"] == "interactive":
                self.assertTrue(await rt.on_child_response(f"con nói {step['id']} barn", source="voice_transcript"))
            inbound_seq += 1

        self.assertEqual([f["stepId"] for f in self._sent_frames(conn) if f["type"] == "lesson_step"], list(pose_by_step))
        self.assertEqual(self._sent_frames(conn)[-1]["type"], "lesson_stop")
        self.assertEqual(rt._steps_completed, len(pose_by_step))

    # 18b) a STRAY passive step_completed must NOT latch the next (interactive) step -

    async def test_stray_passive_step_completed_does_not_latch_interactive_step(self):
        # Latch-contamination regression. With the REAL firmware a PASSIVE step is
        # auto-advanced by the runtime on its ACK, but the firmware ALSO emits an
        # UNCONDITIONAL step_completed for that SAME passive step (lesson_handler.cc
        # :327 ack then :339 step_completed). WS in-order delivery means the passive
        # ack is processed first (passive auto-advances, the INTERACTIVE step is now
        # in flight and the latches are reset), and only THEN the stray passive
        # step_completed lands. If that stray progress sets _step_completed=True on the
        # now-current interactive step, the interactive step would finish on its ack
        # ALONE — skipping its OWN step_completed and cascading an off-by-one for the
        # rest of the run. The stepId guard on step_completed must drop the stale event.
        #
        # Tests 20/21 do NOT cover this: 20 sends only the passive ack (no stray
        # step_completed), and 21 has no preceding passive step to auto-advance.
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_steps_manifest([("s1", "greeting"), ("s4", "model")])
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))  # prepare-ack
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING + greeting (S->F 3)

        step_ids = [
            f["stepId"] for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        ]
        self.assertEqual(step_ids, ["s1"])

        # PASSIVE greeting: its ACK ALONE auto-advances to the INTERACTIVE model step
        # (S->F 4). The greeting's step_completed has NOT arrived yet (WS in-order).
        await rt.on_lesson_ack(_ack(3, 3, step_id="s1"))
        step_ids = [
            f["stepId"] for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        ]
        self.assertEqual(step_ids, ["s1", "s4"])
        self.assertEqual(rt._step_id, "s4")  # model is now the in-flight step.

        # STRAY passive step_completed for the ALREADY-ADVANCED greeting (F->S env
        # seq 4). The firmware emitted it unconditionally; it lands AFTER the model is
        # in flight and its latch has been reset. It MUST be ignored for completion —
        # its stepId ('s1') is not the current step ('s4'), so the latch stays clear.
        await rt.on_lesson_progress(
            _progress(4, {"event": "step_completed", "stepType": "greeting",
                          "result": "success"}, step_id="s1")
        )
        self.assertFalse(
            rt._step_completed,
            "stray passive step_completed must NOT latch the interactive step",
        )

        # INTERACTIVE model ACK ALONE (body.acks 4, F->S env seq 5). Pre-fix the stray
        # latch above would make this finish the model and emit lesson_stop here; with
        # the guard the model still WAITS for its OWN step_completed -> no stop yet.
        await rt.on_lesson_ack(_ack(4, 5, step_id="s4"))
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt.state, "RUNNING")

        # The model's OWN step_completed (matching stepId 's4', F->S env seq 6) is what
        # finishes it -> lesson_stop with the REAL stepsCompleted=2.
        await rt.on_lesson_progress(
            _progress(6, {"event": "step_completed", "stepType": "model",
                          "result": "success", "detail": {"recognizedText": "barn"}}, step_id="s4")
        )
        stop = [f for f in self._sent_frames(conn) if f["type"] == "lesson_stop"]
        self.assertEqual(len(stop), 1)
        self.assertEqual(rt._steps_completed, 2)

        # stop-ack (body.acks 5, F->S env seq 7) -> COMPLETED with stepsCompleted=2.
        await rt.on_lesson_ack(_ack(5, 7))
        self.assertEqual(rt.state, "COMPLETED")
        completed = [
            b for b in forwarder.batches
            if b["events"] and b["events"][0].get("type") == "lesson_completed"
        ]
        self.assertTrue(completed)
        self.assertEqual(completed[0]["events"][0]["summary"]["stepsCompleted"], 2)

    # 19) an UN-acked passive step still STEP_TIMEOUTs (render stall, not auto-pass) --

    async def test_passive_step_without_ack_still_times_out(self):
        # Auto-advance is gated on the ACK (render confirmed). If a passive step is
        # never acked, that is a genuine render stall and MUST still STEP_TIMEOUT —
        # the fix must not turn an un-rendered passive step into a silent pass.
        import asyncio

        conn = _FakeConn()
        manifest = _build_steps_manifest([("s4", "greeting")])
        manifest["steps"][0]["timeoutSec"] = 0.05
        rt = self._runtime(conn=conn, manifest=manifest)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # RUNNING + emits greeting step
        self.assertEqual(rt.state, "RUNNING")

        # Never ack the step -> its STEP_TIMEOUT fires (un-rendered, not auto-passed).
        await asyncio.sleep(0.15)
        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "STEP_TIMEOUT")
        self.assertNotIn(
            "lesson_stop", [f["type"] for f in self._sent_frames(conn)]
        )

    # ── L3 P1: explicit completionClass classifier (+ v1 fallback) ──────────────

    # 20) an AUTHOR-DEFINED type with completionClass='passive' auto-advances on ack --

    async def test_author_type_passive_class_auto_advances_on_ack(self):
        # 'songBreak' is UNKNOWN to PASSIVE_STEP_TYPES -> pre-L3 it would be
        # misclassified INTERACTIVE and hang waiting for a step_completed that never
        # comes. With completionClass='passive' it must AUTO-ADVANCE on its ack alone
        # -> lesson_stop, stepsCompleted=1, NO hang and NO STEP_TIMEOUT.
        import asyncio

        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest([("s4", "songBreak", "passive")])
        manifest["steps"][0]["timeoutSec"] = 0.05  # proves the run ignores the dwell
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))  # prepare-ack -> preload
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING + emits songBreak
        self.assertEqual(rt.state, "RUNNING")

        # FIX#7: the emitted lesson_step.body must FORWARD the author's explicit
        # completionClass (renderer-v1 additive field) so the firmware uses it as the
        # authoritative classifier instead of re-deriving from the (author-defined,
        # PASSIVE_STEP_TYPES-unknown) stepType.
        step_frame = next(
            f for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        )
        self.assertEqual(step_frame["body"]["completionClass"], "passive")
        self.assertEqual(step_frame["body"]["stepType"], "songBreak")

        # Ack ALONE finishes the passive author step -> lesson_stop (no step_completed).
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        self.assertIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt._steps_completed, 1)

        # The leftover dwell timer must NOT fail an acked passive step.
        await asyncio.sleep(0.1)
        self.assertNotEqual(rt.state, "FAILED")
        codes = [
            f.get("body", {}).get("code")
            for f in self._sent_frames(conn)
            if f["type"] == "lesson_error"
        ]
        self.assertNotIn("STEP_TIMEOUT", codes)

        await rt.on_lesson_ack(_ack(4, 4))  # stop-ack
        self.assertEqual(rt.state, "COMPLETED")

    # 21) an AUTHOR-DEFINED type with completionClass='interactive' waits for completed -

    async def test_author_type_interactive_class_waits_for_step_completed(self):
        # 'puzzle' is also UNKNOWN to PASSIVE_STEP_TYPES. With
        # completionClass='interactive' it MUST wait for BOTH the ack AND the firmware
        # step_completed — its ack alone must NOT auto-advance / stop the run.
        conn = _FakeConn()
        manifest = _build_class_steps_manifest([("s4", "puzzle", "interactive")])
        rt = self._runtime(conn=conn, manifest=manifest)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # RUNNING + emits puzzle step
        self.assertEqual(rt.state, "RUNNING")

        # Ack ALONE must NOT stop: interactive waits for step_completed.
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt.state, "RUNNING")

        # Compatible progress with response evidence is what finishes it -> lesson_stop.
        await rt.on_lesson_progress(
            _progress(4, {"event": "step_completed", "stepType": "puzzle",
                          "result": "success", "detail": {"recognizedText": "barn"}}, step_id="s4")
        )
        self.assertIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt._steps_completed, 1)

        await rt.on_lesson_ack(_ack(4, 5))  # stop-ack
        self.assertEqual(rt.state, "COMPLETED")

    async def test_interactive_step_ignores_render_only_progress_until_child_response(self):
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest([("s4", "repeat", "interactive")])
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))

        await rt.on_lesson_progress(
            _progress(4, {"event": "step_completed", "stepType": "repeat"}, step_id="s4")
        )

        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt.state, "RUNNING")
        self.assertFalse(rt._step_completed)
        completed_events = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "step_completed"
        ]
        self.assertEqual(completed_events, [])

        await rt.on_lesson_progress(
            _progress(
                5,
                {
                    "event": "step_completed",
                    "stepType": "repeat",
                    "result": "success",
                    "detail": {},
                },
                step_id="s4",
            )
        )

        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt.state, "RUNNING")
        self.assertFalse(rt._step_completed)
        completed_events = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "step_completed"
        ]
        self.assertEqual(completed_events, [])

        await rt.on_lesson_progress(
            _progress(
                6,
                {
                    "event": "step_completed",
                    "stepType": "repeat",
                    "result": "success",
                    "detail": {"recognizedText": "unknown"},
                },
                step_id="s4",
            )
        )

        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt.state, "RUNNING")
        self.assertFalse(rt._step_completed)
        completed_events = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "step_completed"
        ]
        self.assertEqual(completed_events, [])

        for sequence, detail in enumerate(
            (
                {"recognizedText": "barn", "recognized": False},
                {"recognizedText": "barn", "recognized": "false"},
                {"recognizedText": "barn", "recognized": 0},
                {"recognizedText": "barn", "recognized": 0.0},
                {"recognizedText": "barn", "accepted": False},
                {"recognizedText": "barn", "handled": "0"},
                {"recognizedText": "barn", "confidence": 0},
                {"recognizedText": "barn", "asrConfidence": 0.0},
                {"recognizedText": "barn", "confidence": ""},
                {"recognizedText": "barn", "asr_confidence": "nan"},
                {"recognizedText": "barn", "asrConfidence": "inf"},
                {"recognizedText": "barn", "asr_confidence": "-inf"},
                {"recognizedText": "barn", "confidence": "abc"},
            ),
            start=7,
        ):
            await rt.on_lesson_progress(
                _progress(
                    sequence,
                    {
                        "event": "step_completed",
                        "stepType": "repeat",
                        "result": "success",
                        "detail": detail,
                    },
                    step_id="s4",
                )
            )

            self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)], detail)
            self.assertEqual(rt.state, "RUNNING", detail)
            self.assertFalse(rt._step_completed, detail)
            completed_events = [
                event
                for batch in forwarder.batches
                for event in batch.get("events", [])
                if event.get("type") == "step_completed"
            ]
            self.assertEqual(completed_events, [], detail)

        await rt.on_lesson_progress(
            _progress(
                20,
                {
                    "event": "step_completed",
                    "stepType": "repeat",
                    "result": "success",
                    "detail": {"utterance": "barn", "recognized": "yes"},
                },
                step_id="s4",
            )
        )

        self.assertIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt._steps_completed, 1)
        completed_events = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "step_completed"
        ]
        self.assertEqual(len(completed_events), 1)
        self.assertEqual(completed_events[0]["stepId"], "s4")
        self.assertEqual(completed_events[0]["detail"], {"utterance": "barn", "recognized": "yes"})

    async def test_interactive_progress_strips_immediate_pronunciation_scoring_detail(self):
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest([("s4", "repeat", "interactive")])
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))

        await rt.on_lesson_progress(
            _progress(
                4,
                {
                    "event": "step_completed",
                    "stepType": "repeat",
                    "result": "success",
                    "detail": {
                        "recognizedText": "barn",
                        "source": "firmware_asr",
                        "score": 42,
                        "pronunciation": {"accuracy": 0.4},
                        "correction": "Try again",
                    },
                },
                step_id="s4",
            )
        )

        self.assertIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        completed_events = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "step_completed"
        ]
        self.assertEqual(len(completed_events), 1)
        detail = completed_events[0]["detail"]
        self.assertEqual(detail["recognizedText"], "barn")
        self.assertEqual(detail["source"], "firmware_asr")
        _assert_no_pronunciation_scoring_payload(self, completed_events[0])

    async def test_child_voice_response_completes_current_interactive_step(self):
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest([("s4", "repeat", "interactive")])
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))

        handled = await rt.on_child_response("ạ con nói rồi", source="voice_transcript")

        self.assertTrue(handled)
        self.assertIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt._steps_completed, 1)
        completed = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "step_completed"
        ]
        self.assertEqual(completed[-1]["stepId"], "s4")
        self.assertEqual(completed[-1]["result"], "success")
        self.assertEqual(completed[-1]["detail"]["recognizedText"], "ạ con nói rồi")
        self.assertEqual(completed[-1]["detail"]["source"], "voice_transcript")
        self.assertNotIn("score", completed[-1]["detail"])
        self.assertNotIn("pronunciation", completed[-1]["detail"])

    async def test_duplicate_child_voice_response_after_step_completion_is_ignored(self):
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest([("s4", "repeat", "interactive")])
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))

        self.assertTrue(await rt.on_child_response("con noi barn", source="voice_transcript"))
        events_after_first = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "step_completed"
        ]
        self.assertEqual(len(events_after_first), 1)
        self.assertEqual(rt._steps_completed, 1)

        handled_duplicate = await rt.on_child_response("con noi barn lan nua", source="voice_transcript")

        self.assertFalse(handled_duplicate)
        completed = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "step_completed"
        ]
        self.assertEqual(completed, events_after_first)
        self.assertEqual(rt._steps_completed, 1)
        self.assertEqual(
            [frame["type"] for frame in self._sent_frames(conn)].count("lesson_stop"),
            1,
        )

    async def test_completion_reentry_while_stop_ack_pending_does_not_emit_duplicate_stop(self):
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest([("s4", "repeat", "interactive")])
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))

        self.assertTrue(await rt.on_child_response("con noi barn", source="voice_transcript"))
        self.assertEqual(rt._steps_completed, 1)
        self.assertEqual(
            [frame["type"] for frame in self._sent_frames(conn)].count("lesson_stop"),
            1,
        )

        await rt._maybe_finish_step()

        self.assertEqual(rt._steps_completed, 1)
        self.assertEqual(
            [frame["type"] for frame in self._sent_frames(conn)].count("lesson_stop"),
            1,
        )

    async def test_late_progress_after_child_voice_completion_is_ignored(self):
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest([("s4", "repeat", "interactive")])
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))

        self.assertTrue(await rt.on_child_response("con noi barn", source="voice_transcript"))
        events_after_voice = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "step_completed"
        ]
        self.assertEqual(len(events_after_voice), 1)
        self.assertEqual(rt._steps_completed, 1)

        await rt.on_lesson_progress(
            _progress(
                4,
                {
                    "event": "step_completed",
                    "stepType": "repeat",
                    "result": "success",
                    "detail": {"recognizedText": "barn", "source": "firmware_asr"},
                },
                step_id="s4",
            )
        )

        completed = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "step_completed"
        ]
        self.assertEqual(completed, events_after_voice)
        self.assertEqual(rt._steps_completed, 1)
        self.assertEqual(
            [frame["type"] for frame in self._sent_frames(conn)].count("lesson_stop"),
            1,
        )

    async def test_stale_prior_step_progress_after_voice_advance_is_not_forwarded(self):
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest(
            [
                ("s4", "repeat", "interactive"),
                ("s5", "listen", "interactive"),
            ]
        )
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))

        self.assertTrue(await rt.on_child_response("con noi barn", source="voice_transcript"))
        self.assertEqual(rt._step_id, "s5")
        events_after_voice = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "step_completed"
        ]
        self.assertEqual([event["stepId"] for event in events_after_voice], ["s4"])
        self.assertEqual(rt._steps_completed, 1)

        await rt.on_lesson_progress(
            _progress(
                4,
                {
                    "event": "step_completed",
                    "stepType": "repeat",
                    "result": "success",
                    "detail": {"recognizedText": "barn", "source": "firmware_asr"},
                },
                step_id="s4",
            )
        )

        completed = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "step_completed"
        ]
        self.assertEqual(completed, events_after_voice)
        self.assertEqual(rt._step_id, "s5")
        self.assertEqual(rt._steps_completed, 1)
        self.assertNotIn("lesson_stop", [frame["type"] for frame in self._sent_frames(conn)])

    async def test_child_voice_response_uses_backend_outcome_enum_not_internal_sentinel(self):
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest([("s4", "listen", "interactive")])
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))

        self.assertTrue(await rt.on_child_response("con nói barn", source="voice_transcript"))

        completed = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "step_completed"
        ]
        self.assertEqual(completed[-1]["result"], "success")
        self.assertNotEqual(completed[-1]["result"], "child_response")
        self.assertEqual(completed[-1]["detail"]["recognizedText"], "con nói barn")
        self.assertNotIn("score", completed[-1]["detail"])
        self.assertNotIn("pronunciation", completed[-1]["detail"])

    async def test_child_voice_responses_use_stable_negative_sequences_per_step(self):
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest(
            [
                ("s4", "model", "interactive"),
                ("s5", "listen", "interactive"),
            ]
        )
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))

        self.assertTrue(await rt.on_child_response("con nói barn", source="voice_transcript"))
        await rt.on_lesson_ack(_ack(4, 4, step_id="s5"))
        self.assertTrue(await rt.on_child_response("con nói cow", source="voice_transcript"))

        completed = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "step_completed"
        ]
        self.assertEqual([event["stepId"] for event in completed], ["s4", "s5"])
        self.assertEqual([event["sequence"] for event in completed], [-3, -4])
        self.assertEqual(len({event["sequence"] for event in completed}), 2)

    async def test_child_voice_response_advances_to_next_prompt_not_pronunciation_scoring(self):
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        provider = _RecordingLessonVoiceProvider()
        conn.voice_provider = provider
        manifest = _build_class_steps_manifest(
            [
                ("s4", "model", "interactive"),
                ("s5", "listen", "interactive"),
                ("s8", "feedback", "passive"),
            ]
        )
        manifest["steps"][0]["prompt"] = 'This is a barn. Listen: "barn." TeeBot will say it first, then you can try.'
        manifest["steps"][1]["prompt"] = 'Your turn. You try: "barn." TeeBot is listening.'
        manifest["steps"][2]["prompt"] = 'I heard you. Nice speaking with TeeBot. That word is "barn."'
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))

        handled = await rt.on_child_response("barn", source="voice_transcript")

        self.assertTrue(handled)
        step_frames = [f for f in self._sent_frames(conn) if f["type"] == "lesson_step"]
        self.assertEqual([f["stepId"] for f in step_frames], ["s4", "s5"])
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(
            provider.prompts,
            [manifest["steps"][0]["prompt"], "Đúng rồi! barn!"],
        )
        self.assertNotRegex(provider.prompts[-1].lower(), r"\b(wrong|sai|không đúng)\b")

        completed_events = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "step_completed"
        ]
        self.assertEqual(completed_events[-1]["stepId"], "s4")
        self.assertEqual(completed_events[-1]["result"], "success")
        self.assertNotIn("score", completed_events[-1]["detail"])
        self.assertNotIn("pronunciation", completed_events[-1]["detail"])

        await rt.on_lesson_ack(_ack(4, 4, step_id="s5"))
        self.assertEqual(provider.child_response_windows, [True, True])
        await rt.on_child_response("con nói barn", source="voice_transcript")
        await rt.on_lesson_ack(_ack(5, 5, step_id="s8"))
        await rt.on_lesson_ack(_ack(6, 6))
        self.assertEqual(rt.state, "COMPLETED")
        self.assertEqual(rt._steps_completed, 3)

    async def test_child_voice_response_closes_provider_window_before_next_prompt(self):
        conn = _FakeConn()

        class _OrderedCloseProvider(_RecordingLessonVoiceProvider):
            def __init__(self):
                super().__init__()
                self.events = []

            async def speak_lesson_step_prompt(self, text, *, continue_listening=False):
                self.events.append(("prompt", text))
                return await super().speak_lesson_step_prompt(
                    text,
                    continue_listening=continue_listening,
                )

            async def open_lesson_child_response_window(self):
                self.events.append(("open", None))
                return await super().open_lesson_child_response_window()

            def close_lesson_child_response_window(self):
                self.events.append(("close", None))
                super().close_lesson_child_response_window()

        provider = _OrderedCloseProvider()
        conn.voice_provider = provider
        manifest = _build_class_steps_manifest(
            [
                ("s4", "model", "interactive"),
                ("s5", "listen", "interactive"),
            ]
        )
        manifest["steps"][0]["prompt"] = "Say barn."
        manifest["steps"][1]["prompt"] = "Now say barn again."
        rt = self._runtime(conn=conn, manifest=manifest)

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))

        self.assertTrue(await rt.on_child_response("barn", source="internal_dev_endpoint"))

        self.assertEqual(provider.closed_child_response_windows, 1)
        self.assertEqual(provider.prompts, ["Say barn.", "Đúng rồi! barn!"])
        self.assertNotRegex(provider.prompts[-1].lower(), r"\b(wrong|sai|không đúng)\b")
        await rt.on_lesson_ack(_ack(4, 4, step_id="s5"))
        self.assertEqual(provider.prompts, ["Say barn.", "Đúng rồi! barn!", "Now say barn again."])
        self.assertLess(
            provider.events.index(("close", None)),
            provider.events.index(("prompt", "Now say barn again.")),
        )

    async def test_child_voice_response_is_ignored_when_empty_inactive_or_passive(self):
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        rt = self._runtime(
            conn=conn,
            manifest=_build_class_steps_manifest([("s1", "greeting", "passive")]),
            forwarder=forwarder,
        )

        self.assertFalse(await rt.on_child_response("barn", source="voice_transcript"))

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        self.assertFalse(await rt.on_child_response("   ", source="voice_transcript"))
        self.assertFalse(await rt.on_child_response("barn", source="voice_transcript"))
        self.assertEqual(
            [event for batch in forwarder.batches for event in batch.get("events", []) if event.get("type") == "step_completed"],
            [],
        )
        self.assertEqual(rt.state, "RUNNING")

    async def test_child_voice_response_ignores_placeholder_or_noise_transcripts(self):
        for transcript in ("unknown", "unrecognized", "[noise]", "[inaudible]", "silence", "no_speech", "...", "???"):
            conn = _FakeConn()
            forwarder = _FakeForwarder()
            manifest = _build_class_steps_manifest([("s4", "repeat", "interactive")])
            rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
            await rt.start()
            await rt.on_lesson_ack(_ack(1, 1))
            await rt._preload_task
            await rt.on_lesson_ack(_ack(2, 2))
            await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))

            handled = await rt.on_child_response(transcript, source="voice_transcript")

            self.assertFalse(handled, transcript)
            self.assertFalse(rt._step_completed, transcript)
            self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)], transcript)
            self.assertEqual(rt.state, "RUNNING", transcript)
            self.assertEqual(
                [
                    event
                    for batch in forwarder.batches
                    for event in batch.get("events", [])
                    if event.get("type") == "step_completed"
                ],
                [],
                transcript,
            )

    async def test_interactive_step_ack_opens_child_response_window(self):
        conn = _FakeConn()
        provider = _RecordingLessonVoiceProvider()
        conn.voice_provider = provider
        manifest = _build_class_steps_manifest([("s4", "repeat", "interactive")])
        manifest["steps"][0]["prompt"] = "Say barn."
        rt = self._runtime(conn=conn, manifest=manifest)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))

        self.assertEqual(provider.child_response_windows, [True])
        self.assertEqual(provider.prompt_continue_listening, [True])

    async def test_wrong_child_response_retry_prompt_keeps_listening_after_tts(self):
        conn = _FakeConn()
        provider = _RecordingLessonVoiceProvider()
        conn.voice_provider = provider
        manifest = _build_class_steps_manifest([("s4", "repeat", "interactive")])
        manifest["steps"][0]["prompt"] = "Say barn."
        manifest["steps"][0]["expectedResponses"] = ["barn"]
        rt = self._runtime(conn=conn, manifest=manifest)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))

        handled = await rt.on_child_response("cat")

        self.assertTrue(handled)
        self.assertEqual(provider.child_response_windows, [True, True])
        self.assertEqual(provider.prompt_continue_listening[-1], True)

    async def test_storybeat_wait_for_child_guided_turn_waits_for_child_response(self):
        conn = _FakeConn()
        provider = _RecordingLessonVoiceProvider()
        conn.voice_provider = provider
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest([("s1", "greeting", "interactive")])
        manifest["steps"][0]["prompt"] = "What animal do you see?"
        manifest["steps"][0]["storyBeat"] = {
            "ask": "What animal do you see?",
            "waitForChild": True,
        }
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        step_frame = next(f for f in self._sent_frames(conn) if f["type"] == "lesson_step")
        self.assertEqual(step_frame["body"]["completionClass"], "interactive")
        self.assertEqual(step_frame["body"]["storyBeat"], {"ask": "What animal do you see?", "waitForChild": True})
        await rt.on_lesson_ack(_ack(3, 3, step_id="s1"))
        self.assertEqual(provider.child_response_windows, [True])

        await rt.on_lesson_progress(
            _progress(4, {"event": "step_completed", "stepType": "greeting", "result": "success"}, step_id="s1")
        )
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt.state, "RUNNING")

    async def test_storybeat_ask_drives_spoken_prompt_without_rewriting_step_prompt(self):
        conn = _FakeConn()
        provider = _RecordingLessonVoiceProvider()
        conn.voice_provider = provider
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest([("s1", "greeting", "interactive")])
        manifest["steps"][0]["prompt"] = "Look at the screen."
        manifest["steps"][0]["storyBeat"] = {
            "ask": "What animal do you see?",
            "waitForChild": True,
        }
        manifest["steps"][0]["vocab"] = {"word": "barn", "promptKind": "guided-speaking"}
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        step_frame = next(f for f in self._sent_frames(conn) if f["type"] == "lesson_step")
        self.assertEqual(step_frame["body"]["prompt"], "Look at the screen.")
        self.assertEqual(step_frame["body"]["storyBeat"]["ask"], "What animal do you see?")
        self.assertEqual(conn.voice_provider.prompts, [])

        await rt.on_lesson_ack(_ack(3, 3, step_id="s1"))

        self.assertEqual(conn.voice_provider.prompts, ["What animal do you see?"])
        self.assertEqual(provider.child_response_windows, [True])
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        forwarded_events = [event for batch in forwarder.batches for event in batch["events"]]
        self.assertNotIn("step_completed", [event.get("type") for event in forwarded_events])
        _assert_no_pronunciation_scoring_payload(self, forwarded_events)

    async def test_guided_turn_with_blank_storybeat_ask_uses_safe_question_not_visual_prompt(self):
        conn = _FakeConn()
        provider = _RecordingLessonVoiceProvider()
        conn.voice_provider = provider
        manifest = _build_class_steps_manifest([("s1", "model", "interactive")])
        manifest["steps"][0]["prompt"] = "Look at the screen."
        manifest["steps"][0]["storyBeat"] = {"ask": "   ", "waitForChild": True}
        manifest["steps"][0]["vocab"] = {"word": "barn", "promptKind": "guided-speaking"}
        rt = self._runtime(conn=conn, manifest=manifest)

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        step_frame = next(f for f in self._sent_frames(conn) if f["type"] == "lesson_step")
        self.assertEqual(step_frame["body"]["prompt"], "Look at the screen.")
        self.assertEqual(step_frame["body"]["storyBeat"], {"ask": "   ", "waitForChild": True})

        await rt.on_lesson_ack(_ack(step_frame["sequence"], 3, step_id="s1"))

        self.assertEqual(provider.prompts, ["What do you see?"])
        self.assertEqual(provider.child_response_windows, [True])
        self.assertNotIn("Look at the screen.", provider.prompts)

    async def test_interactive_storybeat_ask_drives_spoken_prompt_without_wait_flag(self):
        conn = _FakeConn()
        provider = _RecordingLessonVoiceProvider()
        conn.voice_provider = provider
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest([("s1", "model", "interactive")])
        manifest["steps"][0]["prompt"] = "Look at the screen."
        manifest["steps"][0]["storyBeat"] = {"ask": "Which animal is beside the barn?"}
        manifest["steps"][0]["vocab"] = {"word": "barn", "promptKind": "guided-speaking"}
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        step_frame = next(f for f in self._sent_frames(conn) if f["type"] == "lesson_step")
        self.assertEqual(step_frame["body"]["completionClass"], "interactive")
        self.assertEqual(step_frame["body"]["prompt"], "Look at the screen.")
        self.assertEqual(step_frame["body"]["storyBeat"], {"ask": "Which animal is beside the barn?"})
        self.assertEqual(conn.voice_provider.prompts, [])

        await rt.on_lesson_ack(_ack(step_frame["sequence"], 3, step_id="s1"))
        self.assertEqual(conn.voice_provider.prompts, ["Which animal is beside the barn?"])
        self.assertEqual(provider.child_response_windows, [True])
        self.assertTrue(await rt.on_child_response("con thấy barn", source="voice_transcript"))
        self.assertIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        completed = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "step_completed"
        ]
        self.assertEqual(completed[-1]["detail"]["recognizedText"], "con thấy barn")
        self.assertNotIn("score", completed[-1]["detail"])
        self.assertNotIn("pronunciation", completed[-1]["detail"])

    async def test_child_response_window_failure_does_not_crash_lesson_step(self):
        conn = _FakeConn()
        provider = _FailingChildResponseWindowProvider()
        conn.voice_provider = provider
        sleeper = _GatedSleep()
        conn.config["lesson"] = {"child_response_timeout_sec": 3, "max_no_answer_attempts": 1}
        manifest = _build_class_steps_manifest([("s4", "repeat", "interactive")])
        rt = self._runtime(conn=conn, manifest=manifest, sleep=sleeper)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        await asyncio.sleep(0)

        self.assertEqual(provider.calls, 1)
        self.assertEqual(rt.state, "RUNNING")
        self.assertEqual(rt._step_id, "s4")
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual([entry[0] for entry in sleeper.calls], [3])

        sleeper.release_latest()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(rt.state, "PAUSED")
        abandoned = [
            event
            for batch in rt.forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "lesson_abandoned"
        ]
        self.assertEqual(abandoned[-1]["reason"], "child_inactive")

    async def test_child_response_reprompt_reopen_failure_keeps_liveness_until_pause(self):
        conn = _FakeConn()
        provider = _SequenceChildResponseWindowProvider(
            [True, RuntimeError("listener unavailable")]
        )
        conn.voice_provider = provider
        conn.voice_provider.prompts = []
        sleeper = _GatedSleep()
        conn.config["lesson"] = {"child_response_timeout_sec": 3, "max_no_answer_attempts": 2}
        manifest = _build_class_steps_manifest([("s4", "repeat", "interactive")])
        rt = self._runtime(conn=conn, manifest=manifest, sleep=sleeper)

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        await asyncio.sleep(0)

        self.assertEqual(provider.calls, 1)
        self.assertEqual([entry[0] for entry in sleeper.calls], [3])

        sleeper.release_latest()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(provider.calls, 2)
        self.assertEqual(rt.state, "RUNNING")
        self.assertEqual([entry[0] for entry in sleeper.calls], [3])

        sleeper.release_latest()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(rt.state, "PAUSED")
        abandoned = [
            event
            for batch in rt.forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "lesson_abandoned"
        ]
        self.assertEqual(abandoned[-1]["reason"], "child_inactive")

    async def test_child_response_window_failure_does_not_accept_voice_response(self):
        conn = _FakeConn()
        provider = _FailingChildResponseWindowProvider()
        conn.voice_provider = provider
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest([("s4", "repeat", "interactive")])
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))

        handled = await rt.on_child_response("con noi barn", source="voice_transcript")

        self.assertFalse(handled)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(rt.state, "RUNNING")
        self.assertFalse(rt._step_completed)
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(
            [
                event
                for batch in forwarder.batches
                for event in batch.get("events", [])
                if event.get("type") == "step_completed"
            ],
            [],
        )

    async def test_prepare_ack_timeout_fails_and_releases_lesson_mode(self):
        sleeper = _GatedSleep()
        conn = _FakeConn()
        conn.config["lesson"] = {"frame_ack_timeout_sec": 4, "frame_ack_max_retries": 0}
        released = []

        async def release_lesson_mode(*, reason):
            released.append(reason)

        conn.release_lesson_mode = release_lesson_mode
        forwarder = _FakeForwarder()
        rt = self._runtime(conn=conn, forwarder=forwarder, sleep=sleeper)

        await rt.start()
        await asyncio.sleep(0)
        sleeper.release_latest()
        await asyncio.sleep(0)

        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "LESSON_FRAME_ACK_TIMEOUT")
        self.assertEqual(released, ["frame_ack_timeout"])
        self.assertIn("lesson_error", [f["type"] for f in self._sent_frames(conn)])
        self.assertTrue(
            any(
                batch["events"] and batch["events"][0].get("type") == "lesson_failed"
                for batch in forwarder.batches
            )
        )

    async def test_prepare_ack_timeout_retries_before_failing_lesson(self):
        sleeper = _GatedSleep()
        conn = _FakeConn()
        conn.config["lesson"] = {"frame_ack_timeout_sec": 4, "frame_ack_max_retries": 1}
        released = []

        async def release_lesson_mode(*, reason):
            released.append(reason)

        conn.release_lesson_mode = release_lesson_mode
        rt = self._runtime(conn=conn, sleep=sleeper)

        await rt.start()
        await asyncio.sleep(0)
        sleeper.release_latest()
        await asyncio.sleep(0)

        sent = self._sent_frames(conn)
        self.assertEqual([f["type"] for f in sent], ["lesson_prepare", "lesson_prepare"])
        self.assertEqual([f["sequence"] for f in sent], [1, 2])
        self.assertEqual(rt.state, "PRELOADING")
        self.assertEqual(released, [])

        await rt.on_lesson_ack(_ack(1, 1))
        self.assertIsNone(rt._preload_task)

        await rt.on_lesson_ack(_ack(2, 1))
        await rt._preload_task

        sent = self._sent_frames(conn)
        self.assertEqual([f["type"] for f in sent], ["lesson_prepare", "lesson_prepare", "lesson_start"])
        self.assertEqual(rt.state, "READY")

    async def test_child_inactivity_reprompts_without_step_timeout_or_scoring(self):
        sleeper = _GatedSleep()
        conn = _FakeConn()
        provider = _RecordingLessonVoiceProvider()
        conn.voice_provider = provider
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest([("s4", "repeat", "interactive")])
        manifest["steps"][0]["prompt"] = "Con nói theo robot nhé: barn."
        manifest["steps"][0]["responseTimeoutSec"] = 4
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder, sleep=sleeper)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await asyncio.sleep(0)
        self.assertEqual(len(sleeper.calls), 1)  # render-ack timeout for s4

        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        await asyncio.sleep(0)
        self.assertEqual(provider.child_response_windows, [True])
        self.assertEqual(provider.prompts, ["Con nói theo robot nhé: barn."])

        sleeper.release_latest()
        await asyncio.sleep(0)

        self.assertEqual(rt.state, "RUNNING")
        self.assertIsNone(rt.last_error)
        self.assertEqual(provider.child_response_windows, [True, True])
        self.assertEqual(
            provider.prompts[-1],
            "Không sao, con từ từ nhé. Thử nói lại khi con sẵn sàng.",
        )
        self.assertNotRegex(provider.prompts[-1].lower(), r"\b(wrong|sai|không đúng)\b")
        self.assertNotIn("lesson_error", [f["type"] for f in self._sent_frames(conn)])
        event_types = [event.get("type") for batch in forwarder.batches for event in batch.get("events", [])]
        self.assertNotIn("lesson_failed", event_types)
        self.assertNotIn("step_completed", event_types)

    async def test_repeated_child_inactivity_abandons_without_lesson_failure(self):
        sleeper = _GatedSleep()
        conn = _FakeConn()
        provider = _RecordingLessonVoiceProvider()
        conn.voice_provider = provider
        released = []

        async def release_lesson_mode(*, reason):
            released.append(reason)

        conn.release_lesson_mode = release_lesson_mode
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest([("s4", "listen", "interactive")])
        manifest["steps"][0]["responseTimeoutSec"] = 4
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder, sleep=sleeper)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await asyncio.sleep(0)
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        await asyncio.sleep(0)

        sleeper.release_latest()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        sleeper.release_latest()
        await asyncio.sleep(0)

        self.assertEqual(rt.state, "PAUSED")
        self.assertIsNone(rt.last_error)
        self.assertEqual(released, ["child_inactive"])
        self.assertNotIn("lesson_error", [f["type"] for f in self._sent_frames(conn)])
        event_types = [event.get("type") for batch in forwarder.batches for event in batch.get("events", [])]
        self.assertIn("lesson_abandoned", event_types)
        self.assertNotIn("lesson_failed", event_types)
        abandoned = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "lesson_abandoned"
        ][-1]
        self.assertEqual(abandoned["reason"], "child_inactive")
        self.assertEqual(abandoned["stepId"], "s4")

    async def test_empty_renderable_lesson_fails_without_emitting_lesson_step(self):
        conn = _FakeConn()
        manifest = _build_manifest()
        manifest["steps"] = [{"id": "metadata-only", "prompt": "not renderable"}]
        rt = self._runtime(conn=conn, manifest=manifest)

        with self.assertRaises(LessonError) as ctx:
            await rt.start()

        self.assertEqual(ctx.exception.code, "LESSON_STEP_MISSING")
        self.assertEqual(conn.websocket.sent, [])

    # 22) NO completionClass -> v1 PASSIVE_STEP_TYPES fallback (unchanged behavior) ---

    async def test_no_completion_class_falls_back_to_passive_step_types(self):
        # A step with NO completionClass classifies via PASSIVE_STEP_TYPES, exactly
        # like the pre-L3 builtins. 'greeting' (passive, in the set) auto-advances on
        # ack; 'model' (interactive, not in the set) waits for step_completed. This
        # pins that the fallback path is byte-for-behavior identical to v1.
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest(
            [("s1", "greeting", None), ("s4", "model", None)]
        )
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # RUNNING + greeting (S->F 3)

        # FIX#7: a manifest step WITHOUT completionClass must OMIT the field from the
        # wire body (not emit completionClass=null), keeping the body byte-identical to
        # the frozen v1 fixtures whose firmware then falls back to PASSIVE_STEP_TYPES.
        greeting_frame = next(
            f for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        )
        self.assertNotIn("completionClass", greeting_frame["body"])

        # PASSIVE greeting (fallback, no completionClass): ack ALONE auto-advances.
        await rt.on_lesson_ack(_ack(3, 3, step_id="s1"))
        step_ids = [
            f["stepId"] for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        ]
        self.assertEqual(step_ids, ["s1", "s4"])
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])

        # INTERACTIVE model (fallback): ack alone must NOT stop; waits for completed.
        await rt.on_lesson_ack(_ack(4, 4, step_id="s4"))
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        await rt.on_lesson_progress(
            _progress(5, {"event": "step_completed", "stepType": "model",
                          "result": "success", "detail": {"recognizedText": "barn"}}, step_id="s4")
        )
        self.assertIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt._steps_completed, 2)

        await rt.on_lesson_ack(_ack(5, 6))  # stop-ack
        self.assertEqual(rt.state, "COMPLETED")

    # ── L3 P3 renderer-capability negotiation ────────────────────────────────────

    async def test_v1_device_default_capability_set(self):
        # A v1 device's advertised renderer ('teebot-lesson-renderer.v1') yields the
        # v1-only capability set; a device that OMITS renderer defaults to the same.
        from core.lesson.errors import device_renderer_capabilities

        rt = self._runtime(conn=_FakeConn())  # default features = v1 renderer string
        self.assertEqual(rt.renderer_capabilities, ["teebot-lesson-renderer.v1"])
        # Default fallback when the field is absent (current firmware that omits it).
        self.assertEqual(
            device_renderer_capabilities({"lesson": True}),
            ["teebot-lesson-renderer.v1"],
        )
        self.assertEqual(
            device_renderer_capabilities(None), ["teebot-lesson-renderer.v1"]
        )

    async def test_negotiated_version_stamps_v1_for_v1_manifest_unchanged(self):
        # A served v1 manifest to a v1 device negotiates v1, so EVERY outbound
        # envelope stamps protocolVersion 'teebot-lesson-renderer.v1' — byte-identical
        # to the hardcoded-constant behaviour (the happy-thread fixture test still
        # passes; this pins the stamp value explicitly post-negotiation).
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        rt = self._runtime(conn=conn)
        await rt.start()
        self.assertEqual(rt.negotiated_version, "teebot-lesson-renderer.v1")
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING + lesson_step
        versions = {
            f["protocolVersion"] for f in self._sent_frames(conn)
        }
        self.assertEqual(versions, {"teebot-lesson-renderer.v1"})

    async def test_served_version_outside_device_capability_set_rejected(self):
        # SIMULATED future-renderer manifest: the backend served a v2 manifestVersion
        # to a v1-only device. The start() gate rejects it (LESSON_VERSION_UNSUPPORTED),
        # NO crash, NO frame on the wire — the structural guard that a v1-only device
        # is never handed a v2 manifest it cannot render.
        conn = _FakeConn()  # v1-only capability set
        manifest = _build_manifest()
        manifest["manifestVersion"] = "teebot-lesson-renderer.v2"
        rt = self._runtime(conn=conn, manifest=manifest)
        with self.assertRaises(LessonError) as ctx:
            await rt.start()
        self.assertEqual(ctx.exception.code, "LESSON_VERSION_UNSUPPORTED")
        self.assertEqual(conn.websocket.sent, [])  # nothing emitted

    async def test_device_advertising_v2_negotiates_and_stamps_v2(self):
        # Forward-looking: a device advertising BOTH v1+v2 (list-form renderer)
        # served a v2 manifest negotiates v2 and stamps it. This proves the
        # negotiation is real (not hardcoded v1) while today's devices stay v1.
        conn = _FakeConn(
            features={
                "lesson": True,
                "renderer": ["teebot-lesson-renderer.v1", "teebot-lesson-renderer.v2"],
            }
        )
        conn.device_id = "robot-01"
        conn.config = {
            "lesson": {
                "renderer_v2_enabled": True,
                "rollout_device_allowlist": ["robot-01"],
            }
        }
        manifest = _build_manifest()
        manifest["manifestVersion"] = "teebot-lesson-renderer.v2"
        manifest["openingEntrance"] = {
            "template": "tvideoFlyWalk",
            "preset": "flyLandWalkGreet",
            "policy": "oncePerLessonSession",
            "layoutPreset": "centerRoad",
            "phases": [
                "hidden",
                "flyIn",
                "landFar",
                "settle",
                "walkToward",
                "arriveNear",
                "greetIdle",
                "revealTeachingContent",
            ],
            "backgroundAssetKey": "scene.farm",
            "robotAssetKey": "robotOverlay.teach",
            "fallback": "staticGreet",
        }
        rt = self._runtime(conn=conn, manifest=manifest)
        await rt.start()
        self.assertEqual(rt.negotiated_version, "teebot-lesson-renderer.v2")
        prepare = self._sent_frames(conn)[0]
        self.assertEqual(prepare["type"], "lesson_prepare")
        self.assertEqual(prepare["protocolVersion"], "teebot-lesson-renderer.v2")
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        start = self._sent_frames(conn)[-1]
        self.assertEqual(start["type"], "lesson_start")
        self.assertEqual(
            start["body"]["openingEntrance"],
            {
                "preset": "flyLandWalkGreet",
                "policy": "oncePerLessonSession",
                "layoutPreset": "centerRoad",
                "backgroundAssetKey": "scene.farm",
                "robotAssetKey": "robotOverlay.teach",
                "fallback": "staticGreet",
            },
        )
        self.assertIn("template", manifest["openingEntrance"])
        self.assertIn("phases", manifest["openingEntrance"])
        self.assertEqual(
            start["body"]["runtimeControls"],
            {
                "openingEntranceEnabled": True,
                "visualStateEventsEnabled": True,
                "physicalMotionOwner": "server",
            },
        )

    async def test_v2_only_string_capability_starts_when_rollout_gate_matches(self):
        from core.lesson.errors import lesson_capability_ok

        conn = _FakeConn(
            features={"lesson": True, "renderer": "teebot-lesson-renderer.v2"}
        )
        conn.device_id = "robot-01"
        conn.config = {
            "lesson": {
                "renderer_v2_enabled": True,
                "rollout_device_allowlist": ["robot-01"],
            }
        }
        manifest = _build_manifest()
        manifest["manifestVersion"] = "teebot-lesson-renderer.v2"

        self.assertTrue(lesson_capability_ok(conn.features, renderer_v2_enabled=True))
        rt = self._runtime(conn=conn, manifest=manifest)
        await rt.start()
        self.assertEqual(self._sent_frames(conn)[0]["protocolVersion"], "teebot-lesson-renderer.v2")

    async def test_v2_only_list_capability_starts_when_rollout_gate_matches(self):
        from core.lesson.errors import lesson_capability_ok

        conn = _FakeConn(
            features={"lesson": True, "renderer": ["teebot-lesson-renderer.v2"]}
        )
        conn.device_id = "robot-01"
        conn.config = {
            "lesson": {
                "renderer_v2_enabled": True,
                "rollout_device_allowlist": ["robot-01"],
            }
        }
        manifest = _build_manifest()
        manifest["manifestVersion"] = "teebot-lesson-renderer.v2"

        self.assertTrue(lesson_capability_ok(conn.features, renderer_v2_enabled=True))
        rt = self._runtime(conn=conn, manifest=manifest)
        await rt.start()
        self.assertEqual(self._sent_frames(conn)[0]["protocolVersion"], "teebot-lesson-renderer.v2")

    async def test_v2_manifest_is_rejected_when_server_rollout_gate_is_off(self):
        conn = _FakeConn(
            features={
                "lesson": True,
                "renderer": ["teebot-lesson-renderer.v1", "teebot-lesson-renderer.v2"],
            }
        )
        manifest = _build_manifest()
        manifest["manifestVersion"] = "teebot-lesson-renderer.v2"
        rt = self._runtime(conn=conn, manifest=manifest)
        with self.assertRaises(LessonError):
            await rt.start()
        self.assertEqual(conn.websocket.sent, [])


# ── L3 P3 manifest-fetch capability forwarding ───────────────────────────────────


class _CapRecordingResponse:
    """Minimal httpx-Response stand-in for get_lesson_manifest."""

    def __init__(self, manifest, etag):
        self._manifest = manifest
        self.headers = {"ETag": etag}
        # get_lesson_manifest now routes through _lesson_request_with_retry, which
        # inspects status_code/content for the 204/empty short-circuit. A real
        # httpx.Response carries both, so the stand-in must too.
        self.status_code = 200
        self.content = b'{"data": {"manifest": {}}}'

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": {"manifest": self._manifest}}

    async def aclose(self):
        return None


class _CapRecordingClient:
    """Records the params + headers get_lesson_manifest puts on the wire."""

    def __init__(self, manifest, etag='"lesson-3-espTft-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3"'):
        self._manifest = manifest
        self._etag = etag
        self.calls = []

    async def request(self, method, url, params=None, headers=None):
        self.calls.append(
            {"method": method, "url": url, "params": params or {}, "headers": headers or {}}
        )
        return _CapRecordingResponse(self._manifest, self._etag)


class LessonManifestCapabilityFetchTest(unittest.IsolatedAsyncioTestCase):
    """Exercises the REAL config.manage_api_client.get_lesson_manifest (a fresh
    copy loaded from disk) to pin how the device capability set is forwarded."""

    def setUp(self):
        self.mac = _load_real_manage_api_client()
        self.manifest = _build_manifest()

    async def test_capabilities_forwarded_as_query_param_and_header(self):
        client = _CapRecordingClient(self.manifest)
        caps = ["teebot-lesson-renderer.v1"]
        manifest, etag = await self.mac.get_lesson_manifest(
            client,
            "http://backend.test/v1",
            "w01-d01-barn-say-it",
            "espTft",
            renderer_capabilities=caps,
        )
        self.assertEqual(manifest, self.manifest)
        self.assertTrue(etag)
        call = client.calls[0]
        # Default v1 capability set forwarded BOTH ways (query param + header).
        self.assertEqual(call["method"], "GET")
        self.assertEqual(
            call["url"],
            "http://backend.test/v1/lessons/w01-d01-barn-say-it/manifest",
        )
        self.assertEqual(call["params"].get("profile"), "espTft")
        self.assertEqual(
            call["params"].get("rendererCapabilities"), "teebot-lesson-renderer.v1"
        )
        self.assertEqual(
            call["headers"].get("X-Renderer-Capabilities"), "teebot-lesson-renderer.v1"
        )

    async def test_multiple_capabilities_comma_joined(self):
        client = _CapRecordingClient(self.manifest)
        caps = ["teebot-lesson-renderer.v1", "teebot-lesson-renderer.v2"]
        await self.mac.get_lesson_manifest(
            client,
            "http://backend.test/v1",
            "L",
            "espTft",
            renderer_capabilities=caps,
            renderer_v2_enabled=True,
        )
        call = client.calls[0]
        joined = "teebot-lesson-renderer.v1,teebot-lesson-renderer.v2"
        self.assertEqual(call["params"].get("rendererCapabilities"), joined)
        self.assertEqual(call["headers"].get("X-Renderer-Capabilities"), joined)

    async def test_v2_capability_is_filtered_when_rollout_gate_is_off(self):
        client = _CapRecordingClient(self.manifest)
        await self.mac.get_lesson_manifest(
            client,
            "http://backend.test/v1",
            "L",
            "espTft",
            renderer_capabilities=[
                "teebot-lesson-renderer.v1",
                "teebot-lesson-renderer.v2",
            ],
        )
        call = client.calls[0]
        self.assertEqual(call["params"]["rendererCapabilities"], "teebot-lesson-renderer.v1")
        self.assertEqual(call["headers"]["X-Renderer-Capabilities"], "teebot-lesson-renderer.v1")

    async def test_assignment_lesson_version_is_forwarded_as_manifest_version_query_param(self):
        client = _CapRecordingClient(self.manifest)
        await self.mac.get_lesson_manifest(
            client,
            "http://backend.test/v1",
            "w01-d01-barn-say-it-age3-20260617",
            "espTft",
            lesson_version=3,
        )
        call = client.calls[0]
        self.assertEqual(call["params"].get("profile"), "espTft")
        self.assertEqual(call["params"].get("version"), "3")

    async def test_backward_compat_no_capabilities_omits_param_and_header(self):
        # Older call sites / tests that omit renderer_capabilities -> request is
        # byte-identical to today's v1 call: NO rendererCapabilities param, NO header.
        client = _CapRecordingClient(self.manifest)
        await self.mac.get_lesson_manifest(
            client, "http://backend.test/v1", "L", "espTft"
        )
        call = client.calls[0]
        self.assertEqual(call["params"], {"profile": "espTft"})
        self.assertNotIn("X-Renderer-Capabilities", call["headers"])


# ── manifest leg shares the transient-retry path (MED: bare request had none) ────


class _RetryingManifestResponse:
    """httpx-Response stand-in that raises_for_status -> HTTPStatusError once."""

    def __init__(self, manifest, etag, status_code):
        import httpx

        self._manifest = manifest
        self.headers = {"ETag": etag}
        self.status_code = status_code
        self.content = b'{"data": {"manifest": {}}}'
        self._httpx = httpx

    def raise_for_status(self):
        if self.status_code >= 400:
            raise self._httpx.HTTPStatusError(
                "server error",
                request=self._httpx.Request("GET", "http://backend.test/v1/x"),
                response=self._httpx.Response(self.status_code),
            )
        return None

    def json(self):
        return {"data": {"manifest": self._manifest}}

    async def aclose(self):
        return None


class _RetryingManifestClient:
    """Yields a 500 on the first manifest request, then a 200 — proving the leg
    now retries transient failures instead of aborting the whole pull."""

    def __init__(self, manifest, etag='"lesson-3-espTft-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3"'):
        self._manifest = manifest
        self._etag = etag
        self.attempts = 0

    async def request(self, method, url, params=None, headers=None):
        self.attempts += 1
        status = 500 if self.attempts == 1 else 200
        return _RetryingManifestResponse(self._manifest, self._etag, status)


class LessonManifestRetryTest(unittest.IsolatedAsyncioTestCase):
    """MED fix: get_lesson_manifest routes through _lesson_request_with_retry, so a
    single transient 5xx no longer aborts the pull; falsy lesson_id is early-guarded."""

    def setUp(self):
        self.mac = _load_real_manage_api_client()
        self.manifest = _build_manifest()

    async def test_transient_5xx_is_retried_then_succeeds(self):
        import asyncio as _asyncio

        client = _RetryingManifestClient(self.manifest)
        # Patch the real module's asyncio.sleep so the retry backoff is instant.
        orig_sleep = _asyncio.sleep

        async def _no_sleep(_delay):
            return None

        _asyncio.sleep = _no_sleep
        try:
            manifest, etag = await self.mac.get_lesson_manifest(
                client, "http://backend.test/v1", "L", "espTft"
            )
        finally:
            _asyncio.sleep = orig_sleep
        self.assertEqual(client.attempts, 2)  # one 500, one 200
        self.assertEqual(manifest, self.manifest)
        self.assertTrue(etag)

    async def test_falsy_lesson_id_short_circuits_without_request(self):
        class _ExplodingClient:
            async def request(self, *a, **k):
                raise AssertionError("must not hit the wire on falsy lesson_id")

        for bad in ("", None):
            manifest, etag = await self.mac.get_lesson_manifest(
                _ExplodingClient(), "http://backend.test/v1", bad, "espTft"
            )
            self.assertIsNone(manifest)
            self.assertIsNone(etag)


# ── L3 P3 pull-on-connect threads the device capability set to the fetch ──────────


class LessonPullOnConnectCapabilityTest(unittest.IsolatedAsyncioTestCase):
    def _patch_backend(self, assignment, manifest, etag='"lesson-3-espTft-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3"'):
        import config.manage_api_client as mac
        import config.device_token_client as dtc

        self.manifest_calls = []

        async def _get_assignment(client, base_url, device_id, *, token=None):
            return assignment

        async def _get_manifest(
            client,
            base_url,
            lesson_id,
            profile,
            *,
            token=None,
            renderer_capabilities=None,
            renderer_v2_enabled=False,
            lesson_version=None,
        ):
            self.manifest_calls.append(
                {
                    "lesson_id": lesson_id,
                    "profile": profile,
                    "renderer_capabilities": renderer_capabilities,
                    "lesson_version": lesson_version,
                }
            )
            return manifest, etag

        async def _resolve_device_identity(client, base_url, device_id, *, logger=None):
            return device_id, "device-token"

        saved = (mac.get_current_assignment, mac.get_lesson_manifest, dtc.resolve_device_identity)
        mac.get_current_assignment = _get_assignment
        mac.get_lesson_manifest = _get_manifest
        dtc.resolve_device_identity = _resolve_device_identity

        def _undo():
            mac.get_current_assignment, mac.get_lesson_manifest, dtc.resolve_device_identity = saved

        return _undo

    async def test_v1_device_forwards_default_capability_set_to_fetch(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()  # default features = v1 renderer
        prep = FIX["frames"]["lesson_prepare"]
        assignment = {
            "assignmentId": prep["assignmentId"],
            "assignmentVersion": prep["body"]["assignmentVersion"],
            "lessonId": prep["lessonId"],
            "lessonVersion": prep["lessonVersion"],
            "manifestChecksum": _manifest_checksum(),
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        undo = self._patch_backend(assignment, _build_manifest())
        try:
            rt = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNotNone(rt)
        # The device's v1-only capability set was threaded to the manifest fetch.
        self.assertEqual(
            [call["renderer_capabilities"] for call in self.manifest_calls],
            [["teebot-lesson-renderer.v1"]],
        )
        # And the negotiated stamp on the wire is v1 (unchanged behaviour today).
        prepare = json.loads(conn.websocket.sent[0])
        self.assertEqual(prepare["protocolVersion"], "teebot-lesson-renderer.v1")

    async def test_v4_rollout_keeps_v2_and_v1_fallbacks_for_assigned_v2_manifest(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        conn.features = {
            "lesson": True,
            "renderer": [
                "teebot-lesson-renderer.v1",
                "teebot-lesson-renderer.v2",
                "teebot-lesson-renderer.v3",
                "teebot-lesson-renderer.v4",
            ],
            "lessonRendererV3": {"directMp4Cinematic": True, "sdAssetPack": True},
            "lessonRendererV4": {"flattenedMjpegCinematic": True, "sdAssetPack": True},
        }
        conn.config["lesson"].update({
            "renderer_v2_enabled": True,
            "renderer_v4_enabled": True,
        })
        assignment = {
            "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
            "assignmentVersion": 1,
            "lessonId": FIX["frames"]["lesson_prepare"]["lessonId"],
            "lessonVersion": 3,
            "manifestChecksum": _manifest_checksum(),
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        manifest = _build_manifest()
        manifest["manifestVersion"] = "teebot-lesson-renderer.v2"
        manifest["openingEntrance"] = {
            "template": "tvideoFlyWalk",
            "preset": "flyLandWalkGreet",
            "policy": "oncePerLessonSession",
            "layoutPreset": "centerRoad",
            "phases": [
                "hidden", "flyIn", "landFar", "settle", "walkToward",
                "arriveNear", "greetIdle", "revealTeachingContent",
            ],
            "backgroundAssetKey": "scene.farm",
            "robotAssetKey": "robotOverlay.teach",
            "fallback": "staticGreet",
        }
        undo = self._patch_backend(assignment, manifest)
        try:
            runtime = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNotNone(runtime)
        self.assertEqual(
            self.manifest_calls[0]["renderer_capabilities"],
            [
                "teebot-lesson-renderer.v4",
                "teebot-lesson-renderer.v2",
                "teebot-lesson-renderer.v1",
            ],
        )
        self.assertEqual(runtime.negotiated_version, "teebot-lesson-renderer.v2")

    async def test_manifest_fetch_is_pinned_to_assignment_lesson_version(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        assignment = {
            "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
            "assignmentVersion": 1,
            "lessonId": FIX["frames"]["lesson_prepare"]["lessonId"],
            "lessonVersion": 7,
            "manifestChecksum": "9b1f7c2a",
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        undo = self._patch_backend(
            assignment,
            {**_build_manifest(), "lessonVersion": 7},
            etag='"lesson-7-espTft-9b1f7c2a"',
        )
        try:
            rt = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNotNone(rt)
        self.assertEqual(self.manifest_calls[-1]["lesson_id"], assignment["lessonId"])
        self.assertEqual(self.manifest_calls[-1]["profile"], "espTft")
        self.assertEqual(self.manifest_calls[-1]["lesson_version"], 7)

    async def test_new_course_assignment_replaces_prior_runtime_and_pulls_new_lesson_manifest(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        old = _PinnedRuntime(
            assignment_id="assignment-lesson-a",
            lesson_version=3,
            assignment_version=1,
            manifest_checksum="old-course-checksum",
        )
        conn.lesson_runtime = old
        lesson_b_checksum = "b" * 64
        assignment = {
            "assignmentId": "assignment-lesson-b",
            "assignmentVersion": 1,
            "lessonId": "journey-d02-barn-colors",
            "lessonVersion": 1,
            "manifestChecksum": lesson_b_checksum,
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        manifest = {
            **_build_manifest(),
            "lessonId": "journey-d02-barn-colors",
            "lessonVersion": 1,
        }
        undo = self._patch_backend(
            assignment,
            manifest,
            etag=f'"lesson-1-espTft-{lesson_b_checksum}"',
        )
        try:
            rt = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNotNone(rt)
        self.assertIs(conn.lesson_runtime, rt)
        self.assertIsNot(rt, old)
        self.assertTrue(old.closed)
        self.assertEqual(rt.assignment_id, "assignment-lesson-b")
        self.assertEqual(rt.lesson_id, "journey-d02-barn-colors")
        self.assertEqual(rt.manifest_checksum, lesson_b_checksum)
        self.assertEqual(conn.lesson_start_status["code"], "STARTED")
        self.assertEqual(self.manifest_calls[-1]["lesson_id"], "journey-d02-barn-colors")
        prepare = json.loads(conn.websocket.sent[0])
        self.assertEqual(prepare["assignmentId"], "assignment-lesson-b")
        self.assertEqual(prepare["lessonId"], "journey-d02-barn-colors")
        self.assertEqual(
            prepare["body"]["manifestRef"]["manifestChecksum"],
            lesson_b_checksum,
        )

    async def test_manifest_body_lesson_version_must_match_assignment_version(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        assignment = {
            "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
            "assignmentVersion": 1,
            "lessonId": FIX["frames"]["lesson_prepare"]["lessonId"],
            "lessonVersion": 7,
            "manifestChecksum": "9b1f7c2a",
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        stale_manifest = {**_build_manifest(), "lessonVersion": 3}
        undo = self._patch_backend(assignment, stale_manifest, etag='"lesson-7-espTft-9b1f7c2a"')
        try:
            rt = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNone(rt)
        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(conn.lesson_start_status["code"], "MANIFEST_IDENTITY_MISMATCH")

    async def test_manifest_body_must_include_assigned_lesson_identity(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        assignment = {
            "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
            "assignmentVersion": 1,
            "lessonId": FIX["frames"]["lesson_prepare"]["lessonId"],
            "lessonVersion": 7,
            "manifestChecksum": "9b1f7c2a",
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        manifest = {**_build_manifest(), "lessonVersion": 7}
        manifest.pop("lessonId")
        manifest.pop("profile")
        undo = self._patch_backend(assignment, manifest, etag='"lesson-7-espTft-9b1f7c2a"')
        try:
            rt = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNone(rt)
        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(conn.lesson_start_status["code"], "MANIFEST_IDENTITY_MISMATCH")

    async def test_manifest_identity_mismatch_preserves_existing_runtime_for_same_assignment(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        assignment = {
            "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
            "assignmentVersion": 1,
            "lessonId": FIX["frames"]["lesson_prepare"]["lessonId"],
            "lessonVersion": 7,
            "manifestChecksum": "9b1f7c2a",
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        existing = _PinnedRuntime(
            assignment_id=assignment["assignmentId"],
            lesson_version=7,
            assignment_version=1,
            manifest_checksum="9b1f7c2a",
        )
        conn.lesson_runtime = existing
        manifest = {**_build_manifest(), "lessonVersion": 7}
        manifest.pop("lessonId")
        undo = self._patch_backend(assignment, manifest, etag='"lesson-7-espTft-9b1f7c2a"')
        try:
            rt = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIs(rt, existing)
        self.assertFalse(existing.closed)
        self.assertEqual(conn.lesson_start_status["code"], "MANIFEST_IDENTITY_MISMATCH")

    async def test_manifest_body_must_include_assigned_lesson_version(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        assignment = {
            "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
            "assignmentVersion": 1,
            "lessonId": FIX["frames"]["lesson_prepare"]["lessonId"],
            "lessonVersion": 7,
            "manifestChecksum": "9b1f7c2a",
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        manifest = {**_build_manifest(), "lessonVersion": 7}
        manifest.pop("lessonVersion")
        undo = self._patch_backend(assignment, manifest, etag='"lesson-7-espTft-9b1f7c2a"')
        try:
            rt = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNone(rt)
        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(conn.lesson_start_status["code"], "MANIFEST_IDENTITY_MISMATCH")

    async def test_manifest_checksum_must_match_assignment_checksum(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        assignment = {
            "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
            "assignmentVersion": 1,
            "lessonId": FIX["frames"]["lesson_prepare"]["lessonId"],
            "lessonVersion": 7,
            "manifestChecksum": "expected-current-checksum",
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        undo = self._patch_backend(
            assignment,
            {**_build_manifest(), "lessonVersion": 7},
            etag='"lesson-7-espTft-stale-checksum"',
        )
        try:
            rt = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNone(rt)
        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(conn.lesson_start_status["code"], "MANIFEST_CHECKSUM_MISMATCH")

    async def test_manifest_checksum_mismatch_preserves_existing_runtime_for_same_assignment(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        assignment = {
            "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
            "assignmentVersion": 1,
            "lessonId": FIX["frames"]["lesson_prepare"]["lessonId"],
            "lessonVersion": 7,
            "manifestChecksum": "expected-current-checksum",
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        existing = _PinnedRuntime(
            assignment_id=assignment["assignmentId"],
            lesson_version=7,
            assignment_version=1,
            manifest_checksum="expected-current-checksum",
        )
        conn.lesson_runtime = existing
        undo = self._patch_backend(
            assignment,
            {**_build_manifest(), "lessonVersion": 7},
            etag='"lesson-7-espTft-stale-checksum"',
        )
        try:
            rt = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIs(rt, existing)
        self.assertFalse(existing.closed)
        self.assertEqual(conn.lesson_start_status["code"], "MANIFEST_CHECKSUM_MISMATCH")

    async def test_assignment_missing_manifest_checksum_does_not_fetch_manifest(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        assignment = {
            "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
            "assignmentVersion": 1,
            "lessonId": FIX["frames"]["lesson_prepare"]["lessonId"],
            "lessonVersion": 7,
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        undo = self._patch_backend(
            assignment,
            {**_build_manifest(), "lessonVersion": 7},
            etag='"lesson-7-espTft-expected-current-checksum"',
        )
        try:
            rt = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNone(rt)
        self.assertEqual(self.manifest_calls, [])
        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(conn.lesson_start_status["code"], "ASSIGNMENT_INVALID")

    async def test_assignment_blank_manifest_checksum_does_not_fetch_manifest_or_start_firmware(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        assignment = {
            "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
            "assignmentVersion": 1,
            "lessonId": FIX["frames"]["lesson_prepare"]["lessonId"],
            "lessonVersion": 7,
            "profile": "espTft",
            "manifestChecksum": "   ",
            "state": "ASSIGNED",
        }
        undo = self._patch_backend(
            assignment,
            {**_build_manifest(), "lessonVersion": 7},
            etag='"lesson-7-espTft-expected-current-checksum"',
        )
        try:
            rt = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNone(rt)
        self.assertEqual(self.manifest_calls, [])
        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(conn.lesson_start_status["code"], "ASSIGNMENT_INVALID")

    async def test_assignment_missing_required_metadata_does_not_fetch_default_manifest_version(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        assignment = {
            "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
            "lessonId": FIX["frames"]["lesson_prepare"]["lessonId"],
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        undo = self._patch_backend(assignment, _build_manifest())
        try:
            rt = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNone(rt)
        self.assertEqual(self.manifest_calls, [])
        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(conn.lesson_start_status["code"], "ASSIGNMENT_INVALID")


class LessonPullAuthFailureSurfaceTest(unittest.IsolatedAsyncioTestCase):
    async def test_assignment_pull_mints_backend_identity_and_threads_device_token(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect
        import config.manage_api_client as mac
        import config.device_token_client as dtc

        conn = _RepublishConn()
        conn.device_id = "AA:BB:CC:DD:EE:FF"
        conn.config["lesson"]["rollout_device_allowlist"] = [conn.device_id]
        assignment_calls = []

        async def _resolve_device_identity(client, base_url, device_id, *, logger=None):
            self.assertEqual(device_id, "AA:BB:CC:DD:EE:FF")
            return "backend-device-uuid", "device-token"

        async def _get_child_name(client, base_url, device_id, *, token=None):
            return None

        async def _get_assignment(client, base_url, device_id, *, token=None):
            assignment_calls.append((device_id, token))
            return _build_assignment()

        async def _get_manifest(
            client,
            base_url,
            lesson_id,
            profile,
            *,
            token=None,
            renderer_capabilities=None,
            renderer_v2_enabled=False,
            lesson_version=None,
        ):
            self.assertEqual(token, "device-token")
            return _build_manifest(), f'"lesson-3-espTft-{_manifest_checksum()}"'

        saved = (
            dtc.resolve_device_identity,
            mac.get_device_child_name,
            mac.get_current_assignment,
            mac.get_lesson_manifest,
        )
        dtc.resolve_device_identity = _resolve_device_identity
        mac.get_device_child_name = _get_child_name
        mac.get_current_assignment = _get_assignment
        mac.get_lesson_manifest = _get_manifest
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            (
                dtc.resolve_device_identity,
                mac.get_device_child_name,
                mac.get_current_assignment,
                mac.get_lesson_manifest,
            ) = saved

        self.assertIsNotNone(result)
        self.assertEqual(assignment_calls, [("backend-device-uuid", "device-token")])
        self.assertEqual(conn.lesson_start_status["code"], "STARTED")

    async def test_identity_and_manifest_logs_use_authoritative_normalized_fields(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect
        import config.manage_api_client as mac
        import config.device_token_client as dtc

        conn = _RepublishConn(api_base="http://backend.test/v1///")
        conn.device_id = "AA:BB:CC:DD:EE:FF"
        conn.config["lesson"]["rollout_device_allowlist"] = [conn.device_id]
        conn.logger = _CapturingLogger()
        backend_device_id = "14140000-0000-4000-8000-000000000004"
        manifest = {**_build_manifest(), "courseId": "course-from-manifest"}
        assignment = {**_build_assignment(), "courseId": "must-not-be-logged-as-authoritative"}

        async def _resolve_device_identity(client, base_url, device_id, *, logger=None):
            return backend_device_id, "device-token"

        async def _get_child_name(client, base_url, device_id, *, token=None):
            return None

        async def _get_assignment(client, base_url, device_id, *, token=None):
            return assignment

        async def _get_manifest(*args, **kwargs):
            return manifest, f'"lesson-3-espTft-{_manifest_checksum()}"'

        saved = (
            dtc.resolve_device_identity,
            mac.get_device_child_name,
            mac.get_current_assignment,
            mac.get_lesson_manifest,
        )
        dtc.resolve_device_identity = _resolve_device_identity
        mac.get_device_child_name = _get_child_name
        mac.get_current_assignment = _get_assignment
        mac.get_lesson_manifest = _get_manifest
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            (
                dtc.resolve_device_identity,
                mac.get_device_child_name,
                mac.get_current_assignment,
                mac.get_lesson_manifest,
            ) = saved

        self.assertIsNotNone(result)
        messages = "\n".join(message for _level, message in conn.logger.events)
        self.assertIn("apiBase=http://backend.test/v1", messages)
        self.assertIn("deviceMac=AA:BB:CC:DD:EE:FF", messages)
        self.assertIn(f"backendDeviceId={backend_device_id}", messages)
        self.assertIn("courseId=course-from-manifest", messages)
        self.assertNotIn("must-not-be-logged-as-authoritative", messages)

    async def test_no_current_assignment_sets_user_visible_start_status(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect
        import config.manage_api_client as mac
        import config.device_token_client as dtc

        conn = _RepublishConn()

        async def _resolve_device_identity(client, base_url, device_id, *, logger=None):
            return device_id, "device-token"

        async def _get_assignment(client, base_url, device_id, *, token=None):
            return None

        saved = (dtc.resolve_device_identity, mac.get_current_assignment)
        dtc.resolve_device_identity = _resolve_device_identity
        mac.get_current_assignment = _get_assignment
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            dtc.resolve_device_identity, mac.get_current_assignment = saved

        self.assertIsNone(result)
        self.assertEqual(conn.lesson_start_status["code"], "NO_CURRENT_ASSIGNMENT")
        self.assertIn("chưa có bài học", conn.lesson_start_status["message"].lower())
        self.assertEqual(conn.websocket.sent, [])

    async def test_current_assignment_backend_errors_surface_redacted_status(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect
        import config.manage_api_client as mac
        import config.device_token_client as dtc

        for exc in (
            TimeoutError("timeout from http://backend.test/v1 with Bearer secret-device-token"),
            RuntimeError("backend down at http://backend.test/v1?token=secret-device-token"),
        ):
            with self.subTest(exc=type(exc).__name__):
                conn = _RepublishConn()
                events = []

                class _CapturingLogger(_DummyLogger):
                    def warning(self, message, *args, **kwargs):
                        events.append(("warning", str(message)))
                        return None

                conn.logger = _CapturingLogger()

                async def _resolve_device_identity(client, base_url, device_id, *, logger=None):
                    return device_id, "secret-device-token"

                async def _get_child_name(client, base_url, device_id, *, token=None):
                    return None

                async def _get_assignment(client, base_url, device_id, *, token=None):
                    raise exc

                async def _get_manifest(*args, **kwargs):
                    events.append(("manifest", "called"))
                    return _build_manifest(), f'"lesson-3-espTft-{_manifest_checksum()}"'

                saved = (
                    dtc.resolve_device_identity,
                    mac.get_device_child_name,
                    mac.get_current_assignment,
                    mac.get_lesson_manifest,
                )
                dtc.resolve_device_identity = _resolve_device_identity
                mac.get_device_child_name = _get_child_name
                mac.get_current_assignment = _get_assignment
                mac.get_lesson_manifest = _get_manifest
                try:
                    result = await maybe_start_lesson_on_connect(conn)
                finally:
                    (
                        dtc.resolve_device_identity,
                        mac.get_device_child_name,
                        mac.get_current_assignment,
                        mac.get_lesson_manifest,
                    ) = saved

                self.assertIsNone(result)
                self.assertEqual(conn.lesson_start_status["code"], "BACKEND_UNAVAILABLE")
                status_message = conn.lesson_start_status["message"]
                self.assertTrue(status_message)
                self.assertNotIn("secret-device-token", status_message)
                self.assertNotIn("backend.test", status_message)
                self.assertNotIn("Bearer", status_message)
                self.assertFalse(any(level == "manifest" for level, _message in events))
                warning_messages = [message for level, message in events if level == "warning"]
                self.assertTrue(warning_messages)
                for message in warning_messages:
                    self.assertNotIn("secret-device-token", message)
                    self.assertNotIn("backend.test", message)
                    self.assertNotIn("Bearer", message)

    async def test_manifest_backend_error_surfaces_redacted_status(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect
        import config.manage_api_client as mac
        import config.device_token_client as dtc

        conn = _RepublishConn()
        events = []

        class _CapturingLogger(_DummyLogger):
            def warning(self, message, *args, **kwargs):
                events.append(("warning", str(message)))
                return None

        conn.logger = _CapturingLogger()

        async def _resolve_device_identity(client, base_url, device_id, *, logger=None):
            return device_id, "secret-device-token"

        async def _get_child_name(client, base_url, device_id, *, token=None):
            return None

        async def _get_assignment(client, base_url, device_id, *, token=None):
            return {
                "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
                "assignmentVersion": 1,
                "lessonId": FIX["frames"]["lesson_prepare"]["lessonId"],
                "lessonVersion": 3,
                "manifestChecksum": _manifest_checksum(),
                "profile": "espTft",
                "state": "ASSIGNED",
            }

        async def _get_manifest(*args, **kwargs):
            raise RuntimeError("manifest fetch failed at http://backend.test/v1?token=secret-device-token")

        saved = (
            dtc.resolve_device_identity,
            mac.get_device_child_name,
            mac.get_current_assignment,
            mac.get_lesson_manifest,
        )
        dtc.resolve_device_identity = _resolve_device_identity
        mac.get_device_child_name = _get_child_name
        mac.get_current_assignment = _get_assignment
        mac.get_lesson_manifest = _get_manifest
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            (
                dtc.resolve_device_identity,
                mac.get_device_child_name,
                mac.get_current_assignment,
                mac.get_lesson_manifest,
            ) = saved

        self.assertIsNone(result)
        self.assertEqual(conn.lesson_start_status["code"], "BACKEND_UNAVAILABLE")
        status_message = conn.lesson_start_status["message"]
        self.assertTrue(status_message)
        self.assertNotIn("secret-device-token", status_message)
        self.assertNotIn("backend.test", status_message)
        self.assertNotIn("Bearer", status_message)
        warning_messages = [message for level, message in events if level == "warning"]
        self.assertTrue(warning_messages)
        for message in warning_messages:
            self.assertNotIn("secret-device-token", message)
            self.assertNotIn("backend.test", message)
            self.assertNotIn("Bearer", message)
        self.assertEqual(conn.websocket.sent, [])

    async def test_tokenless_assignment_failure_surfaces_backend_unavailable_status(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect
        import config.manage_api_client as mac
        import config.device_token_client as dtc

        conn = _RepublishConn()
        conn.device_id = "AA:BB:CC:DD:EE:FF"
        conn.config["lesson"]["rollout_device_allowlist"] = [conn.device_id]
        events = []

        class _CapturingLogger(_DummyLogger):
            def warning(self, message, *args, **kwargs):
                events.append(("warning", str(message)))
                return None

            def info(self, message, *args, **kwargs):
                events.append(("info", str(message)))
                return None

        conn.logger = _CapturingLogger()

        async def _resolve_device_identity(client, base_url, mac_addr, *, logger=None):
            self.assertEqual(mac_addr, "AA:BB:CC:DD:EE:FF")
            return None, None

        async def _get_assignment(client, base_url, device_id, *, token=None):
            raise AssertionError("tokenless MAC fallback must not call assignment/current")

        saved = (dtc.resolve_device_identity, mac.get_current_assignment)
        dtc.resolve_device_identity = _resolve_device_identity
        mac.get_current_assignment = _get_assignment
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            dtc.resolve_device_identity, mac.get_current_assignment = saved

        self.assertIsNone(result)
        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(conn.lesson_start_status["code"], "BACKEND_UNAVAILABLE")
        self.assertTrue(
            any("lesson backend identity unavailable" in message for _level, message in events),
            events,
        )

    async def test_tokenless_assignment_exception_sets_backend_unavailable_status(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect
        import config.manage_api_client as mac
        import config.device_token_client as dtc

        conn = _RepublishConn()
        events = []

        class _CapturingLogger(_DummyLogger):
            def warning(self, message, *args, **kwargs):
                events.append(("warning", str(message)))
                return None

        conn.logger = _CapturingLogger()

        async def _resolve_device_identity(client, base_url, device_id, *, logger=None):
            raise RuntimeError("mint route refused unclaimed device with secret-token")

        async def _get_assignment(client, base_url, device_id, *, token=None):
            raise AssertionError("assignment/current must not run after token mint failure")

        saved = (dtc.resolve_device_identity, mac.get_current_assignment)
        dtc.resolve_device_identity = _resolve_device_identity
        mac.get_current_assignment = _get_assignment
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            dtc.resolve_device_identity, mac.get_current_assignment = saved

        self.assertIsNone(result)
        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(conn.lesson_start_status["code"], "BACKEND_UNAVAILABLE")
        self.assertTrue(
            any("lesson backend identity unavailable" in message for _level, message in events),
            events,
        )


# ── P5 republish-on-connect (no reconnect) ──────────────────────────────────────


class _RepublishConn:
    """Minimal conn for maybe_start_lesson_on_connect: lesson config + features +
    device_id + a settable is_realtime_busy + a pinnable lesson_runtime."""

    def __init__(self, *, busy=False, api_base="http://backend.test/v1"):
        self.logger = _DummyLogger()
        self.websocket = _FakeWebSocket()
        self.session_id = FIX["frames"]["lesson_prepare"]["sessionId"]
        self.device_id = "dev-republish"
        self.features = {"lesson": True, "renderer": "teebot-lesson-renderer.v1"}
        self.config = {
            "lesson": {
                "api_base": api_base,
                "runtime_enabled": True,
                "rollout_device_allowlist": [self.device_id],
            }
        }
        self.lesson_runtime = None
        self.lesson_voice_alarm = None
        self._busy = busy

    def is_realtime_busy(self):
        return self._busy

    def _disable_lesson_runtime(self):
        return None


class _RegistryStartLessonHandler:
    """Minimal real-tool handler: dispatches start_lesson through the registry."""

    def __init__(self):
        self.calls = []

    def get_functions(self):
        import plugins_func.functions.start_lesson as _start_lesson  # noqa: F401
        from plugins_func.register import all_function_registry

        return [all_function_registry["start_lesson"].description]

    async def handle_llm_function_call(self, conn, payload):
        import plugins_func.functions.start_lesson as _start_lesson  # noqa: F401
        from plugins_func.register import all_function_registry

        self.calls.append(payload)
        return all_function_registry[payload["name"]].func(conn)


class _LiveClientStub:
    connected = True

    def __init__(self, provider=None):
        self.provider = provider
        self.sent_texts = []
        self.interrupts = 0
        self.audio_stream_ends = 0

    async def interrupt(self):
        self.interrupts += 1

    async def end_audio_stream(self):
        self.audio_stream_ends += 1

    async def send_text(self, text):
        self.sent_texts.append(text)
        if self.provider is not None:
            self.provider.conn.google_live_lesson_prompt_output_allowed = False

    async def close(self):
        self.connected = False


class _PinnedRuntime:
    """Stand-in for an already-running lesson session, so the republish guard can
    read its version identity + verify teardown is invoked."""

    def __init__(
        self,
        *,
        assignment_id,
        lesson_version,
        assignment_version,
        manifest_checksum=None,
        state="RUNNING",
        session_id="11111111-1111-4111-8111-111111111111",
        terminal_pending=False,
        terminal_replay_result=True,
    ):
        self.assignment_id = assignment_id
        self.lesson_version = lesson_version
        self.assignment_version = assignment_version
        self.manifest_checksum = manifest_checksum or _manifest_checksum()
        self.state = state
        self.session_id = session_id
        self.asset_cache = _EvictableCache()
        self.closed = False
        self.terminal_replay_calls = 0
        self.terminal_replay_result = terminal_replay_result
        self.forwarder = type(
            "_PinnedForwarder",
            (),
            {"pending_terminal_batch": {"events": [{"type": "lesson_failed"}]} if terminal_pending else None},
        )()

    async def close(self):
        self.closed = True

    async def replay_pending_terminal_event(self):
        self.terminal_replay_calls += 1
        if self.terminal_replay_result:
            self.forwarder.pending_terminal_batch = None
        return self.terminal_replay_result


class _EvictableCache:
    def __init__(self):
        self.evicted = False

    async def evict(self):
        self.evicted = True

    async def aclose(self):
        return None


class RepublishOnConnectTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import core.lesson.forwarder as forwarder_module

        self._saved_terminal_store = forwarder_module._DEFAULT_TERMINAL_STORE
        self._saved_pending_terminal_batches = dict(
            forwarder_module._PENDING_TERMINAL_BATCHES
        )
        forwarder_module._PENDING_TERMINAL_BATCHES.clear()
        forwarder_module._DEFAULT_TERMINAL_STORE = (
            forwarder_module.MemoryTerminalReplayStore()
        )

    def tearDown(self):
        import core.lesson.forwarder as forwarder_module

        forwarder_module._PENDING_TERMINAL_BATCHES.clear()
        forwarder_module._PENDING_TERMINAL_BATCHES.update(
            self._saved_pending_terminal_batches
        )
        forwarder_module._DEFAULT_TERMINAL_STORE = self._saved_terminal_store

    def _patch_backend(self, assignment, manifest, etag='"lesson-3-espTft-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3"'):
        """Monkeypatch the REAL config.manage_api_client attributes the runtime
        resolves at call time (conftest does NOT stub this module). Returns an undo callable."""
        import config.manage_api_client as mac
        import config.device_token_client as dtc

        # Record the renderer_capabilities the runtime forwards to the manifest
        # fetch (L3 P3) so a test can assert the device capability set is threaded
        # through. The fake's signature MUST accept the keyword the production call
        # now passes (and stay tolerant of older positional-only callers).
        self.manifest_calls = []

        async def _get_assignment(client, base_url, device_id, *, token=None):
            return assignment

        async def _get_manifest(
            client,
            base_url,
            lesson_id,
            profile,
            *,
            token=None,
            renderer_capabilities=None,
            renderer_v2_enabled=False,
            lesson_version=None,
        ):
            self.manifest_calls.append(
                {
                    "lesson_id": lesson_id,
                    "lesson_version": lesson_version,
                    "profile": profile,
                    "renderer_capabilities": renderer_capabilities,
                }
            )
            return manifest, etag

        async def _resolve_device_identity(client, base_url, device_id, *, logger=None):
            return device_id, "device-token"

        saved = (mac.get_current_assignment, mac.get_lesson_manifest, dtc.resolve_device_identity)
        mac.get_current_assignment = _get_assignment
        mac.get_lesson_manifest = _get_manifest
        dtc.resolve_device_identity = _resolve_device_identity

        def _undo():
            mac.get_current_assignment, mac.get_lesson_manifest, dtc.resolve_device_identity = saved

        return _undo

    def _assignment(
        self,
        *,
        lesson_version,
        assignment_version,
        state="ASSIGNED",
        manifest_checksum=None,
        assignment_id=None,
        session_id=None,
    ):
        prep = FIX["frames"]["lesson_prepare"]
        assignment = {
            "assignmentId": assignment_id or prep["assignmentId"],
            "assignmentVersion": assignment_version,
            "lessonId": prep["lessonId"],
            "lessonVersion": lesson_version,
            "manifestChecksum": manifest_checksum or _manifest_checksum(),
            "profile": "espTft",
            "state": state,
        }
        if session_id is not None:
            assignment["sessionId"] = session_id
        return assignment

    async def test_new_assignment_on_same_websocket_mints_run_uuid_for_frames_and_events(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        conversational_session = conn.session_id
        historical_assignment_session = "22222222-2222-4222-8222-222222222222"
        prior = _PinnedRuntime(
            assignment_id="old-assignment",
            lesson_version=2,
            assignment_version=1,
        )
        conn.lesson_runtime = prior
        prior_session_id = prior.session_id
        conversational_session_id = conn.session_id
        undo = self._patch_backend(
            self._assignment(
                lesson_version=3,
                assignment_version=1,
                assignment_id="new-assignment",
                session_id=historical_assignment_session,
            ),
            _build_manifest(),
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNotNone(result)
        self.assertEqual(uuid.UUID(result.session_id).version, 4)
        self.assertNotEqual(result.session_id, conversational_session)
        self.assertNotEqual(result.session_id, historical_assignment_session)
        self.assertEqual(conn.session_id, conversational_session)
        self.assertTrue(prior.closed)
        frames = [json.loads(payload) for payload in conn.websocket.sent]
        self.assertEqual({frame["sessionId"] for frame in frames}, {result.session_id})

        forwarder = _FakeForwarder()
        result.forwarder = forwarder
        result._forward({"type": "lesson_started"})
        self.assertEqual(forwarder.batches[0]["sessionId"], result.session_id)

    async def test_reconnect_ignores_historical_assignment_session_and_mints_new_run_uuid(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        historical_assignment_session = "33333333-3333-4333-8333-333333333333"
        undo = self._patch_backend(
            self._assignment(
                lesson_version=3,
                assignment_version=1,
                session_id=historical_assignment_session,
            ),
            _build_manifest(),
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertEqual(uuid.UUID(result.session_id).version, 4)
        self.assertNotEqual(result.session_id, historical_assignment_session)
        self.assertNotEqual(result.session_id, conn.session_id)

    async def test_missing_config_skips_lesson_start_with_status(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn(api_base=None)

        result = await maybe_start_lesson_on_connect(conn)

        self.assertIsNone(result)
        self.assertEqual(conn.lesson_start_status["code"], "LESSON_CONFIG_MISSING")
        self.assertEqual(conn.websocket.sent, [])

    async def test_malformed_server_config_skips_lesson_start_with_status(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn(api_base=None)
        conn.config["server"] = "bad"

        result = await maybe_start_lesson_on_connect(conn)

        self.assertIsNone(result)
        self.assertEqual(conn.lesson_start_status["code"], "LESSON_CONFIG_MISSING")
        self.assertEqual(conn.websocket.sent, [])

    async def test_missing_config_with_no_logger_is_still_a_noop(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn(api_base=None)
        conn.logger = None

        result = await maybe_start_lesson_on_connect(conn)

        self.assertIsNone(result)
        self.assertEqual(conn.lesson_start_status["code"], "LESSON_CONFIG_MISSING")

    async def test_missing_config_with_broken_logger_is_still_a_noop(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        class _BrokenLogger:
            def bind(self, **_kwargs):
                raise RuntimeError("logger unavailable")

        conn = _RepublishConn(api_base=None)
        conn.logger = _BrokenLogger()

        result = await maybe_start_lesson_on_connect(conn)

        self.assertIsNone(result)
        self.assertEqual(conn.lesson_start_status["code"], "LESSON_CONFIG_MISSING")

    async def test_feature_wait_loop_yields_before_capability_reject(self):
        import core.lesson.runtime as runtime_mod

        conn = _RepublishConn()
        conn.features = None
        sleeps = []

        async def fast_sleep(delay):
            sleeps.append(delay)
            conn.features = {"lesson": False}

        saved_sleep = runtime_mod.asyncio.sleep
        runtime_mod.asyncio.sleep = fast_sleep
        try:
            result = await runtime_mod.maybe_start_lesson_on_connect(conn)
        finally:
            runtime_mod.asyncio.sleep = saved_sleep

        self.assertIsNone(result)
        self.assertEqual(sleeps, [0.1])
        self.assertEqual(conn.lesson_start_status["code"], "LESSON_CAPABILITY_MISSING")

    async def test_missing_lesson_capability_skips_before_backend_calls(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        conn.features = {"lesson": False}

        result = await maybe_start_lesson_on_connect(conn)

        self.assertIsNone(result)
        self.assertEqual(conn.lesson_start_status["code"], "LESSON_CAPABILITY_MISSING")
        self.assertEqual(conn.websocket.sent, [])

    async def test_terminal_assignment_state_skips_restart(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1, state="COMPLETED"),
            _build_manifest(),
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNone(result)
        self.assertEqual(conn.lesson_start_status["code"], "ASSIGNMENT_TERMINAL")
        self.assertEqual(conn.websocket.sent, [])

    async def test_rollback_cancelled_assignment_is_refused_cleanly(self):
        """T6.3: CANCELLED is the state a staged rollback actually produces.

        The backend refuses to assign the previous published version while the bad one is
        still active (409 ASSIGNMENT_CONFLICT via ux_one_active_assignment_per_device), so
        the operator must terminalize it first. The robot must then decline to restart it
        without wedging — status set, nothing emitted. COMPLETED is covered above; this
        pins the two states a rollback and an incident stop really go through.
        """
        from core.lesson.runtime import maybe_start_lesson_on_connect

        for state in ("CANCELLED", "FAILED"):
            with self.subTest(state=state):
                conn = _RepublishConn()
                undo = self._patch_backend(
                    self._assignment(lesson_version=3, assignment_version=1, state=state),
                    _build_manifest(),
                )
                try:
                    result = await maybe_start_lesson_on_connect(conn)
                finally:
                    undo()

                self.assertIsNone(result)
                self.assertEqual(conn.lesson_start_status["code"], "ASSIGNMENT_TERMINAL")
                self.assertEqual(conn.websocket.sent, [])

    async def test_assignment_is_read_once_per_start_so_a_running_lesson_is_not_revoked(self):
        """T6.3: pins the cross-component mid-assignment rollback policy.

        The backend rollout gate runs only on assignment CREATION and never mutates rows,
        and the robot reads the assignment exactly once — at start. Together that is the
        policy `docs/lesson-studio-rollout-runbook.md` documents: a lesson already RUNNING
        when an operator flips a rollout flag runs to completion rather than being torn
        down mid-step, and the rolled-back version is picked up on the next start.

        If a mid-lesson re-poll is ever added, that policy changes and this test should
        fail loudly rather than the runbook quietly becoming wrong.
        """
        import config.manage_api_client as mac
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1),
            _build_manifest(),
        )
        patched = mac.get_current_assignment
        calls = []

        async def _counting(*args, **kwargs):
            calls.append(1)
            return await patched(*args, **kwargs)

        mac.get_current_assignment = _counting
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            mac.get_current_assignment = patched
            undo()

        self.assertIsNotNone(result)
        self.assertEqual(len(calls), 1)

    # NOTE (T6.3): "re-enable after rollback starts cleanly" is deliberately NOT tested
    # here. The shared fixture carries a single lesson version, so an older-version
    # assignment is correctly refused with MANIFEST_IDENTITY_MISMATCH (the manifest fetched
    # is still v3) — proving the identity guard, not the rollback. That guard already has
    # four dedicated tests above, and on the robot a rolled-back assignment is an ordinary
    # start with no residue from the cancelled one. The backend half of the box is covered
    # by lesson-rollout.drill.spec.ts stage 4. Testing it properly here needs a second
    # seeded lesson version in the fixture — the same gap F-T53-09 blocks T5.3 on.

    async def test_empty_manifest_sets_status_and_does_not_start(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1),
            None,
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNone(result)
        self.assertEqual(conn.lesson_start_status["code"], "MANIFEST_EMPTY")
        self.assertEqual(conn.websocket.sent, [])

    async def test_manifest_missing_checksum_sets_status_and_does_not_start(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1),
            _build_manifest(),
            etag=None,
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNone(result)
        self.assertEqual(conn.lesson_start_status["code"], "MANIFEST_CHECKSUM_MISSING")
        self.assertIsNone(conn.lesson_runtime)
        self.assertEqual(conn.websocket.sent, [])

    async def test_republish_with_malformed_checksum_keeps_existing_runtime_without_eviction(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"],
            lesson_version=3,
            assignment_version=1,
            manifest_checksum="9b1f7c2a",
        )
        conn.lesson_runtime = pinned
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1),
            _build_manifest(),
            etag="malformed",
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIs(result, pinned)
        self.assertEqual(conn.lesson_start_status["code"], "MANIFEST_CHECKSUM_MISSING")
        self.assertIs(conn.lesson_runtime, pinned)
        self.assertFalse(pinned.closed)
        self.assertFalse(pinned.asset_cache.evicted)
        self.assertEqual(conn.websocket.sent, [])

    async def test_new_assignment_closes_prior_runtime_and_enters_lesson_mode(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        conn.lesson_runtime = _PinnedRuntime(
            assignment_id="old-assignment", lesson_version=2, assignment_version=1
        )
        entered = []

        async def enter_lesson_mode(*, reason):
            entered.append(reason)

        conn.enter_lesson_mode = enter_lesson_mode
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1),
            _build_manifest(),
        )
        prior = conn.lesson_runtime
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNotNone(result)
        self.assertTrue(prior.closed)
        self.assertEqual(entered, ["lesson_start"])
        self.assertEqual(conn.lesson_start_status["code"], "STARTED")

    async def test_connect_preload_keeps_conversation_mode_until_assets_are_ready(self):
        import core.lesson.runtime as runtime_module

        conn = _RepublishConn()
        conn.session_mode = "CONVERSATION"
        entered = []
        modes_seen_during_preload = []

        async def enter_lesson_mode(*, reason):
            entered.append(reason)
            conn.session_mode = "LESSON"

        async def blocked_preload(_runtime):
            modes_seen_during_preload.append(conn.session_mode)
            return False

        conn.enter_lesson_mode = enter_lesson_mode
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1),
            _build_manifest(),
        )
        try:
            with patch.object(runtime_module.LessonRuntime, "preload_only", new=blocked_preload):
                result = await runtime_module.maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNone(result)
        self.assertEqual(modes_seen_during_preload, ["CONVERSATION"])
        self.assertEqual(entered, [])
        self.assertEqual(conn.lesson_start_status["code"], "START_REFUSED")

    async def test_start_protocol_crash_releases_lesson_mode_when_no_prior_runtime(self):
        import core.lesson.runtime as runtime_module

        conn = _RepublishConn()
        entered = []
        released = []

        async def enter_lesson_mode(*, reason):
            entered.append(reason)

        async def release_lesson_mode(*, reason):
            released.append(reason)

        async def crash_start_protocol(_runtime, *, preloaded=False):
            self.assertTrue(preloaded)
            raise RuntimeError("prepare send crashed")

        conn.enter_lesson_mode = enter_lesson_mode
        conn.release_lesson_mode = release_lesson_mode
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1),
            _build_manifest(),
        )
        try:
            with patch.object(
                runtime_module.LessonRuntime,
                "start_protocol",
                new=crash_start_protocol,
            ):
                result = await runtime_module.maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNone(result)
        self.assertEqual(entered, ["lesson_start"])
        self.assertEqual(released, ["lesson_start_failed"])
        self.assertIsNone(conn.lesson_runtime)

    async def test_candidate_lesson_error_keeps_prior_runtime_in_lesson_mode(self):
        import core.lesson.runtime as runtime_module

        conn = _RepublishConn()
        prior = _PinnedRuntime(
            assignment_id="old-assignment",
            lesson_version=2,
            assignment_version=1,
        )
        conn.lesson_runtime = prior
        prior_session_id = prior.session_id
        conversational_session_id = conn.session_id
        released = []

        async def release_lesson_mode(*, reason):
            released.append(reason)

        async def reject_preload(_runtime):
            raise LessonError("ASSET_CHECKSUM_MISMATCH", "bad candidate")

        conn.release_lesson_mode = release_lesson_mode
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1),
            _build_manifest(),
        )
        try:
            with patch.object(runtime_module.LessonRuntime, "preload_only", new=reject_preload):
                result = await runtime_module.maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIs(result, prior)
        self.assertIs(conn.lesson_runtime, prior)
        self.assertEqual(prior.session_id, prior_session_id)
        self.assertEqual(conn.session_id, conversational_session_id)
        self.assertEqual(released, [])
        self.assertFalse(prior.closed)

    async def test_candidate_preload_failure_is_silent_until_activation(self):
        import core.lesson.asset_cache as asset_cache_module
        import core.lesson.forwarder as forwarder_module
        import core.lesson.runtime as runtime_module

        conn = _RepublishConn()
        conn.config["lesson"]["asset_delivery_mode"] = "sd_pack"
        prior = _PinnedRuntime(
            assignment_id="old-assignment",
            lesson_version=2,
            assignment_version=1,
        )
        conn.lesson_runtime = prior
        prior_session_id = prior.session_id
        conversational_session_id = conn.session_id
        released = []
        candidate_forwarders = []

        async def release_lesson_mode(*, reason):
            released.append(reason)

        class _CandidateForwarder(_FakeForwarder):
            def __init__(self, **_kwargs):
                super().__init__()
                candidate_forwarders.append(self)

        conn.release_lesson_mode = release_lesson_mode
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1),
            _build_manifest(),
        )
        try:
            with patch.object(forwarder_module, "LessonEventForwarder", _CandidateForwarder), patch.object(
                asset_cache_module,
                "AssetCache",
                side_effect=lambda **_kwargs: _FakeAssetCache(
                    preload_error=LessonError("ASSET_CHECKSUM_MISMATCH", "bad candidate")
                ),
            ):
                result = await runtime_module.maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIs(result, prior)
        self.assertIs(conn.lesson_runtime, prior)
        self.assertEqual(prior.session_id, prior_session_id)
        self.assertEqual(conn.session_id, conversational_session_id)
        self.assertFalse(prior.closed)
        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(released, [])
        self.assertEqual(candidate_forwarders[0].batches, [])

    async def test_start_refused_releases_lesson_mode_and_surfaces_status(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        released = []

        async def release_lesson_mode(*, reason):
            released.append(reason)

        conn.release_lesson_mode = release_lesson_mode
        manifest = _build_manifest()
        manifest["manifestVersion"] = "teebot-lesson-renderer.v2"
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1),
            manifest,
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNone(result)
        self.assertEqual(conn.lesson_start_status["code"], "START_REFUSED")
        self.assertEqual(released, ["lesson_start_refused"])
        self.assertIsNone(conn.lesson_runtime)
        self.assertEqual(conn.websocket.sent, [])

    async def test_sd_pack_pre_prepare_failure_does_not_report_started(self):
        import core.lesson.asset_cache as asset_cache_module
        import core.lesson.runtime as runtime_module

        conn = _RepublishConn()
        conn.config["lesson"]["asset_delivery_mode"] = "sd_pack"
        released = []

        async def release_lesson_mode(*, reason):
            released.append(reason)

        conn.release_lesson_mode = release_lesson_mode
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1),
            _build_manifest(),
        )
        saved_asset_cache = asset_cache_module.AssetCache
        asset_cache_module.AssetCache = lambda **_kwargs: _FakeAssetCache(
            preload_error=LessonError("ASSET_CHECKSUM_MISMATCH", "bad asset")
        )
        try:
            result = await runtime_module.maybe_start_lesson_on_connect(conn)
        finally:
            asset_cache_module.AssetCache = saved_asset_cache
            undo()

        self.assertIsNone(result)
        self.assertEqual(conn.lesson_start_status["code"], "START_REFUSED")
        self.assertEqual(released, ["sd_asset_pack_preload_failed"])
        self.assertIsNone(conn.lesson_runtime)
        sent = [json.loads(payload) for payload in conn.websocket.sent]
        self.assertEqual([frame["type"] for frame in sent], ["lesson_error"])
        self.assertEqual(sent[0]["body"]["code"], "ASSET_CHECKSUM_MISMATCH")

    async def test_asset_cache_size_limits_are_passed_from_lesson_config(self):
        import core.lesson.asset_cache as asset_cache_module
        import core.lesson.runtime as runtime_module

        conn = _RepublishConn()
        conn.config["lesson"].update({"max_asset_bytes": 12345, "max_total_asset_bytes": 67890})
        captured = []
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1),
            _build_manifest(),
        )
        saved_asset_cache = asset_cache_module.AssetCache

        def _asset_cache_factory(**kwargs):
            captured.append(kwargs)
            return _FakeAssetCache(ready=True)

        asset_cache_module.AssetCache = _asset_cache_factory
        try:
            result = await runtime_module.maybe_start_lesson_on_connect(conn)
        finally:
            asset_cache_module.AssetCache = saved_asset_cache
            undo()

        self.assertIsNotNone(result)
        self.assertEqual(captured[0]["max_asset_bytes"], 12345)
        self.assertEqual(captured[0]["max_total_asset_bytes"], 67890)

    async def test_malformed_asset_cache_config_uses_defaults(self):
        import core.lesson.asset_cache as asset_cache_module
        import core.lesson.runtime as runtime_module

        conn = _RepublishConn()
        conn.config["lesson"].update({
            "preload_timeout_sec": "bad",
            "preload_concurrency": "bad",
            "max_asset_bytes": "bad",
            "max_total_asset_bytes": "bad",
        })
        captured = []
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1),
            _build_manifest(),
        )
        saved_asset_cache = asset_cache_module.AssetCache

        def _asset_cache_factory(**kwargs):
            captured.append(kwargs)
            return _FakeAssetCache(ready=True)

        asset_cache_module.AssetCache = _asset_cache_factory
        try:
            result = await runtime_module.maybe_start_lesson_on_connect(conn)
        finally:
            asset_cache_module.AssetCache = saved_asset_cache
            undo()

        self.assertIsNotNone(result)
        self.assertEqual(captured[0]["preload_timeout_sec"], 90.0)
        self.assertEqual(captured[0]["concurrency"], 2)
        self.assertEqual(captured[0]["max_asset_bytes"], 8 * 1024 * 1024)
        self.assertEqual(captured[0]["max_total_asset_bytes"], 64 * 1024 * 1024)

    async def test_non_positive_asset_cache_config_uses_defaults(self):
        import core.lesson.asset_cache as asset_cache_module
        import core.lesson.runtime as runtime_module

        conn = _RepublishConn()
        conn.config["lesson"].update({
            "preload_timeout_sec": -1,
            "preload_concurrency": 0,
            "max_asset_bytes": -1,
            "max_total_asset_bytes": 0,
        })
        captured = []
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1),
            _build_manifest(),
        )
        saved_asset_cache = asset_cache_module.AssetCache

        def _asset_cache_factory(**kwargs):
            captured.append(kwargs)
            return _FakeAssetCache(ready=True)

        asset_cache_module.AssetCache = _asset_cache_factory
        try:
            result = await runtime_module.maybe_start_lesson_on_connect(conn)
        finally:
            asset_cache_module.AssetCache = saved_asset_cache
            undo()

        self.assertIsNotNone(result)
        self.assertEqual(captured[0]["preload_timeout_sec"], 90.0)
        self.assertEqual(captured[0]["concurrency"], 2)
        self.assertEqual(captured[0]["max_asset_bytes"], 8 * 1024 * 1024)
        self.assertEqual(captured[0]["max_total_asset_bytes"], 64 * 1024 * 1024)

    async def test_unchanged_version_keeps_existing_session(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"], lesson_version=3, assignment_version=1
        )
        conn.lesson_runtime = pinned
        pinned_session_id = pinned.session_id
        conversational_session_id = conn.session_id

        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1), _build_manifest()
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        # Same version -> idempotent no-op: existing kept, NOT torn down, NO new frame.
        self.assertIs(result, pinned)
        self.assertIs(conn.lesson_runtime, pinned)
        self.assertEqual(pinned.session_id, pinned_session_id)
        self.assertEqual(conn.session_id, conversational_session_id)
        self.assertFalse(pinned.closed)
        self.assertFalse(pinned.asset_cache.evicted)
        self.assertEqual(conn.websocket.sent, [])

    async def test_unchanged_paused_session_restarts_when_child_says_start_again(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"],
            lesson_version=3,
            assignment_version=1,
            state="PAUSED",
        )
        conn.lesson_runtime = pinned

        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1, state="PAUSED"),
            _build_manifest(),
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNotNone(result)
        self.assertIsNot(result, pinned)
        self.assertIs(conn.lesson_runtime, result)
        self.assertTrue(pinned.closed)
        self.assertFalse(pinned.asset_cache.evicted)
        self.assertEqual(conn.lesson_start_status["code"], "STARTED")
        self.assertEqual([json.loads(p)["type"] for p in conn.websocket.sent], ["lesson_prepare"])

    async def test_unchanged_failed_session_restarts_when_child_says_start_again(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"],
            lesson_version=3,
            assignment_version=1,
            state="FAILED",
        )
        conn.lesson_runtime = pinned

        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1, state="ASSIGNED"),
            _build_manifest(),
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNotNone(result)
        self.assertIsNot(result, pinned)
        self.assertIs(conn.lesson_runtime, result)
        self.assertEqual(pinned.terminal_replay_calls, 0)
        self.assertTrue(pinned.closed)
        self.assertFalse(pinned.asset_cache.evicted)
        self.assertEqual(conn.lesson_start_status["code"], "STARTED")
        self.assertEqual([json.loads(p)["type"] for p in conn.websocket.sent], ["lesson_prepare"])

    async def test_unchanged_failed_session_with_local_pending_terminal_blocks_restart_on_replay_failure(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"],
            lesson_version=3,
            assignment_version=1,
            state="FAILED",
            terminal_pending=True,
            terminal_replay_result=False,
        )
        conn.lesson_runtime = pinned
        pinned_session_id = pinned.session_id

        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1, state="ASSIGNED"),
            _build_manifest(),
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIs(result, pinned)
        self.assertIs(conn.lesson_runtime, pinned)
        self.assertEqual(pinned.session_id, pinned_session_id)
        self.assertEqual(pinned.terminal_replay_calls, 1)
        self.assertFalse(pinned.closed)
        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(self.manifest_calls, [])
        self.assertEqual(conn.lesson_start_status["code"], "TERMINAL_REPLAY_PENDING")

    async def test_unchanged_failed_session_with_local_pending_terminal_skips_restart_after_replay_success(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"],
            lesson_version=3,
            assignment_version=1,
            state="FAILED",
            terminal_pending=True,
            terminal_replay_result=True,
        )
        conn.lesson_runtime = pinned

        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1, state="ASSIGNED"),
            _build_manifest(),
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIs(result, pinned)
        self.assertIs(conn.lesson_runtime, pinned)
        self.assertEqual(pinned.terminal_replay_calls, 1)
        self.assertFalse(pinned.closed)
        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(self.manifest_calls, [])
        self.assertEqual(conn.lesson_start_status["code"], "TERMINAL_REPLAYED")

    async def test_unchanged_failed_session_rechecks_terminal_pending_after_manifest_fetch_failure(self):
        import config.manage_api_client as mac
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"],
            lesson_version=3,
            assignment_version=1,
            state="FAILED",
            terminal_replay_result=False,
        )
        conn.lesson_runtime = pinned
        pinned_session_id = pinned.session_id

        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1, state="ASSIGNED"),
            _build_manifest(),
        )
        get_manifest = mac.get_lesson_manifest

        async def inject_pending_during_manifest(*args, **kwargs):
            pinned.forwarder.pending_terminal_batch = {
                "events": [{"type": "lesson_failed"}]
            }
            return await get_manifest(*args, **kwargs)

        mac.get_lesson_manifest = inject_pending_during_manifest
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIs(result, pinned)
        self.assertIs(conn.lesson_runtime, pinned)
        self.assertEqual(pinned.session_id, pinned_session_id)
        self.assertEqual(pinned.terminal_replay_calls, 1)
        self.assertFalse(pinned.closed)
        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(conn.lesson_start_status["code"], "TERMINAL_REPLAY_PENDING")

    async def test_unchanged_failed_session_rechecks_terminal_pending_after_manifest_fetch_success(self):
        import config.manage_api_client as mac
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"],
            lesson_version=3,
            assignment_version=1,
            state="FAILED",
            terminal_replay_result=True,
        )
        conn.lesson_runtime = pinned

        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1, state="ASSIGNED"),
            _build_manifest(),
        )
        get_manifest = mac.get_lesson_manifest

        async def inject_pending_during_manifest(*args, **kwargs):
            pinned.forwarder.pending_terminal_batch = {
                "events": [{"type": "lesson_failed"}]
            }
            return await get_manifest(*args, **kwargs)

        mac.get_lesson_manifest = inject_pending_during_manifest
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIs(result, pinned)
        self.assertIs(conn.lesson_runtime, pinned)
        self.assertEqual(pinned.terminal_replay_calls, 1)
        self.assertFalse(pinned.closed)
        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(conn.lesson_start_status["code"], "TERMINAL_REPLAYED")

    async def test_changed_assignment_version_rechecks_terminal_pending_after_manifest_fetch_failure(self):
        import config.manage_api_client as mac
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"],
            lesson_version=3,
            assignment_version=1,
            state="FAILED",
            terminal_replay_result=False,
        )
        conn.lesson_runtime = pinned
        pinned_session_id = pinned.session_id

        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=2, state="ASSIGNED"),
            _build_manifest(),
        )
        get_manifest = mac.get_lesson_manifest

        async def inject_pending_during_manifest(*args, **kwargs):
            pinned.forwarder.pending_terminal_batch = {
                "events": [{"type": "lesson_failed"}]
            }
            return await get_manifest(*args, **kwargs)

        mac.get_lesson_manifest = inject_pending_during_manifest
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIs(result, pinned)
        self.assertIs(conn.lesson_runtime, pinned)
        self.assertEqual(pinned.session_id, pinned_session_id)
        self.assertEqual(pinned.terminal_replay_calls, 1)
        self.assertFalse(pinned.closed)
        self.assertEqual(conn.websocket.sent, [])
        self.assertIsNone(getattr(conn, "lesson_runtime_candidate", None))
        self.assertEqual(conn.lesson_start_status["code"], "TERMINAL_REPLAY_PENDING")

    async def test_changed_assignment_version_rechecks_terminal_pending_after_manifest_fetch_success(self):
        import config.manage_api_client as mac
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"],
            lesson_version=3,
            assignment_version=1,
            state="FAILED",
            terminal_replay_result=True,
        )
        conn.lesson_runtime = pinned

        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=2, state="ASSIGNED"),
            _build_manifest(),
        )
        get_manifest = mac.get_lesson_manifest

        async def inject_pending_during_manifest(*args, **kwargs):
            pinned.forwarder.pending_terminal_batch = {
                "events": [{"type": "lesson_failed"}]
            }
            return await get_manifest(*args, **kwargs)

        mac.get_lesson_manifest = inject_pending_during_manifest
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIs(result, pinned)
        self.assertIs(conn.lesson_runtime, pinned)
        self.assertEqual(pinned.terminal_replay_calls, 1)
        self.assertFalse(pinned.closed)
        self.assertEqual(conn.websocket.sent, [])
        self.assertIsNone(getattr(conn, "lesson_runtime_candidate", None))
        self.assertEqual(conn.lesson_start_status["code"], "TERMINAL_REPLAYED")

    async def test_changed_assignment_version_rechecks_old_terminal_after_candidate_preload_failure(self):
        import core.lesson.runtime as runtime_module

        conn = _RepublishConn()
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"],
            lesson_version=3,
            assignment_version=1,
            state="FAILED",
            terminal_replay_result=False,
        )
        conn.lesson_runtime = pinned
        pinned_session_id = pinned.session_id
        candidates = []
        entered = []

        async def inject_pending_during_preload(candidate):
            candidates.append(candidate)
            pinned.forwarder.pending_terminal_batch = {
                "events": [{"type": "lesson_failed"}]
            }
            return True

        async def enter_lesson_mode(*, reason):
            entered.append(reason)

        async def unexpected_start_protocol(_candidate, *, preloaded=False):
            raise AssertionError("terminal barrier must run before candidate protocol start")

        conn.enter_lesson_mode = enter_lesson_mode
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=2, state="ASSIGNED"),
            _build_manifest(),
        )
        try:
            with patch.object(
                runtime_module.LessonRuntime,
                "preload_only",
                new=inject_pending_during_preload,
            ), patch.object(
                runtime_module.LessonRuntime,
                "start_protocol",
                new=unexpected_start_protocol,
            ):
                result = await runtime_module.maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIs(result, pinned)
        self.assertIs(conn.lesson_runtime, pinned)
        self.assertEqual(pinned.session_id, pinned_session_id)
        self.assertEqual(pinned.terminal_replay_calls, 1)
        self.assertFalse(pinned.closed)
        self.assertEqual(entered, [])
        self.assertEqual(conn.websocket.sent, [])
        self.assertIsNone(getattr(conn, "lesson_runtime_candidate", None))
        self.assertTrue(candidates[0]._closed)
        self.assertEqual(conn.lesson_start_status["code"], "TERMINAL_REPLAY_PENDING")

    async def test_changed_assignment_version_rechecks_old_terminal_after_candidate_preload_success(self):
        import core.lesson.runtime as runtime_module

        conn = _RepublishConn()
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"],
            lesson_version=3,
            assignment_version=1,
            state="FAILED",
            terminal_replay_result=True,
        )
        conn.lesson_runtime = pinned
        candidates = []
        entered = []

        async def inject_pending_during_preload(candidate):
            candidates.append(candidate)
            pinned.forwarder.pending_terminal_batch = {
                "events": [{"type": "lesson_failed"}]
            }
            return True

        async def enter_lesson_mode(*, reason):
            entered.append(reason)

        async def unexpected_start_protocol(_candidate, *, preloaded=False):
            raise AssertionError("terminal barrier must run before candidate protocol start")

        conn.enter_lesson_mode = enter_lesson_mode
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=2, state="ASSIGNED"),
            _build_manifest(),
        )
        try:
            with patch.object(
                runtime_module.LessonRuntime,
                "preload_only",
                new=inject_pending_during_preload,
            ), patch.object(
                runtime_module.LessonRuntime,
                "start_protocol",
                new=unexpected_start_protocol,
            ):
                result = await runtime_module.maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIs(result, pinned)
        self.assertIs(conn.lesson_runtime, pinned)
        self.assertEqual(pinned.terminal_replay_calls, 1)
        self.assertFalse(pinned.closed)
        self.assertEqual(entered, [])
        self.assertEqual(conn.websocket.sent, [])
        self.assertIsNone(getattr(conn, "lesson_runtime_candidate", None))
        self.assertTrue(candidates[0]._closed)
        self.assertEqual(conn.lesson_start_status["code"], "TERMINAL_REPLAYED")

    async def test_changed_assignment_version_rechecks_old_terminal_after_enter_mode_failure(self):
        import core.lesson.runtime as runtime_module

        conn = _RepublishConn()
        conn.session_mode = "CONVERSATION"
        conn.audio_channel_owner = "CONVERSATION"
        mode_changes = []

        def set_session_mode(mode, *, reason=""):
            conn.session_mode = mode
            conn.audio_channel_owner = mode
            mode_changes.append((mode, reason))
            return mode

        conn._set_session_mode = set_session_mode
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"],
            lesson_version=3,
            assignment_version=1,
            state="FAILED",
            terminal_replay_result=False,
        )
        conn.lesson_runtime = pinned
        pinned_session_id = pinned.session_id
        candidates = []
        entered = []
        released = []

        async def preload(candidate):
            candidates.append(candidate)
            return True

        async def enter_lesson_mode(*, reason):
            entered.append(reason)
            conn._set_session_mode("LESSON", reason=reason)
            pinned.forwarder.pending_terminal_batch = {
                "events": [{"type": "lesson_failed"}]
            }

        async def release_lesson_mode(*, reason):
            released.append(reason)

        async def unexpected_start_protocol(_candidate, *, preloaded=False):
            raise AssertionError("terminal barrier must run before candidate protocol start")

        conn.enter_lesson_mode = enter_lesson_mode
        conn.release_lesson_mode = release_lesson_mode
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=2, state="ASSIGNED"),
            _build_manifest(),
        )
        try:
            with patch.object(
                runtime_module.LessonRuntime, "preload_only", new=preload
            ), patch.object(
                runtime_module.LessonRuntime,
                "start_protocol",
                new=unexpected_start_protocol,
            ):
                result = await runtime_module.maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIs(result, pinned)
        self.assertIs(conn.lesson_runtime, pinned)
        self.assertEqual(pinned.session_id, pinned_session_id)
        self.assertEqual(pinned.terminal_replay_calls, 1)
        self.assertFalse(pinned.closed)
        self.assertEqual(entered, ["lesson_start"])
        self.assertEqual(released, [])
        self.assertEqual(conn.session_mode, "CONVERSATION")
        self.assertEqual(conn.audio_channel_owner, "CONVERSATION")
        self.assertEqual(mode_changes[-1], ("CONVERSATION", "lesson_candidate_aborted"))
        self.assertEqual(conn.websocket.sent, [])
        self.assertIsNone(getattr(conn, "lesson_runtime_candidate", None))
        self.assertTrue(candidates[0]._closed)
        self.assertEqual(conn.lesson_start_status["code"], "TERMINAL_REPLAY_PENDING")

    async def test_changed_assignment_version_rechecks_old_terminal_after_enter_mode_success(self):
        import core.lesson.runtime as runtime_module

        conn = _RepublishConn()
        conn.session_mode = "LESSON"
        conn.audio_channel_owner = "LESSON"
        mode_changes = []

        def set_session_mode(mode, *, reason=""):
            conn.session_mode = mode
            conn.audio_channel_owner = mode
            mode_changes.append((mode, reason))
            return mode

        conn._set_session_mode = set_session_mode
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"],
            lesson_version=3,
            assignment_version=1,
            state="FAILED",
            terminal_replay_result=True,
        )
        conn.lesson_runtime = pinned
        candidates = []
        entered = []
        released = []

        async def preload(candidate):
            candidates.append(candidate)
            return True

        async def enter_lesson_mode(*, reason):
            entered.append(reason)
            conn._set_session_mode("LESSON", reason=reason)
            pinned.forwarder.pending_terminal_batch = {
                "events": [{"type": "lesson_failed"}]
            }

        async def release_lesson_mode(*, reason):
            released.append(reason)

        async def unexpected_start_protocol(_candidate, *, preloaded=False):
            raise AssertionError("terminal barrier must run before candidate protocol start")

        conn.enter_lesson_mode = enter_lesson_mode
        conn.release_lesson_mode = release_lesson_mode
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=2, state="ASSIGNED"),
            _build_manifest(),
        )
        try:
            with patch.object(
                runtime_module.LessonRuntime, "preload_only", new=preload
            ), patch.object(
                runtime_module.LessonRuntime,
                "start_protocol",
                new=unexpected_start_protocol,
            ):
                result = await runtime_module.maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIs(result, pinned)
        self.assertIs(conn.lesson_runtime, pinned)
        self.assertEqual(pinned.terminal_replay_calls, 1)
        self.assertFalse(pinned.closed)
        self.assertEqual(entered, ["lesson_start"])
        self.assertEqual(released, [])
        self.assertEqual(conn.session_mode, "LESSON")
        self.assertEqual(conn.audio_channel_owner, "LESSON")
        self.assertEqual(mode_changes[-1], ("LESSON", "lesson_candidate_aborted"))
        self.assertEqual(conn.websocket.sent, [])
        self.assertIsNone(getattr(conn, "lesson_runtime_candidate", None))
        self.assertTrue(candidates[0]._closed)
        self.assertEqual(conn.lesson_start_status["code"], "TERMINAL_REPLAYED")

    async def test_unchanged_completed_session_replays_pending_terminal_event_on_reconnect_once(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"],
            lesson_version=3,
            assignment_version=1,
            state="COMPLETED",
            terminal_pending=True,
        )
        conn.lesson_runtime = pinned

        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1), _build_manifest()
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIs(result, pinned)
        self.assertEqual(pinned.terminal_replay_calls, 1)
        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(self.manifest_calls, [])
        self.assertEqual(conn.lesson_start_status["code"], "TERMINAL_REPLAYED")

    async def test_fresh_reconnect_replays_dead_lettered_terminal_event_before_restart(self):
        import httpx
        import config.manage_api_client as mac
        import config.device_token_client as dtc
        from core.lesson.forwarder import LessonEventForwarder, get_terminal_replay_store
        from core.lesson.runtime import maybe_start_lesson_on_connect

        prep = FIX["frames"]["lesson_prepare"]
        terminal = {
            "assignmentId": prep["assignmentId"],
            "sessionId": "sess_reconnect_terminal",
            "events": [{"type": "lesson_completed", "completedAt": 1_700_000_000_000}],
        }

        async def _fail_post(_client, _base_url, _device_id, _batch, *, token=None):
            request = httpx.Request("POST", "http://backend.test/v1/devices/dev-republish/lesson-events")
            response = httpx.Response(500, request=request)
            raise httpx.HTTPStatusError("backend hiccup", request=request, response=response)

        old_forwarder = LessonEventForwarder(
            device_id="dev-republish",
            base_url="http://backend.test/v1",
            post_fn=_fail_post,
            retry_backoff_sec=0,
            max_reenqueue_attempts=0,
        )
        old_forwarder.enqueue(terminal)
        await old_forwarder._queue.join()
        await old_forwarder.aclose()

        replayed = []

        async def _resolve_device_identity(client, base_url, device_id, *, logger=None):
            return device_id, "device-token"

        async def _get_assignment(client, base_url, device_id, *, token=None):
            return self._assignment(lesson_version=3, assignment_version=1, state="ASSIGNED")

        async def _get_manifest(*_args, **_kwargs):
            raise AssertionError("terminal replay should prevent restarting the lesson")

        async def _post_lesson_event(_client, _base_url, _device_id, batch, *, token=None):
            replayed.append(batch)
            return {"accepted": 1, "duplicates": 0}

        saved = (
            dtc.resolve_device_identity,
            mac.get_current_assignment,
            mac.get_lesson_manifest,
            mac.post_lesson_event,
        )
        dtc.resolve_device_identity = _resolve_device_identity
        mac.get_current_assignment = _get_assignment
        mac.get_lesson_manifest = _get_manifest
        mac.post_lesson_event = _post_lesson_event
        conn = _RepublishConn()
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            (
                dtc.resolve_device_identity,
                mac.get_current_assignment,
                mac.get_lesson_manifest,
                mac.post_lesson_event,
            ) = saved

        await get_terminal_replay_store().clear("dev-republish", terminal)

        self.assertIsNone(result)
        self.assertEqual(replayed, [terminal])
        self.assertEqual(conn.lesson_start_status["code"], "TERMINAL_REPLAYED")

    async def test_fresh_reconnect_blocks_restart_when_terminal_replay_post_fails(self):
        import httpx
        import config.manage_api_client as mac
        import config.device_token_client as dtc
        from core.lesson.forwarder import LessonEventForwarder, get_terminal_replay_store
        from core.lesson.runtime import maybe_start_lesson_on_connect

        prep = FIX["frames"]["lesson_prepare"]
        terminal = {
            "assignmentId": prep["assignmentId"],
            "sessionId": "sess_reconnect_terminal_failed",
            "events": [{"type": "lesson_completed", "completedAt": 1_700_000_000_000}],
        }

        async def _fail_post(_client, _base_url, _device_id, _batch, *, token=None):
            request = httpx.Request("POST", "http://backend.test/v1/devices/dev-republish/lesson-events")
            response = httpx.Response(500, request=request)
            raise httpx.HTTPStatusError("backend hiccup", request=request, response=response)

        old_forwarder = LessonEventForwarder(
            device_id="dev-republish",
            base_url="http://backend.test/v1",
            post_fn=_fail_post,
            retry_backoff_sec=0,
            max_reenqueue_attempts=0,
        )
        old_forwarder.enqueue(terminal)
        await old_forwarder._queue.join()
        await old_forwarder.aclose()

        manifest_calls = []

        async def _resolve_device_identity(client, base_url, device_id, *, logger=None):
            return device_id, "device-token"

        async def _get_assignment(client, base_url, device_id, *, token=None):
            return self._assignment(lesson_version=3, assignment_version=1, state="ASSIGNED")

        async def _get_manifest(*_args, **_kwargs):
            manifest_calls.append(True)
            return _build_manifest(), '"lesson-3-espTft-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3"'

        saved = (
            dtc.resolve_device_identity,
            mac.get_current_assignment,
            mac.get_lesson_manifest,
            mac.post_lesson_event,
        )
        dtc.resolve_device_identity = _resolve_device_identity
        mac.get_current_assignment = _get_assignment
        mac.get_lesson_manifest = _get_manifest
        mac.post_lesson_event = _fail_post
        conn = _RepublishConn()
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            (
                dtc.resolve_device_identity,
                mac.get_current_assignment,
                mac.get_lesson_manifest,
                mac.post_lesson_event,
            ) = saved

        await get_terminal_replay_store().clear("dev-republish", terminal)

        self.assertIsNone(result)
        self.assertEqual(manifest_calls, [])
        self.assertEqual(conn.lesson_start_status["code"], "TERMINAL_REPLAY_PENDING")

    async def test_fresh_reconnect_replays_dead_lettered_child_inactivity_before_restart(self):
        import httpx
        import config.manage_api_client as mac
        import config.device_token_client as dtc
        from core.lesson.forwarder import LessonEventForwarder
        from core.lesson.runtime import maybe_start_lesson_on_connect

        prep = FIX["frames"]["lesson_prepare"]
        terminal = {
            "assignmentId": prep["assignmentId"],
            "sessionId": "sess_reconnect_child_inactive",
            "events": [{"type": "lesson_abandoned", "reason": "child_inactive"}],
        }

        async def _fail_post(_client, _base_url, _device_id, _batch, *, token=None):
            request = httpx.Request("POST", "http://backend.test/v1/devices/dev-republish/lesson-events")
            response = httpx.Response(500, request=request)
            raise httpx.HTTPStatusError("backend hiccup", request=request, response=response)

        old_forwarder = LessonEventForwarder(
            device_id="dev-republish",
            base_url="http://backend.test/v1",
            post_fn=_fail_post,
            retry_backoff_sec=0,
            max_reenqueue_attempts=0,
        )
        old_forwarder.enqueue(terminal)
        await old_forwarder._queue.join()
        await old_forwarder.aclose()

        replayed = []

        async def _resolve_device_identity(client, base_url, device_id, *, logger=None):
            return device_id, "device-token"

        async def _get_assignment(client, base_url, device_id, *, token=None):
            return self._assignment(lesson_version=3, assignment_version=1, state="ASSIGNED")

        async def _get_manifest(*_args, **_kwargs):
            raise AssertionError("child-inactivity terminal replay should prevent restarting the lesson")

        async def _post_lesson_event(_client, _base_url, _device_id, batch, *, token=None):
            replayed.append(batch)
            return {"accepted": 1, "duplicates": 0}

        saved = (
            dtc.resolve_device_identity,
            mac.get_current_assignment,
            mac.get_lesson_manifest,
            mac.post_lesson_event,
        )
        dtc.resolve_device_identity = _resolve_device_identity
        mac.get_current_assignment = _get_assignment
        mac.get_lesson_manifest = _get_manifest
        mac.post_lesson_event = _post_lesson_event
        try:
            result = await maybe_start_lesson_on_connect(_RepublishConn())
        finally:
            (
                dtc.resolve_device_identity,
                mac.get_current_assignment,
                mac.get_lesson_manifest,
                mac.post_lesson_event,
            ) = saved

        self.assertIsNone(result)
        self.assertEqual(replayed, [terminal])

    async def test_changed_version_evicts_and_repulls_without_reconnect(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"], lesson_version=3, assignment_version=1
        )
        conn.lesson_runtime = pinned

        # Author republished: lessonVersion 3 -> 4.
        undo = self._patch_backend(
            self._assignment(lesson_version=4, assignment_version=2, manifest_checksum="deadbeef"),
            {**_build_manifest(), "lessonVersion": 4},
            etag='"lesson-4-espTft-deadbeef"',
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        # Old exact cache remains rollback-safe; the old runtime closes only after
        # the fresh candidate emitted lesson_prepare for the new version.
        self.assertFalse(pinned.asset_cache.evicted)
        self.assertTrue(pinned.closed)
        self.assertIsNotNone(result)
        self.assertIsNot(result, pinned)
        self.assertIs(conn.lesson_runtime, result)
        self.assertEqual(result.lesson_version, 4)
        sent_types = [json.loads(p)["type"] for p in conn.websocket.sent]
        self.assertEqual(sent_types, ["lesson_prepare"])
        # The new version's cache dir is scoped by the new (version, checksum).
        self.assertIn("v4", result.asset_cache.cache_key)

    async def test_changed_manifest_checksum_evicts_and_repulls_even_when_versions_match(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"],
            lesson_version=3,
            assignment_version=1,
            manifest_checksum="9b1f7c2a",
        )
        conn.lesson_runtime = pinned

        # Author republished bytes under the same assignment/lesson version. The
        # ETag checksum is still part of the runtime identity, otherwise the robot
        # can keep serving an old SD/cache pack after backend content changed.
        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1, manifest_checksum="deadbeef"),
            _build_manifest(),
            etag='"lesson-3-espTft-deadbeef"',
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertFalse(pinned.asset_cache.evicted)
        self.assertTrue(pinned.closed)
        self.assertIsNotNone(result)
        self.assertIsNot(result, pinned)
        self.assertIs(conn.lesson_runtime, result)
        self.assertEqual(result.lesson_version, 3)
        self.assertEqual(result.assignment_version, 1)
        self.assertEqual(result.manifest_checksum, "deadbeef")
        sent_types = [json.loads(p)["type"] for p in conn.websocket.sent]
        self.assertEqual(sent_types, ["lesson_prepare"])
        self.assertEqual(result.asset_cache.cache_key, "w01-d01-barn-say-it/v3-deadbeef")

    async def test_changed_version_deferred_while_voice_busy(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn(busy=True)  # active voice turn
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"], lesson_version=3, assignment_version=1
        )
        conn.lesson_runtime = pinned

        undo = self._patch_backend(
            self._assignment(lesson_version=4, assignment_version=2, manifest_checksum="deadbeef"),
            {**_build_manifest(), "lessonVersion": 4},
            etag='"lesson-4-espTft-deadbeef"',
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        # Voice wins: the republish is DEFERRED — old session kept, nothing torn down,
        # no new frame put on the wire mid voice turn.
        self.assertIs(result, pinned)
        self.assertIs(conn.lesson_runtime, pinned)
        self.assertFalse(pinned.closed)
        self.assertFalse(pinned.asset_cache.evicted)
        self.assertEqual(conn.websocket.sent, [])

    async def test_changed_manifest_checksum_deferred_while_voice_busy(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn(busy=True)
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"],
            lesson_version=3,
            assignment_version=1,
            manifest_checksum="9b1f7c2a",
        )
        conn.lesson_runtime = pinned

        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1, manifest_checksum="deadbeef"),
            _build_manifest(),
            etag='"lesson-3-espTft-deadbeef"',
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        # Voice wins for checksum-only republishes too: no SD/cache teardown or
        # fresh lesson_prepare while the child/robot are mid conversation.
        self.assertIs(result, pinned)
        self.assertIs(conn.lesson_runtime, pinned)
        self.assertFalse(pinned.closed)
        self.assertFalse(pinned.asset_cache.evicted)
        self.assertEqual(conn.websocket.sent, [])

    async def test_pull_uses_server_api_url_fallback_and_fetches_esptft_manifest(self):
        import config.manage_api_client as mac
        import config.device_token_client as dtc
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn(api_base=None)
        conn.config["lesson"].pop("api_base", None)
        conn.config["server"] = {"api_url": "http://course-backend.test/v1"}
        calls = []

        async def _resolve_device_identity(client, base_url, device_id, *, logger=None):
            calls.append(("identity", base_url, device_id))
            return "backend-device-uuid", "device-token"

        async def _get_assignment(client, base_url, device_id, *, token=None):
            calls.append(("assignment", base_url, device_id, token))
            return self._assignment(lesson_version=3, assignment_version=1)

        async def _get_manifest(
            client,
            base_url,
            lesson_id,
            profile,
            *,
            token=None,
            renderer_capabilities=None,
            renderer_v2_enabled=False,
            lesson_version=None,
        ):
            calls.append(("manifest", base_url, lesson_id, profile, token, lesson_version))
            return _build_manifest(), '"lesson-3-espTft-9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3"'

        saved = (mac.get_current_assignment, mac.get_lesson_manifest, dtc.resolve_device_identity)
        mac.get_current_assignment = _get_assignment
        mac.get_lesson_manifest = _get_manifest
        dtc.resolve_device_identity = _resolve_device_identity
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            mac.get_current_assignment, mac.get_lesson_manifest, dtc.resolve_device_identity = saved

        self.assertIsNotNone(result)
        self.assertIn(
            ("assignment", "http://course-backend.test/v1", "backend-device-uuid", "device-token"),
            calls,
        )
        self.assertIn(
            (
                "manifest",
                "http://course-backend.test/v1",
                FIX["frames"]["lesson_prepare"]["lessonId"],
                "espTft",
                "device-token",
                3,
            ),
            calls,
        )

    async def test_start_lesson_pull_renders_background_step_and_completes(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        prep = FIX["frames"]["lesson_prepare"]
        assignment = {
            "assignmentId": prep["assignmentId"],
            "assignmentVersion": prep["body"]["assignmentVersion"],
            "lessonId": prep["lessonId"],
            "lessonVersion": prep["lessonVersion"],
            "manifestChecksum": _manifest_checksum(),
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        conn = _RepublishConn()
        undo = self._patch_backend(assignment, _build_manifest())
        try:
            with patch("core.lesson.runtime.uuid.uuid4", return_value=prep["sessionId"]):
                rt = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        sent = lambda: [json.loads(p) for p in conn.websocket.sent]
        self.assertIsNotNone(rt)
        self.assertEqual(conn.lesson_start_status["code"], "STARTED")
        self.assertEqual([f["type"] for f in sent()], ["lesson_prepare"])
        rt.asset_cache = _FakeAssetCache(ready=True)
        forwarder = _FakeForwarder()
        rt.forwarder = forwarder

        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        step = [f for f in sent() if f["type"] == "lesson_step"][-1]
        poster = step["body"]["scene"]["backgroundScene"]["poster"]
        self.assertTrue(poster["src"], "lesson_step must carry a background poster src")

        await rt.on_lesson_ack(_ack(3, 3, step_id=step["stepId"]))
        await rt.on_lesson_progress(
            _progress(
                4,
                {
                    "event": "step_completed",
                    "stepType": step["body"]["stepType"],
                    "result": "success",
                    "detail": {"utterance": "barn"},
                },
                step_id=step["stepId"],
            )
        )
        await rt.on_lesson_ack(_ack(4, 5))

        self.assertEqual(rt.state, "COMPLETED")
        self.assertEqual(
            [f["type"] for f in sent()],
            ["lesson_prepare", "lesson_start", "lesson_step", "lesson_stop"],
        )
        self.assertTrue(
            any(
                batch["events"] and batch["events"][0].get("type") == "lesson_completed"
                for batch in forwarder.batches
            ),
            "lesson_completed event must be forwarded after stop ack",
        )

    async def test_spoken_start_loads_assigned_course_and_plays_all_layered_prompt_steps(self):
        import asyncio
        import plugins_func.functions.start_lesson as start_lesson_module
        from plugins_func.register import Action

        prep = FIX["frames"]["lesson_prepare"]
        assignment = {
            "assignmentId": prep["assignmentId"],
            "assignmentVersion": prep["body"]["assignmentVersion"],
            "lessonId": prep["lessonId"],
            "lessonVersion": prep["lessonVersion"],
            "manifestChecksum": _manifest_checksum(),
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        manifest = _build_class_steps_manifest(
            [
                ("welcome", "greeting", "passive"),
                ("s4", "model", "interactive"),
                ("celebrate", "celebrate", "passive"),
            ]
        )
        prompts = [
            "Welcome to the barn story.",
            "Now say barn with TeeBot.",
            "Great talking. The barn is in the field.",
        ]
        for step, prompt in zip(manifest["steps"], prompts):
            step["prompt"] = prompt

        conn = _RepublishConn()
        conn.loop = asyncio.get_running_loop()
        conn.voice_provider = _RecordingLessonVoiceProvider()
        undo = self._patch_backend(assignment, manifest)
        try:
            with patch("core.lesson.runtime.uuid.uuid4", return_value=prep["sessionId"]):
                response = start_lesson_module.start_lesson(conn)
                rt = await conn.lesson_pull_task
        finally:
            undo()

        self.assertEqual(response.action, Action.RECORD)
        self.assertIsNotNone(rt)
        self.assertIs(conn.lesson_runtime, rt)
        self.assertEqual(conn.lesson_start_status["code"], "STARTED")
        self.assertEqual([json.loads(p)["type"] for p in conn.websocket.sent], ["lesson_prepare"])

        rt.asset_cache = _FakeAssetCache(ready=True)
        forwarder = _FakeForwarder()
        rt.forwarder = forwarder
        sent = lambda: [json.loads(p) for p in conn.websocket.sent]

        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        await rt.on_lesson_ack(_ack(3, 3, step_id="welcome"))
        await rt.on_lesson_ack(_ack(4, 4, step_id="s4"))
        await rt.on_lesson_progress(
            _progress(
                5,
                {
                    "event": "step_completed",
                    "stepType": "model",
                    "result": "success",
                    "detail": {"utterance": "barn"},
                },
                step_id="s4",
            )
        )
        await rt.on_lesson_ack(_ack(5, 6, step_id="celebrate"))
        await rt.on_lesson_ack(_ack(6, 7))

        step_frames = [f for f in sent() if f["type"] == "lesson_step"]
        self.assertEqual([f["stepId"] for f in step_frames], ["welcome", "s4", "celebrate"])
        self.assertEqual([f["body"]["completionClass"] for f in step_frames], ["passive", "interactive", "passive"])
        self.assertEqual(conn.voice_provider.prompts, prompts)

        for frame in step_frames:
            scene = frame["body"]["scene"]
            expected_step = next(step for step in manifest["steps"] if step["id"] == frame["stepId"])
            expected_overlay_asset = expected_step["scene"]["robotOverlay"]["asset"]
            self.assertEqual(set(scene), {"backgroundScene", "teachingObject", "robotOverlay"})
            self.assertTrue(scene["backgroundScene"]["poster"]["src"])
            self.assertTrue(scene["teachingObject"]["asset"]["src"])
            self.assertEqual(scene["robotOverlay"]["asset"]["key"], expected_overlay_asset["key"])
            self.assertEqual(scene["robotOverlay"]["asset"]["src"], expected_overlay_asset["src"])
            self.assertEqual(scene["robotOverlay"]["atlas"]["image"], "bright-teach.png")

        self.assertEqual(
            [f["type"] for f in sent()],
            ["lesson_prepare", "lesson_start", "lesson_step", "lesson_step", "lesson_step", "lesson_stop"],
        )
        self.assertEqual(rt.state, "COMPLETED")
        completed = [
            batch["events"][0]
            for batch in forwarder.batches
            if batch["events"] and batch["events"][0].get("type") == "lesson_completed"
        ]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["summary"]["stepsCompleted"], 3)

    async def test_spoken_start_plays_all_nine_barn_lesson_steps_with_layers_and_story_prompts(self):
        import asyncio
        import plugins_func.functions.start_lesson as start_lesson_module
        from plugins_func.register import Action

        prep = FIX["frames"]["lesson_prepare"]
        assignment = {
            "assignmentId": prep["assignmentId"],
            "assignmentVersion": prep["body"]["assignmentVersion"],
            "lessonId": prep["lessonId"],
            "lessonVersion": prep["lessonVersion"],
            "manifestChecksum": _manifest_checksum(),
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        seed_steps = SEED_LESSON["steps"]
        specs = [
            (step["id"], step["type"], step["completionClass"])
            for step in seed_steps
        ]
        self.assertEqual(specs, [
            ("s1", "greeting", "passive"),
            ("s2", "review", "passive"),
            ("s3", "focus", "passive"),
            ("s4", "model", "interactive"),
            ("s5", "listen", "interactive"),
            ("s6", "repeat", "interactive"),
            ("s7", "fillBlank", "interactive"),
            ("s8", "feedback", "passive"),
            ("s9", "celebrate", "passive"),
        ])
        prompts = [step["prompt"] for step in seed_steps]
        manifest = _build_class_steps_manifest(specs)
        seed_by_id = {step["id"]: step for step in seed_steps}
        for step in manifest["steps"]:
            seed_step = seed_by_id[step["id"]]
            step["prompt"] = seed_step["prompt"]
            step["subject"] = "barn"
            for key in ("helperText", "l1TransferHint", "choices"):
                if key in seed_step:
                    step[key] = copy.deepcopy(seed_step[key])

        conn = _RepublishConn()
        conn.loop = asyncio.get_running_loop()
        conn.voice_provider = _RecordingLessonVoiceProvider()
        undo = self._patch_backend(assignment, manifest)
        try:
            with patch("core.lesson.runtime.uuid.uuid4", return_value=prep["sessionId"]):
                response = start_lesson_module.start_lesson(conn)
                rt = await conn.lesson_pull_task
        finally:
            undo()

        self.assertEqual(response.action, Action.RECORD)
        self.assertIsNotNone(rt)
        self.assertEqual(conn.lesson_start_status["code"], "STARTED")
        rt.asset_cache = _FakeAssetCache(ready=True)
        forwarder = _FakeForwarder()
        rt.forwarder = forwarder

        sent = lambda: [json.loads(p) for p in conn.websocket.sent]
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        inbound_seq = 3
        interactive = {sid for sid, _stype, klass in specs if klass == "interactive"}
        for sid, stype, _klass in specs:
            frame = [f for f in sent() if f["type"] == "lesson_step"][-1]
            self.assertEqual(frame["stepId"], sid)
            self.assertEqual(frame["body"]["stepType"], stype)
            self.assertEqual(frame["body"]["audio"], {"via": "tts"})
            self.assertEqual(frame["body"]["timeoutSec"], FIX["frames"]["lesson_step"]["body"]["timeoutSec"])
            scene = frame["body"]["scene"]
            expected_step = next(step for step in manifest["steps"] if step["id"] == sid)
            expected_overlay_asset = expected_step["scene"]["robotOverlay"]["asset"]
            self.assertEqual(set(scene), {"backgroundScene", "teachingObject", "robotOverlay"})
            self.assertTrue(scene["backgroundScene"]["poster"]["src"])
            self.assertTrue(scene["teachingObject"]["asset"]["src"])
            self.assertEqual(scene["robotOverlay"]["asset"]["key"], expected_overlay_asset["key"])
            self.assertEqual(scene["robotOverlay"]["asset"]["src"], expected_overlay_asset["src"])
            self.assertEqual(scene["robotOverlay"]["atlas"]["image"], "bright-teach.png")

            await rt.on_lesson_ack(_ack(frame["sequence"], inbound_seq, step_id=sid))
            if sid in interactive:
                inbound_seq += 1
                await rt.on_lesson_progress(
                    _progress(
                        inbound_seq,
                        {
                            "event": "step_completed",
                            "stepType": stype,
                            "result": "success",
                            "detail": {"utterance": "barn"},
                        },
                        step_id=sid,
                    )
                )
            inbound_seq += 1

        stop = [f for f in sent() if f["type"] == "lesson_stop"][-1]
        await rt.on_lesson_ack(_ack(stop["sequence"], inbound_seq))

        step_frames = [f for f in sent() if f["type"] == "lesson_step"]
        self.assertEqual([f["stepId"] for f in step_frames], [sid for sid, _stype, _klass in specs])
        self.assertEqual(conn.voice_provider.prompts, prompts)
        for frame, prompt in zip(step_frames, prompts):
            self.assertEqual(frame["body"].get("prompt"), prompt)
            self.assertEqual(frame["body"].get("subject"), "barn")
            _assert_no_inline_media_payload(self, frame["body"], path=f"lesson_step[{frame['stepId']}].body")
            _assert_guided_speaking_practice_prompt(self, frame["body"].get("prompt") or "")
        self.assertEqual(
            next(f for f in step_frames if f["stepId"] == "s5")["body"].get("helperText"),
            seed_by_id["s5"]["helperText"],
        )
        self.assertEqual(
            next(f for f in step_frames if f["stepId"] == "s7")["body"].get("choices"),
            seed_by_id["s7"]["choices"],
        )
        for prompt in conn.voice_provider.prompts:
            _assert_guided_speaking_practice_prompt(self, prompt)
        self.assertEqual(
            [f["type"] for f in sent()],
            ["lesson_prepare", "lesson_start", *(["lesson_step"] * 9), "lesson_stop"],
        )
        self.assertEqual(rt.state, "COMPLETED")
        completed = [
            batch["events"][0]
            for batch in forwarder.batches
            if batch["events"] and batch["events"][0].get("type") == "lesson_completed"
        ]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["summary"]["stepsCompleted"], 9)

    async def test_voice_transcript_start_lesson_loads_backend_manifest_and_waits_on_guided_story_step(self):
        import asyncio

        from core.voice.session_provider.google_live import (
            GoogleLiveProvider,
            LESSON_LIVE_TEXT_INSTRUCTION,
        )

        prep = FIX["frames"]["lesson_prepare"]
        assignment = {
            "assignmentId": prep["assignmentId"],
            "assignmentVersion": prep["body"]["assignmentVersion"],
            "lessonId": prep["lessonId"],
            "lessonVersion": prep["lessonVersion"],
            "manifestChecksum": _manifest_checksum(),
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        manifest = _build_full_seed_story_manifest()
        manifest["steps"][0].update(
            {
                "prompt": "Look at the picture on the screen.",
                "story": {"beatId": "intro", "text": "TeeBot and the child visit a barn."},
                "storyText": "TeeBot and the child visit a barn.",
                "storyBeat": {"ask": "What animal do you see?", "waitForChild": True},
                "vocab": {"word": "barn", "partOfSpeech": "noun"},
                "completionClass": "interactive",
            }
        )

        conn = _RepublishConn()
        conn.loop = asyncio.get_running_loop()
        conn.func_handler = _RegistryStartLessonHandler()
        provider = GoogleLiveProvider(conn)
        provider._client = _LiveClientStub(provider)
        opened_windows = []
        open_child_response_window = provider.open_lesson_child_response_window

        async def _record_child_response_window():
            opened_windows.append(True)
            return await open_child_response_window()

        provider.open_lesson_child_response_window = _record_child_response_window
        conn.voice_provider = provider
        undo = self._patch_backend(assignment, manifest)
        try:
            with patch("core.lesson.runtime.uuid.uuid4", return_value=prep["sessionId"]):
                handled = await provider._on_user_transcript("bắt đầu khoá học")
                rt = await conn.lesson_pull_task
        finally:
            undo()

        self.assertTrue(handled)
        self.assertEqual(conn.func_handler.calls, [{"name": "start_lesson", "arguments": {}}])
        self.assertIs(conn.lesson_runtime, rt)
        self.assertIsNotNone(rt)
        self.assertEqual(conn.lesson_start_status["code"], "STARTED")
        self.assertEqual([json.loads(p)["type"] for p in conn.websocket.sent], ["lesson_prepare"])
        # No competing "Bắt đầu bài học nhé" ack — step prompts own the spoken intro.
        self.assertEqual(provider._client.sent_texts, [])

        rt.asset_cache = _FakeAssetCache(ready=True)
        forwarder = _FakeForwarder()
        rt.forwarder = forwarder
        sent = lambda: [json.loads(p) for p in conn.websocket.sent]

        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        inbound_seq = 3
        for expected_step in manifest["steps"]:
            frame = [f for f in sent() if f["type"] == "lesson_step"][-1]
            sid = expected_step["id"]
            stype = expected_step["type"]
            klass = expected_step["completionClass"]
            self.assertEqual(frame["stepId"], sid)
            self.assertEqual(frame["body"]["stepType"], stype)
            self.assertEqual(frame["body"]["completionClass"], klass)
            self.assertEqual(frame["body"]["profile"], "espTft")
            scene = frame["body"]["scene"]
            self.assertEqual(set(scene), {"backgroundScene", "teachingObject", "robotOverlay"})
            self.assertTrue(scene["backgroundScene"]["poster"]["src"])
            self.assertTrue(scene["teachingObject"]["asset"]["src"])
            self.assertEqual(
                scene["robotOverlay"]["asset"]["key"],
                expected_step["scene"]["robotOverlay"]["asset"]["key"],
            )
            self.assertEqual(scene["robotOverlay"]["asset"]["src"], expected_step["scene"]["robotOverlay"]["asset"]["src"])

            await rt.on_lesson_ack(_ack(frame["sequence"], inbound_seq, step_id=sid))
            if klass == "interactive":
                await rt.on_child_response("barn", source="voice_transcript")
            inbound_seq += 1

        stop = [f for f in sent() if f["type"] == "lesson_stop"][-1]
        await rt.on_lesson_ack(_ack(stop["sequence"], inbound_seq))

        self.assertEqual(
            [f["stepId"] for f in sent() if f["type"] == "lesson_step"],
            [step["id"] for step in manifest["steps"]],
        )
        for frame in [f for f in sent() if f["type"] == "lesson_step"]:
            _assert_no_inline_media_payload(self, frame["body"], path=f"lesson_step[{frame['stepId']}].body")
        first_step_body = next(f for f in sent() if f.get("stepId") == "s1")["body"]
        self.assertEqual(first_step_body["prompt"], "Look at the picture on the screen.")
        self.assertEqual(first_step_body["story"], {"beatId": "intro", "text": "TeeBot and the child visit a barn."})
        self.assertEqual(first_step_body["storyText"], "TeeBot and the child visit a barn.")
        self.assertEqual(first_step_body["storyBeat"], {"ask": "What animal do you see?", "waitForChild": True})
        self.assertEqual(first_step_body["vocab"], {"word": "barn", "partOfSpeech": "noun"})
        # No schedule-ack TTS; first Live text is the first spoken step prompt.
        lesson_prompt_texts = list(provider._client.sent_texts)
        self.assertGreaterEqual(len(lesson_prompt_texts), 9)
        self.assertEqual(
            lesson_prompt_texts[0],
            LESSON_LIVE_TEXT_INSTRUCTION + "What animal do you see?",
        )
        self.assertNotIn("Look at the picture on the screen.", lesson_prompt_texts[0])
        self.assertNotIn(
            LESSON_LIVE_TEXT_INSTRUCTION + "Bắt đầu bài học nhé.",
            lesson_prompt_texts,
        )
        for prompt in lesson_prompt_texts:
            _assert_guided_speaking_practice_prompt(self, prompt)
        self.assertEqual(len(opened_windows), 5)
        self.assertEqual(rt.state, "COMPLETED")
        self.assertEqual(rt._steps_completed, 9)
        step_completed_events = [
            batch["events"][0]
            for batch in forwarder.batches
            if batch["events"] and batch["events"][0].get("type") == "step_completed"
        ]
        self.assertEqual(
            [event["stepId"] for event in step_completed_events],
            [step["id"] for step in manifest["steps"]],
        )
        self.assertEqual([event["sequence"] for event in step_completed_events], list(range(-3, -12, -1)))
        self.assertTrue(all(event["result"] == "success" for event in step_completed_events))
        self.assertEqual(
            [
                event["stepId"]
                for event in step_completed_events
                if event.get("detail", {}).get("source") == "passive_runtime"
            ],
            ["s2", "s3", "s8", "s9"],
        )
        for event in step_completed_events:
            _assert_no_pronunciation_scoring_payload(self, event, path=f"step_completed[{event['stepId']}]")
        completed = [
            batch["events"][0]
            for batch in forwarder.batches
            if batch["events"] and batch["events"][0].get("type") == "lesson_completed"
        ]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["summary"]["stepsCompleted"], 9)

    async def test_natural_vietnamese_voice_variant_loads_backend_manifest_and_completes_story(self):
        import asyncio

        from core.voice.session_provider.google_live import GoogleLiveProvider

        prep = FIX["frames"]["lesson_prepare"]
        assignment = {
            "assignmentId": prep["assignmentId"],
            "assignmentVersion": prep["body"]["assignmentVersion"],
            "lessonId": prep["lessonId"],
            "lessonVersion": prep["lessonVersion"],
            "manifestChecksum": _manifest_checksum(),
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        manifest = _build_full_seed_story_manifest()

        conn = _RepublishConn()
        conn.loop = asyncio.get_running_loop()
        conn.func_handler = _RegistryStartLessonHandler()
        provider = GoogleLiveProvider(conn)
        provider._client = _LiveClientStub(provider)
        opened_windows = []
        open_child_response_window = provider.open_lesson_child_response_window

        async def _record_child_response_window():
            opened_windows.append(True)
            return await open_child_response_window()

        provider.open_lesson_child_response_window = _record_child_response_window
        conn.voice_provider = provider
        undo = self._patch_backend(assignment, manifest)
        try:
            with patch("core.lesson.runtime.uuid.uuid4", return_value=prep["sessionId"]):
                handled = await provider._on_user_transcript("học bài đi")
                rt = await conn.lesson_pull_task
        finally:
            undo()

        self.assertTrue(handled)
        self.assertEqual(conn.func_handler.calls, [{"name": "start_lesson", "arguments": {}}])
        self.assertIs(conn.lesson_runtime, rt)
        self.assertEqual(conn.lesson_start_status["code"], "STARTED")

        rt.asset_cache = _FakeAssetCache(ready=True)
        forwarder = _FakeForwarder()
        rt.forwarder = forwarder
        sent = lambda: [json.loads(p) for p in conn.websocket.sent]

        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))

        inbound_seq = 3
        for expected_step in manifest["steps"]:
            frame = [f for f in sent() if f["type"] == "lesson_step"][-1]
            sid = expected_step["id"]
            self.assertEqual(frame["stepId"], sid)
            self.assertEqual(set(frame["body"]["scene"]), {"backgroundScene", "teachingObject", "robotOverlay"})
            self.assertTrue(frame["body"]["scene"]["backgroundScene"]["poster"].get("src"))
            self.assertTrue(frame["body"]["scene"]["teachingObject"]["asset"].get("src"))
            self.assertTrue(frame["body"]["scene"]["robotOverlay"]["asset"].get("src"))
            _assert_no_inline_media_payload(self, frame["body"], path=f"lesson_step[{sid}].body")

            await rt.on_lesson_ack(_ack(frame["sequence"], inbound_seq, step_id=sid))
            if expected_step["completionClass"] == "interactive":
                await rt.on_child_response("barn", source="voice_transcript")
            inbound_seq += 1

        stop = [f for f in sent() if f["type"] == "lesson_stop"][-1]
        await rt.on_lesson_ack(_ack(stop["sequence"], inbound_seq))

        self.assertEqual([f["stepId"] for f in sent() if f["type"] == "lesson_step"], [step["id"] for step in manifest["steps"]])
        self.assertEqual(len(opened_windows), 4)
        self.assertEqual(rt.state, "COMPLETED")
        self.assertEqual(rt._steps_completed, 9)
        completed = [
            batch["events"][0]
            for batch in forwarder.batches
            if batch["events"] and batch["events"][0].get("type") == "lesson_completed"
        ]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["summary"]["stepsCompleted"], 9)

    async def test_voice_start_lesson_with_sd_pack_loads_backend_manifest_and_sends_local_layer_paths(self):
        import asyncio

        from core.voice.session_provider.google_live import GoogleLiveProvider

        class _LayeredSdAssetCache(_FakeAssetCache):
            def __init__(self, *args, cache_key, **kwargs):
                super().__init__(*args, **kwargs)
                self.cache_key = cache_key

            def asset_pack_manifest(self, *, assignment_version, lesson_id, lesson_version, manifest_checksum):
                pack = super().asset_pack_manifest(
                    assignment_version=assignment_version,
                    lesson_id=lesson_id,
                    lesson_version=lesson_version,
                    manifest_checksum=manifest_checksum,
                )
                pack["assets"] = [
                    {
                        "key": "backgroundScene.poster",
                        "path": "barn-round-field-poster.jpg",
                        "sha256": "2e3b77c7ee3c07381e46a6c9f2412c0d39ff14f08a569f42336299baa0502990",
                        "mediaType": "image/jpeg",
                        "critical": True,
                        "localPath": self._local_urls["barn-round-field-poster.jpg"],
                        "state": "READY",
                        "checksumOk": True,
                    },
                    {
                        "key": "teachingObject.barn",
                        "path": "barn.png",
                        "sha256": "eac30a7ddf3f14df79f27c3eb39f2114f3a780d5670bb11ef62446f5fa5dcbb9",
                        "mediaType": "image/png",
                        "critical": True,
                        "localPath": self._local_urls["barn.png"],
                        "state": "READY",
                        "checksumOk": True,
                    },
                    {
                        "key": "robotOverlay.teach",
                        "path": "bright-teach.png",
                        "sha256": "40f9c095b11a67c023f62847f498cc557e7fcef45762d41787dafffd96a60b34",
                        "mediaType": "image/png",
                        "critical": False,
                        "localPath": self._local_urls["bright-teach.png"],
                        "state": "READY",
                        "checksumOk": True,
                    },
                    {
                        "key": "robotOverlay.listening",
                        "path": "bright-listening.png",
                        "sha256": "6f4d2c8f9b0e1a234567890abcdef1234567890abcdef1234567890abcdef12",
                        "mediaType": "image/png",
                        "critical": False,
                        "localPath": self._local_urls["bright-listening.png"],
                        "state": "READY",
                        "checksumOk": True,
                    },
                    {
                        "key": "robotOverlay.thinking",
                        "path": "bright-thinking.png",
                        "sha256": "7a5e3d9c8b1f0a234567890abcdef1234567890abcdef1234567890abcdef34",
                        "mediaType": "image/png",
                        "critical": False,
                        "localPath": self._local_urls["bright-thinking.png"],
                        "state": "READY",
                        "checksumOk": True,
                    },
                    {
                        "key": "robotOverlay.celebrate",
                        "path": "bright-celebrate.png",
                        "sha256": "8b6f4e0d9c2a1b34567890abcdef1234567890abcdef1234567890abcdef56",
                        "mediaType": "image/png",
                        "critical": False,
                        "localPath": self._local_urls["bright-celebrate.png"],
                        "state": "READY",
                        "checksumOk": True,
                    },
                ]
                return pack

        prep = FIX["frames"]["lesson_prepare"]
        manifest_checksum = _manifest_checksum()
        cache_key = f"w01-d01-barn-say-it/v3-{manifest_checksum}"
        assignment = {
            "assignmentId": prep["assignmentId"],
            "assignmentVersion": prep["body"]["assignmentVersion"],
            "lessonId": prep["lessonId"],
            "lessonVersion": prep["lessonVersion"],
            "manifestChecksum": manifest_checksum,
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        manifest = _build_full_seed_story_manifest()
        for step in manifest["steps"]:
            overlay = step["scene"]["robotOverlay"]
            overlay["atlas"] = {"image": overlay["asset"]["src"]}

        local_urls = {
            "barn-round-field-poster.jpg": f"sd://sdcard/tbot/lesson-assets/{cache_key}/backgroundScene.poster",
            "barn.png": f"sd://sdcard/tbot/lesson-assets/{cache_key}/teachingObject.barn",
            "bright-teach.png": f"sd://sdcard/tbot/lesson-assets/{cache_key}/robotOverlay.teach",
            "bright-listening.png": f"sd://sdcard/tbot/lesson-assets/{cache_key}/robotOverlay.listening",
            "bright-thinking.png": f"sd://sdcard/tbot/lesson-assets/{cache_key}/robotOverlay.thinking",
            "bright-celebrate.png": f"sd://sdcard/tbot/lesson-assets/{cache_key}/robotOverlay.celebrate",
        }
        local_urls.update(
            {
                f"assets/robot/poses/{name}": local_urls[name]
                for name in (
                    "bright-teach.png",
                    "bright-listening.png",
                    "bright-thinking.png",
                    "bright-celebrate.png",
                )
            }
        )

        conn = _RepublishConn()
        conn.loop = asyncio.get_running_loop()
        conn.config["lesson"]["asset_delivery_mode"] = "sd_pack"
        conn.func_handler = _RegistryStartLessonHandler()
        provider = GoogleLiveProvider(conn)
        provider._client = _LiveClientStub(provider)
        opened_windows = []
        open_child_response_window = provider.open_lesson_child_response_window

        async def _record_child_response_window():
            opened_windows.append(True)
            return await open_child_response_window()

        provider.open_lesson_child_response_window = _record_child_response_window
        conn.voice_provider = provider
        import core.lesson.asset_cache as asset_cache_mod

        saved_asset_cache = asset_cache_mod.AssetCache
        asset_cache_mod.AssetCache = lambda **_kwargs: _LayeredSdAssetCache(
            ready=True,
            local_urls=local_urls,
            cache_key=cache_key,
        )
        undo = self._patch_backend(assignment, manifest)
        try:
            with patch("core.lesson.runtime.uuid.uuid4", return_value=prep["sessionId"]):
                handled = await provider._on_user_transcript("bắt đầu bài học")
                rt = await conn.lesson_pull_task
        finally:
            undo()
            asset_cache_mod.AssetCache = saved_asset_cache

        self.assertTrue(handled)
        self.assertIsNotNone(rt)
        rt.forwarder = _FakeForwarder()
        sent = lambda: [json.loads(p) for p in conn.websocket.sent]

        prepare = sent()[-1]
        self.assertEqual(prepare["type"], "lesson_prepare")
        self.assertEqual(prepare["body"]["assetPack"]["cacheKey"], cache_key)
        self.assertIn(manifest_checksum, prepare["body"]["assetPack"]["cacheKey"])
        self.assertEqual(
            prepare["body"]["assetPack"]["localRoot"],
            f"sd://sdcard/tbot/lesson-assets/{cache_key}",
        )
        self.assertEqual(
            {asset["key"] for asset in prepare["body"]["assetPack"]["assets"]},
            {
                "backgroundScene.poster",
                "teachingObject.barn",
                "robotOverlay.teach",
                "robotOverlay.listening",
                "robotOverlay.thinking",
                "robotOverlay.celebrate",
            },
        )

        await rt.on_lesson_ack(
            _ack(
                prepare["sequence"],
                1,
                extra={
                    "acks": prepare["sequence"],
                    "rendered": True,
                    "degraded": False,
                    "assetPack": {"ready": True, "cacheKey": cache_key},
                },
            )
        )
        await rt.on_lesson_ack(_ack(2, 2))

        expected_steps = [step["id"] for step in manifest["steps"]]
        interactive_steps = {step["id"] for step in manifest["steps"] if step["completionClass"] == "interactive"}
        inbound_seq = 3
        for step_id in expected_steps:
            expected_step = next(step for step in manifest["steps"] if step["id"] == step_id)
            frame = sent()[-1]
            self.assertEqual(frame["stepId"], step_id)
            self.assertEqual(frame["body"]["completionClass"], "interactive" if step_id in interactive_steps else "passive")
            self.assertEqual(frame["body"]["profile"], "espTft")
            scene = frame["body"]["scene"]
            self.assertEqual(scene["backgroundScene"]["poster"]["src"], local_urls["barn-round-field-poster.jpg"])
            self.assertEqual(scene["teachingObject"]["asset"]["src"], local_urls["barn.png"])
            pose_asset = expected_step["scene"]["robotOverlay"]["asset"]
            self.assertEqual(scene["robotOverlay"]["asset"]["key"], pose_asset["key"])
            self.assertEqual(scene["robotOverlay"]["asset"]["src"], local_urls[pose_asset["src"].rsplit("/", 1)[-1]])
            self.assertEqual(scene["robotOverlay"]["atlas"]["image"], local_urls[pose_asset["src"]])
            _assert_no_inline_media_payload(self, frame["body"], path=f"voice_sd_pack.{frame['stepId']}")

            await rt.on_lesson_ack(_ack(frame["sequence"], inbound_seq, step_id=step_id))
            if step_id in interactive_steps:
                self.assertTrue(await rt.on_child_response("barn", source="voice_transcript"))
            inbound_seq += 1

        stop = sent()[-1]
        self.assertEqual(stop["type"], "lesson_stop")
        await rt.on_lesson_ack(_ack(stop["sequence"], inbound_seq))
        self.assertEqual([f["stepId"] for f in sent() if f["type"] == "lesson_step"], expected_steps)
        self.assertEqual(len(opened_windows), len(interactive_steps))
        self.assertEqual(rt.state, "COMPLETED")
        self.assertEqual(rt._steps_completed, 9)

class ActivityLeaseLessonMutationTest(unittest.IsolatedAsyncioTestCase):
    async def test_lesson_start_refuses_exclusive_lease_inside_shared_lock(self):
        from core.lesson import runtime as runtime_module

        lock = asyncio.Lock()
        await lock.acquire()
        conn = SimpleNamespace(
            _lesson_pull_lock=lock,
            activity_leases=ActivityLeaseCoordinator(asyncio.get_running_loop()),
        )
        async def forbidden(_conn):
            self.fail("lesson mutation must not run during exclusive eviction")

        task = None
        with patch(
            "core.providers.tools.product_toolset.lesson_runtime_enabled",
            return_value=True,
        ), patch.object(runtime_module, "_maybe_start_lesson_on_connect_impl", forbidden):
            task = asyncio.create_task(runtime_module.maybe_start_lesson_on_connect(conn))
            try:
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                lease = conn.activity_leases.try_acquire_eviction(
                    ActivityOperation.LESSON_CACHE_EVICT,
                    busy_probe=lambda: False,
                )
                self.assertIsNotNone(lease)
                lease.complete_exclusive(ExclusiveDisposition.AMBIGUOUS)
                lock.release()
                result = await task
            finally:
                if lock.locked():
                    lock.release()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        self.assertIsNone(result)
        self.assertEqual(conn.lesson_start_status["code"], "CACHE_EVICTION_RESERVED")


if __name__ == "__main__":
    unittest.main()
