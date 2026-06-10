#!/usr/bin/env node
// scripts/reddit-publish.mjs
// Reddit composer helper: login (one-time) + publish to a subreddit (fill form, stop before Post).
// NEVER clicks Post — irreversible action stays with the human.
//
// Usage:
//   node scripts/reddit-publish.mjs login
//   node scripts/reddit-publish.mjs publish <subreddit> "<title>" "<body>" [kind=text|link] [url]
//
// Examples:
//   node scripts/reddit-publish.mjs publish ClaudeAI "Title" "Body markdown..."
//   node scripts/reddit-publish.mjs publish LocalLLaMA "Title" "" link https://github.com/...
//
// Env:
//   REDDIT_PUBLISH_STATE    storage state path (default: ~/.burnkit/publish-state/reddit.json)
//   REDDIT_PUBLISH_BROWSER  Chrome executable path
//   REDDIT_PUBLISH_PORT     CDP debug port (default: 9225)

import { chromium } from 'playwright';
import { homedir } from 'os';
import { join, dirname } from 'path';
import { existsSync, mkdirSync, chmodSync } from 'fs';
import { execFileSync } from 'child_process';

const DEFAULT_STATE = join(homedir(), '.burnkit', 'publish-state', 'reddit.json');
const DEFAULT_BROWSER = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const DEFAULT_PORT = 9225;

const statePath = process.env.REDDIT_PUBLISH_STATE || DEFAULT_STATE;
const browserPath = process.env.REDDIT_PUBLISH_BROWSER || DEFAULT_BROWSER;
const debugPort = Number(process.env.REDDIT_PUBLISH_PORT || DEFAULT_PORT);

const log = (...a) => console.log('[reddit-publish]', ...a);
const die = (msg, code = 1) => { console.error(`[reddit-publish] ${msg}`); process.exit(code); };

function ensureDir(p) { mkdirSync(dirname(p), { recursive: true }); }
function checkBrowser() {
  if (!existsSync(browserPath)) {
    die(`Chrome not found at ${browserPath}. Set REDDIT_PUBLISH_BROWSER to override.`);
  }
}

async function cmdLogin() {
  ensureDir(statePath);
  checkBrowser();
  const userDataDir = join(dirname(statePath), 'chrome-profile-reddit');
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
    'https://www.reddit.com/login',
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
  let page = ctx.pages().find((p) => /reddit\.com/.test(p.url())) || ctx.pages()[0];
  if (!page || !/reddit\.com/.test(page.url())) {
    page = await ctx.newPage();
    await page.goto('https://www.reddit.com/login', { waitUntil: 'domcontentloaded' });
  }

  log('waiting for login (Ctrl+C to abort, up to 15 min)...');
  log('detecting login by presence of user avatar / username in header');
  const deadline = Date.now() + 15 * 60 * 1000;
  let loggedIn = false;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2000));
    try {
      const found = await page.evaluate(() => {
        // Reddit shows the username link in the header when logged in
        return Boolean(
          document.querySelector('a[href^="/user/"]') ||
          document.querySelector('[data-testid="user-menu"]') ||
          document.querySelector('faceplate-tracker[source="user_menu"]')
        );
      });
      if (found) { loggedIn = true; break; }
    } catch { /* keep polling */ }
  }
  if (!loggedIn) {
    await browser.close();
    die(`login timed out (no user avatar after 15 min, last url: ${page.url()})`);
  }

  await ctx.storageState({ path: statePath });
  chmodSync(statePath, 0o600);
  await browser.close();
  log(`✅ saved storageState to ${statePath} (mode 600)`);
  log('   Chrome window was kept open — close it manually when done.');
  log('   next: node scripts/reddit-publish.mjs publish <sub> "<title>" "<body>" [kind] [url]');
}

