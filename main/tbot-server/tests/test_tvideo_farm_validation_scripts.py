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
    fixture = (
        tvideo_farm_preview.MANAGER_ROOT / "tests" / "browser" / "lesson-builder-main.js"
    ).read_text(encoding="utf-8")

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
