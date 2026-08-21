#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
#
# F-T64-05 (remainder) / T6.4 deep-dive box 5 — "ESP admin/API handlers
# (lesson_sd_*, nudge, console) require auth — none anonymously callable".
#
# T6.4 closed the lesson_sd_* and nudge halves and stopped the console leaking
# the connected-robot inventory, but the console PAGE itself was still served to
# anyone. It cannot be gated the usual two ways:
#   * not by a header — an operator opens it in a browser to paste a parent JWT,
#     and a browser cannot send X-Mint-Secret;
#   * not by Nginx — cloudflared routes the esp.tjbot.vn catch-all straight to
#     127.0.0.1:8003 and never traverses Nginx at all
#     (deploy/cloudflared/config.yml.example).
# So in production it is not served at all unless deliberately enabled.
set -euo pipefail

cd "$(pwd)/main/tbot-server"

cat > tests/__t64e_repro.py <<'PY'
"""F-T64-05 repro — the operator console is not anonymously callable in production."""

import os
import unittest

ENV_KEYS = ("NODE_ENV", "ENV", "APP_ENV", "PYTHON_ENV", "LESSON_ASSIGN_CONSOLE_ENABLED")


class _Req:
    headers: dict = {}


class ConsoleProductionGateRepro(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    async def _status(self):
        from core.api.lesson_assignment_console_handler import LessonAssignmentConsoleHandler

        handler = LessonAssignmentConsoleHandler({"server": {"api_url": "https://b.test/v1"}}, {})
        return (await handler.handle_get(_Req())).status

    async def test_production_refuses_the_console(self):
        os.environ["NODE_ENV"] = "production"
        self.assertEqual(await self._status(), 404)

    async def test_every_production_alias_refuses_it(self):
        for key in ("ENV", "APP_ENV", "PYTHON_ENV", "NODE_ENV"):
            for other in ENV_KEYS:
                os.environ.pop(other, None)
            os.environ[key] = "production"
            self.assertEqual(await self._status(), 404, key)

    async def test_explicit_opt_in_serves_it(self):
        os.environ["NODE_ENV"] = "production"
        os.environ["LESSON_ASSIGN_CONSOLE_ENABLED"] = "true"
        self.assertEqual(await self._status(), 200)

    async def test_a_truthy_looking_value_is_not_enough(self):
        os.environ["NODE_ENV"] = "production"
        for value in ("1", "yes", "on", "false"):
            os.environ["LESSON_ASSIGN_CONSOLE_ENABLED"] = value
            self.assertEqual(await self._status(), 404, value)

    async def test_development_is_unaffected(self):
        # Non-regression control: local operator + e2e workflows keep the console.
        os.environ["NODE_ENV"] = "development"
        self.assertEqual(await self._status(), 200)


class ConsoleDeployWiringRepro(unittest.TestCase):
    def test_the_flag_defaults_to_off_in_the_production_compose(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        compose = (root / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8")
        assert "LESSON_ASSIGN_CONSOLE_ENABLED: ${LESSON_ASSIGN_CONSOLE_ENABLED:-false}" in compose
        env_example = (root / "deploy" / ".env.example").read_text(encoding="utf-8")
        assert "LESSON_ASSIGN_CONSOLE_ENABLED=false" in env_example
PY

trap 'rm -f tests/__t64e_repro.py' EXIT
python3 -m pytest tests/__t64e_repro.py -q
