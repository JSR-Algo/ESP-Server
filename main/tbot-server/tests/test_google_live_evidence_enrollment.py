import base64
import hashlib
import hmac
import json
from dataclasses import FrozenInstanceError

import pytest

from core.api.google_live_evidence_handler import GoogleLiveEvidenceHandler
from core.voice.google_live.evidence_enrollment import (
    TRANSCRIPT_NORMALIZATION_VERSION,
    EnrollmentError,
    EvidenceEnrollmentRegistry,
    TranscriptExpectation,
    normalize_transcript,
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _plan(mac="a" * 64):
    return (TranscriptExpectation(slot=1, phase="interrupt", expected_mac=mac),)


def _mac(key, value):
    return hmac.new(
        key,
        normalize_transcript(value).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def test_transcript_normalization_is_versioned_nfkc_casefold_and_space_collapsed():
    assert TRANSCRIPT_NORMALIZATION_VERSION == "google-live-transcript-nfkc-casefold.v1"
    assert normalize_transcript("  ＨÉLLO—Con!!  ") == "héllo con"


def test_registry_records_ordered_boolean_transcript_proof_without_private_material():
    key = b"k" * 32
    first = "  BẮT đầu bài học!  "
    final = "Con nói tiếp nhé"
    registry = EvidenceEnrollmentRegistry()
    _register(
        registry,
        hmac_key=key,
        transcript_plan=(
            TranscriptExpectation(1, "interrupt", _mac(key, first)),
            TranscriptExpectation(2, "post_lesson", _mac(key, final)),
        ),
    )
    registry.claim(device_id="aa:bb", client_id="robot-client", journey_id="physical.run-1")

    proof = registry.observe_transcript(
        "physical.run-1", first, phase="interrupt", observed_at=1234.5,
        response_generation=7,
    )
    report = registry.safe_snapshot("physical.run-1")

    assert proof == {
        "slot": 1,
        "phase": "interrupt",
        "chars": len(first),
        "matched": True,
        "observedAt": 1234.5,
    }
    assert report["transcriptMatchedCount"] == 1
    assert report["transcriptExpectedCount"] == 2
    assert report["transcriptObservedCount"] == 1
    assert report["transcriptMismatchCount"] == 0
    assert report["transcriptMissingCount"] == 1
    assert report["transcriptOrderingProof"] is True
    assert report["postInterruptVerdict"] is True
    assert report["postLessonVerdict"] is False
    assert report["transcriptProofs"] == [proof]
    assert report["readyToFinalize"] is False
    encoded = json.dumps(report, ensure_ascii=False)
    assert normalize_transcript(first) not in encoded.casefold()
    assert _mac(key, first) not in encoded
    assert key.hex() not in encoded


def test_registry_wrong_phase_terminally_invalidates_proof_even_if_later_sequence_matches():
    key = b"u" * 32
    registry = EvidenceEnrollmentRegistry()
    _register(
        registry,
        hmac_key=key,
        transcript_plan=(
            TranscriptExpectation(1, "interrupt", _mac(key, "Café")),
            TranscriptExpectation(2, "lesson", _mac(key, "two")),
        ),
    )
    registry.claim(device_id="aa:bb", client_id="robot-client", journey_id="physical.run-1")

    wrong_phase = registry.observe_transcript(
        "physical.run-1", "Cafe\u0301", phase="lesson", observed_at=1.0,
        response_generation=1,
    )
    later = registry.observe_transcript(
        "physical.run-1", "Cafe\u0301", phase="interrupt", observed_at=2.0,
        response_generation=1,
    )

    assert wrong_phase["matched"] is False
    assert later["matched"] is False
    report = registry.safe_snapshot("physical.run-1")
    assert report["transcriptMatchedSlots"] == []
    assert report["transcriptMismatchCount"] == 2
    assert report["transcriptOrderingProof"] is False
    assert report["readyToFinalize"] is False


def test_registry_accepts_unicode_equivalent_transcript_without_prior_mismatch():
    key = b"u" * 32
    registry = EvidenceEnrollmentRegistry()
    _register(
        registry,
        hmac_key=key,
        transcript_plan=(
            TranscriptExpectation(1, "interrupt", _mac(key, "Café")),
        ),
    )
    registry.claim(device_id="aa:bb", client_id="robot-client", journey_id="physical.run-1")

    proof = registry.observe_transcript(
        "physical.run-1", "Cafe\u0301", phase="interrupt", observed_at=2.0,
        response_generation=1,
    )

    assert proof["matched"] is True


@pytest.mark.parametrize("extra_value", ["first", "unexpected"])
def test_duplicate_or_extra_observation_terminally_invalidates_completed_sequence(extra_value):
    key = b"d" * 32
    registry = EvidenceEnrollmentRegistry()
    _register(
        registry,
        hmac_key=key,
        transcript_plan=(
            TranscriptExpectation(1, "interrupt", _mac(key, "first")),
            TranscriptExpectation(2, "post_lesson", _mac(key, "final")),
        ),
    )
    registry.claim(device_id="aa:bb", client_id="robot-client", journey_id="physical.run-1")
    registry.observe_transcript(
        "physical.run-1", "first", phase="interrupt", observed_at=1.0,
        response_generation=1,
    )
    registry.observe_transcript(
        "physical.run-1", "final", phase="post_lesson", observed_at=2.0,
        response_generation=77,
    )

    extra = registry.observe_transcript(
        "physical.run-1", extra_value, phase="interrupt", observed_at=3.0,
        response_generation=2,
    )

    assert extra["matched"] is False
    assert registry.mark_output_idle("physical.run-1", response_generation=77) is False
    report = registry.safe_snapshot("physical.run-1")
    assert report["transcriptExpectedCount"] == 2
    assert report["transcriptObservedCount"] == 3
    assert report["transcriptMatchedCount"] == 2
    assert report["transcriptMismatchCount"] == 1
    assert report["transcriptOrderingProof"] is False
    assert report["postInterruptVerdict"] is False
    assert report["postLessonVerdict"] is False
    assert report["readyToFinalize"] is False


def test_registry_requires_final_post_lesson_generation_output_idle_before_ready():
    key = b"f" * 32
    registry = EvidenceEnrollmentRegistry()
    _register(
        registry,
        hmac_key=key,
        transcript_plan=(
            TranscriptExpectation(1, "post_lesson", _mac(key, "finished")),
        ),
    )
    registry.claim(device_id="aa:bb", client_id="robot-client", journey_id="physical.run-1")

    proof = registry.observe_transcript(
        "physical.run-1", "finished", phase="post_lesson", observed_at=10.0,
        response_generation=9,
    )
    assert proof["matched"] is True
    assert registry.mark_output_idle("physical.run-1", response_generation=8) is False
    assert registry.safe_snapshot("physical.run-1")["readyToFinalize"] is False
    assert registry.mark_output_idle("physical.run-1", response_generation=9) is True
    assert registry.safe_snapshot("physical.run-1")["readyToFinalize"] is True


def test_output_idle_before_final_transcript_cannot_authorize_cleanup():
    key = b"p" * 32
    registry = EvidenceEnrollmentRegistry()
    _register(
        registry,
        hmac_key=key,
        transcript_plan=(
            TranscriptExpectation(1, "post_lesson", _mac(key, "finished")),
        ),
    )
    registry.claim(device_id="aa:bb", client_id="robot-client", journey_id="physical.run-1")

    assert registry.mark_output_idle("physical.run-1", response_generation=9) is False
    registry.observe_transcript(
        "physical.run-1", "finished", phase="post_lesson", observed_at=10.0,
        response_generation=9,
    )

    assert registry.safe_snapshot("physical.run-1")["readyToFinalize"] is False


def test_registry_mismatch_and_cancellation_never_expose_or_retain_transcript_secrets():
    key = bytearray(b"z" * 32)
    registry = EvidenceEnrollmentRegistry()
    _register(
        registry,
        hmac_key=key,
        transcript_plan=(
            TranscriptExpectation(1, "interrupt", _mac(key, "private phrase")),
        ),
    )
    registry.claim(device_id="aa:bb", client_id="robot-client", journey_id="physical.run-1")
    owned_key = registry._active["physical.run-1"].hmac_key

    proof = registry.observe_transcript(
        "physical.run-1", "different secret", phase="interrupt", observed_at=20.0,
        response_generation=2,
    )
    cancelled = registry.finalize(
        "physical.run-1", status="FAIL", failure_code="OPERATOR_CANCELLED"
    )

    assert proof["matched"] is False
    assert owned_key == bytearray(32)
    encoded = json.dumps(cancelled)
    assert "private phrase" not in encoded
    assert "different secret" not in encoded
    assert "expectedMac" not in encoded


def test_transcript_observation_cannot_bypass_expiry_with_explicit_timestamp():
    clock = Clock()
    key = b"e" * 32
    registry = EvidenceEnrollmentRegistry(clock=clock)
    _register(
        registry,
        hmac_key=key,
        ttl_sec=30,
        transcript_plan=(
            TranscriptExpectation(1, "interrupt", _mac(key, "hello")),
        ),
    )
    registry.claim(device_id="aa:bb", client_id="robot-client", journey_id="physical.run-1")
    clock.now += 31

    with pytest.raises(EnrollmentError, match="JOURNEY_NOT_FOUND"):
        registry.observe_transcript(
            "physical.run-1",
            "hello",
            phase="interrupt",
            observed_at=1000.0,
            response_generation=1,
        )


def _register(registry, **overrides):
    values = {
        "device_id": " AA:BB ",
        "client_id": " Robot-Client ",
        "journey_id": "physical.run-1",
        "transcript_plan": _plan(),
        "hmac_key": bytearray(b"k" * 32),
        "ttl_sec": 120,
    }
    values.update(overrides)
    return registry.register(**values)


def _register_candidate(registry, **overrides):
    values = {
        "device_id": " AA:BB ",
        "client_id": " Robot-Client ",
        "journey_id": "candidate-soak.20260901T010203Z.1",
        "journey_type": "conversation",
        "proof_profile": "candidate-lifecycle",
        "transcript_plan": (),
        "hmac_key": bytearray(),
        "ttl_sec": 120,
    }
    values.update(overrides)
    return registry.register(**values)


def test_registry_stores_immutable_candidate_lifecycle_claims_without_transcript_gate():
    registry = EvidenceEnrollmentRegistry()

    enrollment = _register_candidate(registry)
    snapshot = registry.safe_snapshot(enrollment.journey_id)

    assert enrollment.journey_type == "conversation"
    assert enrollment.proof_profile == "candidate-lifecycle"
    assert snapshot["journeyType"] == "conversation"
    assert snapshot["proofProfile"] == "candidate-lifecycle"
    assert snapshot["transcriptCount"] == 0
    assert snapshot["expectedCount"] == 0
    assert snapshot["transcriptExpectedCount"] == 0
    assert snapshot["transcriptObservedCount"] == 0
    assert snapshot["transcriptMatchedCount"] == 0
    assert snapshot["transcriptMissingCount"] == 0
    assert snapshot["transcriptProofEligible"] is True
    assert snapshot["readyToFinalize"] is True
    with pytest.raises(FrozenInstanceError):
        enrollment.journey_type = "bargein"
    with pytest.raises(FrozenInstanceError):
        enrollment.proof_profile = "physical-transcript"


def test_registry_defaults_legacy_enrollment_to_physical_transcript_claims():
    registry = EvidenceEnrollmentRegistry()

    enrollment = _register(registry)
    snapshot = registry.safe_snapshot(enrollment.journey_id)

    assert enrollment.journey_type == "physical"
    assert enrollment.proof_profile == "physical-transcript"
    assert snapshot["journeyType"] == "physical"
    assert snapshot["proofProfile"] == "physical-transcript"
    assert snapshot["readyToFinalize"] is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"journey_type": "unknown"},
        {"proof_profile": "unknown"},
        {"journey_type": "physical"},
        {"proof_profile": "physical-transcript"},
        {"transcript_plan": _plan()},
        {"hmac_key": bytearray(b"k" * 32)},
    ],
)
def test_registry_rejects_invalid_or_mixed_candidate_profile_payload(overrides):
    registry = EvidenceEnrollmentRegistry()

    with pytest.raises(EnrollmentError, match="INVALID_EVIDENCE"):
        _register_candidate(registry, **overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"transcript_plan": ()},
        {"hmac_key": b"short"},
        {"transcript_plan": (TranscriptExpectation(2, "interrupt", "a" * 64),)},
    ],
)
def test_registry_rejects_invalid_physical_profile_payload(overrides):
    registry = EvidenceEnrollmentRegistry()

    with pytest.raises(EnrollmentError, match="INVALID_EVIDENCE_PROFILE_PAYLOAD"):
        _register(registry, **overrides)


