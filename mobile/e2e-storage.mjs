/**
 * VIGILIA — Phase 4D storage screen end-to-end check.
 *
 * Loads the real UI in a headless browser against a live backend, pushes a
 * Phase 4A storage breakdown over the agent WebSocket as the agent would,
 * and asserts the Storage screen renders it — and only it:
 *
 *   - the donut, the legend and the category list come from the pushed frame
 *   - a category the agent could not read is labelled a lower bound, not
 *     presented as an exact measurement
 *   - nothing on the screen accepts a path: there is no input, no search
 *     field and no "delete" verb anywhere on the screen
 *
 * Run:  node mobile/e2e-storage.mjs
 */
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { WebSocket } from 'ws';

const BASE = process.env.VIGILIA_BASE_URL || 'http://127.0.0.1:3000';
const CHROME = process.env.CHROME_BIN || '/snap/bin/chromium';
// A fresh profile per run: a reused one keeps the previous run's DOM in the
// bfcache, so "the screen starts empty" would read last run's breakdown.
const PROFILE = mkdtempSync(join(tmpdir(), 'vigilia-storage-e2e-'));
const sleep = ms => new Promise(r => setTimeout(r, ms));

let failures = 0;
const assert = (c, m) => { console.log(c ? `  ✔ ${m}` : `  ✖ ${m}`); if (!c) failures++; };

async function registerDevice() {
  // A unique name per run: registration upserts on (name, hostname), so a
  // repeated probe name would reuse the previous run's device row — and the
  // StorageReport replayed from it, which would defeat the "starts empty"
  // assertion before the first push.
  const run = process.hrtime.bigint().toString(36).slice(-8);
  const res = await fetch(`${BASE}/api/devices/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: `E2E Storage Probe ${run}`, os: 'ubuntu', hostname: `e2e-storage-${run}`, protocolVersion: 1 }),
  });
  if (!res.ok) throw new Error(`register failed: ${res.status}`);
  return res.json();
}

async function main() {
  const { deviceId, token } = await registerDevice();
  if (!deviceId || !token) throw new Error('registration returned no credentials');
  console.log(`storage e2e: device ${deviceId}`);

  const chrome = spawn(CHROME, ['--headless=new', '--no-sandbox', '--disable-gpu',
    `--user-data-dir=${PROFILE}`, '--remote-debugging-port=9351', 'about:blank'], { stdio: 'ignore' });

  let targets = null;
  for (let i = 0; i < 40; i++) {
    try { targets = await (await fetch('http://127.0.0.1:9351/json')).json(); if (targets.length) break; } catch { /* booting */ }
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
  await send('Page.navigate', { url: `${BASE}/?device=${deviceId}` });
  await sleep(3000);

  // ---------------- the screen starts empty, not faked ----------------
  // Read the empty state *before* opening the agent socket: once the agent
  // pushes, the replay path legitimately fills the screen, so the "no data"
  // state is only observable before the first report arrives.
  await evalJs(`document.querySelector('[data-nav="storage"]').click()`);
  await sleep(600);
  const emptyState = await evalJs(`document.querySelector('#categoryList')?.textContent?.trim() ?? ''`);
  assert(/No breakdown yet/i.test(emptyState), 'the storage screen waits for the device rather than inventing numbers');

  // ---------------- push a real breakdown as the agent would ----------------
  const agentWs = new WebSocket(`${BASE.replace('http', 'ws')}/ws`, [`vigilia.${deviceId}.${token}`]);
  await new Promise(r => agentWs.onopen = r);
  const summary = {
    deviceId,
    totalBytes: 500_000_000_000,
    usedBytes: 342_000_000_000,
    freeBytes: 158_000_000_000,
    usagePercent: 68.4,
    timestamp: new Date().toISOString(),
    categories: [
      { categoryId: 'downloads', label: 'Downloads', bytes: 26_000_000_000, itemCount: 412, safeToAnalyze: true },
      { categoryId: 'tmp', label: 'Temporary files', bytes: 1_200_000_000, itemCount: 89, safeToAnalyze: true },
      { categoryId: 'trash', label: 'Trash', bytes: 340_000_000, itemCount: 12, safeToAnalyze: false },
      { categoryId: 'browser-cache', label: 'Browser cache', bytes: 680_000_000, itemCount: 133, safeToAnalyze: true },
    ],
  };
  agentWs.send(JSON.stringify({ type: 'storage', data: summary }));
  await sleep(1500);

  const dom = await evalJs(`JSON.stringify({
    mid: document.querySelector('#donutMid')?.textContent?.replace(/\\s+/g, ' ').trim() ?? '',
    legend: [...document.querySelectorAll('#donutLegend span')].map(s => s.textContent.replace(/\\s+/g, ' ').trim()),
    rows: [...document.querySelectorAll('#categoryList .folder')].map(f => f.textContent.replace(/\\s+/g, ' ').trim()),
    segments: document.querySelectorAll('#donut .d-seg').length,
  })`);
  const parsed = JSON.parse(dom ?? '{}');
  console.log('  donut:', parsed.mid);
  console.log(`  legend rows: ${parsed.legend.length}, category rows: ${parsed.rows.length}, donut segments: ${parsed.segments}`);

  // fmtBytes renders in GiB (1024^3), so 342 GB decimal is 318.5 GiB on screen.
  assert(/318\.5 GB/.test(parsed.mid), `the donut shows the measured used total (got ${parsed.mid})`);
  assert(/68%/.test(parsed.mid), `the donut shows the measured percentage (got ${parsed.mid})`);
  assert(parsed.segments === 4, `every measured category becomes one donut segment (got ${parsed.segments})`);
  assert(parsed.legend.length === 4, 'the legend lists the measured categories');
  assert(parsed.rows.length === 4, 'the category list renders one row per measured category');
  assert(parsed.rows.some(r => /Downloads/.test(r) && /24\.2 GB/.test(r)), 'the downloads row shows its measured size');
  assert(parsed.rows.some(r => /Trash/.test(r) && /lower bound/.test(r)), 'an unreadable category is labelled a lower bound');
  assert(!parsed.rows.some(r => /exact/i.test(r)), 'no category is claimed to be exact');

  // ---------------- the screen has no path or delete affordance ----------------
  const inputs = await evalJs(`document.querySelectorAll('#s-storage input, #s-storage textarea').length`);
  assert(inputs === 0, 'the storage screen accepts no text input of any kind (no path can be entered)');
  const verbs = await evalJs(`[...document.querySelectorAll('#s-storage button')].map(b => b.textContent.trim()).join('|')`);
  assert(!/delete|remove|erase|exec|shell/i.test(verbs), `no destructive verb on a read-only screen (got ${JSON.stringify(verbs)})`);

  // ---------------- refresh re-reads the backend ----------------
  await evalJs(`document.querySelector('#storageRefresh').click()`);
  await sleep(800);
  const after = await evalJs(`document.querySelectorAll('#categoryList .folder').length`);
  assert(after === 4, 'refresh re-renders from the stored breakdown');

  agentWs.close();
  ws.close();
  chrome.kill();

  if (failures) { console.log(`\n${failures} assertion(s) failed`); process.exit(1); }
  console.log('\nstorage e2e: all assertions passed');
  process.exit(0);
}

main().catch(err => { console.error('storage e2e failed:', err); process.exit(1); });
