import base64
import hashlib
import hmac
import json
from dataclasses import FrozenInstanceError

import pytest

from core.api.google_live_evidence_handler import GoogleLiveEvidenceHandler
from core.voice.google_live import evidence_enrollment
from core.voice.google_live.evidence_enrollment import (
    TRANSCRIPT_NORMALIZATION_VERSION,
    CandidateIntentExpectation,
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
    return (TranscriptExpectation(slot=1, phase="post_lesson", expected_mac=mac),)


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
            TranscriptExpectation(2, "post_lesson", _mac(key, "two")),
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
            TranscriptExpectation(2, "post_lesson", _mac(key, "done")),
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
            TranscriptExpectation(2, "post_lesson", _mac(key, "done")),
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
            TranscriptExpectation(2, "post_lesson", _mac(key, "done")),
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


def _register_bargein_semantic(registry, *, key=None, **overrides):
    key = bytearray(b"s" * 32) if key is None else key
    values = {
        "journey_id": "candidate-soak.20260902T010203Z.18",
        "journey_type": "bargein",
        "semantic_kind": "bargein-intent",
        "intent_plan": (
            CandidateIntentExpectation(1, "initial", _mac(key, "First intent")),
            CandidateIntentExpectation(2, "newest", _mac(key, "Newest intent")),
        ),
        "semantic_hmac_key": key,
    }
    values.update(overrides)
    return _register_candidate(registry, **values)


def test_candidate_intent_normalization_reuses_versioned_nfkc_casefold_contract():
    assert (
        getattr(evidence_enrollment, "CANDIDATE_INTENT_NORMALIZATION_VERSION", None)
        == "google-live-candidate-intent-nfkc-casefold.v1"
    )
    assert normalize_transcript("  ＦＩＲＳＴ—Intent!!  ") == "first intent"


def test_registry_matches_exact_ordered_candidate_intents_without_exposing_secrets():
    registry = EvidenceEnrollmentRegistry()
    enrollment = _register_bargein_semantic(registry)
    registry.claim(
        device_id="aa:bb",
        client_id="robot-client",
        journey_id=enrollment.journey_id,
    )

    initial = registry.observe_candidate_intent(
        enrollment.journey_id, "ＦＩＲＳＴ intent", role="initial"
    )
    newest = registry.observe_candidate_intent(
        enrollment.journey_id, "Newest intent", role="newest"
    )
    snapshot = registry.safe_snapshot(enrollment.journey_id)

    assert initial == {
        "slot": 1,
        "role": "initial",
        "chars": len("ＦＩＲＳＴ intent"),
        "matched": True,
    }
    assert newest == {
        "slot": 2,
        "role": "newest",
        "chars": len("Newest intent"),
        "matched": True,
    }
    assert snapshot["semanticProofKind"] == "bargein-intent"
    assert snapshot["semanticExpectedCount"] == 2
    assert snapshot["semanticObservedCount"] == 2
    assert snapshot["semanticMatchCount"] == 2
    assert snapshot["semanticMismatchCount"] == 0
    assert snapshot["semanticOrderingValid"] is True
    assert snapshot["semanticEligible"] is True
    assert snapshot["latestIntentMatched"] is True
    encoded = json.dumps(snapshot).casefold()
    for private in ("expectedmac", "hmackey", "first intent", "newest intent"):
        assert private not in encoded


def test_candidate_intent_matching_uses_constant_time_digest_comparison(monkeypatch):
    compared = []
    real_compare = hmac.compare_digest

    def recording_compare(left, right):
        compared.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(evidence_enrollment.hmac, "compare_digest", recording_compare)
    registry = EvidenceEnrollmentRegistry()
    enrollment = _register_bargein_semantic(registry)
    registry.claim(
        device_id="aa:bb",
        client_id="robot-client",
        journey_id=enrollment.journey_id,
    )

    proof = registry.observe_candidate_intent(
        enrollment.journey_id, "First intent", role="initial"
    )

    assert proof["matched"] is True
    assert len(compared) == 1
    assert all(isinstance(value, str) and len(value) == 64 for value in compared[0])


def test_registry_detaches_semantic_key_and_plan_then_zeroizes_owned_key_on_finalize():
    registry = EvidenceEnrollmentRegistry()
    input_key = bytearray(b"s" * 32)
    enrollment = _register_bargein_semantic(registry, key=input_key)
    owned_key = registry._active[enrollment.journey_id].semantic_hmac_key

    input_key[:] = b"x" * 32
    assert enrollment.semantic_hmac_key == bytearray()
    assert [item.expected_mac for item in enrollment.intent_plan] == ["", ""]
    assert owned_key == bytearray(b"s" * 32)

    terminal = registry.finalize(enrollment.journey_id, status="FAIL")

    assert owned_key == bytearray(32)
    encoded = json.dumps(terminal).casefold()
    assert "expectedmac" not in encoded and "hmackey" not in encoded


def test_semantic_match_remains_nonterminal_and_does_not_change_lifecycle_readiness():
    registry = EvidenceEnrollmentRegistry()
    enrollment = _register_bargein_semantic(registry)
    registry.claim(
        device_id="aa:bb",
        client_id="robot-client",
        journey_id=enrollment.journey_id,
    )
    before = registry.safe_snapshot(enrollment.journey_id)

    registry.observe_candidate_intent(
        enrollment.journey_id, "First intent", role="initial"
    )
    registry.observe_candidate_intent(
        enrollment.journey_id, "Newest intent", role="newest"
    )
    after = registry.safe_snapshot(enrollment.journey_id)

    assert before["status"] == after["status"] == "ACTIVE"
    assert before["readyToFinalize"] is after["readyToFinalize"]
    assert after["latestIntentMatched"] is True


@pytest.mark.parametrize("duplicate_event", ["start", "end"])
def test_candidate_replacement_duplicate_lifecycle_event_fails_sticky(duplicate_event):
    registry = EvidenceEnrollmentRegistry()
    enrollment = _register_bargein_semantic(registry)
    registry.claim(
        device_id=enrollment.device_id,
        client_id=enrollment.client_id,
        journey_id=enrollment.journey_id,
    )
    registry.observe_candidate_intent(enrollment.journey_id, "First intent", role="initial")
    registry.observe_candidate_intent(
        enrollment.journey_id,
        "Newest intent",
        role="newest",
        response_generation=7,
        active_old_output=True,
    )
    assert registry.record_candidate_interrupt(
        enrollment.journey_id, old_generation=7, new_generation=8
    )
    assert registry.record_candidate_response_started(
        enrollment.journey_id, response_generation=8
    )
    if duplicate_event == "start":
        assert not registry.record_candidate_response_started(
            enrollment.journey_id, response_generation=8
        )
    else:
        assert registry.record_candidate_response_completed(
            enrollment.journey_id, response_generation=8
        )
        assert not registry.record_candidate_response_completed(
            enrollment.journey_id, response_generation=8
        )

    snapshot = registry.safe_snapshot(enrollment.journey_id)
    assert snapshot["semanticEligible"] is False
    assert snapshot["semanticOwnershipReady"] is False


def test_candidate_replacement_completion_before_start_fails_sticky():
    registry = EvidenceEnrollmentRegistry()
    enrollment = _register_bargein_semantic(registry)
    registry.claim(
        device_id=enrollment.device_id,
        client_id=enrollment.client_id,
        journey_id=enrollment.journey_id,
    )
    registry.observe_candidate_intent(enrollment.journey_id, "First intent", role="initial")
    registry.observe_candidate_intent(
        enrollment.journey_id,
        "Newest intent",
        role="newest",
        response_generation=7,
        active_old_output=True,
    )
    assert registry.record_candidate_interrupt(
        enrollment.journey_id, old_generation=7, new_generation=8
    )

    assert not registry.record_candidate_response_completed(
        enrollment.journey_id, response_generation=8
    )

    snapshot = registry.safe_snapshot(enrollment.journey_id)
    assert snapshot["semanticEligible"] is False
    assert snapshot["semanticOwnershipReady"] is False


@pytest.mark.parametrize(
    "observations",
    [
        [("newest", "Newest intent")],
        [("initial", "wrong"), ("initial", "First intent")],
        [("initial", "First intent"), ("initial", "First intent")],
        [
            ("initial", "First intent"),
            ("newest", "Newest intent"),
            ("newest", "Newest intent"),
        ],
    ],
)
def test_candidate_intent_mismatch_duplicate_reorder_and_extra_are_sticky_fail_closed(
    observations,
):
    registry = EvidenceEnrollmentRegistry()
    enrollment = _register_bargein_semantic(registry)
    registry.claim(
        device_id="aa:bb",
        client_id="robot-client",
        journey_id=enrollment.journey_id,
    )

    proofs = [
        registry.observe_candidate_intent(enrollment.journey_id, value, role=role)
        for role, value in observations
    ]
    snapshot = registry.safe_snapshot(enrollment.journey_id)

    assert proofs[-1]["matched"] is False
    assert snapshot["semanticEligible"] is False
    assert snapshot["semanticOrderingValid"] is False
    assert snapshot["semanticMismatchCount"] >= 1
    assert snapshot["latestIntentMatched"] is False


def test_quiet_semantic_claim_is_safe_and_survives_tombstoning_without_key_material():
    registry = EvidenceEnrollmentRegistry()
    enrollment = _register_candidate(
        registry,
        journey_type="quiet",
        semantic_kind="quiet",
        quiet_mode="robot_speaking",
    )

    active = registry.safe_snapshot(enrollment.journey_id)
    terminal = registry.finalize(enrollment.journey_id, status="FAIL")

    assert active["semanticProofKind"] == "quiet"
    assert active["quietMode"] == "robot_speaking"
    assert terminal["semanticProofKind"] == "quiet"
    assert terminal["quietMode"] == "robot_speaking"
    encoded = json.dumps(terminal).casefold()
    assert "hmackey" not in encoded and "expectedmac" not in encoded


@pytest.mark.parametrize(
    "overrides",
    [
        {"semantic_kind": "bargein-intent"},
        {"semantic_hmac_key": bytearray(b"s" * 32)},
        {
            "intent_plan": (
                CandidateIntentExpectation(1, "initial", "a" * 64),
            )
        },
        {
            "journey_type": "quiet",
            "semantic_kind": "quiet",
            "quiet_mode": "invalid",
        },
        {"journey_type": "quiet", "semantic_kind": "quiet", "quiet_mode": "silence", "semantic_hmac_key": bytearray(b"s" * 32)},
        {"journey_type": "bargein", "semantic_kind": "quiet", "quiet_mode": "silence"},
        {"journey_type": "conversation", "semantic_kind": "quiet", "quiet_mode": "silence"},
    ],
)
def test_registry_rejects_incompatible_candidate_semantic_shapes(overrides):
    registry = EvidenceEnrollmentRegistry()

    with pytest.raises(EnrollmentError, match="INVALID_EVIDENCE_PROFILE_PAYLOAD"):
        _register_candidate(registry, **overrides)


@pytest.mark.parametrize("journey_type", ["bargein", "quiet"])
def test_registry_preserves_nonsemantic_candidate_bargein_and_quiet(journey_type):
    registry = EvidenceEnrollmentRegistry()

    enrollment = _register_candidate(registry, journey_type=journey_type)
    snapshot = registry.safe_snapshot(enrollment.journey_id)

    assert enrollment.semantic_kind == "none"
    assert "semanticProofKind" not in snapshot


def test_registry_zeroizes_semantic_key_on_validation_and_iterator_failures():
    class ExplodingPlan:
        def __iter__(self):
            raise RuntimeError("unexpected semantic iterator failure")

    invalid_key = bytearray(b"i" * 32)
    with pytest.raises(EnrollmentError, match="INVALID_EVIDENCE_PROFILE_PAYLOAD"):
        _register_candidate(
            EvidenceEnrollmentRegistry(),
            journey_type="bargein",
            semantic_kind="bargein-intent",
            intent_plan=((2, "initial", "a" * 64),),
            semantic_hmac_key=invalid_key,
        )
    assert invalid_key == bytearray(32)

    exploding_key = bytearray(b"e" * 32)
    with pytest.raises(RuntimeError, match="unexpected semantic iterator failure"):
        _register_candidate(
            EvidenceEnrollmentRegistry(),
            journey_type="bargein",
            semantic_kind="bargein-intent",
            intent_plan=ExplodingPlan(),
            semantic_hmac_key=exploding_key,
        )
    assert exploding_key == bytearray(32)


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


def test_registry_rejects_physical_plan_without_final_post_lesson():
    registry = EvidenceEnrollmentRegistry()

    with pytest.raises(EnrollmentError, match="INVALID_EVIDENCE_PROFILE_PAYLOAD"):
        _register(
            registry,
            transcript_plan=(TranscriptExpectation(1, "interrupt", "a" * 64),),
        )


@pytest.mark.parametrize(
    ("field", "malformed"),
    [
        ("journey_type", []),
        ("journey_type", {}),
        ("journey_type", 1),
        ("proof_profile", []),
        ("proof_profile", {}),
        ("proof_profile", 1),
    ],
)
def test_registry_normalizes_malformed_claim_types_and_zeroizes_key(field, malformed):
    registry = EvidenceEnrollmentRegistry()
    rejected_key = bytearray(b"r" * 32)

    with pytest.raises(EnrollmentError, match="INVALID_EVIDENCE_CLAIMS"):
        _register(registry, hmac_key=rejected_key, **{field: malformed})

    assert rejected_key == bytearray(32)


@pytest.mark.parametrize(
    "malformed_plan",
    [
        7,
        (object(),),
        (TranscriptExpectation([], "post_lesson", "a" * 64),),
        (TranscriptExpectation(True, "post_lesson", "a" * 64),),
        (TranscriptExpectation("1", "post_lesson", "a" * 64),),
        (TranscriptExpectation(1, [], "a" * 64),),
        (TranscriptExpectation(1, "post_lesson", 7),),
        (TranscriptExpectation(1, "post_lesson", {}),),
    ],
)
def test_registry_normalizes_malformed_transcript_plan_and_zeroizes_key(
    malformed_plan,
):
    registry = EvidenceEnrollmentRegistry()
    rejected_key = bytearray(b"r" * 32)

    with pytest.raises(EnrollmentError, match="INVALID_EVIDENCE_PROFILE_PAYLOAD"):
        _register(
            registry,
            transcript_plan=malformed_plan,
            hmac_key=rejected_key,
        )

    assert rejected_key == bytearray(32)


def test_registry_zeroizes_key_before_preserving_unexpected_plan_iterator_error():
    class ExplodingPlan:
        def __iter__(self):
            raise RuntimeError("unexpected iterator failure")

    registry = EvidenceEnrollmentRegistry()
    rejected_key = bytearray(b"r" * 32)

    with pytest.raises(RuntimeError, match="unexpected iterator failure"):
        _register(
            registry,
            transcript_plan=ExplodingPlan(),
            hmac_key=rejected_key,
        )

    assert rejected_key == bytearray(32)


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
        "transcriptPlan": [{"slot": 1, "phase": "post_lesson", "expectedMac": "a" * 64}],
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