def test_registry_zeroizes_mutable_key_when_profile_payload_is_rejected():
    registry = EvidenceEnrollmentRegistry()
    rejected_key = bytearray(b"r" * 32)

    with pytest.raises(EnrollmentError, match="INVALID_EVIDENCE_PROFILE_PAYLOAD"):
        _register_candidate(registry, hmac_key=rejected_key)

    assert rejected_key == bytearray(32)


def test_candidate_claims_survive_claim_binding_and_safe_tombstoning_without_secrets():
    registry = EvidenceEnrollmentRegistry()
    enrollment = _register_candidate(registry)

    registry.bind_candidate_identity(
        device_id="aa:bb",
        journey_id=enrollment.journey_id,
        candidate_identity=_candidate_identity()["candidateIdentity"],
    )
    claimed_identity = registry.claim_for_scope(
        device_id="aa:bb",
        client_id="robot-client",
        journey_id=enrollment.journey_id,
    )
    claimed = registry.claim(
        device_id="aa:bb",
        client_id="robot-client",
        journey_id=enrollment.journey_id,
    )
    terminal = registry.finalize(enrollment.journey_id, status="PASS")
    retry = registry.finalize(enrollment.journey_id, status="PASS")

    assert claimed_identity == _candidate_identity()["candidateIdentity"]
    assert claimed.journey_type == "conversation"
    assert claimed.proof_profile == "candidate-lifecycle"
    assert terminal["journeyType"] == "conversation"
    assert terminal["proofProfile"] == "candidate-lifecycle"
    assert retry == terminal
    encoded = json.dumps(terminal).casefold()
    assert "expectedmac" not in encoded
    assert "hmackey" not in encoded
    assert "transcriptplan" not in encoded


