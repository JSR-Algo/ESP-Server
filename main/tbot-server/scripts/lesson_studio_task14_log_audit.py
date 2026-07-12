#!/usr/bin/env python3
import argparse, json, re
from collections import Counter
from pathlib import Path
MARKERS={
    'allocationFailure':r'alloc(?:ation)? failed|out of memory|malloc failed',
    'watchdog':r'watchdog|task wdt',
    'decodeFailure':r'decode failed|image decode error',
    'audioUnderrun':r'audio underrun|i2s.*underrun',
    'sequenceDivergence':r'lesson sequence divergence|sequence mismatch',
    'firmwareCrash':r'assert failed|stack overflow|corrupt heap|abort\(\) was called|loadprohibited|storeprohibited|illegalinstruction',
}
EVENT=re.compile(r'(?i)(lesson_progress(?:_[a-z]+)?|progress_posted(?:_[a-z]+)?)')
SESSION=re.compile(r'(?i)session(?:[_ ]?id)?["\']?\s*[:=]\s*["\']?([^,\s"\'}]+)')
STEP=re.compile(r'(?i)step(?:[_ ]?id)?["\']?\s*[:=]\s*["\']?([^,\s"\'}]+)')
SEQUENCE=re.compile(r'(?i)(?:sequence|seq)["\']?\s*[:=]\s*["\']?([^,\s"\'}]+)')
IDEMPOTENCY=re.compile(r'(?i)(?:idempotency[_ ]?key|event[_ ]?id)["\']?\s*[:=]\s*["\']?([^,\s"\'}]+)')
SEMANTIC_EVENT=re.compile(r'(?i)\bevent["\']?\s*[:=]\s*["\']?([^,\s"\'}]+)')
def progress_events(text):
    events=[]
    for line in text.splitlines():
        event_match=EVENT.search(line)
        if not event_match:continue
        try:
            payload=json.loads(line)
        except json.JSONDecodeError:
            payload={}
        if not isinstance(payload,dict):payload={}
        body=payload.get('body') if isinstance(payload.get('body'),dict) else {}
        semantic_event=body.get('event') or payload.get('event')
        semantic_match=SEMANTIC_EVENT.search(line)
        semantic_event=semantic_event or (semantic_match.group(1) if semantic_match else None)
        event_type=str(semantic_event or payload.get('type') or event_match.group(1)).lower()
        session=payload.get('session_id') or payload.get('sessionId') or payload.get('session')
        step=payload.get('step_id') or payload.get('stepId') or payload.get('step')
        sequence=payload.get('sequence') if payload.get('sequence') is not None else payload.get('seq')
        idempotency=payload.get('idempotency_key') or payload.get('idempotencyKey') or payload.get('event_id')
        session_match=SESSION.search(line); step_match=STEP.search(line)
        session=session or (session_match.group(1) if session_match else None)
        step=step or (step_match.group(1) if step_match else None)
        sequence_match=SEQUENCE.search(line); idempotency_match=IDEMPOTENCY.search(line)
        sequence=sequence if sequence is not None else (sequence_match.group(1) if sequence_match else None)
        idempotency=idempotency or (idempotency_match.group(1) if idempotency_match else None)
        identity=f'sequence:{sequence}' if sequence is not None else f'idempotency:{idempotency}' if idempotency else 'identity:missing'
        if session and step:events.append((event_type,str(session),str(step),identity))
    return events
def audit(text):
    findings={k:len(re.findall(v,text,re.I)) for k,v in MARKERS.items()}; events=progress_events(text); duplicates=sorted(x for x,count in Counter(events).items() if count>1); ok=not any(findings.values()) and not duplicates
    return {'status':'PASS' if ok else 'NOT_PASS','findings':findings,'duplicateProgress':[{'eventType':x[0],'session':x[1],'step':x[2],'identity':x[3]} for x in duplicates]}
def main():
    p=argparse.ArgumentParser(); p.add_argument('logs',nargs='*',type=Path); p.add_argument('--output',type=Path); p.add_argument('--self-test',action='store_true'); a=p.parse_args()
    if a.self_test:
        assert audit('lesson_progress session_id=s step_id=a')['status']=='PASS'
        for marker in ('malloc failed','stack overflow in task lesson','CORRUPT HEAP'):
            assert audit(marker)['status']=='NOT_PASS'
        print('self-test PASS'); return 0
    if not a.logs:p.error('logs required')
    r=audit('\n'.join(x.read_text(errors='replace') for x in a.logs)); data=json.dumps(r,indent=2)+'\n'; print(data,end=''); a.output and a.output.write_text(data); return 0 if r['status']=='PASS' else 1
if __name__=='__main__': raise SystemExit(main())
