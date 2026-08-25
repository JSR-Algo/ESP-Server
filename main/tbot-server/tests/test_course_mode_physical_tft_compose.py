import json
import os
import subprocess
from pathlib import Path

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


ROOT = Path(__file__).resolve().parents[3]
BASE_COMPOSE = ROOT / "docs/docker/docker-compose.lesson-studio-e2e.yml"
OVERLAY_COMPOSE = ROOT / "docs/docker/docker-compose.course-mode-physical-tft.yml"
PHYSICAL_TFT_UP = ROOT / "docs/docker/course-mode-physical-tft/up.sh"


def _test_key_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode(),
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )


def _write_admin_w1_fixture(backend):
    fixture = backend / "src/lessons/fixtures/course-mode/admin-w1"
    assets = fixture / "assets"
    assets.mkdir(parents=True)
    (fixture / "lesson.json").write_text('{"lessonKey":"w01-greetings-politeness"}\n')
    (fixture / "published-pack.json").write_text(
        '{"assets":[{"sha256":"abc123","mediaType":"image/png"}]}\n'
    )
    (assets / "abc123.png").write_bytes(b"fixture")


class ComposeLoader(yaml.SafeLoader):
    pass


def _construct_override(loader, node):
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_sequence(node)


ComposeLoader.add_constructor(
    "!override",
    _construct_override,
)


