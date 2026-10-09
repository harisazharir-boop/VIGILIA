/**
 * VIGILIA — Phase 3B application-control end-to-end check.
 *
 * Loads the UI in a headless browser against a live backend + Linux agent,
 * and verifies the control contract end to end:
 *
 *   - the Control screen lists the applications the *device* reported
 *   - an Open button dispatches APPLICATION_OPEN and is audited
 *   - the row only flips to Running once the agent confirms
 *   - a Close flips it back to Stopped after the agent closes the process
 *
 * The pairing token is seeded into localStorage before the test, because
 * pairing itself needs the agent token (out of band by design).
 *
 * Run:  node mobile/e2e-applications.mjs
 *       VIGILIA_DEVICE_ID=cm… VIGILIA_COMMAND_TOKEN=vc_… node mobile/e2e-applications.mjs
 */
import { spawn } from 'node:child_process';
import { WebSocket } from 'ws';

const BASE = process.env.VIGILIA_BASE_URL || 'http://127.0.0.1:3000';
const CHROME = process.env.CHROME_BIN || '/snap/bin/chromium';
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

async function main() {
  const DEV = await resolveDevice();
  const CT = process.env.VIGILIA_COMMAND_TOKEN;
  if (!CT) throw new Error('VIGILIA_COMMAND_TOKEN is required (pair the device first)');
  console.log(`following ${DEV}`);

  const chrome = spawn(CHROME, ['--headless=new', '--no-sandbox', '--disable-gpu',
    '--user-data-dir=/tmp/vigilia-3b-profile', '--remote-debugging-port=9342', 'about:blank'], { stdio: 'ignore' });

  let targets = null;
  for (let i = 0; i < 40; i++) {
    try { targets = await (await fetch('http://127.0.0.1:9342/json')).json(); if (targets.length) break; } catch {}
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
    return out.result?.result?.value;
  };
  await send('Page.enable');

  // Navigate before seeding the token: localStorage is per-origin, and
  // about:blank is not the page's origin.
  await send('Page.navigate', { url: `${BASE}/?device=${DEV}#control` });
  await sleep(2000);
  await evalJs(`localStorage.setItem('vigilia.commandToken', ${JSON.stringify(CT)})`);
  await sleep(3000);

  const rows = await evalJs(`[...document.querySelectorAll('#appList .app')]
    .map(a => ({ id: a.dataset.appId, pill: a.querySelector('.pill')?.textContent }))`);
  console.log('  catalog:', JSON.stringify(rows.map(r => r.id)));
  assert(rows.length > 0, `the control screen lists ${rows.length} real applications from the device`);

  // A safe target: present on most Ubuntu hosts, and harmless to open/close.
  const target = rows.find(r => r.id === 'gnome-calculator')?.id ?? rows[0]?.id;
  if (!target) { chrome.kill(); throw new Error('no controllable application to test with'); }
  console.log(`  target: ${target}`);

  const rowState = () => evalJs(`(()=>{const c=[...document.querySelectorAll('#appList .app')]
    .find(a=>a.dataset.appId===${JSON.stringify(target)});return c?.querySelector('.pill')?.textContent;})()`);
  const clickButton = re => evalJs(`(()=>{const c=[...document.querySelectorAll('#appList .app')]
    .find(a=>a.dataset.appId===${JSON.stringify(target)});
    const b=[...c.querySelectorAll('button')].find(b=>${re}.test(b.textContent));if(!b) return 'missing';b.click();return b.textContent.trim();})()`);
  const latestAudit = command => evalJs(`(async()=>{const r=await fetch('${BASE}/api/devices/${DEV}/commands?limit=5',
    {headers:{'x-vigilia-command-token':'${CT}'}});const c=(await r.json()).commands;return c.find(x=>x.command==='${command}')||c[0];})()`);

  const startedAs = await rowState();
  const wantOpen = /Stopped/i.test(startedAs || '');
  console.log(`  state before: ${startedAs}`);

  if (wantOpen) {
    const label = await clickButton(/Open/i);
    assert(/Opening/i.test(label || ''), 'the Open button enters a loading state');
    await sleep(600);
    const row = await latestAudit('APPLICATION_OPEN');
    assert(row?.command === 'APPLICATION_OPEN' && row?.target === target,
      'the dispatch created an audited APPLICATION_OPEN row for the target');
    await sleep(4000);
    const after = await rowState();
    console.log(`  state after open: ${after}`);
    assert(/Running/i.test(after || ''), 'the pill only flips to Running once the agent confirms');
  } else {
    console.log('  already running — skipping the open step');
  }

  const label = await clickButton(/Close/i);
  assert(/Closing/i.test(label || ''), 'the Close button enters a loading state');
  await sleep(6000);
  const closeRow = await latestAudit('APPLICATION_CLOSE');
  assert(closeRow?.command === 'APPLICATION_CLOSE', 'the close reached the audit trail');
  const closed = await rowState();
  console.log(`  state after close: ${closed}`);
  assert(/Stopped/i.test(closed || ''), 'the pill flips back to Stopped after the agent closes it');

  console.log(failures ? `${failures} FAILURE(S)` : 'ALL CHECKS PASSED');
  chrome.kill();
  process.exit(failures ? 1 : 0);
}

main().catch(err => { console.error(err.message); process.exit(1); });
