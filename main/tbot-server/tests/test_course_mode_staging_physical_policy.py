"""Profile and protected-slot boundaries; no hardware or signatures fabricated as proof."""
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.test_course_mode_physical_flash_admission import admission, documents, NOW, valid_files


@pytest.fixture
def staged_documents(tmp_path, monkeypatch):
    doc, identity, _, actual = documents(tmp_path, Ed25519PrivateKey.generate())
    policy = {
        'firmwareSha': '7edf23ac4e09a745700396330b06fd26c929e05c',
        'appSha256': '5' * 64, 'appBytes': 3800000, 'manifestSha256': '6' * 64,
        'appOffset': '0x20000', 'partitionBytes': 4128768,
        'partitions': deepcopy(admission.EXPECTED_PARTITIONS),
    }
    policy['partitions'][6:7] = [
        {'name':'application', 'offset':'0x20000', 'size':'0x3f0000', 'end':'0x410000', 'protected':False},
        {'name':'inactive-application', 'offset':'0x410000', 'size':'0x3f0000', 'end':'0x800000', 'protected':True},
    ]
    # Synthetic pins exercise policy binding, not real artifact qualification.
    monkeypatch.setattr(admission, 'M1_STAGING_POLICY', policy, raising=False)
    binding = doc['candidate']
    binding['qualificationProfile'] = actual['qualificationProfile'] = 'm1-staging'
    binding['repositories']['firmware']['sha'] = policy['firmwareSha']
    actual['repositories']['firmware']['sha'] = policy['firmwareSha']
    binding['firmware']['gitSha'] = policy['firmwareSha']
    binding['firmware']['app'].update(sha256=policy['appSha256'], bytes=policy['appBytes'])
    binding['firmware']['manifest']['sha256'] = policy['manifestSha256']
    actual['firmware'].update(appSha256=policy['appSha256'], appBytes=policy['appBytes'], evidenceManifestSha256=policy['manifestSha256'])
    identity['candidate'] = deepcopy(binding)
    identity['partitionTable'] = deepcopy(policy['partitions'])
    doc['flashPlan']['operation'].update(imageSha256=policy['appSha256'], imageBytes=policy['appBytes'])
    doc['flashPlan']['protectedPartitions'] = [p for p in deepcopy(policy['partitions']) if p['protected']]
    doc['safety']['preserveInactiveApplication'] = True
    return doc, identity, actual


def validate(items, **kwargs):
    return admission.validate_documents(*items, NOW, [admission.SERIAL_PATH], [], None, **kwargs)


def test_staging_physical_policy_requires_explicit_profile(staged_documents):
    assert validate(staged_documents)
    assert validate(staged_documents, qualification_profile='m1-staging') == []


@pytest.mark.parametrize('mutation', ['missing-profile', 'actual-profile', 'firmware', 'app', 'manifest', 'inactive-slot', 'inactive-safety', 'other-safety'])
def test_staging_policy_preserves_exact_identity_and_safety(staged_documents, mutation):
    doc, identity, actual = staged_documents
    if mutation == 'missing-profile':
        doc['candidate'].pop('qualificationProfile')
        identity['candidate'].pop('qualificationProfile')
    elif mutation == 'actual-profile':
        actual.pop('qualificationProfile')
    elif mutation == 'firmware':
        doc['candidate']['firmware']['gitSha'] = admission.FIRMWARE_SHA
    elif mutation == 'app':
        doc['candidate']['firmware']['app']['sha256'] = admission.APP_SHA256
    elif mutation == 'manifest':
        doc['candidate']['firmware']['manifest']['sha256'] = admission.MANIFEST_SHA256
    elif mutation == 'inactive-slot':
        identity['partitionTable'][7]['protected'] = False
        doc['flashPlan']['protectedPartitions'] = [p for p in identity['partitionTable'] if p['protected']]
    elif mutation == 'inactive-safety':
        doc['safety']['preserveInactiveApplication'] = False
    else:
        doc['safety']['adultObserverPresent'] = False
    assert validate(staged_documents, qualification_profile='m1-staging')


def test_staging_cannot_accept_production_documents(tmp_path):
    doc, identity, _, actual = documents(tmp_path, Ed25519PrivateKey.generate())
    assert validate((doc, identity, actual), qualification_profile='m1-staging')


def test_signed_staging_cli_binds_profile_and_preserves_signature_gate(valid_files, monkeypatch, capsys):
    import hashlib
    import json
    from pathlib import Path
    from tests.test_course_mode_physical_flash_admission import canonical, resign, rewrite

    doc, identity, paths, actual = valid_files
    # Existing file fixture supplies synthetic immutable tools/artifacts. This
    # test exercises real signing, profile routing and publication around them.
    policy = admission.admission_policy('production')
    policy['partitions'] = admission.admission_policy('m1-staging')['partitions']
    monkeypatch.setattr(admission, 'M1_STAGING_POLICY', policy)
    doc['candidate']['qualificationProfile'] = actual['qualificationProfile'] = 'm1-staging'
    candidate_path = Path(doc['candidate']['path'])
    rewrite(candidate_path, actual)
    doc['candidate']['sha256'] = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    identity['candidate'] = deepcopy(doc['candidate'])
    identity['partitionTable'] = deepcopy(policy['partitions'])
    doc['flashPlan']['protectedPartitions'] = [p for p in deepcopy(policy['partitions']) if p['protected']]
    doc['safety']['preserveInactiveApplication'] = True
    resign(paths, doc, identity)
    calls = []

    def validate_candidate(value, profile, **kwargs):
        calls.append((value.get('qualificationProfile'), profile))
        return []

    monkeypatch.setattr(admission, '_validate_profile_candidate', validate_candidate)
    args = ['--input', str(paths['input']), '--output', str(paths['output']),
            '--expected-identity', str(paths['identity']),
            '--expected-identity-signature', str(paths['signature'])]
    assert admission.main(args) == 1
    assert not paths['output'].exists()
    capsys.readouterr()
    assert admission.main([*args, '--profile', 'm1-staging']) == 0
    receipt = json.loads(paths['output'].read_text())
    assert receipt['qualificationProfile'] == 'm1-staging'
    assert receipt['physicalActionsPerformed'] is False
    assert receipt['serialOpened'] is False
    assert ('m1-staging', 'm1-staging') in calls
    paths['output'].unlink()
    identity['candidate']['qualificationProfile'] = 'production'
    rewrite(paths['identity'], identity)
    assert admission.main([*args, '--profile', 'm1-staging']) == 1
    assert not paths['output'].exists()
    assert 'expectedIdentity.signature' in capsys.readouterr().out
