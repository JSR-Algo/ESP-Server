// Only the targets verified by QA-03's normal-TLS readback are accepted.
// A preview requires its own reviewed target receipt before extending this list.
function verifiedOrigin(raw, expected) {
  if (!raw) throw new Error('An explicit deployed URL is required');
  let url;
  try { url = new URL(raw); } catch (_) { throw new Error('Expected verified HTTPS origin'); }
  if (url.origin !== expected || url.username || url.password || url.pathname !== '/'
      || url.search || url.hash) throw new Error('Expected verified HTTPS origin');
  return url.origin;
}
function deployedReadOnlyTargets(env) {
  return {
    web: verifiedOrigin(env.ADMIN_COURSE_DEPLOYED_WEB_URL, 'https://admin.tjbot.vn'),
    api: verifiedOrigin(env.ADMIN_COURSE_DEPLOYED_API_URL, 'https://backend.tjbot.vn'),
  };
}
module.exports = { deployedReadOnlyTargets };
