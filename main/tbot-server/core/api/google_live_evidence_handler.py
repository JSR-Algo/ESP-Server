import base64
import hmac
import os
import re

from aiohttp import web

from core.voice.google_live.evidence_enrollment import (
    CANDIDATE_LIFECYCLE_PROFILE,
    PHYSICAL_TRANSCRIPT_PROFILE,
    EnrollmentError,
    EvidenceEnrollmentRegistry,
    TranscriptExpectation,
    normalize_peer_id,
)

NORMALIZATION_VERSION = "google-live-transcript-nfkc-casefold.v1"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAC = re.compile(r"^[0-9a-f]{64}$")
_LEGACY_PHYSICAL_POST_FIELDS = {
    "clientId",
    "journeyId",
    "ttlSec",
    "normalizationVersion",
    "hmacKeyBase64",
    "transcriptPlan",
}
_EXPLICIT_PHYSICAL_POST_FIELDS = _LEGACY_PHYSICAL_POST_FIELDS | {
    "journeyType",
    "proofProfile",
}
_CANDIDATE_POST_FIELDS = {
    "clientId",
    "journeyId",
    "ttlSec",
    "journeyType",
    "proofProfile",
}
_PLAN_FIELDS = {"slot", "phase", "expectedMac"}
_CANDIDATE_FIELDS = {
    "gitSha",
    "imageDigest",
    "firmwareIdentity",
    "configFingerprint",
    "fixtureSha256",
}
_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LOWER_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SAFE_FIRMWARE_IDENTITY = re.compile(r"^[A-Za-z0-9._:+/-]{1,128}$")


