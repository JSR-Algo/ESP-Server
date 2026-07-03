import asyncio
import importlib
import unittest

import uvloop


class UvloopRuntimeTest(unittest.TestCase):
    def test_app_installs_uvloop_event_loop_policy_before_run(self):
        app = importlib.import_module("app")
        previous_policy = asyncio.get_event_loop_policy()
        try:
            app.install_uvloop_policy()
            self.assertIsInstance(asyncio.get_event_loop_policy(), uvloop.EventLoopPolicy)
        finally:
            asyncio.set_event_loop_policy(previous_policy)


if __name__ == "__main__":
    unittest.main()
