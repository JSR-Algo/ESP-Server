#!/usr/bin/env python3
"""Read-only preflight for the local sample lesson demo path."""

from __future__ import annotations

import argparse
import json
from urllib.error import URLError
from urllib.request import Request, urlopen


def _norm(value: str) -> str:
    return str(value or "").strip().lower()


def _metrics_device_ids(metrics: dict | None) -> set[str]:
    if not isinstance(metrics, dict):
        return set()
    return {
        _norm(device.get("deviceId"))
        for device in metrics.get("devices", [])
        if isinstance(device, dict) and device.get("deviceId")
    }


def _usb_target_present(device_id: str, usb_ports: list[str]) -> bool:
    target = _norm(device_id)
    return any(target in _norm(port) or "303a:1001" in _norm(port) for port in usb_ports)


def classify(
    *,
    device_id: str,
    usb_ports: list[str],
    local_metrics: dict | None,
    public_metrics: dict | None,
    local_error: str | None,
    public_error: str | None,
) -> dict:
    target = _norm(device_id)
    usb_present = _usb_target_present(target, usb_ports)
    local_present = target in _metrics_device_ids(local_metrics)
    public_present = target in _metrics_device_ids(public_metrics)

    if local_present:
        status = "LOCAL_NUDGE_READY"
        next_action = "run_local_sample_demo_nudge"
        can_nudge = True
    elif not usb_present:
        status = "TARGET_USB_ABSENT"
        next_action = "reconnect_robot_usb"
        can_nudge = False
    elif public_present:
        status = "TARGET_PUBLIC_ONLY"
        next_action = "approve_endpoint_redirect_or_production_nudge"
        can_nudge = False
    elif local_error:
        status = "LOCAL_SERVER_UNREACHABLE"
        next_action = "start_local_sample_demo_server"
        can_nudge = False
    else:
        status = "TARGET_NOT_CONNECTED"
        next_action = "wait_for_robot_or_endpoint_redirect"
        can_nudge = False

    return {
        "status": status,
        "canNudgeLocal": can_nudge,
        "nextAction": next_action,
        "deviceId": target,
        "usbTargetPresent": usb_present,
        "localTargetPresent": local_present,
        "publicTargetPresent": public_present,
        "localMetricsError": local_error,
        "publicMetricsError": public_error,
    }


def get_json(url: str, *, open_url=urlopen, timeout: int = 5) -> tuple[dict | None, str | None]:
    try:
        with_response = open_url(url, timeout=timeout)
        if hasattr(with_response, "__enter__"):
            with with_response as response:
                body = response.read()
        else:
            body = with_response
        return json.loads(body.decode()), None
    except (OSError, URLError, json.JSONDecodeError) as exc:
        return None, str(exc)

def device_affinity_request(url: str, *, device_id: str) -> Request:
    return Request(
        url,
        headers={
            "device-id": _norm(device_id),
            "x-device-id": _norm(device_id),
        },
    )


def list_usb_ports() -> list[str]:
    try:
        from serial.tools import list_ports
    except Exception as exc:
        return [f"serial.tools.list_ports unavailable: {exc}"]
    return [
        " ".join(
            str(part)
            for part in (port.device, port.description, port.hwid)
            if part
        )
        for port in list_ports.comports()
    ]


def main(argv: list[str] | None = None, *, open_url=urlopen) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only preflight for the local sample lesson demo."
    )
    parser.add_argument("--device-id", default="28:84:85:85:1a:80")
    parser.add_argument("--local-base-url", default="http://127.0.0.1:8003")
    parser.add_argument(
        "--public-metrics-url",
        default="https://esp.tjbot.vn/internal/lesson-runtime/metrics",
    )
    args = parser.parse_args(argv)

    local_url = args.local_base_url.rstrip("/") + "/internal/lesson-runtime/metrics"
    local_metrics, local_error = get_json(local_url, open_url=open_url)
    public_metrics, public_error = get_json(
        device_affinity_request(args.public_metrics_url, device_id=args.device_id),
        open_url=open_url,
    )
    result = classify(
        device_id=args.device_id,
        usb_ports=list_usb_ports(),
        local_metrics=local_metrics,
        public_metrics=public_metrics,
        local_error=local_error,
        public_error=public_error,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["canNudgeLocal"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
