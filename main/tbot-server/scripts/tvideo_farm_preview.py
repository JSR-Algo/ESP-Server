#!/usr/bin/env python3
"""Launch the committed, preview-only TVideo farm admin fixture."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
MANAGER_ROOT = SERVER_ROOT.parent / "manager-web"
PREVIEW_ENTRY = MANAGER_ROOT / "tests" / "browser" / "tvideo-farm-preview-main.js"
PREVIEW_BOOTSTRAP = MANAGER_ROOT / "public" / "tvideo-farm-preview.html"
FIXTURE_LESSON_ID = "journey-v4"
REQUIRED_MEDIA_SHA256 = {
    "tvideo-demo/assets/scenes/deep-barn-farm-background-6s.mp4": "53d3ac70d166ba83029d5d122493dc48304d2caf933e03c09b0907152531f5f1",
    "tvideo-demo/assets/objects/barn.png": "eac30a7ddf3f14df79f27c3eb39f2114f3a780d5670bb11ef62446f5fa5dcbb9",
    "tvideo-demo/assets/objects/hay.png": "f74c34f44459495062091d4d91bb8fef0a2501ff3fab78beddaf0f70f0bf2e11",
    "tvideo-demo/assets/robot-alive/flight/flight-in.webm": "52091cdbfe5712e4afed800ce35e4a743e923c65b4034dd4c0a3c85d0f6c345c",
    "tvideo-demo/assets/robot-alive/flight/walk-toward.webm": "46a3058664949eaebf057f99309ee317bd36257178c187e83329fdbaf030cf5a",
    "tvideo-demo/assets/robot-alive/flight/greet-loop.webm": "249a86cea2456b41ee5344445b0b83777a0ee7217ce435e7e9294ed7f39b12e0",
    "tvideo-demo/assets/robot-alive/flight/celebrate.webm": "708d982dc9f2257a170ad1811c1afc230b5249a4139f975133a703ebdf39e105",
}


def _resolve_npm() -> Path:
    candidates = [
        shutil.which("npm"),
        "/Applications/ChatGPT.app/Contents/Resources/cua_node/bin/npm",
    ]
    for candidate in candidates:
        if candidate:
            path = Path(candidate)
            if path.is_file() and os.access(path, os.X_OK):
                return path
    raise FileNotFoundError("npm runtime not found; install Node.js/npm before starting preview")


def _serve_environment(npm: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join((str(npm.parent), env.get("PATH", "")))
    return env


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_required_media() -> list[dict[str, str]]:
    manifest = []
    for relative_path, expected_sha256 in REQUIRED_MEDIA_SHA256.items():
        path = MANAGER_ROOT / "public" / relative_path
        if not path.is_file():
            raise FileNotFoundError(f"missing preview fixture media: {path}")
        actual_sha256 = _sha256(path)
        if actual_sha256 != expected_sha256:
            raise ValueError(
                "preview fixture media digest mismatch: "
                f"{path} expected {expected_sha256} got {actual_sha256}"
            )
        manifest.append({"url": f"/{relative_path}", "sha256": expected_sha256})
    return manifest


def _check_payload(host: str, port: int) -> dict[str, object]:
    missing = [
        str(path)
        for path in (MANAGER_ROOT / "package.json", PREVIEW_ENTRY, PREVIEW_BOOTSTRAP)
        if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError("missing preview fixture files: " + ", ".join(missing))
    media_manifest = _validate_required_media()
    return {
        "status": "PREVIEW_ONLY_READY",
        "url": f"http://{host}:{port}/tvideo-farm-preview.html",
        "fixtureLessonId": FIXTURE_LESSON_ID,
        "publishReady": False,
        "rolloutChanged": False,
        "source": "committed-local-fixture",
        "fixtureAssets": media_manifest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8090, type=int)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    payload = _check_payload(args.host, args.port)
    if args.check:
        print(json.dumps(payload, sort_keys=True))
        return 0

    print(json.dumps(payload, sort_keys=True), flush=True)
    npm = _resolve_npm()
    command = [
        str(npm),
        "run",
        "serve",
        "--",
        "--host",
        args.host,
        "--port",
        str(args.port),
        str(PREVIEW_ENTRY.relative_to(MANAGER_ROOT)),
    ]
    os.chdir(MANAGER_ROOT)
    os.execvpe(command[0], command, _serve_environment(npm))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
