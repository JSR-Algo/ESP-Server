from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import google_live_robot_soak, tvideo_farm_preview

SERVER_ROOT = Path(__file__).resolve().parents[1]
PREVIEW_SCRIPT = SERVER_ROOT / "scripts" / "tvideo_farm_preview.py"


def test_preview_check_is_local_fixture_only_and_keeps_rollout_disabled() -> None:
    completed = subprocess.run(
        [sys.executable, str(PREVIEW_SCRIPT), "--check"],
        cwd=SERVER_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "PREVIEW_ONLY_READY"
    assert payload["url"] == "http://127.0.0.1:8090/tvideo-farm-preview.html"
    assert payload["fixtureLessonId"] == "journey-v4"
    assert payload["publishReady"] is False
    assert payload["rolloutChanged"] is False
    assert payload["source"] == "committed-local-fixture"
    assert payload["fixtureAssets"] == [
        {
            "url": "/tvideo-demo/assets/scenes/deep-barn-farm-background-6s.mp4",
            "sha256": "53d3ac70d166ba83029d5d122493dc48304d2caf933e03c09b0907152531f5f1",
        },
        {
            "url": "/tvideo-demo/assets/objects/barn.png",
            "sha256": "eac30a7ddf3f14df79f27c3eb39f2114f3a780d5670bb11ef62446f5fa5dcbb9",
        },
        {
            "url": "/tvideo-demo/assets/objects/hay.png",
            "sha256": "f74c34f44459495062091d4d91bb8fef0a2501ff3fab78beddaf0f70f0bf2e11",
        },
        {
            "url": "/tvideo-demo/assets/robot-alive/flight/flight-in.webm",
            "sha256": "52091cdbfe5712e4afed800ce35e4a743e923c65b4034dd4c0a3c85d0f6c345c",
        },
        {
            "url": "/tvideo-demo/assets/robot-alive/flight/walk-toward.webm",
            "sha256": "46a3058664949eaebf057f99309ee317bd36257178c187e83329fdbaf030cf5a",
        },
        {
            "url": "/tvideo-demo/assets/robot-alive/flight/greet-loop.webm",
            "sha256": "249a86cea2456b41ee5344445b0b83777a0ee7217ce435e7e9294ed7f39b12e0",
        },
        {
            "url": "/tvideo-demo/assets/robot-alive/flight/celebrate.webm",
            "sha256": "708d982dc9f2257a170ad1811c1afc230b5249a4139f975133a703ebdf39e105",
        },
    ]


def test_preview_check_refuses_digest_mismatch_before_ready(monkeypatch) -> None:
    first_asset = next(iter(tvideo_farm_preview.REQUIRED_MEDIA_SHA256))
    monkeypatch.setitem(
        tvideo_farm_preview.REQUIRED_MEDIA_SHA256,
        first_asset,
        "0" * 64,
    )

    with pytest.raises(ValueError, match="digest mismatch"):
        tvideo_farm_preview._check_payload("127.0.0.1", 8090)


def test_preview_helper_resolves_an_executable_npm_runtime() -> None:
    npm = tvideo_farm_preview._resolve_npm()

    assert npm.is_file()
    assert os.access(npm, os.X_OK)
    env = tvideo_farm_preview._serve_environment(npm)
    assert env["PATH"].split(os.pathsep)[0] == str(npm.parent)


def test_preview_entry_loads_the_side_effect_fixture_without_tree_shaking() -> None:
    source = tvideo_farm_preview.PREVIEW_ENTRY.read_text(encoding="utf-8")

    assert "import('./lesson-builder-main')" in source
    assert source.startswith("(async () => {")
    assert "10000000-0000-4000-8000-000000000001" in source
    assert "30000000-0000-4000-8000-000000000001" in source
    assert "30000000-0000-4000-8000-000000000002" in source
    assert "20000000-0000-4000-8000-000000000004" in source


def test_preview_fixture_uses_committed_media_instead_of_missing_placeholders() -> None:
    fixture = (tvideo_farm_preview.MANAGER_ROOT / "tests" / "browser" / "lesson-builder-main.js").read_text(
        encoding="utf-8"
    )

    assert "/fixtures/" not in fixture
    assert "data:image/png" not in fixture
    assert "/tvideo-demo/assets/objects/hay.png" in fixture


def test_tvideo_farm_missing_credentials_is_a_typed_privacy_safe_skip(
    monkeypatch,
) -> None:
    for name in google_live_robot_soak.GOOGLE_LIVE_CREDENTIAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    args = SimpleNamespace(
        scenario="tvideo-farm",
        audio_source="synthetic",
        event_timeout_sec=180.0,
        report=None,
    )

    report = google_live_robot_soak._credential_gated_tvideo_farm_report(args)

    assert report == {
        "scenario": "tvideo-farm",
        "status": "SKIP_GOOGLE_LIVE_CREDENTIALS",
        "audio_source": "synthetic",
        "duration_sec": 180.0,
        "raw_audio_persisted": False,
        "transcript_persisted": False,
        "exit_code": 0,
    }
    serialized = json.dumps(report)
    assert "prompt" not in serialized.lower()
    assert "utterance" not in serialized.lower()


@pytest.mark.parametrize("env_name", google_live_robot_soak.GOOGLE_LIVE_CREDENTIAL_ENV_NAMES)
def test_tvideo_farm_credential_gate_accepts_every_production_live_alias(monkeypatch, env_name) -> None:
    for name in google_live_robot_soak.GOOGLE_LIVE_CREDENTIAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("TBOT_GEMINI_TTS_API_KEY", raising=False)
    monkeypatch.setenv(env_name, "live-key")

    assert (
        google_live_robot_soak._credential_gated_tvideo_farm_report(
            SimpleNamespace(
                scenario="tvideo-farm",
                audio_source="synthetic",
                event_timeout_sec=1.0,
            )
        )
        is None
    )


def test_tvideo_farm_credential_gate_rejects_tts_only_alias(monkeypatch) -> None:
    for name in google_live_robot_soak.GOOGLE_LIVE_CREDENTIAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TBOT_GEMINI_TTS_API_KEY", "tts-only")

    report = google_live_robot_soak._credential_gated_tvideo_farm_report(
        SimpleNamespace(
            scenario="tvideo-farm",
            audio_source="synthetic",
            event_timeout_sec=1.0,
        )
    )

    assert report["status"] == "SKIP_GOOGLE_LIVE_CREDENTIALS"


def test_tvideo_farm_credential_gate_allows_manager_provisioned_server(monkeypatch) -> None:
    for name in google_live_robot_soak.GOOGLE_LIVE_CREDENTIAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    report = google_live_robot_soak._credential_gated_tvideo_farm_report(
        SimpleNamespace(
            scenario="tvideo-farm",
            audio_source="synthetic",
            event_timeout_sec=1.0,
            server_has_google_live_credentials=True,
        )
    )

    assert report is None


def test_tvideo_farm_accepts_only_synthetic_or_adult_audio_metadata() -> None:
    parser = google_live_robot_soak._build_argument_parser()

    parsed = parser.parse_args(
        [
            "--scenario",
            "tvideo-farm",
            "--audio-source",
            "synthetic",
            "--duration-sec",
            "180",
        ]
    )
    assert parsed.scenario == "tvideo-farm"
    assert parsed.audio_source == "synthetic"

    rejected = subprocess.run(
        [
            sys.executable,
            str(SERVER_ROOT / "scripts" / "google_live_robot_soak.py"),
            "--scenario",
            "tvideo-farm",
            "--audio-source",
            "child",
            "--duration-sec",
            "180",
        ],
        cwd=SERVER_ROOT,
        env={**os.environ, "GOOGLE_API_KEY": ""},
        text=True,
        capture_output=True,
        check=False,
    )
    assert rejected.returncode == 2
    assert "invalid choice" in rejected.stderr


def test_tvideo_farm_scenario_uses_the_bounded_live_runner(monkeypatch) -> None:
    expected = {
        "scenario": "tvideo-farm",
        "status": "PASS",
        "turns": [],
        "exit_code": 0,
    }
    calls = []

    async def fake_runner(args):
        calls.append(args.scenario)
        return expected

    monkeypatch.setattr(google_live_robot_soak, "_run_tvideo_farm_scenario", fake_runner)
    args = SimpleNamespace(scenario="tvideo-farm", dry_run=False)

    result = asyncio.run(google_live_robot_soak.run_soak(args))

    assert result is expected
    assert calls == ["tvideo-farm"]


def test_tvideo_farm_explicit_dry_run_uses_local_fake_server_without_credentials(
    monkeypatch,
) -> None:
    for name in google_live_robot_soak.GOOGLE_LIVE_CREDENTIAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    result = asyncio.run(google_live_robot_soak.run_soak(_tvideo_farm_args(dry_run=True)))

    assert result["status"] == "FAKE_PASS"
    assert result["dry_run"] is True
    assert result["binary_chunks_sent"] > 0
    assert result["raw_audio_persisted"] is False
    assert result["transcript_persisted"] is False
    serialized = json.dumps(result)
    assert "prompt" not in serialized.lower()
    assert "utterance" not in serialized.lower()
    assert "/fixtures/" not in serialized


def _tvideo_farm_frame(
    frame_type: str,
    cue_id: str,
    sequence: int,
    *,
    command: str = "start",
    effect: str | None = None,
    protocol_version: str = "teebot-lesson-renderer.v4",
    step_id: str | None = None,
    playback_mode: str | None = None,
) -> str:
    step_key = cue_id.split("-", 1)[0]
    resolved_effect = effect or cue_id.removeprefix(f"{step_key}-")
    if cue_id == "barn-to-hay-word-transition" and effect is None:
        resolved_effect = "word-transition"
    resolved_playback = playback_mode or (
        "loop" if resolved_effect in {"listen", "thinking"} else "once"
    )
    return json.dumps(
        {
            "type": frame_type,
            "protocolVersion": protocol_version,
            "assignmentId": "assignment-1",
            "sessionId": "lesson-session-1",
            "lessonId": "farm-english",
            "lessonVersion": 4,
            "sequence": sequence,
            "stepId": step_id or step_key,
            "body": {
                "command": command,
                "cueId": cue_id,
                "effect": resolved_effect,
                "stepKey": step_key,
                "playbackMode": resolved_playback,
                "commandSequenceId": sequence,
                "cinematicPhase": {
                    "command": command,
                    "cueId": cue_id,
                    "stepKey": step_key,
                    "effect": resolved_effect,
                    "playbackMode": resolved_playback,
                    "commandSequenceId": sequence,
                },
            },
        }
    )


def _passing_tvideo_farm_messages() -> list[str | bytes]:
    cues = [
        "barn-listen",
        "barn-thinking",
        "barn-correct",
        "barn-retry-level-1",
        "barn-correct",
        "barn-to-hay-word-transition",
        "hay-listen",
        "hay-thinking",
        "hay-correct",
        "hay-celebrate",
    ]
    messages = [json.dumps({"type": "hello"})]
    audit_turn = 1
    for index, cue_id in enumerate(cues, start=1):
        label = google_live_robot_soak.TVIDEO_FARM_EXPECTED_PROGRESS[index - 1][
            "label"
        ]
        step_key = cue_id.split("-", 1)[0]
        effect = cue_id.removeprefix(f"{step_key}-")
        if cue_id == "barn-to-hay-word-transition":
            effect = "word-transition"
        for tool_name in google_live_robot_soak.TVIDEO_FARM_EXPECTED_TOOL_PLAN[
            label
        ]:
            messages.append(
                _tool_audit_frame(
                    tool_name=tool_name,
                    cue_id=cue_id,
                    effect=google_live_robot_soak._expected_tvideo_tool_effect(
                        effect
                    ),
                    step_key=step_key,
                    origin_turn=audit_turn,
                    refreshed_turn=audit_turn + 1,
                )
            )
            audit_turn += 1
        if cue_id == "barn-to-hay-word-transition":
            messages.append(json.dumps({"type": "tts", "state": "stop", "reason": "interrupt"}))
        messages.append(_tvideo_farm_frame("lesson_prepare", cue_id, index * 2 - 1, command="prepare"))
        messages.append(_tvideo_farm_frame("lesson_cinematic_control", cue_id, index * 2, command="start"))
        messages.append(json.dumps({"type": "tts", "state": "start"}))
        messages.append(_valid_output_opus_packet())
        if cue_id != "barn-correct" or index != 5:
            messages.append(json.dumps({"type": "tts", "state": "stop"}))
    return messages


def _valid_output_opus_packet() -> bytes:
    args = _tvideo_farm_args()
    base_fixture = google_live_robot_soak._tvideo_farm_fixture_config(args.audio_source)
    packets, _fixture = google_live_robot_soak._tvideo_farm_turn_opus_packets(args, "target_answer", base_fixture)
    return packets[0]


def _find_frame_index(messages, cue_id: str, command: str) -> int:
    for index, message in enumerate(messages):
        if not isinstance(message, str):
            continue
        payload = json.loads(message)
        phase = (payload.get("body") or {}).get("cinematicPhase") or {}
        if phase.get("cueId") == cue_id and phase.get("command") == command:
            return index
    raise AssertionError(f"frame not found: {cue_id}/{command}")


def _replace_frame(messages, cue_id: str, command: str, replacement: str) -> None:
    messages[_find_frame_index(messages, cue_id, command)] = replacement


def _remove_frame(messages, cue_id: str, command: str) -> None:
    messages.pop(_find_frame_index(messages, cue_id, command))


def _remove_interrupt_stop(messages) -> None:
    transition = _find_frame_index(messages, "barn-to-hay-word-transition", "prepare")
    assert json.loads(messages[transition - 1])["state"] == "stop"
    messages.pop(transition - 1)


def _replace_interrupt_reason(messages) -> None:
    transition = _find_frame_index(messages, "barn-to-hay-word-transition", "prepare")
    payload = json.loads(messages[transition - 1])
    payload["reason"] = "natural_end"
    messages[transition - 1] = json.dumps(payload)


def _insert_late_output_after_transition_cue(messages) -> None:
    start = _find_frame_index(messages, "barn-to-hay-word-transition", "start")
    messages.insert(start + 1, b"late-cancelled-generation-opus")


def _remove_command_sequence(messages) -> None:
    index = _find_frame_index(messages, "barn-thinking", "start")
    payload = json.loads(messages[index])
    payload["body"].pop("commandSequenceId", None)
    payload["body"]["cinematicPhase"].pop("commandSequenceId", None)
    messages[index] = json.dumps(payload)


def _replace_lesson_session(messages) -> None:
    index = _find_frame_index(messages, "hay-listen", "start")
    payload = json.loads(messages[index])
    payload["sessionId"] = "stale-lesson-session"
    messages[index] = json.dumps(payload)


def _replace_output_with_corrupt_binary(messages) -> None:
    messages[messages.index(_valid_output_opus_packet())] = b"not-opus"


def _mutate_frame(messages, cue_id: str, command: str, mutator) -> None:
    index = _find_frame_index(messages, cue_id, command)
    payload = json.loads(messages[index])
    mutator(payload)
    messages[index] = json.dumps(payload)


def _tool_audit_frame(
    *,
    tool_name="lesson_visual_reaction",
    cue_id="barn-listen",
    effect="show_listening_scene",
    step_key="barn",
    origin_turn=1,
    refreshed_turn=2,
    accepted=True,
    stale=False,
    feature="googleLiveValidationToolAuditV1",
) -> str:
    lesson_session_id = "stale-session" if stale else "lesson-session-1"
    return json.dumps(
        {
            "type": "google_live_validation_tool_audit",
            "feature": feature,
            "protocolVersion": "teebot-lesson-renderer.v4",
            "identity": {
                "lessonSessionId": lesson_session_id,
                "turnSequenceId": origin_turn,
                "attemptId": f"{lesson_session_id}:{step_key}:{origin_turn}",
                "stepKey": step_key,
            },
            "toolName": tool_name,
            "accepted": accepted,
            "code": "ACCEPTED" if accepted else "REJECTED",
            "cueId": cue_id,
            "effect": effect,
            "refreshedIdentity": {
                "lessonSessionId": lesson_session_id,
                "turnSequenceId": refreshed_turn,
                "attemptId": f"{lesson_session_id}:{step_key}:{refreshed_turn}",
                "stepKey": step_key,
                "cueId": cue_id,
            },
        }
    )


def _insert_rejected_tool_audit(messages) -> None:
    messages.insert(_find_frame_index(messages, "barn-listen", "prepare"), _tool_audit_frame(accepted=False))


def _insert_stale_accepted_tool_audit(messages) -> None:
    messages.insert(_find_frame_index(messages, "barn-listen", "start") + 1, _tool_audit_frame(stale=True))


def _insert_wrong_feature_tool_audit(messages) -> None:
    messages.insert(
        _find_frame_index(messages, "barn-listen", "start") + 1,
        _tool_audit_frame(feature="legacyAuditFeature"),
    )


class _TVideoFarmFakeWebSocket:
    def __init__(self, messages: list[str | bytes]):
        self.messages = list(messages)
        self.sent: list[str | bytes] = []

    async def send(self, payload):
        self.sent.append(payload)

    async def recv(self):
        if not self.messages:
            await asyncio.sleep(0)
            raise asyncio.TimeoutError
        return self.messages.pop(0)

    async def close(self):
        return None


class _TVideoFarmFakeConnect:
    def __init__(self, websocket: _TVideoFarmFakeWebSocket):
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, *_exc):
        return False


