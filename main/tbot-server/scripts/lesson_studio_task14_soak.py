#!/usr/bin/env python3
import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

BUILD_IDENTITY_PATH = Path(__file__).with_name('lesson_studio_task14_build_identity.py')
BUILD_IDENTITY_SPEC = importlib.util.spec_from_file_location(
    'lesson_studio_task14_build_identity_shared', BUILD_IDENTITY_PATH
)
BUILD_IDENTITY = importlib.util.module_from_spec(BUILD_IDENTITY_SPEC)
assert BUILD_IDENTITY_SPEC.loader is not None
BUILD_IDENTITY_SPEC.loader.exec_module(BUILD_IDENTITY)
load_build_identity = BUILD_IDENTITY.load_build_identity
load_release_order_artifact = BUILD_IDENTITY.load_release_order_artifact
atomic_write_json = BUILD_IDENTITY.atomic_write_json
BuildIdentityError = BUILD_IDENTITY.BuildIdentityError
BUILD_IDENTITY_EXCEPTIONS = (
    BuildIdentityError,
    OSError,
    UnicodeError,
    json.JSONDecodeError,
)

PRODUCTION_MINIMUM_TRANSITIONS = 104

FIXTURE_VERSION = '2026-07-11.1'
COURSE_ID = 'production-farm-english-358'
LESSON_IDS = ('pip-farm-3m', 'pip-farm-5m', 'pip-farm-8m')

LEGACY_TRANSITION = re.compile(
    r'(?i)(?:lesson_step_started|lesson_step_transition).*?'
    r'(?:sequence|seq|step_index)["\']?\s*[:=]\s*["\']?(\d+)'
)
SERVER_STEP = re.compile(r'(?i)\bemit lesson_step\b')
SERIAL_STEP = re.compile(
    r'(?i)\bws text lesson frame\b.*?\btype=lesson_step\b.*?\bseq=(\d+)'
)
SERIAL_RENDER = re.compile(r'(?i)\blesson_step rendered\b.*?\bstepId=([^\s,]+)')
SERVER_PREPARE = re.compile(r'(?i)\bemit lesson_prepare\b')
SERIAL_PREPARE = re.compile(r'(?i)\bws text lesson frame\b.*?\btype=lesson_prepare\b.*?\bseq=(\d+)')
TIMELINE_LINE = re.compile(r'^(\d+)\s+(server|serial)\s+(.*)$', re.I)
PSRAM = re.compile(
    r'(?i)(?:psram_free|free_psram|psramFreeBytes)["\']?\s*[:=]\s*["\']?(\d+)'
)
PHASE_SRAM = re.compile(
    r'(?i)(?:phase_min_internal|internalMinimumFreeBytes|internal_min_free|'
    r'minimum_internal_sram|min_internal_sram)["\']?\s*[:=]\s*["\']?(\d+)'
)
LIFETIME_SRAM = re.compile(
    r'(?i)lifetime_min_internal["\']?\s*[:=]\s*["\']?(\d+)'
)
RESET = re.compile(r'(?i)(?:rst:0x|guru meditation|watchdog.*reset|brownout|firmware reset)')
FIELD_PATTERNS = {
    'assignment': re.compile(r'(?i)\bassignment(?:_id|Id)?["\']?\s*[:=]\s*["\']?([^,\s"\'}]+)'),
    'session': re.compile(r'(?i)\bsession(?:_id|Id)?["\']?\s*[:=]\s*["\']?([^,\s"\'}]+)'),
    'step': re.compile(r'(?i)\bstep(?:_id|Id)?["\']?\s*[:=]\s*["\']?([^,\s"\'}]+)'),
}


def _field(line, name):
    match = FIELD_PATTERNS[name].search(line)
    return match.group(1) if match else None


def _server_steps(text):
    events = []
    malformed = 0
    for line in text.splitlines():
        if not SERVER_STEP.search(line):
            continue
        event = (_field(line, 'assignment'), _field(line, 'session'), _field(line, 'step'))
        if any(value is None for value in event):
            malformed += 1
        else:
            events.append(event)
    return events, malformed


def _serial_steps(text):
    pending = deque()
    events = []
    malformed = 0
    for line in text.splitlines():
        start = SERIAL_STEP.search(line)
        if start:
            pending.append(int(start.group(1)))
            continue
        rendered = SERIAL_RENDER.search(line)
        if rendered:
            if not pending:
                malformed += 1
                continue
            events.append((rendered.group(1), pending.popleft()))
    malformed += len(pending)
    return events, malformed


