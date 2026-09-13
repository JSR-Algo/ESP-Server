"""Terminal replay ownership against an isolated real Redis server."""

import asyncio
import json
import shutil
import socket
import subprocess
import time
import uuid

import pytest
import redis
from redis import asyncio as redis_asyncio

from core.lesson.forwarder import (
    LessonEventForwarder,
    RedisTerminalReplayStore,
    replay_stored_terminal_event,
)


@pytest.fixture(scope="module")
def owned_redis(tmp_path_factory):
    executable = shutil.which("redis-server")
    assert executable is not None, "real Redis prerequisite unavailable"
    folder = tmp_path_factory.mktemp("course-terminal-owned-redis")
    folder.chmod(0o700)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    command = [
        executable, "--bind", "127.0.0.1", "--port", str(port),
        "--protected-mode", "yes", "--dir", str(folder),
        "--dbfilename", "owned-terminal.rdb", "--save", "",
        "--appendonly", "no",
    ]
    url = f"redis://127.0.0.1:{port}/0"
    client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
    with (folder / "server.log").open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        identified = False
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                assert process.poll() is None, "owned Redis exited before readiness"
                try:
                    identity = client.info("server")
                    identified = identity["process_id"] == process.pid
                    assert identified
                    break
                except redis.ConnectionError:
                    time.sleep(0.02)
            assert identified, "owned Redis did not become ready"
            (folder / "identity.json").write_text(json.dumps({
                "argv": command, "pid": process.pid, "port": port,
                "redisVersion": identity["redis_version"], "url": url,
            }, indent=2))
            yield url
        finally:
            if identified and process.poll() is None:
                assert client.info("server")["process_id"] == process.pid
                client.shutdown(save=True)
            elif process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
            client.close()
            with socket.socket() as probe:
                stopped = probe.connect_ex(("127.0.0.1", port)) != 0
            (folder / "cleanup.json").write_text(json.dumps({
                "pid": process.pid, "exit": process.returncode,
                "portClosed": stopped, "snapshotRetained": (folder / "owned-terminal.rdb").exists(),
            }, indent=2))
            assert stopped


def terminal(session, event):
    return {
        "assignmentId": "assignment-1", "sessionId": session,
        "events": [{"type": event}],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("replay_kind", ["fresh_connection", "existing_forwarder"])
async def test_old_replay_success_preserves_newer_terminal_batch(owned_redis, replay_kind):
    client = redis_asyncio.from_url(owned_redis, decode_responses=True)
    writer_client = redis_asyncio.from_url(owned_redis, decode_responses=True)
    namespace = "t13-owned-" + uuid.uuid4().hex
    store = RedisTerminalReplayStore(url=owned_redis, namespace=namespace, client=client)
    writer = RedisTerminalReplayStore(url=owned_redis, namespace=namespace, client=writer_client)
    original = terminal("old-session", "lesson_completed")
    newer = terminal("new-session", "lesson_failed")
    post_started = asyncio.Event()
    release_post = asyncio.Event()
    posted = []

    async def post(*args, **kwargs):
        batch = args[3] if len(args) > 1 else args[0]
        posted.append(batch)
        post_started.set()
        await release_post.wait()

    task = None
    try:
        await store.store("device-1", original)
        if replay_kind == "fresh_connection":
            task = asyncio.create_task(replay_stored_terminal_event(
                device_id="device-1", assignment_id="assignment-1", base_url="http://backend.invalid",
                post_fn=post, terminal_store=store,
            ))
        else:
            forwarder = LessonEventForwarder(
                device_id="device-1", base_url="http://backend.invalid", terminal_store=store,
            )
            forwarder.pending_terminal_batch = original
            forwarder._post = post
            task = asyncio.create_task(forwarder.replay_pending_terminal_event())
        await asyncio.wait_for(post_started.wait(), timeout=2)
        await writer.store("device-1", newer)
        key = writer._key("device-1", "assignment-1")
        ttl_before = await writer_client.pttl(key)
        release_post.set()
        assert await asyncio.wait_for(task, timeout=2) is True
        assert posted == [original]
        assert await writer.load("device-1", "assignment-1") == newer
        assert 0 < await writer_client.pttl(key) <= ttl_before
        await writer.clear("device-1", newer)
        assert await writer.load("device-1", "assignment-1") is None
    finally:
        release_post.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await client.aclose()
        await writer_client.aclose()


@pytest.mark.asyncio
async def test_clear_accepts_equivalent_batch_with_different_key_order(owned_redis):
    client = redis_asyncio.from_url(owned_redis, decode_responses=True)
    store = RedisTerminalReplayStore(
        url=owned_redis, namespace="t13-owned-" + uuid.uuid4().hex, client=client,
    )
    batch = terminal("same-session", "lesson_completed")
    try:
        await store.store("device-1", batch)
        await store.clear("device-1", dict(reversed(list(batch.items()))))
        assert await store.load("device-1", "assignment-1") is None
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("decode_responses", [True, False])
async def test_replacement_between_clear_read_and_delete_survives(owned_redis, decode_responses):
    client = redis_asyncio.from_url(owned_redis, decode_responses=decode_responses)
    writer_client = redis_asyncio.from_url(owned_redis, decode_responses=decode_responses)
    namespace = "t13-owned-" + uuid.uuid4().hex
    writer = RedisTerminalReplayStore(url=owned_redis, namespace=namespace, client=writer_client)
    original = terminal("old-session", "lesson_completed")
    newer = terminal("new-session", "lesson_failed")
    replaced = False

    class ReplaceAfterRead:
        async def get(self, key):
            nonlocal replaced
            raw = await client.get(key)
            await writer.store("device-1", newer)
            replaced = True
            return raw

        async def eval(self, *args):
            return await client.eval(*args)

    store = RedisTerminalReplayStore(
        url=owned_redis, namespace=namespace, client=ReplaceAfterRead(),
    )
    try:
        await writer.store("device-1", original)
        await store.clear("device-1", original)
        assert replaced
        assert await writer.load("device-1", "assignment-1") == newer
    finally:
        await client.aclose()
        await writer_client.aclose()
