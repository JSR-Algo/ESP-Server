import asyncio
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace

import tests.test_connection_voice_provider_routing as routing  # noqa: F401

from core.voice.live_admission import (
    AdmissionDecision,
    LiveAdmissionGate,
    RedisLiveStateStore,
)
from core.lesson.forwarder import (
    RedisTerminalReplayStore,
    _PENDING_TERMINAL_BATCHES,
    replay_stored_terminal_event,
)
from core.voice.session_provider.google_live import GoogleLiveProvider


class _Logger:
    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


class _RedisCliAsyncClient:
    def __init__(self, port):
        self.port = port

    async def set(self, key, value, ex=None):
        args = ["SET", key, str(value)]
        if ex is not None:
            args.extend(["EX", str(int(ex))])
        await self._run(*args)
        return True

    async def get(self, key):
        output = await self._run("GET", key)
        return output if output else None

    async def delete(self, key):
        output = await self._run("DEL", key)
        return int(output or 0)

    async def incrbyfloat(self, key, amount):
        output = await self._run("INCRBYFLOAT", key, str(float(amount)))
        return float(output)

    async def expire(self, key, seconds):
        await self._run("EXPIRE", key, str(int(seconds)))
        return True

    async def zadd(self, key, mapping):
        args = ["ZADD", key]
        for member, score in mapping.items():
            args.extend([str(float(score)), str(member)])
        await self._run(*args)
        return len(mapping)

    async def zremrangebyscore(self, key, minimum, maximum):
        output = await self._run("ZREMRANGEBYSCORE", key, str(minimum), str(maximum))
        return int(output or 0)

    async def zcard(self, key):
        output = await self._run("ZCARD", key)
        return int(output or 0)

    async def _run(self, *args):
        proc = await asyncio.create_subprocess_exec(
            "redis-cli",
            "-p",
            str(self.port),
            "--raw",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace"))
        return stdout.decode("utf-8", errors="replace").strip()


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ScaleoutRedisIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if shutil.which("redis-server") is None or shutil.which("redis-cli") is None:
            self.skipTest("redis-server and redis-cli are required for scaleout integration")
        self.tmpdir = tempfile.TemporaryDirectory()
        self.port = _free_port()
        self.redis_proc = subprocess.Popen(
            [
                "redis-server",
                "--port",
                str(self.port),
                "--save",
                "",
                "--appendonly",
                "no",
                "--dir",
                self.tmpdir.name,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            ping = subprocess.run(
                ["redis-cli", "-p", str(self.port), "PING"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
            if ping.stdout.strip() == "PONG":
                return
            await asyncio.sleep(0.05)
        self.fail("temporary redis-server did not become ready")

    async def asyncTearDown(self):
        self.redis_proc.terminate()
        try:
            self.redis_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.redis_proc.kill()
        self.tmpdir.cleanup()

    async def test_kill_replica_a_mid_session_then_reconnect_on_b_resumes(self):
        redis = _RedisCliAsyncClient(self.port)
        store_a = RedisLiveStateStore(redis, namespace="integration", day_key="2026-06-17")
        conn_a = SimpleNamespace(
            config={"google_live": {}},
            device_id="device-1",
            google_live_session_resumption_handle=None,
            live_resumption_store=store_a,
            logger=_Logger(),
        )
        provider_a = GoogleLiveProvider(conn_a)

        provider_a._handle_session_resumption_update(
            {
                "type": "session_resumption_update",
                "resumable": True,
                "handle": "resume-from-replica-a",
            }
        )
        for _ in range(20):
            if await store_a.load("device-1") == "resume-from-replica-a":
                break
            await asyncio.sleep(0.05)

        del provider_a
        del conn_a

        store_b = RedisLiveStateStore(redis, namespace="integration", day_key="2026-06-17")
        conn_b = SimpleNamespace(
            config={"google_live": {}},
            device_id="device-1",
            google_live_session_resumption_handle=None,
            live_resumption_store=store_b,
            logger=_Logger(),
        )
        provider_b = GoogleLiveProvider(conn_b)

        restored = await provider_b._restore_session_resumption_handle()

        self.assertTrue(restored)
        self.assertEqual(conn_b.google_live_session_resumption_handle, "resume-from-replica-a")

    async def test_budget_written_by_replica_a_is_enforced_by_replica_b(self):
        redis = _RedisCliAsyncClient(self.port)
        store_a = RedisLiveStateStore(redis, namespace="integration", day_key="2026-06-17")
        gate_a = LiveAdmissionGate(store_a, daily_device_minutes=1)
        await gate_a.record_live_usage_async("device-1", "house-1", 61)

        store_b = RedisLiveStateStore(redis, namespace="integration", day_key="2026-06-17")
        gate_b = LiveAdmissionGate(store_b, daily_device_minutes=1)

        decision = await gate_b.admit_async("device-1", "house-1")

        self.assertEqual(decision, AdmissionDecision.DEGRADE_TTS_ONLY)

    async def test_terminal_replay_survives_replica_restart_through_redis_store(self):
        redis = _RedisCliAsyncClient(self.port)
        terminal = {
            "assignmentId": "assignment-1",
            "sessionId": "session-1",
            "events": [
                {"type": "lesson_started", "startedAt": 1_700_000_000_000},
                {"type": "lesson_completed", "completedAt": 1_700_000_010_000},
            ],
        }
        replica_a = RedisTerminalReplayStore(
            url=f"redis://127.0.0.1:{self.port}/0",
            namespace="integration",
            client=redis,
        )
        await replica_a.store("device-1", terminal)

        _PENDING_TERMINAL_BATCHES.clear()
        replayed = []

        async def _post(_client, base_url, device_id, batch, *, token=None):
            replayed.append((base_url, device_id, batch, token))

        replica_b = RedisTerminalReplayStore(
            url=f"redis://127.0.0.1:{self.port}/0",
            namespace="integration",
            client=redis,
        )
        self.assertTrue(
            await replay_stored_terminal_event(
                device_id="device-1",
                assignment_id="assignment-1",
                base_url="http://backend.test/v1",
                token="device-token",
                post_fn=_post,
                terminal_store=replica_b,
            )
        )

        self.assertEqual(
            replayed,
            [("http://backend.test/v1", "device-1", terminal, "device-token")],
        )
        self.assertIsNone(await replica_b.load("device-1", "assignment-1"))
