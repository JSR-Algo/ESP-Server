# ruff: noqa: B904, N818, SIM105, SIM108, UP006, UP035, UP045
import base64
import logging
import os
import re
from typing import Dict, Optional

import httpx

TAG = __name__


class DeviceNotFoundException(Exception):
    pass


class DeviceBindException(Exception):
    def __init__(self, bind_code):
        self.bind_code = bind_code
        super().__init__(f"Device binding exception, binding code: {bind_code}")


class ManageApiClient:
    _instance = None
    _async_clients = {}  # For eachEventLoop store independent client
    _secret = None

    def __new__(cls, config):
        """Singleton mode ensures globally unique instance and supports passing config parameters"""
        if cls._instance is None:
            instance = super().__new__(cls)
            cls._init_client(config)
            cls._instance = instance
        return cls._instance

    @classmethod
    def _init_client(cls, config):
        """Initialize config (lazy client creation)"""
        cls.config = config.get("manager-api")

        if not cls.config:
            raise Exception("manager-api config error")

        if not cls.config.get("url") or not cls.config.get("secret"):
            raise Exception("manager-api url or secret config error")

        if "You" in cls.config.get("secret"):
            raise Exception("Configure manager-api secret first")

        cls._secret = cls.config.get("secret")
        cls.max_retries = cls.config.get("max_retries", 6)  # Max retry count
        cls.retry_delay = cls.config.get("retry_delay", 10)  # Initial retry delay(seconds)
        # Do not create here AsyncClient, delay creation until actual use
        cls._async_clients = {}

    @classmethod
    async def _ensure_async_client(cls):
        """Ensure async context and create a request-scoped client"""
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError as exc:
            raise Exception("Must be called in async context") from exc

        # Server may actively close connection,httpx Connection pool cannot correctly detect and clean
        limits = httpx.Limits(
            max_keepalive_connections=0,  # Disable keep-alive, create new connection each time
        )
        return httpx.AsyncClient(
            base_url=cls.config.get("url"),
            headers={
                "User-Agent": f"PythonClient/2.0 (PID:{os.getpid()})",
                "Accept": "application/json",
                "Authorization": "Bearer " + cls._secret,
            },
            timeout=cls.config.get("timeout", 30),
            limits=limits,  # Usage Limit
        )

    @classmethod
    async def _async_request(cls, method: str, endpoint: str, **kwargs) -> Dict:
        """Send single async HTTP request and handle response"""
        client = None
        response = None
        primary_exception = None
        cleanup_exception = None
        try:
            # Ensure client created
            client = await cls._ensure_async_client()
            endpoint = endpoint.lstrip("/")
            response = await client.request(method, endpoint, **kwargs)
            response.raise_for_status()

            result = response.json()

            # ProcessAPIreturned businessError
            if result.get("code") == 10041:
                raise DeviceNotFoundException(result.get("msg"))
            elif result.get("code") == 10042:
                raise DeviceBindException(result.get("msg"))
            elif result.get("code") != 0:
                raise Exception(f"APIReturnError: {result.get('msg', 'Unknown error')}")

            # Return success data
            return result.get("data") if result.get("code") == 0 else None
        except BaseException as exc:
            primary_exception = exc
            raise
        finally:
            # EnsureResponsewas closed (even ifExceptionalso execute)
            if response is not None:
                try:
                    await response.aclose()
                except BaseException as exc:
                    if primary_exception is not None:
                        logging.getLogger(TAG).exception(
                            "Failed to close manager API response after primary exception"
                        )
                    elif cleanup_exception is None:
                        cleanup_exception = exc
                    else:
                        logging.getLogger(TAG).exception(
                            "Failed to close manager API response after another cleanup exception"
                        )
            if client is not None:
                try:
                    await client.aclose()
                except BaseException as exc:
                    if primary_exception is not None:
                        logging.getLogger(TAG).exception(
                            "Failed to close manager API client after primary exception"
                        )
                    elif cleanup_exception is None:
                        cleanup_exception = exc
                    else:
                        logging.getLogger(TAG).exception(
                            "Failed to close manager API client after another cleanup exception"
                        )
            if primary_exception is None and cleanup_exception is not None:
                raise cleanup_exception

    @classmethod
    def _should_retry(cls, exception: Exception) -> bool:
        """Determine whether exception should retry"""
        # Network connection relatedError
        if isinstance(
            exception, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)
        ):
            return True

        # HTTPStatuscodeError
        if isinstance(exception, httpx.HTTPStatusError):
            status_code = exception.response.status_code
            return status_code in [408, 429, 500, 502, 503, 504]

        return False

    @classmethod
    async def _execute_async_request(cls, method: str, endpoint: str, **kwargs) -> Dict:
        """Async request executor with retry mechanism"""
        import asyncio

        retry_count = 0

        while retry_count <= cls.max_retries:
            try:
                # Execute async request
                return await cls._async_request(method, endpoint, **kwargs)
            except Exception as e:
                # Decide whether should retry
                if retry_count < cls.max_retries and cls._should_retry(e):
                    retry_count += 1
                    print(
                        f"{method} {endpoint} Async request failed, will {cls.retry_delay:.1f} seconds later do # {retry_count} Retries"
                    )
                    await asyncio.sleep(cls.retry_delay)
                    continue
                else:
                    # No retry, throw directlyException
                    raise

    @classmethod
    def safe_close(cls):
        """Safely close all async connection pools"""
        import asyncio

        clients = list(cls._async_clients.values())
        cls._async_clients.clear()
        cls._instance = None

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            loop.create_task(cls._close_async_clients(clients))
            return

        for client in clients:
            try:
                asyncio.run(client.aclose())
            except Exception:
                pass

    @classmethod
    async def _close_async_clients(cls, clients):
        for client in clients:
            try:
                await client.aclose()
            except Exception:
                pass


