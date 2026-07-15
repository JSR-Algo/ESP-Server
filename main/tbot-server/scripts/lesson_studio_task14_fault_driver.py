#!/usr/bin/env python3
"""Fail-closed evidence collector; it never injects a fault or contacts production."""
import argparse
import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from PIL import Image, UnidentifiedImageError

SCENARIOS=('preview-parity','cold','warm','offline','checksum','interrupted','power-loss','missing-optional','sd-full','slave-unavailable','rollback')
REQUIRED=('serial.log','server.log','command.txt','result.json')
COLD_REQUIRED=(
    'eviction-response.json','eviction-response.sha256','utc-start.txt',
    'eviction-completed-utc.txt','cold-capture-started-utc.txt',
    'assignment-create-response.json','assignment-create-response.sha256',
)
SHA256=re.compile(r'^[0-9a-f]{64}$')
COMMIT=re.compile(r'^[0-9a-f]{7,40}$')
MAX_SCREENSHOT_BYTES=10*1024*1024
FIXTURE_VERSION='2026-07-11.1'
COURSE_ID='production-farm-english-358'
LESSON_IDS=('pip-farm-3m','pip-farm-5m','pip-farm-8m')
ASSIGNMENT_RESPONSE_FIELDS={
    'assignmentId','assignmentVersion','deviceId','childId','lessonId',
    'lessonTitle','lessonVersion','manifestChecksum','profile','state','createdAt',
}
SECRET_FIELD_NAMES={'authorization','token','accesstoken','refreshtoken'}
JWT_VALUE=re.compile(r'(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])')
COMMON_FIELDS=(
    'utcStart','utcEnd','backendCommit','espServerCommit','firmwareCommit',
    'firmwareVersion','deviceId','assignmentId','sessionId','assignmentVersion',
    'fixtureVersion','courseId','lessonId','lessonVersion','manifestChecksum',
    'packChecksum','cacheKey','captureScriptSha256','verifierScriptSha256',
    'internalSramMin','psramFirst','psramLast','screenshots','operator',
    'commandExitCode','logMarkers',
)
SCENARIO_LOG_MARKERS={
    'preview-parity':('lesson_step_started','motion_preset'),
    'cold':('lesson_cache_evict','lesson_preload_ready','checksum_verified'),
    'warm':('asset_cache_hit',),
    'offline':('offline_replay','sd://'),
    'checksum':('checksum_mismatch','partial_cleaned'),
    'interrupted':('download_interrupted','partial_cleaned'),
    'power-loss':('power_loss_recovery','partial_cleaned'),
    'missing-optional':('optional_asset_missing','render_degraded'),
    'sd-full':('sd_full_refused','previous_pack_retained'),
    'slave-unavailable':('motion_degraded',),
    'rollback':('rollback_activated','old_files_reattested'),
}

def _raw_field(line,name):
    match=re.search(
        rf'(?<![A-Za-z0-9_]){re.escape(name)}["\']?\s*[:=]\s*["\']?([^,\s"\'}}]+)',
        line,
        re.I,
    )
    return match.group(1) if match else None

def _line_has_fields(line,fields):
    return all(_raw_field(line,name)==str(value) for name,value in fields.items())

def _positive_int_field(line,name):
    value=_raw_field(line,name)
    try:return int(value) if value is not None and int(value)>0 else None
    except ValueError:return None

def _zero_int_field(line,name):
    value=_raw_field(line,name)
    try:return value is not None and int(value)==0
    except ValueError:return False

def _identity_bound(lines,result):
    fields={
        'assignmentId':result['assignmentId'],
        'lessonId':result['lessonId'],
        'deviceId':result['deviceId'],
    }
    return any(_line_has_fields(line,fields) for line in lines)

