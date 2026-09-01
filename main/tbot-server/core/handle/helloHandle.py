import asyncio
import hashlib
import json
import random
import re
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

from core.handle.sendAudioHandle import send_tts_message, sendAudioMessage
from core.providers.tools.device_mcp import MCPClient, send_mcp_initialize_message
from core.providers.tts.dto.dto import SentenceType
from core.utils.dialogue import Message
from core.utils.util import audio_to_data, opus_datas_to_wav_bytes, remove_punctuation_and_length
from core.utils.wakeup_word import WakeupWordsConfig
from core.voice.google_live.evidence_enrollment import (
    EnrollmentError,
    validate_evidence_claims,
)

TAG = __name__
SAFE_EVIDENCE_JOURNEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _evidence_peer_identity_hash(conn):
    device_id = str(getattr(conn, "device_id", "") or "")
    client_id = str(getattr(conn, "client_id", "") or "")
    digest = hashlib.sha256(f"{device_id}\0{client_id}".encode()).hexdigest()
    return f"sha256:{digest}"


def _single_client_id(conn):
    headers = getattr(conn, "headers", None)
    if headers is None:
        return str(getattr(conn, "client_id", "") or "")
    getall = getattr(headers, "getall", None)
    if callable(getall):
        values = getall("client-id", [])
        return values[0] if len(values) == 1 else ""
    value = headers.get("client-id", headers.get("Client-Id", ""))
    return value if isinstance(value, str) else ""


WAKEUP_CONFIG = {
    "refresh_time": 10,
    "responses": [
        "I am always here, please speak.",
        "Here, ready for your command anytime.",
        "Here I am, please tell me.",
        "Please speak, I'm listening.",
        "Please speak, I am ready.",
        "Please say command.",
        "I am listening carefully, please speak.",
        "How can I help you?",
        "I am here, waiting for your command.",
    ],
}

# Create global wake word config manager
wakeup_words_config = WakeupWordsConfig()

def _new_asyncio_lock():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    return asyncio.Lock()

# Used to prevent concurrent callswakeupWordsResponseLock of
_wakeup_response_lock = _new_asyncio_lock()

def _to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _is_google_live_connection(conn: "ConnectionHandler"):
    config = getattr(conn, "config", None)
    if not isinstance(config, dict):
        return False
    voice_mode = config.get("voice_mode")
    return isinstance(voice_mode, dict) and voice_mode.get("type") == "google_live"

def _google_live_output_sample_rate(conn: "ConnectionHandler"):
    config = getattr(conn, "config", None)
    if isinstance(config, dict):
        google_live = config.get("google_live")
        if isinstance(google_live, dict):
            sample_rate = _to_int(google_live.get("output_sample_rate"))
            if sample_rate:
                return sample_rate
    welcome_audio = getattr(conn, "welcome_msg", {}).get("audio_params", {})
    return _to_int(welcome_audio.get("sample_rate"), getattr(conn, "sample_rate", 24000))


def _abort_google_live_claim(conn, registry, journey_id, failure_code):
    if registry is not None and journey_id is not None:
        try:
            registry.abort_claim(
                device_id=str(getattr(conn, "device_id", "") or ""),
                client_id=_single_client_id(conn),
                journey_id=journey_id,
                failure_code=failure_code,
            )
        except Exception:
            pass
    conn.google_live_evidence_journey_id = None
    conn.google_live_evidence_scope = None
    conn.google_live_evidence_candidate_identity = None
    conn.google_live_evidence_journey_type = None
    conn.google_live_evidence_proof_profile = None
    conn.google_live_evidence_semantic_kind = None
    conn.google_live_evidence_quiet_mode = None

