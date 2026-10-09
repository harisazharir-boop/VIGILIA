/**
 * VIGILIA — mobile UI ↔ backend integration test (headless browser).
 *
 * Loads the real UI at http://127.0.0.1:3000/ in headless Chromium, then
 * pushes telemetry over the agent WebSocket and asserts the DOM updates.
 *
 * Requires: a healthy backend on :3000 and Chromium at /snap/bin/chromium.
 * Run:  node mobile/e2e.test.mjs
 */
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { WebSocket } from 'ws';

const BASE = process.env.VIGILIA_BASE_URL || 'http://127.0.0.1:3000';
const CHROME = process.env.CHROME_BIN || '/snap/bin/chromium';
// A fresh profile per run: a reused one restores the previous run's DOM from
// the bfcache, and the mockup's placeholder numbers would still be showing.
const PROFILE = mkdtempSync(join(tmpdir(), 'vigilia-e2e-'));

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function registerDevice(name, hostname) {
  const res = await fetch(`${BASE}/api/devices/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, os: 'ubuntu', hostname, protocolVersion: 1 }),
  });
  if (!res.ok) throw new Error(`register failed: ${res.status}`);
  return res.json();
}

// ---------------- assertions ----------------
let failures = 0;
function assert(cond, msg) {
  if (cond) { console.log(`  ✔ ${msg}`); }
  else { console.log(`  ✖ ${msg}`); failures++; }
}

async function main() {
  console.log('registering an e2e device…');
  const { deviceId, token } = await registerDevice('E2E UI Probe', 'e2e-ui-probe');
  if (!deviceId || !token) throw new Error('registration returned no credentials');

  // Chromium keeps its state in a throwaway profile dir.
  const chrome = spawn(CHROME, [
    '--headless=new', '--no-sandbox', '--disable-gpu',
    `--user-data-dir=${PROFILE}`,
    `--remote-debugging-port=9333`,
    'about:blank',
  ], { stdio: 'ignore' });

  // ---------------- find the CDP endpoint ----------------
  let targets = null;
  for (let i = 0; i < 40; i++) {
    try {
      const res = await fetch('http://127.0.0.1:9333/json');
      targets = await res.json();
      if (targets.length) break;
    } catch { /* still booting */ }
    await sleep(250);
  }
  const page = targets?.find(t => t.type === 'page');
  if (!page) { chrome.kill(); throw new Error('no CDP page target'); }

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.onopen = r);
  let seq = 0;
  const pending = new Map();
  ws.onmessage = e => {
    const msg = JSON.parse(e.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  };
  const send = (method, params = {}) => new Promise(res => {
    const id = ++seq;
    pending.set(id, res);
    ws.send(JSON.stringify({ id, method, params }));
  });

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Page.navigate', { url: `${BASE}/?device=${deviceId}` });
  await sleep(3500);

  const evalJs = async (expression) => {
    const out = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (out.result?.exceptionDetails) throw new Error(`eval failed: ${JSON.stringify(out.result.exceptionDetails)}`);
    return out.result?.result?.value;
  };

  // ---------------- 1. the UI picked the device we asked for ----------------
  const name = await evalJs(`document.querySelector('#deviceName').textContent.trim()`);
  assert(/E2E UI Probe/.test(name ?? ''), `device name rendered from the backend (got ${JSON.stringify(name)})`);

  // ---------------- 2. the mockup's placeholder numbers are still up ----------------
  // index.html ships hardcoded demo numbers; they must be replaced once real
  // telemetry arrives (checked in step 3), and must never survive alongside it.
  const before = await evalJs(`document.querySelectorAll('#s-home .stat-val')[0].textContent`);
  console.log(`  placeholder value before telemetry: ${JSON.stringify(before)}`);

  // ---------------- 3. push telemetry as the agent would ----------------
  const agentWs = new WebSocket(`${BASE.replace('http', 'ws')}/ws`, [`vigilia.${deviceId}.${token}`]);
  await new Promise(r => agentWs.onopen = r);
agentWs.send(JSON.stringify({ type: 'agent.hello', data: { agentVersion: '1.0.0', protocolVersion: 1 } }));
  await sleep(100);
  const sample = {
    type: 'telemetry',
    data: {
      deviceId,
      cpu: 42.5, ram: 67.0, storage: 81.0, battery: 55.0,
      os: 'Ubuntu 24.04', hostname: 'e2e-ui-probe', uptime: 3600,
      network: { connected: true, interface: 'eth0' },
      timestamp: new Date().toISOString(),
    },
  };
  agentWs.send(JSON.stringify(sample));
  await sleep(1500);

  // The mockup's CPU placeholder was 18%; the live value must have replaced it.
  assert(before !== undefined, 'the stat card existed before telemetry arrived');

  const after = await evalJs(`(() => {
    const cards = [...document.querySelectorAll('#s-home .card.stat')];
    const byLabel = {};
    for (const c of cards) {
      const lbl = c.querySelector('.stat-lbl')?.textContent?.trim().toLowerCase();
      byLabel[lbl] = c.querySelector('.stat-val')?.textContent?.trim();
    }
    return JSON.stringify(byLabel);
  })()`);
  const values = JSON.parse(after ?? '{}');
  console.log('  live stat cards:', after);
  assert(values.cpu?.includes('42.5'), `CPU updated from the WS frame (got ${values.cpu})`);
  assert(values.ram?.includes('67'), `RAM updated from the WS frame (got ${values.ram})`);
  assert(values.storage?.includes('81'), `storage updated (got ${values.storage})`);
  assert(values.battery?.includes('55'), `battery updated (got ${values.battery})`);
  assert(values.network?.includes('eth0'), `network interface shown (got ${values.network})`);
  assert(values.uptime?.includes('1'), `uptime rendered (got ${values.uptime})`);

  // ---------------- 4. status flips on a heartbeat ----------------
  agentWs.send(JSON.stringify({
    type: 'heartbeat',
    data: { deviceId, status: 'offline', timestamp: new Date().toISOString() },
  }));
  await sleep(1000);
  const dot = await evalJs(`document.querySelector('#deviceDot')?.className`);
  assert(/offline/.test(dot ?? ''), `device status reflected the heartbeat (got ${JSON.stringify(dot)})`);

  // ---------------- 5. sparklines are drawn from real history ----------------
  const sparkCount = await evalJs(`[...document.querySelectorAll('#s-home .spark')]
    .filter(s => s.dataset.spark && s.querySelector('path')).length`);
  assert(sparkCount >= 2, `sparklines rendered from history (got ${sparkCount})`);

  agentWs.close();
  ws.close();
  chrome.kill();

  if (failures) {
    console.log(`\n${failures} assertion(s) failed`);
    process.exit(1);
  }
  console.log('\ne2e: all assertions passed');
}

main().catch(err => {
  console.error('e2e failed:', err);
  process.exit(1);
});
