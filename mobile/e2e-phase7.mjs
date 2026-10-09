/** Phase 7 real E2E: consented portal agent -> binary relay -> Chromium browser. */
import { spawn, execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { WebSocket } from 'ws';

const ROOT = resolve(import.meta.dirname, '..');
const BASE = 'http://127.0.0.1:3007';
const CHROME = process.env.CHROME_BIN || '/snap/bin/chromium';
const TMP = mkdtempSync(join(tmpdir(), 'vigilia-phase7-'));
const DB = join(TMP, 'phase7.db');
const TOKEN = join(TMP, 'agent.token');
const STATE = join(TMP, 'state');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let backend; let agent; let chrome; let cdp; let backendOutput = ''; let agentOutput = '';

async function waitFor(check, label, attempts = 180) {
  for (let i = 0; i < attempts; i++) { try { const value = await check(); if (value) return value; } catch {} await sleep(500); }
  throw new Error(`timeout: ${label}\nbackend: ${backendOutput.slice(-1200)}\nagent: ${agentOutput.slice(-1200)}`);
}
function request(path, options = {}) { return fetch(`${BASE}${path}`, options); }
function stop(child) { if (child && !child.killed) child.kill('SIGTERM'); }
async function browser() {
  chrome = spawn(CHROME, ['--headless=new', '--no-sandbox', '--disable-gpu', `--user-data-dir=${join(TMP, 'chromium')}`, '--remote-debugging-port=9477', 'about:blank'], { stdio: 'ignore' });
  const target = await waitFor(async () => (await (await fetch('http://127.0.0.1:9477/json')).json()).find(item => item.type === 'page'), 'Chromium');
  cdp = new WebSocket(target.webSocketDebuggerUrl); await new Promise(resolve => { cdp.onopen = resolve; });
  let sequence = 0; const pending = new Map();
  cdp.onmessage = event => { const message = JSON.parse(event.data); if (message.id && pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); } };
  return async expression => { const id = ++sequence; const result = await new Promise(resolve => { pending.set(id, resolve); cdp.send(JSON.stringify({ id, method: 'Runtime.evaluate', params: { expression, awaitPromise: true, returnByValue: true } })); }); if (result.result?.exceptionDetails) throw new Error(JSON.stringify(result.result.exceptionDetails)); return result.result?.result?.value; };
}

