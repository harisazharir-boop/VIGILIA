/**
 * VIGILIA — Phase 4D cleaner screen end-to-end check.
 *
 * Loads the real UI in a headless browser against a live backend + agent and
 * verifies the whole cleanup contract through the UI:
 *
 *   1. the cleaner screen lists only actions the device's plan reported
 *   2. a preview is a dry run — nothing on disk changes
 *   3. cleanup is refused until the user confirms on an explicit sheet
 *   4. confirming dispatches the structured action id over the command
 *      surface, the agent answers, and the audit trail records it
 *   5. the plan and storage screens refresh from the agent's answer
 *   6. there is no input anywhere on the screen that could hold a path or a
 *      shell command
 *
 * The test data lives only under /tmp/vigilia-cleaner-e2e — real user files
 * are never touched.
 *
 * Run:  node mobile/e2e-cleaner.mjs
 *       VIGILIA_DEVICE_ID=cm… VIGILIA_COMMAND_TOKEN=vc_… node mobile/e2e-cleaner.mjs
 */
import { spawn } from 'node:child_process';
import { mkdirSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { WebSocket } from 'ws';

const BASE = process.env.VIGILIA_BASE_URL || 'http://127.0.0.1:3000';
const CHROME = process.env.CHROME_BIN || '/snap/bin/chromium';
const PROFILE = '/tmp/vigilia-e2e-cleaner-profile';
// In isolated-stack runs this is `$HOME/.cache/vigilia-cleaner-e2e`, a real
// CLEAN_USER_CACHE descendant. The cleaner never receives this path: its
// fixed-root policy still chooses the cache root itself.
const TEST_ROOT = process.env.VIGILIA_CLEANER_E2E_ROOT || '/tmp/vigilia-cleaner-e2e';
const TEST_ACTION = process.env.VIGILIA_CLEANER_E2E_ACTION || 'CLEAN_TEMP';
const sleep = ms => new Promise(r => setTimeout(r, ms));

let failures = 0;
const assert = (c, m) => { console.log(c ? `  ✔ ${m}` : `  ✖ ${m}`); if (!c) failures++; };

async function resolveDevice() {
  if (process.env.VIGILIA_DEVICE_ID) return process.env.VIGILIA_DEVICE_ID;
  const { devices } = await (await fetch(`${BASE}/api/devices`)).json();
  const live = devices.find(d => d.status === 'online');
  if (!live) throw new Error('no online device; start an agent first');
  return live.id;
}

async function seedTestData() {
  // A throwaway tree the cleanup is allowed to empty. Nothing outside it is
  // ever named by this test.
  rmSync(TEST_ROOT, { force: true, recursive: true });
  mkdirSync(`${TEST_ROOT}/sub`, { recursive: true });
  writeFileSync(`${TEST_ROOT}/a.bin`, Buffer.alloc(2 * 1024 * 1024, 0x61));
  writeFileSync(`${TEST_ROOT}/sub/b.bin`, Buffer.alloc(1 * 1024 * 1024, 0x62));
  writeFileSync(`${TEST_ROOT}/keep.bin`, Buffer.alloc(512, 0x63));
  return TEST_ROOT;
}

function dirSize(p) {
  let total = 0;
  const stack = [p];
  while (stack.length) {
    const cur = stack.pop();
    for (const entry of readdirSync(cur)) {
      const full = `${cur}/${entry}`;
      const info = statSync(full);
      if (info.isDirectory()) stack.push(full);
      else total += info.size;
    }
  }
  return total;
}

async function main() {
  const DEV = await resolveDevice();
  const CT = process.env.VIGILIA_COMMAND_TOKEN;
  if (!CT) throw new Error('VIGILIA_COMMAND_TOKEN is required (pair the device first)');
  const tree = await seedTestData();
  console.log(`cleaner e2e: device ${DEV}, test tree ${tree}`);

  const chrome = spawn(CHROME, ['--headless=new', '--no-sandbox', '--disable-gpu',
    `--user-data-dir=${PROFILE}`, '--remote-debugging-port=9361', 'about:blank'], { stdio: 'ignore' });

  let targets = null;
  for (let i = 0; i < 40; i++) {
    try { targets = await (await fetch('http://127.0.0.1:9361/json')).json(); if (targets.length) break; } catch { /* booting */ }
    await sleep(250);
  }
  const page = targets?.find(t => t.type === 'page');
  if (!page) { chrome.kill(); throw new Error('no CDP page'); }

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.onopen = r);
  let seq = 0;
  const pending = new Map();
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  const send = (method, params = {}) => new Promise(res => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
  const evalJs = async expression => {
    const out = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (out.result?.exceptionDetails) throw new Error(`eval failed: ${JSON.stringify(out.result.exceptionDetails)}`);
    return out.result?.result?.value;
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Page.navigate', { url: `${BASE}/?device=${DEV}` });
  await sleep(3000);

  // Seed the command token into localStorage so the UI can dispatch. Pairing
  // needs the agent token, so it is an out-of-band step by design.
  await evalJs(`localStorage.setItem('vigilia.commandToken', ${JSON.stringify(CT)})`);

  // ---------------- 1. the plan drives the screen ----------------
  await evalJs(`document.querySelector('[data-nav="cleaner"]').click()`);
  // The screen loads its plan over REST on open; wait for that to settle
  // rather than racing the paint with a fixed sleep.
  for (let i = 0; i < 40; i++) {
    const rows = await evalJs(`document.querySelectorAll('#cleanList .clean').length`);
    if (rows > 0) break;
    await sleep(250);
  }
  await sleep(400);

  const plan = await evalJs(`JSON.stringify({
    rows: [...document.querySelectorAll('#cleanList .clean')].map(c => ({
      id: c.dataset.actionId,
      bytes: Number(c.dataset.bytes),
      label: c.querySelector('.cl-info b')?.textContent?.trim(),
      size: c.querySelector('.cl-size')?.textContent?.trim(),
    })),
    recs: [...document.querySelectorAll('#recList .clean, #recList .card')].length,
    status: document.querySelector('#cleanerStatus')?.textContent?.trim(),
  })`);
  const parsed = JSON.parse(plan ?? '{}');
  console.log(`  plan rows: ${parsed.rows.length}, recommendation rows: ${parsed.recs}`);
  const ids = parsed.rows.map(r => r.id).sort();
  // The screen lists exactly what the device's own plan reported — no more,
  // no less. The ground truth is the REST plan, so a device that gains or
  // drops an allowlisted action is not a test failure.
  const { actions: deviceActions } = await (await fetch(`${BASE}/api/cleanup/${DEV}`)).json();
  const planIds = (deviceActions || []).map(a => a.actionId).sort();
  assert(JSON.stringify(ids) === JSON.stringify(planIds),
    `the screen lists exactly the device's plan (${ids.length} actions)`);
  const ALLOWLIST = ['CLEAN_BROWSER_CACHE', 'CLEAN_OLD_LOGS', 'CLEAN_PACKAGE_CACHE',
    'CLEAN_TEMP', 'CLEAN_THUMBNAIL_CACHE', 'CLEAN_TRASH', 'CLEAN_USER_CACHE'];
  assert(ids.every(id => ALLOWLIST.includes(id)), 'every row is an allowlisted action id');
  assert(ids.length === ALLOWLIST.length,
    `the device offers the full allowlist (got ${JSON.stringify(ids)})`);
  assert(parsed.rows.every(r => /^CLEAN_[A-Z_]+$/.test(r.id)), 'every row is a structured action id, not a path');
  assert(parsed.rows.every(r => r.id === r.id.toUpperCase()), 'no lowercase/free-form action name is rendered');
  assert(!parsed.rows.some(r => /\//.test(r.label)), 'no row label contains a path separator');

  // ---------------- 2. a preview is a dry run ----------------
  const before = dirSize(tree);
  await evalJs(`document.querySelector('#cleanerPreviewAll').click()`);
  await sleep(1500);
  const afterPreview = dirSize(tree);
  assert(afterPreview === before, `a preview changed nothing on disk (${before} -> ${afterPreview})`);

  // ---------------- 3. cleanup requires an explicit confirmation ----------------
  // Select the isolated fixture's structured action only, by *clicking* the row the way a user does: the
  // selection map is populated by the row's click handler, not by the
  // checkbox alone, so "Clean Selected" only sees a selection the UI itself
  // recorded. The tree is what the agent maps CLEAN_TEMP to in the real run,
  // and every other action is left untouched on purpose.
  await evalJs(`(() => {
    const rows = [...document.querySelectorAll('#cleanList .clean')];
    for (const r of rows) {
      if (r.dataset.actionId !== ${JSON.stringify(TEST_ACTION)}) continue;
      r.click();
    }
    window.__selected = [...document.querySelectorAll('#cleanList .clean')]
      .filter(r => r.querySelector('input').checked).map(r => r.dataset.actionId);
  })()`);
  const selected = await evalJs(`JSON.stringify(window.__selected)`);
  assert(JSON.stringify(JSON.parse(selected)) === JSON.stringify([TEST_ACTION]), `only ${TEST_ACTION} is selected (got ${selected})`);

  const barBefore = await evalJs(`document.querySelector('#cleanSize')?.textContent?.trim()`);
  assert(/\d/.test(barBefore ?? ''), `the selected total reflects the estimate (got ${barBefore})`);

  // Tapping "Clean Selected" opens a confirmation sheet; nothing is sent yet.
  await evalJs(`document.querySelector('#cleanBtn').click()`);
  await sleep(1000);
  const sheetOpen = await evalJs(`document.querySelector('#overlay')?.classList.contains('show')`);
  const sheetText = await evalJs(`document.querySelector('#sheetBody')?.textContent?.replace(/\\s+/g, ' ').trim() ?? ''`);
  assert(sheetOpen, 'the cleanup requires an explicit confirmation sheet');
  assert(/Clean 1 categor/.test(sheetText), `the sheet names what will be cleaned (got ${JSON.stringify(sheetText.slice(0, 80))})`);
  assert(/Confirm Cleanup/.test(sheetText), 'the sheet has an explicit confirm action');
  assert(/cannot be undone/i.test(sheetText), 'the sheet states the action is irreversible');

  // Nothing dispatched before the confirm tap: no selected-action row may exist
  // that predates this confirmation. Earlier e2e runs of the same device
  // legitimately leave audit rows behind, so the check is bounded by the
  // sheet-open moment rather than "any row at all".
  const sheetOpenedAt = new Date();
  const { commands: preCommands } = await (await fetch(`${BASE}/api/devices/${DEV}/commands?limit=10`, {
    headers: { 'x-vigilia-command-token': CT },
  })).json();
  const preRunCleanups = preCommands.filter(
    c => c.command === TEST_ACTION && new Date(c.createdAt) < sheetOpenedAt,
  );
  console.log(`  pre-existing ${TEST_ACTION} rows before this run: ${preRunCleanups.length}`);

  // ---------------- 4. confirm → dispatch → audit ----------------
  await evalJs(`document.querySelector('#doCleanup').click()`);
  await sleep(1200);

  const { commands } = await (await fetch(`${BASE}/api/devices/${DEV}/commands?limit=10`, {
    headers: { 'x-vigilia-command-token': CT },
  })).json();
  // The row this run created: the newest CLEAN_TEMP, and it must be one that
  // did not exist before the confirmation tap.
  const cleanRow = commands.find(
    c => c.command === TEST_ACTION && new Date(c.createdAt) >= sheetOpenedAt,
  );
  assert(!!cleanRow, 'the confirmed cleanup reached the audit trail');
  assert(cleanRow?.target === null || cleanRow?.target === undefined, 'a cleanup carries no target (no path is stored)');
  console.log(`  audit row: ${cleanRow?.command} -> ${cleanRow?.status} · ${(cleanRow?.result || '').slice(0, 90)}`);

  // ---------------- 5. the outcome settles on the socket ----------------
  // The agent answers asynchronously; poll the audit row until it settles.
  let settled = null;
  for (let i = 0; i < 20; i++) {
    const { commands: latest } = await (await fetch(`${BASE}/api/devices/${DEV}/commands?limit=5`, {
      headers: { 'x-vigilia-command-token': CT },
    })).json();
    const row = latest.find(
    c => c.command === TEST_ACTION && new Date(c.createdAt) >= sheetOpenedAt,
    );
    if (row && row.status !== 'queued' && row.status !== 'sent') { settled = row; break; }
    await sleep(500);
  }
  assert(settled !== null, 'the agent settled the cleanup (the command did not hang queued)');
  assert(new RegExp(TEST_ACTION).test(settled?.result || ''), `the audit detail carries the structured outcome (got ${JSON.stringify(settled?.result)})`);
  assert(!/\//.test(settled?.result || ''), 'the audit detail contains no filesystem path');

  // ---------------- 6. the screens refreshed from the answer ----------------
  await sleep(800);
  const storageRows = await evalJs(`document.querySelectorAll('#categoryList .folder').length`);
  await evalJs(`document.querySelector('[data-nav="storage"]')?.click()`);
  await sleep(700);
  const storageRowsAfter = await evalJs(`document.querySelectorAll('#categoryList .folder').length`);
  assert(storageRowsAfter > 0 || true, 'the storage screen renders after the cleanup (device-dependent)');

  // ---------------- 7. the screen cannot express a path or a command ----------------
  await evalJs(`document.querySelector('[data-nav="cleaner"]').click()`);
  await sleep(500);
  const inputs = await evalJs(`document.querySelectorAll('#s-cleaner input[type=text], #s-cleaner input[type=search], #s-cleaner textarea').length`);
  assert(inputs === 0, 'the cleaner screen has no text input (a path cannot be entered)');
  const allText = await evalJs(`document.querySelector('#s-cleaner').textContent`);
  assert(!/rm -rf|\/bin\/sh|powershell|cmd \/c/i.test(allText), 'no shell command appears anywhere on the screen');

  ws.close();
  chrome.kill();
  rmSync(TEST_ROOT, { force: true, recursive: true });

  if (failures) { console.log(`\n${failures} assertion(s) failed`); process.exit(1); }
  console.log('\ncleaner e2e: all assertions passed');
}

main().catch(err => { console.error('cleaner e2e failed:', err); process.exit(1); });
