# TVideo farm speech fixture provenance

These files are deterministic local system-TTS fixtures for the Google Live
robot smoke. They are not microphone captures or human recordings.

- Generator: macOS `/usr/bin/say`
- Voice: `Linh`
- Synthetic set rate: `180`
- Adult-category set rate: `150`
- Intermediate format: temporary AIFF
- Normalization: `ffmpeg -ac 1 -ar 24000 -c:a pcm_s16le`
- Final format: WAV PCM signed 16-bit little-endian, mono, 24000 Hz
- Human recordings: none
- Child recordings: none
- External speech datasets: none

The `adult_*` name denotes the harness's slower adult-voice category. It is
still macOS system-generated synthetic speech, not a consenting-human sample.
The exact per-file SHA-256 values and semantic fixture IDs are pinned in
`scripts/google_live_robot_soak.py` and verified before encoding any Opus
packet. Temporary AIFF files are not retained.

Both categories use the same semantic text; only the speaking rate changes:

| Label | Generated text |
|---|---|
| `lesson_start` | `bắt đầu bài học nông trại` |
| `target_answer` | `barn` |
| `meaning_bridge` | `nông trại` |
| `related_concept` | `hay` |
| `retry_coaching` | `thử lại nhẹ nhàng` |
| `target_correction` | `barn` |
| `hay_listen` | `nghe từ hay` |
| `hay_thinking` | `đang nghĩ về hay` |
| `hay_correct` | `hay chính xác` |
| `hay_celebrate` | `hoàn thành bài học hay` |

Reproduction template:

```bash
say -v Linh -r <rate> -o <source>_<label>.aiff "<text>"
ffmpeg -y -hide_banner -loglevel error \
  -i <source>_<label>.aiff \
  -ac 1 -ar 24000 -c:a pcm_s16le \
  <source>_<label>_24k_mono.wav
rm <source>_<label>.aiff
```
