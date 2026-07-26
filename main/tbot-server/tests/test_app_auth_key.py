import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app


class AppAuthKeyTest(unittest.TestCase):
    def test_resolve_auth_key_prefers_explicit_server_key(self):
        config = {
            "server": {"auth_key": "server-secret"},
            "manager-api": {"secret": "manager-secret"},
        }

        self.assertEqual(app._resolve_auth_key(config), "server-secret")

    def test_resolve_auth_key_uses_manager_secret_when_server_key_missing(self):
        config = {
            "server": {"auth_key": ""},
            "manager-api": {"secret": "manager-secret"},
        }

        self.assertEqual(app._resolve_auth_key(config), "manager-secret")

    def test_resolve_auth_key_persists_generated_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            config = {"server": {"auth_key": ""}, "manager-api": {"secret": ""}}
            fake_uuid = SimpleNamespace(hex="generated-secret")

            with patch.object(app, "get_project_dir", return_value=str(project_dir) + "/"), \
                 patch.object(app.uuid, "uuid4", return_value=fake_uuid):
                self.assertEqual(app._resolve_auth_key(config), "generated-secret")
                self.assertEqual(app._resolve_auth_key(config), "generated-secret")

            key_path = project_dir / app.AUTH_KEY_FILE
            self.assertEqual(key_path.read_text(encoding="utf-8"), "generated-secret\n")

    def test_resolve_auth_key_reuses_existing_fallback_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            key_path = project_dir / app.AUTH_KEY_FILE
            key_path.parent.mkdir(parents=True)
            key_path.write_text("persisted-secret\n", encoding="utf-8")
            config = {"server": {"auth_key": ""}, "manager-api": {"secret": ""}}

            with patch.object(app, "get_project_dir", return_value=str(project_dir) + "/"):
                self.assertEqual(app._resolve_auth_key(config), "persisted-secret")


if __name__ == "__main__":
    unittest.main()

def test_build_servers_shares_one_lesson_sd_online_index(monkeypatch):
    captures = {}

    class WebSocketServer:
        def __init__(self, config, *, lesson_sd_online_index=None):
            self.config = config
            self.lesson_sd_online_index = lesson_sd_online_index
            self.lesson_connections = {}
            captures["ws"] = self

    class SimpleHttpServer:
        def __init__(self, config, lesson_connections, *, lesson_sd_online_index=None):
            self.config = config
            self.lesson_connections = lesson_connections
            self.lesson_sd_online_index = lesson_sd_online_index
            captures["http"] = self

    monkeypatch.setattr(app, "WebSocketServer", WebSocketServer)
    monkeypatch.setattr(app, "SimpleHttpServer", SimpleHttpServer)

    ws_server, http_server = app._build_servers(
        {"server": {"api_url": "http://backend.test/v1"}}
    )

    assert captures["ws"] is ws_server
    assert captures["http"] is http_server
    assert ws_server.lesson_sd_online_index is http_server.lesson_sd_online_index
    assert http_server.lesson_connections is ws_server.lesson_connections


@pytest.mark.asyncio
async def test_http_server_constructor_uses_injected_lesson_sd_online_index():
    from core.http_server import SimpleHttpServer

    shared_index = object()
    server = SimpleHttpServer(
        {"server": {"auth_key": "test-key"}},
        lesson_connections={},
        lesson_sd_online_index=shared_index,
    )

    assert server.lesson_sd_online_index is shared_index
    assert server.lesson_sd_fanout_handler.online_index is shared_index
    assert server.lesson_sd_retry_worker.online_index is shared_index
