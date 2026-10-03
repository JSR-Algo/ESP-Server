import hashlib
import json
from pathlib import Path

import pytest

from scripts import course_mode_candidate_manifest as manifest
from tests.test_course_mode_candidate_manifest import NOW, candidate, repositories


def staging(candidate):
    candidate["qualificationProfile"] = "m1-staging"
    path = Path(candidate["firmware"]["evidenceManifestPath"])
    evidence = json.loads(path.read_text())
    evidence["profile"] = "m1-staging"
    config = evidence["config"]
    config["stagingConfigAudit"] = config.pop("productionConfigAudit")
    config["stagingArtifactAudit"] = config.pop("productionArtifactAudit")
    write_evidence(candidate, evidence)
    return evidence


def write_evidence(candidate, evidence):
    path = Path(candidate["firmware"]["evidenceManifestPath"])
    path.write_text(json.dumps(evidence))
    candidate["firmware"]["evidenceManifestSha256"] = hashlib.sha256(path.read_bytes()).hexdigest()


def test_staging_requires_explicit_qualification_profile(candidate):
    staging(candidate)
    assert manifest.validate_candidate(candidate, now=NOW)
    assert manifest.validate_candidate(candidate, now=NOW, qualification_profile="m1-staging") == []


@pytest.mark.parametrize("mutation", ["production-label", "production-audits", "failed-audit", "one-build", "flashed"])
def test_staging_preserves_all_firmware_evidence_requirements(candidate, mutation):
    evidence = staging(candidate)
    if mutation == "production-label":
        evidence["profile"] = "production"
    elif mutation == "production-audits":
        evidence["config"]["productionConfigAudit"] = evidence["config"].pop("stagingConfigAudit")
        evidence["config"]["productionArtifactAudit"] = evidence["config"].pop("stagingArtifactAudit")
    elif mutation == "failed-audit":
        evidence["config"]["stagingConfigAudit"] = "FAIL"
    elif mutation == "one-build":
        evidence["reproducibility"]["independentCleanBuilds"] = 1
    else:
        evidence["safety"]["flashed"] = True
    write_evidence(candidate, evidence)
    assert manifest.validate_candidate(candidate, now=NOW, qualification_profile="m1-staging")


def test_production_cannot_be_reinterpreted_as_staging(candidate):
    assert manifest.validate_candidate(candidate, now=NOW, qualification_profile="m1-staging")
