#!/usr/bin/env python3
"""Fail-closed evidence collector; it never injects a fault or contacts production."""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

HIL_VALIDATOR_PATH=Path(__file__).with_name('lesson_studio_task14_hil_storage.py')
HIL_VALIDATOR_SPEC=importlib.util.spec_from_file_location(
    'lesson_studio_task14_hil_pure_validators',HIL_VALIDATOR_PATH
)
HIL_VALIDATOR_MODULE=importlib.util.module_from_spec(HIL_VALIDATOR_SPEC)
assert HIL_VALIDATOR_SPEC.loader is not None
HIL_VALIDATOR_SPEC.loader.exec_module(HIL_VALIDATOR_MODULE)
HilValidationError=HIL_VALIDATOR_MODULE.HilValidationError
validate_fixture_response=HIL_VALIDATOR_MODULE.validate_fixture_response
validate_inspect_response=HIL_VALIDATOR_MODULE.validate_inspect_response
validate_scenario_outcome=HIL_VALIDATOR_MODULE.validate_scenario_outcome
validate_preservation_inspections=HIL_VALIDATOR_MODULE.validate_preservation_inspections
validate_cleanup_inspection=HIL_VALIDATOR_MODULE.validate_cleanup_inspection
validate_status_response=HIL_VALIDATOR_MODULE.validate_status_response
del HIL_VALIDATOR_MODULE

SCENARIOS=('preview-parity','cold','warm','offline','checksum','interrupted','power-loss','missing-optional','sd-full','slave-unavailable','rollback')
HIL_STORAGE_SCENARIOS=(
    'evict-before-first-unlink-fail','evict-after-unlinks-fail',
    'evict-before-rmdir-fail','evict-after-unlinks-sd-removal',
    'sync-before-download-write-no-space','sync-after-download-bytes-no-space',
    'sync-before-checksum-corrupt-staging','sync-before-commit-rename-fail',
    'sync-before-commit-rename-power-loss',
)
HIL_POWER_LOSS_SCENARIO=HIL_STORAGE_SCENARIOS[-1]
HIL_ORDINARY_REQUIRED=(
    'command.txt','serial.log','server.log','timeline.log','build-manifest.json',
    'build-manifest.sha256','status-before.json','inspect-before.json',
    'stage-response.json','arm-response.json','trigger-response.json',
    'status-after.json','inspect-after.json','cleanup-response.json','result.json',
    'evidence.json','validator-exit-code.txt','SHA256SUMS',
)
HIL_POWER_REQUIRED=tuple(
    name for name in HIL_ORDINARY_REQUIRED if name != 'trigger-response.json'
)[:-1]+(
    'checkpoint-reached-utc.txt','power-removed-utc.txt','reboot-serial.log',
    'post-reboot-inspect.json','SHA256SUMS',
)
HIL_BUILD_FIELDS={
    'sourceCommit','profile','configEnabled','sdkconfigSha256','binarySha256',
    'elfSha256','mapSha256','archiveSha256','binaryBytes','appPartitionFreeBytes',
}
HIL_EVENT_ORDER=(
    'status-before','inspect-before','stage','arm','trigger',
    'status-after','inspect-after','cleanup',
)
HIL_SCENARIO_CONTRACT={
    'evict-before-first-unlink-fail':('evict','before_first_unlink','fail',0),
    'evict-after-unlinks-fail':('evict','after_unlinks','fail',1),
    'evict-before-rmdir-fail':('evict','before_rmdir','fail',1),
    'evict-after-unlinks-sd-removal':('evict','after_unlinks','pause',1),
    'sync-before-download-write-no-space':('sync','before_download_write','no_space',0),
    'sync-after-download-bytes-no-space':('sync','after_download_bytes','no_space',1),
    'sync-before-checksum-corrupt-staging':('sync','before_checksum_verify','corrupt_staging',0),
    'sync-before-commit-rename-fail':('sync','before_commit_rename','fail',0),
    'sync-before-commit-rename-power-loss':('sync','before_commit_rename','pause',0),
}
HIL_ARM_CONTRACT={
    'evict-before-first-unlink-fail':('evict','before_first_unlink','fail',0,0),
    'evict-after-unlinks-fail':('evict','after_unlinks','fail',1,0),
    'evict-before-rmdir-fail':('evict','before_rmdir','fail',0,0),
    'evict-after-unlinks-sd-removal':('evict','after_unlinks','pause',1,15),
    'sync-before-download-write-no-space':('sync','before_download_write','no_space',0,0),
    'sync-after-download-bytes-no-space':('sync','after_download_bytes','no_space',1,0),
    'sync-before-checksum-corrupt-staging':('sync','before_checksum_verify','corrupt_staging',0,0),
    'sync-before-commit-rename-fail':('sync','before_commit_rename','fail',0,0),
    'sync-before-commit-rename-power-loss':('sync','before_commit_rename','pause',0,30),
}
HIL_ARM_FIELDS={
    'cacheKey','status','operation','checkpoint','action','threshold',
    'declaredAssetBytes','pauseSeconds','armSequence',
}
HIL_STATUS_FIELDS={
    'status','cacheKey','armed','reached','consumed','operation','checkpoint',
    'action','threshold','declaredAssetBytes','pauseSeconds','armSequence',
    'reachedSequence','consumedSequence',
}
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

