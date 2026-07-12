import importlib.util
import re
import subprocess
import sys
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SERVER_ROOT / "scripts" / "check_python_runtime.py"
REPO_ROOT = SERVER_ROOT.parents[1]


def _load_preflight_module():
    spec = importlib.util.spec_from_file_location("check_python_runtime", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_contract_accepts_python_310_and_newer():
    preflight = _load_preflight_module()

    assert preflight.validate_version((3, 10, 0)) is None
    assert preflight.validate_version((3, 13, 2)) is None


def test_runtime_contract_rejects_python_39_with_actionable_message():
    preflight = _load_preflight_module()

    message = preflight.validate_version((3, 9, 19))

    assert message is not None
    assert "Python 3.10 or newer is required" in message
    assert "detected 3.9.19" in message


def test_cli_uses_running_interpreter_and_has_no_third_party_dependencies():
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        cwd=SERVER_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    if sys.version_info >= (3, 10):
        assert result.returncode == 0, result.stderr
        assert "Python runtime OK" in result.stdout
    else:
        assert result.returncode == 1
        assert "Python 3.10 or newer is required" in result.stderr


def test_requirement_profiles_state_the_mandatory_runtime_floor():
    required_comment = "# Requires Python >=3.10."

    for filename in ("requirements.txt", "requirements-google-live.txt"):
        contents = (SERVER_ROOT / filename).read_text(encoding="utf-8")
        assert required_comment in contents, filename


def test_ci_runs_runtime_preflight_before_each_python_dependency_install():
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    preflight = "python main/tbot-server/scripts/check_python_runtime.py"

    assert workflow.count(preflight) >= 3
    for job_name in ("python-test", "python-lint", "dependency-audit"):
        match = re.search(rf"^  {job_name}:$(.*?)(?=^  [a-z][a-z-]+:$|\Z)", workflow, re.MULTILINE | re.DOTALL)
        assert match is not None
        job = match.group(1)
        assert preflight in job
        assert job.index(preflight) < job.index("pip install")
