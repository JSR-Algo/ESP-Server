#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import sys
import time
import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config_loader import (  # noqa: E402
    DEFAULT_GOOGLE_LIVE_VOICE_NAME,
    GOOGLE_LIVE_DEFAULTS,
)
from core.voice.google_live.client import GoogleLiveClient  # noqa: E402
from scripts.google_live_reliability import (  # noqa: E402
    SCHEMA_VERSION,
    redact_mapping,
)

DEFAULT_AUDIO_FIXTURE = (
    PROJECT_ROOT / "tests/fixtures/tvideo_farm_audio/adult_speech_24k_mono.wav"
)
_TRANSIENT_ERROR_CLASSES = frozenset(
    {"network_or_transport", "google_service_unavailable"}
)
_SAFE_ERROR_MESSAGES = {
    "credential_or_auth": "Google Live authentication failed",
    "quota_or_rate_limit": "Google Live quota was exhausted",
    "model_or_config": "Google Live model or configuration was rejected",
    "network_or_transport": "Google Live transport failed",
    "google_service_unavailable": "Google Live service was unavailable",
    "acceptance_timeout": "Google Live round trip timed out",
    "audio_input_or_codec": "Audio fixture was empty or malformed",
    "protocol_or_event_order": "Google Live response evidence was incomplete",
    "unknown": "Google Live round trip failed",
}


class _RoundTripEvidenceError(RuntimeError):
    pass


class _ConsoleLogger:
    def bind(self, **kwargs):
        return self

    def info(self, message, *args, **kwargs):
        if args:
            message = message.format(*args)
        print(f"[info] {message}")

    def warning(self, message, *args, **kwargs):
        if args:
            message = message.format(*args)
        print(f"[warn] {message}")

    def error(self, message, *args, **kwargs):
        if args:
            message = message.format(*args)
        print(f"[error] {message}")


def _build_env_config(model, voice_name):
    return {
        "api_key": "${GOOGLE_API_KEY}",
        "model": model,
        "enable_audio_input": True,
        "enable_audio_output": True,
        "native_voice": bool(voice_name),
        "voice_name": voice_name,
        "language_code": GOOGLE_LIVE_DEFAULTS["language_code"],
        "connect_timeout_sec": 15,
        "recv_timeout_sec": 5,
    }

def _has_resolvable_api_key(config):
    api_key = str((config or {}).get("api_key") or "").strip()
    if not api_key:
        return False
    if api_key.startswith("${") and api_key.endswith("}"):
        env_name = api_key[2:-1]
        return bool("".join(str(os.environ.get(env_name, "")).split()))
    return bool("".join(api_key.split()))


def _read_pcm_chunks(path, *, chunk_ms=20):
    """Read a non-empty mono PCM16 WAV into fixed-duration chunks."""
    if (
        not isinstance(chunk_ms, (int, float))
        or isinstance(chunk_ms, bool)
        or chunk_ms <= 0
    ):
        raise ValueError("chunk_ms must be positive")
    with wave.open(str(path), "rb") as wav_file:
        if (
            wav_file.getnchannels() != 1
            or wav_file.getsampwidth() != 2
            or wav_file.getcomptype() != "NONE"
        ):
            raise ValueError("audio fixture must be mono 16-bit PCM WAV")
        sample_rate = wav_file.getframerate()
        frames_per_chunk = int(sample_rate * float(chunk_ms) / 1000)
        if sample_rate <= 0 or frames_per_chunk <= 0:
            raise ValueError("audio fixture sample rate is invalid")
        chunks = []
        while chunk := wav_file.readframes(frames_per_chunk):
            chunks.append(chunk)
    if not chunks or not any(chunks):
        raise ValueError("audio fixture is empty")
    return chunks


def _read_wav_sample_rate(path):
    with wave.open(str(path), "rb") as wav_file:
        return wav_file.getframerate()


def _build_round_trip_config(config, audio_file):
    """Preserve the resolved Live identity while matching the WAV input rate."""
    return {**config, "input_sample_rate": _read_wav_sample_rate(audio_file)}


