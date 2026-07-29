"""Lesson error codes + the ESP-raised exceptions (plan §5.8, ADR 0011 namespace).

These mirror the backend ``ErrorCode`` strings so an ESP-raised failure projects
onto the same wire ``lesson_error.body.code`` the firmware/mobile already map.
Only the slice-relevant codes live here; the full namespace is the backend's.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Frozen wire identity — EXACT string match, NOT semver (plan §1.2 / §5.2).
PROTOCOL_VERSION = "teebot-lesson-renderer.v1"
# hello.features.renderer capability value (D-CAP-FLAG, ADR 0013 §I).
RENDERER_CAPABILITY = "teebot-lesson-renderer.v1"
RENDERER_V2_CAPABILITY = "teebot-lesson-renderer.v2"

# Lesson error codes (subset raised by the ESP runtime).
LESSON_VERSION_UNSUPPORTED = "LESSON_VERSION_UNSUPPORTED"
ASSET_PROFILE_UNAVAILABLE = "ASSET_PROFILE_UNAVAILABLE"
ASSET_CHECKSUM_MISMATCH = "ASSET_CHECKSUM_MISMATCH"
PRELOAD_TIMEOUT = "PRELOAD_TIMEOUT"
STEP_TIMEOUT = "STEP_TIMEOUT"
PROTOCOL_SEQUENCE_ERROR = "PROTOCOL_SEQUENCE_ERROR"
ASSET_PACK_NOT_READY = "ASSET_PACK_NOT_READY"
ASSET_PACK_MATERIALIZE_FAILED = "ASSET_PACK_MATERIALIZE_FAILED"
LESSON_FRAME_TOO_LARGE = "LESSON_FRAME_TOO_LARGE"
LESSON_FRAME_INVALID = "LESSON_FRAME_INVALID"
LESSON_FRAME_ACK_TIMEOUT = "LESSON_FRAME_ACK_TIMEOUT"
CINEMATIC_CAPABILITY_UNSUPPORTED = "CINEMATIC_CAPABILITY_UNSUPPORTED"
CINEMATIC_PACK_NOT_READY = "CINEMATIC_PACK_NOT_READY"
CINEMATIC_SD_PATH_MISSING = "CINEMATIC_SD_PATH_MISSING"
CINEMATIC_METADATA_MISMATCH = "CINEMATIC_METADATA_MISMATCH"


class LessonError(Exception):
    """An ESP-raised lesson failure carrying the wire ``{code, message, retryable}``.

    ``context`` is COPPA-safe metadata only (assetKey, ackedSequence) — never
    child utterances / names (plan §3.7, §6.2.8).
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.context = context or {}

    def to_body(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.context:
            body["context"] = self.context
        return body


class AssetProfileUnavailable(LessonError):
    def __init__(self, message: str, *, context: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(ASSET_PROFILE_UNAVAILABLE, message, retryable=False, context=context)


class AssetChecksumMismatch(LessonError):
    """A critical asset's sha256 did not match the manifest.

    Blocks READY permanently (until re-publish); NO auto-retry loop (plan §5.5/§6.2.3).
    """

    def __init__(self, asset_key: str, *, message: Optional[str] = None) -> None:
        super().__init__(
            ASSET_CHECKSUM_MISMATCH,
            message or f"{asset_key} sha256 did not match manifest",
            retryable=False,
            context={"assetKey": asset_key},
        )


class PreloadTimeout(LessonError):
    """No authoritative READY within ``preloadTimeoutSec`` (retryable — verified bytes kept)."""

    def __init__(self, message: str = "preload did not reach READY in time") -> None:
        super().__init__(PRELOAD_TIMEOUT, message, retryable=True)


class StepTimeout(LessonError):
    """A rendered step was not acked within ``timeoutSec`` (RUNNING->FAILED, NOT retryable).

    DISTINCT from PROTOCOL_SEQUENCE_ERROR: a dropped ``lesson_ack`` is a runtime
    stall, not an ordering fault (plan §5.8).
    """

    def __init__(self, step_id: Optional[str], acked_sequence: Optional[int]) -> None:
        super().__init__(
            STEP_TIMEOUT,
            f"no lesson_ack for step {step_id} within timeout",
            retryable=False,
            context={"stepId": step_id, "ackedSequence": acked_sequence},
        )


class ProtocolSequenceError(LessonError):
    """Inbound F->S sequence gap / bad-state (gap is retryable: resend-from-last-acked)."""

    def __init__(self, message: str, *, retryable: bool = True, context: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(PROTOCOL_SEQUENCE_ERROR, message, retryable=retryable, context=context)


def lesson_capability_ok(
    features: Optional[Dict[str, Any]], *, renderer_v2_enabled: bool = False
) -> bool:
    """D-CAP-FLAG (ADR 0013 §I): a device supports lessons ONLY if it advertised
    ``hello.features.lesson == True`` AND ``features.renderer == PROTOCOL_VERSION``.

    Absence = no support (conservative). The ESP MUST NOT send ``lesson_prepare``
    to a device that did not advertise it (else LESSON_VERSION_UNSUPPORTED).
    """
    if not isinstance(features, dict):
        return False
    if features.get("lesson") is not True:
        return False
    renderer = features.get("renderer")
    accepted = {RENDERER_CAPABILITY}
    if renderer_v2_enabled is True:
        accepted.add(RENDERER_V2_CAPABILITY)
    if isinstance(renderer, str):
        return renderer in accepted
    if isinstance(renderer, (list, tuple)):
        return any(capability in accepted for capability in renderer)
    return False


def device_renderer_capabilities(features: Optional[Dict[str, Any]]) -> List[str]:
    """L3 P3: the device's advertised renderer-capability SET (the renderer
    versions whose manifests this firmware can render).

    Sourced from ``hello.features.renderer``, which today is the single string
    ``teebot-lesson-renderer.v1`` but is forward-modelled as EITHER a string or a
    list so a future v2-capable firmware can advertise both. When the device does
    not advertise a renderer (every current firmware that omits the field, or a
    non-dict features), default to ``[RENDERER_CAPABILITY]`` — the v1-only set.

    This is the structural guard: a v1-only device's capability set never contains
    a v2 version, so it can never negotiate / be handed a v2 manifest. Today the
    set is exactly ``['teebot-lesson-renderer.v1']`` for every device.
    """
    if isinstance(features, dict):
        renderer = features.get("renderer")
        if isinstance(renderer, str) and renderer:
            return [renderer]
        if isinstance(renderer, (list, tuple)):
            caps = [r for r in renderer if isinstance(r, str) and r]
            if caps:
                return caps
    return [RENDERER_CAPABILITY]
