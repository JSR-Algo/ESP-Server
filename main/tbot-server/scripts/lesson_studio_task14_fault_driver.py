#!/usr/bin/env python3
"""Fail-closed evidence collector; it never injects a fault or contacts production."""
import argparse, hashlib, json, re
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, UnidentifiedImageError

SCENARIOS=('preview-parity','cold','warm','offline','checksum','interrupted','power-loss','missing-optional','sd-full','slave-unavailable','rollback')
REQUIRED=('serial.log','server.log','command.txt','result.json')
SHA256=re.compile(r'^[0-9a-f]{64}$')
COMMIT=re.compile(r'^[0-9a-f]{7,40}$')
MAX_SCREENSHOT_BYTES=10*1024*1024
COMMON_FIELDS=(
    'utcStart','utcEnd','backendCommit','espServerCommit','firmwareCommit',
    'firmwareVersion','deviceId','assignmentId','assignmentVersion','lessonId',
    'lessonVersion','manifestChecksum','packChecksum','internalSramMin',
    'psramFirst','psramLast','screenshots','operator','commandExitCode','logMarkers',
)
SCENARIO_LOG_MARKERS={
    'preview-parity':('lesson_step_started','motion_preset'),
    'cold':('lesson_preload_ready','checksum_verified'),
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
def scenario_valid(s,r):
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

def validate_result(scenario,result,raw_logs,base_dir=None):
    if not isinstance(result,dict):return ['result.json must contain an object']
    errors=[f'missing common metadata: {name}' for name in COMMON_FIELDS if name not in result]
    if errors:return errors
    for name in ('backendCommit','espServerCommit','firmwareCommit'):
        if not isinstance(result[name],str) or not COMMIT.fullmatch(result[name]):errors.append(f'invalid {name}')
    for name in ('manifestChecksum','packChecksum'):
        if not isinstance(result[name],str) or not SHA256.fullmatch(result[name]):errors.append(f'invalid {name}')
    for name in ('firmwareVersion','deviceId','assignmentId','lessonId','operator'):
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
    if not scenario_valid(scenario,result):errors.append(f'{scenario} decisive signals are incomplete')
    return errors

def _stream_sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()

def _hash_file(path):
    return {'path':str(path),'sha256':_stream_sha256(path)}

def build_evidence_report(scenario,result,files,raw_logs,base_dir=None):
    errors=validate_result(scenario,result,raw_logs,base_dir)
    screenshots,_=_inspect_screenshots(result.get('screenshots') if isinstance(result,dict) else None,base_dir)
    return {
      'scenario':scenario,'status':'PASS' if not errors else 'NOT_PASS',
      'capturedAt':datetime.now(timezone.utc).isoformat(),'validationErrors':errors,
      'files':{name:_hash_file(path) for name,path in files.items() if path.is_file()},
      'screenshots':[{**_hash_file(item['path']),'role':item['role'],'type':item['type'],'width':item['dimensions'][0],'height':item['dimensions'][1]} for item in screenshots],
    }

def main():
    p=argparse.ArgumentParser(); p.add_argument('scenario',choices=SCENARIOS); p.add_argument('--evidence-dir',required=True,type=Path); p.add_argument('--output',type=Path); p.add_argument('--self-test',action='store_true'); a=p.parse_args()
    if a.self_test:
        valid={'preview-parity':{'previewLayerRects':{'background':[0,0,480,320]},'hardwareLayerRects':{'background':[0,0,480,320]},'previewWordText':'BARN','hardwareWordText':'BARN','previewPathOutcome':'correct','hardwarePathOutcome':'correct','previewMotionTimeline':['teach','listen'],'hardwareMotionTimeline':['teach','listen']},'cold':{'bytesDownloaded':1,'elapsedMs':1,'ready':True,'checksumVerified':True,'manifestChecksum':'a','packChecksum':'a'},'warm':{'cacheHit':True,'bytesDownloaded':0,'elapsedMs':1,'ready':True,'manifestChecksum':'a','packChecksum':'a'},'offline':{'networkAvailable':False,'completed':True,'source':'sd'},'checksum':{'mismatchDetected':True,'partialCleaned':True,'ready':False},'interrupted':{'recovered':True,'partialCleaned':True,'readyBeforeVerify':False,'readyAfterRecovery':True},'power-loss':{'recovered':True,'partialCleaned':True,'readyBeforeVerify':False,'readyAfterRecovery':True},'missing-optional':{'optionalAssetMissing':True,'degraded':True,'advanced':True,'logMarkers':['optional_asset_missing','render_degraded']},'sd-full':{'freeRatio':0.04,'refused':True,'activePackRetained':True,'previousPackRetained':True},'slave-unavailable':{'motionDegraded':True,'completed':True,'logMarkers':['motion_degraded']},'rollback':{'activeVersion':1,'previousVersion':1,'activeChecksum':'a','previousChecksum':'a','oldFilesReattested':True,'ready':True}}
        for scenario in SCENARIOS: assert scenario_valid(scenario,valid[scenario]); assert not scenario_valid(scenario,{})
        print(json.dumps({'status':'PASS','scenarios':SCENARIOS,'validAndInvalidCases':len(SCENARIOS)*2})); return 0
    files={name:a.evidence_dir/name for name in REQUIRED}; missing=[name for name,path in files.items() if not path.is_file() or path.stat().st_size==0]
    result_data={}
    if not missing:
        try: result_data=json.loads(files['result.json'].read_text())
        except Exception: missing.append('valid result.json')
    raw_logs='\n'.join(files[name].read_text(errors='replace') for name in ('serial.log','server.log') if files[name].is_file())
    report=build_evidence_report(a.scenario,result_data,files,raw_logs,a.evidence_dir)
    report['missingEvidence']=missing
    if missing:report['status']='NOT_PASS'
    data=json.dumps(report,indent=2)+'\n'; print(data,end=''); a.output and a.output.write_text(data); return 0 if report['status']=='PASS' else 1
if __name__=='__main__': raise SystemExit(main())
