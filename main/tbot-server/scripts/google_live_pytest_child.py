"""Run the pinned deterministic pytest bootstrap from Git-bound source."""

import ast
from pathlib import Path

source_path = Path(__file__).resolve().with_name("google_live_deterministic_evidence.py")
tree = ast.parse(source_path.read_bytes(), filename=str(source_path))
bootstrap = None
for statement in tree.body:
    if (
        isinstance(statement, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_PYTEST_BOOTSTRAP" for target in statement.targets)
    ):
        bootstrap = ast.literal_eval(statement.value)
        break
if not isinstance(bootstrap, str):
    raise RuntimeError("pinned pytest bootstrap is unavailable")

exec(bootstrap)
