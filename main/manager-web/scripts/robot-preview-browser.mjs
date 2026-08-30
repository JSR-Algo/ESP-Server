import { existsSync } from 'node:fs';
import { userInfo } from 'node:os';
import { join } from 'node:path';

const PLAYWRIGHT_CHROMIUM_1223 = 'Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell';

export function findPinnedRobotPreviewChromium({
  accountHome = userInfo().homedir,
  exists = existsSync
} = {}) {
  const executable = join(accountHome, PLAYWRIGHT_CHROMIUM_1223);
  if (!exists(executable)) {
    throw new Error(`Pinned Playwright Chromium 1223 is required for exact robot preview screenshots: ${executable}`);
  }
  return executable;
}