class GoogleLiveEvidenceHandler:
    def __init__(self, registry: EvidenceEnrollmentRegistry, connections=None):
        self.registry = registry
        self.connections = connections

    def _authorize(self, request: web.Request):
        expected = os.environ.get("TBOT_DEVICE_MINT_SECRET", "")
        if not expected:
            return self._error(503, "MINT_SECRET_NOT_CONFIGURED", "Device token minting is not configured")
        provided = request.headers.get("X-Mint-Secret", "")
        if not provided or not hmac.compare_digest(provided, expected):
            return self._error(401, "MINT_AUTH_INVALID", "Invalid X-Mint-Secret")
        return None

    async def handle_post(self, request: web.Request) -> web.Response:
        auth_error = self._authorize(request)
        if auth_error is not None:
            return auth_error
        try:
            body = await request.json()
            parsed = self._parse_body(request.match_info.get("deviceId", ""), body)
            self.registry.register(**parsed)
        except EnrollmentError as exc:
            code = str(exc)
            if code in ("JOURNEY_REUSED", "ENROLLMENT_ACTIVE"):
                return self._error(409, code, "Evidence enrollment conflicts with active state")
            if code == "CAPACITY_EXCEEDED":
                return self._error(503, code, "Evidence enrollment capacity is exhausted")
            return self._error(400, "INVALID_REQUEST", "Invalid Google Live evidence enrollment request")
        except Exception:
            return self._error(400, "INVALID_REQUEST", "Invalid Google Live evidence enrollment request")
        return web.json_response(
            {
                "data": {
                    "registered": True,
                    "journeyId": parsed["journey_id"],
                }
            },
            status=201,
            headers={"Cache-Control": "no-store"},
        )

    async def handle_get(self, request: web.Request) -> web.Response:
        auth_error = self._authorize(request)
        if auth_error is not None:
            return auth_error
        try:
            device_id, journey_id = self._path_ids(request)
        except ValueError:
            return self._invalid_path()
        try:
            snapshot = self.registry.safe_snapshot(journey_id)
        except EnrollmentError:
            return self._error(404, "JOURNEY_NOT_FOUND", "Evidence journey not found")
        if not self._device_matches(device_id, journey_id):
            return self._error(404, "JOURNEY_NOT_FOUND", "Evidence journey not found")
        return web.json_response(snapshot, headers={"Cache-Control": "no-store"})

    async def handle_delete(self, request: web.Request) -> web.Response:
        auth_error = self._authorize(request)
        if auth_error is not None:
            return auth_error
        try:
            device_id, journey_id = self._path_ids(request)
        except ValueError:
            return self._invalid_path()
        if not self._device_matches(device_id, journey_id):
            return self._error(404, "JOURNEY_NOT_FOUND", "Evidence journey not found")
        try:
            snapshot = self.registry.finalize(
                journey_id,
                status="FAIL",
                failure_code="OPERATOR_CANCELLED",
            )
        except EnrollmentError as exc:
            if str(exc) == "FINAL_STATUS_CONFLICT":
                return self._error(409, "FINAL_STATUS_CONFLICT", "Evidence journey already finalized")
            return self._error(404, "JOURNEY_NOT_FOUND", "Evidence journey not found")
        return web.json_response(snapshot, headers={"Cache-Control": "no-store"})

    async def handle_candidate_identity_put(self, request: web.Request) -> web.Response:
        auth_error = self._authorize(request)
        if auth_error is not None:
            return auth_error
        try:
            device_id, journey_id = self._path_ids(request)
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {"candidateIdentity"}:
                raise ValueError
            identity = body["candidateIdentity"]
            if not isinstance(identity, dict) or set(identity) != _CANDIDATE_FIELDS:
                raise ValueError
            if (
                _GIT_SHA.fullmatch(identity.get("gitSha", "")) is None
                or _LOWER_IMAGE_DIGEST.fullmatch(identity.get("imageDigest", ""))
                is None
                or _SAFE_FIRMWARE_IDENTITY.fullmatch(
                    identity.get("firmwareIdentity", "")
                )
                is None
                or _LOWER_IMAGE_DIGEST.fullmatch(
                    identity.get("configFingerprint", "")
                )
                is None
                or _LOWER_SHA256.fullmatch(identity.get("fixtureSha256", ""))
                is None
            ):
                raise ValueError
            self.registry.bind_candidate_identity(
                device_id=device_id,
                journey_id=journey_id,
                candidate_identity=identity,
            )
        except EnrollmentError as exc:
            code = str(exc)
            if code == "CANDIDATE_IDENTITY_CONFLICT":
                return self._error(409, code, "Candidate identity is already bound")
            if code == "JOURNEY_NOT_FOUND":
                return self._error(404, code, "Evidence journey not found")
            return self._error(400, "INVALID_REQUEST", "Invalid candidate identity")
        except Exception:
            return self._error(400, "INVALID_REQUEST", "Invalid candidate identity")
        return web.json_response(
            {"data": {"bound": True, "journeyId": journey_id}},
            headers={"Cache-Control": "no-store"},
        )

    async def handle_finalize(self, request: web.Request) -> web.Response:
        auth_error = self._authorize(request)
        if auth_error is not None:
            return auth_error
        try:
            device_id, journey_id = self._path_ids(request)
        except ValueError:
            return self._invalid_path()
        device_key = normalize_peer_id(device_id)
        connections = self.connections
        matching_connections = (
            [
                (key, candidate)
                for key, candidate in connections.items()
                if normalize_peer_id(key) == device_key
            ]
            if connections is not None
            else []
        )
        if len(matching_connections) != 1:
            return self._error(404, "EVIDENCE_CONNECTION_NOT_FOUND", "Evidence connection not found")
        connection_key, connection = matching_connections[0]
        if connection is None:
            return self._error(404, "EVIDENCE_CONNECTION_NOT_FOUND", "Evidence connection not found")
        session_id = str(getattr(connection, "session_id", "") or "")
        reserve_current = getattr(connections, "reserve_current", None)
        if not session_id or not callable(reserve_current):
            return self._error(409, "EVIDENCE_CONNECTION_STALE", "Evidence connection is not current")
        async with reserve_current(connection_key, connection, session_id) as current:
            scope = getattr(connection, "google_live_evidence_scope", None)
            client_id = str(getattr(connection, "client_id", "") or "")
            if (
                not current
                or not isinstance(scope, dict)
                or scope.get("journeyId") != journey_id
                or scope.get("connectionId") != session_id
                or not self.registry.active_claim_matches(
                    device_id=device_id, client_id=client_id, journey_id=journey_id
                )
            ):
                cached = getattr(connection, "google_live_evidence_finalize_result", None)
                if (
                    current
                    and isinstance(scope, dict)
                    and scope.get("journeyId") == journey_id
                    and scope.get("connectionId") == session_id
                    and isinstance(cached, dict)
                    and cached.get("evidenceScope") == scope
                    and self.registry.terminal_claim_matches(
                        device_id=device_id,
                        client_id=client_id,
                        journey_id=journey_id,
                    )
                ):
                    return web.json_response(
                        cached, headers={"Cache-Control": "no-store"}
                    )
                return self._error(409, "EVIDENCE_SCOPE_INVALID", "Evidence scope is not active")
            finalize = getattr(connection, "finalize_google_live_evidence", None)
            if not callable(finalize):
                return self._error(503, "EVIDENCE_FINALIZE_UNAVAILABLE", "Evidence finalization is unavailable")
            try:
                result = await finalize(scope)
                snapshot = self.registry.safe_snapshot(journey_id)
                expected_status = "PASS" if result.get("status") == "PASS" else "FAIL"
                if snapshot.get("status") != expected_status:
                    return self._error(503, "EVIDENCE_FINALIZE_FAILED", "Evidence finalization failed")
            except EnrollmentError as exc:
                if str(exc) == "FINAL_STATUS_CONFLICT":
                    return self._error(409, "FINAL_STATUS_CONFLICT", "Evidence journey already finalized")
                return self._error(409, "EVIDENCE_ENROLLMENT_INVALID", "Evidence enrollment is invalid")
            except Exception:
                return self._error(503, "EVIDENCE_FINALIZE_FAILED", "Evidence finalization failed")
        return web.json_response(result, headers={"Cache-Control": "no-store"})

    def _device_matches(self, device_id: str, journey_id: str) -> bool:
        return self.registry.device_matches(device_id=device_id, journey_id=journey_id)

    def _path_ids(self, request: web.Request) -> tuple[str, str]:
        return (
            self._safe_id(request.match_info.get("deviceId", "")),
            self._safe_id(request.match_info.get("journeyId", "")),
        )

    def _invalid_path(self) -> web.Response:
        return self._error(400, "INVALID_REQUEST", "Invalid Google Live evidence path identity")

    def _parse_body(self, route_device_id: str, body) -> dict:
        if not isinstance(body, dict):
            raise ValueError
        fields = set(body)
        if fields == _LEGACY_PHYSICAL_POST_FIELDS:
            journey_type = "physical"
            proof_profile = PHYSICAL_TRANSCRIPT_PROFILE
        elif fields == _EXPLICIT_PHYSICAL_POST_FIELDS:
            journey_type = body["journeyType"]
            proof_profile = body["proofProfile"]
            if (
                not isinstance(journey_type, str)
                or not isinstance(proof_profile, str)
                or journey_type != "physical"
                or proof_profile != PHYSICAL_TRANSCRIPT_PROFILE
            ):
                raise ValueError
        elif fields == _CANDIDATE_POST_FIELDS:
            journey_type = body["journeyType"]
            proof_profile = body["proofProfile"]
            if (
                not isinstance(journey_type, str)
                or not isinstance(proof_profile, str)
                or proof_profile != CANDIDATE_LIFECYCLE_PROFILE
            ):
                raise ValueError
            return self._parse_candidate_body(
                route_device_id,
                body,
                journey_type=journey_type,
                proof_profile=proof_profile,
            )
        else:
            raise ValueError
        device_id = self._safe_id(route_device_id)
        client_id = self._safe_id(body["clientId"])
        journey_id = self._safe_id(body["journeyId"])
        ttl_sec = body["ttlSec"]
        if isinstance(ttl_sec, bool) or not isinstance(ttl_sec, int) or not 30 <= ttl_sec <= 3600:
            raise ValueError
        if body["normalizationVersion"] != NORMALIZATION_VERSION:
            raise ValueError
        encoded = body["hmacKeyBase64"]
        if not isinstance(encoded, str):
            raise ValueError
        decoded = base64.b64decode(encoded, validate=True)
        if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != encoded:
            raise ValueError
        plan = body["transcriptPlan"]
        if not isinstance(plan, list) or not 1 <= len(plan) <= 64:
            raise ValueError
        expectations = []
        for expected_slot, item in enumerate(plan, start=1):
            if not isinstance(item, dict) or set(item) != _PLAN_FIELDS:
                raise ValueError
            if (
                isinstance(item["slot"], bool)
                or not isinstance(item["slot"], int)
                or item["slot"] != expected_slot
                or item["phase"] not in ("interrupt", "lesson", "post_lesson")
            ):
                raise ValueError
            if not isinstance(item["expectedMac"], str) or not _MAC.fullmatch(item["expectedMac"]):
                raise ValueError
            expectations.append(TranscriptExpectation(item["slot"], item["phase"], item["expectedMac"]))
        if expectations[-1].phase != "post_lesson":
            raise ValueError
        return {
            "device_id": device_id,
            "client_id": client_id,
            "journey_id": journey_id,
            "journey_type": journey_type,
            "proof_profile": proof_profile,
            "transcript_plan": tuple(expectations),
            "hmac_key": bytearray(decoded),
            "ttl_sec": ttl_sec,
        }

    def _parse_candidate_body(
        self,
        route_device_id: str,
        body: dict,
        *,
        journey_type: str,
        proof_profile: str,
    ) -> dict:
        parsed = {
            "device_id": self._safe_id(route_device_id),
            "client_id": self._safe_id(body["clientId"]),
            "journey_id": self._safe_id(body["journeyId"]),
            "journey_type": journey_type,
            "proof_profile": proof_profile,
            "transcript_plan": (),
            "hmac_key": bytearray(),
        }
        ttl_sec = body["ttlSec"]
        if (
            isinstance(ttl_sec, bool)
            or not isinstance(ttl_sec, int)
            or not 30 <= ttl_sec <= 3600
        ):
            raise ValueError
        parsed["ttl_sec"] = ttl_sec
        return parsed

    @staticmethod
    def _safe_id(value) -> str:
        if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
            raise ValueError
        return value

    @staticmethod
    def _error(status: int, code: str, message: str) -> web.Response:
        return web.json_response(
            {"error": code, "message": message},
            status=status,
            headers={"Cache-Control": "no-store"},
        )