def _scenario_raw_evidence_bound(scenario,result,raw_logs):
    if scenario not in ('cold','warm','checksum'):
        return True
    lines=raw_logs.splitlines()
    if not _identity_bound(lines,result):
        return False
    scoped={
        'cacheKey':result['cacheKey'],
        'assignment_id':result['assignmentId'],
        'session_id':result['sessionId'],
    }
    if scenario == 'cold':
        preload=next((line for line in lines if 'lesson_preload_ready' in line.lower() and _line_has_fields(line,scoped)),None)
        checksum_fields={**scoped,'manifestChecksum':result['manifestChecksum']}
        checksum=next((line for line in lines if 'checksum_verified' in line.lower() and _line_has_fields(line,checksum_fields)),None)
        return bool(
            preload
            and checksum
            and _positive_int_field(preload,'downloadedCount')
            and _zero_int_field(preload,'failedCount')
            and _raw_field(preload,'durationMs')==str(result.get('elapsedMs'))
        )
    if scenario == 'warm':
        cache_hit=next((line for line in lines if 'asset_cache_hit' in line.lower() and _line_has_fields(line,scoped)),None)
        return bool(
            cache_hit
            and _zero_int_field(cache_hit,'downloadedCount')
            and _zero_int_field(cache_hit,'failedCount')
            and _raw_field(cache_hit,'durationMs')==str(result.get('elapsedMs'))
        )
    mismatch_fields={**scoped,'manifestChecksum':result['manifestChecksum']}
    mismatch=next((line for line in lines if 'checksum_mismatch' in line.lower() and _line_has_fields(line,mismatch_fields)),None)
    cleanup_scope={
        'cacheKey':result['cacheKey'],
        'manifestChecksum':result['manifestChecksum'],
        'assignment_id':result['assignmentId'],
        'session_id':result['sessionId'],
    }
    cleanup=any('partial_cleaned' in line.lower() and _line_has_fields(line,cleanup_scope) for line in lines)
    return bool(
        mismatch
        and cleanup
        and _raw_field(mismatch,'mismatchDetected')=='true'
        and _raw_field(mismatch,'partialCleaned')=='true'
        and _raw_field(mismatch,'ready')=='false'
    )

def scenario_valid(s,r):
    try:
        checks={
          'preview-parity': r.get('previewLayerRects')==r.get('hardwareLayerRects') and bool(r.get('previewLayerRects')) and r.get('previewWordText')==r.get('hardwareWordText') and r.get('previewPathOutcome')==r.get('hardwarePathOutcome') and r.get('previewMotionTimeline')==r.get('hardwareMotionTimeline'),
          'cold': r.get('bytesDownloaded',0)>0 and r.get('elapsedMs',0)>0 and r.get('ready') is True and r.get('checksumVerified') is True and r.get('manifestChecksum')==r.get('packChecksum') and bool(r.get('manifestChecksum')),
          'warm': r.get('cacheHit') is True and r.get('bytesDownloaded')==0 and r.get('elapsedMs',0)>0 and r.get('ready') is True and r.get('manifestChecksum')==r.get('packChecksum') and bool(r.get('manifestChecksum')),
          'offline': r.get('networkAvailable') is False and r.get('completed') is True and r.get('source')=='sd',
          'checksum': r.get('mismatchDetected') is True and r.get('partialCleaned') is True and r.get('ready') is False,
          'interrupted': r.get('recovered') is True and r.get('partialCleaned') is True and r.get('readyBeforeVerify') is False and r.get('readyAfterRecovery') is True,
          'power-loss': r.get('recovered') is True and r.get('partialCleaned') is True and r.get('readyBeforeVerify') is False and r.get('readyAfterRecovery') is True,
          'missing-optional': r.get('optionalAssetMissing') is True and r.get('degraded') is True and r.get('advanced') is True and 'optional_asset_missing' in r.get('logMarkers',[]) and 'render_degraded' in r.get('logMarkers',[]),
          'sd-full': r.get('freeRatio',1)>=0 and r.get('freeRatio',1)<0.05 and r.get('refused') is True and r.get('activePackRetained') is True and r.get('previousPackRetained') is True,
          'slave-unavailable': r.get('motionDegraded') is True and r.get('completed') is True and 'motion_degraded' in r.get('logMarkers',[]),
          'rollback': r.get('activeVersion')==r.get('previousVersion') and r.get('activeChecksum')==r.get('previousChecksum') and bool(r.get('activeChecksum')) and r.get('oldFilesReattested') is True and r.get('ready') is True,
        }; return checks[s]
    except (TypeError, ValueError):
        return False

