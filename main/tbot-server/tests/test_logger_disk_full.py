import errno
import importlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock


class LoggerDiskFullTests(unittest.TestCase):
    def setUp(self):
        logger_config = sys.modules.get("config.logger")
        if logger_config is not None and not hasattr(logger_config, "_SafeFileSink"):
            del sys.modules["config.logger"]
        logger_config = importlib.import_module("config.logger")

        self.logger_config = logger_config
        self.original_initialized = logger_config._logger_initialized
        logger_config._logger_initialized = False

    def tearDown(self):
        self.logger_config._logger_initialized = self.original_initialized

    def test_safe_file_sink_disables_after_disk_full_without_raising(self):
        stderr = io.StringIO()
        sink = self.logger_config._SafeFileSink("tmp/server.log", stderr=stderr)

        with mock.patch("builtins.open", side_effect=OSError(errno.ENOSPC, "No space left on device")):
            sink("first message\n")
            sink("second message\n")

        self.assertTrue(sink.disabled)
        self.assertEqual(stderr.getvalue().count("file logging disabled"), 1)

    def test_safe_file_sink_rotates_by_size(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = f"{temp_dir}/server.log"
            sink = self.logger_config._SafeFileSink(log_path, max_bytes=8)

            sink("12345678")
            sink("90")
            sink._close_file()

            with open(log_path, "r", encoding="utf-8") as file:
                self.assertEqual(file.read(), "90")
            rotated = [
                name for name in os.listdir(temp_dir) if name.startswith("server.log.")
            ]
            self.assertEqual(len(rotated), 1)

    def test_setup_logging_does_not_fetch_manager_config(self):
        with (
            mock.patch(
                "config.config_loader.get_config_from_api_async",
                side_effect=AssertionError("manager config fetch must not run"),
            ),
            mock.patch.object(self.logger_config.logger, "configure"),
            mock.patch.object(self.logger_config.logger, "remove"),
            mock.patch.object(self.logger_config.logger, "add"),
        ):
            self.logger_config.setup_logging()


if __name__ == "__main__":
    unittest.main()