def test_physical_tft_override_is_loopback_only_and_one_device_scoped():
    raw = OVERLAY_COMPOSE.read_text(encoding="utf-8")
    overlay = yaml.load(raw, Loader=ComposeLoader)

    assert set(overlay) == {"name", "services"}
    assert overlay["name"] == "tbot-course-mode-physical-tft"
    assert set(overlay["services"]) == {
        "backend",
        "course-mode-materialize",
        "seed-postgres",
        "seed-mysql",
        "web",
    }
    assert "volumes" not in overlay
    assert "volumes" not in overlay["services"]["backend"]
    assert overlay["services"]["backend"]["environment"][
        "TBOT_DEVICE_MINT_SECRET"
    ] == "${TBOT_DEVICE_MINT_SECRET:?export the shared local device mint secret}"
    assert overlay["services"]["backend"]["environment"]["JWT_PRIVATE_KEY"] == (
        "${JWT_PRIVATE_KEY:?course-mode physical TFT requires a matching local private PEM}"
    )
    materialize = overlay["services"]["course-mode-materialize"]
    assert overlay["services"]["seed-postgres"]["profiles"] == [
        "disabled-course-mode-physical-tft"
    ]
    assert overlay["services"]["seed-mysql"]["profiles"] == [
        "disabled-course-mode-physical-tft"
    ]
    assert materialize["environment"]["COURSE_MODE_LOCAL_COMPOSE_ENABLED"] == "true"
    assert materialize["environment"]["COURSE_MODE_LOCAL_FIXTURE"] == "admin-w1"
    assert "COURSE_MODE_V2_PUBLISH_ENABLED" not in materialize["environment"]
    assert materialize["environment"]["COURSE_MODE_DEVICE_MAC"] == "14:c1:9f:d1:ac:20"
    assert overlay["services"]["backend"]["image"] == (
        "${TBOT_LESSON_STUDIO_BACKEND_IMAGE:?run docs/docker/course-mode-physical-tft/up.sh}"
    )
    assert materialize["image"] == overlay["services"]["backend"]["image"]
    assert overlay["services"]["backend"]["environment"]["ROBOT_ESP_BASE_URL"] == (
        "http://host.docker.internal:8003"
    )
    assert overlay["services"]["backend"]["environment"]["TBOT_ESP_SERVER_URL"] == (
        "http://host.docker.internal:8003"
    )
    assert overlay["services"]["backend"]["extra_hosts"] == [
        "host.docker.internal:host-gateway"
    ]
    assert all("seed" not in str(value).lower() for value in materialize.values())
    assert all(".sql" not in str(value).lower() for value in materialize.values())

    env = os.environ.copy()
    for variable in (
        "COMPOSE_PROJECT_NAME",
        "LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME",
        "LESSON_STUDIO_E2E_RESOURCE_PREFIX",
    ):
        env.pop(variable, None)
    env.update(
        {
            "JWT_PUBLIC_KEY": "-----BEGIN PUBLIC KEY-----\nlab-public\n-----END PUBLIC KEY-----",
            "JWT_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\nlab-private\n-----END PRIVATE KEY-----",
            "TBOT_DEVICE_MINT_SECRET": "dummy-local-mint-secret",
            "LESSON_ASSET_ORIGIN_BASE": "http://127.0.0.1:8102/tvideo-demo",
            "ROBOT_ESP_BASE_URL": "http://host.docker.internal:8080",
            "COURSE_MODE_ASSET_ORIGIN_BASE": "http://192.168.100.183:8102/",
            "TBOT_BACKEND_WORKTREE": "/tmp/task-owned-backend",
            "TBOT_LESSON_STUDIO_BACKEND_IMAGE": (
                "local/tbot-backend:course-mode-physical-tft-0123456789abcdef"
            ),
        }
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(BASE_COMPOSE),
            "-f",
            str(OVERLAY_COMPOSE),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        cwd=ROOT,
        env=env,
        text=True,
    )
    compose = json.loads(result.stdout)
    backend = compose["services"]["backend"]
    materialize = compose["services"]["course-mode-materialize"]
    web = compose["services"]["web"]

    assert compose["name"] == "tbot-course-mode-physical-tft"
    assert backend["container_name"] == "tbot-course-mode-physical-tft-backend"
    assert compose["networks"]["lesson-studio-e2e"]["name"] == (
        "tbot-course-mode-physical-tft"
    )
    assert {
        volume["name"] for volume in compose["volumes"].values()
    } == {
        "tbot-course-mode-physical-tft-pg-data",
        "tbot-course-mode-physical-tft-redis-data",
        "tbot-course-mode-physical-tft-mysql-data",
    }
    assert "tbot-ls-e2e" not in result.stdout
    assert backend["ports"] == [
        {
            "mode": "ingress",
            "host_ip": "127.0.0.1",
            "target": 3000,
            "published": "3000",
            "protocol": "tcp",
        }
    ]
    assert backend["extra_hosts"] == ["host.docker.internal=host-gateway"]
    required_environment = (
        "COURSE_MODE_V2_PUBLISH_ENABLED",
        "LESSON_STUDIO_NEW_ASSIGNMENTS_ENABLED",
        "LESSON_ROLLOUT_DEVICE_ALLOWLIST",
        "TBOT_DEVICE_MINT_SECRET",
        "COURSE_MODE_LOCAL_PHYSICAL_TFT_ENABLED",
        "FLATTENED_CINEMATIC_PUBLIC_BASE_URL",
    )
    assert {
        key: backend["environment"][key] for key in required_environment
    } == {
        "COURSE_MODE_V2_PUBLISH_ENABLED": "true",
        "LESSON_STUDIO_NEW_ASSIGNMENTS_ENABLED": "true",
        "LESSON_ROLLOUT_DEVICE_ALLOWLIST": "14:c1:9f:d1:ac:20",
        "TBOT_DEVICE_MINT_SECRET": "dummy-local-mint-secret",
        "COURSE_MODE_LOCAL_PHYSICAL_TFT_ENABLED": "true",
        "FLATTENED_CINEMATIC_PUBLIC_BASE_URL": "http://192.168.100.183:8102/",
    }
    assert materialize["depends_on"]["backend"]["condition"] == "service_healthy"
    assert backend["image"] == "local/tbot-backend:course-mode-physical-tft-0123456789abcdef"
    assert materialize["image"] == backend["image"]
    assert materialize["command"] == [
        "dist/lessons/course-mode/course-mode-local-materializer.js",
        "materialize",
    ]
    assert materialize["environment"]["DATABASE_URL"] == "postgresql://tbot:tbot@postgres:5432/tbot"
    assert materialize["environment"]["COURSE_MODE_FIXTURE_ROOT"] == "/course-mode-fixtures"
    assert materialize["volumes"] == [
        {
            "type": "bind",
            "source": "/tmp/task-owned-backend/src/lessons/fixtures/course-mode",
            "target": "/course-mode-fixtures",
            "read_only": True,
            "bind": {},
        }
    ]
    assert web["volumes"] == [
        {
            "type": "bind",
            "source": "/tmp/task-owned-backend/src/lessons/fixtures/course-mode/admin-w1/assets",
            "target": "/usr/share/nginx/html/lesson-assets",
            "read_only": True,
            "bind": {},
        }
    ]
    robot_facing_origins = {
        backend["environment"]["LESSON_ASSET_ORIGIN_BASE"],
        backend["environment"]["FLATTENED_CINEMATIC_PUBLIC_BASE_URL"],
        materialize["environment"]["FLATTENED_CINEMATIC_PUBLIC_BASE_URL"],
    }
    assert robot_facing_origins == {"http://192.168.100.183:8102/"}
    assert "192.168.0.120" not in result.stdout
    assert set(web["depends_on"]) == {"backend", "course-mode-materialize", "mysql", "redis"}
    assert "seed-postgres" not in compose["services"]
    assert "seed-mysql" not in compose["services"]
    assert "seed-postgres" not in web["depends_on"]

    lowered = raw.lower()
    assert "production" not in lowered
    assert "prod." not in lowered
    assert "password:" not in lowered
    assert "token:" not in lowered
    assert "https://" not in lowered
    assert "seed-course-mode" not in lowered
    assert ".sql" not in lowered


