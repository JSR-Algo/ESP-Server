#!/usr/bin/env python3
"""Fail-closed evidence collector; it never injects a fault or contacts production."""
import argparse, hashlib, json, re
from datetime import datetime, timezone
from pathlib import Path

SCENARIOS=('preview-parity','cold','warm','offline','checksum','interrupted','power-loss','missing-optional','sd-full','slave-unavailable','rollback')
REQUIRED=('serial.log','server.log','command.txt','result.json')
SHA256=re.compile(r'^[0-9a-f]{64}$')
COMMIT=re.compile(r'^[0-9a-f]{7,40}$')
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
    return candidate if candidate.is_absolute() or base_dir is None else base_dir/candidate

def validate_result(scenario,result,raw_logs,base_dir=None):
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
    screenshots=result.get('screenshots')
    if not isinstance(screenshots,list) or not screenshots or any(not _evidence_path(path,base_dir).is_file() or _evidence_path(path,base_dir).stat().st_size == 0 for path in screenshots):
        errors.append('screenshots must reference at least one non-empty existing file')
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

def _hash_file(path):
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

def build_evidence_report(scenario,result,files,raw_logs,base_dir=None):
    errors=validate_result(scenario,result,raw_logs,base_dir)
    return {
      'scenario':scenario,'status':'PASS' if not errors else 'NOT_PASS',
      'capturedAt':datetime.now(timezone.utc).isoformat(),'validationErrors':errors,
      'files':{name:_hash_file(path) for name,path in files.items() if path.is_file()},
      'screenshots':[_hash_file(_evidence_path(path,base_dir)) for path in result.get('screenshots',[]) if _evidence_path(path,base_dir).is_file()],
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
