from __future__ import annotations

import hashlib
import json
import subprocess
import sys
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
from scripts.generate_tvideo_farm_trgb_task4_fixture import generate_fixture
from scripts.project_tvideo_farm_firmware_fixture import build_firmware_fixture

LEGACY_FIXTURE = Path(__file__).parent / "fixtures" / "tvideo_farm_manifest_v2.json"
LEGACY_PROVENANCE = Path(__file__).parent / "fixtures" / "tvideo_farm_manifest_v2.provenance.json"
FIXTURE = Path(__file__).parent / "fixtures" / "tvideo_farm_manifest_v2_trgb_task4.json"
PROVENANCE = Path(__file__).parent / "fixtures" / "tvideo_farm_manifest_v2_trgb_task4.provenance.json"
PROJECT_SCRIPT = Path(__file__).parents[1] / "scripts" / "project_tvideo_farm_firmware_fixture.py"
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
TRGB_MEDIA_TYPE = "application/vnd.tbot.rgb565-indexed"


def _trgb_bytes(frame_count: int) -> int:
    index_end = 64 + frame_count * 16
    return ((index_end + 511) // 512) * 512 + frame_count * 307200


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
    provenance = json.loads(LEGACY_PROVENANCE.read_text(encoding="utf-8"))

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


def test_task4_trgb_fixture_and_provenance_are_reproducible(tmp_path: Path) -> None:
    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert provenance["source"]["file"] == LEGACY_FIXTURE.name
    assert provenance["source"]["fileSha256"] == hashlib.sha256(LEGACY_FIXTURE.read_bytes()).hexdigest()
    assert provenance["generator"]["version"] == "tvideo-farm-trgb-task4.v1"
    assert provenance["generator"]["fileSha256"] == hashlib.sha256(
        (PROJECT_SCRIPT.parent / "generate_tvideo_farm_trgb_task4_fixture.py").read_bytes()
    ).hexdigest()
    assert provenance["artifact"] == {
        "fileSha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "canonicalSha256": hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest(),
        "manifestChecksum": provenance["artifact"]["canonicalSha256"],
        "cueCount": 19,
        "staticAssetCount": 8,
        "totalAssetCount": 27,
    }
    assert len(provenance["staticPayloads"]) == 8
    for payload in provenance["staticPayloads"]:
        path = PROJECT_SCRIPT.parents[1] / payload["path"]
        content = path.read_bytes()
        assert payload == {
            "path": payload["path"],
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }

    generated = tmp_path / FIXTURE.name
    generated_provenance = tmp_path / PROVENANCE.name
    generate_fixture(source=LEGACY_FIXTURE, output=generated, provenance_output=generated_provenance)
    assert generated.read_bytes() == FIXTURE.read_bytes()
    assert generated_provenance.read_bytes() == PROVENANCE.read_bytes()
    assert provenance["firmwareFixture"] == {
        "canonicalSha256": provenance["firmwareFixture"]["canonicalSha256"],
        "frameCount": 38,
        "cueCount": 19,
    }


def test_task4_generator_rejects_missing_static_payloads(tmp_path: Path) -> None:
    output = tmp_path / "manifest.json"
    provenance = tmp_path / "manifest.provenance.json"

    with pytest.raises(ValueError, match="static payload is unavailable"):
        generate_fixture(
            source=LEGACY_FIXTURE,
            output=output,
            provenance_output=provenance,
            payload_root=tmp_path / "missing-static-payloads",
        )

    assert not output.exists()
    assert not provenance.exists()


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
    assert all(phase["asset"]["path"].endswith(f"/{phase['cueId']}.trgb") for phase in phases)
    assert all(phase["asset"]["metadata"]["fps"] == 10 for phase in phases)
    assert all(phase["asset"]["metadata"]["hasAudio"] is False for phase in phases)
    assert all(
        phase["asset"]["bytes"] == _trgb_bytes(phase["asset"]["metadata"]["frameCount"])
        for phase in phases
    )

    projected = _manifest_asset_cache_inputs(manifest)
    flattened = [asset for asset in projected if asset["key"].startswith("flattenedCinematic.")]
    static = [asset for asset in projected if not asset["key"].startswith("flattenedCinematic.")]
    assert len(projected) == 27
    assert len(flattened) == 19
    assert len(static) == 8
    assert not any(asset["path"].endswith(".mp4") for asset in flattened)
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
            "mediaType": TRGB_MEDIA_TYPE,
            "derivativeId": source["derivativeId"],
            "cueId": phase["cueId"],
            "effect": phase["effect"],
            "stepKey": phase["stepKey"],
            "playbackMode": phase["playbackMode"],
            "compatibilityMetadata": source["metadata"],
        }


