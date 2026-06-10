#!/usr/bin/env node
// scripts/jike-publish.mjs
// 即刻 (okjike.com) compose helper: login (one-time) + publish (fill compose, stop before 发送).
// NEVER clicks 发送 — irreversible action stays with the human.
//
// Usage:
//   node scripts/jike-publish.mjs login
//   node scripts/jike-publish.mjs publish "<text>" [image1 image2 ...]
//
// Env:
//   JIKE_PUBLISH_STATE    storage state path (default: ~/.burnkit/publish-state/jike.json)
//   JIKE_PUBLISH_BROWSER  Chrome executable path
//   JIKE_PUBLISH_PORT     CDP debug port (default: 9224 — must not collide with x-publish's 9223)

import { chromium } from 'playwright';
import { homedir } from 'os';
import { join, dirname } from 'path';
import { existsSync, mkdirSync, chmodSync } from 'fs';
import { execFileSync } from 'child_process';

const DEFAULT_STATE = join(homedir(), '.burnkit', 'publish-state', 'jike.json');
const DEFAULT_BROWSER = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const DEFAULT_PORT = 9224;

const statePath = process.env.JIKE_PUBLISH_STATE || DEFAULT_STATE;
const browserPath = process.env.JIKE_PUBLISH_BROWSER || DEFAULT_BROWSER;
const debugPort = Number(process.env.JIKE_PUBLISH_PORT || DEFAULT_PORT);

const log = (...a) => console.log('[jike-publish]', ...a);
const die = (msg, code = 1) => { console.error(`[jike-publish] ${msg}`); process.exit(code); };

function ensureDir(p) { mkdirSync(dirname(p), { recursive: true }); }
function checkBrowser() {
  if (!existsSync(browserPath)) {
    die(`Chrome not found at ${browserPath}. Set JIKE_PUBLISH_BROWSER to override.`);
  }
}

async function cmdLogin() {
  ensureDir(statePath);
  checkBrowser();
  // Plan C: native Chrome via macOS `open` (no Playwright flags at all).
  const userDataDir = join(dirname(statePath), 'chrome-profile-jike');
  mkdirSync(userDataDir, { recursive: true });

  log('launching NATIVE Chrome via `open` (no Playwright automation flags)');
  log(`profile dir:         ${userDataDir}`);
  log(`debug port:          ${debugPort}`);
  log(`storageState target: ${statePath}`);

  execFileSync('open', [
    '-na', 'Google Chrome',
    '--args',
    `--user-data-dir=${userDataDir}`,
    `--remote-debugging-port=${debugPort}`,
    '--no-first-run',
    '--no-default-browser-check',
    'https://web.okjike.com/login',
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
  let page = ctx.pages().find((p) => p.url().includes('okjike.com')) || ctx.pages()[0];
  if (!page || !page.url().includes('okjike.com')) {
    page = await ctx.newPage();
    await page.goto('https://web.okjike.com/login', { waitUntil: 'domcontentloaded' });
  }

  log('waiting for login (Ctrl+C to abort, up to 15 min)...');
  log('detecting login by URL change off /login + "创建动态" aria-label appearing');
  const deadline = Date.now() + 15 * 60 * 1000;
  let loggedIn = false;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2000));
    const url = page.url();
    // Once we leave /login and land on the home feed, look for the "创建动态" button
    // (jike puts the compose trigger as an aria-label="创建动态" button — text is empty
    // because the inner content is just an SVG icon).
    if (!/\/login/i.test(url) && /okjike\.com/.test(url)) {
      try {
        const found = await page.evaluate(() => {
          return Boolean(
            document.querySelector('button[aria-label="创建动态"]') ||
            document.querySelector('[aria-label="创建动态"]')
          );
        });
        if (found) { loggedIn = true; break; }
      } catch { /* keep polling */ }
    }
  }
  if (!loggedIn) {
    await browser.close();
    die(`login timed out (no "创建动态" button after 15 min, last url: ${page.url()})`);
  }

  await ctx.storageState({ path: statePath });
  chmodSync(statePath, 0o600);
  await browser.close();
  log(`✅ saved storageState to ${statePath} (mode 600)`);
  log('   Chrome window was kept open — close it manually when done.');
  log('   next: node scripts/jike-publish.mjs publish "..." [images...]');
}