async def get_server_config() -> Optional[Dict]:
    """Get server base config"""
    return await ManageApiClient._instance._execute_async_request(
        "POST", "/config/server-base"
    )


async def get_agent_models(
    mac_address: str, client_id: str, selected_module: Dict
) -> Optional[Dict]:
    """Get proxy model config"""
    return await ManageApiClient._instance._execute_async_request(
        "POST",
        "/config/agent-models",
        json={
            "macAddress": mac_address,
            "clientId": client_id,
            "selectedModule": selected_module,
        },
    )


async def get_correct_words(mac_address: str) -> Optional[Dict]:
    """Get agent replacement words"""
    try:
        return await ManageApiClient._instance._execute_async_request(
            "POST", "/config/correct-words",
            json={"macAddress": mac_address}
        )
    except Exception as e:
        print(f"GetReplacement wordFail: {e}")
        return None


async def generate_and_save_chat_summary(session_id: str) -> Optional[Dict]:
    """Generate and save chat history summary"""
    try:
        return await ManageApiClient._instance._execute_async_request(
            "POST",
            f"/agent/chat-summary/{session_id}/save",
        )
    except Exception as e:
        print(f"Generate and save chat history summaryFail: {e}")
        return None


async def generate_and_save_chat_title(session_id: str) -> Optional[Dict]:
    """Generate and save chat title"""
    try:
        return await ManageApiClient._instance._execute_async_request(
            "POST",
            f"/agent/chat-title/{session_id}/generate",
        )
    except Exception as e:
        print(f"Generate and save chat titleFail: {e}")
        return None


async def report(
    mac_address: str, session_id: str, chat_type: int, content: str, audio, report_time
) -> Optional[Dict]:
    """Async chat record reporting"""
    if not content or not ManageApiClient._instance:
        return None
    try:
        return await ManageApiClient._instance._execute_async_request(
            "POST",
            "/agent/chat-history/report",
            json={
                "macAddress": mac_address,
                "sessionId": session_id,
                "chatType": chat_type,
                "content": content,
                "reportTime": report_time,
                "audioBase64": (
                    base64.b64encode(audio).decode("utf-8") if audio else None
                ),
            },
        )
    except Exception as e:
        print(f"TTSReport Failed: {e}")
        return None


def init_service(config):
    ManageApiClient(config)


def manage_api_http_safe_close():
    ManageApiClient.safe_close()