def test_candidate_active_and_terminal_ownership_matching_includes_claims():
    registry = EvidenceEnrollmentRegistry()
    enrollment = _register_candidate(registry)
    registry.claim(
        device_id="aa:bb",
        client_id="robot-client",
        journey_id=enrollment.journey_id,
    )

    match = {
        "device_id": "aa:bb",
        "client_id": "robot-client",
        "journey_id": enrollment.journey_id,
        "journey_type": "conversation",
        "proof_profile": "candidate-lifecycle",
    }
    assert registry.active_claim_matches(**match) is True
    assert registry.active_claim_matches(**{**match, "journey_type": "bargein"}) is False
    assert registry.active_claim_matches(
        **{**match, "proof_profile": "physical-transcript"}
    ) is False

    registry.finalize(enrollment.journey_id, status="PASS")

    assert registry.terminal_claim_matches(**match) is True
    assert registry.terminal_claim_matches(
        **{**match, "journey_type": "bargein"}
    ) is False
    assert registry.terminal_claim_matches(
        **{**match, "proof_profile": "physical-transcript"}
    ) is False


def test_registry_binds_exact_normalized_peer_and_rejects_replacement():
    registry = EvidenceEnrollmentRegistry()
    _register(registry)

    assert registry.ota_journey("aa:bb", "robot-client") == "physical.run-1"
    assert registry.ota_journey("aa:bb", "other-client") is None
    claimed = registry.claim(
        device_id="AA:BB",
        client_id="ROBOT-CLIENT",
        journey_id="physical.run-1",
    )
    assert claimed is not None
    assert claimed.connected is True
    replacement_key = bytearray(b"r" * 32)
    with pytest.raises(EnrollmentError, match="ENROLLMENT_ACTIVE"):
        _register(registry, journey_id="physical.run-2", hmac_key=replacement_key)
    assert replacement_key == bytearray(32)


