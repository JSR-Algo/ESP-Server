"""Profile and protected-slot boundaries; no hardware or signatures fabricated as proof."""
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.test_course_mode_physical_flash_admission import admission, documents, NOW, valid_files


@pytest.fixture
def staged_documents(tmp_path, monkeypatch):
    doc, identity, _, actual = documents(tmp_path, Ed25519PrivateKey.generate())
    policy = {
        'serialPath': '/dev/cu.usbmodem101',
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
    doc['robot']['serialPath'] = identity['robot']['serialPath'] = policy['serialPath']
    doc['serialLease'].update(devicePath=policy['serialPath'], discoveredDevices=[policy['serialPath']])
    return doc, identity, actual


def validate(items, **kwargs):
    return admission.validate_documents(*items, NOW, [items[0]['robot']['serialPath']], [], None, **kwargs)


def test_staging_serial_inventory_checks_selected_port(monkeypatch):
    calls = []
    monkeypatch.setattr(admission, '_enumerate_devices', lambda: {'/dev/cu.usbmodem101': (1, 2)})
    monkeypatch.setattr(admission, '_trusted_lsof', lambda: True)
    def lsof(command):
        calls.append(command)
        return 0, b'123\n', b'', None
    monkeypatch.setattr(admission, '_run_lsof', lsof)
    assert admission.collect_serial_inventory('/dev/cu.usbmodem101') == (['/dev/cu.usbmodem101'], [123], None)
    assert calls[0][-1] == '/dev/cu.usbmodem101'


def test_signed_staging_documents_are_scannable(staged_documents, monkeypatch):
    import hashlib
    from pathlib import Path
    from cryptography.hazmat.primitives import serialization
    import course_mode_software_evidence_audit as audit
    doc, identity, actual = staged_documents
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    fingerprint = hashlib.sha256(public).hexdigest()
    monkeypatch.setattr(admission, 'PINNED_APPROVAL_PUBLIC_KEY_RAW', public)
    monkeypatch.setattr(admission, 'PINNED_APPROVAL_KEY_FINGERPRINT', fingerprint)
    identity['signer']['fingerprint'] = fingerprint
    raw = admission._canonical_bytes(actual)
    doc['candidate']['sha256'] = hashlib.sha256(raw).hexdigest()
    identity['candidate'] = deepcopy(doc['candidate'])
    identity_raw = admission._canonical_bytes(identity)
    args = (actual, Path(doc['candidate']['path']), raw, admission._canonical_bytes(doc), identity_raw)
    assert audit._public_admission_scan_payloads(*args, key.sign(identity_raw)) is not None
    assert audit._public_admission_scan_payloads(*args, bytes(64)) is None
    doc['safety']['adultObserverPresent'] = False
    unsafe_args = (actual, Path(doc['candidate']['path']), raw, admission._canonical_bytes(doc), identity_raw)
    assert audit._public_admission_scan_payloads(*unsafe_args, key.sign(identity_raw)) is None


@pytest.mark.parametrize('devices,holders,error', [
    ([admission.SERIAL_PATH], [], None),
    (['/dev/cu.usbmodem101'], [123], None),
    (['/dev/cu.usbmodem101'], [], 'visibility'),
    (['/dev/cu.usbmodem101', admission.SERIAL_PATH], [], None),
])
def test_staging_inventory_fails_closed(staged_documents, devices, holders, error):
    assert admission.validate_documents(*staged_documents, NOW, devices, holders, error,
                                        qualification_profile='m1-staging')


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
    policy['serialPath'] = '/dev/cu.usbmodem101'
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
    doc['robot']['serialPath'] = identity['robot']['serialPath'] = policy['serialPath']
    doc['serialLease'].update(devicePath=policy['serialPath'], discoveredDevices=[policy['serialPath']])
    inventory_calls = []
    def inventory(serial_path=admission.SERIAL_PATH):
        inventory_calls.append(serial_path)
        return [serial_path], [], None
    monkeypatch.setattr(admission, 'collect_serial_inventory', inventory)
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
    inventory_calls.clear()
    assert admission.main([*args, '--profile', 'm1-staging']) == 0
    receipt = json.loads(paths['output'].read_text())
    assert receipt['qualificationProfile'] == 'm1-staging'
    assert receipt['physicalActionsPerformed'] is False
    assert receipt['serialOpened'] is False
    assert receipt['serialPath'] == policy['serialPath']
    assert len(inventory_calls) >= 3
    assert set(inventory_calls) == {policy['serialPath']}
    assert ('m1-staging', 'm1-staging') in calls
    paths['output'].unlink()
    identity['candidate']['qualificationProfile'] = 'production'
    rewrite(paths['identity'], identity)
    assert admission.main([*args, '--profile', 'm1-staging']) == 1
    assert not paths['output'].exists()
    assert 'expectedIdentity.signature' in capsys.readouterr().out
