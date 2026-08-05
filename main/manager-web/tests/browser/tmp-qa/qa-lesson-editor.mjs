import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';

const BASE = 'http://localhost:8001';
const SHOT_DIR = '/tmp/farm-v8-diag/screenshots';

const MANAGER_USER = 'lesson_admin_e2e';
const MANAGER_PASSWORD = 'TbotE2E!2026';
const AUTHOR_EMAIL = 'lesson-author-e2e@local.invalid';
const AUTHOR_PASSWORD = 'TbotAuthorE2E!2026';

function redisCaptchaFor(uuid) {
  const out = execFileSync('docker', [
    'exec', 'tbot-esp32-server-redis', 'redis-cli', 'GET', `sys:captcha:${uuid}`,
  ], { encoding: 'utf8' }).trim();
  // RedisTemplate serializes String values as JSON, so a plain uuid comes back
  // wrapped in quotes (e.g. "8avt2").
  return out.replace(/^"(.*)"$/, '$1');
}

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const consoleErrors = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  page.on('pageerror', (err) => consoleErrors.push('pageerror: ' + err.message));
  const httpErrors = [];
  page.on('response', (response) => {
    if (response.status() >= 400) httpErrors.push(`${response.status()} ${response.url()}`);
  });

  let latestCaptchaUuid = null;
  page.on('response', (response) => {
    if (response.url().includes('/tbot/user/captcha?uuid=')) {
      latestCaptchaUuid = new URL(response.url()).searchParams.get('uuid');
    }
  });

  console.log('--- Navigating to login ---');
  await page.goto(BASE + '/#/login', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => Boolean(document.querySelector('img[alt="Verification code"]')));
  await page.screenshot({ path: `${SHOT_DIR}/01-login.png` });

  console.log('--- Filling outer manager-web login ---');
  await page.getByTestId('manager-login-username').fill(MANAGER_USER);
  await page.getByTestId('manager-login-password').fill(MANAGER_PASSWORD);

  if (!latestCaptchaUuid) throw new Error('captcha uuid was never observed');
  const code = redisCaptchaFor(latestCaptchaUuid);
  console.log('captcha uuid:', latestCaptchaUuid, 'code:', code);
  await page.getByTestId('manager-login-captcha').fill(code);

  const managerLogin = page.waitForResponse((r) => r.url().includes('/tbot/user/login') && r.request().method() === 'POST');
  await page.getByTestId('manager-login-submit').click();
  const managerResp = await managerLogin;
  console.log('manager login status:', managerResp.status());
  const managerBody = await managerResp.json().catch(() => null);
  console.log('manager login body code:', managerBody && managerBody.code, managerBody && managerBody.msg);

  if (managerResp.status() !== 200 || (managerBody && managerBody.code !== 0)) {
    await page.screenshot({ path: `${SHOT_DIR}/02-login-failed.png` });
    console.log('LOGIN FAILED, aborting.');
    await browser.close();
    process.exit(1);
  }

  await page.waitForURL(/#\/home$/, { timeout: 15000 });
  await page.screenshot({ path: `${SHOT_DIR}/03-home.png` });
  console.log('--- Outer login succeeded, at:', page.url());

  console.log('--- Navigating to course management ---');
  const coursesReqPromise = page.waitForResponse((r) => r.url().includes('/nestjs/v1/admin/courses') && r.request().method() === 'GET');
  await page.goto(BASE + '/#/course-management', { waitUntil: 'networkidle' });
  await page.screenshot({ path: `${SHOT_DIR}/04-course-management.png` });

  const authorDialog = page.getByRole('dialog', { name: /sign in as author/i });
  const dialogVisible = await authorDialog.isVisible().catch(() => false);
  console.log('author dialog visible (expect false: VUE_APP_NEST_AUTH_DISABLED=true bypasses it):', dialogVisible);

  const coursesResp = await coursesReqPromise;
  console.log('courses list status:', coursesResp.status());
  const coursesBody = await coursesResp.json().catch(() => null);
  console.log('courses list body:', JSON.stringify(coursesBody).slice(0, 1000));

  const bodyText = await page.textContent('body');
  console.log('Course management body snippet:', bodyText.slice(0, 800));

  const farmCourse = (coursesBody && coursesBody.data || []).find((c) => c.course_key === 'tvideo-raw-code-farm-8m');
  if (!farmCourse) throw new Error('Farm course not found in courses list response');
  console.log('Farm course:', farmCourse.id, farmCourse.title, farmCourse.status);

  console.log('--- Navigating to Farm course lessons ---');
  const lessonsReqPromise = page.waitForResponse((r) => r.url().includes('/nestjs/v1/admin/courses/') && r.url().includes('/lessons') && r.request().method() === 'GET');
  await page.goto(`${BASE}/#/course-lessons?courseId=${farmCourse.id}&courseKey=${farmCourse.course_key}&title=${encodeURIComponent(farmCourse.title)}`, { waitUntil: 'networkidle' });
  const lessonsResp = await lessonsReqPromise;
  console.log('lessons list status:', lessonsResp.status());
  const lessonsBody = await lessonsResp.json().catch(() => null);
  console.log('lessons list body:', JSON.stringify(lessonsBody).slice(0, 2000));
  await page.screenshot({ path: `${SHOT_DIR}/05-course-lessons.png` });

  const lessonRows = (lessonsBody && (lessonsBody.data || lessonsBody)) || [];
  const farmLesson = Array.isArray(lessonRows)
    ? lessonRows.find((l) => l.lesson_key === 'tvideo-farm-real-assets-v2' || l.lessonKey === 'tvideo-farm-real-assets-v2')
    : null;
  if (!farmLesson) {
    console.log('Farm lesson not found in lessons list; dumping full body for diagnosis.');
    console.log(JSON.stringify(lessonsBody, null, 2));
  } else {
    console.log('Farm lesson:', JSON.stringify(farmLesson).slice(0, 500));
  }

  const lessonId = farmLesson ? (farmLesson.id || farmLesson.lessonId) : null;
  if (lessonId) {
    console.log('--- Navigating to lesson editor ---');
    const lessonDetailReqPromise = page.waitForResponse((r) => r.url().includes(`/nestjs/v1/admin/lessons/${lessonId}`) && r.request().method() === 'GET');
    await page.goto(`${BASE}/#/lesson-editor?lessonId=${lessonId}&courseId=${farmCourse.id}&courseTitle=${encodeURIComponent(farmCourse.title)}`, { waitUntil: 'networkidle' });
    const lessonDetailResp = await lessonDetailReqPromise.catch((e) => { console.log('lesson detail wait failed:', e.message); return null; });
    if (lessonDetailResp) {
      console.log('lesson detail status:', lessonDetailResp.status());
      const lessonDetailBody = await lessonDetailResp.json().catch(() => null);
      console.log('lesson detail body cues count:', lessonDetailBody && lessonDetailBody.cues && lessonDetailBody.cues.length);
    }
    await page.waitForTimeout(1000);
    await page.screenshot({ path: `${SHOT_DIR}/06-lesson-editor.png`, fullPage: true });

    const editorBodyText = await page.textContent('body');
    console.log('Lesson editor body snippet:', editorBodyText.slice(0, 1500));

    console.log('--- Inspecting canonical source video element ---');
    const videoInfo = await page.evaluate(() => {
      const el = document.querySelector('[data-testid="canonical-source-video"]');
      if (!el) return null;
      const source = el.querySelector('source');
      return {
        tag: el.tagName,
        currentSrc: el.currentSrc,
        srcAttr: el.getAttribute('src'),
        sourceSrc: source && source.getAttribute('src'),
        sourceType: source && source.getAttribute('type'),
        videoWidth: el.videoWidth,
        videoHeight: el.videoHeight,
      };
    });
    console.log('canonical-source-video:', JSON.stringify(videoInfo));

    console.log('--- Checking cue status grid (19 rows) ---');
    const cueRows = await page.evaluate(() => {
      const grid = document.querySelector('.cue-status__grid');
      if (!grid) return null;
      return Array.from(grid.querySelectorAll('article')).map((a) => ({
        cueId: a.querySelector('.mono') && a.querySelector('.mono').textContent,
        state: a.querySelector('.el-tag') && a.querySelector('.el-tag').textContent.trim(),
      }));
    });
    console.log('cue rows count:', cueRows ? cueRows.length : null);
    console.log('cue rows:', JSON.stringify(cueRows));

    console.log('--- Checking a11y: tablist / roles on TVideo journey editor ---');
    const a11y = await page.evaluate(() => {
      const tablist = document.querySelector('[role="tablist"]');
      return {
        tablistAriaLabel: tablist && tablist.getAttribute('aria-label'),
        tabButtons: tablist ? Array.from(tablist.querySelectorAll('button')).map((b) => ({
          text: b.textContent.trim(), ariaSelected: b.getAttribute('aria-selected'),
        })) : [],
      };
    });
    console.log('a11y tablist:', JSON.stringify(a11y));

    console.log('--- Clicking "Generate preview" (expect graceful 409 handling, no crash) ---');
    const genPreviewBtn = page.getByRole('button', { name: 'Generate preview' });
    const genPreviewVisible = await genPreviewBtn.isVisible().catch(() => false);
    console.log('Generate preview button visible:', genPreviewVisible);
    if (genPreviewVisible) {
      const previewReqPromise = page.waitForResponse((r) => r.url().includes('manifest-preview') && r.request().method() === 'GET');
      await genPreviewBtn.click();
      const previewResp = await previewReqPromise.catch((e) => { console.log('preview wait failed:', e.message); return null; });
      if (previewResp) console.log('manifest-preview click status:', previewResp.status());
      await page.waitForTimeout(800);
      const errorToast = await page.locator('.el-message--error').textContent().catch(() => null);
      console.log('error toast after Generate preview click:', errorToast);
    }
    await page.screenshot({ path: `${SHOT_DIR}/07-after-generate-preview.png`, fullPage: true });

    console.log('--- Responsive check: mobile viewport ---');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${SHOT_DIR}/08-mobile-viewport.png`, fullPage: true });
    await page.setViewportSize({ width: 1440, height: 900 });
  }

  await browser.close();
  console.log('HTTP ERRORS:', JSON.stringify(httpErrors, null, 2));
  console.log('CONSOLE ERRORS:', JSON.stringify(consoleErrors, null, 2));
}

main().catch((e) => { console.error(e); process.exit(1); });
