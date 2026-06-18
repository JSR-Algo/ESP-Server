import re
from pathlib import Path


CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"

SECRET_FIELD_RE = re.compile(r"^\s*(api_key|api_secret|access_token|secret_id|secret_key|access_key_secret|token|personal_access_token):\s*(.+?)\s*$")
PLACEHOLDER_RE = re.compile(r"^(\$\{[A-Z0-9_]+(?::[^}]*)?\}|none|null|\"\"|'')$", re.IGNORECASE)


def _strip_inline_comment(value: str) -> str:
    return value.split(" #", 1)[0].strip().strip('"').strip("'")


def test_config_yaml_uses_env_indirection_for_provider_secrets():
    offenders = []
    for lineno, line in enumerate(CONFIG.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = SECRET_FIELD_RE.match(line)
        if not match:
            continue
        value = _strip_inline_comment(match.group(2))
        if PLACEHOLDER_RE.match(value):
            continue
        offenders.append(f"{lineno}: {match.group(1)}: {value}")

    assert offenders == []