def _evidence_path(path,base_dir=None):
    candidate=Path(path)
    return candidate if candidate.is_absolute() or base_dir is None else Path(base_dir)/candidate

def _image_dimensions(path):
    try:
        with Image.open(path) as image:
            image_type=(image.format or '').lower()
            dimensions=image.size
            image.verify()
        with Image.open(path) as image:
            image.load()
        return (image_type,dimensions) if image_type in ('png','jpeg') else (None,None)
    except (Image.DecompressionBombError,IndexError,OSError,SyntaxError,UnidentifiedImageError,ValueError):
        return None,None

def _inspect_screenshots(entries,base_dir=None):
    errors=[]; inspected=[]
    if not isinstance(entries,list) or not entries:
        return [],['screenshots must contain at least one image entry']
    base=Path(base_dir).resolve() if base_dir is not None else None
    for entry in entries:
        if not isinstance(entry,dict) or not isinstance(entry.get('role'),str) or not isinstance(entry.get('path'),str) or not entry['role'].strip() or not entry['path'].strip():
            errors.append('malformed screenshot entry'); continue
        path=_evidence_path(entry['path'],base_dir)
        try:
            if path.is_symlink():
                errors.append('screenshot paths must not be symlinks'); continue
            resolved=path.resolve(strict=True)
            if base is not None and resolved != base and base not in resolved.parents:
                errors.append('screenshot path escapes evidence directory'); continue
            stat=path.stat()
            if not path.is_file() or stat.st_size <= 0:
                errors.append('screenshots must reference non-empty regular files'); continue
            if stat.st_size > MAX_SCREENSHOT_BYTES:
                errors.append('screenshot exceeds maximum size'); continue
            image_type,dimensions=_image_dimensions(path)
            if image_type not in ('png','jpeg') or not dimensions:
                errors.append('screenshots must be valid PNG or JPEG images'); continue
            inspected.append({'role':entry['role'],'path':resolved,'type':image_type,'dimensions':dimensions,'bytes':stat.st_size})
        except (OSError,ValueError):
            errors.append('screenshots must reference non-empty regular files')
    return inspected,errors

def _fixture_errors(result):
    errors=[]
    if result.get('fixtureVersion') != FIXTURE_VERSION:
        errors.append(f'fixtureVersion must equal {FIXTURE_VERSION}')
    if result.get('courseId') != COURSE_ID:
        errors.append(f'courseId must equal {COURSE_ID}')
    if result.get('lessonId') not in LESSON_IDS:
        errors.append('lessonId is not an approved Task 14 fixture lesson')
    return errors

def _script_hash_errors(result,capture_script=None,verifier_script=None):
    errors=[]
    for field,flag,path in (
        ('captureScriptSha256','--capture-script',capture_script),
        ('verifierScriptSha256','--verifier-script',verifier_script),
    ):
        declared=result.get(field)
        if not isinstance(declared,str) or not SHA256.fullmatch(declared):
            errors.append(f'invalid {field}')
        if path is None:
            continue
        try:
            candidate=Path(path)
            if candidate.is_symlink() or not candidate.is_file():
                raise OSError
            actual=_stream_sha256(candidate)
        except (OSError,ValueError):
            errors.append(f'cannot hash {flag}')
            continue
        if isinstance(declared,str) and declared != actual:
            errors.append(f'{field} does not match {flag}')
    return errors

def _strict_utc(value):
    if not isinstance(value,str) or not re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z',value
    ):
        raise ValueError
    parsed=datetime.fromisoformat(value[:-1]+'+00:00')
    if parsed.tzinfo != timezone.utc:
        raise ValueError
    return parsed

