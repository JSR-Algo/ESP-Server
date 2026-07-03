# 2026-07-03 Real Robot Sample Guard Follow-up

## Scope

Robot `28:84:85:85:1a:80`, production ESP server image
`local/tbot-server:prod-lesson-guard-20260703T042243Z`, built-in interactive
sample lesson only.

## Result

Pass at `2026-07-03T04:55:30Z` to `2026-07-03T04:55:55Z` UTC.

- Owner replica: `current-tbot-esp32-server-2`.
- Nudge: `202`, body `{"data":{"mode":"sample","nudged":true}}`.
- `lesson_prepare` ACKed immediately; no prepare ACK timeout.
- `s3` and `s4` child-response windows opened.
- Internal `barn` responses were sent only after each window opened; both
  returned `handled=true`.
- Completion: `lesson_completed stepsCompleted=4`.
- Final metrics: `connections=1`, `alarms=0`, device present.
- Bad markers: `0` for lesson errors, step timeouts, prompt guard timeouts,
  child-response inactivity/retry, traceback/error, and disconnect.

## Notes

Opening `/dev/cu.usbmodem1101` reset the robot with `USB_UART_CHIP_RESET`, so
those serial captures are diagnostic only. The accepted proof is the controlled
server-side run above.