# ─────────────────────────────────────────────────────────────────────────────
# Lesson runtime backend legs (US-006 LANE-ESP: S6 pull-on-connect + S9 forward).
# Restored ADDITIVELY from deploy/lesson-voice after the prod-unify graft (4666631b)
# dropped them; core/lesson/runtime.py (get_current_assignment, get_lesson_manifest)
# and core/lesson/forwarder.py (post_lesson_event) bind these by attribute at call time.
#
# DELIBERATE DIVERGENCE FROM plan §6.4/§6.5b (surfaced, not silently reconciled):
# the plan said reuse the manager-api ``ManageApiClient`` (base ``manager-api.url``
# + that service's Bearer). Live config (config.yaml:21-28, locked 2026-06-04) makes
# ``server.api_url`` — the NestJS backend, ``.../v1`` — the SOLE authority for the
# ``/v1/devices/*`` + ``/v1/lessons/*`` routes; the Java manager-api is admin/legacy
# only and does NOT serve them. So these legs take their OWN caller-owned httpx
# client/base_url. ``result -> outcome`` is renamed HERE as the single translation point.
#
# D-RUNTOKEN (ADR 0013 §F): callers must resolve MAC -> backend device UUID and
# present a device-scoped JWT before hitting lesson device routes. The function
# signatures keep ``token`` optional only for tests/older call sites; production
# runtime skips tokenless pulls instead of creating a swallowed backend 401.
# ─────────────────────────────────────────────────────────────────────────────

_LESSON_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def _lesson_is_transient(exc: Exception) -> bool:
    if httpx is not None and isinstance(
        exc, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)
    ):
        return True
    if httpx is not None and isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _LESSON_RETRYABLE_STATUS
    return False


def _lesson_auth_headers(token: Optional[str]) -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers

def _lesson_trace_headers(batch: Optional[Dict]) -> Dict[str, str]:
    if not isinstance(batch, dict):
        return {}
    headers: Dict[str, str] = {}
    for key in ("traceparent", "tracestate"):
        value = batch.get(key)
        if isinstance(value, str) and value.strip():
            headers[key] = value.strip()
    return headers


LESSON_EVENT_SENSITIVE_DETAIL_KEYS = {
    "utterance",
    "childutterance",
    "transcript",
    "asrtranscript",
    "rawspeech",
    "speechtext",
    "asrtext",
    "voicetext",
    "recognizedtext",
    "childresponse",
    "usertranscript",
    "userutterance",
    "score",
    "pronunciation",
    "pronunciationscore",
    "pronunciationassessment",
    "phonemescore",
    "phonemeassessment",
    "grade",
    "correct",
    "iscorrect",
    "correctness",
    "assessment",
    "evaluation",
    "confidence",
    "audio",
    "audiobase64",
    "audiodata",
    "debug",
    "debuginfo",
    "debugdata",
}

def _lesson_sensitive_key(key: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "", str(key)).lower()

def _is_lesson_sensitive_key(key: str) -> bool:
    normalized = _lesson_sensitive_key(key)
    return (
        normalized in LESSON_EVENT_SENSITIVE_DETAIL_KEYS
        or "transcript" in normalized
        or "utterance" in normalized
        or "audio" in normalized
        or "confidence" in normalized
        or "debug" in normalized
        or "score" in normalized
        or "pronunciation" in normalized
        or "phoneme" in normalized
        or "assessment" in normalized
    )

def _strip_lesson_sensitive_fields(value):
    if isinstance(value, list):
        return [_strip_lesson_sensitive_fields(item) for item in value]
    if isinstance(value, dict):
        return {
            k: _strip_lesson_sensitive_fields(v)
            for k, v in value.items()
            if not _is_lesson_sensitive_key(k)
        }
    return value

_LESSON_EVENT_DETAIL_FIELDS = (
    "choiceId",
    "source",
    "responseClass",
    "interactionTemplate",
    "funPattern",
    "reason",
    "tapTargetHit",
    "accepted",
    "handled",
    "recognized",
    "attempts",
)

def _allowlisted_lesson_detail(value) -> Optional[Dict]:
    if not isinstance(value, dict):
        return None
    sanitized = _strip_lesson_sensitive_fields(value)
    detail = {
        key: sanitized[key]
        for key in _LESSON_EVENT_DETAIL_FIELDS
        if key in sanitized and sanitized[key] is not None
    }
    return detail or None

def _copy_lesson_event_fields(out: Dict, event: Dict, fields) -> None:
    for key in fields:
        if key in event and event[key] is not None:
            out[key] = event[key]

