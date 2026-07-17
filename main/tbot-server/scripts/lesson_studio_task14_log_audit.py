#!/usr/bin/env python3
import argparse
import importlib.util
import json
import re
from collections import Counter
from pathlib import Path

SOAK_PATH = Path(__file__).with_name('lesson_studio_task14_soak.py')
SOAK_SPEC = importlib.util.spec_from_file_location('lesson_studio_task14_soak_shared', SOAK_PATH)
SOAK = importlib.util.module_from_spec(SOAK_SPEC)
assert SOAK_SPEC.loader is not None
SOAK_SPEC.loader.exec_module(SOAK)
validate_live_attestation = SOAK.validate_live_attestation
minimum_transition_count = SOAK.minimum_transition_count
load_build_identity = SOAK.load_build_identity
load_release_ledger = SOAK.load_release_ledger

MARKERS = {
    'allocationFailure': r'alloc(?:ation)? failed|failed to alloc(?:ate|ation)|out of memory|malloc failed',
    'watchdog': r'watchdog|task wdt',
    'decodeFailure': r'decode failed|failed to decode|image decode error|jpeg (?:validation|decode) failed|decoded jpeg rejected',
    'audioUnderrun': r'audio underrun|audio underflow|i2s.*underrun',
    'sequenceDivergence': r'lesson sequence divergence|sequence mismatch|sequence gap:\s*got',
    'firmwareCrash': r'assert failed|stack overflow|corrupt heap|abort\(\) was called|loadprohibited|storeprohibited|illegalinstruction',
}
EVENT = re.compile(r'(?i)(lesson_progress(?:_[a-z]+)?|progress_posted(?:_[a-z]+)?)')
SESSION = re.compile(r'(?i)session(?:[_ ]?id)?["\']?\s*[:=]\s*["\']?([^,\s"\'}]+)')
STEP = re.compile(r'(?i)step(?:[_ ]?id)?["\']?\s*[:=]\s*["\']?([^,\s"\'}]+)')
SEQUENCE = re.compile(r'(?i)(?:sequence|seq)["\']?\s*[:=]\s*["\']?([^,\s"\'}]+)')
IDEMPOTENCY = re.compile(r'(?i)(?:idempotency[_ ]?key|event[_ ]?id)["\']?\s*[:=]\s*["\']?([^,\s"\'}]+)')
SEMANTIC_EVENT = re.compile(r'(?i)\bevent["\']?\s*[:=]\s*["\']?([^,\s"\'}]+)')
DROP_SAMPLE = re.compile(r'(?i)\b(?:decode_drop|encode_drop)["\']?\s*[:=]\s*["\']?(\d+)')


def progress_events(text):
    events = []
    for line in text.splitlines():
        event_match = EVENT.search(line)
        if not event_match:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        body = payload.get('body') if isinstance(payload.get('body'), dict) else {}
        semantic_event = body.get('event') or payload.get('event')
        semantic_match = SEMANTIC_EVENT.search(line)
        semantic_event = semantic_event or (semantic_match.group(1) if semantic_match else None)
        event_type = str(semantic_event or payload.get('type') or event_match.group(1)).lower()
        session = payload.get('session_id') or payload.get('sessionId') or payload.get('session')
        step = payload.get('step_id') or payload.get('stepId') or payload.get('step')
        sequence = payload.get('sequence') if payload.get('sequence') is not None else payload.get('seq')
        idempotency = payload.get('idempotency_key') or payload.get('idempotencyKey') or payload.get('event_id')
        session_match = SESSION.search(line)
        step_match = STEP.search(line)
        session = session or (session_match.group(1) if session_match else None)
        step = step or (step_match.group(1) if step_match else None)
        sequence_match = SEQUENCE.search(line)
        idempotency_match = IDEMPOTENCY.search(line)
        sequence = sequence if sequence is not None else (sequence_match.group(1) if sequence_match else None)
        idempotency = idempotency or (idempotency_match.group(1) if idempotency_match else None)
        identity = f'sequence:{sequence}' if sequence is not None else f'idempotency:{idempotency}' if idempotency else 'identity:missing'
        if session and step:
            events.append((event_type, str(session), str(step), identity))
    return events


