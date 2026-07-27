import fs from 'node:fs';
import path from 'node:path';

const repoRoot = path.resolve(process.cwd(), '../..');

function read(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), 'utf8');
}

function expectContains(relativePath, needle) {
  if (!read(relativePath).includes(needle)) {
    throw new Error(`${relativePath} missing ${needle}`);
  }
}

function collectFiles(relativePath) {
  const absolutePath = path.join(repoRoot, relativePath);
  const stat = fs.statSync(absolutePath);
  if (stat.isFile()) return [absolutePath];
  return fs.readdirSync(absolutePath, { withFileTypes: true }).flatMap((entry) => {
    const child = path.join(absolutePath, entry.name);
    return entry.isDirectory()
      ? collectFiles(path.relative(repoRoot, child))
      : [child];
  });
}

function expectNotContains(relativePath, needle) {
  for (const file of collectFiles(relativePath)) {
    if (fs.readFileSync(file, 'utf8').includes(needle)) {
      throw new Error(`${path.relative(repoRoot, file)} must not contain ${needle}`);
    }
  }
}

function extractNginxLocation(config, marker) {
  const start = config.indexOf(marker);
  if (start === -1) throw new Error(`docs/docker/nginx.conf missing ${marker}`);

  const openingBrace = config.indexOf('{', start);
  let depth = 0;
  for (let index = openingBrace; index < config.length; index += 1) {
    if (config[index] === '{') depth += 1;
    if (config[index] === '}') depth -= 1;
    if (depth === 0) return config.slice(start, index + 1);
  }

  throw new Error(`docs/docker/nginx.conf has unterminated ${marker}`);
}

expectContains('docs/docker/start.sh', ': "${NESTJS_ADMIN_PROXY_KEY:=}"');
expectContains('docs/docker/start.sh', 'NESTJS_ADMIN_PROXY_KEY_ESCAPED=');
expectContains('docs/docker/start.sh', '__NESTJS_ADMIN_PROXY_KEY__');
expectContains('docs/docker/start.sh', 'NESTJS_ADMIN_PROXY_KEY contains unsupported characters');
expectContains(
  'docs/docker/nginx.conf',
  'proxy_set_header X-TBOT-Admin-Key "__NESTJS_ADMIN_PROXY_KEY__";',
);
expectContains('docs/docker/nginx.conf', 'location = /_nestjs_manager_auth {');
expectContains(
  'docs/docker/nginx.conf',
  'proxy_pass http://127.0.0.1:8003/tbot/user/proxy-auth;',
);
expectContains(
  'docs/docker/nginx.conf',
  'proxy_set_header Authorization $http_authorization;',
);
expectContains('docs/docker/nginx.conf', 'auth_request /_nestjs_manager_auth;');
const nestjsLocation = extractNginxLocation(read('docs/docker/nginx.conf'), 'location /nestjs/');
if (/\bauth_basic\b/.test(nestjsLocation)) {
  throw new Error(
    'docs/docker/nginx.conf /nestjs/ must not consume the manager Bearer Authorization header with auth_basic',
  );
}
if (!nestjsLocation.includes('auth_request_set $manager_auth_status $upstream_status;')) {
  throw new Error('docs/docker/nginx.conf /nestjs/ must capture the manager auth subrequest status');
}
if (!nestjsLocation.includes('add_header X-TBOT-Manager-Auth-Status $manager_auth_status always;')) {
  throw new Error('docs/docker/nginx.conf /nestjs/ must expose the manager auth status on every response');
}
expectNotContains('docs/docker/start.sh', 'NESTJS_BASIC_HTPASSWD');
expectNotContains('deploy/docker-compose.prod.yml', 'NESTJS_BASIC_HTPASSWD');
expectContains(
  'deploy/docker-compose.prod.yml',
  'NESTJS_ADMIN_PROXY_KEY: ${NESTJS_ADMIN_PROXY_KEY:-}',
);
expectContains('deploy/.env.example', 'NESTJS_ADMIN_PROXY_KEY=');
expectContains('deploy/.env.example', 'NESTJS_UPSTREAM_SCHEME=');
expectContains('deploy/redeploy-web.sh', 'NESTJS_ADMIN_PROXY_KEY');
expectNotContains('Dockerfile-web', 'NESTJS_ADMIN_PROXY_KEY');
expectNotContains('main/manager-web/src', 'NESTJS_ADMIN_PROXY_KEY');

console.log('admin proxy key wiring contract OK');
