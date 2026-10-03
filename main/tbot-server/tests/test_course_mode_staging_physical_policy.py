"""Profile and protected-slot boundaries; no hardware or signatures fabricated as proof."""
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.test_course_mode_physical_flash_admission import admission, documents, NOW, valid_files


def current_staging_documents(tmp_path):
    """Reviewed build-b identity; these test documents do not attest hardware safety."""
    doc, identity, _, actual = documents(tmp_path, Ed25519PrivateKey.generate())
    binding = doc['candidate']
    binding['qualificationProfile'] = actual['qualificationProfile'] = 'm1-staging'
    firmware_sha = '283f88e55e918e9a7f7337f66bb04c82a246db35'
    app_sha = '7f348063189a9bf884215ffa2c450da3acb5fb32fb2697d30c4b9dd4e938ee41'
    manifest_sha = '87d503c0ca4f2d1c821c9e27e62dacb3b6a8dd6176946ae001ea9e5566cac7b0'
    binding['repositories']['firmware']['sha'] = actual['repositories']['firmware']['sha'] = firmware_sha
    binding['firmware']['gitSha'] = firmware_sha
    binding['firmware']['app'].update(sha256=app_sha, bytes=3846880)
    binding['firmware']['manifest']['sha256'] = manifest_sha
    actual['firmware'].update(appSha256=app_sha, appBytes=3846880, evidenceManifestSha256=manifest_sha)
    identity['candidate'] = deepcopy(binding)
    partitions = deepcopy(admission.EXPECTED_PARTITIONS)
    partitions[6:7] = [
        {'name':'application', 'offset':'0x20000', 'size':'0x3f0000', 'end':'0x410000', 'protected':False},
        {'name':'inactive-application', 'offset':'0x410000', 'size':'0x3f0000', 'end':'0x800000', 'protected':True},
    ]
    identity['partitionTable'] = partitions
    doc['flashPlan']['protectedPartitions'] = [p for p in partitions if p['protected']]
    doc['flashPlan']['operation'].update(imageSha256=app_sha, imageBytes=3846880)
    doc['safety']['preserveInactiveApplication'] = True
    return doc, identity, actual


def test_current_verified_staging_build_and_port_match_unmodified_policy(tmp_path):
    assert validate(current_staging_documents(tmp_path), qualification_profile='m1-staging') == []


@pytest.mark.parametrize('mutation', ['old-app', 'old-source', 'old-manifest', 'old-port', 'unsafe', 'inactive-slot', 'production'])
def test_current_staging_binding_keeps_identity_and_safety_fail_closed(tmp_path, mutation):
    items = current_staging_documents(tmp_path)
    doc, identity, actual = items
    if mutation == 'old-app':
        old = '6cdf24124d3c7469d1c2c3644300cff64f5b1a99cb305712c93e33d19c31ac0e'
        doc['candidate']['firmware']['app']['sha256'] = actual['firmware']['appSha256'] = old
        doc['flashPlan']['operation']['imageSha256'] = old
    elif mutation == 'old-source':
        old = '7edf23ac4e09a745700396330b06fd26c929e05c'
        doc['candidate']['firmware']['gitSha'] = old
        doc['candidate']['repositories']['firmware']['sha'] = actual['repositories']['firmware']['sha'] = old
    elif mutation == 'old-manifest':
        old = 'ac798559639e9e6beb2183958af6ca65b9ca36c132e8e69499fc3424fca6ebe2'
        doc['candidate']['firmware']['manifest']['sha256'] = actual['firmware']['evidenceManifestSha256'] = old
    elif mutation == 'old-port':
        doc['robot']['serialPath'] = identity['robot']['serialPath'] = '/dev/cu.usbmodem101'
        doc['serialLease'].update(devicePath='/dev/cu.usbmodem101', discoveredDevices=['/dev/cu.usbmodem101'])
    elif mutation == 'unsafe':
        doc['safety']['adultObserverPresent'] = False
    elif mutation == 'inactive-slot':
        doc['safety']['preserveInactiveApplication'] = False
    else:
        assert validate(items, qualification_profile='production')
        return
    identity['candidate'] = deepcopy(doc['candidate'])
    reasons = validate(items, qualification_profile='m1-staging')
    expected = {'old-app': 'candidate.firmware.app', 'old-source': 'candidate.firmware',
                'old-manifest': 'candidate.firmware.manifest', 'old-port': 'robot.identity'}
    assert reasons
    if mutation in expected:
        assert expected[mutation] in reasons
        assert 'candidate.identity' not in reasons
        assert 'candidate.reference' not in reasons


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
