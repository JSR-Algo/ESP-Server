import fs from 'node:fs';
import path from 'node:path';

const repoRoot = path.resolve(process.cwd(), '../..');

function readFromRepo(rel) {
  return fs.readFileSync(path.join(repoRoot, rel), 'utf8');
}

function expectContains(file, needle, reason) {
  const body = readFromRepo(file);
  if (!body.includes(needle)) {
    throw new Error(`${file} missing ${needle}: ${reason}`);
  }
}

function expectRegex(file, regex, reason) {
  const body = readFromRepo(file);
  if (!regex.test(body)) {
    throw new Error(`${file} missing ${regex}: ${reason}`);
  }
}

const noStore = 'no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0';
const immutable = 'public, max-age=31536000, immutable';

expectRegex('docs/docker/nginx.conf', /root\s+\/usr\/share\/nginx\/html;/, 'static root should be server-level for all SPA routes');
expectRegex('docs/docker/nginx.conf', /location\s+=\s+\/index\.html\s*{[\s\S]*Cache-Control\s+"no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0"/m, 'index.html must not be cached across deploys');
expectRegex('docs/docker/nginx.conf', /location\s+=\s+\/service-worker\.js\s*{[\s\S]*Cache-Control\s+"no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0"/m, 'service worker must update immediately across deploys');
expectRegex('docs/docker/nginx.conf', /location\s+=\s+\/manifest\.json\s*{[\s\S]*Cache-Control\s+"no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0"/m, 'manifest should not pin stale app metadata');
expectRegex('docs/docker/nginx.conf', /location\s+~\*\s+\^\/\(js\|css\|img\|fonts\)\/[\s\S]*Cache-Control\s+"public, max-age=31536000, immutable"/m, 'hashed static assets should be cached immutably');
expectRegex('docs/docker/nginx.conf', /location\s+\/\s*{[\s\S]*Cache-Control\s+"no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0"[\s\S]*try_files\s+\$uri\s+\$uri\/\s+\/index\.html;/m, 'SPA fallback must serve fresh shell');
expectRegex(
  'docs/docker/nginx.conf',
  /set\s+\$nest_auth\s+"__NESTJS_AUTH_HEADER__";[\s\S]*if\s*\(\$http_x_nest_authorization\)[\s\S]*rewrite\s+\^\/nestjs\/\(\.\*\)\$\s+\/\$1\s+break;/m,
  'Nest auth selection must run before the terminating rewrite break',
);

expectContains('docs/docker/nginx.conf', noStore, 'no-store policy must be explicit');
expectContains('docs/docker/nginx.conf', immutable, 'immutable policy must be explicit');

expectContains('main/manager-web/src/registerServiceWorker.js', 'registration.update()', 'registered service worker should proactively check for updates');
expectContains('main/manager-web/src/registerServiceWorker.js', 'SKIP_WAITING', 'new service worker should activate without stale UI waiting');
expectContains('main/manager-web/src/registerServiceWorker.js', 'controllerchange', 'clients should reload once after controller changes');

console.log('web cache policy contract OK');
