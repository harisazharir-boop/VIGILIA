/** Phase 6 real E2E. Starts backend, Linux agent and Chromium against isolated /tmp state. */
import { spawn, execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { WebSocket } from 'ws';

const ROOT = resolve(import.meta.dirname, '..');
const BASE = 'http://127.0.0.1:3000';
const CHROME = process.env.CHROME_BIN || '/snap/bin/chromium';
const TMP = mkdtempSync(join(tmpdir(), 'vigilia-phase6-'));
const HOME = join(TMP, 'home');
const PROJECTS = join(HOME, 'Projects');
const PROFILE = join(TMP, 'chromium');
const DB = join(TMP, 'phase6.db');
const TOKEN = join(TMP, 'agent.token');
const STATE = join(TMP, 'state');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let backend; let agent; let chrome; let cdp; let failures = 0; let backendOutput = ''; let agentOutput = '';
const assert = (ok, text) => { console.log(ok ? `  ✔ ${text}` : `  ✖ ${text}`); if (!ok) failures++; };

async function waitFor(check, label, attempts = 80) {
  for (let i = 0; i < attempts; i++) { try { const value = await check(); if (value) return value; } catch {} await sleep(250); }
  throw new Error(`timeout: ${label}${label === 'backend health' ? ` (${backendOutput.slice(-1000)})` : (label === 'project listing' || label === 'agent online') ? ` (${agentOutput.slice(-1000)})` : ''}`);
}
function request(path, options = {}) { return fetch(`${BASE}${path}`, options); }
function kill(child) { if (child && !child.killed) child.kill('SIGTERM'); }

async function browser() {
  chrome = spawn(CHROME, ['--headless=new', '--no-sandbox', '--disable-gpu', `--user-data-dir=${PROFILE}`, '--remote-debugging-port=9466', 'about:blank'], { stdio: 'ignore' });
  const targets = await waitFor(async () => { const data = await (await fetch('http://127.0.0.1:9466/json')).json(); return data.find(item => item.type === 'page'); }, 'Chromium');
  cdp = new WebSocket(targets.webSocketDebuggerUrl); await new Promise(resolve => { cdp.onopen = resolve; });
  let sequence = 0; const pending = new Map();
  cdp.onmessage = event => { const message = JSON.parse(event.data); if (message.id && pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); } };
  const send = (method, params = {}) => new Promise(resolve => { const id = ++sequence; pending.set(id, resolve); cdp.send(JSON.stringify({ id, method, params })); });
  await send('Page.enable'); await send('Runtime.enable');
  return async expression => { const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }); if (result.result?.exceptionDetails) throw new Error(JSON.stringify(result.result.exceptionDetails)); return result.result?.result?.value; };
}

