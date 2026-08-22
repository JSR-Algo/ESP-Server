import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/course_mode_physical_tft_receipt_verify.py"
sys.path.insert(0, str(ROOT / "scripts"))

VALID_RECEIPT = {
    "result": "pass",
    "deviceSuffix": "AC:20",
    "lessonKey": "course-mode-pilot-cat-ball",
    "lessonVersion": 1,
    "rendererId": "teebot-lesson-renderer.v4",
    "contractChecksum": "cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264",
    "layoutChecksum": "e61b56d1f8219a86c7f3986e7d5c70b91f512286604b5b206ef11e2c989d275c",
    "manifestChecksum": "a" * 64,
    "cueCount": 8,
    "conversationPresent": False,
}


def _run(tmp_path: Path, document, rerun=None):
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(document), encoding="utf-8")
    command = [sys.executable, str(SCRIPT), str(receipt)]
    if rerun is not None:
        rerun_path = tmp_path / "rerun.json"
        rerun_path.write_text(json.dumps(rerun), encoding="utf-8")
        command.extend(["--rerun-receipt", str(rerun_path)])
    return subprocess.run(command, capture_output=True, text=True)


def test_valid_receipt_and_semantically_identical_rerun(tmp_path):
    result = _run(tmp_path, VALID_RECEIPT, dict(reversed(list(VALID_RECEIPT.items()))))

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "cueCount": 8,
        "deviceSuffix": "AC:20",
        "lessonKey": "course-mode-pilot-cat-ball",
        "rendererId": "teebot-lesson-renderer.v4",
        "valid": True,
    }


def test_library_contract_accepts_valid_single_receipt():
    from course_mode_physical_tft_receipt_verify import validate_receipt_pair

    assert validate_receipt_pair(VALID_RECEIPT, None) == []


def test_identity_mismatches_are_stable_and_redacted(tmp_path):
    replacements = {
        "result": "failed-private-value",
        "deviceSuffix": "14:c1:9f:d1:ac:20",
        "lessonKey": "private-lesson",
        "lessonVersion": 2,
        "rendererId": "legacy-renderer",
        "contractChecksum": "b" * 64,
        "layoutChecksum": "b" * 64,
        "manifestChecksum": "A" * 64,
        "cueCount": 7,
        "conversationPresent": True,
    }
    for field, rejected_value in replacements.items():
        document = deepcopy(VALID_RECEIPT)
        document[field] = rejected_value
        result = _run(tmp_path, document)
        payload = json.loads(result.stdout)
        assert result.returncode == 1
        assert any(field in reason for reason in payload["reasons"])
        assert str(rejected_value) not in result.stdout


def test_strict_schema_and_sensitive_fields_fail_closed(tmp_path):
    cases = []
    extra = deepcopy(VALID_RECEIPT)
    extra["token"] = "do-not-echo-token"
    cases.append((extra, "schema.extra_field"))
    missing = deepcopy(VALID_RECEIPT)
    del missing["cueCount"]
    cases.append((missing, "schema.missing_field.cueCount"))
    cases.append(([VALID_RECEIPT], "schema.not_object"))

    for index, (document, reason) in enumerate(cases):
        case_dir = tmp_path / str(index)
        case_dir.mkdir()
        result = _run(case_dir, document)
        assert result.returncode == 1
        assert reason in json.loads(result.stdout)["reasons"]
        assert "do-not-echo-token" not in result.stdout


def test_short_manifest_hash_and_different_rerun_fail(tmp_path):
    short = deepcopy(VALID_RECEIPT)
    short["manifestChecksum"] = "abc"
    short_dir = tmp_path / "short"
    short_dir.mkdir()
    result = _run(short_dir, short)
    assert "identity.manifestChecksum" in json.loads(result.stdout)["reasons"]

    rerun = deepcopy(VALID_RECEIPT)
    rerun["manifestChecksum"] = "b" * 64
    pair_dir = tmp_path / "pair"
    pair_dir.mkdir()
    result = _run(pair_dir, VALID_RECEIPT, rerun)
    assert result.returncode == 1
    assert json.loads(result.stdout)["reasons"] == ["rerun.semantic_mismatch"]
    assert "b" * 64 not in result.stdout


def test_malformed_json_is_reported_without_echo(tmp_path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"token":"do-not-echo"', encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(receipt)], capture_output=True, text=True
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["reasons"] == ["input.invalid_json"]
    assert "do-not-echo" not in result.stdout