def _normalize_lesson_event(event: Dict) -> Dict:
    """Single ``result -> outcome`` translation point (plan §5.7/§6.4.1).

    Strip child speech text and immediate assessment fields before the event body
    leaves ESP; backend repeats the same boundary check before persistence.
    """
    if not isinstance(event, dict):
        return {}
    sanitized = _strip_lesson_sensitive_fields(event)
    event_type = sanitized.get("type")
    out: Dict = {}
    _copy_lesson_event_fields(
        out,
        sanitized,
        (
            "type", "sequence", "stepId", "occurredAt", "startedAt",
            "completedAt", "abandonedAt", "failedAt",
        ),
    )
    outcome = sanitized.get("outcome", sanitized.get("result"))
    if outcome is not None:
        out["outcome"] = outcome

    if event_type in {"step_started", "step_completed", "answer_recorded", "lesson_abandoned", "lesson_failed", "runtime_phase_changed"}:
        _copy_lesson_event_fields(out, sanitized, ("stepType",))
    if event_type in {"step_completed", "answer_recorded"}:
        detail = _allowlisted_lesson_detail(sanitized.get("detail"))
        if detail is not None:
            out["detail"] = detail
    if event_type in {"step_started", "step_completed"}:
        _copy_lesson_event_fields(
            out,
            sanitized,
            ("retryCount", "renderDegraded", "sramFreeBytes", "psramFreeBytes", "motionDispatch"),
        )
    if event_type == "runtime_phase_changed":
        _copy_lesson_event_fields(out, sanitized, ("phase",))
    if event_type == "story_progress":
        _copy_lesson_event_fields(out, sanitized, ("petReaction", "unitGrowth", "nextTease"))
    if event_type in {"lesson_abandoned", "lesson_failed", "protocol_error"}:
        _copy_lesson_event_fields(out, sanitized, ("reason", "code"))
    if event_type == "protocol_error":
        _copy_lesson_event_fields(out, sanitized, ("retryable",))
    if event_type == "lesson_completed" and isinstance(sanitized.get("summary"), dict):
        steps_completed = sanitized["summary"].get("stepsCompleted")
        if steps_completed is not None:
            out["summary"] = {"stepsCompleted": steps_completed}
    if event_type in {"step_completed", "lesson_completed", "lesson_failed", "lesson_abandoned"}:
        _copy_lesson_event_fields(out, sanitized, ("totalAttempts",))
    return out


async def _lesson_request_with_retry(
    client,
    method: str,
    url: str,
    *,
    max_retries: int = 2,
    retry_delay: float = 1.0,
    return_headers: bool = False,
    **kwargs,
):
    """Transient-retry wrapper shared by every lesson leg (S6 assignment, S6
    manifest, S9 event forward) so a single 5xx/network blip can't abort the pull.

    Default return is the decoded JSON body (or ``None`` on 204/empty), keeping the
    assignment/event call-sites byte-identical. When ``return_headers`` is True the
    return is the ``(body, headers)`` tuple so the manifest leg can read ``ETag``;
    ``body`` is ``None`` on a 204/empty response while ``headers`` is still surfaced.
    """
    import asyncio

    attempt = 0
    while True:
        response = None
        try:
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            if response.status_code == 204 or not response.content:
                body = None
            else:
                body = response.json()
            if return_headers:
                return body, response.headers
            return body
        except Exception as exc:  # noqa: BLE001 - retry decision is explicit below
            if attempt < max_retries and _lesson_is_transient(exc):
                attempt += 1
                await asyncio.sleep(retry_delay)
                continue
            raise
        finally:
            if response is not None:
                await response.aclose()


def _lesson_base(base_url: str) -> str:
    return (base_url or "").rstrip("/")

_ASSIGNMENT_KEY_ALIASES = {
    "assignment_id": "assignmentId",
    "assignment_version": "assignmentVersion",
    "device_id": "deviceId",
    "child_id": "childId",
    "lesson_id": "lessonId",
    "lesson_title": "lessonTitle",
    "lesson_version": "lessonVersion",
    "manifest_checksum": "manifestChecksum",
    "created_at": "createdAt",
}

def _normalize_assignment_payload(assignment):
    if not isinstance(assignment, dict):
        return None
    normalized = dict(assignment)
    for snake_key, camel_key in _ASSIGNMENT_KEY_ALIASES.items():
        if camel_key not in normalized and snake_key in assignment:
            normalized[camel_key] = assignment[snake_key]
    return normalized