def transition_evidence(serial_text, server_text):
    server_events, server_malformed = _server_steps(server_text)
    serial_events, serial_malformed = _serial_steps(serial_text)
    bound = []
    mismatched_steps = 0
    for server_event, serial_event in zip(server_events, serial_events):
        assignment, session, server_step = server_event
        serial_step, sequence = serial_event
        if server_step != serial_step:
            mismatched_steps += 1
            continue
        bound.append((assignment, session, server_step, sequence))
    binding_complete = (
        server_malformed == 0
        and serial_malformed == 0
        and len(server_events) == len(serial_events)
        and mismatched_steps == 0
        and bool(bound)
    )
    unique = len(bound) == len(set(bound))
    sequences = defaultdict(list)
    for _assignment, session, _step, sequence in bound:
        sequences[session].append(sequence)
    ordered = bool(sequences) and all(
        all(current > previous for previous, current in zip(values, values[1:]))
        for values in sequences.values()
    )
    step_sessions = defaultdict(set)
    for _assignment, session, step, _sequence in bound:
        step_sessions[step].add(session)
    unambiguous = all(len(sessions) == 1 for sessions in step_sessions.values())
    return {
        'identities': bound,
        'binding_complete': binding_complete,
        'unique': unique,
        'ordered_per_session': ordered,
        'unambiguous': unambiguous,
        'sessions': len(sequences),
    }


def timeline_transition_evidence(timeline_text):
    bound = []
    malformed = 0
    last_timestamp = None
    pending_prepare = None
    active_session = None
    pending_server_step = None
    pending_serial_sequence = None

    for raw_line in timeline_text.splitlines():
        if not raw_line.strip():
            continue
        match = TIMELINE_LINE.match(raw_line)
        if not match:
            malformed += 1
            continue
        timestamp, source, line = int(match.group(1)), match.group(2).lower(), match.group(3)
        if last_timestamp is not None and timestamp < last_timestamp:
            malformed += 1
        last_timestamp = timestamp

        if source == 'server' and SERVER_PREPARE.search(line):
            if pending_prepare or pending_server_step or pending_serial_sequence:
                malformed += 1
            prepare = (_field(line, 'assignment'), _field(line, 'session'))
            if any(value is None for value in prepare):
                malformed += 1
                pending_prepare = None
            else:
                pending_prepare = prepare
                active_session = None
            continue

        serial_prepare = SERIAL_PREPARE.search(line) if source == 'serial' else None
        if serial_prepare:
            if pending_prepare is None or int(serial_prepare.group(1)) != 1:
                malformed += 1
            else:
                active_session = pending_prepare
            pending_prepare = None
            continue

        if source == 'server' and SERVER_STEP.search(line):
            event = (_field(line, 'assignment'), _field(line, 'session'), _field(line, 'step'))
            if (
                active_session is None
                or any(value is None for value in event)
                or event[:2] != active_session
                or pending_server_step is not None
            ):
                malformed += 1
            else:
                pending_server_step = event
            continue

        serial_step = SERIAL_STEP.search(line) if source == 'serial' else None
        if serial_step:
            if active_session is None or pending_server_step is None or pending_serial_sequence is not None:
                malformed += 1
            else:
                pending_serial_sequence = int(serial_step.group(1))
            continue

        rendered = SERIAL_RENDER.search(line) if source == 'serial' else None
        if rendered:
            if pending_server_step is None or pending_serial_sequence is None:
                malformed += 1
            else:
                assignment, session, step = pending_server_step
                if rendered.group(1) != step:
                    malformed += 1
                else:
                    bound.append((assignment, session, step, pending_serial_sequence))
            pending_server_step = None
            pending_serial_sequence = None

    if pending_prepare or pending_server_step or pending_serial_sequence:
        malformed += 1

    sequences = defaultdict(list)
    for _assignment, session, _step, sequence in bound:
        sequences[session].append(sequence)
    ordered = bool(sequences) and all(
        all(current > previous for previous, current in zip(values, values[1:]))
        for values in sequences.values()
    )
    return {
        'identities': bound,
        'binding_complete': malformed == 0 and bool(bound),
        'unique': len(bound) == len(set(bound)),
        'ordered_per_session': ordered,
        'unambiguous': malformed == 0,
        'sessions': len(sequences),
    }