def _cold_artifact_root(result,base_dir=None):
    if base_dir is not None:
        return Path(base_dir)
    screenshots=result.get('screenshots') if isinstance(result,dict) else None
    if isinstance(screenshots,list):
        for entry in screenshots:
            if isinstance(entry,dict) and isinstance(entry.get('path'),str):
                return Path(entry['path']).parent
    return None

def _cold_artifact_errors(result,base_dir=None):
    errors=[]
    root=_cold_artifact_root(result,base_dir)
    if root is None:
        return [f'cold evidence artifact missing: {name}' for name in COLD_REQUIRED]
    paths={name:root/name for name in COLD_REQUIRED}
    available={}
    for name,path in paths.items():
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
                raise OSError
            available[name]=path
        except OSError:
            errors.append(f'cold evidence artifact missing: {name}')

    response=available.get('eviction-response.json')
    if response is not None:
        try:
            response_data=json.loads(response.read_text())
        except (OSError,TypeError,ValueError,json.JSONDecodeError):
            response_data=None
        if response_data != {'data':result.get('evictionResult')}:
            errors.append('cold eviction response does not exactly match result')

    checksum=available.get('eviction-response.sha256')
    if response is not None and checksum is not None:
        try:
            parts=checksum.read_text().strip().split(maxsplit=1)
            declared_hash=parts[0]
            declared_path=Path(parts[1].lstrip('*'))
            if not declared_path.is_absolute():
                declared_path=checksum.parent/declared_path
            valid_checksum=(
                len(parts)==2
                and SHA256.fullmatch(declared_hash) is not None
                and declared_path.resolve()==response.resolve()
                and declared_hash==_stream_sha256(response)
            )
        except (IndexError,OSError,ValueError):
            valid_checksum=False
        if not valid_checksum:
            errors.append('cold eviction response checksum does not match artifact')

    assignment_response=available.get('assignment-create-response.json')
    if assignment_response is not None:
        assignment_payload,assignment_has_credentials=_read_assignment_artifact(
            assignment_response
        )
        if assignment_has_credentials:
            errors.append('cold assignment response contains forbidden credential material')
        valid_assignment_shape=_assignment_payload_matches(assignment_payload,result)
        if not assignment_has_credentials and not valid_assignment_shape:
            errors.append('cold assignment creation response does not match result')

    assignment_checksum=available.get('assignment-create-response.sha256')
    if assignment_response is not None and assignment_checksum is not None:
        try:
            parts=assignment_checksum.read_text().strip().split(maxsplit=1)
            declared_hash=parts[0]
            declared_path=Path(parts[1].lstrip('*'))
            if not declared_path.is_absolute():
                declared_path=assignment_checksum.parent/declared_path
            valid_assignment_checksum=(
                len(parts)==2
                and SHA256.fullmatch(declared_hash) is not None
                and declared_path.resolve()==assignment_response.resolve()
                and declared_hash==_stream_sha256(assignment_response)
            )
        except (IndexError,OSError,ValueError):
            valid_assignment_checksum=False
        if not valid_assignment_checksum:
            errors.append('cold assignment creation checksum does not match artifact')

    timestamp_fields={
        'utc-start.txt':'utcStart',
        'eviction-completed-utc.txt':'evictionCompletedUtc',
        'cold-capture-started-utc.txt':'coldCaptureStartedUtc',
    }
    artifact_times={}
    timestamps_match=True
    for name,field in timestamp_fields.items():
        path=available.get(name)
        if path is None:
            timestamps_match=False
            continue
        try:
            value=path.read_text().strip()
            artifact_times[field]=_strict_utc(value)
            if value != result.get(field):
                timestamps_match=False
        except (OSError,TypeError,ValueError):
            timestamps_match=False
    if len(artifact_times)==3:
        try:
            assignment=_strict_utc(result.get('assignmentCreatedUtc'))
            end=_strict_utc(result.get('utcEnd'))
            if not (
                artifact_times['utcStart']
                <= artifact_times['evictionCompletedUtc']
                < artifact_times['coldCaptureStartedUtc']
                < assignment
                < end
            ):
                timestamps_match=False
        except (TypeError,ValueError):
            timestamps_match=False
    if not timestamps_match:
        errors.append('cold artifact timestamps do not match result')
    return errors

