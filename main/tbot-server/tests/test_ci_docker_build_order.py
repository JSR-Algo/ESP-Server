from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_ci_builds_server_base_before_runtime_image():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    base_build = "docker build -f Dockerfile-server-base -t tbot-server-base:test ."
    runtime_build = "docker build -f Dockerfile-server --build-arg TBOT_SERVER_BASE_IMAGE=tbot-server-base:test -t tbot-server:test ."

    assert base_build in workflow
    assert runtime_build in workflow
    assert workflow.index(base_build) < workflow.index(runtime_build)


def test_ci_runtime_image_uses_the_locally_built_base():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "--build-arg TBOT_SERVER_BASE_IMAGE=tbot-server-base:test" in workflow


def test_ci_security_scan_uses_the_locally_built_base():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    scan_start = workflow.index("security-scan:")
    scan_job = workflow[scan_start:]

    assert "docker build -f Dockerfile-server-base -t tbot-server-base:test ." in scan_job
    assert "--build-arg TBOT_SERVER_BASE_IMAGE=tbot-server-base:test" in scan_job


def test_server_base_image_installs_torch_pins_from_requirements():
    dockerfile = (ROOT / "Dockerfile-server-base").read_text(encoding="utf-8")

    assert "grep -E '^(torch|torchaudio)==' requirements.txt > requirements.torch.txt" in dockerfile
    assert "pip install --no-cache-dir" in dockerfile
    assert "-r requirements.torch.txt" in dockerfile
    assert "torch==2.2.2+cpu" not in dockerfile
    assert "torchaudio==2.2.2+cpu" not in dockerfile
