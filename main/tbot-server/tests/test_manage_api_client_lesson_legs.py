"""CR-RUN-01 — isolated CONTRACT test pinning the 3 robot-server lesson legs.

``config.manage_api_client`` exposes three caller-owned-client lesson functions —
``get_current_assignment`` (S6 pull), ``get_lesson_manifest`` (S6 manifest) and
``post_lesson_event`` (S9 forward) — that ``core.lesson.runtime`` /
``core.lesson.forwarder`` bind by attribute at call time. A prod-unify graft
(4666631b) once silently DROPPED all three; CI stayed green because nothing pinned
their wire shape in isolation. This file is that pin: each leg's exact GET/POST
verb + URL, query params (incl ``version``), the ``X-Renderer-Capabilities`` header
+ ``rendererCapabilities`` param, the ``Authorization`` bearer, and the
``result -> outcome`` rename + COPPA child-speech strip on the POST body.

NO network, NO real backend: every leg takes its own httpx-shaped client, so a
``_RecordingClient`` (records method/url/params/headers/json and returns a canned
``_FakeResponse``) exercises the REAL function body end to end. Async tests use
``unittest.IsolatedAsyncioTestCase`` (this repo does not use pytest-asyncio markers),
mirroring tests/test_lesson_forwarder.py.
"""

import importlib.util
import os
import unittest


def _load_real_manage_api_client():
    """Load a fresh, isolated copy of the real ``config.manage_api_client`` from
    disk (mirrors the helper in tests/test_lesson_runtime.py) so the contract is
    asserted against the on-disk source even if another test monkeypatches the
    shared module instance."""
    spec = importlib.util.spec_from_file_location(
        "config._mac_real_for_lesson_legs_test",
        os.path.join(os.path.dirname(__file__), "..", "config", "manage_api_client.py"),
    )
    mac = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mac)
    return mac


MAC = _load_real_manage_api_client()


# ── httpx-shaped fakes (no network) ──────────────────────────────────────────────


class _FakeResponse:
    """Minimal httpx.Response stand-in: the only surface the lesson legs touch is
    ``raise_for_status`` / ``status_code`` / ``content`` / ``json`` / ``headers`` /
    ``aclose``."""

    def __init__(self, *, status_code=200, json_body=None, headers=None, content=b"x"):
        self.status_code = status_code
        self._json_body = json_body
        self.headers = headers or {}
        # ``content`` is consulted to detect 204/empty; keep it truthy for a body.
        self.content = b"" if json_body is None else (content or b"x")
        self.closed = False

    def raise_for_status(self):
        return None

    def json(self):
        return self._json_body

    async def aclose(self):
        self.closed = True