async def _handleHelloMessage(conn: "ConnectionHandler", msg_json):
    """Handle hello message"""
    send_mcp_initialize = False
    connection_transition_to_log = None
    previous_evidence_scope = getattr(conn, "google_live_evidence_scope", None)
    previous_finalize_result = getattr(
        conn, "google_live_evidence_finalize_result", None
    )
    previous_candidate_identity = getattr(
        conn, "google_live_evidence_candidate_identity", None
    )
    previous_journey_type = getattr(
        conn, "google_live_evidence_journey_type", None
    )
    previous_proof_profile = getattr(
        conn, "google_live_evidence_proof_profile", None
    )
    previous_semantic_kind = getattr(
        conn, "google_live_evidence_semantic_kind", None
    )
    previous_quiet_mode = getattr(conn, "google_live_evidence_quiet_mode", None)
    previous_reliability_start_logged = getattr(
        conn, "google_live_reliability_start_logged", False
    )

    def restore_previous_evidence_scope():
        conn.google_live_evidence_journey_id = previous_evidence_scope.get(
            "journeyId"
        )
        conn.google_live_evidence_scope = previous_evidence_scope
        conn.google_live_evidence_candidate_identity = previous_candidate_identity
        conn.google_live_evidence_journey_type = previous_journey_type
        conn.google_live_evidence_proof_profile = previous_proof_profile
        conn.google_live_evidence_semantic_kind = previous_semantic_kind
        conn.google_live_evidence_quiet_mode = previous_quiet_mode
        conn.google_live_reliability_start_logged = (
            previous_reliability_start_logged
        )

    rotate_completed_scope = False
    preserved_previous_scope_after_rejection = False
    evidence_journey_id = msg_json.get("evidence_journey_id")
    conn.google_live_evidence_journey_id = (
        evidence_journey_id
        if _is_google_live_connection(conn)
        and isinstance(evidence_journey_id, str)
        and SAFE_EVIDENCE_JOURNEY_RE.fullmatch(evidence_journey_id)
        else None
    )
    conn.google_live_evidence_scope = None
    conn.google_live_evidence_candidate_identity = None
    conn.google_live_evidence_journey_type = None
    conn.google_live_evidence_proof_profile = None
    conn.google_live_evidence_semantic_kind = None
    conn.google_live_evidence_quiet_mode = None
    conn.google_live_reliability_start_logged = False
    enrollment_invalid = False
    registry = getattr(conn, "evidence_registry", None)
    audio_params = msg_json.get("audio_params")
    if audio_params:
        format = audio_params.get("format")
        conn.logger.bind(tag=TAG).debug(f"Client audio format: {format}")
        conn.audio_format = format
        sample_rate = audio_params.get("sample_rate")
        server_audio_params = dict(audio_params)
        if sample_rate:
            client_sample_rate = int(sample_rate)
            conn.input_sample_rate = client_sample_rate
            if _is_google_live_connection(conn):
                conn.sample_rate = _google_live_output_sample_rate(conn)
                server_audio_params["sample_rate"] = conn.sample_rate
                conn.logger.bind(tag=TAG).info(
                    "Set Google Live audio sample rates from client hello: "
                    f"input={client_sample_rate} output={conn.sample_rate}"
                )
            else:
                conn.sample_rate = client_sample_rate
                conn.logger.bind(tag=TAG).info(
                    f"Set output audio sample rate from client hello to: {conn.sample_rate}"
                )
        conn.welcome_msg["audio_params"] = server_audio_params
    features = msg_json.get("features")
    if features:
        conn.logger.bind(tag=TAG).debug(f"Client features: {features}")
        conn.features = features
        if features.get("mcp"):
            conn.logger.bind(tag=TAG).debug("Client supports MCP")
            conn.mcp_client = MCPClient()
            send_mcp_initialize = True

    if conn.google_live_evidence_journey_id is not None and registry is not None:
        claim_for_scope = getattr(registry, "claim_for_scope", None)
        claimed = (
            claim_for_scope(
                device_id=str(getattr(conn, "device_id", "") or ""),
                client_id=_single_client_id(conn),
                journey_id=conn.google_live_evidence_journey_id,
            )
            if callable(claim_for_scope)
            else None
        )
        if claimed is None:
            conn.google_live_evidence_journey_id = None
            enrollment_invalid = True
        else:
            safe_snapshot = getattr(registry, "safe_snapshot", None)
            try:
                enrollment_snapshot = (
                    safe_snapshot(conn.google_live_evidence_journey_id)
                    if callable(safe_snapshot)
                    else None
                )
            except Exception:
                enrollment_snapshot = None
            journey_type = (
                enrollment_snapshot.get("journeyType")
                if isinstance(enrollment_snapshot, dict)
                else None
            )
            proof_profile = (
                enrollment_snapshot.get("proofProfile")
                if isinstance(enrollment_snapshot, dict)
                else None
            )
            claims_valid = False
            try:
                validate_evidence_claims(journey_type, proof_profile)
                claims_valid = True
            except EnrollmentError:
                pass
            if not claims_valid:
                _abort_google_live_claim(
                    conn,
                    registry,
                    conn.google_live_evidence_journey_id,
                    "EVIDENCE_ENROLLMENT_INVALID",
                )
                enrollment_invalid = True
            else:
                conn.google_live_evidence_candidate_identity = claimed
                conn.google_live_evidence_journey_type = journey_type
                conn.google_live_evidence_proof_profile = proof_profile
                conn.google_live_evidence_semantic_kind = (
                    enrollment_snapshot.get("semanticProofKind")
                    if isinstance(enrollment_snapshot, dict)
                    else None
                )
                conn.google_live_evidence_quiet_mode = (
                    enrollment_snapshot.get("quietMode")
                    if isinstance(enrollment_snapshot, dict)
                    else None
                )
                previous_journey_id = (
                    previous_evidence_scope.get("journeyId")
                    if isinstance(previous_evidence_scope, dict)
                    else None
                )
                if (
                    isinstance(previous_journey_id, str)
                    and previous_journey_id != conn.google_live_evidence_journey_id
                ):
                    terminal_matches = getattr(
                        registry, "terminal_claim_matches", None
                    )
                    previous_complete = (
                        isinstance(previous_finalize_result, dict)
                        and previous_finalize_result.get("type")
                        == "evidence_finalized"
                        and previous_finalize_result.get("status") == "PASS"
                        and previous_finalize_result.get("evidenceScope")
                        == previous_evidence_scope
                        and callable(terminal_matches)
                        and terminal_matches(
                            device_id=str(getattr(conn, "device_id", "") or ""),
                            client_id=_single_client_id(conn),
                            journey_id=previous_journey_id,
                        )
                        is True
                    )
                    if previous_complete:
                        rotate_completed_scope = True
                    else:
                        _abort_google_live_claim(
                            conn,
                            registry,
                            conn.google_live_evidence_journey_id,
                            "PREVIOUS_EVIDENCE_SCOPE_INCOMPLETE",
                        )
                        enrollment_invalid = True

    hello_ack = dict(conn.welcome_msg)
    if enrollment_invalid:
        hello_ack["evidenceScope"] = {
            "status": "FAIL",
            "failureCode": "EVIDENCE_ENROLLMENT_INVALID",
        }
        previous_journey_id = (
            previous_evidence_scope.get("journeyId")
            if isinstance(previous_evidence_scope, dict)
            else None
        )
        if isinstance(previous_journey_id, str):
            restore_previous_evidence_scope()
            preserved_previous_scope_after_rejection = True
    elif (
        conn.google_live_evidence_journey_id is None
        and isinstance(previous_evidence_scope, dict)
    ):
        restore_previous_evidence_scope()
        preserved_previous_scope_after_rejection = True
    if (
        conn.google_live_evidence_journey_id is not None
        and not preserved_previous_scope_after_rejection
    ):
        server_start_utc = _utc_now_iso()
        provider = getattr(conn, "voice_provider", None)
        if rotate_completed_scope:
            prepare_next_scope = getattr(
                provider, "prepare_next_evidence_scope", None
            )
            try:
                rotated = (
                    await prepare_next_scope(previous_evidence_scope)
                    if callable(prepare_next_scope)
                    else False
                )
            except Exception:
                rotated = False
            if rotated is not True:
                failed_journey_id = conn.google_live_evidence_journey_id
                _abort_google_live_claim(
                    conn,
                    registry,
                    failed_journey_id,
                    "PREVIOUS_EVIDENCE_SCOPE_ROTATION_FAILED",
                )
                restore_previous_evidence_scope()
                hello_ack["evidenceScope"] = {
                    "status": "FAIL",
                    "failureCode": "PREVIOUS_EVIDENCE_SCOPE_ROTATION_FAILED",
                }
                try:
                    await conn.websocket.send(json.dumps(hello_ack))
                except BaseException:
                    raise
                return
            conn.google_live_evidence_finalize_result = None
            conn.google_live_evidence_finalize_task = None
        prepare_scope = getattr(provider, "prepare_evidence_scope", None)
        try:
            live_connection_id = (
                await prepare_scope() if callable(prepare_scope) else None
            )
        except BaseException:
            _abort_google_live_claim(
                conn,
                registry,
                conn.google_live_evidence_journey_id,
                "LIVE_SCOPE_PREPARE_FAILED",
            )
            raise
        if isinstance(live_connection_id, str) and live_connection_id:
            try:
                scope = {
                    "journeyId": conn.google_live_evidence_journey_id,
                    "connectionId": str(conn.session_id),
                    "liveConnectionId": live_connection_id,
                    "initialLiveConnectionId": live_connection_id,
                    "peerIdentityHash": _evidence_peer_identity_hash(conn),
                    "serverStartUtc": server_start_utc,
                    "journeyType": conn.google_live_evidence_journey_type,
                    "proofProfile": conn.google_live_evidence_proof_profile,
                    "semanticProofKind": (
                        conn.google_live_evidence_semantic_kind or "none"
                    ),
                    "quietMode": conn.google_live_evidence_quiet_mode or "none",
                }
            except BaseException:
                _abort_google_live_claim(
                    conn,
                    registry,
                    conn.google_live_evidence_journey_id,
                    "LIVE_SCOPE_PREPARE_FAILED",
                )
                raise
            conn.google_live_evidence_scope = scope
            hello_ack["evidenceScope"] = scope
            previous = getattr(conn, "google_live_previous_server_connection", None)
            previous_scope = (
                previous.get("evidenceScope") if isinstance(previous, dict) else None
            )
            previous_journey_id = (
                previous_scope.get("journeyId")
                if isinstance(previous_scope, dict)
                else None
            )
            if (
                isinstance(previous_scope, dict)
                and isinstance(previous_journey_id, str)
                and SAFE_EVIDENCE_JOURNEY_RE.fullmatch(previous_journey_id)
                and previous.get("peerIdentityHash") == scope["peerIdentityHash"]
                and previous_scope.get("peerIdentityHash")
                == scope["peerIdentityHash"]
                and previous.get("connectionId") == previous_scope.get("connectionId")
                and previous["connectionId"] != scope["connectionId"]
            ):
                transition = {
                    "schemaVersion": "google-live-reliability.v1",
                    "status": "PASS",
                    "source": "server_log",
                    "serverIssued": True,
                    "executionSequence": 1,
                    "reason": "same_device_reconnect",
                    "peerIdentityHash": scope["peerIdentityHash"],
                    "fromJourneyId": previous_journey_id,
                    "fromConnectionId": previous["connectionId"],
                    "toJourneyId": scope["journeyId"],
                    "toConnectionId": scope["connectionId"],
                }
                conn.google_live_server_connection_transition = transition
                conn.google_live_previous_server_connection = None
                hello_ack["connectionTransition"] = transition
                connection_transition_to_log = transition
        else:
            _abort_google_live_claim(
                conn,
                registry,
                conn.google_live_evidence_journey_id,
                "LIVE_SCOPE_UNAVAILABLE",
            )
            hello_ack["evidenceScope"] = {
                "status": "FAIL",
                "failureCode": "LIVE_SCOPE_UNAVAILABLE",
            }

    try:
        await conn.websocket.send(json.dumps(hello_ack))
    except BaseException:
        scope = getattr(conn, "google_live_evidence_scope", None)
        _abort_google_live_claim(
            conn,
            registry,
            scope.get("journeyId") if isinstance(scope, dict) else None,
            "HELLO_ACK_FAILED",
        )
        raise
    scope = getattr(conn, "google_live_evidence_scope", None)
    candidate_identity = getattr(
        conn, "google_live_evidence_candidate_identity", None
    )
    if isinstance(scope, dict) and not preserved_previous_scope_after_rejection:
        try:
            conn.logger.bind(tag=TAG).info(
                "Google Live reliability_window_start window_id={} journey_id={} "
                "journeys={} proof_profile={} semantic_proof_kind={} quiet_mode={} "
                "connection_id={} live_connection_id={} initial_live_connection_id={} "
                "peer_identity_hash={} server_start_utc={} server_issued=true "
                "candidate_identity={}",
                scope["journeyId"],
                scope["journeyId"],
                scope["journeyType"],
                scope["proofProfile"],
                scope["semanticProofKind"],
                scope["quietMode"],
                scope["connectionId"],
                scope["liveConnectionId"],
                scope["initialLiveConnectionId"],
                scope["peerIdentityHash"],
                scope["serverStartUtc"],
                json.dumps(
                    candidate_identity or {}, sort_keys=True, separators=(",", ":")
                ),
            )
            conn.google_live_reliability_start_logged = True
        except BaseException:
            _abort_google_live_claim(
                conn, registry, scope["journeyId"], "RELIABILITY_START_FAILED"
            )
            raise
        if connection_transition_to_log is not None:
            transition = connection_transition_to_log
            conn.logger.bind(tag=TAG).info(
                "Google Live evidence_server_connection_transition "
                f"from_journey_id={transition['fromJourneyId']} "
                f"from_connection_id={transition['fromConnectionId']} "
                f"to_journey_id={transition['toJourneyId']} "
                f"to_connection_id={transition['toConnectionId']} "
                f"peer_identity_hash={transition['peerIdentityHash']} "
                "sequence=1 reason=same_device_reconnect"
            )
    if send_mcp_initialize:
        conn.schedule_mcp_background_task(send_mcp_initialize_message(conn))


