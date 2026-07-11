#!/usr/bin/env python3
"""Fail-closed evidence collector; it never injects a fault or contacts production."""
import argparse, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

SCENARIOS=('cold','warm','offline','checksum','interrupted','power-loss','missing-optional','sd-full','slave-unavailable','rollback')
REQUIRED=('serial.log','server.log','command.txt','result.json')
def main():
    p=argparse.ArgumentParser(); p.add_argument('scenario',choices=SCENARIOS); p.add_argument('--evidence-dir',required=True,type=Path); p.add_argument('--output',type=Path); p.add_argument('--self-test',action='store_true'); a=p.parse_args()
    if a.self_test: print(json.dumps({'status':'PASS','scenarios':SCENARIOS})); return 0
    files={name:a.evidence_dir/name for name in REQUIRED}; missing=[name for name,path in files.items() if not path.is_file() or path.stat().st_size==0]
    result_data={}
    if not missing:
        try: result_data=json.loads(files['result.json'].read_text())
        except Exception: missing.append('valid result.json')
    explicit_pass=result_data.get('status')=='PASS' and result_data.get('scenario')==a.scenario
    report={'scenario':a.scenario,'status':'PASS' if not missing and explicit_pass else 'NOT_PASS','capturedAt':datetime.now(timezone.utc).isoformat(),'missingEvidence':missing,'files':{name:{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for name,path in files.items() if path.is_file()}}
    data=json.dumps(report,indent=2)+'\n'; print(data,end=''); a.output and a.output.write_text(data); return 0 if report['status']=='PASS' else 1
if __name__=='__main__': raise SystemExit(main())
