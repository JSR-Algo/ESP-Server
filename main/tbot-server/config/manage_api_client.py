import os
import base64
from typing import Optional, Dict

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
            cls._instance = super().__new__(cls)
            cls._init_client(config)
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
        """Ensure async client created (separate client per event loop)"""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            loop_id = id(loop)

            # For eachEventLoop create independent client
            if loop_id not in cls._async_clients:
                # Server may actively close connection,httpx Connection pool cannot correctly detect and clean
                limits = httpx.Limits(
                    max_keepalive_connections=0,  # Disable keep-alive, create new connection each time
                )
                cls._async_clients[loop_id] = httpx.AsyncClient(
                    base_url=cls.config.get("url"),
                    headers={
                        "User-Agent": f"PythonClient/2.0 (PID:{os.getpid()})",
                        "Accept": "application/json",
                        "Authorization": "Bearer " + cls._secret,
                    },
                    timeout=cls.config.get("timeout", 30),
                    limits=limits,  # Usage Limit
                )
            return cls._async_clients[loop_id]
        except RuntimeError:
            # If no runningEventLoop, create temporary
            raise Exception("Must be called in async context")

    @classmethod
    async def _async_request(cls, method: str, endpoint: str, **kwargs) -> Dict:
        """Send single async HTTP request and handle response"""
        # Ensure client created
        client = await cls._ensure_async_client()
        endpoint = endpoint.lstrip("/")
        response = None
        try:
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
        finally:
            # EnsureResponsewas closed (even ifExceptionalso execute)
            if response is not None:
                await response.aclose()

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
            f"/agent/chat-history/report",
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
# US-006 lesson runtime backend legs (LANE-ESP S6 pull-on-connect + S9 forward).
#
# DELIBERATE DIVERGENCE FROM plan §6.4/§6.5b (surfaced, not silently reconciled):
# the plan said reuse the manager-api ``ManageApiClient`` (base ``manager-api.url``
# + that service's Bearer). Live config (config.yaml:21-28, locked 2026-06-04)
# makes ``server.api_url`` — the NestJS backend, ``.../v1`` — the SOLE authority
# for the ``/v1/devices/*`` + ``/v1/lessons/*`` routes the lesson slice calls; the
# Java manager-api is "admin/legacy only" and does NOT serve them. So these legs
# target ``server.api_url`` via a caller-owned dedicated client. The plan's pins
# (method ``post_lesson_event`` lives here; retry/backoff reused; result->outcome
# renamed HERE as the single translation point) are preserved.
#
# D-RUNTOKEN (ADR 0013 §F) wants a device-scoped token; the ESP has no device-token
# minting path today, so ``token`` is injectable and OPTIONAL — cross-device authz
# is enforced backend-side on the ``device_id`` claim. Token wiring is an ops/backend
# follow-up; surfaced, not invented here.
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


def _normalize_lesson_event(event: Dict) -> Dict:
    """Single ``result -> outcome`` translation point (plan §5.7/§6.4.1) + COPPA
    strip of the authored ``detail.utterance`` (it never enters progress_events)."""
    out = dict(event)
    if "result" in out and "outcome" not in out:
        out["outcome"] = out.pop("result")
    else:
        out.pop("result", None)
    detail = out.get("detail")
    if isinstance(detail, dict) and "utterance" in detail:
        detail = {k: v for k, v in detail.items() if k != "utterance"}
        if detail:
            out["detail"] = detail
        else:
            out.pop("detail", None)
    return out


async def _lesson_request_with_retry(
    client, method: str, url: str, *, max_retries: int = 2, retry_delay: float = 1.0, **kwargs
):
    import asyncio

    attempt = 0
    while True:
        response = None
        try:
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
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


async def get_current_assignment(
    client, base_url: str, device_id: str, *, token: Optional[str] = None
) -> Optional[Dict]:
    """S6 pull-on-connect — GET /v1/devices/:deviceId/assignment/current.

    Returns the ``data.assignment`` object (or ``None`` when the device has no
    active assignment). The authoritative offline-catch-up hand-off (ADR 0013 §A/§B).
    """
    url = f"{_lesson_base(base_url)}/devices/{device_id}/assignment/current"
    payload = await _lesson_request_with_retry(
        client, "GET", url, headers=_lesson_auth_headers(token)
    )
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") or {}
    return data.get("assignment")


async def get_lesson_manifest(
    client,
    base_url: str,
    lesson_id: str,
    profile: str,
    *,
    token: Optional[str] = None,
    renderer_capabilities: Optional[list] = None,
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
    url = f"{_lesson_base(base_url)}/lessons/{lesson_id}/manifest"
    params: Dict[str, str] = {"profile": profile}
    headers = _lesson_auth_headers(token)
    if renderer_capabilities:
        joined = ",".join(renderer_capabilities)
        params["rendererCapabilities"] = joined
        headers["X-Renderer-Capabilities"] = joined
    response = await client.request(
        "GET", url, params=params, headers=headers
    )
    try:
        response.raise_for_status()
        body = response.json()
        etag = response.headers.get("ETag") or response.headers.get("etag")
    finally:
        await response.aclose()
    manifest = (body.get("data") or {}).get("manifest") if isinstance(body, dict) else None
    return manifest, etag


async def post_lesson_event(
    client, base_url: str, device_id: str, batch: Dict, *, token: Optional[str] = None
) -> Optional[Dict]:
    """S9 — POST /v1/devices/:deviceId/lesson-events (OWN dispatch path, NOT the
    chat-history report queue). Owns the single wire ``result -> outcome`` rename and
    the COPPA ``detail.utterance`` strip before the body leaves the ESP (plan §6.4)."""
    url = f"{_lesson_base(base_url)}/devices/{device_id}/lesson-events"
    body = dict(batch)
    body["events"] = [_normalize_lesson_event(e) for e in batch.get("events", [])]
    payload = await _lesson_request_with_retry(
        client, "POST", url, json=body, headers=_lesson_auth_headers(token)
    )
    if isinstance(payload, dict):
        return payload.get("data") or payload
    return None
