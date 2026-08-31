import base64
import json

import pytest

from core.api.google_live_evidence_handler import GoogleLiveEvidenceHandler
from core.voice.google_live.evidence_enrollment import (
    EnrollmentError,
    EvidenceEnrollmentRegistry,
    TranscriptExpectation,
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _plan(mac="a" * 64):
    return (TranscriptExpectation(slot=1, phase="interrupt", expected_mac=mac),)


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


def test_registry_binds_exact_normalized_peer_and_rejects_replacement():
    registry = EvidenceEnrollmentRegistry()
    enrollment = _register(registry)

    assert registry.ota_journey("aa:bb", "robot-client") == "physical.run-1"
    assert registry.ota_journey("aa:bb", "other-client") is None
    assert registry.claim(device_id="AA:BB", client_id="ROBOT-CLIENT", journey_id="physical.run-1") is enrollment
    assert enrollment.connected is True
    replacement_key = bytearray(b"r" * 32)
    with pytest.raises(EnrollmentError, match="ENROLLMENT_ACTIVE"):
        _register(registry, journey_id="physical.run-2", hmac_key=replacement_key)
    assert replacement_key == bytearray(32)


def test_registry_expires_and_zeroizes_without_exposing_secrets():
    clock = Clock()
    registry = EvidenceEnrollmentRegistry(clock=clock)
    enrollment = _register(registry)
    rendered = repr(enrollment) + repr(enrollment.transcript_plan[0])
    assert "kkkk" not in rendered
    assert "a" * 64 not in rendered

    clock.now += 121
    assert registry.ota_journey("aa:bb", "robot-client") is None
    assert enrollment.hmac_key == bytearray(32)
    snapshot = registry.safe_snapshot("physical.run-1")
    assert snapshot["status"] == "EXPIRED"
    assert "hmac" not in json.dumps(snapshot).lower()
    assert "a" * 64 not in json.dumps(snapshot)


def test_registry_finalization_is_safe_bounded_and_idempotent():
    registry = EvidenceEnrollmentRegistry(max_active=2, max_tombstones=2)
    first = _register(registry)
    result = registry.finalize("physical.run-1", status="PASS")
    assert result == registry.finalize("physical.run-1", status="PASS")
    assert first.hmac_key == bytearray(32)
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
