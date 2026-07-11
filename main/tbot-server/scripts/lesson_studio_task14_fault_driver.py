#!/usr/bin/env python3
"""Fail-closed evidence collector; it never injects a fault or contacts production."""
import argparse, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

SCENARIOS=('cold','warm','offline','checksum','interrupted','power-loss','missing-optional','sd-full','slave-unavailable','rollback')
REQUIRED=('serial.log','server.log','command.txt','result.json')
def scenario_valid(s,r):
    checks={
      'cold': r.get('bytesDownloaded',0)>0 and r.get('ready') is True and r.get('checksumVerified') is True,
      'warm': r.get('cacheHit') is True and r.get('bytesDownloaded')==0 and r.get('ready') is True,
      'offline': r.get('networkAvailable') is False and r.get('completed') is True and r.get('source')=='sd',
      'checksum': r.get('mismatchDetected') is True and r.get('partialCleaned') is True and r.get('ready') is False,
      'interrupted': r.get('recovered') is True and r.get('partialCleaned') is True and r.get('ready') is True,
      'power-loss': r.get('recovered') is True and r.get('partialCleaned') is True and r.get('ready') is True,
      'missing-optional': r.get('optionalAssetMissing') is True and r.get('degraded') is True and r.get('advanced') is True,
      'sd-full': r.get('refused') is True and r.get('previousPackRetained') is True,
      'slave-unavailable': r.get('motionDegraded') is True and r.get('completed') is True,
      'rollback': r.get('activeVersion')==r.get('previousVersion') and r.get('activeChecksum')==r.get('previousChecksum') and r.get('ready') is True,
    }; return checks[s]
def main():
    p=argparse.ArgumentParser(); p.add_argument('scenario',choices=SCENARIOS); p.add_argument('--evidence-dir',required=True,type=Path); p.add_argument('--output',type=Path); p.add_argument('--self-test',action='store_true'); a=p.parse_args()
    if a.self_test:
        valid={'cold':{'bytesDownloaded':1,'ready':True,'checksumVerified':True},'warm':{'cacheHit':True,'bytesDownloaded':0,'ready':True},'offline':{'networkAvailable':False,'completed':True,'source':'sd'},'checksum':{'mismatchDetected':True,'partialCleaned':True,'ready':False},'interrupted':{'recovered':True,'partialCleaned':True,'ready':True},'power-loss':{'recovered':True,'partialCleaned':True,'ready':True},'missing-optional':{'optionalAssetMissing':True,'degraded':True,'advanced':True},'sd-full':{'refused':True,'previousPackRetained':True},'slave-unavailable':{'motionDegraded':True,'completed':True},'rollback':{'activeVersion':1,'previousVersion':1,'activeChecksum':'a','previousChecksum':'a','ready':True}}
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