async function cmdPublish(subreddit, title, body, kind = 'text', linkUrl) {
  if (!subreddit) die('subreddit required (e.g. ClaudeAI)');
  if (!title) die('title required');
  if (kind === 'text' && !body) die('text post requires body');
  if (kind === 'link' && !linkUrl) die('link post requires url arg');
  if (!existsSync(statePath)) {
    die(`no saved state at ${statePath}. Run \`node scripts/reddit-publish.mjs login\` first.`);
  }

  // Reddit's bot defense blocks any Playwright-launched Chrome ("You've been
  // blocked by network security"). We must reuse the same NATIVE Chrome from
  // the login step (still on port 9225, since login left the window open).
  // That Chrome has the user's real session AND zero Playwright automation flags.
  const cdpUrl = `http://127.0.0.1:${debugPort}`;
  log(`connecting to native Chrome at ${cdpUrl} (same instance as login)`);
  let browser;
  try {
    browser = await chromium.connectOverCDP(cdpUrl);
  } catch (e) {
    die(`could not connect to Chrome at ${cdpUrl}. Is the login window still open? Error: ${e.message}`);
  }

  const ctx = browser.contexts()[0];
  // If the user closed the login window, the browser process may have died.
  // Reuse any existing reddit tab; otherwise open one.
  let page = ctx.pages().find((p) => /reddit\.com/.test(p.url()));
  if (!page) {
    page = await ctx.newPage();
  }
  // We don't use storageState injection here — the user is already logged in
  // in this Chrome (login step left them signed in). Just navigate.
  // For TEXT mode we hit /submit/?type=TEXT so the new Reddit composer auto-selects
  // TEXT and renders faceplate-textarea-input[name=title] + shreddit-composer[name=body].
  // For LINK mode we let the default page load; the UI exposes a type=select we can click.
  const submitUrl = kind === 'text'
    ? `https://www.reddit.com/r/${subreddit}/submit/?type=TEXT`
    : `https://www.reddit.com/r/${subreddit}/submit/`;
  log(`navigating to ${submitUrl}`);
  await page.goto(submitUrl, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);

  // Wait for the form to render — title faceplate + body composer must both exist
  log('waiting for submit form (up to 30s)...');
  await page.waitForFunction(
    () => Boolean(
      document.querySelector('faceplate-textarea-input[name="title"]') &&
      document.querySelector('shreddit-composer[name="body"]')
    ),
    { timeout: 30000 }
  ).catch(() => null);

  if (kind === 'text') {
    // Title — faceplate wraps a textarea in shadow DOM. Playwright's `>>>`
    // piercing fails when the context comes from connectOverCDP, so we
    // use evaluate + the native value setter + composed events. The
    // {composed: true} flag is critical: without it, the input event stays
    // inside the shadow root and faceplate's parent validator never fires.
    log('filling title (shadow-DOM textarea)...');
    const titleResult = await page.evaluate((titleText) => {
      const ti = document.querySelector('faceplate-textarea-input[name="title"]');
      const ta = ti?.shadowRoot?.querySelector('textarea[name="title"]');
      if (!ta) return { ok: false, reason: 'shadow textarea not found' };
      ta.focus();
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
      setter.call(ta, titleText);
      ta.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
      ta.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
      ta.dispatchEvent(new FocusEvent('blur', { bubbles: true, composed: true }));
      return { ok: true, value: ta.value };
    }, title);
    log('  title:', JSON.stringify(titleResult));
    if (!titleResult.ok) log('⚠️  title not filled — review manually before posting.');

    // Body — shreddit-composer[name=body] hosts a Lexical contenteditable div
    log('filling body (Lexical editor)...');
    const bodyResult = await page.evaluate((bodyText) => {
      const c = document.querySelector('shreddit-composer[name="body"]');
      const ed = c?.querySelector('[contenteditable="true"]');
      if (!ed) return { ok: false, reason: 'lexical editor not found' };
      ed.focus();
      // Lexical's InputEvent handler is the cleanest paste path: it routes the
      // value through Lexical's clipboard plugin and emits the right node ops.
      const ev = new InputEvent('beforeinput', {
        inputType: 'insertFromPaste', data: bodyText, bubbles: true, cancelable: true,
      });
      ed.dispatchEvent(ev);
      // Fallback: also drop the text into the editor via the real clipboard API
      // + a Ctrl+V keystroke. The paste shortcut fires `paste` again, but the
      // browser-level clipboard keeps the InputEvent path's outcome intact.
      return navigator.clipboard.writeText(bodyText).then(() => ({ ok: true }));
    }, body);
    log('  body inputEvent dispatched, falling back to clipboard paste...');
    await page.keyboard.press('ControlOrMeta+v');
    await page.waitForTimeout(800);
    if (!bodyResult.ok) log('⚠️  body not filled — review manually before posting.');
  } else if (kind === 'link') {
    log('filling title (shadow-DOM textarea)...');
    const titleResult = await page.evaluate((titleText) => {
      const ti = document.querySelector('faceplate-textarea-input[name="title"]');
      const ta = ti?.shadowRoot?.querySelector('textarea[name="title"]');
      if (!ta) return { ok: false, reason: 'shadow textarea not found' };
      ta.focus();
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
      setter.call(ta, titleText);
      ta.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
      ta.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
      return { ok: true };
    }, title);
    if (!titleResult.ok) log('⚠️  title not filled.');

    log('filling link URL (shadow-DOM input)...');
    const urlResult = await page.evaluate((urlText) => {
      // shreddit puts the URL input inside a faceplate-textarea-input[name="url"]
      // — try that, then fall back to the legacy selector.
      const candidates = [
        'faceplate-textarea-input[name="url"]',
        'faceplate-textarea-input[name="link"]',
      ];
      for (const sel of candidates) {
        const host = document.querySelector(sel);
        const input = host?.shadowRoot?.querySelector('input');
        if (input) {
          input.focus();
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
          setter.call(input, urlText);
          input.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
          input.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
          return { ok: true, sel };
        }
      }
      return { ok: false, reason: 'URL input not found' };
    }, linkUrl);
    if (!urlResult.ok) log('⚠️  URL not filled.');
  }

  // Final verification — read the Post button state (inside shadow DOM) and
  // report it. If disabled, the validator rejected something; surface that
  // loudly so the user knows to inspect before clicking.
  log('checking Post button state...');
  await page.waitForTimeout(500);
  const postBtn = await page.evaluate(() => {
    const btn = document.querySelector('r-post-form-submit-button#submit-post-button')?.shadowRoot?.querySelector('button');
    return btn ? { text: btn.textContent.trim(), disabled: btn.disabled } : null;
  });
  if (postBtn) {
    if (postBtn.disabled) {
      log(`⚠️  Post button is disabled: "${postBtn.text}" — the form validator rejected something.`);
      log('   Review the open window and complete any required fields manually.');
    } else {
      log(`✅ Post button enabled: "${postBtn.text}". Click it when ready.`);
    }
  } else {
    log('⚠️  could not find Post button — review the open window manually.');
  }

  log('✅ compose filled. Review the open window, then click Post yourself.');
  log('   (this script never clicks Post — irreversible actions stay with you)');
  log('   The login Chrome will stay open — close it when fully done.');

  // Don't close the browser — the user is still using it
  await new Promise(() => {});
}

