#!/usr/bin/env python3
"""
Quick-demo TTS server for TeeBot Child Companion.
Serves EdgeTTS-generated MP3 over HTTP with CORS.

Run:
  source .venv-quick/bin/activate
  python demo_server.py

Endpoints:
  GET  /demo/tts?text=Hello&voice=vi-VN-HoaiMyNeural  → MP3 audio
  POST /demo/tts  JSON:{"text":"Hello","voice":"vi-VN-HoaiMyNeural"} → MP3 audio
  GET  /demo/health  → { "ok": true }
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

import edge_tts
from aiohttp import web

DEFAULT_VOICE = "vi-VN-HoaiMyNeural"  # Female Vietnamese — TeeBot persona
HOST = "0.0.0.0"
PORT = 9000
AUDIO_DIR = Path(__file__).parent / "tmp" / "demo_audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


async def tts_handler(request: web.Request) -> web.Response:
    """Generate TTS audio from text."""
    if request.method == "POST":
        try:
            body = await request.json()
            text = body.get("text", "").strip()
            voice = body.get("voice", DEFAULT_VOICE)
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)
    else:
        text = request.query.get("text", "").strip()
        voice = request.query.get("voice", DEFAULT_VOICE)

    if not text:
        return web.json_response({"error": "Missing 'text' parameter"}, status=400)

    # Limit length for demo safety
    if len(text) > 500:
        text = text[:500] + "..."

    try:
        # Use deterministic filename based on content hash for caching
        import hashlib
        content_hash = hashlib.sha256(f"{voice}:{text}".encode()).hexdigest()[:16]
        out_path = AUDIO_DIR / f"{content_hash}.mp3"

        if not out_path.exists():
            communicate = edge_tts.Communicate(text, voice=voice)
            with open(out_path, "wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])

        # Read and return audio with proper headers
        with open(out_path, "rb") as f:
            audio_bytes = f.read()

        headers = {
            "Content-Type": "audio/mpeg",
            "Content-Length": str(len(audio_bytes)),
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Cache-Control": "public, max-age=3600",
        }
        return web.Response(body=audio_bytes, headers=headers)

    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def health_handler(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "teebot-demo-tts"})


async def voices_handler(request: web.Request) -> web.Response:
    """List available EdgeTTS voices."""
    try:
        voices = await edge_tts.list_voices()
        # Return a curated list
        curated = []
        for v in voices:
            locale = v.get("Locale", "")
            if locale.startswith("vi") or locale.startswith("en") or locale.startswith("zh"):
                curated.append({
                    "name": v["ShortName"],
                    "locale": locale,
                    "gender": v.get("Gender", "Unknown"),
                    "friendly": v.get("FriendlyName", ""),
                })
        headers = {"Access-Control-Allow-Origin": "*"}
        return web.json_response({"voices": curated}, headers=headers)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def cors_preflight(request: web.Request) -> web.Response:
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }
    return web.Response(headers=headers)


def main():
    app = web.Application()
    app.router.add_get("/demo/tts", tts_handler)
    app.router.add_post("/demo/tts", tts_handler)
    app.router.add_options("/demo/tts", cors_preflight)
    app.router.add_get("/demo/health", health_handler)
    app.router.add_get("/demo/voices", voices_handler)
    app.router.add_options("/demo/voices", cors_preflight)

    print(f"🎙️  TeeBot Demo TTS Server starting on http://{HOST}:{PORT}")
    print(f"   Try: http://localhost:{PORT}/demo/tts?text=Chào+bạn")
    print(f"   Health: http://localhost:{PORT}/demo/health")
    web.run_app(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
