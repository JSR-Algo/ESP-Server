#!/usr/bin/env python3
import argparse, json, re, sys
from pathlib import Path

TRANSITION = re.compile(r'(?i)(?:lesson_step_started|lesson_step_transition).*?(?:sequence|seq|step_index)["\']?\s*[:=]\s*["\']?(\d+)')
PSRAM = re.compile(r'(?i)(?:psram_free|free_psram)["\']?\s*[:=]\s*["\']?(\d+)')
SRAM = re.compile(r'(?i)(?:internal_min_free|minimum_internal_sram|min_internal_sram)["\']?\s*[:=]\s*["\']?(\d+)')
RESET = re.compile(r'(?i)(?:rst:0x|guru meditation|watchdog.*reset|brownout|firmware reset)')

def analyze(text, count=100, gate=20*1024, tolerance=64*1024):
    steps=[int(x) for x in TRANSITION.findall(text)]; ps=[int(x) for x in PSRAM.findall(text)]; sr=[int(x) for x in SRAM.findall(text)]
    leak=len(ps)>=3 and all(a>=b for a,b in zip(ps,ps[1:])) and ps[0]-ps[-1]>tolerance
    ordered=bool(steps) and all(current>previous for previous,current in zip(steps,steps[1:]))
    checks={'at_least_100_transitions':len(steps)>=count,'transition_sequence_strictly_increasing':ordered,'psram_samples_present':len(ps)>=3,'no_monotonic_psram_loss':not leak,'internal_sram_above_gate':bool(sr) and min(sr)>=gate,'no_firmware_reset':not RESET.search(text)}
    return {'status':'PASS' if all(checks.values()) else 'NOT_PASS','checks':checks,'metrics':{'transitions':len(steps),'psramFirst':ps[0] if ps else None,'psramLast':ps[-1] if ps else None,'internalSramMin':min(sr) if sr else None}}

def main():
    p=argparse.ArgumentParser(); p.add_argument('logs',nargs='*',type=Path); p.add_argument('--output',type=Path); p.add_argument('--self-test',action='store_true'); a=p.parse_args()
    if a.self_test:
        stable='\n'.join(f'lesson_step_transition sequence={i} psram_free={8000000-(i%4)*1024} internal_min_free=32768' for i in range(100))
        assert analyze(stable)['status']=='PASS'; print('self-test PASS'); return 0
    if not a.logs: p.error('logs required')
    report=analyze('\n'.join(x.read_text(errors='replace') for x in a.logs)); data=json.dumps(report,indent=2)+'\n'; print(data,end=''); a.output and a.output.write_text(data); return 0 if report['status']=='PASS' else 1
if __name__=='__main__': raise SystemExit(main())