def test_physical_tft_up_builds_exact_sha_image_before_render_or_start(tmp_path):
    script = PHYSICAL_TFT_UP.read_text(encoding="utf-8")
    assert 'openssl pkey -in "${BACKEND_ROOT}/keys/dev-private-pkcs8.pem" -pubout -outform DER' in script
    assert 'openssl pkey -pubin -in "${BACKEND_ROOT}/keys/dev-public.pem" -outform DER' in script
    assert 'export JWT_PRIVATE_KEY="$(cat "${BACKEND_ROOT}/keys/dev-private-pkcs8.pem")"' in script

    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "keys").mkdir()
    public_pem, private_pem = _test_key_pair()
    (backend / "keys" / "dev-public.pem").write_bytes(public_pem.replace("\n", "\r\n").encode())
    (backend / "keys" / "dev-private-pkcs8.pem").write_text(private_pem, encoding="utf-8")
    (backend / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    materializer = backend / "src/lessons/course-mode/course-mode-local-materializer.ts"
    materializer.parent.mkdir(parents=True)
    materializer.write_text("export {};\n", encoding="utf-8")
    _write_admin_w1_fixture(backend)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    sha = "0123456789abcdef0123456789abcdef01234567"

    (fake_bin / "git").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"if [[ \"$*\" == *\"rev-parse --show-toplevel\"* ]]; then echo {backend}; exit 0; fi\n"
        f"if [[ \"$*\" == *\"rev-parse HEAD\"* ]]; then echo {sha}; exit 0; fi\n"
        "if [[ \"$*\" == *\"status --porcelain\"* ]]; then exit 0; fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    (fake_bin / "npm").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"printf 'npm cwd=%s args=%s\\n' \"$PWD\" \"$*\" >> {log}\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "[[ -z \"${COMPOSE_PROFILES:-}\" ]] || exit 97\n"
        f"printf 'docker %s\\n' \"$*\" >> {log}\n"
        f"printf 'asset=%s robot=%s esp=%s\\n' \"${{LESSON_ASSET_ORIGIN_BASE:-}}\" \"${{ROBOT_ESP_BASE_URL:-}}\" \"${{TBOT_ESP_SERVER_URL:-}}\" >> {log}.env\n"
        f"printf 'asset_public=%s device=%s\\n' \"${{LESSON_ASSET_PUBLIC_BASE_URL:-}}\" \"${{TASK07_DEVICE_MAC:-}}\" >> {log}.env\n"
        f"if [[ \"$*\" == *\"inspect --format\"* ]]; then echo {BASE_COMPOSE},{OVERLAY_COMPOSE}; fi\n"
        "if [[ \"$*\" == *\"exec -T postgres psql\"* ]]; then echo missing; fi\n",
        encoding="utf-8",
    )
    for command in ("git", "npm", "docker"):
        (fake_bin / command).chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "TBOT_BACKEND_WORKTREE": str(backend),
            "TBOT_BACKEND_GIT_SHA": sha,
            "JWT_PUBLIC_KEY": "-----BEGIN PUBLIC KEY-----\nlab-public\n-----END PUBLIC KEY-----",
            "JWT_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\nlab-private\n-----END PRIVATE KEY-----",
            "TBOT_DEVICE_MINT_SECRET": "dummy-local-mint-secret",
            "COURSE_MODE_ASSET_ORIGIN_BASE": "http://192.168.100.183:8102/",
            "COMPOSE_PROJECT_NAME": "unrelated-stack",
            "COMPOSE_PROFILES": "disabled-course-mode-physical-tft",
            "LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME": "another-stack",
            "LESSON_STUDIO_E2E_RESOURCE_PREFIX": "external-resources",
        }
    )
    subprocess.run(
        [str(PHYSICAL_TFT_UP), "--config-only"],
        check=True,
        cwd=ROOT,
        env=env,
        text=True,
    )

    calls = log.read_text(encoding="utf-8").splitlines()
    expected_image = f"local/tbot-backend:course-mode-physical-tft-{sha}"
    assert calls[0] == f"npm cwd={backend} args=run build"
    assert calls[1] == (
        "docker build --pull=false --label "
        "com.tbot.course-mode.materializer-path=/app/dist/lessons/course-mode/"
        "course-mode-local-materializer.js --label "
        f"org.opencontainers.image.revision={sha} --label "
        "com.tbot.course-mode.build-source=reviewed-clean-git-worktree "
        f"-f {backend}/Dockerfile -t {expected_image} {backend}"
    )
    assert calls[2] == (
        "docker run --rm --entrypoint /nodejs/bin/node "
        f"{expected_image} -e require('node:fs').accessSync('/app/dist/lessons/"
        "course-mode/course-mode-local-materializer.js')"
    )
    assert " compose --project-name tbot-course-mode-physical-tft " in f" {calls[3]} "
    assert calls[3].endswith("config --quiet")
    assert "unrelated-stack" not in calls[3]
    assert "another-stack" not in calls[3]
    assert "external-resources" not in calls[3]
    assert all(" up " not in f" {call} " for call in calls)
    assert set(Path(f"{log}.env").read_text().splitlines()) == {
        "asset=http://192.168.100.183:8102/ robot=http://192.168.100.183:8003 esp=http://192.168.100.183:8003",
        "asset_public=http://192.168.100.183:8003/ device=14:c1:9f:d1:ac:20",
    }

    log.unlink()
    Path(f"{log}.env").unlink()
    subprocess.run(
        [str(PHYSICAL_TFT_UP)],
        check=True,
        cwd=ROOT,
        env=env,
        text=True,
    )
    start_calls = log.read_text(encoding="utf-8").splitlines()
    assert any(
        call.endswith("up -d --force-recreate tbot-esp32-server")
        for call in start_calls
    )
    assert any(call.endswith("up -d --wait postgres redis mysql backend") for call in start_calls)
    assert any("exec -T postgres psql" in call for call in start_calls)
    assert any(
        call.endswith("run --rm -e COURSE_MODE_LOCAL_FIXTURE=cat-ball course-mode-materialize")
        for call in start_calls
    )
    assert start_calls[-1].endswith("up -d")


