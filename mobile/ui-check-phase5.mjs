/**
 * Render check for the Phase 5 mobile UI.
 *
 * Loads the real backend (which has the real agent connected) in headless
 * Chromium and asserts the security surfaces render from the API rather than
 * from the mockup: the trust state, the honest capabilities, the daily
 * cleanup card and the guest screen.
 */
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { WebSocket } from 'ws';

const BASE = process.env.VIGILIA_BASE_URL || 'http://127.0.0.1:3000';
const CHROME = process.env.CHROME_BIN || '/snap/bin/chromium';
const PROFILE = mkdtempSync(join(tmpdir(), 'vigilia-ui-'));
const CDP_PORT = 9382;
const sleep = ms => new Promise(r => setTimeout(r, ms));

let failures = 0;
const assert = (c, m) => { console.log(c ? `  ✔ ${m}` : `  ✖ ${m}`); if (!c) failures++; };

async function main() {
  const { devices } = await (await fetch(`${BASE}/api/devices`)).json();
  const live = devices.find(d => d.status === 'online');
  if (!live) throw new Error('no online device; start the agent first');
  const DEV = live.id;
  console.log(`ui check: device ${DEV} (${live.os})`);

  const sec = await (await fetch(`${BASE}/api/security/${DEV}`)).json();
  console.log(`  backend reports state=${sec.presence.state} events=${sec.events.length}`);

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
  const collect = () => evalJs(`window.__consoleErrors || []`);

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Page.navigate', { url: `${BASE}/?device=${DEV}` });
  await sleep(2500);
  for (let i = 0; i < 30; i++) {
    const ready = await evalJs(`!!document.querySelector('#secStateText')?.textContent`);
    if (ready) break;
    await sleep(300);
  }

  // ---- the Security screen ----
  await evalJs(`document.querySelector('[data-nav="security"]')?.click()`);
  await sleep(800);

  const secStateText = await evalJs(`document.querySelector('#secStateText')?.textContent`);
  console.log(`  security screen: ${secStateText}`);
  assert(!!secStateText, 'the security screen renders a state line');
  assert(/Unknown|Guest|Owner|Locked/i.test(secStateText || ''),
    'the state line reports a real trust state, not the placeholder');

  const modeChip = await evalJs(`document.querySelector('#secModeChip')?.textContent`);
  assert(!!modeChip && modeChip !== 'Owner Mode', `the mode chip is the reported state (${modeChip})`);

  const faceVal = await evalJs(`document.querySelector('#secFaceAuth')?.textContent`);
  assert(/Unavailable|Active/i.test(faceVal || ''), `face capability is reported (${faceVal})`);

  const livenessVal = await evalJs(`document.querySelector('#secLiveness')?.textContent`);
  assert(/Enabled|Unavailable/i.test(livenessVal || ''), `liveness is reported (${livenessVal})`);

  const lastCleanup = await evalJs(`document.querySelector('#secLastCleanup')?.textContent`);
  assert(/Never|\\d/.test(lastCleanup || ''), `last cleanup is a real value (${lastCleanup})`);

  // No invented owner name anywhere on the screen.
  const secBody = await evalJs(`document.querySelector('#s-security')?.textContent || ''`);
  assert(!/Haris|haris@vigilia/.test(secBody), 'the security screen shows no fabricated owner name');
  assert(!/14:32|Yesterday, 23:14/.test(secBody), 'the security screen shows no fabricated timestamp');

  // ---- the guest screen ----
  await evalJs(`document.querySelector('[data-nav="guest"]')?.click() || document.querySelector('#s-guest [data-nav="guest"]')?.click()`);
  await sleep(500);
  await evalJs(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>/Guest/i.test(b.textContent)); if(b&&location.hash!=='#guest'){b.click();} return 1})()`);
  await sleep(700);
  const guestTitle = await evalJs(`document.querySelector('#guestBannerTitle')?.textContent`);
  console.log(`  guest screen: ${guestTitle}`);
  assert(!!guestTitle && guestTitle !== 'Guest Mode Active' || sec.presence.state === 'GUEST',
    'the guest banner reports the real state, not a static "Guest Mode Active"');
  const guestEvents = await evalJs(`document.querySelectorAll('#guestEvents .ev-body').length`);
  // An empty timeline renders one placeholder child — the honest empty state,
  // not an invented row — so it counts as zero real events.
  assert(guestEvents === Math.min(sec.events.length, 25),
    `the guest timeline mirrors the backend events (${guestEvents} vs ${sec.events.length})`);
  const guestCpu = await evalJs(`document.querySelector('#guestCpu')?.textContent`);
  assert(guestCpu !== '34%', `the guest CPU tile is not the mockup's 34% (got ${guestCpu})`);

  // ---- the home card ----
  await evalJs(`document.querySelector('[data-nav="home"]')?.click()`);
  await sleep(500);
  const homeText = await evalJs(`document.querySelector('#homeSecText')?.textContent`);
  console.log(`  home card: ${homeText}`);
  assert(!!homeText && !/Owner Mode • Protected/.test(homeText),
    'the home card no longer shows the fabricated "Owner Mode • Protected"');
  assert(/Unknown|Guest|Owner|Locked|Waiting/i.test(homeText || ''), 'the home card reports a real state');

  // ---- the settings card ----
  await evalJs(`document.querySelector('[data-nav="settings"]')?.click()`);
  await sleep(500);
  const setOwner = await evalJs(`document.querySelector('#settingsOwnerTitle')?.textContent`);
  assert(!/Haris/.test(setOwner || ''), `settings shows no fabricated owner name (got ${setOwner})`);
  const setOs = await evalJs(`document.querySelector('#setDeviceOs')?.textContent`);
  assert(!!setOs && setOs !== 'Ubuntu / Win 11', `settings shows the real OS (got ${setOs})`);

  cdp.close();
  chrome.kill();

  if (failures) { console.log(`\n${failures} assertion(s) failed`); process.exit(1); }
  console.log('\nui check: all assertions passed');
  process.exit(0);
}

main().catch(err => { console.error('ui check failed:', err); process.exit(1); });