def _contains_credential_material(value):
    if isinstance(value,dict):
        for key,item in value.items():
            normalized=re.sub(r'[^a-z0-9]','',str(key).lower())
            if normalized in SECRET_FIELD_NAMES:
                return True
            if _contains_credential_material(item):
                return True
        return False
    if isinstance(value,list):
        return any(_contains_credential_material(item) for item in value)
    if isinstance(value,str):
        return bool(re.search(r'(?i)\bbearer\s+\S+',value) or JWT_VALUE.search(value))
    return False

def _raw_contains_credential_material(raw):
    if not isinstance(raw,str):
        return False
    if _contains_credential_material(raw):
        return True
    return bool(
        re.search(
            r'(?i)(?:authorization|access[_-]?token|refresh[_-]?token|token)\s*["\']?\s*[:=]',
            raw,
        )
    )

def _read_assignment_artifact(path):
    try:
        raw=path.read_text()
    except (OSError,UnicodeError):
        return None,False
    has_credentials=_raw_contains_credential_material(raw)
    try:
        payload=json.loads(raw)
    except (TypeError,ValueError,json.JSONDecodeError):
        return None,has_credentials
    return payload,has_credentials or _contains_credential_material(payload)

def _assignment_payload_matches(payload,result):
    if not isinstance(payload,dict) or set(payload)!={'data'}:
        return False
    data=payload.get('data')
    if not isinstance(data,dict) or set(data)!={'assignment'}:
        return False
    assignment=data.get('assignment')
    valid=(
        isinstance(assignment,dict)
        and set(assignment)==ASSIGNMENT_RESPONSE_FIELDS
        and all(
            isinstance(assignment.get(name),str) and bool(assignment.get(name).strip())
            for name in (
                'assignmentId','deviceId','childId','lessonId','lessonTitle',
                'manifestChecksum','profile','state','createdAt',
            )
        )
        and all(
            type(assignment.get(name)) is int and assignment.get(name)>0
            for name in ('assignmentVersion','lessonVersion')
        )
        and isinstance(result,dict)
        and assignment.get('assignmentId')==result.get('assignmentId')
        and assignment.get('assignmentVersion')==result.get('assignmentVersion')
        and assignment.get('deviceId')==result.get('assignmentBackendDeviceId')
        and assignment.get('childId')==result.get('assignmentChildId')
        and assignment.get('lessonId')==result.get('lessonId')
        and assignment.get('lessonVersion')==result.get('lessonVersion')
        and assignment.get('manifestChecksum')==result.get('manifestChecksum')
        and assignment.get('profile')==result.get('assignmentProfile')=='espTft'
        and assignment.get('state')=='ASSIGNED'
        and assignment.get('createdAt')==result.get('assignmentCreatedUtc')
    )
    if not valid:
        return False
    try:
        _strict_utc(assignment.get('createdAt'))
    except (TypeError,ValueError):
        return False
    return True

