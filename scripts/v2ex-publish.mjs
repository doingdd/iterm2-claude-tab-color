#!/usr/bin/env node
// scripts/v2ex-publish.mjs
// V2EX composer helper: login (one-time) + publish to a node (fill form, stop before 发起).
// NEVER clicks 发起 — irreversible action stays with the human.
//
// Usage:
//   node scripts/v2ex-publish.mjs login
//   node scripts/v2ex-publish.mjs publish <nodeName> "<title>" "<body>"
//
// Examples:
//   node scripts/v2ex-publish.mjs publish claude "Title" "Body markdown..."
//
// Env:
//   V2EX_PUBLISH_STATE    storage state path (default: ~/.burnkit/publish-state/v2ex.json)
//   V2EX_PUBLISH_BROWSER  Chrome executable path
//   V2EX_PUBLISH_PORT     CDP debug port (default: 9225 — shared with reddit-publish
//                         so a single native Chrome instance handles all platforms)

import { chromium } from 'playwright';
import { homedir } from 'os';
import { join, dirname } from 'path';
import { existsSync, mkdirSync, chmodSync } from 'fs';
import { execFileSync } from 'child_process';

const DEFAULT_STATE = join(homedir(), '.burnkit', 'publish-state', 'v2ex.json');
const DEFAULT_BROWSER = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const DEFAULT_PORT = 9225;

const statePath = process.env.V2EX_PUBLISH_STATE || DEFAULT_STATE;
const browserPath = process.env.V2EX_PUBLISH_BROWSER || DEFAULT_BROWSER;
const debugPort = Number(process.env.V2EX_PUBLISH_PORT || DEFAULT_PORT);

const log = (...a) => console.log('[v2ex-publish]', ...a);
const die = (msg, code = 1) => { console.error(`[v2ex-publish] ${msg}`); process.exit(code); };

function ensureDir(p) { mkdirSync(dirname(p), { recursive: true }); }
function checkBrowser() {
  if (!existsSync(browserPath)) {
    die(`Chrome not found at ${browserPath}. Set V2EX_PUBLISH_BROWSER to override.`);
  }
}

async function cmdLogin() {
  ensureDir(statePath);
  checkBrowser();

  // V2EX login now uses the same shared native Chrome on port 9225 that
  // reddit-publish uses. We just open a new tab pointed at the V2EX signin
  // page. If the user already has a V2EX session in this Chrome (rare on a
  // fresh reddit-only profile), they'll see the new-topic link immediately.
  log('connecting to shared native Chrome at 127.0.0.1:9225');
  const cdpUrl = `http://127.0.0.1:${debugPort}`;
  let browser = null;
  const cdpDeadline = Date.now() + 10 * 1000;
  while (Date.now() < cdpDeadline) {
    try {
      const res = await fetch(`${cdpUrl}/json/version`);
      if (res.ok) { browser = await chromium.connectOverCDP(cdpUrl); break; }
    } catch { /* not ready yet */ }
    await new Promise((r) => setTimeout(r, 500));
  }
  if (!browser) die(`Chrome CDP did not come up at ${cdpUrl} within 10s`);

  const ctx = browser.contexts()[0];
  let page = ctx.pages().find((p) => /v2ex\.com/.test(p.url()));
  if (!page) {
    page = await ctx.newPage();
  }
  log('opening V2EX signin in the shared Chrome...');
  await page.goto('https://v2ex.com/signin', { waitUntil: 'domcontentloaded' });

  log('waiting for login (Ctrl+C to abort, up to 15 min)...');
  log('detecting login by "创作新主题" link visibility');
  const deadline = Date.now() + 15 * 60 * 1000;
  let loggedIn = false;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2000));
    try {
      const found = await page.evaluate(() => {
        return Boolean(
          Array.from(document.querySelectorAll('a, button')).find((el) => {
            const t = (el.textContent || '').trim();
            return t === '创作新主题' || /^创作新主题$/.test(t);
          })
        );
      });
      if (found) { loggedIn = true; break; }
    } catch { /* keep polling */ }
  }
  if (!loggedIn) {
    die(`login timed out (no "创作新主题" link after 15 min, last url: ${page.url()})`);
  }

  await ctx.storageState({ path: statePath });
  chmodSync(statePath, 0o600);
  log(`✅ saved storageState to ${statePath} (mode 600)`);
  log('   next: node scripts/v2ex-publish.mjs publish <node> "<title>" "<body>"');
  // Don't close the browser — user is still using it
  await new Promise(() => {});
}

