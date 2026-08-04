import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

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
expectContains('docs/docker/start.sh', 'source /cms-authority.sh');
expectContains('docs/docker/start.sh', 'configure_cms_authority');
const startScript = read('docs/docker/start.sh');
if (startScript.indexOf('configure_cms_authority') > startScript.indexOf('java -jar')) {
  throw new Error('CMS authority must fail closed before the local manager API starts');
}
expectContains('docs/docker/start.sh', 'NESTJS_ADMIN_PROXY_KEY_ESCAPED=');
expectContains('docs/docker/start.sh', '__NESTJS_ADMIN_PROXY_KEY__');
expectContains('docs/docker/start.sh', 'NESTJS_ADMIN_PROXY_KEY contains unsupported characters');
expectContains(
  'docs/docker/nginx.conf',
  'proxy_set_header X-TBOT-Admin-Key "__NESTJS_ADMIN_PROXY_KEY__";',
);
expectContains(
  'main/manager-web/vue.config.js',
  "const adminProxyKey = browserE2E ? '' : (process.env.NESTJS_ADMIN_PROXY_KEY || '');",
);
expectContains(
  'main/manager-web/vue.config.js',
  "proxyReq.setHeader('X-TBOT-Admin-Key', adminProxyKey);",
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
expectContains('deploy/redeploy-web.sh', 'PUBLIC_CMS_UPSTREAM_HOST');
expectContains('deploy/redeploy-web.sh', 'PUBLIC_CMS_UPSTREAM_SCHEME');
expectContains('deploy/redeploy-web.sh', 'TBOT_ALLOW_SPLIT_CMS_AUTHORITY');
expectContains('deploy/redeploy-web.sh', 'configure_cms_authority');
expectContains('deploy/redeploy-web.sh', 'NESTJS_AUTH_HEALTH_PATH="/nestjs/v1/admin/lesson-rollout-capabilities"');
expectContains('deploy/redeploy-web.sh', '[[ "${auth_status}" == "401" ]]');
expectContains('deploy/docker-compose.prod.yml', 'PUBLIC_CMS_UPSTREAM_HOST: ${PUBLIC_CMS_UPSTREAM_HOST:-${NESTJS_UPSTREAM_HOST:-tbot-backend-8wmh.onrender.com}}');
expectContains('deploy/docker-compose.prod.yml', 'PUBLIC_CMS_UPSTREAM_SCHEME: ${PUBLIC_CMS_UPSTREAM_SCHEME:-${NESTJS_UPSTREAM_SCHEME:-https}}');
expectContains('deploy/docker-compose.prod.yml', 'TBOT_ALLOW_SPLIT_CMS_AUTHORITY: ${TBOT_ALLOW_SPLIT_CMS_AUTHORITY:-false}');
expectContains('deploy/.env.example', 'PUBLIC_CMS_UPSTREAM_HOST=');
expectContains('deploy/.env.example', 'PUBLIC_CMS_UPSTREAM_SCHEME=');
expectContains('deploy/.env.example', 'TBOT_ALLOW_SPLIT_CMS_AUTHORITY=false');
expectNotContains('Dockerfile-web', 'NESTJS_ADMIN_PROXY_KEY');
expectNotContains('main/manager-web/src', 'NESTJS_ADMIN_PROXY_KEY');

const authorityScript = path.join(repoRoot, 'docs/docker/cms-authority.sh');
const runAuthorityCheck = (env) => spawnSync(
  'bash',
  ['-c', `source "${authorityScript}"; configure_cms_authority`],
  { env: { PATH: process.env.PATH, ...env }, encoding: 'utf8' },
);
const aligned = runAuthorityCheck({
  NESTJS_UPSTREAM_HOST: 'tbot-backend-8wmh.onrender.com',
  NESTJS_UPSTREAM_SCHEME: 'https',
  PUBLIC_CMS_UPSTREAM_HOST: 'tbot-backend-8wmh.onrender.com',
  PUBLIC_CMS_UPSTREAM_SCHEME: 'https',
});
if (aligned.status !== 0) throw new Error(`aligned CMS authority rejected: ${aligned.stderr}`);
const inherited = runAuthorityCheck({
  NESTJS_UPSTREAM_HOST: 'backend:3000',
  NESTJS_UPSTREAM_SCHEME: 'http',
});
if (inherited.status !== 0) throw new Error(`inherited CMS authority rejected: ${inherited.stderr}`);
const split = runAuthorityCheck({
  NESTJS_UPSTREAM_HOST: 'backend:3000',
  NESTJS_UPSTREAM_SCHEME: 'http',
  PUBLIC_CMS_UPSTREAM_HOST: 'tbot-backend-8wmh.onrender.com',
  PUBLIC_CMS_UPSTREAM_SCHEME: 'https',
});
if (split.status === 0 || !split.stderr.includes('CMS authority mismatch')) {
  throw new Error('split CMS authority must fail closed with a diagnostic');
}
const emergency = runAuthorityCheck({
  NESTJS_UPSTREAM_HOST: 'backend:3000',
  NESTJS_UPSTREAM_SCHEME: 'http',
  PUBLIC_CMS_UPSTREAM_HOST: 'tbot-backend-8wmh.onrender.com',
  PUBLIC_CMS_UPSTREAM_SCHEME: 'https',
  TBOT_ALLOW_SPLIT_CMS_AUTHORITY: 'true',
});
if (emergency.status !== 0) throw new Error(`explicit emergency split rejected: ${emergency.stderr}`);

console.log('admin proxy key wiring contract OK');