async def _run_audio_round_trip(
    client, *, pcm_chunks, event_timeout_sec, clock=time.monotonic
):
    """Send one audio fixture and collect privacy-safe response evidence."""
    chunks = list(pcm_chunks)
    started = clock()
    first_event_ms = None
    first_audio_ms = None
    connection_ms = None
    audio_chunks = 0
    terminal = False
    try:
        if not chunks or not all(isinstance(chunk, bytes) and chunk for chunk in chunks):
            raise ValueError("PCM audio is empty or malformed")
        await client.connect()
        connection_ms = round((clock() - started) * 1000, 1)
        for chunk in chunks:
            await client.send_audio(chunk)
        await client.end_audio_stream()
        event_stream = client.receive_events()
        try:
            async with asyncio.timeout(event_timeout_sec):
                async for event in event_stream:
                    now_ms = round((clock() - started) * 1000, 1)
                    if first_event_ms is None:
                        first_event_ms = now_ms
                    event_type = event.get("type") if isinstance(event, dict) else None
                    if event_type == "receive_timeout":
                        raise TimeoutError("Google Live round trip timed out")
                    if event_type in {"audio", "audio_chunk"}:
                        audio_chunks += 1
                        if first_audio_ms is None:
                            first_audio_ms = now_ms
                    if event_type in {"audio_end", "turn_complete"}:
                        terminal = True
                        break
        finally:
            close_stream = getattr(event_stream, "aclose", None)
            if close_stream is not None:
                await close_stream()
        if not terminal or audio_chunks == 0:
            raise _RoundTripEvidenceError(
                "Google Live round trip ended without terminal audio"
            )
        return {
            "status": "PASS",
            "connectionMs": connection_ms,
            "firstServerEventMs": first_event_ms,
            "firstAudioMs": first_audio_ms,
            "audioChunks": audio_chunks,
        }
    finally:
        await client.close()


def _classify_error(error):
    """Classify errors without returning credential-bearing exception details."""
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return "acceptance_timeout"
    if isinstance(error, (ConnectionError, OSError)):
        return "network_or_transport"
    if isinstance(error, (ValueError, wave.Error)):
        return "audio_input_or_codec"
    if isinstance(error, _RoundTripEvidenceError):
        return "protocol_or_event_order"

    details = " ".join(
        str(value)
        for value in (
            type(error).__name__,
            getattr(error, "code", ""),
            getattr(error, "status", ""),
            error,
        )
    ).lower()
    if "timed out" in details or "timeout" in details:
        return "acceptance_timeout"
    if any(marker in details for marker in ("401", "403", "unauth", "permission_denied")):
        return "credential_or_auth"
    if any(marker in details for marker in ("429", "resource_exhausted", "quota")):
        return "quota_or_rate_limit"
    if any(
        marker in details
        for marker in ("500", "502", "503", "504", "unavailable", "service unavailable")
    ):
        return "google_service_unavailable"
    if any(
        marker in details
        for marker in (
            "400",
            "404",
            "invalid_argument",
            "failed_precondition",
            "model",
            "configuration",
            "config",
            "api key is missing",
        )
    ):
        return "model_or_config"
    return "unknown"


async def _run_round_trip_with_retry(
    client_factory, *, pcm_chunks, event_timeout_sec, clock=time.monotonic
):
    chunks = list(pcm_chunks)
    attempts = 0
    while attempts < 2:
        attempts += 1
        try:
            result = await _run_audio_round_trip(
                client_factory(),
                pcm_chunks=chunks,
                event_timeout_sec=event_timeout_sec,
                clock=clock,
            )
            return {**result, "attempts": attempts}
        except Exception as error:
            error_class = _classify_error(error)
            if error_class in _TRANSIENT_ERROR_CLASSES and attempts == 1:
                continue
            return {
                "status": "FAIL",
                "attempts": attempts,
                "error": {
                    "class": error_class,
                    "message": _SAFE_ERROR_MESSAGES[error_class],
                },
            }


def _build_report(result):
    return redact_mapping(
        {
            "schemaVersion": SCHEMA_VERSION,
            "name": "real_api",
            **result,
        }
    )