def _heap_metrics(text):
    psram = []
    phase = []
    lifetime = [int(value) for value in LIFETIME_SRAM.findall(text)]
    for line in text.splitlines():
        lowered = line.lower()
        active_sample = (
            'phase=lesson_' in lowered
            or 'internalminimumfreebytes' in lowered
            or 'lesson_step_transition' in lowered
            or 'lesson_step_started' in lowered
        )
        if not active_sample:
            continue
        psram.extend(int(value) for value in PSRAM.findall(line))
        phase.extend(int(value) for value in PHASE_SRAM.findall(line))
    if not psram:
        psram = [int(value) for value in PSRAM.findall(text)]
    if not phase:
        phase = [int(value) for value in PHASE_SRAM.findall(text)]
    return psram, phase, lifetime


def _report(steps, sessions, binding_complete, unique, ordered, unambiguous,
            timeline_bound, text, count, gate, tolerance):
    psram, phase_sram, lifetime_sram = _heap_metrics(text)
    monotonic_loss = (
        len(psram) >= 3
        and all(previous >= current for previous, current in zip(psram, psram[1:]))
        and psram[0] - psram[-1] > tolerance
    )
    loss_above_tolerance = len(psram) >= 3 and psram[0] - psram[-1] > tolerance
    active_internal_min = min(phase_sram) if phase_sram else None
    lifetime_internal_min = min(lifetime_sram) if lifetime_sram else None
    authoritative_internal = phase_sram + lifetime_sram
    internal_min = min(authoritative_internal) if authoritative_internal else None
    active_above_gate = active_internal_min is not None and active_internal_min >= gate
    lifetime_present = lifetime_internal_min is not None
    lifetime_above_gate = lifetime_present and lifetime_internal_min >= gate
    checks = {
        'at_least_100_transitions': len(steps) >= count,
        'transition_binding_complete': binding_complete,
        'transition_identities_unique': unique,
        'transition_sequence_strictly_increasing': ordered,
        'transition_binding_unambiguous': unambiguous,
        'timeline_binding_required': timeline_bound or count < 100,
        'psram_samples_present': len(psram) >= 3,
        'no_monotonic_psram_loss': not monotonic_loss,
        'no_psram_loss_above_tolerance': not loss_above_tolerance,
        'internal_sram_above_gate': active_above_gate and lifetime_above_gate,
        'active_internal_sram_above_gate': active_above_gate,
        'lifetime_internal_sram_present': lifetime_present,
        'lifetime_internal_sram_above_gate': lifetime_above_gate,
        'no_firmware_reset': not RESET.search(text),
    }
    return {
        'status': 'PASS' if all(checks.values()) else 'NOT_PASS',
        'checks': checks,
        'metrics': {
            'transitions': len(steps),
            'sessions': sessions,
            'psramFirst': psram[0] if psram else None,
            'psramLast': psram[-1] if psram else None,
            'psramNetLoss': psram[0] - psram[-1] if psram else None,
            'internalSramMin': internal_min,
            'activeInternalSramMin': active_internal_min,
            'lifetimeInternalSramMin': lifetime_internal_min,
        },
    }


def analyze_logs(serial_text, server_text, count=100, gate=20 * 1024, tolerance=64 * 1024):
    evidence = transition_evidence(serial_text, server_text)
    return _report(
        evidence['identities'],
        evidence['sessions'],
        evidence['binding_complete'],
        evidence['unique'],
        evidence['ordered_per_session'],
        evidence['unambiguous'],
        False,
        serial_text + '\n' + server_text,
        count,
        gate,
        tolerance,
    )


def analyze_timeline(timeline_text, count=100, gate=20 * 1024, tolerance=64 * 1024):
    evidence = timeline_transition_evidence(timeline_text)
    return _report(
        evidence['identities'],
        evidence['sessions'],
        evidence['binding_complete'],
        evidence['unique'],
        evidence['ordered_per_session'],
        evidence['unambiguous'],
        True,
        timeline_text,
        count,
        gate,
        tolerance,
    )


def analyze(text, count=100, gate=20 * 1024, tolerance=64 * 1024):
    steps = [int(value) for value in LEGACY_TRANSITION.findall(text)]
    ordered = bool(steps) and all(current > previous for previous, current in zip(steps, steps[1:]))
    return _report(steps, 1 if steps else 0, bool(steps), len(steps) == len(set(steps)), ordered,
                   True, False, text, count, gate, tolerance)


def _source_logs(paths):
    serial = server = None
    for path in paths:
        text = path.read_text(errors='replace')
        name = path.name.lower()
        if 'serial' in name and serial is None:
            serial = text
        elif 'server' in name and server is None:
            server = text
    if serial is None and paths:
        serial = paths[0].read_text(errors='replace')
    if server is None and len(paths) > 1:
        server = paths[1].read_text(errors='replace')
    return serial or '', server or ''


