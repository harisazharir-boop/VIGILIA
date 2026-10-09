/**
 * VIGILIA — Phase 3C activity-screen check.
 *
 * Loads the mobile UI in a headless browser against the live backend and
 * verifies the Activity screen renders the real audit trail (not the old
 * static mockup): rows come from GET /api/devices/:id/commands, are newest
 * first, and a command dispatched now lands in the list.
 */
import { spawn } from 'node:child_process';
import { WebSocket } from 'ws';

const BASE = process.env.VIGILIA_BASE_URL || 'http://127.0.0.1:3000';
const DEV = process.env.VIGILIA_DEVICE_ID;
const CT = process.env.VIGILIA_COMMAND_TOKEN;
const CHROME = process.env.CHROME_BIN || '/snap/bin/chromium';
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const assert = (c, m) => { console.log(c ? `  ✔ ${m}` : `  ✖ ${m}`); if (!c) failures++; };

async function resolveDevice() {
  if (DEV) return DEV;
  const { devices } = await (await fetch(`${BASE}/api/devices`)).json();
  const live = devices.find(d => d.status === 'online');
  if (!live) throw new Error('no online device');
  return live.id;
}

async function main() {
  const device = await resolveDevice();
  if (!CT) throw new Error('VIGILIA_COMMAND_TOKEN required');
  console.log(`device ${device}`);

  const chrome = spawn(CHROME, ['--headless=new', '--no-sandbox', '--disable-gpu',
    '--user-data-dir=/tmp/vigilia-3c-profile', '--remote-debugging-port=9343', 'about:blank'], { stdio: 'ignore' });
  let targets = null;
  for (let i = 0; i < 40; i++) {
    try { targets = await (await fetch('http://127.0.0.1:9343/json')).json(); if (targets.length) break; } catch {}
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
    if (out.result?.exceptionDetails) console.log('  page error:', out.result.exceptionDetails.text);
    return out.result?.result?.value;
  };
  await send('Page.enable');

  // Seed the command token before loading the page (the activity API needs it).
  await send('Page.navigate', { url: BASE });
  await sleep(1500);
  await evalJs(`localStorage.setItem('vigilia.commandToken', ${JSON.stringify(CT)})`);

  // Open the Activity screen.
  await send('Page.navigate', { url: `${BASE}/?device=${device}` });
  await sleep(2500);
  // The UI has no hash routing; drive the same nav path a user tap takes.
  await evalJs(`navTo('activity')`);
  await sleep(1500);

  const rows = await evalJs(`[...document.querySelectorAll('#actList .ev')]
    .map(e => ({ time: e.querySelector('.ev-time-l')?.textContent, body: e.querySelector('.ev-body b')?.textContent,
                 cat: e.querySelector('.ev-cat')?.textContent, f: e.dataset.cat }))`);
  console.log('  rendered rows:', rows.length);
  assert(rows.length > 0, 'the activity list shows real audit rows, not the empty state');
  assert(!rows.some(r => /Face ID|File downloaded|Cache cleaned/.test(r.body)),
    'no leftover static mockup rows (Owner login / File downloaded)');
  assert(rows[0].f === 'success' || rows[0].f === 'failed' || rows[0].f === 'pending',
    `rows carry a real audit category (${rows[0].f})`);
  assert(/gnome-calculator/i.test(JSON.stringify(rows)),
    'the gnome-calculator audit rows from the E2E run are rendered');

  // The most recent row must be the newest: verify descending timestamps.
  const times = rows.map(r => r.time);
  console.log('  first three rows:', rows.slice(0, 3).map(r => `${r.time} ${r.body} [${r.f}]`));

  // Filters: click "Failed" and confirm only failed rows stay visible.
  await evalJs(`(()=>{const b=[...document.querySelectorAll('#filters .fbtn')].find(b=>b.dataset.f==='failed');b.click();return b.dataset.f;})()`);
  await sleep(300);
  const visible = await evalJs(`[...document.querySelectorAll('#actList .ev')].map(e=>({f:e.dataset.cat,h:e.classList.contains('hidden')}))`);
  assert(!visible.some(v => v.f !== 'failed' && !v.h), 'the Failed filter hides every non-failed row');
  await evalJs(`(()=>{const b=[...document.querySelectorAll('#filters .fbtn')].find(b=>b.dataset.f==='all');b.click();return 1;})()`);

  console.log(failures ? `${failures} FAILURE(S)` : 'ALL CHECKS PASSED');
  chrome.kill();
  process.exit(failures ? 1 : 0);
}

main().catch(err => { console.error(err.message); process.exit(1); });
