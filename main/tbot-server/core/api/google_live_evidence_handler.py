import base64
import hmac
import os
import re

from aiohttp import web

from core.voice.google_live.evidence_enrollment import (
    EnrollmentError,
    EvidenceEnrollmentRegistry,
    TranscriptExpectation,
)

NORMALIZATION_VERSION = "google-live-transcript-nfkc-casefold.v1"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAC = re.compile(r"^[0-9a-f]{64}$")
_POST_FIELDS = {
    "clientId",
    "journeyId",
    "ttlSec",
    "normalizationVersion",
    "hmacKeyBase64",
    "transcriptPlan",
}
_PLAN_FIELDS = {"slot", "phase", "expectedMac"}


class GoogleLiveEvidenceHandler:
    def __init__(self, registry: EvidenceEnrollmentRegistry):
        self.registry = registry

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
            snapshot = self.registry.safe_snapshot(parsed["journey_id"])
        except EnrollmentError as exc:
            code = str(exc)
            if code in ("JOURNEY_REUSED", "ENROLLMENT_ACTIVE"):
                return self._error(409, code, "Evidence enrollment conflicts with active state")
            if code == "CAPACITY_EXCEEDED":
                return self._error(503, code, "Evidence enrollment capacity is exhausted")
            return self._error(400, "INVALID_REQUEST", "Invalid Google Live evidence enrollment request")
        except Exception:
            return self._error(400, "INVALID_REQUEST", "Invalid Google Live evidence enrollment request")
        return web.json_response(snapshot, status=201, headers={"Cache-Control": "no-store"})

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
        if not isinstance(body, dict) or set(body) != _POST_FIELDS:
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
        return {
            "device_id": device_id,
            "client_id": client_id,
            "journey_id": journey_id,
            "transcript_plan": tuple(expectations),
            "hmac_key": bytearray(decoded),
            "ttl_sec": ttl_sec,
        }

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
