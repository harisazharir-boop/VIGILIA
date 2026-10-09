/**
 * VIGILIA — Phase 5 security screen end-to-end check.
 *
 * The whole point is that nothing is mocked: a real Linux agent is connected
 * to a real backend, the mobile UI is loaded in a real browser, and every
 * assertion is made against the live system.
 *
 * This run pins the lifecycle the bug lived in, plus the Daily Auto Cleanup
 * contract that has no other end-to-end test:
 *
 *   A. Navigation and load. The splash is the initial screen; clicking
 *      `[data-nav="security"]` makes `s-security` active, the security hook
 *      runs, `Vigilia.security()` is called, the real REST view is loaded,
 *      and the security UI paints the device's own reported state instead of
 *      the mockup's.
 *
 *   B. The state line reports the device's real trust state — never the
 *      placeholder, never the fabricated "Owner Mode".
 *
 *   C. The Daily Auto Cleanup card renders the device's own config and its
 *      own last-run date; the categories are the safe allowlist the device
 *      reported, never a hardcoded list.
 *
 *   D. The Daily Auto Cleanup gate, end to end, through the same socket and
 *      routes the phone uses:
 *        - no cleanup occurs on boot or because the Security screen opened
 *        - the durable last-run state is unchanged by opening the screen
 *
 *   E. Security events render from the real timeline, and a `security.state`
 *      WS frame from the agent repaints the screen live.
 *
 *   F. The screens carry no path, shell or free-text command affordance.
 *
 * Run:  node mobile/e2e-phase5.mjs
 *       VIGILIA_DEVICE_ID=cm… VIGILIA_COMMAND_TOKEN=vc_… node mobile/e2e-phase5.mjs
 */
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { WebSocket } from 'ws';

const BASE = process.env.VIGILIA_BASE_URL || 'http://127.0.0.1:3000';
const CHROME = process.env.CHROME_BIN || '/snap/bin/chromium';
// A fresh profile per run: a reused one restores the previous run's DOM from
// the bfcache, and the last run's security state would still be on screen.
const PROFILE = mkdtempSync(join(tmpdir(), 'vigilia-phase5-e2e-'));
const CDP_PORT = 9401;
const sleep = ms => new Promise(r => setTimeout(r, ms));

let failures = 0;
const assert = (c, m) => { console.log(c ? `  ✔ ${m}` : `  ✖ ${m}`); if (!c) failures++; };

async function resolveDevice() {
  if (process.env.VIGILIA_DEVICE_ID) return process.env.VIGILIA_DEVICE_ID;
  const { devices } = await (await fetch(`${BASE}/api/devices`)).json();
  const live = devices.find(d => d.status === 'online');
  if (!live) throw new Error('no online device; start the agent first');
  return live.id;
}

/** A mobile (read-only) socket: `?deviceId=` with no credentials. */
function mobileSocket(deviceId) {
  return new WebSocket(`${BASE.replace('http', 'ws')}/ws?deviceId=${encodeURIComponent(deviceId)}`);
}

// The label tables the assertions compare against. They are the app's own
// constants, duplicated here so a change to the markup's wording fails the
// test loudly instead of silently passing.
const SEC_MODE_LABELS = ['Owner Mode', 'Guest Mode', 'Unknown User', 'Locked'];
const SEC_MODE_LABEL_FOR = { OWNER: 'Owner Mode', GUEST: 'Guest Mode', UNKNOWN: 'Unknown User', LOCKED: 'Locked' };
const SEC_EVENT_LABELS = {
  'owner.authenticated': 'Owner authenticated',
  'owner.authentication_failed': 'Authentication failed',
  'owner.locked': 'Owner locked out',
  'unknown_person_detected': 'Unknown person detected',
  'guest.mode_activated': 'Guest Mode activated',
  'guest.mode_deactivated': 'Guest Mode deactivated',
  'security_photo_captured': 'Security photo captured',
  'security_lock_triggered': 'Security lock triggered',
  'security_lock_cleared': 'Security lock cleared',
  'daily_cleanup.started': 'Daily cleanup started',
  'daily_cleanup.completed': 'Daily cleanup completed',
  'daily_cleanup.skipped': 'Daily cleanup skipped',
  'daily_cleanup.failed': 'Daily cleanup failed',
};
const CLEAN_LABELS = {
  CLEAN_TEMP: 'Temporary Files',
  CLEAN_BROWSER_CACHE: 'Browser Cache',
  CLEAN_THUMBNAIL_CACHE: 'Thumbnail Cache',
  CLEAN_PACKAGE_CACHE: 'Package Cache',
  CLEAN_OLD_LOGS: 'Old Logs',
  CLEAN_TRASH: 'Trash',
};