def _cold_eviction_errors(result: Dict[str, Any], raw_logs: str) -> List[str]:
    errors=[]
    fields=(
        'evictionRequestedCacheKey','evictionResult','evictionCompletedUtc',
        'coldCaptureStartedUtc','assignmentCreatedUtc','assignmentBackendDeviceId',
        'assignmentChildId','assignmentProfile',
    )
    missing=[name for name in fields if name not in result]
    errors.extend(f'cold eviction evidence missing: {name}' for name in missing)
    if missing:
        return errors

    eviction=result.get('evictionResult')
    expected_fields={'cacheKey','status','evicted','notFound','fileCount','reason'}
    coherent=False
    if isinstance(eviction,dict) and set(eviction)==expected_fields:
        file_count=eviction.get('fileCount')
        typed=(
            isinstance(eviction.get('cacheKey'),str)
            and isinstance(eviction.get('status'),str)
            and type(eviction.get('evicted')) is bool
            and type(eviction.get('notFound')) is bool
            and type(file_count) is int
            and file_count >= 0
            and isinstance(eviction.get('reason'),str)
        )
        coherent_evicted=(
            eviction.get('status')=='evicted'
            and eviction.get('evicted') is True
            and eviction.get('notFound') is False
            and eviction.get('reason')=='evicted'
        )
        coherent_not_found=(
            eviction.get('status')=='not_found'
            and eviction.get('evicted') is False
            and eviction.get('notFound') is True
            and file_count==0
            and eviction.get('reason')=='not_found'
        )
        coherent=typed and (coherent_evicted or coherent_not_found)
    if not coherent:
        errors.append('cold eviction result is not coherent')

    eviction_key=eviction.get('cacheKey') if isinstance(eviction,dict) else None
    if not (
        result.get('evictionRequestedCacheKey')
        == eviction_key
        == result.get('cacheKey')
    ):
        errors.append('cold eviction cache keys must match exactly')

    try:
        start=_strict_utc(result.get('utcStart'))
        completed=_strict_utc(result.get('evictionCompletedUtc'))
        capture=_strict_utc(result.get('coldCaptureStartedUtc'))
        assignment=_strict_utc(result.get('assignmentCreatedUtc'))
        end=_strict_utc(result.get('utcEnd'))
        if not start <= completed < capture < assignment < end:
            raise ValueError
    except (TypeError,ValueError):
        errors.append('cold eviction timestamps must be strict UTC and correctly ordered')

    if coherent:
        marker_pattern=re.compile(
            r'(?<![A-Za-z0-9_])lesson_cache_evict '
            rf'cache_key={re.escape(str(eviction_key))} '
            rf'code={re.escape(str(eviction.get("status")))} '
            rf'file_count={re.escape(str(eviction.get("fileCount")))}(?=\s|$)'
        )
        marker_found=any(marker_pattern.search(line) for line in raw_logs.splitlines())
        if not marker_found:
            errors.append('cold eviction log marker does not match result')
    return errors