class _RecordingClient:
    """Caller-owned client the lesson legs receive as their first positional arg.

    Records every ``request(method, url, **kwargs)`` call and returns a queued
    ``_FakeResponse``. This is the REAL collaborator interface the production code
    uses (``await client.request(...)``); nothing about the behaviour under test is
    stubbed out — only the socket is replaced."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []  # list of dicts: {method, url, params, headers, json}

    async def request(self, method, url, **kwargs):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": kwargs.get("params"),
                "headers": kwargs.get("headers"),
                "json": kwargs.get("json"),
            }
        )
        if not self._responses:
            raise AssertionError("client.request called more times than queued responses")
        return self._responses.pop(0)

    @property
    def last(self):
        return self.calls[-1]


BASE = "http://backend.test/v1"
TOKEN = "dev-jwt-123"


# ── S6 assignment leg ────────────────────────────────────────────────────────────


class GetCurrentAssignmentContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_pins_get_url_bearer_accept_and_unwraps_data_assignment(self):
        assignment = {"assignmentId": "a1", "lessonId": "w01-d01-barn-say-it", "state": "ASSIGNED"}
        client = _RecordingClient([_FakeResponse(json_body={"data": {"assignment": assignment}})])

        result = await MAC.get_current_assignment(
            client, BASE, "device-uuid-9", token=TOKEN
        )

        # Result-to-outcome of the pull: the bare ``data.assignment`` object.
        self.assertEqual(result, assignment)
        self.assertEqual(len(client.calls), 1)
        call = client.last
        self.assertEqual(call["method"], "GET")
        # Exact URL — :deviceId interpolated, single /v1, no trailing junk.
        self.assertEqual(
            call["url"], "http://backend.test/v1/devices/device-uuid-9/assignment/current"
        )
        # No query params on this leg.
        self.assertIsNone(call["params"])
        # Auth + Accept headers — the device-scoped bearer (D-RUNTOKEN).
        self.assertEqual(call["headers"]["Authorization"], "Bearer dev-jwt-123")
        self.assertEqual(call["headers"]["Accept"], "application/json")

    async def test_normalizes_snake_case_assignment_contract_before_runtime_metadata_gate(self):
        assignment = {
            "assignment_id": "a1",
            "assignment_version": 2,
            "lesson_id": "w01-d01-barn-say-it",
            "lesson_title": "Barn — Say It",
            "lesson_version": 3,
            "manifest_checksum": "9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
            "child_id": "child-1",
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        client = _RecordingClient([_FakeResponse(json_body={"data": {"assignment": assignment}})])

        result = await MAC.get_current_assignment(client, BASE, "device-uuid-9", token=TOKEN)

        self.assertEqual(result["assignmentId"], "a1")
        self.assertEqual(result["assignmentVersion"], 2)
        self.assertEqual(result["lessonId"], "w01-d01-barn-say-it")
        self.assertEqual(result["lessonTitle"], "Barn — Say It")
        self.assertEqual(result["lessonVersion"], 3)
        self.assertEqual(
            result["manifestChecksum"],
            "9b1f7c2a5d3e8f04a6c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
        )
        self.assertEqual(result["childId"], "child-1")
        self.assertEqual(result["profile"], "espTft")
        self.assertEqual(result["state"], "ASSIGNED")

    async def test_no_token_omits_authorization_header(self):
        client = _RecordingClient([_FakeResponse(json_body={"data": {"assignment": None}})])

        result = await MAC.get_current_assignment(client, BASE, "device-uuid-9")

        self.assertIsNone(result)  # data.assignment is null -> None
        self.assertNotIn("Authorization", client.last["headers"])
        self.assertEqual(client.last["headers"]["Accept"], "application/json")

    async def test_base_url_trailing_slash_does_not_double_slash(self):
        client = _RecordingClient([_FakeResponse(json_body={"data": {}})])

        result = await MAC.get_current_assignment(client, BASE + "/", "dev1", token=TOKEN)

        # data present but no assignment key -> None.
        self.assertIsNone(result)
        self.assertEqual(
            client.last["url"], "http://backend.test/v1/devices/dev1/assignment/current"
        )


# ── S6 manifest leg ──────────────────────────────────────────────────────────────


class GetLessonManifestContractTest(unittest.IsolatedAsyncioTestCase):
    def _manifest_response(self, *, etag='"lesson-7-espTft-9b1f7c2a"'):
        manifest = {"lessonId": "w01-d01-barn-say-it", "steps": [{"id": "s4"}]}
        return _FakeResponse(
            json_body={"data": {"manifest": manifest}},
            headers={"ETag": etag},
        ), manifest

    async def test_pins_get_url_profile_param_and_unwraps_manifest_and_etag(self):
        response, manifest = self._manifest_response()
        client = _RecordingClient([response])

        got_manifest, etag = await MAC.get_lesson_manifest(
            client, BASE, "w01-d01-barn-say-it", "espTft", token=TOKEN
        )

        self.assertEqual(got_manifest, manifest)
        self.assertEqual(etag, '"lesson-7-espTft-9b1f7c2a"')
        call = client.last
        self.assertEqual(call["method"], "GET")
        self.assertEqual(
            call["url"], "http://backend.test/v1/lessons/w01-d01-barn-say-it/manifest"
        )
        # profile is always present; version absent when not supplied.
        self.assertEqual(call["params"], {"profile": "espTft"})
        self.assertEqual(call["headers"]["Authorization"], "Bearer dev-jwt-123")
        # No renderer negotiation when capabilities are omitted.
        self.assertNotIn("X-Renderer-Capabilities", call["headers"])

    async def test_version_is_stringified_into_query_params(self):
        response, _ = self._manifest_response()
        client = _RecordingClient([response])

        await MAC.get_lesson_manifest(
            client, BASE, "lesson-7", "espTft", token=TOKEN, lesson_version=3
        )

        self.assertEqual(client.last["params"], {"profile": "espTft", "version": "3"})

    async def test_renderer_capabilities_set_both_param_and_header(self):
        response, _ = self._manifest_response()
        client = _RecordingClient([response])

        await MAC.get_lesson_manifest(
            client,
            BASE,
            "lesson-7",
            "espTft",
            token=TOKEN,
            renderer_capabilities=["teebot-lesson-renderer.v1", "teebot-lesson-renderer.v2"],
            lesson_version=9,
        )

        call = client.last
        joined = "teebot-lesson-renderer.v1,teebot-lesson-renderer.v2"
        # Comma-joined in BOTH the query param AND the dedicated header.
        self.assertEqual(
            call["params"],
            {"profile": "espTft", "version": "9", "rendererCapabilities": joined},
        )
        self.assertEqual(call["headers"]["X-Renderer-Capabilities"], joined)
        self.assertEqual(call["headers"]["Authorization"], "Bearer dev-jwt-123")

    async def test_falsy_lesson_id_short_circuits_before_any_request(self):
        client = _RecordingClient([])  # no response queued: a request would AssertionError

        manifest, etag = await MAC.get_lesson_manifest(client, BASE, "", "espTft", token=TOKEN)

        self.assertIsNone(manifest)
        self.assertIsNone(etag)
        self.assertEqual(client.calls, [])  # the early guard never hit the wire

    async def test_lowercase_etag_header_is_read(self):
        response, manifest = self._manifest_response(etag=None)
        response.headers = {"etag": "lower-case-tag"}
        client = _RecordingClient([response])

        got_manifest, etag = await MAC.get_lesson_manifest(
            client, BASE, "lesson-7", "espTft"
        )

        self.assertEqual(got_manifest, manifest)
        self.assertEqual(etag, "lower-case-tag")
        self.assertNotIn("Authorization", client.last["headers"])


# ── S9 event forward leg ─────────────────────────────────────────────────────────


class PostLessonEventContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_pins_post_url_bearer_result_to_outcome_rename_and_coppa_strip(self):
        client = _RecordingClient([_FakeResponse(json_body={"data": {"accepted": 1}})])
        batch = {
            "assignmentId": "a1",
            "sessionId": "s1",
            "events": [
                {
                    "type": "step_completed",
                    "stepId": "listen-1",
                    "result": "success",  # -> renamed to "outcome"
                    "detail": {
                        # COPPA child-speech fields must be stripped before the wire.
                        "recognizedText": "con noi barn",
                        "childResponse": "barn",
                        "transcript": "barn raw",
                        "utterance": "Yes! barn!",
                        "source": "voice_transcript",  # non-speech field kept
                        "nested": {"recognized_text": "nested raw", "keep": 1},
                    },
                    "attempts": [{"ChildResponse": "attempt raw", "kept": True}],
                }
            ],
        }

        data = await MAC.post_lesson_event(client, BASE, "device-uuid-9", batch, token=TOKEN)

        # Returned payload unwraps ``data``.
        self.assertEqual(data, {"accepted": 1})

        call = client.last
        self.assertEqual(call["method"], "POST")
        self.assertEqual(
            call["url"], "http://backend.test/v1/devices/device-uuid-9/lesson-events"
        )
        self.assertEqual(call["headers"]["Authorization"], "Bearer dev-jwt-123")
        self.assertEqual(call["headers"]["Accept"], "application/json")

        sent_event = call["json"]["events"][0]
        # result -> outcome rename happened; bare "result" key is gone.
        self.assertEqual(sent_event["outcome"], "success")
        self.assertNotIn("result", sent_event)
        # COPPA strip: every child-speech field removed at every nesting depth,
        # including underscore/case variants, while non-speech fields survive.
        self.assertEqual(
            sent_event["detail"],
            {"source": "voice_transcript", "nested": {"keep": 1}},
        )
        self.assertEqual(sent_event["attempts"], [{"kept": True}])
        # Envelope metadata around the events is preserved verbatim.
        self.assertEqual(call["json"]["assignmentId"], "a1")
        self.assertEqual(call["json"]["sessionId"], "s1")

    async def test_does_not_mutate_caller_batch(self):
        client = _RecordingClient([_FakeResponse(json_body={"data": None})])
        batch = {
            "assignmentId": "a1",
            "events": [{"type": "step_completed", "result": "success", "detail": {"transcript": "raw"}}],
        }

        await MAC.post_lesson_event(client, BASE, "dev1", batch, token=TOKEN)

        # The caller's original batch is untouched: original "result" and child
        # speech still present (the rename/strip happen on a copy).
        self.assertEqual(batch["events"][0]["result"], "success")
        self.assertEqual(batch["events"][0]["detail"], {"transcript": "raw"})
        # But the wire body shows the rename + strip.
        sent_event = client.last["json"]["events"][0]
        self.assertEqual(sent_event["outcome"], "success")
        self.assertNotIn("result", sent_event)
        # detail held only a child-speech field; once stripped it is empty, and
        # _normalize_lesson_event DROPS an emptied detail (it is not sent as {}).
        self.assertNotIn("detail", sent_event)

    async def test_no_token_post_omits_authorization(self):
        client = _RecordingClient([_FakeResponse(json_body={"data": {"accepted": 0}})])

        await MAC.post_lesson_event(client, BASE, "dev1", {"events": []})

        self.assertNotIn("Authorization", client.last["headers"])
        self.assertEqual(client.last["json"]["events"], [])


if __name__ == "__main__":
    unittest.main()
