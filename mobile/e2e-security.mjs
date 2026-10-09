/**
 * VIGILIA — Phase 4E real end-to-end + security regression.
 *
 * The whole point of this run is that nothing is mocked: a real Linux agent
 * is connected to a real backend, and the mobile UI is loaded in a real
 * browser. Every assertion is made against the live system.
 *
 * Two things are checked, and the second is the one that has no unit test:
 *
 *   A. The command→UI race. The agent's `command.result` and the application
 *      catalog both arrive, and the catalog is derived from the agent's
 *      *previous* process snapshot — so a catalog still in flight when the
 *      agent answers can describe a state that predates the command. The
 *      settled state must win; the catalog must not un-flip the row.
 *
 *   B. The security invariants of the Phase 4 surface, end to end, through
 *      the same sockets and routes the phone actually uses:
 *        - a read-only mobile socket cannot write anything
 *        - an agent socket cannot speak for another device
 *        - no route accepts a path, and no cleanup can name a file
 *        - a command token for one device cannot command another
 *        - the UI itself exposes no path, shell or free-text command affordance
 *
 * Run:  node mobile/e2e-security.mjs
 *       VIGILIA_DEVICE_ID=cm… VIGILIA_COMMAND_TOKEN=vc_… node mobile/e2e-security.mjs
 */
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { WebSocket } from 'ws';

const BASE = process.env.VIGILIA_BASE_URL || 'http://127.0.0.1:3000';
const CHROME = process.env.CHROME_BIN || '/snap/bin/chromium';
// A fresh profile per run: a reused one restores the previous run's DOM from
// the bfcache, and the last run's catalog would still be on screen.
const PROFILE = mkdtempSync(join(tmpdir(), 'vigilia-security-e2e-'));
const CDP_PORT = 9371;
const sleep = ms => new Promise(r => setTimeout(r, ms));

let failures = 0;
const assert = (c, m) => { console.log(c ? `  ✔ ${m}` : `  ✖ ${m}`); if (!c) failures++; };