def validate_result(
    scenario,result,raw_logs,base_dir=None,capture_script=None,verifier_script=None
):
    if not isinstance(result,dict):return ['result.json must contain an object']
    errors=[f'missing common metadata: {name}' for name in COMMON_FIELDS if name not in result]
    if errors:return errors
    errors.extend(_fixture_errors(result))
    errors.extend(_script_hash_errors(result,capture_script,verifier_script))
    for name in ('backendCommit','espServerCommit','firmwareCommit'):
        if not isinstance(result[name],str) or not COMMIT.fullmatch(result[name]):errors.append(f'invalid {name}')
    for name in ('manifestChecksum','packChecksum'):
        if not isinstance(result[name],str) or not SHA256.fullmatch(result[name]):errors.append(f'invalid {name}')
    for name in ('firmwareVersion','deviceId','assignmentId','sessionId','fixtureVersion','courseId','lessonId','cacheKey','operator'):
        if not isinstance(result[name],str) or not result[name].strip():errors.append(f'invalid {name}')
    try:
        start=datetime.fromisoformat(result['utcStart'].replace('Z','+00:00'))
        end=datetime.fromisoformat(result['utcEnd'].replace('Z','+00:00'))
        if start.tzinfo is None or end.tzinfo is None or end <= start:raise ValueError
    except (AttributeError,TypeError,ValueError):
        errors.append('invalid UTC evidence interval')
    for name in ('assignmentVersion','lessonVersion','internalSramMin','psramFirst','psramLast'):
        if not isinstance(result[name],int) or isinstance(result[name],bool) or result[name] <= 0:errors.append(f'invalid {name}')
    if result.get('commandExitCode') != 0:errors.append('commandExitCode must be zero')
    screenshots,screenshot_errors=_inspect_screenshots(result.get('screenshots'),base_dir)
    errors.extend(screenshot_errors)
    if scenario == 'preview-parity' and not screenshot_errors:
        roles={item['role']:item for item in screenshots}
        if set(roles) != {'preview','hardware'} or len(screenshots) != 2:
            errors.append('preview-parity requires exactly preview and hardware screenshots')
        elif any(item['dimensions'] != (480,320) for item in screenshots):
            errors.append('preview-parity screenshots must be exactly 480x320')
        elif _stream_sha256(roles['preview']['path']) == _stream_sha256(roles['hardware']['path']):
            errors.append('preview and hardware screenshots must not have identical content')
    markers=result.get('logMarkers')
    if not isinstance(markers,list) or not markers or any(not isinstance(marker,str) or not marker for marker in markers):
        errors.append('logMarkers must be a non-empty string list')
    else:
        lowered=raw_logs.lower()
        for marker in SCENARIO_LOG_MARKERS[scenario]:
            if marker not in markers:errors.append(f'{scenario} requires log marker: {marker}')
        for marker in markers:
            if marker.lower() not in lowered:errors.append(f'raw logs missing declared marker: {marker}')
    if result.get('scenario') != scenario:errors.append('result scenario does not match command')
    if result.get('status') != 'PASS':errors.append('result status is not PASS')
    if scenario == 'cold':
        errors.extend(_cold_eviction_errors(result,raw_logs))
        errors.extend(_cold_artifact_errors(result,base_dir))
    if not scenario_valid(scenario,result):errors.append(f'{scenario} decisive signals are incomplete')
    if not _scenario_raw_evidence_bound(scenario,result,raw_logs):
        errors.append(f'{scenario} raw evidence is not bound to result identity and cache')
    return errors

def _stream_sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()

def _hash_file(path):
    return {'path':str(path),'sha256':_stream_sha256(path)}

def build_evidence_report(
    scenario,result,files,raw_logs,base_dir=None,capture_script=None,verifier_script=None
):
    errors=validate_result(
        scenario,result,raw_logs,base_dir,capture_script,verifier_script
    )
    screenshots,_=_inspect_screenshots(result.get('screenshots') if isinstance(result,dict) else None,base_dir)
    report_files=dict(files)
    if scenario == 'cold':
        root=_cold_artifact_root(result,base_dir)
        if root is not None:
            for name in COLD_REQUIRED:
                report_files.setdefault(name,root/name)
            assignment_path=root/'assignment-create-response.json'
            assignment_payload,assignment_has_credentials=_read_assignment_artifact(
                assignment_path
            )
            if assignment_has_credentials or not _assignment_payload_matches(
                assignment_payload,result
            ):
                report_files.pop('assignment-create-response.json',None)
                report_files.pop('assignment-create-response.sha256',None)
    return {
      'scenario':scenario,'status':'PASS' if not errors else 'NOT_PASS',
      'capturedAt':datetime.now(timezone.utc).isoformat(),'validationErrors':errors,
      'files':{name:_hash_file(path) for name,path in report_files.items() if path.is_file() and not path.is_symlink()},
      'screenshots':[{**_hash_file(item['path']),'role':item['role'],'type':item['type'],'width':item['dimensions'][0],'height':item['dimensions'][1]} for item in screenshots],
    }