def _write_report(path, report):
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report

async def _load_manager_google_live_config(device_id, client_id):
    from config.config_loader import get_private_config_from_api, load_config_async
    from config.manage_api_client import ManageApiClient

    config = await load_config_async()
    ManageApiClient(config)
    try:
        private_config = await get_private_config_from_api(config, device_id, client_id)
    finally:
        ManageApiClient.safe_close()
        await asyncio.sleep(0)

    google_live_config = private_config.get("google_live") or {}
    if not google_live_config:
        raise RuntimeError("manager private config has no google_live section")
    voice_mode = private_config.get("voice_mode") or {}
    if not isinstance(voice_mode, dict) or voice_mode.get("type") != "google_live":
        raise RuntimeError("manager private config voice_mode is not google_live")
    return dict(google_live_config)

async def _run_smoke(config):
    client = GoogleLiveClient(config, _ConsoleLogger())
    await client.connect()
    print("SMOKE_CONNECT_OK")
    await client.close()
    print("SMOKE_CLOSE_OK")


async def _run_round_trip(config, audio_file, event_timeout_sec):
    try:
        chunks = _read_pcm_chunks(audio_file, chunk_ms=20)
        config = _build_round_trip_config(config, audio_file)
        return await _run_round_trip_with_retry(
            lambda: GoogleLiveClient(config, _ConsoleLogger()),
            pcm_chunks=chunks,
            event_timeout_sec=event_timeout_sec,
        )
    except (ValueError, wave.Error, FileNotFoundError):
        return {
            "status": "FAIL",
            "attempts": 0,
            "error": {
                "class": "audio_input_or_codec",
                "message": _SAFE_ERROR_MESSAGES["audio_input_or_codec"],
            },
        }


def main():
    parser = argparse.ArgumentParser(
        description="Connect to Google Live API and immediately close."
    )
    parser.add_argument(
        "--model",
        default=os.environ.get(
            "GOOGLE_LIVE_MODEL",
            GOOGLE_LIVE_DEFAULTS["model"],
        ),
    )
    parser.add_argument(
        "--voice-name",
        default=os.environ.get(
            "GOOGLE_LIVE_VOICE_NAME",
            DEFAULT_GOOGLE_LIVE_VOICE_NAME,
        ),
    )
    parser.add_argument(
        "--manager-device-id",
        default=os.environ.get("GOOGLE_LIVE_MANAGER_DEVICE_ID", ""),
        help="Load google_live config from manager API private config for this device.",
    )
    parser.add_argument(
        "--manager-client-id",
        default=os.environ.get("GOOGLE_LIVE_MANAGER_CLIENT_ID", ""),
        help="Client/agent id to use with --manager-device-id.",
    )
    parser.add_argument(
        "--round-trip",
        action="store_true",
        help="Send the approved WAV fixture and require terminal output audio.",
    )
    parser.add_argument(
        "--audio-file",
        type=Path,
        default=DEFAULT_AUDIO_FIXTURE,
        help="Mono 16-bit PCM WAV fixture for --round-trip.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Write the redacted reliability layer report as JSON.",
    )
    parser.add_argument(
        "--event-timeout-sec",
        type=float,
        default=20.0,
        help="Maximum time to wait for terminal Google Live output.",
    )
    args = parser.parse_args()

    if args.manager_device_id:
        if not args.manager_client_id:
            print("--manager-client-id is required with --manager-device-id", file=sys.stderr)
            return 1
        config = asyncio.run(
            _load_manager_google_live_config(args.manager_device_id, args.manager_client_id)
        )
    else:
        config = _build_env_config(args.model, args.voice_name)

    if not _has_resolvable_api_key(config):
        print("GOOGLE_API_KEY is required", file=sys.stderr)
        return 1

    if args.round_trip:
        result = asyncio.run(
            _run_round_trip(config, args.audio_file, args.event_timeout_sec)
        )
        report = _build_report(result)
        if args.report:
            _write_report(args.report, report)
        print(json.dumps(report, sort_keys=True))
        return 0 if result["status"] == "PASS" else 1

    asyncio.run(_run_smoke(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