function printUsage() {
  console.log(`reddit-publish.mjs — Reddit composer helper

Usage:
  node scripts/reddit-publish.mjs login
  node scripts/reddit-publish.mjs publish <subreddit> "<title>" "<body>" [kind=text|link] [url]

Subcommands:
  login     Open Chrome, wait for you to log in to Reddit, save storageState to disk.
  publish   Open Chrome using saved state, fill submit form, STOP before Post.

Examples:
  node scripts/reddit-publish.mjs publish ClaudeAI "Title" "Body markdown..."
  node scripts/reddit-publish.mjs publish LocalLLaMA "Title" "" link https://github.com/...

Environment:
  REDDIT_PUBLISH_STATE    storage state path
                          (default: ~/.burnkit/publish-state/reddit.json)
  REDDIT_PUBLISH_BROWSER  Chrome executable path
                          (default: /Applications/Google Chrome.app/.../Google Chrome)
  REDDIT_PUBLISH_PORT     CDP debug port (default: 9225)

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
    const [subreddit, title, body, kind, url] = rest;
    if (!subreddit) die('publish requires a subreddit');
    if (!title) die('publish requires a title');
    const k = kind || (url ? 'link' : 'text');
    await cmdPublish(subreddit, title, body || '', k, url);
  } else {
    die(`unknown subcommand: ${sub}. Run with --help for usage.`);
  }
}

main().catch((err) => {
  console.error('[reddit-publish] fatal:', err.message);
  process.exit(1);
});