async function cmdPublish(nodeName, title, body) {
  if (!nodeName) die('nodeName required (e.g. claude)');
  if (!title) die('title required');
  if (!body) die('body required (markdown ok)');

  // V2EX is way more permissive than Reddit, so we can use the same native
  // Chrome instance the reddit flow is using (port 9225) and just add another
  // tab. The user is already signed in to V2EX in that Chrome (login step
  // left them signed in), so we don't need to inject storageState here.
  const cdpUrl = `http://127.0.0.1:${debugPort}`;
  log(`connecting to shared native Chrome at ${cdpUrl}`);
  let browser;
  try {
    browser = await chromium.connectOverCDP(cdpUrl);
  } catch (e) {
    die(`could not connect to Chrome at ${cdpUrl}. Is the login window still open? Error: ${e.message}`);
  }

  const ctx = browser.contexts()[0];
  let page = ctx.pages().find((p) => /v2ex\.com/.test(p.url()));
  if (!page) {
    page = await ctx.newPage();
  }
  // V2EX compose URL: /new/<node> or /write/<node>
  await page.goto(`https://v2ex.com/new/${nodeName}`, { waitUntil: 'domcontentloaded' });

  log('waiting for compose form (up to 30s)...');
  await page.waitForFunction(
    () => Boolean(
      document.querySelector('input[name="title"]') ||
      document.querySelector('textarea[name="content"]') ||
      document.querySelector('#topic_title')
    ),
    { timeout: 30000 }
  ).catch(() => null);

  // Fill title
  const titleResult = await page.evaluate((titleText) => {
    const input = document.querySelector('input[name="title"]') || document.querySelector('#topic_title');
    if (!input) return { ok: false, reason: 'title input not found' };
    input.focus();
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(input, titleText);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return { ok: true, value: input.value };
  }, title);
  log('  title:', JSON.stringify(titleResult));

  // Body — V2EX uses a textarea[name="content"] (Markdown) for posts
  const bodyResult = await page.evaluate((bodyText) => {
    const ta = document.querySelector('textarea[name="content"]') || document.querySelector('#topic_content');
    if (!ta) return { ok: false, reason: 'content textarea not found' };
    ta.focus();
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
    setter.call(ta, bodyText);
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    ta.dispatchEvent(new Event('change', { bubbles: true }));
    return { ok: true, len: ta.value.length };
  }, body);
  log('  body:', JSON.stringify(bodyResult));

  log('✅ compose filled. Review the open window, then click 发起 yourself.');
  log('   (this script never clicks 发起 — irreversible actions stay with you)');
  log('   The Chrome window will stay open — switch to the V2EX tab and click.');

  await new Promise(() => {});
}

function printUsage() {
  console.log(`v2ex-publish.mjs — V2EX composer helper

Usage:
  node scripts/v2ex-publish.mjs login
  node scripts/v2ex-publish.mjs publish <nodeName> "<title>" "<body>"

Subcommands:
  login     Open Chrome, wait for you to log in to V2EX, save storageState to disk.
  publish   Open Chrome using saved state, fill compose form, STOP before 发起.

Examples:
  node scripts/v2ex-publish.mjs publish claude "Title" "Body markdown..."

Environment:
  V2EX_PUBLISH_STATE    storage state path
                        (default: ~/.burnkit/publish-state/v2ex.json)
  V2EX_PUBLISH_BROWSER  Chrome executable path
                        (default: /Applications/Google Chrome.app/.../Google Chrome)
  V2EX_PUBLISH_PORT     CDP debug port (default: 9226)

This script NEVER clicks 发起. You click it yourself.
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
    const [nodeName, title, body] = rest;
    if (!nodeName) die('publish requires a node name');
    if (!title) die('publish requires a title');
    if (!body) die('publish requires a body');
    await cmdPublish(nodeName, title, body);
  } else {
    die(`unknown subcommand: ${sub}. Run with --help for usage.`);
  }
}

main().catch((err) => {
  console.error('[v2ex-publish] fatal:', err.message);
  process.exit(1);
});