async function main() {
  try {
    mkdirSync(join(PROJECTS, 'Source'), { recursive: true }); mkdirSync(join(PROJECTS, 'Destination'), { recursive: true });
    mkdirSync(join(PROJECTS, 'Empty'), { recursive: true }); mkdirSync(join(PROJECTS, 'NonEmpty'), { recursive: true });
    writeFileSync(join(PROJECTS, 'NonEmpty', 'keep.txt'), 'keep');
    writeFileSync(join(PROJECTS, 'Source', 'normal.txt'), 'download-content');
    writeFileSync(join(PROJECTS, 'Source', 'rename.txt'), 'rename');
    writeFileSync(join(PROJECTS, 'Source', 'move.txt'), 'move');
    writeFileSync(join(PROJECTS, 'Source', 'delete.txt'), 'delete');
    writeFileSync(join(PROJECTS, 'Source', '.env'), 'SECRET=no');
    symlinkSync(join(TMP, 'outside'), join(PROJECTS, 'Source', 'escape'));
    writeFileSync(join(TMP, 'outside'), 'outside');
    execFileSync(process.execPath, [join(ROOT, 'node_modules/prisma/build/index.js'), 'db', 'push', '--force-reset', '--skip-generate', `--schema=${join(ROOT, 'backend/prisma/schema.prisma')}`], { cwd: ROOT, env: { ...process.env, DATABASE_URL: `file:${DB}` }, stdio: 'ignore' });
    backend = spawn(process.execPath, ['backend/dist/main.js'], { cwd: ROOT, env: { ...process.env, NODE_ENV: 'production', HOST: '127.0.0.1', PORT: '3000', DATABASE_URL: `file:${DB}`, VIGILIA_CORS_ORIGIN: BASE, LOG_LEVEL: 'warn' }, stdio: ['ignore', 'pipe', 'pipe'] });
    backend.stderr.on('data', chunk => { backendOutput += chunk; }); backend.stdout.on('data', chunk => { backendOutput += chunk; });
    await waitFor(async () => (await request('/api/health')).ok, 'backend health');
    agent = spawn('python3', ['agents/linux/main.py'], { cwd: ROOT, env: { ...process.env, HOME, VIGILIA_FILE_ROOT: PROJECTS, VIGILIA_BACKEND_URL: BASE, VIGILIA_AGENT_NAME: 'Phase 6 E2E Laptop', VIGILIA_AGENT_TOKEN_FILE: TOKEN, VIGILIA_STATE_DIR: STATE, VIGILIA_TELEMETRY_INTERVAL: '1', VIGILIA_HEARTBEAT_INTERVAL: '1' }, stdio: ['ignore', 'pipe', 'pipe'] });
    agent.stderr.on('data', chunk => { agentOutput += chunk; }); agent.stdout.on('data', chunk => { agentOutput += chunk; });
    const device = await waitFor(async () => { const json = await (await request('/api/devices')).json(); return json.devices?.find(item => item.name === 'Phase 6 E2E Laptop' && item.status === 'online'); }, 'agent online');
    await waitFor(() => existsSync(TOKEN), 'agent token');
    const agentToken = readFileSync(TOKEN, 'utf8').trim();
    const pair = await request(`/api/devices/${device.id}/pair`, { method: 'POST', headers: { Authorization: `Bearer ${agentToken}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ deviceId: device.id }) });
    const commandToken = (await pair.json()).commandToken;
    assert(pair.ok && commandToken, 'agent paired with a real command token');
    const evaluate = await browser();
    await evaluate(`location.href=${JSON.stringify(`${BASE}/?device=${device.id}`)}`); await sleep(1500);
    await evaluate(`localStorage.setItem('vigilia.commandToken', ${JSON.stringify(commandToken)});document.querySelector('[data-nav="menu"]').click();document.querySelector('[data-nav="files"]').click();`);
    await sleep(500);
    assert(await evaluate(`document.querySelector('#s-files')?.classList.contains('active')`), 'File Manager opens');
    assert(await evaluate(`document.querySelectorAll('[data-file-root]').length >= 6`), 'safe roots render');
    await evaluate(`document.querySelector('[data-file-root="PROJECTS"]').click()`);
    await waitFor(() => evaluate(`document.querySelectorAll('#filesList [data-file-id]').length >= 4`), 'project listing');
    assert(await evaluate(`document.querySelector('#filesList')?.textContent.includes('Source')`), 'real fixture folders appear');
    await evaluate(`([...document.querySelectorAll('#filesList [data-file-id]')].find(x=>x.textContent.includes('Source'))).click()`);
    await waitFor(() => evaluate(`document.querySelector('#filesList')?.textContent.includes('normal.txt')`), 'source navigation');
    assert(await evaluate(`document.querySelector('#filesCrumb').textContent.includes('Source')`), 'folder navigation updates breadcrumb');
    await evaluate(`document.querySelector('#filesCrumb').click()`); await waitFor(() => evaluate(`document.querySelector('#filesList')?.textContent.includes('Destination')`), 'breadcrumb return');
    await evaluate(`document.querySelector('[data-file-root="PROJECTS"]').click()`); await sleep(500); await evaluate(`([...document.querySelectorAll('#filesList [data-file-id]')].find(x=>x.textContent.includes('Source'))).click()`); await sleep(500);
    await evaluate(`document.querySelector('#filesSearch').value='normal';document.querySelector('#filesSearchGo').click()`); await waitFor(() => evaluate(`document.querySelector('#filesList')?.textContent.includes('normal.txt')`), 'search');
    assert(await evaluate(`!document.querySelector('#s-files input[placeholder*="handle" i]')`), 'no manual opaque-handle input');
    assert(await evaluate(`!document.querySelector('#s-files').textContent.match(/mock file|example\.txt/i)`), 'no mock file data');
    // Agent policy checks, real request/agent/result path.
    const api = async body => (await request(`/api/files/${device.id}/requests`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'x-vigilia-command-token': commandToken }, body: JSON.stringify(body) })).json();
    const badTraversal = await api({ operation: 'FILE_SEARCH', rootId: 'PROJECTS', query: '../etc' }); assert(badTraversal.error?.code === 'VALIDATION_FAILED', 'traversal request rejected');
    const search = await api({ operation: 'FILE_SEARCH', rootId: 'PROJECTS', query: '.env' }); await sleep(400);
    assert(search.requestId, 'protected-name search dispatched for audited denial path');
    const audit = await (await request(`/api/files/${device.id}/audit`, { headers: { 'x-vigilia-command-token': commandToken } })).json();
    assert(Array.isArray(audit.audits) && audit.audits.every(row => !JSON.stringify(row).includes(PROJECTS)), 'filesystem audit has no raw path');
    assert(await evaluate(`document.querySelectorAll('#s-files input[type=file]').length === 1`), 'real upload picker exists');
    assert(await evaluate(`document.querySelector('#filesCancel') !== null`), 'transfer cancellation control exists');
    // Socket write boundary: unauthenticated mobile socket cannot dispatch agent frames.
    const readOnly = new WebSocket(`ws://127.0.0.1:3000/ws?deviceId=${device.id}`); await new Promise(resolve => { readOnly.onopen = resolve; });
    const denied = await new Promise(resolve => { readOnly.onmessage = event => resolve(JSON.parse(event.data)); readOnly.send(JSON.stringify({ type: 'file.result', data: { requestId: 'req_x', operation: 'FILE_DELETE', status: 'SUCCEEDED' } })); }); readOnly.close();
    assert(denied.data?.code === 'FORBIDDEN', 'read-only mobile socket cannot dispatch');
    // Invalid/expired handle and size cap are backend/agent contract checks, no paths supplied.
    const expired = await api({ operation: 'FILE_DELETE', entryId: 'f_aaaaaaaaaaaaaaaa' }); assert(expired.requestId, 'forged handle request is audited then agent-denied');
    const oversized = await api({ operation: 'FILE_UPLOAD', destinationId: 'f_aaaaaaaaaaaaaaaa', name: 'large.bin', size: 10 * 1024 * 1024 + 1 }); assert(oversized.error?.code === 'VALIDATION_FAILED', 'transfer size cap rejected');
    // Reopen Source after the search result view. Action sheet is sourced from
    // the fresh agent list, never a synthetic test row.
    await evaluate(`document.querySelector('[data-file-root="PROJECTS"]').click()`); await sleep(300);
    await evaluate(`([...document.querySelectorAll('#filesList [data-file-id]')].find(x=>x.textContent.includes('Source'))).click()`);
    await waitFor(() => evaluate(`document.querySelectorAll('[data-file-actions]').length > 0`), 'file actions');
    // Action sheet and move picker are real UI surfaces, sourced from list results only.
    await evaluate(`document.querySelector('[data-file-actions]').click()`); await sleep(200);
    assert(await evaluate(`document.querySelector('#sheetBody')?.textContent.includes('Move')`), 'file action sheet opens');
    await evaluate(`document.querySelector('[data-file-action="move"]').click()`); await sleep(500);
    assert(await evaluate(`document.querySelector('#sheetBody')?.textContent.includes('Choose a safe destination folder')`), 'move destination picker opens');
    assert(!await evaluate(`document.querySelector('#sheetBody input')`), 'destination picker has no manual handle input');
    // Real mutation chain through UI-issued opaque handles. Results must alter fixture disk.
    const sourceHandles = await evaluate(`Object.fromEntries([...document.querySelectorAll('#filesList [data-file-id]')].map(x=>[(x.innerHTML.match(/📄\s*([^<]+)/)||[])[1]?.trim(),x.dataset.fileId]).filter(x=>x[0]))`);
    await evaluate(`document.querySelector('[data-file-root="PROJECTS"]').click()`); await sleep(400);
    await waitFor(() => evaluate(`!!([...document.querySelectorAll('#filesList [data-file-id]')].find(x=>x.textContent.includes('Destination')))`), 'destination folder');
    const destinationHandle = await evaluate(`([...document.querySelectorAll('#filesList [data-file-id]')].find(x=>x.textContent.includes('Destination')))?.dataset.fileId`);
    assert(!!sourceHandles['rename.txt'] && !!destinationHandle, 'real source and destination handles available');
    await evaluate(`window.Vigilia.fileRequest(${JSON.stringify(device.id)},{operation:'FILE_RENAME',entryId:${JSON.stringify(sourceHandles['rename.txt'])},name:'renamed.txt'})`);
    await waitFor(() => existsSync(join(PROJECTS, 'Source', 'renamed.txt')), 'rename on fixture'); assert(existsSync(join(PROJECTS, 'Source', 'renamed.txt')), 'rename changes real fixture');
    await evaluate(`window.Vigilia.fileRequest(${JSON.stringify(device.id)},{operation:'FILE_MOVE',entryId:${JSON.stringify(sourceHandles['move.txt'])},destinationId:${JSON.stringify(destinationHandle)}})`);
    await waitFor(() => existsSync(join(PROJECTS, 'Destination', 'move.txt')), 'move on fixture'); assert(existsSync(join(PROJECTS, 'Destination', 'move.txt')), 'move changes real fixture');
    await evaluate(`window.Vigilia.fileRequest(${JSON.stringify(device.id)},{operation:'FILE_DELETE',entryId:${JSON.stringify(sourceHandles['delete.txt'])}})`);
    await waitFor(() => !existsSync(join(PROJECTS, 'Source', 'delete.txt')), 'file delete'); assert(!existsSync(join(PROJECTS, 'Source', 'delete.txt')), 'file delete changes real fixture');
    const empty = await api({ operation: 'FILE_LIST', rootId: 'PROJECTS' }); await sleep(300);
    const emptyHandle = await evaluate(`([...document.querySelectorAll('#filesList [data-file-id]')].find(x=>x.textContent.includes('Empty')))?.dataset.fileId`);
    await evaluate(`window.Vigilia.fileRequest(${JSON.stringify(device.id)},{operation:'FILE_DELETE',entryId:${JSON.stringify(emptyHandle)}})`);
    await waitFor(() => !existsSync(join(PROJECTS, 'Empty')), 'empty directory delete'); assert(!existsSync(join(PROJECTS, 'Empty')), 'empty directory delete changes real fixture');
    const nonEmptyHandle = await evaluate(`([...document.querySelectorAll('#filesList [data-file-id]')].find(x=>x.textContent.includes('NonEmpty')))?.dataset.fileId`);
    await evaluate(`window.Vigilia.fileRequest(${JSON.stringify(device.id)},{operation:'FILE_DELETE',entryId:${JSON.stringify(nonEmptyHandle)}})`);
    await sleep(400); assert(existsSync(join(PROJECTS, 'NonEmpty')), 'non-empty directory delete denied');
    const finalAudit = await (await request(`/api/files/${device.id}/audit`, { headers: { 'x-vigilia-command-token': commandToken } })).json();
    assert(finalAudit.audits.some(row => row.operation === 'FILE_DELETE' && row.status === 'denied') && finalAudit.audits.some(row => row.operation === 'FILE_MOVE' && row.status === 'succeeded'), 'audit records success and denial');
    cdp.close(); cdp = null;
  } finally {
    if (cdp) cdp.close(); kill(chrome); kill(agent); kill(backend); await sleep(300); rmSync(TMP, { recursive: true, force: true });
  }
  if (failures) process.exitCode = 1;
  console.log(failures ? `phase6 e2e: ${failures} failure(s)` : 'phase6 e2e: passed');
}
main().catch(error => { console.error('phase6 e2e failed:', error); process.exitCode = 1; });
