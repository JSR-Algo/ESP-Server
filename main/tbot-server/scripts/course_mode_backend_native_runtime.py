"""Owned PostgreSQL lifecycle for the canonical backend native test lane."""
from __future__ import annotations

import json
import importlib
from pathlib import Path
import re
import secrets
import subprocess
import time
import uuid

try:
    _manifest = importlib.import_module("scripts.course_mode_candidate_manifest")
except ModuleNotFoundError:
    _manifest = importlib.import_module("course_mode_candidate_manifest")


class NativePrerequisiteError(RuntimeError):
    pass


class NativeCleanupError(RuntimeError):
    pass


class NativeBuildError(RuntimeError):
    pass


def _run(command, **kwargs):
    result = _manifest.run_bounded_command(
        command, cwd=Path("/"), env=kwargs["env"], timeout_sec=30,
        max_output_bytes=64 * 1024,
    )
    if result.error:
        raise NativePrerequisiteError("bounded Docker command failed")
    return result


class OwnedPostgres:
    def __init__(self, docker, image, environment):
        if re.fullmatch(r"sha256:[0-9a-f]{64}", image) is None:
            raise NativePrerequisiteError("database image must be pinned by ID")
        self.docker, self.image = docker, image
        self.environment = dict(environment)
        self.owner = uuid.uuid4().hex
        self.name = "course-mode-backend-" + self.owner
        self.identifier = None
        self.attempted = False
        self.url = None

    def _command(self, *arguments, environment=None):
        return _run([self.docker, *arguments], env=self.environment if environment is None else environment)

    def _inspect(self):
        result = self._command("inspect", "--type", "container", self.identifier or self.name)
        if result.returncode:
            inventory = self._command("container", "ls", "--all", "--no-trunc", "--filter",
                "label=com.tbot.course-mode.owner=" + self.owner, "--format", "{{.ID}}")
            if inventory.returncode == 0 and not inventory.stdout.strip():
                return None
            raise NativeCleanupError("owned database inventory unavailable: " + self.name)
        try:
            values = json.loads(result.stdout)
            if len(values) != 1 or not isinstance(values[0], dict):
                raise ValueError()
            value = values[0]
            if (value["Config"]["Labels"].get("com.tbot.course-mode.owner") != self.owner
                    or re.fullmatch(r"[0-9a-f]{64}", value["Id"]) is None
                    or self.identifier is not None and value["Id"] != self.identifier):
                raise ValueError()
            return value
        except (KeyError, TypeError, ValueError):
            raise NativeCleanupError("owned database identity mismatch: " + self.name) from None

    def __enter__(self):
        password = secrets.token_hex(32)
        try:
            self.attempted = True
            created = self._command(
                "create", "--pull", "never", "--name", self.name,
                "--label", "com.tbot.course-mode.owner=" + self.owner,
                "--publish", "127.0.0.1::5432", "--env", "POSTGRES_PASSWORD",
                self.image, environment={**self.environment, "POSTGRES_PASSWORD": password},
            )
            if created.returncode or re.fullmatch(r"[0-9a-f]{64}", created.stdout.strip()) is None:
                raise NativePrerequisiteError("owned database creation failed")
            self.identifier = created.stdout.strip()
            observed = self._inspect()
            if observed is None or observed.get("Image") != self.image:
                raise NativePrerequisiteError("owned database image mismatch")
            if self._command("start", self.identifier).returncode:
                raise NativePrerequisiteError("owned database startup failed")
            deadline = time.monotonic() + 30
            while self._command("exec", self.identifier, "pg_isready", "-U", "postgres", "-d", "postgres").returncode:
                if time.monotonic() >= deadline:
                    raise NativePrerequisiteError("owned database readiness timeout")
                time.sleep(0.2)
            observed = self._inspect()
            ports = observed["NetworkSettings"]["Ports"]["5432/tcp"]
            if (len(ports) != 1 or ports[0].get("HostIp") != "127.0.0.1"
                    or not str(ports[0].get("HostPort", "")).isdigit()
                    or not 1 <= int(ports[0]["HostPort"]) <= 65535):
                raise NativePrerequisiteError("owned database must expose only loopback")
            self.url = f"postgresql://postgres:{password}@127.0.0.1:{ports[0]['HostPort']}/postgres"
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            self._close()
        except BaseException as error:
            raise NativeCleanupError("owned database cleanup unverified: " + self.name) from error

    def _close(self):
        if not self.attempted:
            return
        observed = self._inspect()
        if observed is None:
            if self.identifier is not None:
                raise NativeCleanupError("owned database disappeared before cleanup: " + self.name)
            return
        if self._command("rm", "--force", "--volumes", observed["Id"]).returncode:
            raise NativeCleanupError("owned database cleanup failed: " + self.name)
        if self._inspect() is not None:
            raise NativeCleanupError("owned database remains after cleanup: " + self.name)
        self.attempted = False
        self.url = None

    def __exit__(self, *_):
        self.close()
