'use strict';

const { execFileSync } = require('node:child_process');
const { mkdirSync, statSync } = require('node:fs');
const { resolve } = require('node:path');

const root = process.env.TASK4_ASSIGNMENT_MEDIA_ROOT;
const outputRoot = resolve(__dirname, '../output');
if (!root || !resolve(root).startsWith(`${outputRoot}/`)) {
  throw new Error('TASK4_ASSIGNMENT_MEDIA_ROOT must be an explicit path under manager-web/output');
}
const durations = [600, 1100, 1200, 1300, 1400, 1600, 2600, 3000, 9500];
const templateRoot = resolve(root, 'templates');
mkdirSync(templateRoot, { recursive: true });

for (const durationMs of durations) {
  const output = resolve(templateRoot, `${durationMs}.mp4`);
  execFileSync('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-y',
    '-f', 'lavfi', '-i', `color=c=#1b6f5a:s=480x320:r=10:d=${durationMs / 1000}`,
    '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', output,
  ], { stdio: 'inherit' });
  const probe = JSON.parse(execFileSync('ffprobe', [
    '-v', 'error', '-select_streams', 'v:0',
    '-show_entries', 'stream=codec_name,width,height,r_frame_rate,nb_frames:format=duration',
    '-of', 'json', output,
  ], { encoding: 'utf8' }));
  const stream = probe.streams && probe.streams[0];
  const actualMs = Math.round(Number(probe.format && probe.format.duration) * 1000);
  if (!stream || stream.codec_name !== 'h264' || stream.width !== 480 || stream.height !== 320
      || stream.r_frame_rate !== '10/1' || Number(stream.nb_frames) !== durationMs / 100
      || actualMs !== durationMs || statSync(output).size <= 0) {
    throw new Error(`invalid H264 template ${durationMs}: ${JSON.stringify(probe)}`);
  }
}
