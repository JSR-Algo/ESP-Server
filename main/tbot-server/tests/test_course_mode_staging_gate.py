import json

from scripts import course_mode_release_gate as gate
from tests.test_course_mode_release_gate import candidate_file, _lane
from tests.test_course_mode_staging_profile import staging
import pytest


def test_gate_requires_explicit_staging_and_labels_its_receipt(candidate_file):
    candidate = json.loads(candidate_file.read_text())
    staging(candidate)
    candidate_file.write_text(json.dumps(candidate))
    lane = _lane("profile-proof", "raise SystemExit(0)")
    rejected = gate.run_gate(candidate_file, "quick", lanes=(lane,))
    assert rejected["verdict"] == "BLOCKED"
    assert rejected["lanes"] == []
    accepted = gate.run_gate(candidate_file, "quick", lanes=(lane,), qualification_profile="m1-staging")
    assert accepted["verdict"] == "PASS"
    assert accepted["qualificationProfile"] == "m1-staging"
    assert accepted["lanes"][0]["name"] == "profile-proof"


@pytest.mark.parametrize("failure", ["destination", "late-drift", "write-failure"])
def test_blocked_staging_reports_retain_profile(candidate_file, monkeypatch, failure):
    candidate = json.loads(candidate_file.read_text())
    staging(candidate)
    candidate_file.write_text(json.dumps(candidate))
    report_path = candidate_file.parent / "evidence/report.json"
    published = False
    original_write = gate._write_report_atomic
    original_state = gate._candidate_metadata_matches

    def write(*args, **kwargs):
        nonlocal published
        if failure == "write-failure":
            return False
        result = original_write(*args, **kwargs)
        published = True
        return result

    def state(*args, **kwargs):
        return False if published and failure == "late-drift" else original_state(*args, **kwargs)

    monkeypatch.setattr(gate, "_write_report_atomic", write)
    monkeypatch.setattr(gate, "_candidate_metadata_matches", state)
    if failure == "destination":
        monkeypatch.setattr(gate, "_prepare_report_destination", lambda *_: None)
    result = gate.run_gate(candidate_file, "quick", lanes=(_lane("profile-proof", "raise SystemExit(0)"),),
                           qualification_profile="m1-staging", report_path=report_path)
    assert result["verdict"] == "BLOCKED"
    assert result["qualificationProfile"] == "m1-staging"
    if report_path.exists():
        assert json.loads(report_path.read_text())["qualificationProfile"] == "m1-staging"


def test_physical_command_selects_staging_explicitly(candidate_file, monkeypatch):
    from tests.test_course_mode_release_gate import _install_physical_admission_fixture
    candidate, _ = _install_physical_admission_fixture(candidate_file)
    candidate['qualificationProfile'] = 'm1-staging'
    # Command construction only; signed document validation has separate tests.
    monkeypatch.setattr(gate, '_physical_admission_binding', lambda *args, **kwargs: object())
    command = gate.physical_preflight_command(candidate)
    assert command is not None
    assert command[-2:] == ('--profile', 'm1-staging')