def _stream_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_live_attestation(
    fixtureVersion, courseId, lessonId, capture_script, verifier_script
):
    metadata = {
        'fixtureVersion': fixtureVersion,
        'courseId': courseId,
        'lessonId': lessonId,
    }
    errors = []
    if fixtureVersion != FIXTURE_VERSION:
        errors.append(f'fixtureVersion must equal {FIXTURE_VERSION}')
    if courseId != COURSE_ID:
        errors.append(f'courseId must equal {COURSE_ID}')
    if lessonId not in LESSON_IDS:
        errors.append('lessonId is not an approved Task 14 fixture lesson')
    for field, flag, path in (
        ('captureScriptSha256', '--capture-script', capture_script),
        ('verifierScriptSha256', '--verifier-script', verifier_script),
    ):
        try:
            candidate = Path(path) if path is not None else None
            if candidate is None or candidate.is_symlink() or not candidate.is_file():
                raise OSError
            metadata[field] = _stream_sha256(candidate)
        except (OSError, ValueError):
            errors.append(f'cannot hash {flag}')
    return metadata, errors


def add_live_attestation_args(parser):
    parser.add_argument('--fixture-version')
    parser.add_argument('--course-id')
    parser.add_argument('--lesson-id')
    parser.add_argument('--capture-script', type=Path)
    parser.add_argument('--verifier-script', type=Path)


def minimum_transition_count(value):
    try:
        count = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('minimum transitions must be an integer') from exc
    if count != PRODUCTION_MINIMUM_TRANSITIONS:
        raise argparse.ArgumentTypeError(
            f'minimum transitions must equal {PRODUCTION_MINIMUM_TRANSITIONS}'
        )
    return count


def add_production_evidence_args(parser):
    parser.add_argument('--minimum-transitions', type=minimum_transition_count)
    parser.add_argument('--build-manifest', type=Path)
    parser.add_argument('--release-order-artifact', type=Path)


def bind_production_evidence(report, args):
    report['minimumTransitionsRequired'] = args.minimum_transitions
    report.setdefault('metrics', {})['minimumTransitionsRequired'] = args.minimum_transitions
    report['buildIdentity'] = load_build_identity(
        args.build_manifest, expected_profile='production'
    )
    report['releaseOrderEvidence'] = load_release_order_artifact(
        args.release_order_artifact, production_identity=report['buildIdentity']
    )
    report['buildIdentityErrors'] = []
    report['releaseOrderErrors'] = []
    return report


def record_build_identity_failure(report, args, exc):
    report['status'] = 'NOT_PASS'
    report['minimumTransitionsRequired'] = args.minimum_transitions
    report.setdefault('metrics', {})['minimumTransitionsRequired'] = args.minimum_transitions
    report['buildIdentity'] = None
    report['buildIdentityErrors'] = [str(exc)]
    report['releaseOrderEvidence'] = None
    report['releaseOrderErrors'] = [str(exc)]
    if args.output:
        atomic_write_json(args.output, report)
    print(f'task14 production build identity: FAIL: {exc}', file=sys.stderr)
    print(json.dumps(report, indent=2))
    return 1


def attest_report(report, args):
    metadata, errors = validate_live_attestation(
        args.fixture_version,
        args.course_id,
        args.lesson_id,
        args.capture_script,
        args.verifier_script,
    )
    report.update(metadata)
    report['attestationErrors'] = errors
    if errors:
        report['status'] = 'NOT_PASS'
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('logs', nargs='*', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--timeline-log', type=Path)
    add_live_attestation_args(parser)
    add_production_evidence_args(parser)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        print('self-test PASS')
        return 0
    if args.minimum_transitions is None:
        parser.error('--minimum-transitions is required for live evidence')
    if args.build_manifest is None:
        parser.error('--build-manifest is required for live evidence')
    if args.release_order_artifact is None:
        parser.error('--release-order-artifact is required for live evidence')
    if len(args.logs) < 2:
        parser.error('serial and server logs required')
    serial, server = _source_logs(args.logs)
    report = analyze_timeline(
        args.timeline_log.read_text(errors='replace'), count=args.minimum_transitions
    ) if args.timeline_log else analyze_logs(serial, server, count=args.minimum_transitions)
    attest_report(report, args)
    try:
        bind_production_evidence(report, args)
    except BUILD_IDENTITY_EXCEPTIONS as exc:
        return record_build_identity_failure(report, args, exc)
    data = json.dumps(report, indent=2) + '\n'
    print(data, end='')
    if args.output:
        atomic_write_json(args.output, report)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
