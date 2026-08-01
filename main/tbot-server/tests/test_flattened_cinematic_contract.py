from __future__ import annotations

from copy import deepcopy

import pytest

from core.lesson.flattened_cinematic_contract import (
    FlattenedCinematicContractError,
    cinematic_identity_key,
    project_flattened_cinematic_phase,
    validate_flattened_cinematic_manifest,
)
from core.lesson.runtime import _manifest_asset_cache_inputs


DERIVATIVE_ID = "d" * 64
ASSET_SHA = "a" * 64
MANIFEST_SHA = "b" * 64
CACHE_KEY = f"lesson-a/v4-{MANIFEST_SHA}"
LOCAL_ROOT = f"/sdcard/tbot/lesson-assets/{CACHE_KEY}"


def _phase() -> dict:
    return {
        "templateId": "flattenedMjpegCinematic",
        "templateVersion": 1,
        "phaseId": "opening",
        "timing": {"durationMs": 9000},
        "asset": {
            "derivativeId": DERIVATIVE_ID,
            "path": f"lessons/derivatives/{DERIVATIVE_ID}/opening.mp4",
            "url": f"https://cdn.example/lessons/derivatives/{DERIVATIVE_ID}/opening.mp4",
            "sha256": ASSET_SHA,
            "bytes": 1234567,
            "mediaType": "video/mp4",
            "width": 480,
            "height": 320,
            "metadata": {
                "codec": "mjpeg",
                "fps": 10,
                "durationMs": 9000,
                "frameCount": 90,
                "hasAudio": False,
            },
        },
    }


def _pack() -> dict:
    path = f"{LOCAL_ROOT}/flattenedCinematic.opening"
    return {
        "assignmentVersion": 1,
        "lessonId": "lesson-a",
        "lessonVersion": 4,
        "manifestChecksum": MANIFEST_SHA,
        "cacheKey": CACHE_KEY,
        "localRoot": LOCAL_ROOT,
        "ready": True,
        "assets": [{
            "key": "flattenedCinematic.opening",
            "state": "READY",
            "checksumOk": True,
            "localPath": path,
            "sdPath": path,
            "sha256": ASSET_SHA,
            "size": 1234567,
            "mediaType": "video/mp4",
            "derivativeId": DERIVATIVE_ID,
            "phaseId": "opening",
            "compatibilityMetadata": {
                "codec": "mjpeg",
                "width": 480,
                "height": 320,
                "fps": 10,
                "durationMs": 9000,
                "frameCount": 90,
                "hasAudio": False,
            },
        }],
    }


def _cue(cue_id: str, *, effect: str = "listen", step_key: str = "barn") -> dict:
    cue = _phase()
    cue_id_path = cue_id
    duration_ms = {
        "opening": 9500, "greet": 1200, "teach": 2600, "listen": 1300,
        "thinking": 1300, "correct": 600, "retry-level-1": 1200,
        "retry-level-2": 1400, "retry-level-3": 1600, "celebrate": 3000,
        "word-transition": 1100,
    }[effect]
    cue.update({
        "templateVersion": 2,
        "cueId": cue_id,
        "effect": effect,
        "stepKey": step_key,
        "playbackMode": "loop" if effect in {"listen", "thinking"} else "once",
    })
    cue.pop("phaseId")
    cue["timing"]["durationMs"] = duration_ms
    cue["asset"]["metadata"]["durationMs"] = duration_ms
    cue["asset"]["metadata"]["frameCount"] = duration_ms // 100
    cue["asset"]["path"] = f"lessons/derivatives/{DERIVATIVE_ID}/{cue_id_path}.mp4"
    cue["asset"]["url"] = f"https://cdn.example/lessons/derivatives/{DERIVATIVE_ID}/{cue_id_path}.mp4"
    return cue


