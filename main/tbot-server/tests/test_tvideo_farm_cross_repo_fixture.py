from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from core.lesson.conversation_contract import lesson_conversation_contract_from_backend
from core.lesson.flattened_cinematic_contract import (
    project_flattened_cinematic_phase,
    validate_flattened_cinematic_manifest,
)
from core.lesson.runtime import _manifest_asset_cache_inputs
from core.lesson.sd_pack_materializer import materialize_lesson_sd_pack
from scripts.project_tvideo_farm_firmware_fixture import build_firmware_fixture

FIXTURE = Path(__file__).parent / "fixtures" / "tvideo_farm_manifest_v2.json"
PROVENANCE = Path(__file__).parent / "fixtures" / "tvideo_farm_manifest_v2.provenance.json"
EXPECTED_CUES = [
    "barn-opening",
    "barn-greet",
    "barn-teach",
    "barn-listen",
    "barn-thinking",
    "barn-correct",
    "barn-retry-level-1",
    "barn-retry-level-2",
    "barn-retry-level-3",
    "barn-celebrate",
    "barn-to-hay-word-transition",
    "hay-teach",
    "hay-listen",
    "hay-thinking",
    "hay-correct",
    "hay-retry-level-1",
    "hay-retry-level-2",
    "hay-retry-level-3",
    "hay-celebrate",
]
MANIFEST_CHECKSUM = "bb7d4dcdf6318096c0b9224dc48bcdcb3ff78b325706cdc9c5d39bd4e7da94e4"
BACKEND_HEAD = "f78f8eae312616d7d1a30bf350404e9d8028bab0"
BACKEND_BUILD_INPUT_SHA256 = "f47ac8c0d0dca550dfb47262037a22a6f1f9e354a7b1d6299cfb43648fdb8be1"


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.status_code = 200
        self.headers: dict[str, str] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc_info):
        return False

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self, _chunk_size: int):
        yield self.payload


class _Client:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads

    def stream(self, method: str, url: str, **_kwargs: Any) -> _Response:
        assert method == "GET"
        return _Response(self.payloads[url])


async def _public_resolver(_host: str) -> list[str]:
    return ["93.184.216.34"]


def test_farm_fixture_records_pinned_backend_source_provenance() -> None:
    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))

    assert provenance["backend"]["head"] == BACKEND_HEAD
    assert provenance["backend"]["buildInputSha256"] == BACKEND_BUILD_INPUT_SHA256
    assert provenance["backend"]["buildInputCount"] == 1088
    assert provenance["backend"]["typescriptCompilerSha256"] == (
        "3ae902c92cc44dace175c0e69e13a4b0899f6983c6121d76b9ab8dd5795e7675"
    )
    assert provenance["backend"]["relevantSources"] == {
        "src/lessons/authoring/lesson-authoring.flattened-derivatives.ts": (
            "f6930f54987351ce2cd41266dba179855d06691c4e9545789f4c046de784f705"
        ),
        "src/lessons/lesson-manifest.logic.ts": (
            "95b694505c629ba19c0145aaacd05d5e7003a40f2c8d5af5f9cc11ba6a1f88fc"
        ),
        "src/lessons/lesson.constants.ts": (
            "8b1ff0b9b4e873f6151c8c1845e84fe4102d488b4760822040ab01193ecb5dd2"
        ),
        "src/lessons/tvideo-journey/fixtures/farm-golden.ts": (
            "f5826f62a3b876f6266ca702037943f4ef118184ec6d599068207fceadbd1eb0"
        ),
        "src/lessons/tvideo-journey/tvideo-journey.cues.ts": (
            "22856732ec7d59e19092999e82c0faffabd61bc03b9badf8a2e32afe57398f59"
        ),
    }
    assert provenance["manifest"] == {
        "canonicalSha256": "44f1dd88f44acd903c7196b7ad1245e5d2177c18f5dd7de49e137a045bf4d50f",
        "cueCount": 19,
        "fileSha256": "77f196f20c488aa215fc0051dcdbe490a154f651d8edb060c1b098fba7dc846a",
        "manifestChecksum": MANIFEST_CHECKSUM,
    }


def test_backend_farm_fixture_projects_exact_conversation_and_sd_asset_identities() -> None:
    manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))

    validate_flattened_cinematic_manifest(manifest)
    contracts = [
        lesson_conversation_contract_from_backend(manifest, lesson_session_id="fixture-session", step_key=step_key)
        for step_key in ("barn", "hay")
    ]
    assert [contract.step_key for contract in contracts] == ["barn", "hay"]
    assert all(contract.max_contextual_turns == 2 for contract in contracts)

    phases = manifest["cinematicPhases"]
    assert [phase["cueId"] for phase in phases] == EXPECTED_CUES
    assert len({phase["asset"]["derivativeId"] for phase in phases}) == 19
    assert all(phase["asset"]["metadata"]["fps"] == 10 for phase in phases)
    assert all(phase["asset"]["metadata"]["hasAudio"] is False for phase in phases)

    projected = _manifest_asset_cache_inputs(manifest)
    flattened = [asset for asset in projected if asset["key"].startswith("flattenedCinematic.")]
    assert [asset["cueId"] for asset in flattened] == EXPECTED_CUES
    for phase, asset in zip(phases, flattened, strict=True):
        source = phase["asset"]
        assert asset == {
            "key": f"flattenedCinematic.{phase['cueId']}",
            "path": source["path"],
            "url": source["url"],
            "sha256": source["sha256"],
            "size": source["bytes"],
            "critical": True,
            "layer": "flattenedCinematic",
            "role": phase["cueId"],
            "mediaType": "video/mp4",
            "derivativeId": source["derivativeId"],
            "cueId": phase["cueId"],
            "effect": phase["effect"],
            "stepKey": phase["stepKey"],
            "playbackMode": phase["playbackMode"],
            "compatibilityMetadata": {
                "codec": "mjpeg",
                "width": 480,
                "height": 320,
                "fps": 10,
                "durationMs": phase["timing"]["durationMs"],
                "frameCount": phase["asset"]["metadata"]["frameCount"],
                "hasAudio": False,
            },
        }

    canonical = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    assert hashlib.sha256(canonical.encode()).hexdigest() == (
        "44f1dd88f44acd903c7196b7ad1245e5d2177c18f5dd7de49e137a045bf4d50f"
    )