def test_farm_fixture_projects_attested_sd_paths_and_exact_firmware_metadata() -> None:
    manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cache_key = f"{manifest['lessonId']}/v{manifest['lessonVersion']}-{MANIFEST_CHECKSUM}"
    local_root = f"/sdcard/tbot/lesson-assets/{cache_key}"
    projected = _manifest_asset_cache_inputs(manifest)
    assets = []
    for asset in projected:
        sd_path = f"{local_root}/{asset['key']}"
        assets.append(
            {
                **{key: value for key, value in asset.items() if key not in {"path", "layer", "role"}},
                "onlineUrl": asset["url"],
                "sdPath": sd_path,
                "localPath": sd_path,
            }
        )
    pack = {
        "lessonId": manifest["lessonId"],
        "lessonVersion": manifest["lessonVersion"],
        "manifestChecksum": MANIFEST_CHECKSUM,
        "cacheKey": cache_key,
        "ready": True,
        "localRoot": local_root,
        "assets": assets,
    }
    for asset in pack["assets"]:
        asset.update(state="READY", checksumOk=True, localPath=asset["sdPath"])
    assert len(pack["assets"]) == 27
    assert len([asset for asset in pack["assets"] if asset["key"].startswith("flattenedCinematic.")]) == 19
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
        assert command["asset"]["mediaType"] == TRGB_MEDIA_TYPE
        assert command["asset"]["compatibilityMetadata"] == phase["asset"]["metadata"]
        assert command["asset"]["sdPath"].endswith(f"/flattenedCinematic.{phase['cueId']}")


@pytest.mark.asyncio
async def test_checked_in_static_payloads_materialize_with_artifact_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))
    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    checksum = provenance["artifact"]["manifestChecksum"]
    cache_key = f"{manifest['lessonId']}/v{manifest['lessonVersion']}-{checksum}"
    local_root = f"/sdcard/tbot/lesson-assets/{cache_key}"
    static_assets = _manifest_asset_cache_inputs(manifest)[:8]
    payload_by_url = {}
    materializer_assets = []
    for projected, payload in zip(static_assets, provenance["staticPayloads"], strict=True):
        content = (PROJECT_SCRIPT.parents[1] / payload["path"]).read_bytes()
        payload_by_url[projected["url"]] = content
        materializer_assets.append({
            **{key: value for key, value in projected.items() if key not in {"path", "layer", "role"}},
            "onlineUrl": projected["url"],
            "sdPath": f"{local_root}/{projected['key']}",
            "localPath": f"{local_root}/{projected['key']}",
        })
        assert projected["sha256"] == hashlib.sha256(content).hexdigest()
        assert projected["size"] == len(content)

    monkeypatch.setenv("LESSON_ASSET_ALLOWED_ORIGINS", "https://fixtures.example.test")
    monkeypatch.setenv("LESSON_SD_MAX_FILE_BYTES", "4096")
    monkeypatch.setenv("LESSON_SD_MAX_PACK_BYTES", "65536")
    result = await materialize_lesson_sd_pack(
        {
            "lessonId": manifest["lessonId"],
            "lessonVersion": manifest["lessonVersion"],
            "profile": "espTft",
            "manifestChecksum": checksum,
            "cacheKey": cache_key,
            "assets": materializer_assets,
        },
        config={"lesson": {"asset_pack_mount_root": str(tmp_path / "sd" / "tbot" / "lesson-assets")}},
        client=_Client(payload_by_url),
        resolver=_public_resolver,
    )

    assert result["downloadedCount"] == 8
    stored = json.loads(
        (tmp_path / "sd" / "tbot" / "lesson-assets" / cache_key / "pack.json").read_text(encoding="utf-8")
    )
    assert [(asset["sha256"], asset["size"]) for asset in stored["assets"]] == [
        (payload["sha256"], payload["bytes"]) for payload in provenance["staticPayloads"]
    ]


def test_firmware_fixture_uses_complete_ordered_strict_prepare_start_pairs() -> None:
    manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))
    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    fixture = build_firmware_fixture(manifest, manifest_checksum=provenance["artifact"]["manifestChecksum"])

    assert fixture["source"] == {
        "lessonId": "tvideo-farm",
        "lessonVersion": 4,
        "manifestChecksum": provenance["artifact"]["manifestChecksum"],
        "canonicalManifestSha256": hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest(),
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
        provenance["firmwareFixture"]["canonicalSha256"]
    )


def test_projection_cli_rejects_legacy_mp4_and_accepts_checked_in_trgb(tmp_path: Path) -> None:
    rejected = subprocess.run(
        [sys.executable, str(PROJECT_SCRIPT), "--manifest", str(LEGACY_FIXTURE), "--output", str(tmp_path / "old.json")],
        cwd=PROJECT_SCRIPT.parents[1], text=True, capture_output=True, check=False,
    )
    assert rejected.returncode != 0
    assert not (tmp_path / "old.json").exists()

    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    output = tmp_path / "firmware.json"
    accepted = subprocess.run(
        [
            sys.executable, str(PROJECT_SCRIPT), "--manifest", str(FIXTURE), "--output", str(output),
            "--manifest-checksum", provenance["artifact"]["manifestChecksum"],
        ],
        cwd=PROJECT_SCRIPT.parents[1], text=True, capture_output=True, check=False,
    )
    assert accepted.returncode == 0, accepted.stderr
    assert output.is_file()
    assert accepted.stdout.strip() == provenance["firmwareFixture"]["canonicalSha256"]