def main():
    p=argparse.ArgumentParser(); p.add_argument('scenario',nargs='?',choices=SCENARIOS); p.add_argument('--evidence-dir',type=Path); p.add_argument('--output',type=Path); p.add_argument('--capture-script',type=Path); p.add_argument('--verifier-script',type=Path); p.add_argument('--self-test',action='store_true'); a=p.parse_args()
    if a.self_test:
        valid={'preview-parity':{'previewLayerRects':{'background':[0,0,480,320]},'hardwareLayerRects':{'background':[0,0,480,320]},'previewWordText':'BARN','hardwareWordText':'BARN','previewPathOutcome':'correct','hardwarePathOutcome':'correct','previewMotionTimeline':['teach','listen'],'hardwareMotionTimeline':['teach','listen']},'cold':{'bytesDownloaded':1,'elapsedMs':1,'ready':True,'checksumVerified':True,'manifestChecksum':'a','packChecksum':'a'},'warm':{'cacheHit':True,'bytesDownloaded':0,'elapsedMs':1,'ready':True,'manifestChecksum':'a','packChecksum':'a'},'offline':{'networkAvailable':False,'completed':True,'source':'sd'},'checksum':{'mismatchDetected':True,'partialCleaned':True,'ready':False},'interrupted':{'recovered':True,'partialCleaned':True,'readyBeforeVerify':False,'readyAfterRecovery':True},'power-loss':{'recovered':True,'partialCleaned':True,'readyBeforeVerify':False,'readyAfterRecovery':True},'missing-optional':{'optionalAssetMissing':True,'degraded':True,'advanced':True,'logMarkers':['optional_asset_missing','render_degraded']},'sd-full':{'freeRatio':0.04,'refused':True,'activePackRetained':True,'previousPackRetained':True},'slave-unavailable':{'motionDegraded':True,'completed':True,'logMarkers':['motion_degraded']},'rollback':{'activeVersion':1,'previousVersion':1,'activeChecksum':'a','previousChecksum':'a','oldFilesReattested':True,'ready':True}}
        for scenario in SCENARIOS: assert scenario_valid(scenario,valid[scenario]); assert not scenario_valid(scenario,{})
        fixture={'fixtureVersion':FIXTURE_VERSION,'courseId':COURSE_ID,'lessonId':LESSON_IDS[0]}
        assert not _fixture_errors(fixture)
        for field,foreign in (('fixtureVersion','foreign'),('courseId','foreign'),('lessonId','foreign')):
            candidate={**fixture,field:foreign}; assert _fixture_errors(candidate)
        with tempfile.TemporaryDirectory() as directory:
            capture=Path(directory)/'capture.py'; verifier=Path(directory)/'verify.py'
            capture.write_bytes(b'capture\n'); verifier.write_bytes(b'verify\n')
            hashes={'captureScriptSha256':_stream_sha256(capture),'verifierScriptSha256':_stream_sha256(verifier)}
            assert not _script_hash_errors(hashes,capture,verifier)
            assert _script_hash_errors({**hashes,'captureScriptSha256':'0'*64},capture,verifier)
        print(json.dumps({'status':'PASS','scenarios':SCENARIOS,'validAndInvalidCases':len(SCENARIOS)*2,'fixtureBindingCases':4,'scriptHashCases':2})); return 0
    if a.scenario is None or a.evidence_dir is None:
        p.error('scenario and --evidence-dir are required unless --self-test is used')
    if a.capture_script is None or a.verifier_script is None:
        p.error('--capture-script and --verifier-script are required unless --self-test is used')
    required=REQUIRED+COLD_REQUIRED if a.scenario=='cold' else REQUIRED
    files={name:a.evidence_dir/name for name in required}; missing=[name for name,path in files.items() if not path.is_file() or path.stat().st_size==0]
    result_data={}
    if not missing:
        try: result_data=json.loads(files['result.json'].read_text())
        except Exception: missing.append('valid result.json')
    raw_logs='\n'.join(files[name].read_text(errors='replace') for name in ('serial.log','server.log') if files[name].is_file())
    report=build_evidence_report(a.scenario,result_data,files,raw_logs,a.evidence_dir,a.capture_script,a.verifier_script)
    report['missingEvidence']=missing
    if missing:report['status']='NOT_PASS'
    data=json.dumps(report,indent=2)+'\n'; print(data,end=''); a.output and a.output.write_text(data); return 0 if report['status']=='PASS' else 1
if __name__=='__main__': raise SystemExit(main())