async def get_current_assignment(
    client,
    base_url: str,
    device_id: str,
    *,
    token: Optional[str] = None,
    include_terminal: bool = False,
) -> Optional[Dict]:
    """S6 pull-on-connect — GET /v1/devices/:deviceId/assignment/current.

    Returns the ``data.assignment`` object (or ``None`` when the device has no
    active assignment). The authoritative offline-catch-up hand-off (ADR 0013 §A/§B).

    ``include_terminal`` asks the backend to answer with the most recent TERMINAL
    assignment when none is active. It exists for the post-completion read-back only:
    a released slot and a LOST completion are indistinguishable otherwise. It must stay
    off for pull-on-connect, which asks what to run next and must never be handed a
    finished lesson.
    """
    url = f"{_lesson_base(base_url)}/devices/{device_id}/assignment/current"
    if include_terminal:
        url = f"{url}?includeTerminal=true"
    payload = await _lesson_request_with_retry(
        client, "GET", url, headers=_lesson_auth_headers(token)
    )
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") or {}
    return _normalize_assignment_payload(data.get("assignment"))


async def get_device_child_name(
    client, base_url: str, device_id: str, *, token: Optional[str] = None
) -> Optional[str]:
    """Device-leg read of the bound child's display name — GET
    /v1/devices/:deviceId/child-profile.

    The name a parent sets in the mobile app (Parent Settings) lands in the
    backend ``child_profiles.display_name``; the robot reads it here so Google
    Live can address the child by name in conversation. This is required because
    the esp manager-api ``ai_device.child_name`` column lives in a SEPARATE
    keyspace the mobile cannot write (migration 086) — the agent-models config the
    robot loads from the manager-api therefore carries no name for mobile-only
    households. Returns the trimmed name, or ``None`` when no child is bound /
    the name is blank (the caller then leaves the existing config value alone).
    """
    url = f"{_lesson_base(base_url)}/devices/{device_id}/child-profile"
    # Best-effort, single attempt (max_retries=0): unlike the authoritative lesson
    # pull, a missed name is a graceful degradation (generic greeting), so this must
    # never add retry latency to connect — fail fast and let the caller swallow it.
    payload = await _lesson_request_with_retry(
        client, "GET", url, headers=_lesson_auth_headers(token), max_retries=0
    )
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") or {}
    name = data.get("childName")
    return name.strip() if isinstance(name, str) and name.strip() else None


async def get_lesson_manifest(
    client,
    base_url: str,
    lesson_id: str,
    profile: str,
    *,
    token: Optional[str] = None,
    renderer_capabilities: Optional[list] = None,
    renderer_v2_enabled: bool = False,
    lesson_version: Optional[int] = None,
):
    """S6 — GET /v1/lessons/:lessonId/manifest?profile=... Returns ``(manifest, etag)``.

    The manifest's ``steps[].scene`` is the frozen 3-layer scene the ESP projects
    verbatim into ``lesson_step``; ``assets[]`` carry the critical sha256 set.

    L3 P3 — renderer-capability negotiation. ``renderer_capabilities`` is the
    device's advertised renderer-version set (e.g. ``['teebot-lesson-renderer.v1']``).
    When provided, it is forwarded to the backend manifest endpoint BOTH as a
    ``rendererCapabilities`` query param (comma-joined) AND as an
    ``X-Renderer-Capabilities`` header, so the backend can serve a manifest the
    device can actually render (it defaults to v1 backend-side). When OMITTED
    (older call sites / tests), the request is byte-identical to today's v1 call —
    no extra param, no extra header. This stays renderer v1: no protocol-version
    change today; the negotiation is a structural guard for when a v2 renderer ships.
    """
    # Early guard: a falsy lesson_id would build '/lessons//manifest' (a 404 the
    # device can't recover from). Bail before the request — no assignment, no pull.
    if not lesson_id:
        return None, None
    url = f"{_lesson_base(base_url)}/lessons/{lesson_id}/manifest"
    params: Dict[str, str] = {"profile": profile}
    if lesson_version:
        params["version"] = str(int(lesson_version))
    headers = _lesson_auth_headers(token)
    if renderer_capabilities:
        capabilities = [
            capability
            for capability in renderer_capabilities
            if capability != "teebot-lesson-renderer.v2" or renderer_v2_enabled is True
        ]
        if not capabilities:
            capabilities = ["teebot-lesson-renderer.v1"]
        joined = ",".join(dict.fromkeys(capabilities))
        params["rendererCapabilities"] = joined
        headers["X-Renderer-Capabilities"] = joined
    # Route through the shared retry helper (transient 5xx/network blips no longer
    # abort the pull) while still surfacing the ETag header the device needs.
    body, response_headers = await _lesson_request_with_retry(
        client, "GET", url, params=params, headers=headers, return_headers=True
    )
    etag = None
    if response_headers is not None:
        etag = response_headers.get("ETag") or response_headers.get("etag")
    manifest = (body.get("data") or {}).get("manifest") if isinstance(body, dict) else None
    return manifest, etag


