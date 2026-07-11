#!/usr/bin/env python3
"""Fail-closed evidence collector; it never injects a fault or contacts production."""
import argparse, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

SCENARIOS=('preview-parity','cold','warm','offline','checksum','interrupted','power-loss','missing-optional','sd-full','slave-unavailable','rollback')
REQUIRED=('serial.log','server.log','command.txt','result.json')
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
    explicit_pass=result_data.get('status')=='PASS' and result_data.get('scenario')==a.scenario and scenario_valid(a.scenario,result_data)
    report={'scenario':a.scenario,'status':'PASS' if not missing and explicit_pass else 'NOT_PASS','capturedAt':datetime.now(timezone.utc).isoformat(),'missingEvidence':missing,'files':{name:{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for name,path in files.items() if path.is_file()}}
    data=json.dumps(report,indent=2)+'\n'; print(data,end=''); a.output and a.output.write_text(data); return 0 if report['status']=='PASS' else 1
if __name__=='__main__': raise SystemExit(main())
