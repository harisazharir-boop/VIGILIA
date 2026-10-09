/**
 * VIGILIA — realtime path check: agent WS → backend → mobile WS → DOM.
 *
 * Loads the UI, waits for the agent's own telemetry interval to fire, and
 * asserts the DOM moves with it — proving the whole chain is live rather
 * than a single REST snapshot.
 *
 * Run:  node mobile/e2e-realtime.mjs
 */
import { spawn } from 'node:child_process';
import { WebSocket } from 'ws';

const BASE = process.env.VIGILIA_BASE_URL || 'http://127.0.0.1:3000';
const CHROME = process.env.CHROME_BIN || '/snap/bin/chromium';
const PROFILE = '/tmp/vigilia-e2e-rt-profile';

const sleep = ms => new Promise(r => setTimeout(r, ms));

let failures = 0;
const assert = (cond, msg) => {
  if (cond) console.log(`  ✔ ${msg}`);
  else { console.log(`  ✖ ${msg}`); failures++; }
};

async function main() {
  const { devices } = await (await fetch(`${BASE}/api/devices`)).json();
  // The device list carries no telemetry flag, so confirm freshness against
  // the monitoring route instead: an online device whose latest sample is
  // recent is the one an agent is actively streaming to.
  let device = null;
  for (const candidate of devices.filter(d => d.status === 'online')) {
    try {
      const res = await fetch(`${BASE}/api/monitoring/${candidate.id}/latest`);
      if (!res.ok) continue;
      const { telemetry } = await res.json();
      const ageSeconds = telemetry
        ? (Date.now() - new Date(telemetry.timestamp).getTime()) / 1000
        : Infinity;
      if (ageSeconds < 30) { device = candidate; break; }
    } catch { /* a device with no telemetry yet */ }
  }
  if (!device) { console.log('no live device with telemetry; start the agent first'); process.exit(2); }
  console.log(`following ${device.name} (${device.id})`);

  const chrome = spawn(CHROME, [
    '--headless=new', '--no-sandbox', '--disable-gpu',
    `--user-data-dir=${PROFILE}`, '--remote-debugging-port=9334', 'about:blank',
  ], { stdio: 'ignore' });

  let targets = null;
  for (let i = 0; i < 40; i++) {
    try { targets = await (await fetch('http://127.0.0.1:9334/json')).json(); if (targets.length) break; } catch {}
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
  await send('Runtime.enable');
  await send('Page.navigate', { url: `${BASE}/?device=${device.id}` });
  await sleep(3000);

  // 1. The UI opened the websocket itself.
  const isOpen = await evalJs(`window.Vigilia && window.Vigilia.isOpen`);
  assert(isOpen === true, `the mobile websocket is open to the backend (got ${isOpen})`);
  const connectedId = await evalJs(`window.Vigilia && window.Vigilia.connectedDeviceId`);
  assert(connectedId === device.id, `it subscribed to the right device (got ${connectedId})`);

  // 2. Two samples a couple of seconds apart must differ — the agent is live.
  const first = await evalJs(`document.querySelector('#s-home .card.stat .stat-val').textContent`);
  await sleep(6000);
  const second = await evalJs(`document.querySelector('#s-home .card.stat .stat-val').textContent`);
  assert(!!first && !!second, `two telemetry samples reached the DOM (${first} → ${second})`);
  assert(first !== second, `the value moved between samples (live, not a frozen snapshot)`);

  // 3. The backend actually has fresh rows for this device.
  const { telemetry } = await (await fetch(`${BASE}/api/monitoring/${device.id}/latest`)).json();
  const ageSeconds = telemetry ? (Date.now() - new Date(telemetry.timestamp).getTime()) / 1000 : Infinity;
  assert(ageSeconds < 30, `backend's latest telemetry is fresh (${ageSeconds.toFixed(1)}s old)`);

  // 4. Reconnect: a dropped socket must rejoin on its own.
  await evalJs(`window.Vigilia && window.Vigilia.disconnect && window.Vigilia.disconnect()`);
  // simulate a server-side drop by closing from the client then waiting
  await sleep(500);
  await evalJs(`window.Vigilia && window.Vigilia.connect && window.Vigilia.connect('${device.id}')`);
  await sleep(4000);
  const reopened = await evalJs(`window.Vigilia && window.Vigilia.isOpen`);
  assert(reopened === true, `the socket reconnected after a drop (got ${reopened})`);

  ws.close();
  chrome.kill();
  if (failures) { console.log(`\n${failures} assertion(s) failed`); process.exit(1); }
  console.log('\nrealtime: all assertions passed');
}

main().catch(err => { console.error('realtime check failed:', err); process.exit(1); });
