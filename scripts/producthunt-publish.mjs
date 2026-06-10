#!/usr/bin/env node
// scripts/producthunt-publish.mjs
// Product Hunt launch helper. Unlike Reddit (shadow DOM + Lexical) and V2EX
// (Markdown textarea), Product Hunt's /posts/new is a Next.js multi-step
// wizard we can't reliably script from outside (we'd need to maintain
// selectors against a constantly evolving React tree). So this script is
// intentionally SEMI-AUTOMATIC:
//
//   1. Connects to the same native Chrome instance on port 9225 (shared
//      with reddit-publish and v2ex-publish — no extra Chrome process).
//   2. Opens producthunt.com/posts/new in a new tab.
//   3. Logs the user in (waits for the "Submit" button to appear).
//   4. Fills ONLY the first step (URL + auto-clicks "I made it" + moves to
//      the wizard). The rest of the wizard (tagline, description, topics,
//      makers, gallery, video, schedule) is filled manually by the user,
//      with draft text + media pre-staged on disk and printed to stdout
//      for easy copy-paste.
//   5. NEVER clicks "Schedule" or "Launch" — irreversible action stays
//      with the human.
//
// Usage:
//   node scripts/producthunt-publish.mjs login
//   node scripts/producthunt-publish.mjs fill
//
// Env:
//   PRODUCTHUNT_PUBLISH_STATE    storage state path (default: ~/.burnkit/publish-state/producthunt.json)
//   PRODUCTHUNT_PUBLISH_BROWSER  Chrome executable path
//   PRODUCTHUNT_PUBLISH_PORT     CDP debug port (default: 9225 — shared)

import { chromium } from 'playwright';
import { homedir } from 'os';
import { join, dirname } from 'path';
import { existsSync, mkdirSync, chmodSync, readFileSync } from 'fs';
import { execFileSync } from 'child_process';
import { PH_BURN_AI } from './_d4-drafts.mjs';

const DEFAULT_STATE = join(homedir(), '.burnkit', 'publish-state', 'producthunt.json');
const DEFAULT_BROWSER = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const DEFAULT_PORT = 9225;

const statePath = process.env.PRODUCTHUNT_PUBLISH_STATE || DEFAULT_STATE;
const browserPath = process.env.PRODUCTHUNT_PUBLISH_BROWSER || DEFAULT_BROWSER;
const debugPort = Number(process.env.PRODUCTHUNT_PUBLISH_PORT || DEFAULT_PORT);

const log = (...a) => console.log('[producthunt-publish]', ...a);
const die = (msg, code = 1) => { console.error(`[producthunt-publish] ${msg}`); process.exit(code); };

function ensureDir(p) { mkdirSync(dirname(p), { recursive: true }); }
function checkBrowser() {
  if (!existsSync(browserPath)) {
    die(`Chrome not found at ${browserPath}. Set PRODUCTHUNT_PUBLISH_BROWSER to override.`);
  }
}

function abspath(rel) {
  // draft paths are relative to repo root; this script lives in scripts/
  return join(dirname(new URL(import.meta.url).pathname), '..', rel);
}

async function cmdLogin() {
  ensureDir(statePath);
  checkBrowser();

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
  let page = ctx.pages().find((p) => /producthunt\.com/.test(p.url()));
  if (!page) {
    page = await ctx.newPage();
  }
  log('opening producthunt.com/posts/new in the shared Chrome...');
  await page.goto('https://www.producthunt.com/posts/new', { waitUntil: 'domcontentloaded' });

  log('waiting for login (Ctrl+C to abort, up to 15 min)...');
  log('detecting login by "Submit" button visibility in the top-right nav');
  const deadline = Date.now() + 15 * 60 * 1000;
  let loggedIn = false;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2000));
    try {
      const found = await page.evaluate(() => {
        // After login, the nav has a "Submit" button (or shows a user avatar).
        // Pre-login, you only see "Sign in" / "Sign up".
        const navLinks = Array.from(document.querySelectorAll('a, button'));
        const hasSubmit = navLinks.some((el) => /^submit$/i.test((el.textContent || '').trim()));
        const hasAvatar = !!document.querySelector('img[alt*="avatar" i], [data-testid*="user"]');
        return hasSubmit || hasAvatar;
      });
      if (found) { loggedIn = true; break; }
    } catch { /* keep polling */ }
  }
  if (!loggedIn) {
    die(`login timed out (no "Submit" button after 15 min, last url: ${page.url()})`);
  }

  await ctx.storageState({ path: statePath });
  chmodSync(statePath, 0o600);
  log(`✅ saved storageState to ${statePath} (mode 600)`);
  log('   next: node scripts/producthunt-publish.mjs fill');
  await new Promise(() => {});
}

