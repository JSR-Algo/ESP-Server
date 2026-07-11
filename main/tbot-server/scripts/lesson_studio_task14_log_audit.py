#!/usr/bin/env python3
import argparse, json, re
from pathlib import Path
MARKERS={'allocationFailure':r'alloc(?:ation)? failed|out of memory|malloc failed','watchdog':r'watchdog|task wdt','decodeFailure':r'decode failed|image decode error','audioUnderrun':r'audio underrun|i2s.*underrun','sequenceDivergence':r'lesson sequence divergence|sequence mismatch'}
EVENT=re.compile(r'(?i)(?:lesson_progress|progress_posted)')
SESSION=re.compile(r'(?i)session(?:[_ ]?id)?["\']?\s*[:=]\s*["\']?([^,\s"\'}]+)')
STEP=re.compile(r'(?i)step(?:[_ ]?id)?["\']?\s*[:=]\s*["\']?([^,\s"\'}]+)')
def progress_events(text):
    events=[]
    for line in text.splitlines():
        if not EVENT.search(line):continue
        try:
            payload=json.loads(line)
        except json.JSONDecodeError:
            payload={}
        session=payload.get('session_id') or payload.get('session')
        step=payload.get('step_id') or payload.get('step')
        session_match=SESSION.search(line); step_match=STEP.search(line)
        session=session or (session_match.group(1) if session_match else None)
        step=step or (step_match.group(1) if step_match else None)
        if session and step:events.append((str(session),str(step)))
    return events
def audit(text):
    findings={k:len(re.findall(v,text,re.I)) for k,v in MARKERS.items()}; events=progress_events(text); duplicates=sorted({x for x in events if events.count(x)>1}); ok=not any(findings.values()) and not duplicates
    return {'status':'PASS' if ok else 'NOT_PASS','findings':findings,'duplicateProgress':[{'session':x[0],'step':x[1]} for x in duplicates]}
def main():
    p=argparse.ArgumentParser(); p.add_argument('logs',nargs='*',type=Path); p.add_argument('--output',type=Path); p.add_argument('--self-test',action='store_true'); a=p.parse_args()
    if a.self_test: assert audit('lesson_progress session_id=s step_id=a')['status']=='PASS'; assert audit('malloc failed')['status']=='NOT_PASS'; print('self-test PASS'); return 0
    if not a.logs:p.error('logs required')
    r=audit('\n'.join(x.read_text(errors='replace') for x in a.logs)); data=json.dumps(r,indent=2)+'\n'; print(data,end=''); a.output and a.output.write_text(data); return 0 if r['status']=='PASS' else 1
if __name__=='__main__': raise SystemExit(main())