async function registerDevice(name, hostname) {
  const res = await fetch(`${BASE}/api/devices/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, os: 'ubuntu', hostname, protocolVersion: 1 }),
  });
  if (!res.ok) throw new Error(`register failed: ${res.status}`);
  return res.json();
}

/** A mobile (read-only) socket: `?deviceId=` with no credentials. */
function mobileSocket(deviceId) {
  return new WebSocket(`${BASE.replace('http', 'ws')}/ws?deviceId=${encodeURIComponent(deviceId)}`);
}

/** An agent socket: the subprotocol carries the agent token. */
function agentSocket(deviceId, token) {
  return new WebSocket(`${BASE.replace('http', 'ws')}/ws`, [`vigilia.${deviceId}.${token}`]);
}

/** Collect every frame received on a socket, keyed by type. */
function collect(socket) {
  const frames = [];
  const done = new Promise(resolve => { socket.onclose = () => resolve(frames); });
  socket.onmessage = e => {
    try { frames.push(JSON.parse(e.data)); } catch { /* not JSON */ }
  };
  return { frames, done };
}

async function main() {
  // ---------------- the two devices ----------------
  // The real agent's device, plus a second registered device that exists only
  // to be the target of a cross-device probe. Nothing is connected to it.
  const { devices } = await (await fetch(`${BASE}/api/devices`)).json();
  const live = devices.find(d => d.status === 'online');
  if (!live) throw new Error('no online device; start the agent first');
  const DEV = process.env.VIGILIA_DEVICE_ID || live.id;
  const CT = process.env.VIGILIA_COMMAND_TOKEN;
  if (!CT) throw new Error('VIGILIA_COMMAND_TOKEN is required (pair the device first)');

  const other = await registerDevice(`E2E Security Probe ${process.hrtime.bigint().toString(36).slice(-6)}`,
    `e2e-sec-${process.hrtime.bigint().toString(36).slice(-6)}`);
  const OTHER = other.deviceId;
  const OTHER_AGENT_TOKEN = other.token;
  console.log(`security e2e: live device ${DEV}, probe device ${OTHER}`);

  // A real application from the live agent's own catalog, to command.
  const { applications } = await (await fetch(`${BASE}/api/applications/${DEV}`)).json();
  const target = (applications || []).find(a => a.applicationId === 'gnome-calculator')
    || (applications || [])[0];
  if (!target) throw new Error('the live agent reported no controllable application');
  const TARGET = target.applicationId;
  console.log(`  target application: ${TARGET}`);

  // ==================================================================
  // B1. a read-only mobile socket cannot push anything
  // ==================================================================
  console.log('\n— read-only socket:');
  {
    const ro = mobileSocket(DEV);
    const { frames, done } = collect(ro);
    await new Promise(r => ro.onopen = r);
    // Every frame type an agent may send, from the read-only side. The
    // telemetry one carries a marker the assertions below look for.
    //
    // Retried: the server attaches its message listener after `authenticate`
    // awaits, so a frame sent in the handshake gap is dropped with no error
    // at all, and a single send is a coin flip on timing.
    const pushes = [
      JSON.stringify({ type: 'telemetry', data: {
        deviceId: DEV, cpu: 99, ram: 99, cpuTemp: null, gpu: null, gpuTemp: null,
        timestamp: new Date().toISOString(),
      } }),
      ...['heartbeat', 'processes', 'applications', 'storage', 'cleanup', 'command.result', 'clean.result']
        .map(type => JSON.stringify({ type, data: { deviceId: DEV } })),
    ];
    for (let i = 0; i < 25; i++) {
      for (const raw of pushes) ro.send(raw);
      for (let j = 0; j < 10 && !frames.some(f => f.type === 'error'); j++) await sleep(100);
      if (frames.some(f => f.type === 'error')) break;
    }
    const seenNow = [...frames];
    ro.close();
    const seen = await done;
    const observed = seen.length ? seen : seenNow;
    const forbidden = observed.find(f => f.type === 'error' && f.data?.code === 'FORBIDDEN');
    assert(!!forbidden, 'a read-only socket that pushes is answered FORBIDDEN');
    assert(!observed.some(f => f.type === 'telemetry.latest' && f.data?.telemetry?.cpu === 99),
      'the read-only socket never saw its own telemetry echoed back');
    // The replay on connect is expected and is not the push: confirm nothing
    // reached the stored telemetry.
    const latest = await (await fetch(`${BASE}/api/monitoring/${DEV}/latest`)).json();
    assert(latest.telemetry?.cpu !== 99,
      'the read-only push never reached the stored telemetry');

    // And nothing reached the database: a pushed storage breakdown did not
    // become this device's report.
    const stored = await (await fetch(`${BASE}/api/storage/${DEV}`)).json();
    const pushed = stored.summary?.categories?.some(c => c.bytes === 123456789);
    assert(!pushed, 'the read-only socket never wrote a storage breakdown');
  }

  // ==================================================================
  // B2. an agent socket cannot describe another device
  // ==================================================================
  console.log('\n— cross-device frames:');
  {
    // Watch the *live* device's socket too: a foreign frame must not reach
    // the device it tried to name, so the victim's stream is the place to
    // look for the poison.
    const victim = mobileSocket(DEV);
    const victimFrames = collect(victim);
    await new Promise(r => victim.onopen = r);

    // The probe device's own agent socket is legitimate; it is the frame's
    // claimed deviceId that is a lie.
    const ag = agentSocket(OTHER, OTHER_AGENT_TOKEN);
    const { frames, done } = collect(ag);
    await new Promise(r => ag.onopen = r);
    ag.send(JSON.stringify({ type: 'agent.hello', data: { agentVersion: '1.0.0', protocolVersion: 1 } }));
    for (let i = 0; i < 20 && !frames.some(f => f.type === 'backend.hello'); i++) await sleep(100);
    if (!frames.some(f => f.type === 'backend.hello')) throw new Error('probe agent protocol negotiation failed');

    // The probe performs the same protocol negotiation as a real agent before
    // testing that it cannot claim another device's identity.
    const poison = JSON.stringify({
      type: 'storage',
      data: {
        deviceId: DEV, // the live device, not this connection's
        totalBytes: 1, usedBytes: 1, freeBytes: 0, usagePercent: 1,
        timestamp: new Date().toISOString(),
        categories: [{ categoryId: 'tmp', label: 'tmp', bytes: 123456789, itemCount: 1, safeToAnalyze: true }],
      },
    });
    for (let i = 0; i < 25; i++) {
      ag.send(poison);
      for (let j = 0; j < 10 && !frames.some(f => f.type === 'error'); j++) await sleep(100);
      if (frames.some(f => f.type === 'error')) break;
    }
    const seenNow = [...frames];
    ag.close();
    victim.close();
    const seen = await done;
    await victimFrames.done;
    const observed = seen.length ? seen : seenNow;
    const mismatch = observed.find(f => f.type === 'error' && f.data?.code === 'FORBIDDEN');
    assert(!!mismatch, 'a frame naming another device is refused with FORBIDDEN');
    assert(!observed.some(f => JSON.stringify(f.data || {}).includes('123456789')),
      'the mismatched payload was never stored or broadcast');

    // The live device's breakdown is untouched.
    const stored = await (await fetch(`${BASE}/api/storage/${DEV}`)).json();
    const poisoned = stored.summary?.categories?.some(c => c.bytes === 123456789);
    assert(!poisoned, 'the live device\'s breakdown was not overwritten by a foreign frame');
  }

  // ==================================================================
  // B3. no surface accepts a path or a shell command
  // ==================================================================
  console.log('\n— path and command injection:');
  {
    // A cleanup request is `{ command }` only: there is no field for a path,
    // so a probe that tries to smuggle one is rejected by the schema.
    for (const body of [
      { command: 'CLEAN_TEMP', target: '/etc/passwd' },
      { command: 'CLEAN_TEMP', path: '/home' },
      { command: 'rm -rf /' },
      { command: 'CLEAN_/etc' },
      { command: '../../etc' },
    ]) {
      const res = await fetch(`${BASE}/api/devices/${DEV}/commands`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-vigilia-command-token': CT },
        body: JSON.stringify(body),
      });
      assert(res.status === 400,
        `a cleanup body carrying ${JSON.stringify(body)} is rejected (got ${res.status})`);
    }
    // An application target is an id the device registered, resolved by the
    // agent — a path or a glob is not one.
    for (const bad of ['/usr/bin/firefox', 'firefox; rm -rf /', '..', '']) {
      const res = await fetch(`${BASE}/api/devices/${DEV}/commands`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-vigilia-command-token': CT },
        body: JSON.stringify({ command: 'APPLICATION_OPEN', target: bad }),
      });
      assert(res.status === 400,
        `an application target ${JSON.stringify(bad)} is rejected (got ${res.status})`);
    }
    // A path-shaped target the catalog does not contain is NOT_ALLOWED, not a
    // launch: verify the honest target is still the only thing that resolves.
    const notAllowed = await fetch(`${BASE}/api/devices/${DEV}/commands`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-vigilia-command-token': CT },
      body: JSON.stringify({ command: 'APPLICATION_OPEN', target: 'sudo' }),
    });
    assert(notAllowed.status === 400,
      `an application the device does not offer is rejected (got ${notAllowed.status})`);
    const audit = await (await fetch(`${BASE}/api/devices/${DEV}/commands?limit=50`, {
      headers: { 'x-vigilia-command-token': CT },
    })).json();
    assert(!audit.commands.some(c => /passwd|rm -rf|\.\.\/|\ \/usr\/bin/.test(`${c.command} ${c.target ?? ''}`)),
      'no rejected probe reached the audit trail');
  }

  // ==================================================================
  // B4. a command token is bound to one device
  // ==================================================================
  console.log('\n— token scope:');
  {
    const res = await fetch(`${BASE}/api/devices/${OTHER}/commands`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-vigilia-command-token': CT },
      body: JSON.stringify({ command: 'CLEAN_TEMP' }),
    });
    assert(res.status === 401,
      `the live device's command token cannot command another device (got ${res.status})`);
    const read = await fetch(`${BASE}/api/devices/${OTHER}/commands`, {
      headers: { 'x-vigilia-command-token': CT },
    });
    assert(read.status === 401,
      `the live device's token cannot read another device's audit (got ${read.status})`);
    const none = await fetch(`${BASE}/api/devices/${DEV}/commands`, {
      headers: { 'x-vigilia-command-token': 'vc_wrong' },
    });
    assert(none.status === 401, `a bad command token is rejected (got ${none.status})`);
  }

  // ==================================================================
  // B5. the mobile UI exposes no path or shell affordance
  // ==================================================================
  console.log('\n— the UI:');
  const chrome = spawn(CHROME, ['--headless=new', '--no-sandbox', '--disable-gpu',
    `--user-data-dir=${PROFILE}`, `--remote-debugging-port=${CDP_PORT}`, 'about:blank'], { stdio: 'ignore' });
  let targets = null;
  for (let i = 0; i < 40; i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json`)).json(); if (targets.length) break; } catch { /* booting */ }
    await sleep(250);
  }
  const page = targets?.find(t => t.type === 'page');
  if (!page) { chrome.kill(); throw new Error('no CDP page'); }

  const cdp = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => cdp.onopen = r);
  let seq = 0;
  const pending = new Map();
  cdp.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  const send = (method, params = {}) => new Promise(res => { const id = ++seq; pending.set(id, res); cdp.send(JSON.stringify({ id, method, params })); });
  const evalJs = async expression => {
    const out = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (out.result?.exceptionDetails) throw new Error(`eval failed: ${JSON.stringify(out.result.exceptionDetails)}`);
    return out.result?.result?.value;
  };
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Page.navigate', { url: `${BASE}/?device=${DEV}#control` });
  await sleep(1500);
  await evalJs(`localStorage.setItem('vigilia.commandToken', ${JSON.stringify(CT)})`);
  await sleep(2500);

  const rowState = () => evalJs(`(()=>{const c=[...document.querySelectorAll('#appList .app')]
    .find(a=>a.dataset.appId===${JSON.stringify(TARGET)});return c?.querySelector('.pill')?.textContent;})()`);

  // Wait for the catalog to render before reading the row: the socket
  // replays it asynchronously, and reading too early treats a not-yet-loaded
  // list as "nothing is running".
  for (let i = 0; i < 20; i++) {
    if (await evalJs(`document.querySelectorAll('#appList .app').length`) > 0) break;
    await sleep(300);
  }
  const startedAs = await rowState();
  console.log(`  ${TARGET} before: ${startedAs}`);
  const rowsNow = await evalJs(`[...document.querySelectorAll('#appList .app')].map(a=>a.dataset.appId)`);
  assert(rowsNow.length > 0, `the control screen lists the device's real catalog (${rowsNow.length} applications)`);

  // ==================================================================
  // A. the command → catalog race, against the live agent
  // ==================================================================
  // The agent's real catalog interval is 120 s, so drive the race directly:
  // dispatch a close, wait for the agent's `command.result`, then deliver a
  // stale catalog — the exact frame order the bug was seen in — and assert
  // the row the agent settled on survives it.
  console.log('\n— the command/catalog race:');
  const wantClose = /Running/i.test(startedAs || '');

  if (!wantClose) {
    // Open it first so there is a running row to close. This is a real
    // dispatch the real agent really executes.
    await evalJs(`(()=>{const c=[...document.querySelectorAll('#appList .app')]
      .find(a=>a.dataset.appId===${JSON.stringify(TARGET)});
      const b=[...c.querySelectorAll('button')].find(b=>/Open/i.test(b.textContent));b?.click();return 1;})()`);
    // Poll the row rather than sleeping a fixed interval: the agent's answer
    // and its catalog re-push both arrive asynchronously, and a fixed sleep
    // races whichever lands last.
    let opened = '';
    for (let i = 0; i < 20; i++) {
      opened = await rowState() || '';
      if (/Running/i.test(opened)) break;
      await sleep(400);
    }
    console.log(`  ${TARGET} after open: ${opened}`);
    assert(/Running/i.test(opened), 'the agent confirmed the open and the row flipped');
  }

  // Now close it, and race the stale catalog into the gap.
  //
  // The reproduction matters here: the bug is an ordering, not a value. The
  // agent's own re-push of the catalog (which now follows an application
  // command) would mask a stale push that arrives later, so the stale one has
  // to land *first* — which is exactly the interleaving the WS replay and the
  // REST refresh can both produce. Hooking `settleAppCommand` injects the
  // stale catalog in the moment between the agent's answer arriving and the
  // row being repainted, which is the whole window the bug lived in.
  const { applications: freshCatalog } = await (await fetch(`${BASE}/api/applications/${DEV}`)).json();
  const stalePayload = JSON.stringify({ applications: (freshCatalog || []).map(a => ({
    applicationId: a.applicationId,
    label: a.label,
    running: a.applicationId === TARGET ? true : a.running, // the pre-close view
  })) });
  await evalJs(`(window.__staleOnSettle = ${JSON.stringify(stalePayload)}, window.__armed = true, 1)`);

  const closedAt = new Date();
  await evalJs(`(()=>{
    const realSettle = window.settleAppCommand;
    const realCatalog = window.setAppCatalog;
    // Inject the stale catalog immediately *before* the settle runs, so the
    // two land in the order that reproduces the race: stale catalog first,
    // then the agent's answer on top of it.
    window.settleAppCommand = function (data) {
      if (window.__armed && window.__staleOnSettle) {
        window.__armed = false;
        realCatalog(JSON.parse(window.__staleOnSettle));
      }
      return realSettle(data);
    };
    const c=[...document.querySelectorAll('#appList .app')]
      .find(a=>a.dataset.appId===${JSON.stringify(TARGET)});
    const b=[...c.querySelectorAll('button')].find(b=>/Close/i.test(b.textContent));b?.click();return 1;})()`);

  // Wait for the agent's own answer. The audit row is the ground truth here:
  // it settles when the agent replies, which is the moment the catalog push
  // can land and clobber the row.
  let settled = null;
  for (let i = 0; i < 30; i++) {
    const { commands } = await (await fetch(`${BASE}/api/devices/${DEV}/commands?limit=10`, {
      headers: { 'x-vigilia-command-token': CT },
    })).json();
    const row = commands.find(c => c.command === 'APPLICATION_CLOSE' && c.target === TARGET
      && new Date(c.createdAt) >= closedAt);
    if (row && row.status !== 'queued' && row.status !== 'sent') { settled = row; break; }
    await sleep(400);
  }
  assert(settled !== null, 'the live agent settled the close');
  console.log(`  audit: ${settled?.command} → ${settled?.status} · ${(settled?.result || '').slice(0, 80)}`);

  const afterSettle = await rowState();
  console.log(`  ${TARGET} after the agent's answer: ${afterSettle}`);

  // The stale catalog was delivered first, through the same function the WS
  // replay and the REST refresh both call. The row the agent settled on must
  // survive it.
  assert(/Stopped/i.test(afterSettle || ''),
    'a stale catalog delivered before the agent\'s push does not un-flip the settled row');

  // The override must expire, not live forever: a catalog that genuinely
  // agrees with the agent is authoritative again.
  const agrees = { applications: (freshCatalog || []).map(a => ({
    applicationId: a.applicationId, label: a.label,
    running: a.applicationId === TARGET ? false : a.running,
  })) };
  await evalJs(`setAppCatalog(${JSON.stringify(agrees)})`);
  await sleep(400);
  assert(/Stopped/i.test((await rowState()) || ''),
    'a catalog that agrees with the agent renders unchanged');

  // The TTL path: an override older than the window stops protecting the row.
  await evalJs(`(()=>{ const s = settledCommand; if (!s) return 0;
    s.at = 0; applySettledCommand(); renderAppList(); return 1; })()`);
  await evalJs(`setAppCatalog(${JSON.stringify(JSON.parse(stalePayload))})`);
  await sleep(400);
  const afterExpiry = await rowState();
  assert(/Running/i.test(afterExpiry || ''),
    `once the override expires, the catalog is authoritative again (got ${afterExpiry})`);

  // ==================================================================
  // B5 continued: the screens carry no path, no shell, no free command
  // ==================================================================
  console.log('\n— the screens:');
  // Only a free-text field can carry a path or a shell command. The control
  // screen has a search box (it filters the device's own catalog by name —
  // it never reaches the agent) and the cleaner has checkboxes (each selects
  // one structured action id). Neither is a command surface, so pin them by
  // what they are rather than by count.
  const freeText = ['#s-storage', '#s-cleaner', '#s-activity'];
  for (const sel of freeText) {
    const inputs = await evalJs(`document.querySelectorAll('${sel} input[type=text], ${sel} input[type=search], ${sel} textarea, ${sel} input:not([type])').length`);
    assert(inputs === 0, `${sel} has no free-text field (no path and no command can be entered)`);
  }
  const controlInputs = await evalJs(`[...document.querySelectorAll('#s-control input, #s-control textarea')].map(e => e.id)`);
  assert(controlInputs.every(id => id === 'appSearch'),
    `the control screen's only input is the catalog search box (got ${JSON.stringify(controlInputs)})`);
  const bodyText = await evalJs(`document.body.textContent`);
  assert(!/rm -rf|\/bin\/sh|powershell|cmd \/c/i.test(bodyText),
    'no shell command is rendered anywhere in the UI');

  cdp.close();
  chrome.kill();

  if (failures) { console.log(`\n${failures} assertion(s) failed`); process.exit(1); }
  console.log('\nsecurity e2e: all assertions passed');
  process.exit(0);
}

main().catch(err => { console.error('security e2e failed:', err); process.exit(1); });
