#!/usr/bin/env python3
"""Trigger the local sample lesson demo nudge after fail-closed checks."""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def _is_loopback_base_url(base_url: str) -> bool:
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or not parsed.hostname:
        return False
    if parsed.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _default_open_url(url: str, *, method: str = "GET", headers=None, timeout: int = 5) -> bytes:
    request = Request(url, method=method, headers=headers or {})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _norm(value: str) -> str:
    return str(value or "").strip().lower()


def _device_ids(metrics: dict) -> set[str]:
    device_ids = set()
    for device in metrics.get("devices", []):
        if isinstance(device, dict) and device.get("deviceId"):
            device_ids.add(_norm(device["deviceId"]))
    return device_ids


def nudge(*, device_id: str, base_url: str, open_url=_default_open_url) -> dict:
    base_url = base_url.rstrip("/")
    target_id = _norm(device_id)
    if not _is_loopback_base_url(base_url):
        print("Refusing non-loopback local nudge base URL.", file=sys.stderr)
        raise SystemExit(2)

    metrics_url = f"{base_url}/internal/lesson-runtime/metrics"
    try:
        metrics = json.loads(open_url(metrics_url).decode())
    except URLError as exc:
        print(f"Refusing nudge: local metrics are unreachable: {exc}", file=sys.stderr)
        raise SystemExit(4) from exc
    if target_id not in _device_ids(metrics):
        print(f"Refusing nudge: {device_id} is not connected to the local server.", file=sys.stderr)
        raise SystemExit(3)

    nudge_url = f"{base_url}/internal/devices/{target_id}/lesson-nudge"
    payload = open_url(
        nudge_url,
        method="POST",
        headers={"X-TBOT-Local-Sample-Demo": "1"},
    )
    return json.loads(payload.decode())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed local nudge for the built-in sample lesson demo."
    )
    parser.add_argument("device_id")
    parser.add_argument("--base-url", default="http://127.0.0.1:8003")
    args = parser.parse_args(argv)

    result = nudge(device_id=args.device_id, base_url=args.base_url)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
