# Course speech playout receipts

Canonical additive protocol for T15, negotiated with the device hello feature
`lessonAudioPlayoutAck: true`. The existing `lessonAudioDrainAck` / `drainId`
protocol remains independent. Legacy peers do not qualify speech-driven visuals.

For an active course runtime, the ESP provider captures runtime instance, visual
epoch, assignment, lesson session, activity, step sequence, and increasing local
playout sequence before sending TTS START. It binds these to a 32 lowercase
hexadecimal `playoutId`, the current response generation, socket, and transport
`session_id`. These IDs are ephemeral transport correlation, never media IDs or
canonical course identities. Model output and websocket delivery alone do not
start talking.

The ID contains a connection-owned random 64-bit prefix followed by an increasing
64-bit counter, both hexadecimal and zero-padded to 16 characters. It strictly
increases lexicographically within the transport epoch, including provider and
activity replacement. Counter exhaustion fails closed. Firmware retains a
high-water ID, not a bounded replay history; an old START cannot become valid
after enough replacements. Only an actual transport-epoch change resets that
high-water mark, and source/epoch fences still reject old sockets.

START and the corresponding normal STOP carry `playoutId` alongside existing
fields. The firmware binds the ID to its local audio generation. It sends:

```json
{"type":"tts_ack","state":"start","playoutId":"0123456789abcdef0000000000000001","playoutAtMs":100,"session_id":"transport-session"}
```

The START receipt is emitted exactly once after the first successful PCM
`OutputData` completion for that generation. `playoutAtMs` is the device monotonic
integer millisecond timestamp, in the range 0 through 9007199254740991. This
software output boundary is not an acoustic measurement. START without PCM, model
text, queued bytes, timers, and chunk gaps cannot produce a START receipt.

After normal STOP and the same generation's true drain (decode/PCM/in-flight
output and configured DMA tail settled), firmware emits the equivalent
`state: "stop"` receipt with its drain timestamp. It emits the existing `drainId`
ack separately when requested. STOP without first actual output does not establish
playout. The server rejects a stop before normal STOP dispatch, non-monotonic
timestamps, duplicate events, wrong sessions, obsolete sockets, runtime epochs,
activities, steps, and responses. Unknown receipt fields do not grant ownership.

The owned START queues the activity's authored looping `teach` phase. The owned
drain queues `listen`, without waiting for a talking clip's duration. Phase
prepare/start acknowledgements and existing lifecycle generation fences remain
required. Cue switching does not change captions, teaching objects, assessment
eligibility, mechanical actions, or microphone authorization.

Interrupt STOP carries the current ID when available and retires it before
awaited transport cleanup. Cancellation retires the visual via a separate
runtime cancellation hook, never successful playout completion. Replacement,
failure, disconnect, and reconnect retire old correlation; they cannot replay
entrance, old speech or pending cues. Firmware may handle an untagged explicit
interrupt as cancellation under existing ownership and generation safeguards.

Host protocol tests validate this envelope against the fixture
`main/tbot-server/tests/fixtures/course-mode/speech-playout.v1.json`; firmware
consumer tests must select that same file and record its hash. Physical T02
timing qualification requires device playout/cue timestamps and synchronized
acoustic/TFT capture. Server receipt timestamps use a different clock; subtracting
them from device timestamps does not establish latency or displayed FPS.
