'use strict';

const { createServer } = require('node:https');
const { createReadStream, readFileSync } = require('node:fs');
const { stat } = require('node:fs/promises');
const { extname, resolve, sep } = require('node:path');

const root = resolve(process.env.TASK4_ASSIGNMENT_MEDIA_ROOT || '/task4-media');
const sourceRoot = resolve(process.env.TASK4_ASSIGNMENT_SOURCE_ROOT || '/task4-source');
const port = Number(process.env.TASK4_ASSIGNMENT_MEDIA_PORT || 8443);
const key = readFileSync(process.env.TASK4_ASSIGNMENT_TLS_KEY || '/task4-tls/key.pem');
const cert = readFileSync(process.env.TASK4_ASSIGNMENT_TLS_CERT || '/task4-tls/cert.pem');
const types = { '.mp4': 'video/mp4', '.trgb': 'application/vnd.tbot.rgb565-indexed' };

createServer({ key, cert }, async (request, response) => {
  try {
    const pathname = decodeURIComponent(new URL(request.url, 'https://localhost').pathname);
    const sourceMappings = [
      ['/tvideo-demo/asset-manifest.json', resolve(sourceRoot, 'asset-manifest.json')],
      ['/tvideo-demo/admin/', resolve(sourceRoot, 'admin')],
      ['/tvideo-demo/esp-tft/', resolve(sourceRoot, 'esp-tft')],
      ['/tvideo-demo/assets/', resolve(sourceRoot, 'assets')],
    ];
    const mapping = sourceMappings.find(([prefix]) => pathname === prefix || pathname.startsWith(prefix));
    const targetRoot = mapping ? mapping[1] : root;
    const suffix = mapping && pathname !== mapping[0] ? pathname.slice(mapping[0].length) : '';
    const target = mapping ? resolve(targetRoot, suffix) : resolve(root, `.${pathname}`);
    if (target !== targetRoot && !target.startsWith(`${targetRoot}${sep}`)) throw new Error('unsafe path');
    const info = await stat(target);
    if (!info.isFile()) throw new Error('not a file');
    response.writeHead(200, {
      'Content-Type': types[extname(target)] || 'application/octet-stream',
      'Content-Length': info.size,
      'Cache-Control': 'no-store',
    });
    createReadStream(target).pipe(response);
  } catch {
    response.writeHead(404, { 'Content-Type': 'text/plain' });
    response.end('not found');
  }
}).listen(port, '0.0.0.0');