def _cold_eviction_errors(result: dict[str, Any], raw_logs: str) -> list[str]:
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
    if (
        isinstance(eviction,dict)
        and eviction.get('status')=='partial_evict_recovery_required'
    ):
        errors.append('cold partial eviction requires attended retry or repair')
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

def _hil_build_identity_errors(value):
    errors=[]
    if not isinstance(value,dict) or set(value) != HIL_BUILD_FIELDS:
        return ['invalid HIL build identity fields']
    if value.get('profile') != 'hil' or value.get('configEnabled') is not True:
        errors.append('HIL evidence must bind an enabled HIL build')
    if not isinstance(value.get('sourceCommit'),str) or not re.fullmatch(r'[0-9a-f]{40}',value['sourceCommit']):
        errors.append('invalid HIL source commit')
    for name in ('sdkconfigSha256','binarySha256','elfSha256','mapSha256','archiveSha256'):
        if not isinstance(value.get(name),str) or not SHA256.fullmatch(value[name]):
            errors.append(f'invalid HIL build hash: {name}')
    for name in ('binaryBytes','appPartitionFreeBytes'):
        if type(value.get(name)) is not int or value[name] <= 0:
            errors.append(f'invalid HIL build integer: {name}')
    return errors

def validate_hil_storage_result(scenario,result):
    errors=[]
    if scenario not in HIL_STORAGE_SCENARIOS:
        return ['unknown HIL storage scenario']
    if not isinstance(result,dict):
        return ['HIL result must be an object']
    if result.get('scenario') != scenario:errors.append('HIL result scenario mismatch')
    if result.get('status') != 'PASS':errors.append('HIL result status is not PASS')
    device_id=result.get('deviceId'); device_uuid=result.get('deviceUuid')
    connection=result.get('connectionIdentity')
    if not isinstance(device_id,str) or not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}',device_id):
        errors.append('invalid attended HIL device MAC')
    if not isinstance(device_uuid,str) or not re.fullmatch(
        r'[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}',
        device_uuid,re.I,
    ):
        errors.append('invalid attended HIL route UUID')
    if not isinstance(connection,dict) or set(connection)!={'deviceId','clientId'}:
        errors.append('invalid attended HIL connection identity')
    elif connection.get('deviceId')!=device_id or connection.get('clientId')!=device_uuid:
        errors.append('attended HIL connection identity mismatch')
    errors.extend(_hil_build_identity_errors(result.get('buildIdentity')))
    sequences=[]
    for name in ('armSequence','reachedSequence','consumedSequence'):
        value=result.get(name)
        if type(value) is not int or value <= 0:
            errors.append(f'invalid {name}')
        else:sequences.append(value)
    if len(sequences)==3 and not sequences[0] < sequences[1] < sequences[2]:
        errors.append('HIL sequences must be strictly increasing')
    if result.get('events') != list(HIL_EVENT_ORDER):
        errors.append('HIL event order is invalid')
    operation,checkpoint,action,progress=HIL_SCENARIO_CONTRACT[scenario]
    for name,expected in (
        ('operation',operation),('checkpoint',checkpoint),
        ('faultAction',action),('expectedProgress',progress),
    ):
        if result.get(name) != expected or type(result.get(name)) is not type(expected):
            errors.append(f'HIL scenario contract mismatch: {name}')
    if result.get('checkpointExercised') is not True:
        errors.append('HIL checkpoint was not exercised')
    if result.get('cleanupVerified') is not True:
        errors.append('HIL cleanup was not verified')
    if result.get('controllerInactive') is not True:
        errors.append('HIL controller remained active')
    trigger=result.get('triggerOutcome')
    absent=result.get('triggerResponseAbsent')
    cache_key=result.get('cacheKey')
    if not isinstance(cache_key,str) or not re.fullmatch(r'hil-[a-z0-9-]*/v[1-9][0-9]*-[0-9a-f]{64}',cache_key):
        errors.append('invalid HIL cacheKey')
    if scenario == HIL_POWER_LOSS_SCENARIO:
        if absent is not True or trigger is not None:
            errors.append('power-loss trigger response must be absent')
    else:
        if absent is not False or not isinstance(trigger,dict):
            errors.append('ordinary HIL trigger response is missing')
        elif trigger.get('cacheKey') != cache_key:
            errors.append('HIL trigger cache key mismatch')
        elif operation == 'evict':
            expected={
                'evict-before-first-unlink-fail':('unlink_failed',0,False),
                'evict-after-unlinks-fail':('partial_evict_recovery_required',1,False),
                'evict-before-rmdir-fail':('partial_evict_recovery_required',1,False),
                'evict-after-unlinks-sd-removal':('evicted',1,True),
            }[scenario]
            actual=(trigger.get('status'),trigger.get('fileCount'),trigger.get('evicted'))
            if actual != expected or trigger.get('notFound') is not False:
                errors.append('eviction HIL trigger outcome mismatch')
        else:
            files=trigger.get('files')
            valid_file=(
                isinstance(files,list) and len(files)==1 and
                isinstance(files[0],dict) and files[0].get('state')=='FAILED' and
                files[0].get('error')=='asset transfer failed'
            )
            if not (
                trigger.get('ready') is False and
                type(trigger.get('downloadedCount')) is int and trigger['downloadedCount']==0 and
                type(trigger.get('skippedCount')) is int and trigger['skippedCount']==0 and
                type(trigger.get('failedCount')) is int and trigger['failedCount']==1 and
                type(trigger.get('totalBytes')) is int and trigger['totalBytes']==0 and
                'manifestChecksum' not in trigger and valid_file
            ):
                errors.append('sync HIL trigger outcome mismatch')
    if scenario == HIL_POWER_LOSS_SCENARIO:
        exact={
            'powerLoss':True,'checkpointReached':True,
            'triggerResponseAbsent':True,'successMarkerBeforeLoss':False,
            'rebootCaptured':True,'armClearedAfterReboot':True,
            'postRebootInspected':True,'retryStatus':'ready',
            'triggerPendingAtMarker':True,'triggerPendingAtCutBoundary':True,
            'disconnectAfterPowerCutBoundary':True,
        }
        for name,expected in exact.items():
            if result.get(name) != expected or type(result.get(name)) is not type(expected):
                errors.append(f'invalid power-loss evidence: {name}')
        try:
            ordered=[_strict_utc(result.get(name)) for name in (
                'utcStart','checkpointReachedUtc','powerCutBoundaryUtc',
                'disconnectObservedUtc','powerRemovalConfirmedUtc','utcEnd',
            )]
            start,checkpoint,boundary,disconnected,confirmed,end=ordered
            if not (start < checkpoint < boundary < disconnected <= confirmed < end):
                errors.append('invalid power-loss timestamp order')
        except (TypeError,ValueError):errors.append('invalid power-loss boundary timestamps')
    return errors