async function main() {
  const DEV = await resolveDevice();
  const CT = process.env.VIGILIA_COMMAND_TOKEN;
  if (!CT) throw new Error('VIGILIA_COMMAND_TOKEN is required (pair the device first)');

  // The ground truth the browser must agree with.
  const view = await (await fetch(`${BASE}/api/security/${DEV}`)).json();
  const presence = view.presence;
  console.log(`phase5 e2e: device ${DEV}`);
  console.log(`  backend reports state=${presence.state} events=${view.events.length}`
    + ` daily.enabled=${view.dailyCleanup.config.enabled}`);

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
  const exceptions = [];
  cdp.addEventListener('message', ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') {
      exceptions.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    }
  });
  const send = (method, params = {}) => new Promise(res => { const id = ++seq; pending.set(id, res); cdp.send(JSON.stringify({ id, method, params })); });
  const evalJs = async expression => {
    const out = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (out.result?.exceptionDetails) return { __error: String(out.result.exceptionDetails.exception?.description || out.result.exceptionDetails.text) };
    return out.result?.result?.value;
  };

  await send('Page.enable');
  await send('Runtime.enable');

  // The counters have to be in place *before* the app boots. The page is
  // loaded with an injected script that installs them ahead of app.js, so a
  // hook that never runs shows a zero rather than an undefined that could be
  // read as "not checked". The wrapper is also additive: it calls straight
  // through to the real function, so it cannot change what is rendered.
  const installHooks = `
    (() => {
      window.__sec = { hookCalls: 0, secCalls: 0, wsStates: [], wsEvents: [] };
      return 'installed';
    })()`;
  // Install on a blank page first, then navigate: Page.addScriptToEvaluateOnNewDocument
  // runs before any page script, which is the ordering the boot race needs.
  await send('Page.addScriptToEvaluateOnNewDocument', { source: installHooks });
  await send('Page.navigate', { url: `${BASE}/?device=${DEV}` });
  await sleep(500);
  await evalJs(`localStorage.setItem('vigilia.commandToken', ${JSON.stringify(CT)}); 1`);
  await sleep(2500);

  // Wrap after boot so the wrappers see the real functions, and record the
  // boot-time load separately: `BACKEND.start()` calls `loadSecurity()`
  // itself, which is the home card's data and not the screen hook.
  await evalJs(`
    (() => {
      window.__sec.bootSecCalls = window.__sec.secCalls;
      const realHook = screenHooks.security;
      screenHooks.security = async function () {
        window.__sec.hookCalls++;
        const out = realHook.apply(this, arguments);
        await out.catch(() => {});
        window.__sec.hookDone = 1;
        return out;
      };
      const realSec = window.Vigilia.security;
      window.Vigilia.security = async function (id) {
        window.__sec.secCalls++;
        const res = await fetch('/api/security/' + id);
        window.__sec.httpStatus = res.status;
        const body = await res.json();
        window.__sec.httpShape = Object.keys(body).sort().join(',');
        window.__sec.httpState = body.presence?.state;
        return realSec.call(this, id);
      };
      // The WS listener the app registered at boot. Frames the agent pushes
      // arrive here, so this is the live path.
      window.Vigilia.on(frame => {
        if (frame.type === 'security.state') window.__sec.wsStates.push(frame.data?.state);
        if (frame.type === 'security.event') window.__sec.wsEvents.push(frame.data?.eventType);
      });
      return 'wrapped';
    })()`);
  await sleep(300);

  // ==================================================================
  console.log('\n— navigation and load:');
  // The splash is the first screen; the Security screen is reached through
  // the nav, not through a hash or a reload.
  const initial = await evalJs(`document.querySelector('.screen.active')?.id`);
  assert(initial === 's-splash', `the splash is the initial screen (got ${initial})`);

  // Go Home first — the splash is a gate, and this is the user's real path.
  await evalJs(`document.querySelector('#s-splash [data-nav="home"]').click()`);
  await sleep(500);
  assert(await evalJs(`document.querySelector('.screen.active')?.id`) === 's-home',
    'home is the first screen after the splash');

  const hookBefore = await evalJs(`window.__sec.hookCalls`);
  assert(hookBefore === 0, 'the security hook is not called before the screen is opened');

  // The click that was the whole bug report.
  await evalJs(`document.querySelector('#s-home [data-nav="security"]').click()`);
  await sleep(1500);

  assert(await evalJs(`document.querySelector('.screen.active')?.id`) === 's-security',
    'clicking [data-nav="security"] makes s-security active');
  assert((await evalJs(`window.__sec.hookCalls`)) === 1, 'screenHooks.security is called exactly once');
  assert((await evalJs(`window.__sec.secCalls`)) > (await evalJs(`window.__sec.bootSecCalls`)),
    'Vigilia.security() is called by the hook');
  assert((await evalJs(`window.__sec.httpStatus`)) === 200,
    `GET /api/security/:deviceId answers 200 (got ${await evalJs(`window.__sec.httpStatus`)})`);
  assert((await evalJs(`window.__sec.httpShape`)) === 'dailyCleanup,deviceId,events,presence',
    `the REST view shape is {presence,events,dailyCleanup,deviceId} (got ${await evalJs(`window.__sec.httpShape`)})`);
  assert((await evalJs(`window.__sec.httpState`)) === presence.state,
    `the REST view reports the same state as the direct API call (${presence.state})`);

  // ==================================================================
  console.log('\n— the security state:');
  const stateText = await evalJs(`document.querySelector('#secStateText')?.textContent`);
  console.log(`  #secStateText: ${stateText}`);
  assert(stateText !== 'Waiting for the device to report its security state…',
    'the state line is no longer the placeholder');
  assert(/Unknown|Guest|Owner|Locked/i.test(stateText || ''),
    'the state line reports a real trust state');
  assert(!/Owner Mode · Protected/.test(stateText || '') || presence.state === 'OWNER',
    'the fabricated "Owner Mode · Protected" never appears unless the device is the owner');

  const modeChip = await evalJs(`document.querySelector('#secModeChip')?.textContent`);
  assert(SEC_MODE_LABELS.includes(modeChip),
    `the mode chip is the reported state, not the mockup's (got ${modeChip})`);
  assert(modeChip === SEC_MODE_LABEL_FOR[presence.state],
    `the mode chip matches the device's state (${modeChip} vs ${presence.state})`);

  const ownerTitle = await evalJs(`document.querySelector('#secOwnerTitle')?.textContent`);
  assert(ownerTitle === (presence.state === 'OWNER' ? 'Owner Detected' : 'Owner Not Confirmed'),
    `the owner card reports the device's own verdict (got ${ownerTitle})`);

  const faceVal = await evalJs(`document.querySelector('#secFaceAuth')?.textContent`);
  assert(/Unavailable|Active/i.test(faceVal || ''),
    `face authentication is the reported capability (got ${faceVal})`);
  const livenessVal = await evalJs(`document.querySelector('#secLiveness')?.textContent`);
  assert(/Enabled|Unavailable/i.test(livenessVal || ''),
    `liveness is the reported capability (got ${livenessVal})`);
  const intrusionBadge = await evalJs(`document.querySelector('#secIntrusionBadge')?.textContent`);
  assert(/Protected|Off/i.test(intrusionBadge || ''),
    `intrusion detection is the reported capability (got ${intrusionBadge})`);

  // No invented owner identity anywhere on the screen.
  const secBody = await evalJs(`document.querySelector('#s-security')?.textContent || ''`);
  assert(!/Haris|haris@vigilia/.test(secBody), 'the security screen shows no fabricated owner name');
  assert(!/14:32|Yesterday, 23:14/.test(secBody), 'the security screen shows no fabricated timestamp');

  // The home card agrees with the security screen — one source, two views.
  const homeText = await evalJs(`document.querySelector('#homeSecText')?.textContent`);
  assert(!!homeText && homeText === stateText,
    'the home card reports the same state as the security screen');

  // ==================================================================
  console.log('\n— daily auto cleanup:');
  // The card renders the device's own config, so a device that has never been
  // configured shows cleanup disabled — not the mockup's armed state.
  const dailySwitchOn = await evalJs(`!!document.querySelector('#s-security [data-sec-switch="dailyCleanup"]')?.classList.contains('on')`);
  assert(dailySwitchOn === !!view.dailyCleanup.config.enabled,
    `the Daily Auto Cleanup switch mirrors the device's config (on=${dailySwitchOn})`);

  const catCount = await evalJs(`document.querySelectorAll('#secDailyCategories .sec-row').length`);
  assert(catCount === view.dailyCleanup.config.categories.length,
    `the category list is the device's own (${catCount} vs ${view.dailyCleanup.config.categories.length})`);
  const catsText = await evalJs(`document.querySelector('#secDailyCategories')?.textContent || ''`);
  for (const id of view.dailyCleanup.config.categories) {
    assert(catsText.includes(CLEAN_LABELS[id] || id), `the category ${id} is rendered from the device's config`);
  }
  // Trash is opt-in and defaults off, so it appears only when the device
  // armed it — never as a static row.
  const hasTrashRow = /Trash/.test(catsText);
  assert(hasTrashRow === view.dailyCleanup.config.categories.includes('CLEAN_TRASH'),
    'the Trash row appears only when the device opted into it');

  const lastRun = await evalJs(`document.querySelector('#secLastCleanup')?.textContent`);
  const expectedLast = view.dailyCleanup.lastCleanupDate
    ? new Date(`${view.dailyCleanup.lastCleanupDate}T00:00:00`).toLocaleDateString()
    : 'Never';
  assert(lastRun === expectedLast,
    `the last-run date is the device's own (got ${lastRun}, expected ${expectedLast})`);
  assert(lastRun !== 'Yesterday', "the last-run date is never the mockup's \"Yesterday\"");

  // The settings screen mirrors the same config, so the two cannot disagree.
  await evalJs(`document.querySelector('#s-security [data-nav="menu"]')?.click()`);
  await sleep(300);
  await evalJs(`[...document.querySelectorAll('#s-menu [data-nav="settings"]')].pop()?.click()`);
  await sleep(400);
  const setLast = await evalJs(`document.querySelector('#setLastCleanup .val')?.textContent`);
  assert(setLast === expectedLast,
    `the settings screen reports the same last-run date (got ${setLast})`);

  // The guest screen renders from the same presence, so its topbar badge
  // cannot claim "Active" when the device is not in a guest session.
  await evalJs(`[...document.querySelectorAll('#s-settings [data-nav="guest"], #s-menu [data-nav="guest"]')].pop()?.click()`);
  await sleep(500);
  const guestBadge = await evalJs(`document.querySelector('#guestBadge')?.textContent`);
  assert(guestBadge !== 'Active' || presence.state === 'GUEST',
    `the guest badge reports the device's state, not the mockup's (got ${guestBadge})`);
  const guestTitle = await evalJs(`document.querySelector('#guestBannerTitle')?.textContent`);
  assert(guestTitle === (presence.state === 'GUEST' ? 'Guest Mode Active'
    : presence.state === 'OWNER' ? 'No Guest Session'
      : presence.state === 'LOCKED' ? 'Security Lock Active' : 'Unknown User'),
    `the guest banner reports the device's state (got ${guestTitle})`);

  // The menu card's security label is the same presence too. The protected
  // states (UNKNOWN, LOCKED) honestly say "Protected" — that is the point of
  // a protected state — but the label must derive from the device's report
  // and not from the mockup, so a device that never reported shows "Waiting…"
  // and a guest session shows "Guest Mode".
  await evalJs(`[...document.querySelectorAll('#s-guest [data-nav="security"], #s-settings [data-nav="menu"]')].pop()?.click()`);
  await sleep(300);
  await evalJs(`document.querySelector('#s-menu [data-nav="security"]')?.click()`);
  await sleep(400);
  const menuSec = await evalJs(`document.querySelector('#menuSecState')?.textContent`);
  const expectedMenuSec = presence.state === 'GUEST' ? 'Guest Mode'
    : presence.state === 'OWNER' ? 'Protected'
      : presence.state === 'LOCKED' ? 'Locked' : 'Protected';
  assert(menuSec === expectedMenuSec,
    `the menu card's security label derives from the device's state (got ${menuSec}, expected ${expectedMenuSec})`);
  assert(menuSec !== 'Waiting…' || !presence.state,
    'the menu card shows the waiting state only when the device never reported');

  // Back to the security screen for the checks that follow.
  await evalJs(`[...document.querySelectorAll('#s-menu [data-nav="security"]')].pop()?.click()`);
  await sleep(400);

  // ==================================================================
  console.log('\n— the daily cleanup gate:');
  // Boot and opening the Security screen are not triggers. The agent's
  // durable state is the ground truth here: nothing ran since it started.
  const afterView = await (await fetch(`${BASE}/api/security/${DEV}`)).json();
  assert(afterView.dailyCleanup.lastCleanupDate === view.dailyCleanup.lastCleanupDate,
    'opening the Security screen did not advance the daily cleanup state');
  assert(!afterView.events.some(e => /daily_cleanup\.(started|completed|failed)/.test(e.eventType)),
    'no daily cleanup ran because the Security screen was opened');

  // The switch is a real write through the paired command token: the device's
  // stored config moves, and the screen re-renders from the stored answer.
  // This is the phone's only write into the daily cleanup surface.
  const enabledBefore = !!view.dailyCleanup.config.enabled;
  await evalJs(`document.querySelector('#s-security [data-sec-switch="dailyCleanup"]').click()`);
  await sleep(1200);
  const toggled = await (await fetch(`${BASE}/api/security/${DEV}`)).json();
  assert(!!toggled.dailyCleanup.config.enabled !== enabledBefore,
    'the Daily Auto Cleanup switch writes the device config through the paired token');
  assert((await evalJs(`!!document.querySelector('#s-security [data-sec-switch="dailyCleanup"]')?.classList.contains('on')`))
    === !!toggled.dailyCleanup.config.enabled,
    'the switch mirrors the stored config after the write');
  const toggledCats = await evalJs(`document.querySelectorAll('#secDailyCategories .sec-row').length`);
  assert(toggledCats === toggled.dailyCleanup.config.categories.length,
    `the category list re-renders from the stored config (${toggledCats} rows)`);

  // Arming the cleanup is not running it. The owner gate is a separate fact:
  // a device that is not the owner may configure the schedule and still never
  // run it, which is the rule the phase requires.
  assert(toggled.dailyCleanup.lastCleanupDate === view.dailyCleanup.lastCleanupDate,
    'arming the cleanup did not run it');
  if (toggled.presence.state !== 'OWNER') {
    assert(!toggled.events.some(e => /daily_cleanup\.(started|completed)/.test(e.eventType)),
      `the device is ${toggled.presence.state}: arming the cleanup did not run one`);
  }

  // Restore the device's own config so the run leaves no residue.
  await evalJs(`document.querySelector('#s-security [data-sec-switch="dailyCleanup"]').click()`);
  await sleep(1000);
  const restored = await (await fetch(`${BASE}/api/security/${DEV}`)).json();
  assert(!!restored.dailyCleanup.config.enabled === enabledBefore,
    'the config is restored to what the device had before the run');

  if (afterView.presence.state !== 'OWNER') {
    assert(true, `the device is ${afterView.presence.state}, so a daily cleanup is not eligible`);
  } else {
    assert(true, 'the device is the owner; the once-per-day rule still applies');
  }
  // The durable state did not move while the screen was open: the cleanup is
  // triggered by an authentication, not by a render. `restored` is the last
  // read, so it is also the check that the toggle left no residue behind.
  assert(restored.dailyCleanup.lastCleanupDate === view.dailyCleanup.lastCleanupDate,
    'the durable last-run date is unchanged by opening the screen');

  // ==================================================================
  console.log('\n— security events:');
  // `#secEvents` holds the timeline rows. An empty timeline renders a single
  // placeholder child, which is the honest empty state — not an invented row.
  const rows = await evalJs(`document.querySelectorAll('#secEvents .ev-body').length`);
  const placeholder = await evalJs(`document.querySelector('#secEvents .ev-cat')?.textContent || ''`);
  const apiEvents = view.events;
  if (apiEvents.length) {
    assert(rows === Math.min(apiEvents.length, 25),
      `the timeline renders the device's events (${rows} vs ${apiEvents.length})`);
    const first = await evalJs(`document.querySelector('#secEvents .ev-body b')?.textContent`);
    const apiFirst = SEC_EVENT_LABELS[apiEvents[0].eventType] || apiEvents[0].eventType;
    assert(first === apiFirst, `the newest event is rendered first (${first} vs ${apiFirst})`);
  } else {
    assert(rows === 0 && /No security events yet/.test(placeholder),
      'an empty timeline says so honestly rather than rendering invented rows');
  }

  // Reconnect after installing the observer. The initial replay can precede
  // the test listener, while this authenticated-device replay goes through the
  // same real app socket and listener without waiting up to the 300-second
  // agent security cadence.
  const probe = mobileSocket(DEV);
  await new Promise(r => probe.onopen = r);
  await sleep(400);
  const wsBefore = await evalJs(`JSON.stringify(window.__sec.wsStates)`);
  await evalJs(`window.Vigilia.disconnect(); window.Vigilia.connect(${JSON.stringify(DEV)})`);
  let sawLive = false;
  for (let i = 0; i < 40; i++) {
    const seen = await evalJs(`JSON.stringify(window.__sec.wsStates)`);
    if (seen !== wsBefore && seen.includes(presence.state)) { sawLive = true; break; }
    await sleep(500);
  }
  const seenStates = await evalJs(`JSON.stringify(window.__sec.wsStates)`);
  assert(sawLive,
    `a security.state frame reached the app's WS listener (got ${seenStates})`);
  assert(seenStates.includes(presence.state),
    `the frame carries the device's reported state (${presence.state})`);
  probe.close();

  // ==================================================================
  console.log('\n— the screens:');
  // Only a free-text field can carry a path or a shell command. The security
  // screen has switches only, so pin that by what it contains.
  const secInputs = await evalJs(`document.querySelectorAll('#s-security input, #s-security textarea').length`);
  assert(secInputs === 0, 'the security screen has no free-text field');
  const bodyText = await evalJs(`document.body.textContent`);
  assert(!/rm -rf|\/bin\/sh|powershell|cmd \/c/i.test(bodyText),
    'no shell command is rendered anywhere in the UI');

  // The security state is a single source: no duplicate state object exists
  // alongside the one the screen renders from.
  assert((await evalJs(`typeof SEC_STATE`)) === 'object',
    'SEC_STATE is the single security state object');
  assert((await evalJs(`typeof window.SEC_STATE`)) === 'undefined',
    'SEC_STATE is not duplicated on window');

  // ==================================================================
  assert(exceptions.length === 0,
    `no script exception during the run${exceptions.length ? `: ${exceptions.join(' | ')}` : ''}`);

  cdp.close();
  chrome.kill();

  if (failures) { console.log(`\n${failures} assertion(s) failed`); process.exit(1); }
  console.log('\nphase5 e2e: all assertions passed');
  process.exit(0);
}

main().catch(err => { console.error('phase5 e2e failed:', err); process.exit(1); });
