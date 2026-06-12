import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
