from __future__ import annotations

import hashlib
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Literal


def normalize_peer_id(value: str) -> str:
    return str(value or "").strip().lower()


class EnrollmentError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TranscriptExpectation:
    slot: int
    phase: Literal["interrupt", "lesson", "post_lesson"]
    expected_mac: str = field(repr=False)


@dataclass(slots=True)
class EvidenceEnrollment:
    device_id: str
    client_id: str
    journey_id: str
    transcript_plan: tuple[TranscriptExpectation, ...]
    hmac_key: bytearray = field(repr=False)
    expires_at: float
    connected: bool = False
    finalized: bool = False


class EvidenceEnrollmentRegistry:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        max_active: int = 128,
        max_tombstones: int = 256,
    ):
        self._clock = clock
        self._max_active = max_active
        self._max_tombstones = max_tombstones
        self._active: dict[str, EvidenceEnrollment] = {}
        self._tombstones: OrderedDict[str, dict] = OrderedDict()
        self._created_at: dict[str, float] = {}
        self._terminal_peer_digests: dict[str, bytes] = {}
        self._peer_digest_key = os.urandom(32)

    def register(
        self,
        *,
        device_id: str,
        client_id: str,
        journey_id: str,
        transcript_plan: tuple[TranscriptExpectation, ...],
        hmac_key: bytearray,
        ttl_sec: int,
    ) -> EvidenceEnrollment:
        now = self._prepare()
        device_id = normalize_peer_id(device_id)
        client_id = normalize_peer_id(client_id)
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
            self._zeroize(hmac_key)
            raise EnrollmentError(error)
        enrollment = EvidenceEnrollment(
            device_id=device_id,
            client_id=client_id,
            journey_id=journey_id,
            transcript_plan=tuple(transcript_plan),
            hmac_key=hmac_key,
            expires_at=now + ttl_sec,
        )
        self._active[journey_id] = enrollment
        self._created_at[journey_id] = now
        return enrollment

    def ota_journey(self, device_id: str, client_id: str) -> str | None:
        self._prepare()
        device_id = normalize_peer_id(device_id)
        client_id = normalize_peer_id(client_id)
        for enrollment in self._active.values():
            if enrollment.device_id == device_id and enrollment.client_id == client_id:
                return enrollment.journey_id
        return None

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
        return enrollment

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
        self._active.pop(journey_id)
        enrollment.finalized = True
        self._zeroize(enrollment.hmac_key)
        snapshot = self._terminal_snapshot(enrollment, status=status, timestamp=now)
        if failure_code is not None:
            snapshot["failureCode"] = failure_code
        self._add_tombstone(snapshot)
        return dict(snapshot)

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
            "status": "ACTIVE",
            "createdAt": self._created_at[journey_id],
            "expiresAt": enrollment.expires_at,
            "connected": enrollment.connected,
            "transcriptCount": len(enrollment.transcript_plan),
        }

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
        now = self._clock()
        expired = [
            journey_id
            for journey_id, enrollment in self._active.items()
            if enrollment.expires_at <= now
        ]
        for journey_id in expired:
            enrollment = self._active[journey_id]
            self._terminal_peer_digests[journey_id] = self._peer_digest(enrollment.device_id)
            self._active.pop(journey_id)
            self._zeroize(enrollment.hmac_key)
            self._add_tombstone(
                self._terminal_snapshot(enrollment, status="EXPIRED", timestamp=now)
            )
        return now

    def _terminal_snapshot(self, enrollment: EvidenceEnrollment, *, status: str, timestamp: float) -> dict:
        return {
            "journeyId": enrollment.journey_id,
            "status": status,
            "createdAt": self._created_at.pop(enrollment.journey_id),
            "expiresAt": enrollment.expires_at,
            "finalizedAt": timestamp,
            "transcriptCount": len(enrollment.transcript_plan),
        }

    def _add_tombstone(self, snapshot: dict) -> None:
        self._tombstones[snapshot["journeyId"]] = snapshot
        while len(self._tombstones) > self._max_tombstones:
            evicted, _snapshot = self._tombstones.popitem(last=False)
            self._terminal_peer_digests.pop(evicted, None)

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