async def post_lesson_event(
    client, base_url: str, device_id: str, batch: Dict, *, token: Optional[str] = None
) -> Optional[Dict]:
    """S9 — POST /v1/devices/:deviceId/lesson-events (OWN dispatch path, NOT the
    chat-history report queue). Owns the single wire ``result -> outcome`` rename and
    the COPPA child-speech strip before the body leaves the ESP (plan §6.4)."""
    url = f"{_lesson_base(base_url)}/devices/{device_id}/lesson-events"
    body = {
        key: batch[key]
        for key in (
            "assignmentId", "lessonId", "lessonVersion", "sessionId",
            "traceparent", "tracestate",
        )
        if key in batch and batch[key] is not None
    }
    body["events"] = [
        _normalize_lesson_event(event)
        for event in batch.get("events", [])
        if isinstance(event, dict)
    ]
    headers = _lesson_auth_headers(token)
    headers.update(_lesson_trace_headers(batch))
    payload = await _lesson_request_with_retry(
        client, "POST", url, json=body, headers=headers
    )
    if isinstance(payload, dict):
        return payload.get("data") or payload
    return None

def _normalize_preload_status_report(report: Dict) -> Dict:
    body = {
        "assignmentId": report.get("assignmentId"),
        "assetId": report.get("assetId"),
        "state": report.get("state"),
    }
    if report.get("checksumOk") is not None:
        body["checksumOk"] = bool(report.get("checksumOk"))
    return body

async def post_preload_status(
    client,
    base_url: str,
    device_id: str,
    report: Dict,
    *,
    token: Optional[str] = None,
    max_retries: int = 2,
    retry_delay: float = 1.0,
) -> Optional[Dict]:
    """POST /v1/devices/:deviceId/preload-status for one asset preload outcome.

    Caller owns the httpx client so runtime code can use a short-lived client from
    a fire-and-forget task after the assignment/manifest pull client has closed.
    """
    url = f"{_lesson_base(base_url)}/devices/{device_id}/preload-status"
    payload = await _lesson_request_with_retry(
        client,
        "POST",
        url,
        json=_normalize_preload_status_report(report),
        headers=_lesson_auth_headers(token),
        max_retries=max_retries,
        retry_delay=retry_delay,
    )
    if isinstance(payload, dict):
        return payload.get("data") or payload
    return None

async def post_lesson_sd_sync_result(
    client,
    base_url: str,
    result: Dict,
    *,
    mint_secret: str,
) -> Optional[Dict]:
    """POST one device SD-pack sync result to the lesson backend."""
    if not isinstance(result, dict):
        raise ValueError("result must be a dict")
    device_id = str(result.get("deviceId") or "").strip()
    cache_key = str(result.get("cacheKey") or "").strip()
    if not device_id or not cache_key:
        raise ValueError("result requires deviceId and cacheKey")
    secret = str(mint_secret or "").strip()
    if not secret:
        raise ValueError("mint_secret is required")
    response = await client.post(
        f"{_lesson_base(str(base_url or '').rstrip('/'))}/internal/lesson-asset-sync/device-result",
        json=_normalize_lesson_sd_sync_result(result),
        headers={"Accept": "application/json", "X-Mint-Secret": secret},
        follow_redirects=False,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        data = payload.get("data")
        return data if isinstance(data, dict) else data
    return None

def _normalize_lesson_sd_sync_result(result: Dict) -> Dict:
    from core.lesson.sd_pack_sync import normalize_lesson_sd_error_code

    def count(name: str) -> int:
        try:
            value = int(result.get(name) or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, value)

    body = {
        "deviceId": str(result.get("deviceId") or "").strip(),
        "cacheKey": str(result.get("cacheKey") or "").strip(),
        "downloadedCount": count("downloadedCount"),
        "skippedCount": count("skippedCount"),
        "failedCount": count("failedCount"),
        "criticalFailedCount": count("criticalFailedCount"),
        "ready": bool(result.get("ready")),
    }
    error_code = normalize_lesson_sd_error_code(result.get("errorCode"))
    if error_code:
        body["errorCode"] = error_code
    return body