def test_physical_tft_up_rejects_incomplete_admin_w1_fixture_before_build(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "keys").mkdir()
    public_pem, private_pem = _test_key_pair()
    (backend / "keys" / "dev-public.pem").write_text(public_pem)
    (backend / "keys" / "dev-private-pkcs8.pem").write_text(private_pem)
    (backend / "Dockerfile").write_text("FROM scratch\n")
    materializer = backend / "src/lessons/course-mode/course-mode-local-materializer.ts"
    materializer.parent.mkdir(parents=True)
    materializer.write_text("export {};\n")
    fixture = backend / "src/lessons/fixtures/course-mode/admin-w1"
    fixture.mkdir(parents=True)
    (fixture / "assets").mkdir()
    (fixture / "lesson.json").write_text("{}\n")
    (fixture / "published-pack.json").write_text(
        '{"assets":[{"sha256":"missing","mediaType":"image/png"}]}\n'
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sha = "0123456789abcdef0123456789abcdef01234567"
    (fake_bin / "git").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f"if [[ \"$*\" == *\"rev-parse --show-toplevel\"* ]]; then echo {backend}; exit 0; fi\n"
        f"if [[ \"$*\" == *\"rev-parse HEAD\"* ]]; then echo {sha}; exit 0; fi\n"
        "if [[ \"$*\" == *\"status --porcelain\"* ]]; then exit 0; fi\nexit 2\n"
    )
    (fake_bin / "npm").write_text("#!/usr/bin/env bash\nexit 99\n")
    for command in ("git", "npm"):
        (fake_bin / command).chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        "TBOT_BACKEND_WORKTREE": str(backend),
        "TBOT_BACKEND_GIT_SHA": sha,
        "TBOT_DEVICE_MINT_SECRET": "dummy-local-mint-secret",
        "COURSE_MODE_ASSET_ORIGIN_BASE": "http://192.168.100.183:8102/",
    })
    result = subprocess.run(
        [str(PHYSICAL_TFT_UP), "--config-only"], cwd=ROOT, env=env,
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "Admin W1 fixture asset is missing" in result.stderr


def test_physical_tft_up_rejects_backend_sha_mismatch_before_build(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    actual_sha = "0123456789abcdef0123456789abcdef01234567"

    (fake_bin / "git").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"if [[ \"$*\" == *\"rev-parse --show-toplevel\"* ]]; then echo {backend}; exit 0; fi\n"
        f"if [[ \"$*\" == *\"rev-parse HEAD\"* ]]; then echo {actual_sha}; exit 0; fi\n"
        "if [[ \"$*\" == *\"status --porcelain\"* ]]; then exit 0; fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    for command in ("pnpm", "docker"):
        (fake_bin / command).write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' {command} >> {log}\n",
            encoding="utf-8",
        )
    for command in ("git", "pnpm", "docker"):
        (fake_bin / command).chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "TBOT_BACKEND_WORKTREE": str(backend),
            "TBOT_BACKEND_GIT_SHA": "ffffffffffffffffffffffffffffffffffffffff",
        }
    )
    result = subprocess.run(
        [str(PHYSICAL_TFT_UP), "--config-only"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "does not match TBOT_BACKEND_GIT_SHA" in result.stderr
    assert not log.exists()


def test_physical_tft_up_rejects_dirty_backend_before_build(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    materializer = backend / "src/lessons/course-mode/course-mode-local-materializer.ts"
    materializer.parent.mkdir(parents=True)
    materializer.write_text("export {};\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    sha = "0123456789abcdef0123456789abcdef01234567"

    (fake_bin / "git").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"if [[ \"$*\" == *\"rev-parse --show-toplevel\"* ]]; then echo {backend}; exit 0; fi\n"
        f"if [[ \"$*\" == *\"rev-parse HEAD\"* ]]; then echo {sha}; exit 0; fi\n"
        "if [[ \"$*\" == *\"status --porcelain\"* ]]; then echo ' M src/lessons/course-mode/course-mode-local-materializer.ts'; exit 0; fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    for command in ("pnpm", "docker"):
        (fake_bin / command).write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' {command} >> {log}\n",
            encoding="utf-8",
        )
    for command in ("git", "pnpm", "docker"):
        (fake_bin / command).chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "TBOT_BACKEND_WORKTREE": str(backend),
            "TBOT_BACKEND_GIT_SHA": sha,
        }
    )
    result = subprocess.run(
        [str(PHYSICAL_TFT_UP), "--config-only"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "backend worktree must be clean" in result.stderr
    assert not log.exists()
