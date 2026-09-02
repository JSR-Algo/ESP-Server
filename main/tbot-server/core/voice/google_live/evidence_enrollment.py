from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import wraps
from typing import Literal

TRANSCRIPT_NORMALIZATION_VERSION = "google-live-transcript-nfkc-casefold.v1"
CANDIDATE_INTENT_NORMALIZATION_VERSION = (
    "google-live-candidate-intent-nfkc-casefold.v1"
)
PHYSICAL_TRANSCRIPT_PROFILE = "physical-transcript"
CANDIDATE_LIFECYCLE_PROFILE = "candidate-lifecycle"
PHYSICAL_JOURNEY_TYPES = frozenset({"physical"})
CANDIDATE_JOURNEY_TYPES = frozenset(
    {
        "conversation",
        "bargein",
        "quiet",
        "quiet_padding",
        "reopen",
        "reconnect",
        "lesson",
        "conversation_after_lesson",
        "websocket",
    }
)


def normalize_transcript(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(
        "".join(character if character.isalnum() else " " for character in normalized).split()
    )


def normalize_peer_id(value: str) -> str:
    return str(value or "").strip().lower()


class EnrollmentError(ValueError):
    pass


def validate_evidence_claims(journey_type: str, proof_profile: str) -> None:
    if not isinstance(journey_type, str) or not isinstance(proof_profile, str):
        raise EnrollmentError("INVALID_EVIDENCE_CLAIMS")
    if (
        proof_profile == PHYSICAL_TRANSCRIPT_PROFILE
        and journey_type in PHYSICAL_JOURNEY_TYPES
    ):
        return
    if (
        proof_profile == CANDIDATE_LIFECYCLE_PROFILE
        and journey_type in CANDIDATE_JOURNEY_TYPES
    ):
        return
    raise EnrollmentError("INVALID_EVIDENCE_CLAIMS")


def _synchronized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return locked


@dataclass(frozen=True, slots=True)
class TranscriptExpectation:
    slot: int
    phase: Literal["interrupt", "lesson", "post_lesson"]
    expected_mac: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CandidateIntentExpectation:
    slot: int
    role: Literal["initial", "newest"]
    expected_mac: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CandidateQuietProof:
    mode: Literal["silence", "robot_speaking"]


@dataclass(frozen=True, slots=True)
class EvidenceEnrollment:
    """Detached public enrollment view; ``hmac_key`` is always redacted."""

    device_id: str
    client_id: str
    journey_id: str
    journey_type: str
    proof_profile: str
    transcript_plan: tuple[TranscriptExpectation, ...]
    hmac_key: bytearray = field(repr=False)
    expires_at: float
    semantic_kind: Literal["none", "bargein-intent", "quiet"] = "none"
    intent_plan: tuple[CandidateIntentExpectation, ...] = ()
    quiet_mode: str | None = None
    semantic_hmac_key: bytearray = field(default_factory=bytearray, repr=False)
    connected: bool = False
    finalized: bool = False


@dataclass(slots=True)
class _EvidenceEnrollmentState:
    device_id: str
    client_id: str
    journey_id: str
    journey_type: str
    proof_profile: str
    transcript_plan: tuple[TranscriptExpectation, ...]
    hmac_key: bytearray = field(repr=False)
    created_at: float
    expires_at: float
    monotonic_deadline: float = field(repr=False)
    semantic_kind: Literal["none", "bargein-intent", "quiet"] = "none"
    intent_plan: tuple[CandidateIntentExpectation, ...] = ()
    quiet_mode: str | None = None
    semantic_hmac_key: bytearray = field(default_factory=bytearray, repr=False)
    connected: bool = False
    finalized: bool = False
    candidate_identity: dict[str, str] | None = field(default=None, repr=False)
    transcript_proofs: list[dict[str, object]] = field(default_factory=list, repr=False)
    transcript_matched_count: int = 0
    final_response_generation: int | None = field(default=None, repr=False)
    output_idle_generation: int | None = field(default=None, repr=False)
    transcript_proof_eligible: bool = True
    transcript_observed_count: int = 0
    transcript_mismatch_count: int = 0
    semantic_matched_count: int = 0
    semantic_observed_count: int = 0
    semantic_mismatch_count: int = 0
    semantic_eligible: bool = True
    semantic_initial_observed_generation: int | None = field(default=None, repr=False)
    semantic_initial_response_generation: int | None = field(default=None, repr=False)
    semantic_newest_observed_generation: int | None = field(default=None, repr=False)
    semantic_interrupted_old_generation: int | None = field(default=None, repr=False)
    semantic_replacement_generation: int | None = field(default=None, repr=False)
    semantic_replacement_started: bool = False
    semantic_replacement_completed: bool = False
    semantic_stale_old_audio_count: int = 0
    quiet_setup_turn_consumed: bool = False
    quiet_setup_response_generation: int | None = field(default=None, repr=False)
    quiet_response_started_generation: int | None = field(default=None, repr=False)
    quiet_response_completed_generation: int | None = field(default=None, repr=False)
    quiet_semantic_eligible: bool = True


class EvidenceEnrollmentRegistry:
    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
        max_active: int = 128,
        max_tombstones: int = 256,
    ):
        if clock is not None:
            wall_clock = clock
            monotonic_clock = clock
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._max_active = max_active
        self._max_tombstones = max_tombstones
        self._lock = threading.RLock()
        self._active: dict[str, _EvidenceEnrollmentState] = {}
        self._tombstones: OrderedDict[str, dict] = OrderedDict()
        self._terminal_peer_digests: dict[str, bytes] = {}
        self._terminal_client_digests: dict[str, bytes] = {}
        self._peer_digest_key = os.urandom(32)

    @_synchronized
    def register(
        self,
        *,
        device_id: str,
        client_id: str,
        journey_id: str,
        transcript_plan: tuple[TranscriptExpectation, ...],
        hmac_key: bytes | bytearray,
        ttl_sec: int,
        journey_type: str = "physical",
        proof_profile: str = PHYSICAL_TRANSCRIPT_PROFILE,
        semantic_kind: Literal["none", "bargein-intent", "quiet"] = "none",
        intent_plan: tuple[CandidateIntentExpectation, ...] = (),
        quiet_mode: str | None = None,
        semantic_hmac_key: bytes | bytearray = b"",
    ) -> EvidenceEnrollment:
        owned_key: bytearray | None = None
        owned_semantic_key: bytearray | None = None
        try:
            wall_now = self._prepare()
            monotonic_now = self._monotonic_clock()
            device_id = normalize_peer_id(device_id)
            client_id = normalize_peer_id(client_id)
            try:
                transcript_plan = tuple(transcript_plan)
            except TypeError as exc:
                if not hasattr(transcript_plan, "__iter__"):
                    raise EnrollmentError(
                        "INVALID_EVIDENCE_PROFILE_PAYLOAD"
                    ) from exc
                raise
            try:
                intent_plan = tuple(intent_plan)
            except TypeError as exc:
                if not hasattr(intent_plan, "__iter__"):
                    raise EnrollmentError(
                        "INVALID_EVIDENCE_PROFILE_PAYLOAD"
                    ) from exc
                raise
            self._validate_profile_payload(
                journey_type=journey_type,
                proof_profile=proof_profile,
                transcript_plan=transcript_plan,
                hmac_key=hmac_key,
                semantic_kind=semantic_kind,
                intent_plan=intent_plan,
                quiet_mode=quiet_mode,
                semantic_hmac_key=semantic_hmac_key,
            )
            error = None
            if journey_id in self._active or journey_id in self._tombstones:
                error = "JOURNEY_REUSED"
            elif any(
                item.device_id == device_id and item.client_id == client_id
                for item in self._active.values()
            ):
                error = "ENROLLMENT_ACTIVE"
            elif len(self._active) >= self._max_active:
                error = "CAPACITY_EXCEEDED"
            if error is not None:
                raise EnrollmentError(error)
            owned_key = bytearray(hmac_key)
            owned_semantic_key = bytearray(semantic_hmac_key)
            enrollment = _EvidenceEnrollmentState(
                device_id=device_id,
                client_id=client_id,
                journey_id=journey_id,
                journey_type=journey_type,
                proof_profile=proof_profile,
                transcript_plan=transcript_plan,
                hmac_key=owned_key,
                created_at=wall_now,
                expires_at=wall_now + ttl_sec,
                monotonic_deadline=monotonic_now + ttl_sec,
                semantic_kind=semantic_kind,
                intent_plan=intent_plan,
                quiet_mode=quiet_mode,
                semantic_hmac_key=owned_semantic_key,
            )
            view = self._view(enrollment)
        except Exception:
            if isinstance(hmac_key, bytearray):
                self._zeroize(hmac_key)
            if owned_key is not None:
                self._zeroize(owned_key)
            if isinstance(semantic_hmac_key, bytearray):
                self._zeroize(semantic_hmac_key)
            if owned_semantic_key is not None:
                self._zeroize(owned_semantic_key)
            raise
        self._active[journey_id] = enrollment
        return view

    @_synchronized
    def ota_journey(self, device_id: str, client_id: str) -> str | None:
        self._prepare()
        device_id = normalize_peer_id(device_id)
        client_id = normalize_peer_id(client_id)
        for enrollment in self._active.values():
            if enrollment.device_id == device_id and enrollment.client_id == client_id:
                return enrollment.journey_id
        return None

    @_synchronized
    def claim(self, *, device_id: str, client_id: str, journey_id: str) -> EvidenceEnrollment | None:
        self._prepare()
        enrollment = self._active.get(journey_id)
        if enrollment is None:
            return None
        if (
            enrollment.device_id != normalize_peer_id(device_id)
            or enrollment.client_id != normalize_peer_id(client_id)
        ):
            return None
        enrollment.connected = True
        return self._view(enrollment)

    @_synchronized
    def claim_once(
        self, *, device_id: str, client_id: str, journey_id: str
    ) -> EvidenceEnrollment | None:
        self._prepare()
        enrollment = self._active.get(journey_id)
        if enrollment is None or enrollment.connected:
            return None
        if (
            enrollment.device_id != normalize_peer_id(device_id)
            or enrollment.client_id != normalize_peer_id(client_id)
        ):
            return None
        enrollment.connected = True
        return self._view(enrollment)

    @_synchronized
    def bind_candidate_identity(
        self,
        *,
        device_id: str,
        journey_id: str,
        candidate_identity: Mapping[str, str],
    ) -> dict[str, str]:
        self._prepare()
        enrollment = self._active.get(journey_id)
        if enrollment is None or enrollment.device_id != normalize_peer_id(device_id):
            raise EnrollmentError("JOURNEY_NOT_FOUND")
        identity = dict(candidate_identity)
        rendered = json.dumps(identity, sort_keys=True).casefold()
        private_values = (
            enrollment.device_id,
            enrollment.client_id,
            enrollment.hmac_key.hex(),
            base64.b64encode(enrollment.hmac_key).decode("ascii"),
            *(item.expected_mac for item in enrollment.transcript_plan),
            enrollment.semantic_hmac_key.hex(),
            base64.b64encode(enrollment.semantic_hmac_key).decode("ascii"),
            *(item.expected_mac for item in enrollment.intent_plan),
        )
        if any(value and value.casefold() in rendered for value in private_values) or any(
            token in rendered
            for token in ("secret", "password", "authorization", "bearer", "hmac")
        ):
            raise EnrollmentError("INVALID_CANDIDATE_IDENTITY")
        if enrollment.connected and enrollment.candidate_identity is None:
            raise EnrollmentError("CANDIDATE_IDENTITY_CONFLICT")
        if enrollment.candidate_identity is not None:
            if enrollment.candidate_identity != identity:
                raise EnrollmentError("CANDIDATE_IDENTITY_CONFLICT")
            return dict(enrollment.candidate_identity)
        enrollment.candidate_identity = identity
        return dict(identity)

    @_synchronized
    def claim_for_scope(
        self, *, device_id: str, client_id: str, journey_id: str
    ) -> dict[str, str] | None:
        self._prepare()
        enrollment = self._active.get(journey_id)
        if (
            enrollment is None
            or enrollment.connected
            or enrollment.candidate_identity is None
            or enrollment.device_id != normalize_peer_id(device_id)
            or enrollment.client_id != normalize_peer_id(client_id)
        ):
            return None
        enrollment.connected = True
        return dict(enrollment.candidate_identity)

    @_synchronized
    def active_claim_matches(
        self,
        *,
        device_id: str,
        client_id: str,
        journey_id: str,
        journey_type: str | None = None,
        proof_profile: str | None = None,
    ) -> bool:
        self._prepare()
        enrollment = self._active.get(journey_id)
        return bool(
            enrollment is not None
            and enrollment.connected
            and enrollment.device_id == normalize_peer_id(device_id)
            and enrollment.client_id == normalize_peer_id(client_id)
            and (journey_type is None or enrollment.journey_type == journey_type)
            and (proof_profile is None or enrollment.proof_profile == proof_profile)
        )

    @_synchronized
    def terminal_claim_matches(
        self,
        *,
        device_id: str,
        client_id: str,
        journey_id: str,
        journey_type: str | None = None,
        proof_profile: str | None = None,
    ) -> bool:
        self._prepare()
        tombstone = self._tombstones.get(journey_id)
        return bool(
            tombstone is not None
            and tombstone.get("status") in ("PASS", "FAIL")
            and self._terminal_peer_digests.get(journey_id)
            == self._peer_digest(device_id)
            and self._terminal_client_digests.get(journey_id)
            == self._peer_digest(client_id)
            and (journey_type is None or tombstone.get("journeyType") == journey_type)
            and (proof_profile is None or tombstone.get("proofProfile") == proof_profile)
        )

    def claimed_peer_matches(
        self, *, device_id: str, client_id: str, journey_id: str
    ) -> bool:
        return self.active_claim_matches(
            device_id=device_id,
            client_id=client_id,
            journey_id=journey_id,
        )

    @_synchronized
    def next_transcript_phase(self, journey_id: str) -> str | None:
        self._prepare()
        enrollment = self._active.get(journey_id)
        if enrollment is None or enrollment.transcript_matched_count >= len(
            enrollment.transcript_plan
        ):
            return None
        return enrollment.transcript_plan[enrollment.transcript_matched_count].phase

    @_synchronized
    def observe_transcript(
        self,
        journey_id: str,
        value: str,
        *,
        phase: Literal["interrupt", "lesson", "post_lesson"],
        observed_at: float | None = None,
        response_generation: int | None = None,
    ) -> dict[str, object]:
        wall_now = self._prepare()
        now = wall_now if observed_at is None else float(observed_at)
        enrollment = self._active.get(journey_id)
        if enrollment is None or not enrollment.connected:
            raise EnrollmentError("JOURNEY_NOT_FOUND")
        if enrollment.proof_profile != PHYSICAL_TRANSCRIPT_PROFILE:
            raise EnrollmentError("INVALID_EVIDENCE_PROFILE_PAYLOAD")
        if phase not in ("interrupt", "lesson", "post_lesson"):
            raise EnrollmentError("INVALID_TRANSCRIPT_PHASE")
        next_index = enrollment.transcript_matched_count
        expectation = (
            enrollment.transcript_plan[next_index]
            if next_index < len(enrollment.transcript_plan)
            else None
        )
        observed_mac = hmac.new(
            enrollment.hmac_key,
            normalize_transcript(value).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        cryptographic_match = bool(
            expectation is not None
            and expectation.phase == phase
            and hmac.compare_digest(observed_mac, expectation.expected_mac)
        )
        matched = bool(enrollment.transcript_proof_eligible and cryptographic_match)
        slot = (
            expectation.slot
            if expectation is not None
            else len(enrollment.transcript_plan) + 1
        )
        proof: dict[str, object] = {
            "slot": slot,
            "phase": phase,
            "chars": len(str(value or "")),
            "matched": matched,
            "observedAt": now,
        }
        enrollment.transcript_observed_count += 1
        if len(enrollment.transcript_proofs) < 128:
            enrollment.transcript_proofs.append(dict(proof))
        if matched:
            enrollment.transcript_matched_count += 1
            if (
                enrollment.transcript_matched_count == len(enrollment.transcript_plan)
                and expectation.phase == "post_lesson"
                and isinstance(response_generation, int)
                and not isinstance(response_generation, bool)
            ):
                enrollment.final_response_generation = response_generation
        else:
            enrollment.transcript_proof_eligible = False
            enrollment.transcript_mismatch_count += 1
            enrollment.final_response_generation = None
            enrollment.output_idle_generation = None
        return proof

    @_synchronized
    def observe_candidate_intent(
        self,
        journey_id: str,
        value: str,
        *,
        role: Literal["initial", "newest"],
        response_generation: int | None = None,
        active_old_output: bool = False,
    ) -> dict[str, object]:
        self._prepare()
        enrollment = self._active.get(journey_id)
        if enrollment is None or not enrollment.connected:
            raise EnrollmentError("JOURNEY_NOT_FOUND")
        if enrollment.semantic_kind != "bargein-intent":
            raise EnrollmentError("INVALID_EVIDENCE_PROFILE_PAYLOAD")
        safe_role = role if role in ("initial", "newest") else "invalid"
        next_index = enrollment.semantic_matched_count
        expectation = (
            enrollment.intent_plan[next_index]
            if next_index < len(enrollment.intent_plan)
            else None
        )
        observed_mac = hmac.new(
            enrollment.semantic_hmac_key,
            normalize_transcript(value).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        cryptographic_match = bool(
            expectation is not None
            and expectation.role == safe_role
            and hmac.compare_digest(observed_mac, expectation.expected_mac)
        )
        generation_valid = bool(
            isinstance(response_generation, int)
            and not isinstance(response_generation, bool)
            and response_generation >= 0
        )
        ownership_valid = bool(
            generation_valid
            and (
                safe_role == "initial"
                or (
                    safe_role == "newest"
                    and active_old_output
                    and response_generation
                    == enrollment.semantic_initial_response_generation
                )
            )
        )
        matched = bool(
            enrollment.semantic_eligible
            and cryptographic_match
            and ownership_valid
        )
        proof: dict[str, object] = {
            "slot": (
                expectation.slot
                if expectation is not None
                else len(enrollment.intent_plan) + 1
            ),
            "role": safe_role,
            "chars": len(str(value or "")),
            "matched": matched,
        }
        proof["responseGeneration"] = response_generation if generation_valid else -1
        enrollment.semantic_observed_count += 1
        if matched:
            enrollment.semantic_matched_count += 1
            if safe_role == "initial":
                enrollment.semantic_initial_observed_generation = response_generation
            else:
                enrollment.semantic_newest_observed_generation = response_generation
        else:
            enrollment.semantic_eligible = False
            enrollment.semantic_mismatch_count += 1
        return proof

    @_synchronized
    def consume_quiet_setup_turn(self, journey_id: str, *, source: str) -> bool:
        self._prepare()
        enrollment = self._active.get(journey_id)
        invalid_scope = bool(
            enrollment is None
            or not enrollment.connected
            or enrollment.proof_profile != CANDIDATE_LIFECYCLE_PROFILE
            or enrollment.journey_type != "quiet"
            or enrollment.semantic_kind != "quiet"
            or enrollment.quiet_mode != "robot_speaking"
        )
        if invalid_scope:
            return False
        if source != "audio_input" or enrollment.quiet_setup_turn_consumed:
            enrollment.quiet_semantic_eligible = False
            return False
        enrollment.quiet_setup_turn_consumed = True
        return True

    @_synchronized
    def bind_quiet_setup_response(
        self, journey_id: str, *, response_generation: int
    ) -> bool:
        self._prepare()
        enrollment = self._active.get(journey_id)
        valid = bool(
            enrollment is not None
            and enrollment.connected
            and enrollment.semantic_kind == "quiet"
            and enrollment.quiet_mode == "robot_speaking"
            and enrollment.quiet_setup_turn_consumed
            and enrollment.quiet_semantic_eligible
            and enrollment.quiet_setup_response_generation is None
            and isinstance(response_generation, int)
            and not isinstance(response_generation, bool)
            and response_generation >= 0
        )
        if enrollment is None:
            return False
        if not valid:
            enrollment.quiet_semantic_eligible = False
            return False
        enrollment.quiet_setup_response_generation = response_generation
        return True

    @_synchronized
    def record_quiet_response_lifecycle(
        self, journey_id: str, *, response_generation: int, event: str
    ) -> bool:
        self._prepare()
        enrollment = self._active.get(journey_id)
        if enrollment is None or enrollment.semantic_kind != "quiet":
            return False
        if event == "start":
            valid = bool(
                enrollment.quiet_semantic_eligible
                and enrollment.quiet_setup_turn_consumed
                and enrollment.quiet_setup_response_generation
                == response_generation
                and enrollment.quiet_response_started_generation is None
            )
            if valid:
                enrollment.quiet_response_started_generation = response_generation
        elif event == "end":
            valid = bool(
                enrollment.quiet_semantic_eligible
                and enrollment.quiet_response_started_generation
                == response_generation
                and enrollment.quiet_response_completed_generation is None
            )
            if valid:
                enrollment.quiet_response_completed_generation = response_generation
        else:
            valid = False
        if not valid:
            enrollment.quiet_semantic_eligible = False
        return valid

    @_synchronized
    def record_candidate_interrupt(
        self,
        journey_id: str,
        *,
        old_generation: int,
        new_generation: int,
    ) -> bool:
        self._prepare()
        enrollment = self._active.get(journey_id)
        if enrollment is None or not enrollment.connected:
            raise EnrollmentError("JOURNEY_NOT_FOUND")
        if enrollment.semantic_kind != "bargein-intent":
            return False
        valid = bool(
            enrollment.semantic_eligible
            and enrollment.semantic_initial_response_generation == old_generation
            and enrollment.semantic_newest_observed_generation == old_generation
            and isinstance(new_generation, int)
            and not isinstance(new_generation, bool)
            and new_generation != old_generation
        )
        if not valid:
            enrollment.semantic_eligible = False
            enrollment.semantic_mismatch_count += 1
            return False
        enrollment.semantic_interrupted_old_generation = old_generation
        enrollment.semantic_replacement_generation = new_generation
        return True

    @_synchronized
    def record_candidate_response_started(
        self, journey_id: str, *, response_generation: int
    ) -> bool:
        self._prepare()
        enrollment = self._active.get(journey_id)
        if enrollment is None or not enrollment.connected:
            raise EnrollmentError("JOURNEY_NOT_FOUND")
        if enrollment.semantic_kind != "bargein-intent":
            return False
        if enrollment.semantic_replacement_generation is None:
            valid = bool(
                enrollment.semantic_eligible
                and enrollment.semantic_matched_count == 1
                and enrollment.semantic_initial_response_generation is None
                and response_generation
                == enrollment.semantic_initial_observed_generation
            )
            if valid:
                enrollment.semantic_initial_response_generation = response_generation
                return True
            enrollment.semantic_eligible = False
            enrollment.semantic_mismatch_count += 1
            return False
        valid = bool(
            enrollment.semantic_eligible
            and not enrollment.semantic_replacement_started
            and not enrollment.semantic_replacement_completed
            and response_generation == enrollment.semantic_replacement_generation
            and response_generation != enrollment.semantic_interrupted_old_generation
        )
        if not valid:
            enrollment.semantic_eligible = False
            enrollment.semantic_mismatch_count += 1
            return False
        enrollment.semantic_replacement_started = True
        return True

    @_synchronized
    def record_candidate_response_completed(
        self, journey_id: str, *, response_generation: int
    ) -> bool:
        self._prepare()
        enrollment = self._active.get(journey_id)
        if enrollment is None or not enrollment.connected:
            raise EnrollmentError("JOURNEY_NOT_FOUND")
        if enrollment.semantic_kind != "bargein-intent":
            return False
        valid = bool(
            enrollment.semantic_eligible
            and enrollment.semantic_replacement_started
            and not enrollment.semantic_replacement_completed
            and response_generation == enrollment.semantic_replacement_generation
        )
        if not valid:
            if enrollment.semantic_replacement_generation is not None:
                enrollment.semantic_eligible = False
                enrollment.semantic_mismatch_count += 1
            return False
        enrollment.semantic_replacement_completed = True
        return True

    @_synchronized
    def record_candidate_stale_audio(
        self, journey_id: str, *, response_generation: int
    ) -> bool:
        self._prepare()
        enrollment = self._active.get(journey_id)
        if enrollment is None or not enrollment.connected:
            raise EnrollmentError("JOURNEY_NOT_FOUND")
        if enrollment.semantic_kind != "bargein-intent":
            return False
        if enrollment.semantic_replacement_generation is None:
            return False
        if response_generation == enrollment.semantic_replacement_generation:
            return False
        if response_generation == enrollment.semantic_interrupted_old_generation:
            enrollment.semantic_stale_old_audio_count += 1
        enrollment.semantic_eligible = False
        enrollment.semantic_mismatch_count += 1
        return True

    @_synchronized
    def mark_output_idle(self, journey_id: str, *, response_generation: int) -> bool:
        self._prepare()
        enrollment = self._active.get(journey_id)
        if enrollment is None or not enrollment.connected:
            raise EnrollmentError("JOURNEY_NOT_FOUND")
        if (
            not isinstance(response_generation, int)
            or isinstance(response_generation, bool)
            or not enrollment.transcript_proof_eligible
            or response_generation != enrollment.final_response_generation
        ):
            return False
        enrollment.output_idle_generation = response_generation
        return self._ready_to_finalize(enrollment)

    @_synchronized
    def abort_claim(
        self,
        *,
        device_id: str,
        client_id: str,
        journey_id: str,
        failure_code: str,
    ) -> dict:
        self._prepare()
        enrollment = self._active.get(journey_id)
        if (
            enrollment is None
            or not enrollment.connected
            or enrollment.device_id != normalize_peer_id(device_id)
            or enrollment.client_id != normalize_peer_id(client_id)
        ):
            raise EnrollmentError("JOURNEY_NOT_FOUND")
        return self.finalize(journey_id, status="FAIL", failure_code=failure_code)

    @_synchronized
    def finalize(
        self,
        journey_id: str,
        status: Literal["PASS", "FAIL"] = "PASS",
        *,
        failure_code: str | None = None,
    ) -> dict:
        now = self._prepare()
        if status not in ("PASS", "FAIL"):
            raise EnrollmentError("INVALID_STATUS")
        tombstone = self._tombstones.get(journey_id)
        if tombstone is not None:
            if tombstone["status"] != status:
                raise EnrollmentError("FINAL_STATUS_CONFLICT")
            return dict(tombstone)
        enrollment = self._active.get(journey_id)
        if enrollment is None:
            raise EnrollmentError("JOURNEY_NOT_FOUND")
        self._terminal_peer_digests[journey_id] = self._peer_digest(enrollment.device_id)
        self._terminal_client_digests[journey_id] = self._peer_digest(enrollment.client_id)
        self._active.pop(journey_id)
        enrollment.finalized = True
        self._zeroize(enrollment.hmac_key)
        self._zeroize(enrollment.semantic_hmac_key)
        snapshot = self._terminal_snapshot(enrollment, status=status, timestamp=now)
        if failure_code is not None:
            snapshot["failureCode"] = failure_code
        self._add_tombstone(snapshot)
        return dict(snapshot)

    @_synchronized
    def safe_snapshot(self, journey_id: str) -> dict:
        self._prepare()
        tombstone = self._tombstones.get(journey_id)
        if tombstone is not None:
            return dict(tombstone)
        enrollment = self._active.get(journey_id)
        if enrollment is None:
            raise EnrollmentError("JOURNEY_NOT_FOUND")
        return {
            "journeyId": enrollment.journey_id,
            "journeyType": enrollment.journey_type,
            "proofProfile": enrollment.proof_profile,
            "status": "ACTIVE",
            "createdAt": enrollment.created_at,
            "expiresAt": enrollment.expires_at,
            "connected": enrollment.connected,
            "transcriptCount": len(enrollment.transcript_plan),
            **self._safe_transcript_report(enrollment),
            **self._safe_semantic_report(enrollment),
        }

    @_synchronized
    def device_matches(self, *, device_id: str, journey_id: str) -> bool:
        self._prepare()
        enrollment = self._active.get(journey_id)
        if enrollment is not None:
            return enrollment.device_id == normalize_peer_id(device_id)
        expected = self._terminal_peer_digests.get(journey_id)
        if expected is None:
            return False
        return expected == self._peer_digest(device_id)

    def _prepare(self) -> float:
        wall_now = self._wall_clock()
        monotonic_now = self._monotonic_clock()
        expired = [
            journey_id
            for journey_id, enrollment in self._active.items()
            if enrollment.monotonic_deadline <= monotonic_now
        ]
        for journey_id in expired:
            enrollment = self._active[journey_id]
            self._terminal_peer_digests[journey_id] = self._peer_digest(enrollment.device_id)
            self._terminal_client_digests[journey_id] = self._peer_digest(enrollment.client_id)
            self._active.pop(journey_id)
            self._zeroize(enrollment.hmac_key)
            self._zeroize(enrollment.semantic_hmac_key)
            self._add_tombstone(
                self._terminal_snapshot(
                    enrollment,
                    status="EXPIRED",
                    timestamp=wall_now,
                )
            )
        return wall_now

    def _terminal_snapshot(
        self,
        enrollment: _EvidenceEnrollmentState,
        *,
        status: str,
        timestamp: float,
    ) -> dict:
        terminal_at = max(timestamp, enrollment.created_at)
        if status == "EXPIRED":
            terminal_at = max(terminal_at, enrollment.expires_at)
        return {
            "journeyId": enrollment.journey_id,
            "journeyType": enrollment.journey_type,
            "proofProfile": enrollment.proof_profile,
            "status": status,
            "createdAt": enrollment.created_at,
            "expiresAt": enrollment.expires_at,
            "finalizedAt": terminal_at,
            "transcriptCount": len(enrollment.transcript_plan),
            **self._safe_transcript_report(enrollment),
            **self._safe_semantic_report(enrollment),
        }

    @staticmethod
    def _ready_to_finalize(enrollment: _EvidenceEnrollmentState) -> bool:
        if enrollment.proof_profile == CANDIDATE_LIFECYCLE_PROFILE:
            return True
        return bool(
            enrollment.transcript_plan
            and enrollment.transcript_proof_eligible
            and enrollment.transcript_matched_count == len(enrollment.transcript_plan)
            and enrollment.transcript_plan[-1].phase == "post_lesson"
            and enrollment.final_response_generation is not None
            and enrollment.output_idle_generation == enrollment.final_response_generation
        )

    def _safe_transcript_report(self, enrollment: _EvidenceEnrollmentState) -> dict:
        matched = [proof for proof in enrollment.transcript_proofs if proof["matched"]]
        expected_interrupts = sum(
            item.phase == "interrupt" for item in enrollment.transcript_plan
        )
        matched_interrupts = sum(proof["phase"] == "interrupt" for proof in matched)
        expected_post_lesson = sum(
            item.phase == "post_lesson" for item in enrollment.transcript_plan
        )
        matched_post_lesson = sum(
            proof["phase"] == "post_lesson" for proof in matched
        )
        return {
            "expectedCount": len(enrollment.transcript_plan),
            "transcriptExpectedCount": len(enrollment.transcript_plan),
            "transcriptObservedCount": enrollment.transcript_observed_count,
            "transcriptMatchedCount": enrollment.transcript_matched_count,
            "transcriptMismatchCount": enrollment.transcript_mismatch_count,
            "transcriptMissingCount": max(
                0, len(enrollment.transcript_plan) - enrollment.transcript_matched_count
            ),
            "transcriptMatchedSlots": [proof["slot"] for proof in matched],
            "transcriptMatchedPhases": [proof["phase"] for proof in matched],
            "transcriptProofs": [dict(proof) for proof in enrollment.transcript_proofs],
            "transcriptOrderingProof": enrollment.transcript_proof_eligible,
            "transcriptProofEligible": enrollment.transcript_proof_eligible,
            "postInterruptVerdict": bool(
                enrollment.transcript_proof_eligible
                and expected_interrupts
                and matched_interrupts == expected_interrupts
            ),
            "postLessonVerdict": bool(
                enrollment.transcript_proof_eligible
                and expected_post_lesson
                and matched_post_lesson == expected_post_lesson
            ),
            "outputIdleObserved": enrollment.output_idle_generation is not None,
            "readyToFinalize": self._ready_to_finalize(enrollment),
        }

    @staticmethod
    def _safe_semantic_report(enrollment: _EvidenceEnrollmentState) -> dict:
        if enrollment.semantic_kind == "bargein-intent":
            ownership_ready = bool(
                enrollment.semantic_eligible
                and enrollment.semantic_matched_count == len(enrollment.intent_plan)
                and enrollment.semantic_newest_observed_generation
                == enrollment.semantic_interrupted_old_generation
                and enrollment.semantic_replacement_generation is not None
                and enrollment.semantic_replacement_generation
                != enrollment.semantic_interrupted_old_generation
                and enrollment.semantic_replacement_started
                and enrollment.semantic_replacement_completed
                and enrollment.semantic_stale_old_audio_count == 0
            )
            return {
                "semanticProofKind": "bargein-intent",
                "semanticExpectedCount": len(enrollment.intent_plan),
                "semanticObservedCount": enrollment.semantic_observed_count,
                "semanticMatchCount": enrollment.semantic_matched_count,
                "semanticMismatchCount": enrollment.semantic_mismatch_count,
                "semanticOrderingValid": enrollment.semantic_eligible,
                "semanticEligible": enrollment.semantic_eligible,
                "latestIntentMatched": bool(
                    enrollment.semantic_eligible
                    and enrollment.semantic_matched_count == len(enrollment.intent_plan)
                    and enrollment.intent_plan[-1].role == "newest"
                ),
                "semanticOwnershipReady": ownership_ready,
                "semanticInitialObservedGeneration": enrollment.semantic_initial_observed_generation,
                "semanticInitialResponseGeneration": enrollment.semantic_initial_response_generation,
                "semanticOldResponseGeneration": enrollment.semantic_interrupted_old_generation,
                "semanticReplacementGeneration": enrollment.semantic_replacement_generation,
                "semanticReplacementStarted": enrollment.semantic_replacement_started,
                "semanticReplacementCompleted": enrollment.semantic_replacement_completed,
                "semanticStaleOldAudioCount": enrollment.semantic_stale_old_audio_count,
            }
        if enrollment.semantic_kind == "quiet":
            return {
                "semanticProofKind": "quiet",
                "quietMode": enrollment.quiet_mode,
                "quietSetupTurnConsumed": enrollment.quiet_setup_turn_consumed,
                "quietSetupResponseGeneration": enrollment.quiet_setup_response_generation,
                "quietResponseStartedGeneration": enrollment.quiet_response_started_generation,
                "quietResponseCompletedGeneration": enrollment.quiet_response_completed_generation,
                "quietSemanticEligible": enrollment.quiet_semantic_eligible,
            }
        return {}

    @staticmethod
    def _view(enrollment: _EvidenceEnrollmentState) -> EvidenceEnrollment:
        return EvidenceEnrollment(
            device_id=enrollment.device_id,
            client_id=enrollment.client_id,
            journey_id=enrollment.journey_id,
            journey_type=enrollment.journey_type,
            proof_profile=enrollment.proof_profile,
            transcript_plan=tuple(
                TranscriptExpectation(item.slot, item.phase, "")
                for item in enrollment.transcript_plan
            ),
            hmac_key=bytearray(),
            expires_at=enrollment.expires_at,
            semantic_kind=enrollment.semantic_kind,
            intent_plan=tuple(
                CandidateIntentExpectation(item.slot, item.role, "")
                for item in enrollment.intent_plan
            ),
            quiet_mode=enrollment.quiet_mode,
            semantic_hmac_key=bytearray(),
            connected=enrollment.connected,
            finalized=enrollment.finalized,
        )

    @staticmethod
    def _validate_profile_payload(
        *,
        journey_type: str,
        proof_profile: str,
        transcript_plan: tuple[TranscriptExpectation, ...],
        hmac_key: bytes | bytearray,
        semantic_kind: str,
        intent_plan: tuple[CandidateIntentExpectation, ...],
        quiet_mode: str | None,
        semantic_hmac_key: bytes | bytearray,
    ) -> None:
        validate_evidence_claims(journey_type, proof_profile)
        if not isinstance(hmac_key, (bytes, bytearray)) or not isinstance(
            semantic_hmac_key, (bytes, bytearray)
        ):
            raise EnrollmentError("INVALID_EVIDENCE_PROFILE_PAYLOAD")
        if proof_profile == CANDIDATE_LIFECYCLE_PROFILE:
            if transcript_plan or hmac_key:
                raise EnrollmentError("INVALID_EVIDENCE_PROFILE_PAYLOAD")
            EvidenceEnrollmentRegistry._validate_candidate_semantic_payload(
                journey_type=journey_type,
                semantic_kind=semantic_kind,
                intent_plan=intent_plan,
                quiet_mode=quiet_mode,
                semantic_hmac_key=semantic_hmac_key,
            )
            return
        if semantic_kind != "none" or intent_plan or quiet_mode is not None or semantic_hmac_key:
            raise EnrollmentError("INVALID_EVIDENCE_PROFILE_PAYLOAD")
        if len(hmac_key) != 32 or not 1 <= len(transcript_plan) <= 64:
            raise EnrollmentError("INVALID_EVIDENCE_PROFILE_PAYLOAD")
        for expected_slot, expectation in enumerate(transcript_plan, start=1):
            if (
                not isinstance(expectation, TranscriptExpectation)
                or not isinstance(expectation.slot, int)
                or isinstance(expectation.slot, bool)
                or expectation.slot != expected_slot
                or not isinstance(expectation.phase, str)
                or expectation.phase not in ("interrupt", "lesson", "post_lesson")
                or not isinstance(expectation.expected_mac, str)
                or len(expectation.expected_mac) != 64
                or any(character not in "0123456789abcdef" for character in expectation.expected_mac)
            ):
                raise EnrollmentError("INVALID_EVIDENCE_PROFILE_PAYLOAD")
        if transcript_plan[-1].phase != "post_lesson":
            raise EnrollmentError("INVALID_EVIDENCE_PROFILE_PAYLOAD")

    @staticmethod
    def _validate_candidate_semantic_payload(
        *,
        journey_type: str,
        semantic_kind: str,
        intent_plan: tuple[CandidateIntentExpectation, ...],
        quiet_mode: str | None,
        semantic_hmac_key: bytes | bytearray,
    ) -> None:
        if semantic_kind == "none":
            if intent_plan or quiet_mode is not None or semantic_hmac_key:
                raise EnrollmentError("INVALID_EVIDENCE_PROFILE_PAYLOAD")
            return
        if journey_type == "bargein":
            if (
                semantic_kind != "bargein-intent"
                or quiet_mode is not None
                or len(semantic_hmac_key) != 32
                or len(intent_plan) != 2
            ):
                raise EnrollmentError("INVALID_EVIDENCE_PROFILE_PAYLOAD")
            expected_roles = ("initial", "newest")
            for expected_slot, (expectation, expected_role) in enumerate(
                zip(intent_plan, expected_roles, strict=True), start=1
            ):
                if (
                    not isinstance(expectation, CandidateIntentExpectation)
                    or not isinstance(expectation.slot, int)
                    or isinstance(expectation.slot, bool)
                    or expectation.slot != expected_slot
                    or expectation.role != expected_role
                    or not isinstance(expectation.expected_mac, str)
                    or len(expectation.expected_mac) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in expectation.expected_mac
                    )
                ):
                    raise EnrollmentError("INVALID_EVIDENCE_PROFILE_PAYLOAD")
            return
        if journey_type == "quiet":
            if (
                semantic_kind != "quiet"
                or quiet_mode not in ("silence", "robot_speaking")
                or intent_plan
                or semantic_hmac_key
            ):
                raise EnrollmentError("INVALID_EVIDENCE_PROFILE_PAYLOAD")
            return
        if semantic_kind != "none" or intent_plan or quiet_mode is not None or semantic_hmac_key:
            raise EnrollmentError("INVALID_EVIDENCE_PROFILE_PAYLOAD")

    def _add_tombstone(self, snapshot: dict) -> None:
        self._tombstones[snapshot["journeyId"]] = snapshot
        while len(self._tombstones) > self._max_tombstones:
            evicted, _snapshot = self._tombstones.popitem(last=False)
            self._terminal_peer_digests.pop(evicted, None)
            self._terminal_client_digests.pop(evicted, None)

    def _peer_digest(self, device_id: str) -> bytes:
        return hashlib.blake2b(
            normalize_peer_id(device_id).encode("utf-8"),
            key=self._peer_digest_key,
            digest_size=16,
        ).digest()

    @staticmethod
    def _zeroize(key: bytearray) -> None:
        for index in range(len(key)):
            key[index] = 0