def _v2_pack(*cue_ids: str) -> dict:
    pack = _pack()
    pack["assets"] = []
    for cue_id in cue_ids:
        path = f"{LOCAL_ROOT}/flattenedCinematic.{cue_id}"
        pack["assets"].append({
            "key": f"flattenedCinematic.{cue_id}",
            "state": "READY",
            "checksumOk": True,
            "localPath": path,
            "sdPath": path,
            "sha256": ASSET_SHA,
            "size": 1234567,
            "mediaType": "video/mp4",
            "derivativeId": DERIVATIVE_ID,
            "cueId": cue_id,
            "effect": "listen",
            "stepKey": "barn",
            "playbackMode": "loop",
            "compatibilityMetadata": {
                "codec": "mjpeg", "width": 480, "height": 320, "fps": 10,
                "durationMs": 1300, "frameCount": 13, "hasAudio": False,
            },
        })
    return pack


def test_projects_exact_v4_phase_from_attested_one_file_pack() -> None:
    assert project_flattened_cinematic_phase(_phase(), _pack()) == {
        "templateId": "flattenedMjpegCinematic",
        "templateVersion": 1,
        "phaseId": "opening",
        "durationMs": 9000,
        "fps": 10,
        "frameCount": 90,
        "asset": {
            "derivativeId": DERIVATIVE_ID,
            "phaseId": "opening",
            "sdPath": f"{LOCAL_ROOT}/flattenedCinematic.opening",
            "sha256": ASSET_SHA,
            "bytes": 1234567,
            "mediaType": "video/mp4",
            "width": 480,
            "height": 320,
        },
    }


def test_projects_repeated_v2_effects_by_unique_cue_identity() -> None:
    first = _cue("barn-listen")
    second = _cue("hay-listen", step_key="hay")
    pack = _v2_pack("barn-listen", "hay-listen")
    pack["assets"][1].update(stepKey="hay")

    assert project_flattened_cinematic_phase(first, pack) == {
        "templateId": "flattenedMjpegCinematic",
        "templateVersion": 2,
        "cueId": "barn-listen",
        "effect": "listen",
        "stepKey": "barn",
        "playbackMode": "loop",
        "durationMs": 1300,
        "fps": 10,
        "frameCount": 13,
        "asset": {
            "derivativeId": DERIVATIVE_ID,
            "cueId": "barn-listen",
            "sdPath": f"{LOCAL_ROOT}/flattenedCinematic.barn-listen",
            "sha256": ASSET_SHA,
            "bytes": 1234567,
            "mediaType": "video/mp4",
            "width": 480,
            "height": 320,
        },
    }
    assert project_flattened_cinematic_phase(second, pack)["asset"]["sdPath"].endswith(
        "/flattenedCinematic.hay-listen"
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda pack: pack["assets"][0].pop("cueId"),
        lambda pack: pack["assets"][0].update(phaseId="listen"),
        lambda pack: pack["assets"][0].update(effect="thinking"),
        lambda pack: pack["assets"][0].update(stepKey="hay"),
        lambda pack: pack["assets"][0].update(playbackMode="once"),
    ],
)
def test_rejects_missing_extra_or_stale_v2_pack_identity(mutate) -> None:
    pack = _v2_pack("barn-listen")
    mutate(pack)
    with pytest.raises(FlattenedCinematicContractError):
        project_flattened_cinematic_phase(_cue("barn-listen"), pack)


def test_cinematic_identity_key_is_discriminated() -> None:
    assert cinematic_identity_key({"phaseId": "opening"}) == "opening"
    assert cinematic_identity_key({"cueId": "barn-listen"}) == "barn-listen"
    with pytest.raises(FlattenedCinematicContractError):
        cinematic_identity_key({})
    with pytest.raises(FlattenedCinematicContractError):
        cinematic_identity_key({"phaseId": "listen", "cueId": "barn-listen"})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda phases: phases.append(deepcopy(phases[0])),
        lambda phases: phases[0].pop("playbackMode"),
        lambda phases: phases[0].update(playbackMode="once"),
        lambda phases: phases[0].update(extra=True),
        lambda phases: phases.append(_phase()),
    ],
)
def test_v2_manifest_rejects_duplicate_missing_extra_or_mixed_cues(mutate) -> None:
    phases = [_cue("barn-listen")]
    mutate(phases)
    manifest = {
        "manifestVersion": "teebot-lesson-renderer.v4",
        "protocolVersion": "teebot-lesson-renderer.v4",
        "features": {"lessonRendererV4": {
            "flattenedMjpegCinematic": True,
            "assetSource": "publishedFlattenedDerivative",
        }},
        "cinematicPhases": phases,
    }
    with pytest.raises(FlattenedCinematicContractError):
        validate_flattened_cinematic_manifest(manifest)