def test_registry_strict_claim_is_exact_and_single_use():
    registry = EvidenceEnrollmentRegistry()
    _register(registry)

    claimed = registry.claim_once(
        device_id="AA:BB",
        client_id="ROBOT-CLIENT",
        journey_id="physical.run-1",
    )

    assert claimed is not None
    assert claimed.connected is True
    assert registry.claim_once(
        device_id="aa:bb",
        client_id="robot-client",
        journey_id="physical.run-1",
    ) is None


def test_registry_detaches_input_key_and_returned_enrollments_from_internal_state():
    registry = EvidenceEnrollmentRegistry()
    input_key = bytearray(b"k" * 32)
    returned = _register(registry, hmac_key=input_key)

    input_key[:] = b"x" * 32
    assert returned.hmac_key == bytearray()
    assert returned.transcript_plan[0].expected_mac == ""
    for field_name, value in (
        ("device_id", "mutated-device"),
        ("client_id", "mutated-client"),
        ("journey_id", "mutated-journey"),
        ("expires_at", 0),
        ("connected", True),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(returned, field_name, value)

    snapshot = registry.safe_snapshot("physical.run-1")
    assert snapshot["connected"] is False
    assert snapshot["expiresAt"] > 0
    assert registry.ota_journey("aa:bb", "robot-client") == "physical.run-1"
    assert registry.claim(
        device_id="mutated-device",
        client_id="mutated-client",
        journey_id="mutated-journey",
    ) is None

    claimed = registry.claim(
        device_id="aa:bb",
        client_id="robot-client",
        journey_id="physical.run-1",
    )
    assert claimed is not None
    assert claimed.hmac_key == bytearray()
    assert claimed.transcript_plan[0].expected_mac == ""
    with pytest.raises(FrozenInstanceError):
        claimed.device_id = "other"
    with pytest.raises(FrozenInstanceError):
        claimed.connected = False
    claimed_again = registry.claim(
        device_id="aa:bb",
        client_id="robot-client",
        journey_id="physical.run-1",
    )
    assert claimed_again is not None
    assert claimed_again.device_id == "aa:bb"
    assert claimed_again.connected is True
    assert claimed_again.hmac_key == bytearray()
    assert registry._active["physical.run-1"].hmac_key == bytearray(b"k" * 32)


def test_registry_accepts_immutable_key_bytes_and_rejects_without_mutation_errors():
    registry = EvidenceEnrollmentRegistry(max_active=1)
    registered = _register(registry, hmac_key=b"k" * 32)

    assert registry._active["physical.run-1"].hmac_key == bytearray(b"k" * 32)
    with pytest.raises(EnrollmentError, match="CAPACITY_EXCEEDED"):
        _register(
            registry,
            device_id="other-device",
            client_id="other-client",
            journey_id="other-journey",
            hmac_key=b"r" * 32,
        )
    assert registered.hmac_key == bytearray()


def test_registry_expires_and_zeroizes_without_exposing_secrets():
    clock = Clock()
    registry = EvidenceEnrollmentRegistry(clock=clock)
    enrollment = _register(registry)
    owned_key = registry._active["physical.run-1"].hmac_key
    rendered = repr(enrollment) + repr(enrollment.transcript_plan[0])
    assert "kkkk" not in rendered
    assert "a" * 64 not in rendered

    clock.now += 121
    assert registry.ota_journey("aa:bb", "robot-client") is None
    assert owned_key == bytearray(32)
    snapshot = registry.safe_snapshot("physical.run-1")
    assert snapshot["status"] == "EXPIRED"
    assert "hmac" not in json.dumps(snapshot).lower()
    assert "a" * 64 not in json.dumps(snapshot)


def test_registry_finalization_is_safe_bounded_and_idempotent():
    registry = EvidenceEnrollmentRegistry(max_active=2, max_tombstones=2)
    _register(registry)
    owned_key = registry._active["physical.run-1"].hmac_key
    result = registry.finalize("physical.run-1", status="PASS")
    assert result == registry.finalize("physical.run-1", status="PASS")
    assert owned_key == bytearray(32)
    with pytest.raises(EnrollmentError, match="FINAL_STATUS_CONFLICT"):
        registry.finalize("physical.run-1", status="FAIL")
    _register(registry, device_id="cc:dd", journey_id="physical.run-2")
    _register(registry, device_id="ee:ff", journey_id="physical.run-3")
    with pytest.raises(EnrollmentError, match="CAPACITY_EXCEEDED"):
        _register(registry, device_id="11:22", journey_id="physical.run-4")
    registry.finalize("physical.run-2", status="FAIL")
    registry.finalize("physical.run-3", status="PASS")
    with pytest.raises(EnrollmentError, match="JOURNEY_NOT_FOUND"):
        registry.safe_snapshot("physical.run-1")


def test_registry_expiry_uses_monotonic_deadline_despite_wall_clock_rollback():
    wall = Clock()
    monotonic = Clock()
    monotonic.now = 10.0
    registry = EvidenceEnrollmentRegistry(
        wall_clock=wall,
        monotonic_clock=monotonic,
    )
    _register(registry, ttl_sec=30)
    owned_key = registry._active["physical.run-1"].hmac_key

    wall.now = -10_000.0
    monotonic.now = 41.0

    assert registry.ota_journey("aa:bb", "robot-client") is None
    assert owned_key == bytearray(32)
    assert registry.safe_snapshot("physical.run-1")["status"] == "EXPIRED"
    expired = registry.safe_snapshot("physical.run-1")
    assert expired["finalizedAt"] >= expired["expiresAt"] >= expired["createdAt"]


def test_registry_enforces_production_active_capacity_boundary():
    registry = EvidenceEnrollmentRegistry()
    for index in range(128):
        _register(
            registry,
            device_id=f"device-{index}",
            client_id=f"client-{index}",
            journey_id=f"journey-{index}",
        )

    rejected_key = bytearray(b"r" * 32)
    with pytest.raises(EnrollmentError, match="CAPACITY_EXCEEDED"):
        _register(
            registry,
            device_id="device-128",
            client_id="client-128",
            journey_id="journey-128",
            hmac_key=rejected_key,
        )
    assert rejected_key == bytearray(32)


def test_registry_retains_only_latest_256_terminal_tombstones():
    registry = EvidenceEnrollmentRegistry()
    for index in range(257):
        _register(
            registry,
            device_id=f"device-{index}",
            client_id=f"client-{index}",
            journey_id=f"journey-{index}",
        )
        registry.finalize(f"journey-{index}", status="PASS")

    with pytest.raises(EnrollmentError, match="JOURNEY_NOT_FOUND"):
        registry.safe_snapshot("journey-0")
    assert registry.safe_snapshot("journey-1")["status"] == "PASS"
    assert registry.safe_snapshot("journey-256")["status"] == "PASS"


def test_registry_expires_full_capacity_zeroizes_all_keys_and_accepts_new_entry():
    clock = Clock()
    registry = EvidenceEnrollmentRegistry(clock=clock)
    for index in range(128):
        _register(
            registry,
            device_id=f"device-{index}",
            client_id=f"client-{index}",
            journey_id=f"journey-{index}",
            ttl_sec=30,
        )
    owned_keys = [state.hmac_key for state in registry._active.values()]

    clock.now += 31
    created = _register(
        registry,
        device_id="new-device",
        client_id="new-client",
        journey_id="new-journey",
    )

    assert created.journey_id == "new-journey"
    assert all(key == bytearray(32) for key in owned_keys)
    assert registry.ota_journey("new-device", "new-client") == "new-journey"


class Request:
    def __init__(self, *, device="AA:BB", journey="physical.run-1", body=None, headers=None):
        self.match_info = {"deviceId": device, "journeyId": journey}
        self.headers = headers if headers is not None else {
            "X-Mint-Secret": "mint-secret",
            "Content-Type": "application/json",
        }
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _body(**overrides):
    value = {
        "clientId": "robot-client",
        "journeyId": "physical.run-1",
        "ttlSec": 120,
        "normalizationVersion": "google-live-transcript-nfkc-casefold.v1",
        "hmacKeyBase64": base64.b64encode(b"k" * 32).decode(),
        "transcriptPlan": [{"slot": 1, "phase": "interrupt", "expectedMac": "a" * 64}],
    }
    value.update(overrides)
    return value


def _candidate_identity(**overrides):
    value = {
        "gitSha": "a" * 40,
        "imageDigest": "sha256:" + "b" * 64,
        "firmwareIdentity": "firmware-v1",
        "fixtureSha256": "c" * 64,
        "configFingerprint": "sha256:" + "d" * 64,
    }
    value.update(overrides)
    return {"candidateIdentity": value}


@pytest.mark.asyncio
async def test_candidate_identity_binding_is_authenticated_exact_and_immutable(monkeypatch):
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")
    registry = EvidenceEnrollmentRegistry()
    _register(registry)
    handler = GoogleLiveEvidenceHandler(registry)

    bound = await handler.handle_candidate_identity_put(
        Request(body=_candidate_identity())
    )
    repeated = await handler.handle_candidate_identity_put(
        Request(body=_candidate_identity())
    )
    changed = await handler.handle_candidate_identity_put(
        Request(body=_candidate_identity(firmwareIdentity="firmware-v2"))
    )
    secret = await handler.handle_candidate_identity_put(
        Request(body=_candidate_identity(firmwareIdentity="device-AA:BB-secret"))
    )
    transcript_mac = await handler.handle_candidate_identity_put(
        Request(body=_candidate_identity(fixtureSha256="a" * 64))
    )

    assert bound.status == 200
    assert repeated.status == 200
    assert changed.status == 409
    assert secret.status == 400
    assert transcript_mac.status == 400
    encoded = bound.text + repeated.text + changed.text + secret.text + transcript_mac.text
    assert "AA:BB" not in encoded
    assert "robot-client" not in encoded
    assert "secret" not in encoded.lower()


def test_scope_claim_requires_server_bound_candidate_identity():
    registry = EvidenceEnrollmentRegistry()
    _register(registry)

    assert registry.claim_for_scope(
        device_id="aa:bb", client_id="robot-client", journey_id="physical.run-1"
    ) is None

    registry.bind_candidate_identity(
        device_id="aa:bb",
        journey_id="physical.run-1",
        candidate_identity=_candidate_identity()["candidateIdentity"],
    )
    claimed = registry.claim_for_scope(
        device_id="aa:bb", client_id="robot-client", journey_id="physical.run-1"
    )
    assert claimed == _candidate_identity()["candidateIdentity"]


@pytest.mark.asyncio
async def test_handler_auth_validation_safe_get_and_cancel(monkeypatch):
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")
    registry = EvidenceEnrollmentRegistry()
    handler = GoogleLiveEvidenceHandler(registry)

    unauthorized = await handler.handle_post(Request(headers={}, body=_body()))
    non_json = await handler.handle_post(Request(body=RuntimeError("decoder detail")))
    invalid = await handler.handle_post(Request(body={**_body(), "unknown": True}))
    created = await handler.handle_post(Request(body=_body()))
    fetched = await handler.handle_get(Request())
    cancelled = await handler.handle_delete(Request())
    cancelled_again = await handler.handle_delete(Request())
    cancelled_snapshot = await handler.handle_get(Request())
    wrong_device = await handler.handle_delete(Request(device="CC:DD"))

    assert unauthorized.status == 401
    assert json.loads(non_json.text) == {
        "error": "INVALID_REQUEST",
        "message": "Invalid Google Live evidence enrollment request",
    }
    assert json.loads(invalid.text)["error"] == "INVALID_REQUEST"
    assert created.status == 201
    assert json.loads(created.text) == {
        "data": {"registered": True, "journeyId": "physical.run-1"}
    }
    assert "expectedMac" not in created.text and "hmac" not in created.text.lower()
    assert json.loads(fetched.text)["journeyId"] == "physical.run-1"
    assert json.loads(cancelled.text)["failureCode"] == "OPERATOR_CANCELLED"
    assert cancelled_again.text == cancelled.text
    assert json.loads(cancelled_snapshot.text)["failureCode"] == "OPERATOR_CANCELLED"
    assert wrong_device.status == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "device", "journey"),
    [
        ("handle_get", " AA:BB ", "physical.run-1"),
        ("handle_get", "AA:BB", " physical.run-1 "),
        ("handle_delete", "AA/BB", "physical.run-1"),
        ("handle_delete", "AA:BB", "physical/run-1"),
    ],
)
async def test_handler_rejects_unsafe_path_ids_without_reading_or_cancelling(
    monkeypatch, method, device, journey
):
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")
    registry = EvidenceEnrollmentRegistry()
    _register(registry)
    handler = GoogleLiveEvidenceHandler(registry)

    response = await getattr(handler, method)(Request(device=device, journey=journey))

    assert response.status == 400
    assert json.loads(response.text) == {
        "error": "INVALID_REQUEST",
        "message": "Invalid Google Live evidence path identity",
    }
    assert registry.ota_journey("aa:bb", "robot-client") == "physical.run-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"ttlSec": 29},
        {"normalizationVersion": "wrong"},
        {"hmacKeyBase64": base64.b64encode(b"short").decode()},
        {"hmacKeyBase64": base64.b64encode(b"k" * 32).decode() + "\n"},
        {"transcriptPlan": []},
        {"transcriptPlan": [{"slot": 2, "phase": "interrupt", "expectedMac": "a" * 64}]},
        {"transcriptPlan": [{"slot": True, "phase": "interrupt", "expectedMac": "a" * 64}]},
        {"transcriptPlan": [{"slot": 1, "phase": "bad", "expectedMac": "a" * 64}]},
        {"transcriptPlan": [{"slot": 1, "phase": "interrupt", "expectedMac": "A" * 64}]},
    ],
)
async def test_handler_rejects_invalid_enrollment_payloads(monkeypatch, change):
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")
    response = await GoogleLiveEvidenceHandler(EvidenceEnrollmentRegistry()).handle_post(
        Request(body={**_body(), **change})
    )
    assert response.status == 400
    assert json.loads(response.text) == {
        "error": "INVALID_REQUEST",
        "message": "Invalid Google Live evidence enrollment request",
    }