async function cmdFill() {
  const cdpUrl = `http://127.0.0.1:${debugPort}`;
  log(`connecting to shared native Chrome at ${cdpUrl}`);
  let browser;
  try {
    browser = await chromium.connectOverCDP(cdpUrl);
  } catch (e) {
    die(`could not connect to Chrome at ${cdpUrl}. Is the login window still open? Error: ${e.message}`);
  }

  const ctx = browser.contexts()[0];
  let page = ctx.pages().find((p) => /producthunt\.com/.test(p.url()));
  if (!page) {
    page = await ctx.newPage();
  }
  log('navigating to /posts/new...');
  await page.goto('https://www.producthunt.com/posts/new', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);

  // Step 1 of the wizard: enter the URL
  // The first visible input is a URL box. We try to find it generically.
  log('filling primary URL (first step of wizard)...');
  const urlFilled = await page.evaluate((url) => {
    // The URL input is the most prominent input on /posts/new. Try a few
    // candidates that have shipped over the years.
    const candidates = [
      'input[type="url"]',
      'input[name="url"]',
      'input[placeholder*="url" i]',
      'input[placeholder*="link" i]',
      'input[data-testid*="url" i]',
    ];
    for (const sel of candidates) {
      const input = document.querySelector(sel);
      if (input) {
        input.focus();
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
        setter.call(input, url);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        return { ok: true, sel, value: input.value };
      }
    }
    return { ok: false, reason: 'no URL input found' };
  }, PH_BURN_AI.links.primary);
  log('  url:', JSON.stringify(urlFilled));
  if (!urlFilled.ok) {
    log('⚠️  could not find URL input — fill the URL manually, then press Enter / click Next.');
  } else {
    // The wizard usually auto-advances when a valid URL is entered.
    await page.waitForTimeout(1500);
    log('  waiting for wizard to advance (URL detection)...');
  }

  // Print the full draft to stdout so the user can copy-paste into the
  // remaining fields of the wizard. The wizard is a multi-step form, and
  // PH's selectors change often — manual copy-paste is the most stable path.
  console.log('\n========================================');
  console.log('  PRODUCT HUNT DRAFT (copy-paste below)');
  console.log('========================================\n');
  console.log(`NAME:        ${PH_BURN_AI.name}`);
  console.log(`TAGLINE:     ${PH_BURN_AI.tagline}  (${PH_BURN_AI.tagline.length} chars, max 60)`);
  console.log(`\nDESCRIPTION (${PH_BURN_AI.description.length} chars, max 260):\n${PH_BURN_AI.description}`);
  console.log(`\nTOPICS:      ${PH_BURN_AI.topics.join(', ')}`);
  console.log(`\nPRICING:     ${PH_BURN_AI.pricing}`);
  console.log(`\nLINKS:`);
  for (const [k, v] of Object.entries(PH_BURN_AI.links)) {
    console.log(`  ${k.padEnd(10)} ${v}`);
  }
  console.log(`\nMEDIA:`);
  for (const f of PH_BURN_AI.media.gallery) {
    console.log(`  gallery   ${abspath(f)}`);
  }
  console.log(`  video     ${abspath(PH_BURN_AI.media.video)}`);
  console.log('\nMAKERS:');
  console.log('  (fill in your PH username — and any other contributors)');
  console.log('\n========================================\n');

  log('The PH submit wizard is open. Fill the remaining fields manually:');
  log('  1. After the URL is detected, PH will show a preview card.');
  log('  2. Tagline — copy the TAGLINE line above (must be ≤ 60 chars).');
  log('  3. Description — copy the DESCRIPTION block above (≤ 260 chars).');
  log('  4. Topics — type the 4 topic names above into the topics field.');
  log('  5. Makers — add yourself + any other contributors by PH username.');
  log('  6. Pricing — pick "Free".');
  log('  7. Thumbnail + Gallery — upload the files listed under MEDIA.');
  log('  8. Video — paste the video file path into the "Video URL" field.');
  log('  9. Review the preview, then click "Create Draft" (NOT "Schedule").');
  log('  10. You can review the draft, then click "Schedule" yourself.');
  log('');
  log('⚠️  This script NEVER clicks "Schedule" or "Launch" — irreversible.');

  await new Promise(() => {});
}

function printUsage() {
  console.log(`producthunt-publish.mjs — Product Hunt launch helper (semi-automatic)

Usage:
  node scripts/producthunt-publish.mjs login
  node scripts/producthunt-publish.mjs fill

Subcommands:
  login     Open producthunt.com/posts/new in shared Chrome, wait for you to log in.
  fill      Print the full draft to stdout + jump-start the wizard (URL step).
            Remaining wizard fields are filled manually (more reliable than
            maintaining PH React-tree selectors in this script).

Environment:
  PRODUCTHUNT_PUBLISH_STATE    storage state path
  PRODUCTHUNT_PUBLISH_BROWSER  Chrome executable path
  PRODUCTHUNT_PUBLISH_PORT     CDP debug port (default: 9225, shared)

This script NEVER clicks "Schedule" or "Launch". You click it yourself.
`);
}

async function main() {
  const [sub] = process.argv.slice(2);
  if (!sub || sub === '-h' || sub === '--help') {
    printUsage();
    process.exit(sub ? 0 : 1);
  }
  if (sub === 'login') {
    await cmdLogin();
  } else if (sub === 'fill') {
    await cmdFill();
  } else {
    die(`unknown subcommand: ${sub}. Run with --help for usage.`);
  }
}

main().catch((err) => {
  console.error('[producthunt-publish] fatal:', err.message);
  process.exit(1);
});