@pytest.mark.parametrize(
    ("label", "mutate", "code"),
    [
        ("pack not ready", lambda pack, phase: pack.update(ready=False), "CINEMATIC_PACK_NOT_READY"),
        ("missing path", lambda pack, phase: (pack["assets"][0].pop("localPath"), pack["assets"][0].pop("sdPath")), "CINEMATIC_SD_PATH_MISSING"),
        ("outside root", lambda pack, phase: pack["assets"][0].update(localPath="/sdcard/private/opening.mp4", sdPath="/sdcard/private/opening.mp4"), "CINEMATIC_SD_PATH_MISSING"),
        ("traversal", lambda pack, phase: pack["assets"][0].update(localPath=f"{LOCAL_ROOT}/../secret", sdPath=f"{LOCAL_ROOT}/../secret"), "CINEMATIC_SD_PATH_MISSING"),
        ("URL", lambda pack, phase: pack["assets"][0].update(localPath="https://cdn.example/opening.mp4", sdPath="https://cdn.example/opening.mp4"), "CINEMATIC_SD_PATH_MISSING"),
        ("credential syntax", lambda pack, phase: pack["assets"][0].update(localPath=f"{LOCAL_ROOT}/user@opening", sdPath=f"{LOCAL_ROOT}/user@opening"), "CINEMATIC_SD_PATH_MISSING"),
        ("unsupported key", lambda pack, phase: pack["assets"][0].update(key="opening.mp4"), "CINEMATIC_IDENTITY_UNSUPPORTED"),
        ("derivative mismatch", lambda pack, phase: pack["assets"][0].update(derivativeId="e" * 64), "CINEMATIC_IDENTITY_UNSUPPORTED"),
        ("SHA mismatch", lambda pack, phase: pack["assets"][0].update(sha256="c" * 64), "CINEMATIC_METADATA_MISMATCH"),
        ("bytes mismatch", lambda pack, phase: pack["assets"][0].update(size=2), "CINEMATIC_METADATA_MISMATCH"),
        ("metadata mismatch", lambda pack, phase: pack["assets"][0]["compatibilityMetadata"].update(fps=15), "CINEMATIC_METADATA_MISMATCH"),
    ],
)
def test_rejects_unattested_paths_identity_and_metadata(label, mutate, code) -> None:
    pack = _pack()
    phase = _phase()
    mutate(pack, phase)
    with pytest.raises(FlattenedCinematicContractError) as exc_info:
        project_flattened_cinematic_phase(phase, pack)
    assert exc_info.value.code == code, label


@pytest.mark.parametrize(
    "mutate",
    [
        lambda phase: phase.update(extra=True),
        lambda phase: phase.pop("asset"),
        lambda phase: phase["timing"].update(fps=10),
        lambda phase: phase["asset"].update(signedUrl="secret"),
        lambda phase: phase["asset"]["metadata"].update(profile="robot"),
    ],
)
def test_rejects_missing_or_extra_v4_manifest_fields(mutate) -> None:
    phase = _phase()
    mutate(phase)
    with pytest.raises(FlattenedCinematicContractError) as exc_info:
        project_flattened_cinematic_phase(phase, _pack())
    assert exc_info.value.code == "CINEMATIC_METADATA_MISMATCH"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda phase: phase.update(templateId="directMp4Cinematic"),
        lambda phase: phase.update(templateVersion=2),
        lambda phase: phase.update(phaseId="dance"),
        lambda phase: phase["asset"].update(path="../private/opening.mp4"),
        lambda phase: phase["asset"].update(url="https://user:secret@cdn.example/opening.mp4"),
        lambda phase: phase["asset"].update(width=320),
        lambda phase: phase["asset"]["metadata"].update(codec="h264"),
        lambda phase: phase["asset"]["metadata"].update(frameCount=89),
    ],
)
def test_rejects_invalid_v4_manifest_identity_and_media_contract(mutate) -> None:
    phase = _phase()
    mutate(phase)
    with pytest.raises(FlattenedCinematicContractError) as exc_info:
        project_flattened_cinematic_phase(phase, deepcopy(_pack()))
    assert exc_info.value.code == "CINEMATIC_METADATA_MISMATCH"