def _drop_failure(text):
    samples = [int(value) for value in DROP_SAMPLE.findall(text)]
    return bool(samples) and (samples[0] > 0 or any(current > previous for previous, current in zip(samples, samples[1:])))


def audit(text):
    findings = {name: len(re.findall(pattern, text, re.I)) for name, pattern in MARKERS.items()}
    if _drop_failure(text):
        findings['audioUnderrun'] += 1
    events = progress_events(text)
    duplicates = sorted(event for event, count in Counter(events).items() if count > 1)
    ok = not any(findings.values()) and not duplicates
    return {
        'status': 'PASS' if ok else 'NOT_PASS',
        'findings': findings,
        'duplicateProgress': [
            {'eventType': event[0], 'session': event[1], 'step': event[2], 'identity': event[3]}
            for event in duplicates
        ],
    }


def audit_logs(serial_text, server_text, minimum_transitions=100, timeline_text=None):
    combined = serial_text + '\n' + server_text
    report = audit(combined)
    transitions = (
        SOAK.timeline_transition_evidence(timeline_text)
        if timeline_text is not None
        else SOAK.transition_evidence(serial_text, server_text)
    )
    checks = {
        'serial_log_present': bool(serial_text.strip()),
        'server_log_present': bool(server_text.strip()),
        'at_least_100_bound_transitions': len(transitions['identities']) >= minimum_transitions,
        'transition_binding_complete': transitions['binding_complete'],
        'transition_identities_unique': transitions['unique'],
        'transition_sequence_strictly_increasing': transitions['ordered_per_session'],
        'transition_binding_unambiguous': transitions['unambiguous'],
        'timeline_binding_required': timeline_text is not None or minimum_transitions < 100,
    }
    report['checks'] = checks
    report['metrics'] = {
        'transitions': len(transitions['identities']),
        'sessions': transitions['sessions'],
    }
    if not all(checks.values()):
        report['status'] = 'NOT_PASS'
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('logs', nargs='*', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--timeline-log', type=Path)
    SOAK.add_live_attestation_args(parser)
    SOAK.add_production_evidence_args(parser)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        print('self-test PASS')
        return 0
    if args.minimum_transitions is None:
        parser.error('--minimum-transitions is required for live evidence')
    if args.build_manifest is None:
        parser.error('--build-manifest is required for live evidence')
    if args.release_ledger is None:
        parser.error('--release-ledger is required for live evidence')
    if len(args.logs) < 2:
        parser.error('serial and server logs required')
    serial, server = SOAK._source_logs(args.logs)
    timeline = args.timeline_log.read_text(errors='replace') if args.timeline_log else None
    report = audit_logs(
        serial,
        server,
        minimum_transitions=args.minimum_transitions,
        timeline_text=timeline,
    )
    SOAK.attest_report(report, args)
    report['minimumTransitionsRequired'] = args.minimum_transitions
    report.setdefault('metrics', {})['minimumTransitionsRequired'] = args.minimum_transitions
    try:
        report['buildIdentity'] = load_build_identity(
            args.build_manifest, expected_profile='production'
        )
        report['releaseLedgerEvidence'] = load_release_ledger(
            args.release_ledger,
            production_identity=report['buildIdentity'],
            required_event='production-soak',
        )
        report['buildIdentityErrors'] = []
        report['releaseLedgerErrors'] = []
    except SOAK.BUILD_IDENTITY_EXCEPTIONS as exc:
        return SOAK.record_build_identity_failure(report, args, exc)
    data = json.dumps(report, indent=2) + '\n'
    print(data, end='')
    if args.output:
        SOAK.atomic_write_json(args.output, report)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