async def handleHelloMessage(conn: "ConnectionHandler", msg_json):
    journey_id = msg_json.get("evidence_journey_id")
    registry = getattr(conn, "evidence_registry", None)
    try:
        return await _handleHelloMessage(conn, msg_json)
    except BaseException:
        if (
            _is_google_live_connection(conn)
            and isinstance(journey_id, str)
            and SAFE_EVIDENCE_JOURNEY_RE.fullmatch(journey_id)
            and not getattr(conn, "google_live_reliability_start_logged", False)
        ):
            _abort_google_live_claim(
                conn, registry, journey_id, "HELLO_PRE_START_FAILED"
            )
        raise


async def checkWakeupWords(conn: "ConnectionHandler", text):
    if _is_google_live_connection(conn):
        return False

    enable_wakeup_words_response_cache = conn.config[
        "enable_wakeup_words_response_cache"
    ]

    # WaitttsInitialize, wait max3seconds
    start_time = time.time()
    while time.time() - start_time < 3:
        if conn.tts:
            break
        await asyncio.sleep(0.1)
    else:
        return False

    if not enable_wakeup_words_response_cache:
        return False

    _, filtered_text = remove_punctuation_and_length(text)
    if filtered_text not in conn.config.get("wakeup_words"):
        return False

    conn.just_woken_up = True
    tts_stopped = False

    try:
        # Get current voice
        voice = getattr(conn.tts, "voice", "default")
        if not voice:
            voice = "default"

        # Get wake-word reply config
        response = wakeup_words_config.get_wakeup_response(voice)
        if not response or not response.get("file_path"):
            response = {
                "voice": "default",
                "file_path": "config/assets/wakeup_words_short.wav",
                "time": 0,
                "text": "I'm here!",
            }

        # Get audio data
        opus_packets = await audio_to_data(response.get("file_path"), use_cache=False)
        # Play wake word reply
        conn.client_abort = False

        # Treat wake word reply as new session, generate new sentence_id, ensure flow controller reset
        conn.sentence_id = str(uuid.uuid4().hex)

        conn.logger.bind(tag=TAG).info(f"Play wake word reply: {response.get('text')}")
        await sendAudioMessage(conn, SentenceType.FIRST, opus_packets, response.get("text"))
        await sendAudioMessage(conn, SentenceType.LAST, [], None)
        tts_stopped = True

        # Supplement Dialogue
        conn.dialogue.put(Message(role="assistant", content=response.get("text")))

        # Check whether wake word reply needs update
        if time.time() - response.get("time", 0) > WAKEUP_CONFIG["refresh_time"]:
            if not _wakeup_response_lock.locked():
                asyncio.create_task(wakeupWordsResponse(conn))
    except Exception as e:
        conn.logger.bind(tag=TAG).warning(f"Wake word reply failed: {e}")
    finally:
        if getattr(conn, "client_is_speaking", False) and not tts_stopped:
            await send_tts_message(conn, "stop", None)
    return True


async def wakeupWordsResponse(conn: "ConnectionHandler"):
    if not conn.tts:
        return

    try:
        # Try get lock, return if cannot get
        if not await _wakeup_response_lock.acquire():
            return

        # Randomly choose reply from predefined reply list
        result = random.choice(WAKEUP_CONFIG["responses"])
        if not result or len(result) == 0:
            return

        # GenerateTTSAudio
        tts_result = await asyncio.to_thread(conn.tts.to_tts, result)
        if not tts_result:
            return

        # Get current voice
        voice = getattr(conn.tts, "voice", "default")

        # Use linkedsample_rate
        wav_bytes = opus_datas_to_wav_bytes(tts_result, sample_rate=conn.sample_rate)
        file_path = wakeup_words_config.generate_file_path(voice)
        with open(file_path, "wb") as f:
            f.write(wav_bytes)
        # Update config
        wakeup_words_config.update_wakeup_response(voice, file_path, result)
    finally:
        # Ensure lock released in any case
        if _wakeup_response_lock.locked():
            _wakeup_response_lock.release()