async function main() {
  try {
    execFileSync(process.execPath, [join(ROOT, 'node_modules/prisma/build/index.js'), 'db', 'push', '--force-reset', '--skip-generate', `--schema=${join(ROOT, 'backend/prisma/schema.prisma')}`], { cwd: ROOT, env: { ...process.env, DATABASE_URL: `file:${DB}` }, stdio: 'ignore' });
    backend = spawn(process.execPath, ['backend/dist/main.js'], { cwd: ROOT, env: { ...process.env, HOST: '127.0.0.1', PORT: '3007', DATABASE_URL: `file:${DB}`, VIGILIA_CORS_ORIGIN: BASE, LOG_LEVEL: 'info' }, stdio: ['ignore', 'pipe', 'pipe'] });
    backend.stdout.on('data', data => { backendOutput += data; }); backend.stderr.on('data', data => { backendOutput += data; });
    await waitFor(async () => (await request('/api/health')).ok, 'backend');
    agent = spawn('python3', ['agents/linux/main.py'], { cwd: ROOT, env: { ...process.env, PYTHONUNBUFFERED: '1', VIGILIA_BACKEND_URL: BASE, VIGILIA_AGENT_NAME: 'Phase 7 Portal Laptop', VIGILIA_AGENT_TOKEN_FILE: TOKEN, VIGILIA_STATE_DIR: STATE, VIGILIA_TELEMETRY_INTERVAL: '2', VIGILIA_HEARTBEAT_INTERVAL: '2' }, stdio: ['ignore', 'inherit', 'inherit'] });
    const device = await waitFor(async () => (await (await request('/api/devices')).json()).devices?.find(row => row.name === 'Phase 7 Portal Laptop' && row.status === 'online'), 'portal agent online');
    await waitFor(() => existsSync(TOKEN), 'agent token');
    const agentToken = readFileSync(TOKEN, 'utf8').trim();
    const pair = await request(`/api/devices/${device.id}/pair`, { method: 'POST', headers: { Authorization: `Bearer ${agentToken}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ deviceId: device.id }) });
    const commandToken = (await pair.json()).commandToken;
    let lastCapability;
    try { await waitFor(async () => { const response = await request(`/api/remote/${device.id}/capabilities`, { headers: { 'x-vigilia-command-token': commandToken } }); const payload = await response.json(); lastCapability = payload.capabilities; return lastCapability?.maxWidth === 1280 && lastCapability?.supportedFormats?.includes('jpeg'); }, 'remote capability report'); } catch (error) { throw new Error(`${error.message}; last capability: ${JSON.stringify(lastCapability)}`); }
    const evaluate = await browser();
    await evaluate(`location.href=${JSON.stringify(`${BASE}/?device=${device.id}`)}`);
    await waitFor(() => evaluate(`window.BACKEND?.state?.deviceId === ${JSON.stringify(device.id)}`), 'browser device');
    await evaluate(`localStorage.setItem('vigilia.commandToken', ${JSON.stringify(commandToken)})`);
    await evaluate(`new Promise((resolve, reject) => { window.__phase7Frames = 0; window.__phase7Socket = new WebSocket(${JSON.stringify(`ws://127.0.0.1:3007/ws?deviceId=${device.id}`)}); window.__phase7Socket.binaryType = 'arraybuffer'; window.__phase7Socket.onopen = resolve; window.__phase7Socket.onerror = reject; window.__phase7Socket.onmessage = event => { if (event.data instanceof ArrayBuffer) { const bytes = new Uint8Array(event.data); if (String.fromCharCode(...bytes.slice(0, 4)) === 'VIG7' && bytes[4] === 1 && bytes[6 + bytes[5]] === 0xff && bytes[7 + bytes[5]] === 0xd8) window.__phase7Frames++; } }; })`);
    const start = await request(`/api/remote/${device.id}/sessions`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'x-vigilia-command-token': commandToken }, body: JSON.stringify({ operation: 'REMOTE_DESKTOP_START' }) });
    if (!start.ok) throw new Error(`remote start rejected: ${await start.text()}`);
    const sessionId = (await start.json()).session.sessionId;
    await waitFor(async () => (await (await request(`/api/remote/${device.id}/sessions/${sessionId}`, { headers: { 'x-vigilia-command-token': commandToken } })).json()).session?.status === 'ACTIVE', 'portal capture active');
    await waitFor(() => evaluate(`window.__phase7Frames > 0`), 'first binary JPEG in browser');
    await evaluate(`window.__phase7Socket.close()`);
    await evaluate(`new Promise((resolve, reject) => { window.__phase7Socket = new WebSocket(${JSON.stringify(`ws://127.0.0.1:3007/ws?deviceId=${device.id}`)}); window.__phase7Socket.binaryType = 'arraybuffer'; window.__phase7Socket.onopen = resolve; window.__phase7Socket.onerror = reject; window.__phase7Socket.onmessage = event => { if (event.data instanceof ArrayBuffer && new Uint8Array(event.data)[0] === 0x56) window.__phase7Frames++; }; })`);
    const beforeReconnect = await evaluate(`window.__phase7Frames`);
    await waitFor(() => evaluate(`window.__phase7Frames > ${beforeReconnect}`), 'browser reconnect stream');
    console.log(`phase 7 e2e passed: ${sessionId}, binary JPEG stream, reconnect received stream`);
  } finally {
    if (cdp) cdp.close(); stop(chrome); stop(agent); stop(backend); rmSync(TMP, { recursive: true, force: true });
  }
}
main().catch(error => { console.error(error.stack || error); console.error(`backend output:\n${backendOutput}`); console.error(`agent output:\n${agentOutput}`); process.exitCode = 1; });