@pytest.mark.asyncio
async def test_farm_fixture_materializes_all_cues_and_projects_exact_firmware_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cache_key = f"{manifest['lessonId']}/v{manifest['lessonVersion']}-{MANIFEST_CHECKSUM}"
    local_root = f"/sdcard/tbot/lesson-assets/{cache_key}"
    projected = _manifest_asset_cache_inputs(manifest)
    flattened = [asset for asset in projected if asset["key"].startswith("flattenedCinematic.")]
    payloads = {
        asset["url"]: f"TBOT_TVIDEO_FARM_SOFTWARE_FIXTURE_V1:{asset['cueId']}\n".encode() for asset in flattened
    }
    assets = []
    for asset in flattened:
        sd_path = f"{local_root}/{asset['key']}"
        assets.append(
            {
                **{key: value for key, value in asset.items() if key not in {"path", "layer", "role"}},
                "onlineUrl": asset["url"],
                "sdPath": sd_path,
                "localPath": sd_path,
            }
        )
    materializer_manifest = {
        "lessonId": manifest["lessonId"],
        "lessonVersion": manifest["lessonVersion"],
        "profile": "espTft",
        "manifestChecksum": MANIFEST_CHECKSUM,
        "cacheKey": cache_key,
        "assets": assets,
    }
    monkeypatch.setenv("LESSON_ASSET_ALLOWED_ORIGINS", "https://fixtures.example.test")
    monkeypatch.setenv("LESSON_SD_MAX_FILE_BYTES", "1024")
    monkeypatch.setenv("LESSON_SD_MAX_PACK_BYTES", "65536")
    result = await materialize_lesson_sd_pack(
        materializer_manifest,
        config={"lesson": {"asset_pack_mount_root": str(tmp_path / "sd" / "tbot" / "lesson-assets")}},
        client=_Client(payloads),
        resolver=_public_resolver,
    )
    assert result["downloadedCount"] == 19

    stored_path = tmp_path / "sd" / "tbot" / "lesson-assets" / cache_key / "pack.json"
    stored = json.loads(stored_path.read_text(encoding="utf-8"))
    pack = {**stored, "ready": True, "localRoot": local_root}
    for asset in pack["assets"]:
        asset.update(state="READY", checksumOk=True, localPath=asset["sdPath"])
    commands = [project_flattened_cinematic_phase(phase, pack) for phase in manifest["cinematicPhases"]]
    assert [command["cueId"] for command in commands] == EXPECTED_CUES
    for command, phase in zip(commands, manifest["cinematicPhases"], strict=True):
        assert command["effect"] == phase["effect"]
        assert command["stepKey"] == phase["stepKey"]
        assert command["playbackMode"] == phase["playbackMode"]
        assert command["durationMs"] == phase["timing"]["durationMs"]
        assert command["fps"] == 10
        assert command["frameCount"] == phase["asset"]["metadata"]["frameCount"]
        assert command["asset"]["derivativeId"] == phase["asset"]["derivativeId"]
        assert command["asset"]["sha256"] == phase["asset"]["sha256"]
        assert command["asset"]["bytes"] == phase["asset"]["bytes"]
        assert command["asset"]["sdPath"].endswith(f"/flattenedCinematic.{phase['cueId']}")


def test_firmware_fixture_uses_complete_ordered_strict_prepare_start_pairs() -> None:
    manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture = build_firmware_fixture(manifest)

    assert fixture["source"] == {
        "lessonId": "tvideo-farm",
        "lessonVersion": 4,
        "manifestChecksum": MANIFEST_CHECKSUM,
        "canonicalManifestSha256": "44f1dd88f44acd903c7196b7ad1245e5d2177c18f5dd7de49e137a045bf4d50f",
        "cueCount": 19,
    }
    frames = fixture["frames"]
    assert len(frames) == 38
    for index, cue_id in enumerate(EXPECTED_CUES):
        prepare, start = frames[index * 2 : index * 2 + 2]
        command = prepare["body"]["cinematicPhase"]
        assert prepare["type"] == "lesson_prepare"
        assert command["command"] == "prepare"
        assert command["cueId"] == cue_id
        assert command["asset"]["cueId"] == cue_id
        assert start["type"] == "lesson_cinematic_control"
        assert start["body"] == {
            "command": "start",
            "cueId": cue_id,
            "commandSequenceId": start["sequence"],
        }
        assert set(start["body"]) == {"command", "cueId", "commandSequenceId"}

    canonical = json.dumps(fixture, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    assert hashlib.sha256(canonical.encode()).hexdigest() == (
        "6ae4f029a18ba82b01fc3e20da0f78cfb0a5a18c67e43c86a4f4cc78630c316d"
    )
