#!/usr/bin/env node
// scripts/x-publish.mjs
// X (Twitter) compose helper: login (one-time) + publish (fill compose, stop before Post).
// NEVER clicks Post — irreversible action stays with the human.
//
// Usage:
//   node scripts/x-publish.mjs login
//   node scripts/x-publish.mjs publish "<text>" [image1 image2 ...]
//
// Env:
//   BURNP_PUBLISH_STATE    storage state path (default: ~/.burnkit/publish-state/x.json)
//   BURNP_PUBLISH_BROWSER  Chrome executable path

import { chromium } from 'playwright';
import { homedir } from 'os';
import { join, dirname } from 'path';
import { existsSync, mkdirSync, chmodSync } from 'fs';

const DEFAULT_STATE = join(homedir(), '.burnkit', 'publish-state', 'x.json');
const DEFAULT_BROWSER = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

const statePath = process.env.BURNP_PUBLISH_STATE || DEFAULT_STATE;
const browserPath = process.env.BURNP_PUBLISH_BROWSER || DEFAULT_BROWSER;

const log = (...a) => console.log('[x-publish]', ...a);
const die = (msg, code = 1) => { console.error(`[x-publish] ${msg}`); process.exit(code); };

function ensureDir(p) { mkdirSync(dirname(p), { recursive: true }); }

function checkBrowser() {
  if (!existsSync(browserPath)) {
    die(`Chrome not found at ${browserPath}. Set BURNP_PUBLISH_BROWSER to override.`);
  }
}

async function cmdLogin() {
  ensureDir(statePath);
  checkBrowser();
  // Plan C: launch a NATIVE Chrome via macOS `open` (no Playwright flags at all),
  // then connect over CDP. X's anti-bot only blocks the Playwright-launched
  // Chrome; a hand-launched one looks identical to a user's normal browser.
  const debugPort = 9223;
  const userDataDir = join(dirname(statePath), 'chrome-profile');
  mkdirSync(userDataDir, { recursive: true });

  log('launching NATIVE Chrome via `open` (no Playwright automation flags)');
  log(`profile dir:         ${userDataDir}`);
  log(`debug port:          ${debugPort}`);
  log(`storageState target: ${statePath}`);

  const { execFileSync } = await import('child_process');
  execFileSync('open', [
    '-na', 'Google Chrome',
    '--args',
    `--user-data-dir=${userDataDir}`,
    `--remote-debugging-port=${debugPort}`,
    '--no-first-run',
    '--no-default-browser-check',
    'https://x.com/login',
  ]);

  log('waiting for Chrome CDP endpoint to come up...');
  const cdpUrl = `http://127.0.0.1:${debugPort}`;
  let browser = null;
  const cdpDeadline = Date.now() + 30 * 1000;
  while (Date.now() < cdpDeadline) {
    try {
      const res = await fetch(`${cdpUrl}/json/version`);
      if (res.ok) { browser = await chromium.connectOverCDP(cdpUrl); break; }
    } catch { /* not ready yet */ }
    await new Promise((r) => setTimeout(r, 500));
  }
  if (!browser) die(`Chrome CDP did not come up at ${cdpUrl} within 30s`);

  const ctx = browser.contexts()[0];
  // Open a fresh tab pointed at the login page in case the launched tab navigated away
  let page = ctx.pages().find((p) => p.url().includes('x.com')) || ctx.pages()[0];
  if (!page || !page.url().includes('x.com')) {
    page = await ctx.newPage();
    await page.goto('https://x.com/login', { waitUntil: 'domcontentloaded' });
  }

  log('waiting for login (Ctrl+C to abort, up to 15 min)...');
  log('detecting login by auth_token cookie presence');
  const deadline = Date.now() + 15 * 60 * 1000;
  let loggedIn = false;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2000));
    try {
      const cookies = await ctx.cookies();
      if (cookies.some((c) => c.name === 'auth_token')) {
        loggedIn = true;
        break;
      }
    } catch { /* context may be in transient state */ }
  }
  if (!loggedIn) {
    await browser.close();
    die(`login timed out (no auth_token cookie after 15 min, last url: ${page.url()})`);
  }

  await ctx.storageState({ path: statePath });
  chmodSync(statePath, 0o600);
  await browser.close();
  log(`✅ saved storageState to ${statePath} (mode 600)`);
  log('   Chrome window was kept open — close it manually when done.');
  log('   next: node scripts/x-publish.mjs publish "..." [images...]');
}

async function cmdPublish(text, imagePaths) {
  if (!existsSync(statePath)) {
    die(`no saved state at ${statePath}. Run \`node scripts/x-publish.mjs login\` first.`);
  }
  if (!text || !text.trim()) die('empty post text');
  for (const p of imagePaths || []) {
    if (!existsSync(p)) die(`image not found: ${p}`);
  }

  log(`loading state from ${statePath}`);
  const browser = await chromium.launch({
    executablePath: browserPath,
    headless: false,
    args: ['--no-sandbox'],
  });
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    storageState: statePath,
  });
  const page = await ctx.newPage();
  await page.goto('https://x.com/compose/post', { waitUntil: 'domcontentloaded' });

  await page.waitForSelector('[data-testid="tweetTextarea_0"]', { timeout: 30000 });

  log('pasting text via clipboard...');
  await page.evaluate((t) => navigator.clipboard.writeText(t), text);
  await page.click('[data-testid="tweetTextarea_0"]');
  await page.keyboard.press('ControlOrMeta+v');

  await page.waitForFunction(() => {
    const el = document.querySelector('[data-testid="tweetTextarea_0"]');
    return el && (el.textContent || '').length > 0;
  }, { timeout: 10000 });

  if (imagePaths && imagePaths.length) {
    log(`uploading ${imagePaths.length} image(s)...`);
    // X compose page has two fileInput elements (sidebar media button + main composer);
    // .first() targets the main composer.
    const fileInput = page.locator('input[data-testid="fileInput"]').first();
    await fileInput.setInputFiles(imagePaths);
  }

  log('✅ compose filled. Review the open window, then click Post yourself.');
  log('   (this script never clicks Post — irreversible actions stay with you)');
  log('   Close the window or press Ctrl+C here when done.');

  // Keep process alive until user closes the window or Ctrl+C
  await new Promise(() => {});
}

function printUsage() {
  console.log(`x-publish.mjs — X (Twitter) compose helper

Usage:
  node scripts/x-publish.mjs login
  node scripts/x-publish.mjs publish "<text>" [image1 image2 ...]

Subcommands:
  login     Open Chrome, wait for you to log in to X, save storageState to disk.
  publish   Open Chrome using saved state, fill compose form, STOP before Post.

Environment:
  BURNP_PUBLISH_STATE     storage state path
                          (default: ~/.burnkit/publish-state/x.json)
  BURNP_PUBLISH_BROWSER   Chrome executable path
                          (default: /Applications/Google Chrome.app/.../Google Chrome)

This script NEVER clicks Post. You click it yourself.
`);
}

async function main() {
  const [sub, ...rest] = process.argv.slice(2);
  if (!sub || sub === '-h' || sub === '--help') {
    printUsage();
    process.exit(sub ? 0 : 1);
  }
  if (sub === 'login') {
    await cmdLogin();
  } else if (sub === 'publish') {
    const [text, ...images] = rest;
    if (!text) die('publish requires a text argument');
    await cmdPublish(text, images);
  } else {
    die(`unknown subcommand: ${sub}. Run with --help for usage.`);
  }
}

main().catch((err) => {
  console.error('[x-publish] fatal:', err.message);
  process.exit(1);
});