def _atomic_text(path,data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    descriptor,temporary=tempfile.mkstemp(prefix=f'.{path.name}.',dir=path.parent)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as output:
            output.write(data); output.flush(); os.fsync(output.fileno())
        os.replace(temporary,path)
    except BaseException:
        try:os.unlink(temporary)
        except FileNotFoundError:pass
        raise

def _power_loss_timestamp_artifact_errors(result,evidence_dir):
    root=Path(evidence_dir); errors=[]
    expected={
        'checkpoint-reached-utc.txt':result.get('checkpointReachedUtc'),
        'power-removed-utc.txt':result.get('powerRemovalConfirmedUtc'),
    }
    for name,value in expected.items():
        path=root/name
        try:actual=path.read_text()
        except (OSError,UnicodeError):
            errors.append(f'HIL timestamp artifact missing or invalid: {name}')
            continue
        if not isinstance(value,str) or actual != value+'\n':
            errors.append(f'HIL timestamp artifact mismatch: {name}')
    return errors

def _load_hil_json(path,label,errors):
    try:
        value=json.loads(Path(path).read_text())
    except (OSError,UnicodeError,ValueError,json.JSONDecodeError):
        errors.append(f'invalid HIL control artifact: {label}')
        return None
    if not isinstance(value,dict):
        errors.append(f'invalid HIL control artifact: {label}')
        return None
    return value

def _hil_serial_sequence(lines,marker,expected,sequence_name,errors):
    matches=[]
    for line in lines:
        if marker not in line:continue
        if not _line_has_fields(line,expected):continue
        sequence=_positive_int_field(line,sequence_name)
        if sequence is not None:matches.append(sequence)
    if len(matches)!=1:
        errors.append(f'invalid HIL serial marker: {marker}')
        return None
    return matches[0]

def _hil_control_artifact_errors(scenario,evidence_dir,result):
    root=Path(evidence_dir); errors=[]
    arm=_load_hil_json(root/'arm-response.json','arm-response.json',errors)
    status=_load_hil_json(root/'status-after.json','status-after.json',errors)
    inspect_before=_load_hil_json(root/'inspect-before.json','inspect-before.json',errors)
    if arm is None or status is None or inspect_before is None:return errors
    operation,checkpoint,action,threshold,pause_seconds=HIL_ARM_CONTRACT[scenario]
    cache_key=result.get('cacheKey')
    expected_arm={
        'cacheKey':cache_key,'status':'armed','operation':operation,
        'checkpoint':checkpoint,'action':action,'threshold':threshold,
        'pauseSeconds':pause_seconds,
    }
    if set(arm)!=HIL_ARM_FIELDS:
        errors.append('invalid HIL arm response fields')
    for name,value in expected_arm.items():
        if arm.get(name)!=value or type(arm.get(name)) is not type(value):
            errors.append(f'HIL arm response mismatch: {name}')
    declared=arm.get('declaredAssetBytes')
    if type(declared) is not int or declared < 0 or (
        checkpoint=='after_download_bytes' and declared <= 0
    ) or (checkpoint!='after_download_bytes' and declared != 0):
        errors.append('HIL arm response mismatch: declaredAssetBytes')
    arm_sequence=arm.get('armSequence')
    if type(arm_sequence) is not int or arm_sequence <= 0:
        errors.append('invalid HIL arm sequence')
    elif arm_sequence!=result.get('armSequence'):
        errors.append('HIL arm sequence does not match result')
    if set(status)!=HIL_STATUS_FIELDS:
        errors.append('invalid HIL status-after fields')
    if scenario==HIL_POWER_LOSS_SCENARIO:
        if not (
            status.get('status')=='idle' and status.get('cacheKey')=='' and
            status.get('armed') is False and status.get('reached') is False and
            status.get('consumed') is False and
            status.get('operation')=='evict' and
            status.get('checkpoint')=='before_first_unlink' and
            status.get('action')=='fail' and
            all(type(status.get(name)) is int and status.get(name)==0 for name in (
                'threshold','declaredAssetBytes','pauseSeconds','armSequence',
                'reachedSequence','consumedSequence',
            ))
        ):
            errors.append('power-loss status-after did not clear volatile arm')
    else:
        for name in (
            'cacheKey','operation','checkpoint','action','threshold',
            'declaredAssetBytes','pauseSeconds','armSequence',
        ):
            if status.get(name)!=arm.get(name) or type(status.get(name)) is not type(arm.get(name)):
                errors.append(f'HIL status-after does not bind arm: {name}')
        if not (
            status.get('status')=='consumed' and status.get('armed') is False and
            status.get('reached') is True and status.get('consumed') is True
        ):
            errors.append('ordinary HIL status-after is not consumed')
    reached=status.get('reachedSequence') if scenario!=HIL_POWER_LOSS_SCENARIO else result.get('reachedSequence')
    consumed=status.get('consumedSequence') if scenario!=HIL_POWER_LOSS_SCENARIO else result.get('consumedSequence')
    if not (
        type(arm_sequence) is int and type(reached) is int and type(consumed) is int and
        arm_sequence < reached < consumed and reached==result.get('reachedSequence') and
        consumed==result.get('consumedSequence')
    ):
        errors.append('HIL control artifact sequences are not strictly bound')
    serial=''
    for name in ('serial.log','reboot-serial.log'):
        path=root/name
        if path.is_file():serial+='\n'+path.read_text(errors='replace')
    lines=serial.splitlines()
    marker_fields={'operation':operation,'checkpoint':checkpoint,'cache_key':cache_key}
    reached_fields={**marker_fields,'count':HIL_SCENARIO_CONTRACT[scenario][3]}
    consumed_fields={**marker_fields,'action':action}
    serial_reached=_hil_serial_sequence(
        lines,'HIL_STORAGE_CHECKPOINT_REACHED',reached_fields,
        'reached_sequence',errors,
    )
    serial_consumed=_hil_serial_sequence(
        lines,'HIL_STORAGE_FAULT_CONSUMED',consumed_fields,
        'consumed_sequence',errors,
    )
    if serial_reached!=reached or serial_consumed!=consumed:
        errors.append('HIL serial sequences do not match control artifacts')
    try:
        cleanup_inspection=validate_cleanup_inspection(
            inspect_before,result.get('cleanupInspection')
        )
        final_status=validate_status_response(
            result.get('finalStatus'),expected_cache_key=None
        )
        if final_status!=status:
            errors.append('final HIL status does not match status-after artifact')
        cleanup_verified=cleanup_inspection==result.get('cleanupInspection')
        controller_inactive=final_status.get('armed') is False
        if result.get('cleanupVerified') is not cleanup_verified:
            errors.append('cleanupVerified is not derived from raw inspection')
        if result.get('controllerInactive') is not controller_inactive:
            errors.append('controllerInactive is not derived from raw status')
    except HilValidationError as exc:
        errors.append(f'invalid HIL cleanup recovery evidence: {exc}')
    return errors

def _hil_artifact_credential_errors(root,names):
    errors=[]; root=Path(root)
    credential=re.compile(
        r'''(?im)
        \b(?:proxy-)?authorization\s*[:=]
        |\b(?:bearer|basic)\s+\S+
        |["']?(?:x-mint-secret|x-api-key|api[-_]key|password|passwd|
          token|secret|credential|credentials)["']?\s*[:=]\s*\S+
        |^(?:cookie|set-cookie)\s*:\s*\S+
        ''',
        re.X,
    )
    for name in names:
        path=root/name
        if not path.is_file():continue
        text=path.read_text(errors='replace')
        if JWT_VALUE.search(text):errors.append(f'HIL artifact contains JWT marker: {name}')
        if re.search(r'https?://[^/\s:@]+:[^/\s@]+@',text):errors.append(f'HIL artifact contains URL userinfo: {name}')
        if re.search(r'[?&](?:token|key|secret|password)=',text,re.I):errors.append(f'HIL artifact contains query credential: {name}')
        if credential.search(text):errors.append(f'HIL artifact contains opaque credential: {name}')
    return errors

def _hil_semantic_artifact_errors(scenario,evidence_dir,result):
    root=Path(evidence_dir); errors=[]
    cache_key=result.get('cacheKey')
    if not isinstance(cache_key,str) or '/v1-' not in cache_key:
        return ['invalid HIL semantic artifact cache key']
    sibling=cache_key.replace('/v1-','/v2-',1)
    values={}
    names=['stage-response.json','inspect-before.json','inspect-after.json','cleanup-response.json']
    if scenario==HIL_POWER_LOSS_SCENARIO:names.append('post-reboot-inspect.json')
    else:names.append('trigger-response.json')
    for name in names:
        values[name]=_load_hil_json(root/name,name,errors)
    if errors:return errors
    try:
        validate_fixture_response(
            values['stage-response.json'],cache_key,sibling,
            'preservation_set','staged',
        )
        before=validate_inspect_response(values['inspect-before.json'],cache_key,sibling)
        after=validate_inspect_response(values['inspect-after.json'],cache_key,sibling)
        validate_fixture_response(
            values['cleanup-response.json'],cache_key,sibling,
            'preservation_set','cleaned',
        )
        if scenario==HIL_POWER_LOSS_SCENARIO:
            post=validate_inspect_response(
                values['post-reboot-inspect.json'],cache_key,sibling
            )
            validate_preservation_inspections(
                scenario,before,after,post_reboot=post
            )
            if result.get('triggerResponseAbsent') is not True or result.get('triggerOutcome') is not None:
                errors.append('power-loss result contradicts absent trigger artifact')
        else:
            validate_preservation_inspections(scenario,before,after)
            trigger=values['trigger-response.json']
            validate_scenario_outcome(scenario,trigger,cache_key=cache_key)
            if trigger!=result.get('triggerOutcome') or result.get('triggerResponseAbsent') is not False:
                errors.append('trigger artifact does not match result')
    except HilValidationError as exc:
        errors.append(f'invalid HIL semantic artifact: {exc}')
    return errors

def _hil_storage_artifact_errors(scenario,evidence_dir,result):
    errors=[]
    root=Path(evidence_dir)
    required=HIL_POWER_REQUIRED if scenario==HIL_POWER_LOSS_SCENARIO else HIL_ORDINARY_REQUIRED
    try:actual={path.name for path in root.iterdir()}
    except OSError:return ['HIL evidence directory is unreadable']
    if actual != set(required):errors.append('HIL evidence directory layout is not exact')
    for name in required:
        path=root/name
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:raise OSError
        except OSError:errors.append(f'HIL artifact missing or invalid: {name}')
    errors.extend(_hil_artifact_credential_errors(root,required))
    build_path=root/'build-manifest.json'
    build_sha=root/'build-manifest.sha256'
    if build_path.is_file() and build_sha.is_file():
        try:
            build=json.loads(build_path.read_text())
            parts=build_sha.read_text().strip().split()
            if build != result.get('buildIdentity'):
                errors.append('HIL build manifest does not match result')
            if len(parts)!=2 or parts[1].lstrip('*')!='build-manifest.json' or parts[0]!=_stream_sha256(build_path):
                errors.append('HIL build manifest checksum mismatch')
        except (OSError,ValueError,json.JSONDecodeError):errors.append('invalid HIL build manifest artifact')
    checksum_path=root/'SHA256SUMS'
    if checksum_path.is_file():
        try:
            rows=[line.split() for line in checksum_path.read_text().splitlines() if line.strip()]
            declared={parts[1].lstrip('*'):parts[0] for parts in rows if len(parts)==2}
            expected=set(required)-{'SHA256SUMS'}
            if set(declared)!=expected:errors.append('HIL SHA256SUMS file set mismatch')
            for name in expected:
                if name in declared and (not SHA256.fullmatch(declared[name]) or declared[name]!=_stream_sha256(root/name)):
                    errors.append(f'HIL artifact checksum mismatch: {name}')
        except (OSError,ValueError):errors.append('invalid HIL SHA256SUMS')
    validator=root/'validator-exit-code.txt'
    if validator.is_file() and validator.read_text().strip()!='0':
        errors.append('HIL validator exit code is not zero')
    serial=''
    for name in ('serial.log','reboot-serial.log'):
        path=root/name
        if path.is_file():serial+='\n'+path.read_text(errors='replace')
    if scenario==HIL_POWER_LOSS_SCENARIO:
        errors.extend(_power_loss_timestamp_artifact_errors(result,root))
        if 'HIL_STORAGE_CHECKPOINT_REACHED' not in serial:
            errors.append('power-loss evidence missing reached marker')
        before_loss=(root/'serial.log').read_text(errors='replace') if (root/'serial.log').is_file() else ''
        if 'HIL_STORAGE_OPERATION_SUCCESS' in before_loss:
            errors.append('power-loss evidence contains success before loss')
    errors.extend(_hil_control_artifact_errors(scenario,root,result))
    errors.extend(_hil_semantic_artifact_errors(scenario,root,result))
    return errors

def build_hil_storage_report(scenario,evidence_dir):
    root=Path(evidence_dir); errors=[]; result={}
    try:result=json.loads((root/'result.json').read_text())
    except (OSError,ValueError,json.JSONDecodeError):errors.append('invalid HIL result.json')
    errors.extend(validate_hil_storage_result(scenario,result))
    errors.extend(_hil_storage_artifact_errors(scenario,root,result))
    return {
        'scenario':scenario,'status':'PASS' if not errors else 'NOT_PASS',
        'capturedAt':datetime.now(timezone.utc).isoformat(),
        'validationErrors':errors,
        'cleanupInspection':result.get('cleanupInspection'),
        'finalStatus':result.get('finalStatus'),
    }

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
    p=argparse.ArgumentParser(); p.add_argument('scenario',nargs='?',choices=SCENARIOS); p.add_argument('--hil-storage-scenario',choices=HIL_STORAGE_SCENARIOS); p.add_argument('--evidence-dir',type=Path); p.add_argument('--output',type=Path); p.add_argument('--capture-script',type=Path); p.add_argument('--verifier-script',type=Path); p.add_argument('--self-test',action='store_true'); a=p.parse_args()
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
        hil_build={'sourceCommit':'a'*40,'profile':'hil','configEnabled':True,'sdkconfigSha256':'b'*64,'binarySha256':'c'*64,'elfSha256':'d'*64,'mapSha256':'e'*64,'archiveSha256':'f'*64,'binaryBytes':1,'appPartitionFreeBytes':1}
        hil_result={'scenario':HIL_POWER_LOSS_SCENARIO,'status':'PASS','deviceId':'28:84:85:85:1a:80','deviceUuid':'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee','connectionIdentity':{'deviceId':'28:84:85:85:1a:80','clientId':'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'},'cleanupVerified':True,'controllerInactive':True,'buildIdentity':hil_build,'cacheKey':'hil-task14/v1-'+'d'*64,'armSequence':1,'reachedSequence':2,'consumedSequence':3,'events':list(HIL_EVENT_ORDER),'operation':'sync','checkpoint':'before_commit_rename','faultAction':'pause','expectedProgress':0,'checkpointExercised':True,'triggerResponseAbsent':True,'triggerOutcome':None,'powerLoss':True,'checkpointReached':True,'successMarkerBeforeLoss':False,'rebootCaptured':True,'armClearedAfterReboot':True,'postRebootInspected':True,'retryStatus':'ready','triggerPendingAtMarker':True,'triggerPendingAtCutBoundary':True,'utcStart':'2026-07-17T00:00:00Z','checkpointReachedUtc':'2026-07-17T00:00:00.500000Z','powerCutBoundaryUtc':'2026-07-17T00:00:01Z','disconnectObservedUtc':'2026-07-17T00:00:01.500000Z','powerRemovalConfirmedUtc':'2026-07-17T00:00:02Z','utcEnd':'2026-07-17T00:00:03Z','disconnectAfterPowerCutBoundary':True}
        assert not validate_hil_storage_result(HIL_POWER_LOSS_SCENARIO,hil_result)
        invalid_timestamps=(
            ('checkpointReachedUtc',None),
            ('checkpointReachedUtc','not-a-timestamp'),
            ('checkpointReachedUtc','2026-07-17T00:00:00.500000+00:00'),
            ('checkpointReachedUtc','2026-07-16T23:59:59Z'),
            ('powerCutBoundaryUtc','2026-07-17T00:00:00.250000Z'),
            ('disconnectObservedUtc','2026-07-17T00:00:00.750000Z'),
            ('powerRemovalConfirmedUtc','2026-07-17T00:00:01.250000Z'),
            ('utcEnd','2026-07-17T00:00:01.750000Z'),
        )
        for field,value in invalid_timestamps:
            assert validate_hil_storage_result(HIL_POWER_LOSS_SCENARIO,{**hil_result,field:value})
        equal_timestamps=(
            ('utcStart','checkpointReachedUtc'),
            ('checkpointReachedUtc','powerCutBoundaryUtc'),
            ('powerCutBoundaryUtc','disconnectObservedUtc'),
            ('powerRemovalConfirmedUtc','utcEnd'),
        )
        for earlier,later in equal_timestamps:
            assert validate_hil_storage_result(HIL_POWER_LOSS_SCENARIO,{**hil_result,later:hil_result[earlier]})
        assert not validate_hil_storage_result(HIL_POWER_LOSS_SCENARIO,{**hil_result,'powerRemovalConfirmedUtc':hil_result['disconnectObservedUtc']})
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); checkpoint=root/'checkpoint-reached-utc.txt'; removed=root/'power-removed-utc.txt'
            checkpoint.write_text(hil_result['checkpointReachedUtc']+'\n')
            removed.write_text(hil_result['powerRemovalConfirmedUtc']+'\n')
            assert not _power_loss_timestamp_artifact_errors(hil_result,root)
            invalid_artifacts=(
                (checkpoint,'2026-07-17T00:00:01Z\n'),
                (removed,'2026-07-17T00:00:03Z\n'),
                (checkpoint,hil_result['checkpointReachedUtc']+' \n'),
                (removed,hil_result['powerRemovalConfirmedUtc']+'\n\n'),
            )
            for path,value in invalid_artifacts:
                checkpoint.write_text(hil_result['checkpointReachedUtc']+'\n')
                removed.write_text(hil_result['powerRemovalConfirmedUtc']+'\n')
                path.write_text(value)
                assert _power_loss_timestamp_artifact_errors(hil_result,root)
            checkpoint.unlink()
            assert _power_loss_timestamp_artifact_errors(hil_result,root)
            checkpoint.write_bytes(b'\xff\xfe\n')
            assert _power_loss_timestamp_artifact_errors(hil_result,root)
        assert validate_hil_storage_result(HIL_POWER_LOSS_SCENARIO,{**hil_result,'reachedSequence':1})
        print(json.dumps({'status':'PASS','scenarios':SCENARIOS,'hilStorageScenarios':HIL_STORAGE_SCENARIOS,'validAndInvalidCases':len(SCENARIOS)*2,'fixtureBindingCases':4,'scriptHashCases':2,'hilPowerTimestampCases':2+len(invalid_timestamps)+len(equal_timestamps),'hilTimestampArtifactCases':1+len(invalid_artifacts)+2})); return 0
    if a.hil_storage_scenario is not None:
        if a.evidence_dir is None:p.error('--evidence-dir is required for HIL storage validation')
        report=build_hil_storage_report(a.hil_storage_scenario,a.evidence_dir)
        data=json.dumps(report,indent=2)+'\n'; print(data,end='')
        if a.output:_atomic_text(a.output,data)
        return 0 if report['status']=='PASS' else 1
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
