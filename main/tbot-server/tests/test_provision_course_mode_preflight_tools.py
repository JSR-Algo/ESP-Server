import os
import subprocess
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
SCRIPT = SERVER / "scripts/provision_course_mode_preflight_tools.sh"
SIGNING_GUIDE = SERVER / "docs/course-mode-physical-preflight-signing.md"


def test_provisioning_script_has_valid_shell_syntax_and_public_help():
    subprocess.run(["/bin/bash", "-n", str(SCRIPT)], check=True)
    result = subprocess.run(["/bin/bash", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "/usr/local/libexec/tbot-preflight/<sha256>/docker" in result.stdout
    assert "attended sudo" in result.stdout.lower()


def test_provisioning_script_fails_closed_without_attended_root():
    if os.geteuid() == 0:
        return
    result = subprocess.run(
        [
            "/bin/bash",
            str(SCRIPT),
            "--docker-source",
            "/bin/echo",
            "--docker-sha256",
            "a" * 64,
            "--compose-source",
            "/bin/echo",
            "--compose-sha256",
            "b" * 64,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "attended sudo" in result.stderr.lower()


def test_provisioning_errors_do_not_echo_unknown_argument_values():
    marker = "operator-secret-marker"
    result = subprocess.run(["/bin/bash", str(SCRIPT), marker], capture_output=True, text=True)
    assert result.returncode != 0
    assert marker not in result.stdout + result.stderr


def test_provisioning_contract_uses_verified_fds_and_immutable_root_install():
    source = SCRIPT.read_text()
    for marker in (
        'exec 8<"$docker_source"',
        'exec 9<"$compose_source"',
        "/dev/fd/$source_fd",
        "/usr/bin/shasum -a 256",
        '"$owner_uid" == "0"',
        '"$group_name" == "wheel"',
        '"$mode" == "555"',
        "/bin/chmod 0555",
        "/bin/chmod -N",
        "/usr/sbin/chown root:wheel",
        "/bin/ln",
    ):
        assert marker in source
    assert "/Applications/Docker.app/" not in source
    assert "PRIVATE KEY" not in source
    assert "genpkey" not in source
    assert "rm -rf" not in source
    assert "docker compose" not in source


def test_signing_guide_keeps_private_material_outside_repo_and_signs_canonical_bytes():
    guide = SIGNING_GUIDE.read_text()
    for marker in (
        "explicit operator authority",
        "umask 077",
        "Ed25519PrivateKey.generate()",
        "serialization.PrivateFormat.PKCS8",
        "os.O_EXCL",
        "chmod 0600",
        "sort_keys=True",
        'separators=(",", ":")',
        "private_key.sign(canonical)",
        "64-byte",
        "PINNED_APPROVAL_PUBLIC_KEY_RAW",
        "PINNED_APPROVAL_KEY_FINGERPRINT",
    ):
        assert marker in guide
    assert "Do not run these commands until" in guide
    assert "Never place" in guide


def test_runtime_remains_fail_closed_until_operator_public_key_is_reviewed():
    source = (SERVER / "scripts/course_mode_physical_tft_preflight.py").read_text()
    assert "PINNED_APPROVAL_PUBLIC_KEY_RAW: bytes | None = None" in source
    assert 'PINNED_APPROVAL_KEY_FINGERPRINT = "unprovisioned"' in source