def test_v4_manifest_materialization_discovers_one_asset_per_authored_phase() -> None:
    manifest = {
        "manifestVersion": "teebot-lesson-renderer.v4",
        "assets": [{
            "id": "poster", "path": "poster.jpg", "url": "https://cdn.example/poster.jpg",
            "sha256": "c" * 64, "bytes": 10, "critical": True,
            "layer": "backgroundScene", "role": "poster", "mediaType": "image/jpeg",
        }],
        "cinematicPhases": [_phase()],
    }

    assets = _manifest_asset_cache_inputs(manifest)

    assert [asset["key"] for asset in assets] == ["poster", "flattenedCinematic.opening"]
    flattened = assets[1]
    assert flattened["derivativeId"] == DERIVATIVE_ID
    assert flattened["phaseId"] == "opening"
    assert flattened["size"] == 1234567
    assert flattened["compatibilityMetadata"] == {
        "codec": "mjpeg", "width": 480, "height": 320, "fps": 10,
        "durationMs": 9000, "frameCount": 90, "hasAudio": False,
    }


def test_v4_manifest_materialization_uses_v2_cue_keys_and_preserves_identity() -> None:
    manifest = {
        "manifestVersion": "teebot-lesson-renderer.v4",
        "cinematicPhases": [_cue("barn-listen"), _cue("hay-listen", step_key="hay")],
        "assets": [],
    }

    assets = _manifest_asset_cache_inputs(manifest)

    assert [asset["key"] for asset in assets] == [
        "flattenedCinematic.barn-listen",
        "flattenedCinematic.hay-listen",
    ]
    assert {
        key: assets[0][key]
        for key in ("cueId", "effect", "stepKey", "playbackMode")
    } == {
        "cueId": "barn-listen", "effect": "listen",
        "stepKey": "barn", "playbackMode": "loop",
    }


def test_v3_asset_materialization_is_not_relabelled_as_v4() -> None:
    manifest = {
        "manifestVersion": "teebot-lesson-renderer.v3",
        "assets": [{
            "id": "scene@v3", "path": "scene.mp4", "url": "https://cdn.example/scene.mp4",
            "sha256": "c" * 64, "bytes": 10, "critical": True,
            "layer": "backgroundScene", "role": "video", "mediaType": "video/mp4",
        }],
        "cinematicPhases": [_phase()],
    }

    assert _manifest_asset_cache_inputs(manifest) == [{
        "key": "scene@v3", "path": "scene.mp4", "url": "https://cdn.example/scene.mp4",
        "sha256": "c" * 64, "size": 10, "critical": True,
        "layer": "backgroundScene", "role": "video", "mediaType": "video/mp4",
    }]


@pytest.mark.parametrize("mutate", [
    lambda manifest: manifest.update(protocolVersion="teebot-lesson-renderer.v3"),
    lambda manifest: manifest["features"].update(extra=True),
    lambda manifest: manifest["features"]["lessonRendererV4"].update(directMp4Cinematic=True),
    lambda manifest: manifest["features"]["lessonRendererV4"].update(assetSource="publishedVersionedVisualRefs"),
])
def test_rejects_non_exact_v4_manifest_protocol_and_feature_identity(mutate) -> None:
    manifest = {
        "manifestVersion": "teebot-lesson-renderer.v4",
        "protocolVersion": "teebot-lesson-renderer.v4",
        "features": {"lessonRendererV4": {
            "flattenedMjpegCinematic": True,
            "assetSource": "publishedFlattenedDerivative",
        }},
        "cinematicPhases": [_phase()],
    }
    mutate(manifest)

    with pytest.raises(FlattenedCinematicContractError) as exc_info:
        validate_flattened_cinematic_manifest(manifest)
    assert exc_info.value.code == "CINEMATIC_IDENTITY_UNSUPPORTED"