async function cmdPublish(text, imagePaths) {
  if (!existsSync(statePath)) {
    die(`no saved state at ${statePath}. Run \`node scripts/jike-publish.mjs login\` first.`);
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
    viewport: { width: 1280, height: 900 },
    storageState: statePath,
  });
  const page = await ctx.newPage();
  await page.goto('https://web.okjike.com/following', { waitUntil: 'domcontentloaded' });

  // Jike SPA needs a moment to: load bundles, validate JK_ACCESS_TOKEN,
  // render the home feed, then mount the "创建动态" button.
  // The button exists in two copies (mobile + desktop). The desktop one
  // is the only visible one at viewport ≥ 48em; locate it explicitly to
  // avoid Playwright picking the hidden mobile sibling.
  log('waiting for jike SPA to render the compose button (up to 30s)...');
  // Wait for the page to render enough — body has real content, not just CSS
  await page.waitForFunction(
    () => (document.body.textContent || '').length > 5000,
    { timeout: 30000 }
  ).catch(() => null); // continue even if we time out — we still try

  const composeBtnHandle = await page.evaluateHandle(() => {
    const buttons = Array.from(document.querySelectorAll('button[aria-label="创建动态"]'));
    return buttons.find((b) => b.offsetParent !== null) || null;
  });
  if (!composeBtnHandle || (await composeBtnHandle.evaluate((el) => el === null))) {
    const dump = await page.evaluate(() => ({
      url: location.href, title: document.title,
      bodyChars: (document.body.textContent || '').length,
    }));
    await browser.close();
    die(`no visible "创建动态" button. State: ${JSON.stringify(dump)}`);
  }

  log('opening compose...');
  await composeBtnHandle.asElement().click();
  await page.waitForTimeout(1500);

  // Editor — jike uses a Lexical contenteditable div inside a Mantine modal.
  // The modal overlay (data-fixed="true") sometimes intercepts pointer events
  // during mount, so we focus the editor via JS instead of clicking it.
  log('typing text into compose editor (Lexical)...');
  const editor = page.locator('div[contenteditable="true"]').first();
  await editor.waitFor({ state: 'attached', timeout: 15000 });
  // Focus the Lexical editor and dispatch a synthetic input via clipboard
  // (Lexical reads the clipboard payload and inserts its own paragraph nodes;
  // typing char-by-char into a Lexical editor without proper key events fails.)
  await page.evaluate((t) => {
    const el = document.querySelector('div[contenteditable="true"]');
    if (!el) throw new Error('editor not found');
    el.focus();
    // Use the InputEvent constructor with inputType="insertFromPaste" so
    // Lexical routes the value through its clipboard-paste handler.
    const dt = new DataTransfer();
    dt.setData('text/plain', t);
    const ev = new InputEvent('beforeinput', {
      inputType: 'insertFromPaste', data: t, bubbles: true, cancelable: true,
    });
    el.dispatchEvent(ev);
  }, text);
  // Fallback: also drop text into the editor via clipboard API + paste shortcut
  await page.evaluate((t) => navigator.clipboard.writeText(t), text);
  await page.keyboard.press('ControlOrMeta+v');
  await page.waitForTimeout(500);

  // Image upload: jike usually has a hidden <input type="file"> near the editor toolbar
  if (imagePaths && imagePaths.length) {
    log(`uploading ${imagePaths.length} image(s)...`);
    const fileInput = page.locator('input[type="file"]').first();
    await fileInput.setInputFiles(imagePaths);
    // give jike a moment to render thumbnails
    await page.waitForTimeout(2000);
  }

  log('✅ compose filled. Review the open window, then click 发送 yourself.');
  log('   (this script never clicks 发送 — irreversible actions stay with you)');
  log('   Close the window or press Ctrl+C here when done.');

  await new Promise(() => {});
}

function printUsage() {
  console.log(`jike-publish.mjs — 即刻 (okjike.com) compose helper

Usage:
  node scripts/jike-publish.mjs login
  node scripts/jike-publish.mjs publish "<text>" [image1 image2 ...]

Subcommands:
  login     Open Chrome, wait for you to log in to 即刻, save storageState to disk.
  publish   Open Chrome using saved state, fill compose form, STOP before 发送.

Environment:
  JIKE_PUBLISH_STATE     storage state path
                         (default: ~/.burnkit/publish-state/jike.json)
  JIKE_PUBLISH_BROWSER   Chrome executable path
                         (default: /Applications/Google Chrome.app/.../Google Chrome)
  JIKE_PUBLISH_PORT      CDP debug port (default: 9224)

This script NEVER clicks 发送. You click it yourself.
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
  console.error('[jike-publish] fatal:', err.message);
  process.exit(1);
});