def _tvideo_farm_args(**overrides) -> SimpleNamespace:
    values = {
        "scenario": "tvideo-farm",
        "websocket_url": "ws://fake/tbot/v1",
        "device_mac": "robot-1",
        "device_id": "robot-1",
        "client_id": "soak-harness-client",
        "audio_source": "synthetic",
        "event_timeout_sec": 0.2,
        "open_timeout_sec": 0.2,
        "speak_for_sec": 0,
        "frame_duration_ms": 60,
        "sample_rate": 24000,
        "dry_run": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_tvideo_farm_live_audio_runner_sends_binary_fixtures_and_validates_progression(
    monkeypatch,
) -> None:
    websocket = _TVideoFarmFakeWebSocket(_passing_tvideo_farm_messages())

    def _connect(_url, **_kwargs):
        return _TVideoFarmFakeConnect(websocket)

    async def _sleep(_seconds):
        return None

    monkeypatch.setattr(google_live_robot_soak.websockets, "connect", _connect)
    monkeypatch.setattr(google_live_robot_soak.asyncio, "sleep", _sleep)

    report = asyncio.run(google_live_robot_soak._run_tvideo_farm_scenario(_tvideo_farm_args()))

    sent_json = [json.loads(payload) for payload in websocket.sent if isinstance(payload, str)]
    assert report["status"] == "PASS"
    assert report["audio_source"] == "synthetic"
    assert report["fixture_set_id"] == "tvideo-farm-synthetic-speech-v1"
    assert report["binary_chunks_sent"] > 0
    assert report["output_binary_chunks"] == 10
    assert report["interruption_count"] == 1
    assert report["late_output_chunks"] == 0
    assert report["conversation_identity_changes"] == 1
    assert report["bargein_audio_sent_while_output_active"] is True
    assert report["lesson_session_consistent"] is True
    assert report["tool_audit_count"] == 18
    assert report["tool_audit_counts"] == {
        "lesson_child_response": 2,
        "lesson_context_turn": 1,
        "lesson_continue": 2,
        "lesson_pronunciation_outcome": 3,
        "lesson_visual_reaction": 10,
    }
    serialized_report = json.dumps(report)
    for private_value in (
        "lesson-session-1",
        "attemptId",
        "lessonSessionId",
        "turnSequenceId",
        "cueId",
        "stepKey",
    ):
        assert private_value not in serialized_report
    assert [turn["input_fixture_id"] for turn in report["turns"]] == [
        "tvideo-farm-synthetic-lesson-start-v1",
        "tvideo-farm-synthetic-target-answer-v1",
        "tvideo-farm-synthetic-meaning-bridge-v1",
        "tvideo-farm-synthetic-related-concept-v1",
        "tvideo-farm-synthetic-retry-coaching-v1",
        "tvideo-farm-synthetic-target-correction-v1",
        "tvideo-farm-synthetic-hay-listen-v1",
        "tvideo-farm-synthetic-hay-thinking-v1",
        "tvideo-farm-synthetic-hay-correct-v1",
        "tvideo-farm-synthetic-hay-celebrate-v1",
    ]
    assert report["turns"][5]["input_fixture_id"].endswith("target-correction-v1")
    assert len({turn["input_fixture_sha256"] for turn in report["turns"]}) >= 6
    assert all(turn["input_opus_packets"] > 0 for turn in report["turns"])
    assert all(payload for payload in websocket.sent if isinstance(payload, bytes))
    assert sent_json[0] == {
        "type": "hello",
        "version": 1,
        "transport": "websocket",
        "audio_params": {
            "format": "opus",
            "sample_rate": 24000,
            "channels": 1,
            "frame_duration": 60,
        },
        "features": {"googleLiveValidationToolAuditV1": True},
    }
    acknowledgements = sent_json[1:]
    assert len(acknowledgements) == 20
    assert [ack["body"]["acks"] for ack in acknowledgements] == list(range(1, 21))
    assert [ack["sequence"] for ack in acknowledgements] == list(range(1, 21))
    assert {ack["sessionId"] for ack in acknowledgements} == {"lesson-session-1"}
    serialized = json.dumps(report)
    assert "prompt" not in serialized.lower()
    assert "utterance" not in serialized.lower()
    assert "fixture" in report
    assert sorted(report["fixture"]) == [
        "format",
        "frame_duration_ms",
        "sample_rate",
        "sha256",
        "source",
    ]
    assert all(
        sorted(turn)
        == [
            "input_fixture_id",
            "input_fixture_sha256",
            "input_opus_packets",
            "latency_ms",
        ]
        for turn in report["turns"]
    )


@pytest.mark.parametrize(
    "mutate,expected_error",
    [
        (
            lambda messages: _remove_frame(messages, "barn-thinking", "prepare"),
            "missing_cinematic_event",
        ),
        (
            lambda messages: _replace_frame(
                messages,
                "barn-thinking",
                "start",
                _tvideo_farm_frame("lesson_cinematic_control", "hay-listen", 4, command="start"),
            ),
            "wrong_cue",
        ),
        (
            lambda messages: _replace_frame(
                messages,
                "hay-listen",
                "start",
                _tvideo_farm_frame("lesson_cinematic_control", "barn-correct", 14, command="start"),
            ),
            "stale_or_missing_step_transition",
        ),
        (
            lambda messages: _replace_frame(
                messages,
                "barn-correct",
                "start",
                _tvideo_farm_frame("lesson_cinematic_control", "barn-correct", 4, command="start"),
            ),
            "non_increasing_command_sequence",
        ),
        (
            lambda messages: _replace_frame(
                messages,
                "barn-thinking",
                "start",
                _tvideo_farm_frame(
                    "lesson_cinematic_control",
                    "barn-thinking",
                    4,
                    command="start",
                    effect="incorrect-effect",
                ),
            ),
            "wrong_effect",
        ),
        (
            lambda messages: _replace_frame(
                messages,
                "barn-listen",
                "prepare",
                _tvideo_farm_frame(
                    "lesson_cinematic_control",
                    "barn-listen",
                    1,
                    command="prepare",
                ),
            ),
            "wrong_cinematic_frame_type",
        ),
        (
            lambda messages: messages.pop(_find_frame_index(messages, "barn-listen", "start") + 2),
            "missing_output_audio",
        ),
        (
            _insert_late_output_after_transition_cue,
            "late_output_after_interruption",
        ),
        (_remove_interrupt_stop, "missing_interruption_stop"),
        (_replace_interrupt_reason, "wrong_interruption_reason"),
        (
            lambda messages: _replace_frame(
                messages,
                "barn-listen",
                "prepare",
                _tvideo_farm_frame("lesson_prepare", "barn-listen", 1, command="pause"),
            ),
            "wrong_cinematic_command",
        ),
        (_remove_command_sequence, "missing_command_sequence"),
        (_replace_lesson_session, "lesson_session_mismatch"),
        (_replace_output_with_corrupt_binary, "invalid_output_opus"),
        (
            lambda messages: _replace_frame(
                messages,
                "barn-listen",
                "prepare",
                _tvideo_farm_frame(
                    "lesson_prepare",
                    "barn-listen",
                    1,
                    command="prepare",
                    protocol_version="teebot-lesson-renderer.v3",
                ),
            ),
            "wrong_protocol_version",
        ),
        (
            lambda messages: _replace_frame(
                messages,
                "barn-listen",
                "prepare",
                _tvideo_farm_frame(
                    "lesson_prepare",
                    "barn-listen",
                    1,
                    command="prepare",
                    step_id="hay",
                ),
            ),
            "wrong_step",
        ),
        (
            lambda messages: _mutate_frame(
                messages,
                "barn-thinking",
                "start",
                lambda payload: payload["body"].pop("playbackMode"),
            ),
            "missing_playback_mode",
        ),
        (
            lambda messages: _mutate_frame(
                messages,
                "barn-thinking",
                "start",
                lambda payload: payload["body"]["cinematicPhase"].__setitem__("playbackMode", "once"),
            ),
            "cinematic_duplicate_mismatch",
        ),
        (
            lambda messages: _mutate_frame(
                messages,
                "barn-thinking",
                "start",
                lambda payload: payload["body"].__setitem__("cueId", "hay-listen"),
            ),
            "cinematic_duplicate_mismatch",
        ),
        (_insert_rejected_tool_audit, "tool_audit_rejected"),
        (_insert_stale_accepted_tool_audit, "tool_audit_identity_mismatch"),
        (_insert_wrong_feature_tool_audit, "wrong_tool_audit_feature"),
    ],
)
def test_tvideo_farm_live_audio_runner_rejects_broken_authoritative_transcripts(
    monkeypatch,
    mutate,
    expected_error,
) -> None:
    messages = _passing_tvideo_farm_messages()
    mutate(messages)
    websocket = _TVideoFarmFakeWebSocket(messages)

    def _connect(_url, **_kwargs):
        return _TVideoFarmFakeConnect(websocket)

    async def _sleep(_seconds):
        return None

    monkeypatch.setattr(google_live_robot_soak.websockets, "connect", _connect)
    monkeypatch.setattr(google_live_robot_soak.asyncio, "sleep", _sleep)

    report = asyncio.run(google_live_robot_soak._run_tvideo_farm_scenario(_tvideo_farm_args()))

    assert report["status"] == "FAIL"
    assert expected_error in report["validation_errors"]
    assert report["exit_code"] == 1
