/* ============================================================
   VIGILIA — Interactions
   ============================================================ */

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

/* ---------------- NAVIGATION ---------------- */
const screens = $$('.screen');
const bnav = $('#bnav');
const navMap = {
  home: { screen: 's-home', tab: 'home' },
  control: { screen: 's-control', tab: 'control' },
  ai: { screen: 's-ai', tab: 'ai' },
  security: { screen: 's-security', tab: 'security' },
  menu: { screen: 's-menu', tab: 'menu' },
  guest: { screen: 's-guest', tab: null },
  cleaner: { screen: 's-cleaner', tab: null },
  storage: { screen: 's-storage', tab: null },
  files: { screen: 's-files', tab: null },
  remote: { screen: 's-remote', tab: null },
  hardware: { screen: 's-hardware', tab: null },
  network: { screen: 's-network', tab: null },
  controls: { screen: 's-controls', tab: null },
  activity: { screen: 's-activity', tab: null },
  learning: { screen: 's-learning', tab: null },
  settings: { screen: 's-settings', tab: null },
};

let current = 's-splash';
const history = [];

// per-screen hooks; assigned lazily (some defs appear later in file)
const screenHooks = {
  ai: null,
  cleaner: null,
  storage: null,
  activity: null,
  security: null,
  files: loadFiles,
  remote: loadRemote,
  hardware: loadHardware,
  network: loadNetwork,
  controls: loadControls,
};

function phase8Value(value, suffix = '') { return value === undefined || value === null ? '--' : `${value}${suffix}`; }
async function loadHardware() { const id = fileDeviceId(); if (!id) return; const target = $('#hardwareData'); try { const { hardware } = await Vigilia.hardware(id); const h = hardware || {}; target.innerHTML = [['CPU load', phase8Value(h.cpuLoad1)], ['RAM available', phase8Value(h.ramAvailableMb, ' MB')], ['CPU temperature', phase8Value(h.cpuTemperatureC, ' C')], ['Fan', phase8Value(h.fanRpm, ' RPM')], ['Battery', h.batteryStatus || 'Unsupported'], ['GPU', h.capabilities?.gpu ? phase8Value(h.gpuUsage, '%') : 'Unsupported']].map(([k,v]) => `<div class="set-row"><span>${k}</span><b>${v}</b></div>`).join(''); } catch { target.textContent = 'No hardware telemetry'; } }
async function loadNetwork() { const id = fileDeviceId(); if (!id) return; const target = $('#networkData'); try { const { diagnostic } = await Vigilia.networkDiagnostics(id); const n = diagnostic || {}; target.innerHTML = [['Connection', n.connected ? 'Connected' : 'Disconnected'], ['Interface', n.interface || '--'], ['Type', n.interfaceType || '--'], ['Latency', n.capabilities?.latency ? phase8Value(n.latencyMs, ' ms') : 'Unsupported'], ['Packet loss', n.capabilities?.packetLoss ? phase8Value(n.packetLossPercent, '%') : 'Unsupported'], ['Download', phase8Value(n.downloadMbps, ' Mbps')], ['Upload', phase8Value(n.uploadMbps, ' Mbps')]].map(([k,v]) => `<div class="set-row"><span>${k}</span><b>${v}</b></div>`).join(''); } catch { target.textContent = 'No network telemetry'; } }
function loadControls() { const target = $('#hardwareControls'); if (!target) return; target.innerHTML = ['VOLUME', 'BRIGHTNESS', 'WIFI', 'BLUETOOTH', 'MICROPHONE', 'CAMERA', 'MEDIA_PLAYBACK'].map(control => `<button class="set-row" data-hardware-control="${control}"><span>${control.replace('_', ' ')}</span><span>Unsupported</span></button>`).join(''); }
document.addEventListener('click', event => { const button = event.target.closest('[data-hardware-control]'); if (!button) return; const control = button.dataset.hardwareControl; const value = control === 'MEDIA_PLAYBACK' ? 'play' : ['VOLUME', 'BRIGHTNESS'].includes(control) ? 50 : true; Vigilia.hardwareControl(fileDeviceId(), { control, value }).then(result => { button.lastElementChild.textContent = result.result?.status || 'Unsupported'; }).catch(() => { button.lastElementChild.textContent = 'Unsupported'; }); });

function navTo(id, skipHistory) {
  const target = navMap[id];
  if (!target) return;
  if (!skipHistory && current !== target.screen) history.push(current);
  current = target.screen;

  screens.forEach(s => s.classList.remove('active'));
  const el = $('#' + target.screen);
  el.classList.add('active');

  // bottom nav visibility
  const mainTabs = ['s-home', 's-control', 's-ai', 's-security', 's-menu'];
  bnav.style.display = mainTabs.includes(target.screen) ? 'flex' : 'none';
  $$('.nb').forEach(b => b.classList.toggle('active', b.dataset.nav === target.tab));

  // reset scroll position
  const sc = $('.scroll', el) || $('.chat', el);
  if (sc) sc.scrollTop = 0;

  // per-screen hooks. Each is the screen's own load: the Security screen
  // re-reads /api/security/:id so a state pushed while it was closed appears
  // the moment it is opened.
  for (const [key, hook] of Object.entries(screenHooks)) {
    if (key === id && typeof hook === 'function') hook();
  }
}

document.addEventListener('click', e => {
  const navEl = e.target.closest('[data-nav]');
  if (navEl) { navTo(navEl.dataset.nav); return; }

  const aiEl = e.target.closest('[data-ai]');
  if (aiEl) { navTo('ai'); setTimeout(() => injectUserMessage(aiEl.dataset.ai), 420); return; }

  const confEl = e.target.closest('[data-confirm]');
  if (confEl) { openConfirm(confEl.dataset.confirm); return; }

  const sheetEl = e.target.closest('[data-sheet]');
  if (sheetEl) { openSheet(sheetEl.dataset.sheet); return; }

  const toastEl = e.target.closest('[data-toast]');
  if (toastEl) { toast(toastEl.dataset.toast); return; }

  const sw = e.target.closest('[data-switch]');
  if (sw) {
    sw.classList.toggle('on');
    sw.dataset.toast = sw.classList.contains('on')
      ? sw.dataset.toast.replace(/off/i, 'on').replace('disabled', 'enabled')
      : sw.dataset.toast.replace(/\bon\b/i, 'off').replace('enabled', 'disabled');
    return;
  }

  const closeBtn = e.target.closest('[data-close]');
  if (closeBtn) {
    const card = closeBtn.closest('.app');
    dispatchApplication('APPLICATION_CLOSE', card, closeBtn);
    return;
  }

  const openBtn = e.target.closest('[data-open]');
  if (openBtn) {
    const card = openBtn.closest('.app');
    dispatchApplication('APPLICATION_OPEN', card, openBtn);
    return;
  }

  const restartBtn = e.target.closest('[data-restart]');
  if (restartBtn) {
    const card = restartBtn.closest('.app');
    dispatchApplication('APPLICATION_RESTART', card, restartBtn);
    return;
  }
});

/**
 * Send one application command and reflect only what the agent confirms.
 *
 * The button goes into a loading state immediately, but the row's
 * running/stopped pill changes only on the `command.result` frame the agent
 * sends back — the UI never assumes success from the REST 202 alone.
 */
function dispatchApplication(command, card, button) {
  if (!card || button.disabled) return;
  const target = card.dataset.appId;
  if (!target) return;

  const deviceId = window.BACKEND?.state?.deviceId;
  if (!deviceId) { toast('No device connected'); return; }

  const original = button.innerHTML;
  button.disabled = true;
  button.style.opacity = '.6';
  button.innerHTML = `<svg class="ic xs"><use href="#i-restart"/></svg> ${command === 'APPLICATION_CLOSE' ? 'Closing…' : command === 'APPLICATION_RESTART' ? 'Restarting…' : 'Opening…'}`;

  window.Vigilia.command(deviceId, { command, target })
    .then(record => {
      // `sent` (or `queued`) means the backend accepted it; the agent's reply
      // arrives as a `command.result` frame handled below. If no agent is
      // live, the command stays queued and times out — the pill must not flip.
      toast(`${target}: ${record.status === 'queued' ? 'queued — waiting for the agent' : 'sent to the agent'}`);
      window.__pendingAppCommand = { requestId: record.id, card, target, command };
    })
    .catch(err => {
      const reason = mapCommandError(err.message);
      toast(`${target} failed: ${reason}`);
      button.disabled = false;
      button.style.opacity = '1';
      button.innerHTML = original;
    });
}

function mapCommandError(code) {
  switch (code) {
    case 'NOT_PAIRED': return 'this phone is not paired with the laptop';
    case 'NOT_ALLOWED': return 'this application is not controllable on the laptop';
    case 'VALIDATION_FAILED': return 'the request was rejected';
    case 'UNAUTHORIZED': return 'pairing token rejected — pair the phone again';
    default: return code || 'unknown error';
  }
}

const remoteState = { capabilities: null, session: null };
async function loadRemote() { const deviceId = window.BACKEND?.state?.deviceId; if (!deviceId) return; $('#remoteDevice').textContent = window.BACKEND?.state?.device?.name || 'Connected device'; try { const response = await window.Vigilia.remoteCapabilities(deviceId); remoteState.capabilities = response.capabilities; paintRemote(); } catch (error) { $('#remoteStatus').textContent = mapCommandError(error.message); } }
function paintRemote() { const cap = remoteState.capabilities; const active = remoteState.session?.status === 'ACTIVE'; const available = cap?.screenshot || cap?.streaming; const consentable = cap?.maxWidth && cap?.maxHeight && cap?.supportedFormats?.includes('jpeg'); $('#remotePill').textContent = active ? 'Active' : available ? 'Ready' : consentable ? 'Consent required' : 'Unsupported'; $('#remoteStatus').textContent = active ? 'Remote session active and visible on laptop' : available ? 'Ready to request local-consented session' : consentable ? 'Start to request local portal consent' : 'Remote Desktop unsupported by current desktop session'; $('#remoteStart').disabled = !consentable || active; $('#remoteStop').hidden = !remoteState.session; $('#remoteMouse').disabled = !active || !cap?.mouse; $('#remoteKeyboard').disabled = !active || !cap?.keyboard; $('#remoteScreenshot').disabled = !active || !cap?.screenshot; }
$('#remoteStart')?.addEventListener('click', async () => { try { remoteState.session = (await window.Vigilia.remoteStart(fileDeviceId())).session; paintRemote(); } catch (error) { $('#remoteStatus').textContent = mapCommandError(error.message); } });
$('#remoteStop')?.addEventListener('click', async () => { try { await window.Vigilia.remoteStop(fileDeviceId(), remoteState.session.sessionId); remoteState.session = null; paintRemote(); } catch (error) { $('#remoteStatus').textContent = mapCommandError(error.message); } });
function paintRemoteFrame(frame) { if (!remoteState.session || frame.sessionId !== remoteState.session.sessionId) return; const target = $('#remoteScreen'); if (!target) return; const oldUrl = target.dataset.frameUrl; const url = URL.createObjectURL(new Blob([frame.jpeg], { type: 'image/jpeg' })); target.innerHTML = `<img alt="Consented remote screen" src="${url}" style="display:block;width:100%;height:auto">`; target.dataset.frameUrl = url; if (oldUrl) URL.revokeObjectURL(oldUrl); }
$('#remoteScreenshot')?.addEventListener('click', () => { const url = $('#remoteScreen')?.dataset.frameUrl; if (!remoteState.session || !url) return; const link = document.createElement('a'); link.href = url; link.download = `vigilia-${remoteState.session.sessionId}.jpg`; link.click(); });

/* Phase 6: opaque agent handles only. No browser-side filesystem paths exist. */
const fileState = { rootId: null, parentId: null, entries: [], downloads: new Set(), transferId: null, pendingUploads: new Map(), moveEntry: null, pickerRoot: null, pickerParent: null, pickerEntries: [], pickerRequestId: null };
function fileDeviceId() { return window.BACKEND?.state?.deviceId; }
function paintFileRoots() {
  const target = $('#filesRoots'); if (!target) return;
  target.innerHTML = ['HOME', 'DOCUMENTS', 'DOWNLOADS', 'PICTURES', 'VIDEOS', 'DESKTOP', 'PROJECTS']
    .map(root => `<button class="set-row" data-file-root="${root}"><span>📁 ${root[0] + root.slice(1).toLowerCase()}</span><span>›</span></button>`).join('');
}
function paintFiles(entries) {
  fileState.entries = entries || []; const target = $('#filesList'); if (!target) return;
  target.innerHTML = fileState.entries.length ? fileState.entries.map(entry => `<div class="set-row"><button style="background:none;border:0;color:inherit;text-align:left;flex:1" data-file-id="${entry.id}">${entry.type === 'directory' ? '📁' : '📄'} ${escapeHtml(entry.name)}<small style="display:block">${entry.type} · ${entry.size} B · ${new Date(entry.modifiedAt).toLocaleDateString()}</small></button><button class="icon-btn" data-file-actions="${entry.id}" aria-label="Actions">⋮</button></div>`).join('') : '<div class="set-row"><span>Empty folder</span></div>';
}
function fileRequest(body) {
  const deviceId = fileDeviceId(); if (!deviceId) return Promise.reject(new Error('NO_DEVICE'));
  $('#filesStatus').textContent = 'Waiting for laptop…'; return window.Vigilia.fileRequest(deviceId, body);
}
function loadFiles() { paintFileRoots(); $('#filesList').innerHTML = ''; $('#filesStatus').textContent = 'Choose a safe location.'; }
function handleFileResult(result) {
  if (!result) return;
  $('#filesStatus').textContent = result.status === 'SUCCEEDED' ? 'Completed' : (result.reason || 'Operation denied');
  if (result.requestId === fileState.pickerRequestId && result.entries) { fileState.pickerEntries = result.entries; paintMovePicker(); return; }
  if (result.entries) paintFiles(result.entries);
  if (['FILE_RENAME', 'FILE_MOVE', 'FILE_DELETE', 'FILE_UPLOAD'].includes(result.operation) && result.status === 'SUCCEEDED') refreshFileListing();
  if (result.operation === 'FILE_DOWNLOAD' && result.transfer) { fileState.downloads.add(result.requestId); $('#filesStatus').textContent = 'Download preparing…'; }
}
function refreshFileListing() { if (fileState.rootId) fileRequest({ operation: 'FILE_LIST', rootId: fileState.rootId, ...(fileState.parentId ? { entryId: fileState.parentId } : {}) }).catch(err => { $('#filesStatus').textContent = mapCommandError(err.message); }); }
function openFileActions(entry) {
  // Stat is agent-authoritative metadata; action sheet never trusts stale list fields.
  fileRequest({ operation: 'FILE_STAT', entryId: entry.id }).catch(err => { $('#filesStatus').textContent = mapCommandError(err.message); });
  const folder = entry.type === 'directory';
  openSheet('', { html: `<h3>${escapeHtml(entry.name)}</h3><p class="sub">${entry.type} · ${entry.size} B</p><div class="sheet-actions" style="display:grid;gap:8px">${folder ? '<button class="btn btn-glass" data-file-action="open">Open</button>' : '<button class="btn btn-glass" data-file-action="download">Download</button>'}<button class="btn btn-glass" data-file-action="rename">Rename</button><button class="btn btn-glass" data-file-action="move">Move</button><button class="btn btn-danger" data-file-action="delete">Delete</button><button class="btn btn-ghost" data-close-sheet>Cancel</button></div>`, onOpen: () => $$('[data-file-action]', sheetBody).forEach(button => button.onclick = () => fileAction(button.dataset.fileAction, entry)) });
}
function fileAction(action, entry) {
  if (action === 'open') { closeSheet(); openFileFolder(entry); return; }
  if (action === 'download') { closeSheet(); fileRequest({ operation: 'FILE_DOWNLOAD', entryId: entry.id }); return; }
  if (action === 'rename') { openSheet('', { html: `<h3>Rename ${escapeHtml(entry.name)}</h3><input id="fileRenameInput" maxlength="180" value="${escapeHtml(entry.name)}"><div class="sheet-actions"><button class="btn btn-ghost" data-close-sheet>Cancel</button><button class="btn btn-primary" id="fileRenameConfirm">Rename</button></div>`, onOpen: () => $('#fileRenameConfirm').onclick = () => { const name = $('#fileRenameInput').value.trim(); closeSheet(); if (name) fileRequest({ operation: 'FILE_RENAME', entryId: entry.id, name }).catch(err => { $('#filesStatus').textContent = mapCommandError(err.message); }); } }); return; }
  if (action === 'move') { closeSheet(); openMovePicker(entry); return; }
  if (action === 'delete') { openSheet('', { html: `<h3>Delete ${escapeHtml(entry.name)}?</h3><p class="sub">Deletes one file or an empty folder only. Non-empty folders are denied.</p><div class="sheet-actions"><button class="btn btn-ghost" data-close-sheet>Cancel</button><button class="btn btn-danger" id="fileDeleteConfirm">Delete</button></div>`, onOpen: () => $('#fileDeleteConfirm').onclick = () => { closeSheet(); fileRequest({ operation: 'FILE_DELETE', entryId: entry.id }).catch(err => { $('#filesStatus').textContent = mapCommandError(err.message); }); } }); }
}
function openFileFolder(entry) { fileState.parentId = entry.id; $('#filesCrumb').textContent = `${fileState.rootId} / ${entry.name}`; fileRequest({ operation: 'FILE_LIST', rootId: fileState.rootId, entryId: entry.id }).catch(err => { $('#filesStatus').textContent = mapCommandError(err.message); }); }
function openMovePicker(entry) { fileState.moveEntry = entry; fileState.pickerRoot = fileState.rootId; fileState.pickerParent = null; loadMovePicker(); }
function loadMovePicker() { const body = { operation: 'FILE_LIST', rootId: fileState.pickerRoot, ...(fileState.pickerParent ? { entryId: fileState.pickerParent } : {}) }; fileRequest(body).then(result => { fileState.pickerRequestId = result.requestId; }).catch(err => { $('#filesStatus').textContent = mapCommandError(err.message); }); }
function paintMovePicker() { const folders = fileState.pickerEntries.filter(entry => entry.type === 'directory'); openSheet('', { html: `<h3>Move ${escapeHtml(fileState.moveEntry.name)}</h3><p class="sub">Choose a safe destination folder</p><div class="card group">${folders.map(entry => `<button class="set-row" data-picker-folder="${entry.id}"><span>📁 ${escapeHtml(entry.name)}</span><span>›</span></button>`).join('') || '<div class="set-row">No folders here</div>'}</div><div class="sheet-actions"><button class="btn btn-glass" id="pickerChoose">Move here</button><button class="btn btn-ghost" data-close-sheet>Cancel</button></div>`, onOpen: () => { $$('[data-picker-folder]', sheetBody).forEach(button => button.onclick = () => { fileState.pickerParent = button.dataset.pickerFolder; loadMovePicker(); }); $('#pickerChoose').onclick = () => { const destinationId = fileState.pickerParent; if (!destinationId) { $('#filesStatus').textContent = 'Open destination folder first'; return; } closeSheet(); fileRequest({ operation: 'FILE_MOVE', entryId: fileState.moveEntry.id, destinationId }).catch(err => { $('#filesStatus').textContent = mapCommandError(err.message); }); }; } }); }
async function uploadFile(file) {
  if (!file || !fileState.parentId) { $('#filesStatus').textContent = 'Open destination folder first'; return; }
  if (file.size > 10 * 1024 * 1024) { $('#filesStatus').textContent = 'Upload exceeds 10 MiB limit'; return; }
  const start = await fileRequest({ operation: 'FILE_UPLOAD', destinationId: fileState.parentId, name: file.name, size: file.size }); fileState.transferId = start.requestId; fileState.pendingUploads.set(start.requestId, file); $('#filesCancel').hidden = false; $('#filesStatus').textContent = 'Upload preparing…';
}
async function uploadChunks(requestId, file) {
  for (let offset = 0, sequence = 0; offset < file.size; offset += 64 * 1024, sequence += 1) { const progress = await window.Vigilia.fileUploadChunk(fileDeviceId(), requestId, sequence, await file.slice(offset, offset + 64 * 1024).arrayBuffer()); $('#filesStatus').textContent = `Upload ${progress.transferred} / ${progress.total} bytes`; }
  await window.Vigilia.fileCompleteUpload(fileDeviceId(), requestId); fileState.pendingUploads.delete(requestId);
}
async function downloadFileTransfer(requestId) {
  const token = window.Vigilia.commandToken(); const deviceId = fileDeviceId();
  const response = await fetch(`/api/files/${deviceId}/transfers/${requestId}/download`, { headers: { 'x-vigilia-command-token': token } });
  if (!response.ok) throw new Error('NOT_READY');
  const url = URL.createObjectURL(await response.blob()); const link = document.createElement('a'); link.href = url; link.download = 'vigilia-download'; link.click(); URL.revokeObjectURL(url);
}
document.addEventListener('click', event => {
  const actions = event.target.closest('[data-file-actions]');
  if (actions) { const entry = fileState.entries.find(item => item.id === actions.dataset.fileActions); if (entry) openFileActions(entry); return; }
  const root = event.target.closest('[data-file-root]');
  if (root) { fileState.rootId = root.dataset.fileRoot; fileState.parentId = null; $('#filesCrumb').textContent = fileState.rootId; fileRequest({ operation: 'FILE_LIST', rootId: fileState.rootId }).catch(err => { $('#filesStatus').textContent = mapCommandError(err.message); }); return; }
  const row = event.target.closest('[data-file-id]');
  if (row) { const entry = fileState.entries.find(item => item.id === row.dataset.fileId); if (!entry) return; if (entry.type === 'directory') openFileFolder(entry); else openFileActions(entry); return; }
  if (event.target.closest('#filesRefresh') && fileState.rootId) fileRequest({ operation: 'FILE_LIST', rootId: fileState.rootId, ...(fileState.parentId ? { entryId: fileState.parentId } : {}) });
  if (event.target.closest('#filesCrumb') && fileState.rootId) { fileState.parentId = null; $('#filesCrumb').textContent = fileState.rootId; refreshFileListing(); }
  if (event.target.closest('#filesSearchGo') && fileState.rootId) { const query = $('#filesSearch').value.trim(); if (query) fileRequest({ operation: 'FILE_SEARCH', rootId: fileState.rootId, query }); }
  if (event.target.closest('#filesCancel') && fileState.transferId) { window.Vigilia.fileCancel(fileDeviceId(), fileState.transferId); fileState.pendingUploads.delete(fileState.transferId); fileState.transferId = null; $('#filesCancel').hidden = true; $('#filesStatus').textContent = 'Transfer cancelled'; }
});
$('#filesUpload')?.addEventListener('change', event => uploadFile(event.target.files?.[0]).catch(err => { $('#filesStatus').textContent = mapCommandError(err.message); }));

/* ---------------- SPARKLINES ---------------- */
function drawSpark(svg) {
  const raw = svg.dataset.spark.split(',').map(Number);
  const color = svg.dataset.color || '139,92,246';
  const W = 100, H = 28;
  const min = Math.min(...raw), max = Math.max(...raw);
  const span = max - min || 1;
  const pts = raw.map((v, i) => [
    (i / (raw.length - 1)) * W,
    H - 2 - ((v - min) / span) * (H - 6)
  ]);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  const area = `${line} L${W},${H} L0,${H} Z`;

  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  const gid = 'spark' + Math.random().toString(36).slice(2, 8);
  svg.innerHTML = `
    <defs>
      <linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="rgba(${color},.32)"/>
        <stop offset="1" stop-color="rgba(${color},0)"/>
      </linearGradient>
    </defs>
    <path d="${area}" fill="url(#${gid})"/>
    <path d="${line}" fill="none" stroke="rgb(${color})" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
  `;
}
$$('.spark').forEach(drawSpark);

/* ---------------- DONUT ANIMATE ---------------- */
(function () {
  const donut = $('#donut');
  if (!donut) return;
  const segs = $$('.d-seg', donut);
  requestAnimationFrame(() => setTimeout(() => {
    // already set via dasharray in markup; pulse on load
    segs.forEach((c, i) => {
      c.style.transition = 'none';
      const da = c.getAttribute('stroke-dasharray');
      c.setAttribute('stroke-dasharray', '0 314');
      requestAnimationFrame(() => {
        c.style.transition = 'stroke-dasharray 1.2s cubic-bezier(.22,1,.36,1)';
        c.style.transitionDelay = (i * 0.14) + 's';
        c.setAttribute('stroke-dasharray', da);
      });
    });
  }, 250));
})();

/* ---------------- TOASTS ---------------- */
const toastsEl = $('#toasts');
function toast(msg, icon = 'i-check-c') {
  const t = document.createElement('div');
  t.className = 'toast';
  t.innerHTML = `<svg class="ic"><use href="#${icon}"/></svg><span>${msg}</span>`;
  toastsEl.appendChild(t);
  setTimeout(() => {
    t.classList.add('out');
    setTimeout(() => t.remove(), 300);
  }, 2400);
}

/* ---------------- SHEETS ---------------- */
const overlay = $('#overlay');
const sheetBody = $('#sheetBody');
const sheetEl = $('#sheet');

const sheetTemplates = {
  shutdown: {
    html: `
      <div class="danger-icon"><svg class="ic"><use href="#i-power"/></svg></div>
      <h3 style="text-align:center">Shutdown laptop?</h3>
      <p class="sub" style="text-align:center">"Haris's Laptop" will power off immediately. You can wake it again with Wake on LAN.</p>
      <div class="confirm-list">
        <div><svg class="ic c-red"><use href="#i-alert"/></svg> Unsaved work may be lost</div>
        <div><svg class="ic c-blue"><use href="#i-clock"/></svg> AI will go offline on this device</div>
        <div><svg class="ic c-green"><use href="#i-shield-c"/></svg> Security monitoring paused</div>
      </div>
      <div class="sheet-actions">
        <button class="btn btn-ghost" data-close-sheet>Cancel</button>
        <button class="btn btn-danger" id="doShutdown"><svg class="ic xs"><use href="#i-power"/></svg> Shutdown</button>
      </div>`,
    onOpen: () => $('#doShutdown').addEventListener('click', () => {
      closeSheet();
      toast('Shutdown command sent • 5s to cancel');
    }),
  },
  restart: {
    html: `
      <div class="danger-icon"><svg class="ic"><use href="#i-restart"/></svg></div>
      <h3 style="text-align:center">Restart laptop?</h3>
      <p class="sub" style="text-align:center">The system will reboot. VIGILIA agent reconnects automatically in ~30s.</p>
      <div class="confirm-list">
        <div><svg class="ic c-amber"><use href="#i-alert"/></svg> All running apps will close</div>
        <div><svg class="ic c-green"><use href="#i-check-c"/></svg> Dual-boot picker will appear</div>
      </div>
      <div class="sheet-actions">
        <button class="btn btn-ghost" data-close-sheet>Cancel</button>
        <button class="btn btn-danger" id="doRestart"><svg class="ic xs"><use href="#i-restart"/></svg> Restart</button>
      </div>`,
    onOpen: () => $('#doRestart').addEventListener('click', () => {
      closeSheet();
      toast('Restarting • reconnecting…');
    }),
  },
  os: {
    html: `
      <h3>Connected devices</h3>
      <p class="sub">Select which system VIGILIA controls</p>
      <button class="os-opt active"><svg class="ic c-orange"><use href="#i-ubuntu"/></svg> Ubuntu 24.04<small>Active now</small><svg class="ic check"><use href="#i-check-c"/></svg></button>
      <button class="os-opt"><svg class="ic c-blue"><use href="#i-windows"/></svg> Windows 11<small>Standby</small><svg class="ic check"><use href="#i-check-c"/></svg></button>
      <button class="os-opt"><svg class="ic c-soft"><use href="#i-laptop"/></svg> Haris's Laptop<small>Agent v2.4.1</small><svg class="ic check"><use href="#i-check-c"/></svg></button>
      <div class="sheet-actions">
        <button class="btn btn-primary" data-close-sheet>Done</button>
      </div>`,
    onOpen: () => $$('.os-opt', sheetBody).forEach(b => b.addEventListener('click', () => {
      $$('.os-opt', sheetBody).forEach(x => x.classList.remove('active'));
      b.classList.add('active');
    })),
  },
  details: {
    html: `
      <h3>Diagnostic details</h3>
      <p class="sub">VIGILIA AI • deep scan report</p>
      <div class="card">
        <div class="detail-row"><span>RAM usage</span><span style="color:#FCD34D">86%</span></div>
        <div class="detail-row"><span>Firefox</span><span>2.1 GB</span></div>
        <div class="detail-row"><span>Chrome</span><span>1.8 GB</span></div>
        <div class="detail-row"><span>Removable cache</span><span style="color:#C7B8FF">4.2 GB</span></div>
        <div class="detail-row"><span>CPU temp</span><span>52°C</span></div>
        <div class="detail-row"><span>Verdict</span><span style="color:#6EE7B7">Healthy</span></div>
      </div>
      <div class="sheet-actions">
        <button class="btn btn-glass" data-close-sheet>Close</button>
        <button class="btn btn-primary" data-close-sheet data-nav="cleaner">Clean 4.2 GB</button>
      </div>`,
    onOpen: null,
  },
  photo: {
    html: `
      <h3>Security photo</h3>
      <p class="sub">Captured at 08:47 • 3 failed login attempts</p>
      <div class="photo-preview"><svg class="ic"><use href="#i-camera"/></svg></div>
      <p class="sub" style="margin-top:12px">Stored encrypted, on-device only. Never uploaded. Deleted after 30 days.</p>
      <div class="sheet-actions">
        <button class="btn btn-ghost" data-close-sheet>Dismiss</button>
        <button class="btn btn-danger" data-close-sheet>Lock Laptop Now</button>
      </div>`,
    onOpen: null,
  },
  memory: {
    html: `
      <h3>AI Memory</h3>
      <p class="sub">12 workflows • stored locally, encrypted</p>
      <div class="mem-item"><svg class="ic"><use href="#i-code"/></svg><div><b>Coding Mode</b><small>VS Code + Terminal + Spotify</small></div><button class="del" data-toast="Memory deleted"><svg class="ic xs"><use href="#i-trash"/></svg></button></div>
      <div class="mem-item"><svg class="ic"><use href="#i-firefox"/></svg><div><b>Study Mode</b><small>Firefox + Notes</small></div><button class="del" data-toast="Memory deleted"><svg class="ic xs"><use href="#i-trash"/></svg></button></div>
      <div class="mem-item"><svg class="ic"><use href="#i-chrome"/></svg><div><b>Meeting Mode</b><small>Chrome + Discord</small></div><button class="del" data-toast="Memory deleted"><svg class="ic xs"><use href="#i-trash"/></svg></button></div>
      <div class="sheet-actions">
        <button class="btn btn-glass" data-close-sheet>Cancel</button>
        <button class="btn btn-danger-o" data-close-sheet>Forget All</button>
      </div>`,
    onOpen: null,
  },
};

function openSheet(key, inline) {
  const tpl = inline ?? sheetTemplates[key];
  if (!tpl) return;
  sheetBody.innerHTML = tpl.html;
  if (tpl.onOpen) tpl.onOpen();
  overlay.classList.add('show');
}
function closeSheet() { overlay.classList.remove('show'); }

overlay.addEventListener('click', e => {
  if (e.target === overlay || e.target.closest('[data-close-sheet]')) closeSheet();
});

/* ---------------- CONFIRM WRAPPER ---------------- */
function openConfirm(action) { openSheet(action); }

/* ---------------- AI TYPING ---------------- */
screenHooks.ai = loadGuardian;
let guardianStreamController = null;
async function loadGuardian() { const id = fileDeviceId(); if (!id) return; try { const [alertData, auditData, preferences, model] = await Promise.all([Vigilia.guardianAlerts(id), Vigilia.guardianAudit(id), Vigilia.guardianPreferences(id), Vigilia.guardianModelStatus()]); const target = $('#guardianAlerts'); const history = $('#guardianHistory'); const status = $('#guardianModelStatus'); const prefs = $('#guardianPreferences'); if (status) status.textContent = model.status === 'READY' ? `READY · ${model.model}` : 'UNAVAILABLE · deterministic Guardian active'; if (target) target.innerHTML = alertData.alerts.length ? alertData.alerts.map(alert => `<div class="set-row"><b>${escapeHtml(alert.severity)}</b><span>${escapeHtml(alert.message)}</span></div>`).join('') : '<div class="set-row">No guardian alerts</div>'; if (history) history.innerHTML = auditData.audit.slice(0, 10).map(item => `<div class="set-row"><span>${escapeHtml(item.tool)}</span><small>${escapeHtml(item.status)}</small></div>`).join('') || '<div class="set-row">No Guardian actions</div>'; if (prefs) prefs.innerHTML = [['healthAlerts','Health alerts'], ['securityAlerts','Security alerts']].map(([key,label]) => `<button class="set-row" data-guardian-pref="${key}"><span>${label}</span><span>${preferences.preferences[key] ? 'On' : 'Off'}</span></button>`).join(''); $$('[data-guardian-pref]', prefs).forEach(button => button.onclick = () => { const key = button.dataset.guardianPref; const value = { ...preferences.preferences, [key]: !preferences.preferences[key] }; Vigilia.setGuardianPreferences(id, value).then(() => loadGuardian()); }); } catch {} }

/* ---------------- CHAT SENDER ---------------- */
const chatInput = $('#chatInput');
const chat = $('#chat');
const sendBtn = $('#sendBtn');

function injectUserMessage(text) {
  const u = document.createElement('div');
  u.className = 'msg user';
  u.innerHTML = `<div class="bubble user-bubble">${escapeHtml(text)}</div>`;
  chat.insertBefore(u, chat.lastElementChild);

  const a = document.createElement('div');
  a.className = 'msg ai';
  a.innerHTML = `
    <div class="ai-orb xs"><svg class="ic"><use href="#i-spark"/></svg></div>
    <div class="bubble ai-bubble"><span class="thinking">Analyzing your laptop</span><span class="caret"></span></div>`;
  chat.insertBefore(a, chat.lastElementChild);
  chat.scrollTop = chat.scrollHeight;

  const deviceId = fileDeviceId();
  if (!deviceId) return;
  const status = $('#guardianModelStatus')?.textContent || ''; if (status.startsWith('READY')) { guardianStreamController = new AbortController(); const bubble = a.querySelector('.bubble'); bubble.textContent = ''; Vigilia.guardianStream(deviceId, text, guardianStreamController.signal, token => { bubble.textContent += token; chat.scrollTop = chat.scrollHeight; }).then(() => { guardianStreamController = null; loadGuardian(); }).catch(() => { guardianStreamController = null; bubble.textContent = 'Local model unavailable. Deterministic Guardian fallback active.'; }); return; }
  Vigilia.guardianChat(deviceId, text).then(reply => {
    const confirmation = reply.preview ? `<div class="result-actions"><small>Action: ${escapeHtml(reply.preview.action)} · Request: ${escapeHtml(reply.preview.requestId)} · Expires: ${escapeHtml(new Date(reply.preview.expiresAt).toLocaleTimeString())}<br>${escapeHtml(reply.preview.reason)}</small><button class="btn btn-primary btn-sm" data-guardian-confirm="yes">Confirm ${escapeHtml(reply.preview.action)}</button><button class="btn btn-ghost btn-sm" data-guardian-confirm="no">Deny</button></div>` : '';
    a.innerHTML = `<div class="ai-orb xs"><svg class="ic"><use href="#i-spark"/></svg></div><div class="bubble ai-bubble"><b>${escapeHtml(reply.risk)}</b> ${escapeHtml(reply.message)}<small>${reply.tools.map(item => `${item.tool}: ${item.status}`).join(' · ')}</small>${confirmation}</div>`;
    $$('[data-guardian-confirm]', a).forEach(button => { button.onclick = () => Vigilia.guardianConfirmation(deviceId, reply.requestId, button.dataset.guardianConfirm === 'yes').then(result => { button.parentElement.textContent = `Confirmation: ${result.status}. No action was dispatched.`; }).catch(error => { button.parentElement.textContent = mapCommandError(error.message); }); });
    chat.scrollTop = chat.scrollHeight; loadGuardian();
  }).catch(error => { a.querySelector('.bubble').textContent = `Guardian unavailable: ${mapCommandError(error.message)}`; });
}
function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function sendChat() {
  const v = chatInput.value.trim();
  if (!v) return;
  chatInput.value = '';
  injectUserMessage(v);
}
sendBtn.addEventListener('click', sendChat);
chatInput.addEventListener('keydown', e => { if (e.key === 'Enter') sendChat(); });
$('#guardianCancel')?.addEventListener('click', () => guardianStreamController?.abort());
$('#guardianClearContext')?.addEventListener('click', () => { const id = fileDeviceId(); if (id) Vigilia.clearGuardianContext(id).then(() => { $('#guardianHistory').textContent = 'Conversation context cleared.'; }); });

$$('.mic').forEach(m => m.addEventListener('click', () => {
  m.classList.toggle('recording');
  setTimeout(() => m.classList.remove('recording'), 2200);
}));

/* ---------------- APP SEARCH ---------------- */
const appSearch = $('#appSearch');
if (appSearch) {
  appSearch.addEventListener('input', () => renderAppList());
}

/* ---------------- APPLICATION CONTROL (Phase 3B) ---------------- */
const appList = $('#appList');const appCount = $('#appCount');
let appCatalog = [];
const APP_ICONS = {
  code: 'i-code', firefox: 'i-firefox', chromium: 'i-chrome', chrome: 'i-chrome',
  discord: 'i-discord', spotify: 'i-music', 'telegram-desktop': 'i-send',
  'gnome-calculator': 'i-calculator', calculator: 'i-calculator',
  'gnome-text-editor': 'i-file', gedit: 'i-file', vim: 'i-code', nano: 'i-terminal',
  nautilus: 'i-folder', vlc: 'i-play', gimp: 'i-photo', evince: 'i-file',
};
const ICON_CLASS = {
  code: 'code', firefox: 'ff', chromium: 'ch', chrome: 'ch', 'telegram-desktop': 'dc',
  'gnome-calculator': 'sp', calculator: 'sp', 'gnome-text-editor': 'tm', vim: 'code',
};

/** Render the controllable applications the device reported. */
function renderAppList() {
  if (!appList) return;
  if (!appCatalog.length) {
    appList.innerHTML = `<div class="card app"><div class="app-info" style="padding:4px 0">
      <div class="app-name">No controllable applications yet</div>
      <div class="app-meta">Waiting for the laptop to report what it allows…</div>
    </div></div>`;
    if (appCount) appCount.textContent = '—';
    return;
  }
  const q = (appSearch?.value || '').toLowerCase();
  const rows = appCatalog.filter(a => !q || a.label.toLowerCase().includes(q) || a.applicationId.includes(q));
  appList.innerHTML = rows.map(a => {
    const icon = APP_ICONS[a.applicationId] || 'i-app';
    const cls = ICON_CLASS[a.applicationId] || 'sp';
    const action = a.running
      ? `<button class="btn btn-danger-o btn-sm" data-close data-restart-name="${escapeHtml(a.label)}">Close</button>
         <button class="btn btn-glass btn-sm" data-restart>Restart</button>`
      : `<button class="btn btn-primary-o btn-sm" data-open>Open</button>`;
    return `<div class="card app" data-app-id="${escapeHtml(a.applicationId)}">
      <div class="app-ico ${cls}"><svg class="ic"><use href="#${icon}"/></svg></div>
      <div class="app-info">
        <div class="app-name">${escapeHtml(a.label)}
          <span class="pill ${a.running ? 'ok-pill' : 'off-pill'}">${a.running ? 'Running' : 'Stopped'}</span>
        </div>
        <div class="app-meta">${a.running ? 'Reported by the agent' : 'Not running'}</div>
      </div>
      ${action}
    </div>`;
  }).join('');
  if (appCount) appCount.textContent = `${appCatalog.filter(a => a.running).length} active`;
}

/**
 * The last application command the agent itself confirmed.
 *
 * A catalog's `running` flag is derived from the agent's *previous* process
 * snapshot, so a REST reply or a WS push that is still in flight when the
 * agent answers can describe a state that predates the command. That stale
 * view must not be allowed to un-flip a row the agent just settled, so the
 * confirmation is replayed over every catalog until one agrees with it (or
 * the override times out, in case the agent never reports again).
 */
let settledCommand = null;
const SETTLED_COMMAND_TTL_MS = 60_000;

function applySettledCommand() {
  if (!settledCommand) return;
  const app = appCatalog.find(a => a.applicationId === settledCommand.target);
  if (!app || app.running === settledCommand.running) { settledCommand = null; return; }
  if (Date.now() - settledCommand.at > SETTLED_COMMAND_TTL_MS) { settledCommand = null; return; }
  app.running = settledCommand.running;
}

/** Set the catalog from a REST/WS payload, then repaint. */
function setAppCatalog(catalog) {
  appCatalog = Array.isArray(catalog?.applications) ? catalog.applications : [];
  applySettledCommand();
  renderAppList();
}

async function loadApplications(deviceId) {
  try {
    const { applications } = await Vigilia.applications(deviceId);
    setAppCatalog({ applications });
  } catch (err) {
    console.warn('[vigilia] application catalog unavailable:', err.message);
  }
}

/**
 * React to the agent's answer for a dispatched application command.
 *
 * Only the agent's reply decides whether the row flips: a `succeeded` result
 * repaints the list from the catalog (which the agent refreshes on its next
 * push), a `failed` result surfaces the agent's own reason.
 */
function settleAppCommand(data) {
  const pending = window.__pendingAppCommand;
  if (!pending || data?.requestId !== pending.requestId) return;
  window.__pendingAppCommand = null;

  if (data.status === 'succeeded') {
    toast(`${pending.target}: ${data.result || 'done'}`);
    // The agent confirmed the new state, so reflect it now rather than
    // waiting on the catalog's next interval. The catalog push is still the
    // authority and will reconcile this on its own — but it is derived from
    // the agent's *previous* process snapshot, so remember the confirmation
    // and replay it over any catalog that still lags behind it.
    const command = pending.command;
    const running = command === 'APPLICATION_OPEN' || command === 'APPLICATION_RESTART';
    settledCommand = { target: pending.target, running, at: Date.now() };
    for (const app of appCatalog) {
      if (app.applicationId === pending.target) app.running = running;
    }
    renderAppList();
    const deviceId = window.BACKEND?.state?.deviceId;
    if (deviceId) loadApplications(deviceId);
  } else {
    toast(`${pending.target}: ${data.result || 'the agent reported a failure'}`);
  }
  // The audit row changed: refresh the activity feed if the user has it open.
  const deviceId = window.BACKEND?.state?.deviceId;
  if (deviceId && $('#s-activity')?.classList.contains('active')) loadActivity(deviceId);
}

/* ---------------- CLEANER (Phase 4B/4C) ----------------
 *
 * The cleaner screen is driven entirely by the device's own cleanup plan:
 * every row is an action id the *device* reported, with an estimate the
 * *agent* measured. The phone can only pick from that list. There is no
 * free-text input anywhere on the screen, so a path or a shell command
 * cannot be entered — and even if it were, the backend would reject it,
 * because a cleanup request is `{ command: "CLEAN_TEMP" }` and nothing else.
 *
 * Flow: plan → (optional) dry-run preview → explicit confirmation sheet →
 * dispatch over the command-token surface → the agent's structured result →
 * plan + storage refresh. Nothing is deleted before the confirmation tap.
 */
const cleanList = $('#cleanList');
const recList = $('#recList');
const cleanSizeEl = $('#cleanSize');
const cleanerStatus = $('#cleanerStatus');
const planFreshTag = $('#planFreshTag');

// The action ids the user has checked, plus the descriptors that price them.
// Kept here so the confirmation sheet can describe exactly what will happen
// without a second round-trip.
let cleanSelection = new Map();
// Every action the device offers, keyed by action id.
let cleanActions = new Map();
let planIsFresh = false;

const CLEAN_ICON = {
  CLEAN_TEMP: 'i-file',
  CLEAN_BROWSER_CACHE: 'i-firefox',
  CLEAN_TRASH: 'i-trash',
  CLEAN_PACKAGE_CACHE: 'i-settings',
  CLEAN_USER_CACHE: 'i-hdd',
};
const CLEAN_TONE = {
  CLEAN_TEMP: 't',
  CLEAN_BROWSER_CACHE: 'b',
  CLEAN_TRASH: 'r',
  CLEAN_PACKAGE_CACHE: 'p',
  CLEAN_USER_CACHE: 'c',
};

function fmtBytes(n) {
  const v = Math.max(0, Number(n) || 0);
  if (v < 1024) return `${Math.round(v)} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  if (v < 1024 * 1024 * 1024) return `${(v / (1024 * 1024)).toFixed(1)} MB`;
  return `${(v / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function updateCleanBar() {
  let total = 0;
  for (const el of cleanList.querySelectorAll('.clean')) {
    const id = el.dataset.actionId;
    const on = el.querySelector('input')?.checked;
    el.classList.toggle('checked', !!on);
    if (on) total += Number(el.dataset.bytes) || 0;
  }
  cleanSizeEl.textContent = fmtBytes(total);
}

function paintCleanupPlan(plan) {
  const actions = Array.isArray(plan?.actions) ? plan.actions : [];
  const recs = Array.isArray(plan?.recommendations) ? plan.recommendations : [];
  planIsFresh = !!plan?.fresh;
  cleanActions = new Map(actions.map(a => [a.actionId, a]));
  cleanSelection = new Map();

  if (!actions.length) {
    cleanList.innerHTML = `<div class="card" style="text-align:center;padding:1.4rem">
      <b>No actions reported</b>
      <p class="sub" style="margin:.4rem 0 0">The agent pushes its cleanup plan shortly after connecting.</p>
    </div>`;
    cleanerStatus.textContent = 'Waiting for the device to report its actions…';
  } else {
    cleanList.innerHTML = actions.map(a => `
      <label class="card clean" data-action-id="${escapeHtml(a.actionId)}" data-bytes="${Number(a.estimatedBytes) || 0}">
        <span class="chk"><svg class="ic"><use href="#i-check"/></svg></span>
        <span class="cl-ico ${CLEAN_TONE[a.actionId] || 'c'}"><svg class="ic"><use href="#${CLEAN_ICON[a.actionId] || 'i-file'}"/></svg></span>
        <span class="cl-info"><b>${escapeHtml(a.label)}</b>
          <span>${a.estimateReliable === false ? 'lower bound · agent could not read every entry' : escapeHtml(a.description || '')}</span>
        </span>
        <span class="cl-size">${fmtBytes(a.estimatedBytes)}</span>
        <input type="checkbox">
      </label>`).join('');
    cleanerStatus.textContent = planIsFresh
      ? `${actions.length} action(s) available · device report is current`
      : `${actions.length} action(s) available · report may be stale`;
  }

  if (planFreshTag) {
    planFreshTag.innerHTML = planIsFresh
      ? '<svg class="ic xs"><use href="#i-check-c"/></svg> Device-verified'
      : '<svg class="ic xs"><use href="#i-clock"/></svg> Stale report';
  }

  // AI recommendations are advisory only: a row describes *why* the device
  // suggests an action and offers to check the box for it. Confirming still
  // goes through the same confirmation sheet as a manual selection.
  if (!recs.length) {
    recList.innerHTML = `<div class="card" style="text-align:center;padding:1.1rem">
      <p class="sub" style="margin:0">Nothing safe to recommend right now. The device reports a category once it is large enough to matter.</p>
    </div>`;
  } else {
    recList.innerHTML = recs.map(r => `
      <label class="card clean rec" data-action-id="${escapeHtml(r.actionId)}" data-bytes="${Number(r.estimatedBytes) || 0}">
        <span class="cl-ico b"><svg class="ic"><use href="#i-spark"/></svg></span>
        <span class="cl-info"><b>${escapeHtml(cleanActions.get(r.actionId)?.label || r.actionId)}</b>
          <span>${escapeHtml(r.reason)}</span>
        </span>
        <span class="cl-size">${fmtBytes(r.estimatedBytes)}</span>
        <input type="checkbox">
      </label>`).join('');
  }

  for (const el of [...cleanList.querySelectorAll('.clean'), ...recList.querySelectorAll('.clean')]) {
    el.addEventListener('click', e => {
      e.preventDefault();
      const input = el.querySelector('input');
      input.checked = !input.checked;
      if (el.dataset.actionId) {
        if (input.checked) cleanSelection.set(el.dataset.actionId, el.dataset.bytes);
        else cleanSelection.delete(el.dataset.actionId);
      }
      updateCleanBar();
    });
  }
  updateCleanBar();
}

async function loadCleanupPlan(deviceId) {
  try {
    const plan = await Vigilia.cleanupPlan(deviceId);
    paintCleanupPlan(plan);
    return plan;
  } catch (err) {
    console.warn('[vigilia] cleanup plan unavailable:', err.message);
    cleanerStatus.textContent = 'Cleanup plan unavailable — is the backend reachable?';
    return null;
  }
}

// A dry-run preview per action. Read-only by construction: the route answers
// from the stored breakdown and never reaches the agent, because nothing is
// modified. The sheet says so in plain language.
async function previewCleanupAction(deviceId, actionId) {
  const res = await Vigilia.cleanupPreview(deviceId, actionId);
  return res?.preview ?? null;
}

function openCleanupConfirmation(deviceId, actionIds, previews) {
  const rows = actionIds.map(id => {
    const a = cleanActions.get(id);
    const p = previews.get(id);
    const label = a?.label || id;
    const bytes = p?.estimatedBytes ?? a?.estimatedBytes ?? 0;
    const note = p && p.estimateReliable === false
      ? 'the agent could not read every entry, so this is a lower bound'
      : (a?.description || '');
    return `<div><svg class="ic c-blue"><use href="#i-broom"/></svg> ${escapeHtml(label)} · ${fmtBytes(bytes)}</div>
      ${note ? `<div class="sub" style="padding-left:1.6rem">${escapeHtml(note)}</div>` : ''}`;
  }).join('');

  openSheet('cleanup', {
    html: `
      <div class="danger-icon"><svg class="ic"><use href="#i-broom"/></svg></div>
      <h3 style="text-align:center">Clean ${actionIds.length} categor${actionIds.length === 1 ? 'y' : 'ies'}?</h3>
      <p class="sub" style="text-align:center">This deletes the cached contents of each location on the device. It cannot be undone.</p>
      <div class="confirm-list">${rows}</div>
      <div class="sheet-actions">
        <button class="btn btn-ghost" data-close-sheet>Cancel</button>
        <button class="btn btn-danger" id="doCleanup"><svg class="ic xs"><use href="#i-zap"/></svg> Confirm Cleanup</button>
      </div>`,
    onOpen: () => $('#doCleanup').addEventListener('click', async () => {
      closeSheet();
      await dispatchCleanups(deviceId, actionIds);
    }),
  });
}

/**
 * Dispatch the confirmed actions one at a time over the command surface.
 *
 * Each request is `{ command: <actionId> }` — the structured id is the whole
 * body. There is no path, no target and no option field to fill in, and the
 * backend still re-checks the id against the device's own plan before the
 * command is ever queued or audited.
 *
 * The agent's structured answer settles each row; on failure the reason is
 * the agent's own, never invented client-side.
 */
async function dispatchCleanups(deviceId, actionIds) {
  const btn = $('#cleanBtn');
  btn.innerHTML = `<svg class="ic xs"><use href="#i-restart"/></svg> Cleaning…`;
  btn.style.opacity = '.7';

  let done = 0;
  let failed = 0;
  const failures = [];

  for (const actionId of actionIds) {
    try {
      const record = await Vigilia.command(deviceId, { command: actionId });
      done++;
      // The result comes back over the socket as a `command.result` frame;
      // if the socket missed it, the audit history still holds the outcome.
      console.info('[vigilia] cleanup dispatched', actionId, record?.id);
    } catch (err) {
      failed++;
      failures.push(`${actionId}: ${err.message || 'rejected by the backend'}`);
    }
  }

  // The audit trail changed and the device's breakdown changed: refresh both.
  await loadCleanupPlan(deviceId);
  await loadStorage(deviceId);
  if ($('#s-activity')?.classList.contains('active')) loadActivity(deviceId);

  btn.innerHTML = `<svg class="ic xs"><use href="#i-zap"/></svg> Clean Selected`;
  btn.style.opacity = '1';

  if (failed) {
    toast(`${done} dispatched · ${failed} rejected`, 'i-alert');
    console.warn('[vigilia] cleanup failures:', failures.join('\n'));
  } else {
    toast(`${done} cleanup command(s) dispatched • the agent reports the outcome`, 'i-check-c');
  }
}

$('#cleanBtn').addEventListener('click', async () => {
  const deviceId = window.BACKEND?.state?.deviceId;
  if (!deviceId) { toast('No device selected'); return; }
  const selected = [...cleanSelection.keys()];
  if (!selected.length) { toast('Select at least one category'); return; }

  // A preview per selected action, so the confirmation sheet states what
  // would actually happen rather than the last estimate the plan carried.
  // A preview failure does not block the cleanup: the plan's own estimate is
  // the fallback, and the sheet marks it as a lower bound.
  const previews = new Map();
  await Promise.all(selected.map(async id => {
    try { previews.set(id, await previewCleanupAction(deviceId, id)); }
    catch (err) { console.warn('[vigilia] preview failed for', id, err.message); }
  }));

  openCleanupConfirmation(deviceId, selected, previews);
});

$('#cleanerPreviewAll')?.addEventListener('click', async () => {
  const deviceId = window.BACKEND?.state?.deviceId;
  if (!deviceId) { toast('No device selected'); return; }
  const ids = [...cleanActions.keys()];
  if (!ids.length) { toast('No actions to preview'); return; }
  const btn = $('#cleanerPreviewAll');
  btn.innerHTML = `<svg class="ic xs"><use href="#i-restart"/></svg> Previewing…`;
  let shown = 0;
  for (const id of ids) {
    try {
      const p = await previewCleanupAction(deviceId, id);
      if (p) {
        shown++;
        console.info(`[vigilia] preview ${id}: ${fmtBytes(p.estimatedBytes)} (dry run, nothing modified)`);
      }
    } catch (err) {
      console.warn('[vigilia] preview failed for', id, err.message);
    }
  }
  btn.innerHTML = `<svg class="ic xs"><use href="#i-play"/></svg> Preview`;
  toast(`${shown} action(s) previewed • nothing was modified`, 'i-check-c');
});

$('#cleanerRefresh')?.addEventListener('click', async () => {
  const deviceId = window.BACKEND?.state?.deviceId;
  if (!deviceId) return;
  toast('Refreshing the device cleanup plan…');
  await loadCleanupPlan(deviceId);
});

/* ---------------- STORAGE (Phase 4A) ---------------- */
const DONUT_COLORS = ['#8B5CF6', '#3B82F6', '#22D3EE', '#F59E0B', '#10B981', '#EC4899', '#64748B'];
const CATEGORY_ICON = {
  downloads: 'i-download',
  'user-cache': 'i-hdd',
  tmp: 'i-file',
  trash: 'i-trash',
  'package-cache': 'i-settings',
  'browser-cache': 'i-firefox',
  other: 'i-folder',
};

function paintStorage(summary) {
  const categories = Array.isArray(summary?.categories) ? summary.categories : [];
  const total = Number(summary?.totalBytes) || 0;
  const used = Number(summary?.usedBytes) || 0;
  const free = Number(summary?.freeBytes) || 0;
  const pct = Math.min(100, Math.max(0, Number(summary?.usagePercent) || 0));

  const donut = $('#donut');
  if (donut) {
    // Keep the background ring; replace the segments with ones sized from the
    // real breakdown.
    const bg = donut.querySelector('.d-bg');
    donut.innerHTML = '';
    if (bg) donut.appendChild(bg);
    const C = 2 * Math.PI * 50; // 314
    let offset = 0;
    const shown = categories.slice(0, DONUT_COLORS.length);
    for (let i = 0; i < shown.length; i++) {
      const c = shown[i];
      const share = total > 0 ? Number(c.bytes) / total : 0;
      const len = Math.max(0, share * C);
      if (len <= 0) continue;
      const seg = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      seg.setAttribute('cx', '60');
      seg.setAttribute('cy', '60');
      seg.setAttribute('r', '50');
      seg.setAttribute('class', 'd-seg');
      seg.setAttribute('stroke', DONUT_COLORS[i % DONUT_COLORS.length]);
      seg.setAttribute('stroke-dasharray', `${len} ${C - len}`);
      seg.setAttribute('stroke-dashoffset', `${-offset}`);
      donut.appendChild(seg);
      offset += len;
    }
  }

  const mid = $('#donutMid');
  if (mid) {
    mid.innerHTML = `<strong>${fmtBytes(used)}<span>/ ${fmtBytes(total)}</span></strong>
      <span>${Math.round(pct)}% used · ${fmtBytes(free)} free</span>`;
  }

  const legend = $('#donutLegend');
  if (legend) {
    legend.innerHTML = categories.slice(0, DONUT_COLORS.length).map((c, i) =>
      `<span><i style="background:${DONUT_COLORS[i % DONUT_COLORS.length]}"></i>${escapeHtml(c.label)}
        <b>${fmtBytes(c.bytes)}</b></span>`).join('');
  }

  const list = $('#categoryList');
  if (list) {
    if (!categories.length) {
      list.innerHTML = `<div class="card" style="text-align:center;padding:1.4rem">
        <b>No breakdown yet</b>
        <p class="sub" style="margin:.4rem 0 0">The agent reports its storage breakdown shortly after connecting.</p>
      </div>`;
    } else {
      const max = Math.max(...categories.map(c => Number(c.bytes) || 0), 1);
      list.innerHTML = categories.map(c => {
        const pctOf = Math.round((Number(c.bytes) || 0) / max * 100);
        const reliable = c.safeToAnalyze !== false;
        return `<div class="card folder">
          <span class="f-ico p"><svg class="ic"><use href="#${CATEGORY_ICON[c.categoryId] || 'i-folder'}"/></svg></span>
          <span class="f-info"><b>${escapeHtml(c.label)}${reliable ? '' : ' · lower bound'}</b>
            <span class="f-bar"><i style="width:${pctOf}%"></i></span>
            <span class="sub" style="font-size:.72rem">${c.itemCount} item(s) measured${reliable ? '' : ' · some entries unreadable'}</span>
          </span>
          <span class="f-size">${fmtBytes(c.bytes)}</span>
        </div>`;
      }).join('');
    }
  }
}

async function loadStorage(deviceId) {
  try {
    const { summary } = await Vigilia.storage(deviceId);
    paintStorage(summary);
    return summary;
  } catch (err) {
    console.warn('[vigilia] storage breakdown unavailable:', err.message);
    // Paint the empty state rather than leaving the list blank: a device with
    // no report yet is a real condition the UI should state, not an absence.
    paintStorage(null);
    return null;
  }
}

$('#storageRefresh')?.addEventListener('click', async () => {
  const deviceId = window.BACKEND?.state?.deviceId;
  if (!deviceId) return;
  toast('Refreshing the storage breakdown…');
  await loadStorage(deviceId);
});

// The storage screen loads on open, and both screens follow the live pushes so
// a cleanup is reflected without a manual refresh.
screenHooks.cleaner = async () => {
  const deviceId = window.BACKEND?.state?.deviceId;
  if (deviceId) await loadCleanupPlan(deviceId);
};
screenHooks.storage = async () => {
  const deviceId = window.BACKEND?.state?.deviceId;
  if (deviceId) await loadStorage(deviceId);
};

/* ---------------- SECURITY (Phase 5) ----------------
   The security screen renders the device's own owner-presence report and the
   security event timeline. Nothing is invented: an unreachable backend leaves
   the "waiting" state in place rather than substituting a plausible-looking
   mock, and a device that never reported renders UNKNOWN — the protected
   state — never OWNER. */

const SEC_STATE = {
  presence: null,
  events: [],
  daily: null,
};

const SEC_MODE_TONE = {
  OWNER: { chip: 'green-chip', shield: 'pulse-green', label: 'Owner Mode' },
  GUEST: { chip: 'amber-chip', shield: '', label: 'Guest Mode' },
  UNKNOWN: { chip: 'red-chip', shield: '', label: 'Unknown User' },
  LOCKED: { chip: 'red-chip', shield: 'pulse-red', label: 'Locked' },
};

const SEC_EVENT_ICON = {
  ok: '#i-check-c',
  warn: '#i-alert',
  bad: '#i-alert',
};

const SEC_EVENT_LABEL = {
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

function secModeText(state) {
  if (state === 'OWNER') return 'Owner Mode · Protected';
  if (state === 'GUEST') return 'Guest Mode · Monitoring active, owner-only actions blocked';
  if (state === 'LOCKED') return 'Security lock · repeated failed authentication';
  return 'Unknown user · protected state, owner-only actions blocked';
}

function paintSecurityPresence(presence) {
  SEC_STATE.presence = presence || null;
  if (!presence) return;

  const tone = SEC_MODE_TONE[presence.state] || SEC_MODE_TONE.UNKNOWN;
  const heroShield = $('#secShield');
  heroShield.className = `hero-shield ${tone.shield}`.trim();
  $('#secMode').textContent = tone.label.toUpperCase();
  $('#secStateText').textContent = secModeText(presence.state);

  const chip = $('#secModeChip');
  chip.className = `mode-chip ${tone.chip}`;
  chip.textContent = tone.label;

  const isOwner = presence.state === 'OWNER';
  $('#secOwnerTitle').textContent = isOwner ? 'Owner Detected' : 'Owner Not Confirmed';
  $('#secOwnerSub').textContent = presence.lastAuthenticatedAt
    ? `Last authenticated ${new Date(presence.lastAuthenticatedAt * 1000).toLocaleString()}`
    : 'No successful authentication yet';
  const badge = $('#secOwnerState');
  badge.className = `badge ${isOwner ? 'ok-badge' : 'warn-badge'}`;
  badge.innerHTML = isOwner
    ? '<svg class="ic xs"><use href="#i-check-c"/></svg> Verified'
    : '<svg class="ic xs"><use href="#i-alert"/></svg> Unverified';
  $('#secOwnerBadge').style.display = isOwner ? '' : 'none';

  const caps = presence.capabilities || {};
  $('#secFaceEnrolled').textContent = caps.faceRecognition ? 'Enrolled' : 'Not enrolled';
  $('#secFaceEnrolled').className = `val ${caps.faceRecognition ? 'ok-v' : ''}`;
  $('#secLiveness').textContent = caps.liveness ? 'Enabled' : 'Unavailable';
  $('#secLiveness').className = `val ${caps.liveness ? 'ok-v' : ''}`;
  $('#secFaceAuth').textContent = caps.faceRecognition ? 'Active' : 'Unavailable';
  $('#secFaceAuth').className = `val ${caps.faceRecognition ? 'ok-v' : ''}`;
  $('#secFaceBadge').textContent = caps.faceRecognition ? 'Active' : 'Off';
  $('#secFaceBadge').className = `badge ${caps.faceRecognition ? 'ok-badge' : 'warn-badge'}`;
  $('#secFaceNote').textContent = caps.faceRecognition
    ? 'Face data is stored locally on the device and never uploaded.'
    : 'No camera or no enrollment on this device. The owner authenticates with a PIN; the state stays protected.';

  $('#secIntrusionBadge').textContent = caps.intrusionDetection ? 'Protected' : 'Off';
  $('#secIntrusionBadge').className = `badge ${caps.intrusionDetection ? 'ok-badge' : 'warn-badge'}`;
  $('#secGuestBadge').textContent = presence.state === 'GUEST' ? 'Active' : caps.guestMode ? 'Standby' : 'Off';
  $('#secGuestBadge').className = `badge ${presence.state === 'GUEST' ? 'warn-badge' : 'ok-badge'}`;

  $('#secFailedRow').innerHTML = presence.failedAttempts
    ? `<div class="sec-row"><span class="dot a"></span> ${presence.failedAttempts} failed attempt(s) since the last success</div>`
    : '<div class="sec-row"><span class="dot g"></span> No failed attempts</div>';

  // Every switch renders from the device's own report, so a capability the
  // device cannot honour shows up as off here rather than as a lie.
  setSecSwitch('intrusionDetection', !!caps.intrusionDetection);
  setSecSwitch('guestMode', !!caps.guestMode);
  setSecSwitch('dailyCleanup', !!caps.dailyCleanup);

  const last = presence.lastDailyCleanupDate;
  $('#secLastCleanup').textContent = last
    ? new Date(`${last}T00:00:00`).toLocaleDateString()
    : 'Never';
  $('#setLastCleanup')?.querySelector('.val')?.replaceChildren?.();
  const setVal = $('#setLastCleanup')?.querySelector('.val');
  if (setVal) setVal.textContent = last ? new Date(`${last}T00:00:00`).toLocaleDateString() : 'Never';

  paintHomeSecurityCard(presence);
  paintGuestScreen(presence);
  paintSettingsOwnerCard(presence);
  paintMenuSecurityCard(presence);
}

/**
 * The Settings profile card. The owner's identity is a device-side fact, so
 * this renders the reported trust state — never a name or an email the app
 * has no way to know.
 */
function paintSettingsOwnerCard(presence) {
  const title = $('#settingsOwnerTitle');
  if (!title) return;
  const state = presence?.state;
  if (!state) {
    title.textContent = 'Owner';
    $('#settingsOwnerSub').textContent = 'Waiting for the device to report…';
    const b = $('#settingsOwnerBadge');
    b.textContent = 'Waiting';
    b.className = 'badge';
    return;
  }
  const label = (SEC_MODE_TONE[state] || SEC_MODE_TONE.UNKNOWN).label;
  title.textContent = state === 'OWNER' ? 'Owner' : label;
  $('#settingsOwnerSub').textContent = presence.lastAuthenticatedAt
    ? `Last authenticated ${new Date(presence.lastAuthenticatedAt * 1000).toLocaleString()}`
    : 'No successful authentication yet';
  const b = $('#settingsOwnerBadge');
  b.textContent = state === 'OWNER' ? 'Owner' : state === 'LOCKED' ? 'Locked' : 'Not verified';
  b.className = `badge ${state === 'OWNER' ? 'ok-badge' : 'warn-badge'}`;
}

/**
 * The Guest Monitoring screen.
 *
 * Renders from the same owner-presence report the Security screen uses, so
 * the two cannot disagree. A device that reports `OWNER` shows the honest
 * "no guest" here rather than a pretend monitored session; a device that
 * never reported shows the waiting state. The event list is the real
 * `/api/security/:id/events` timeline — never an invented one.
 */
function paintGuestScreen(presence) {
  const state = presence?.state;
  const title = $('#guestBannerTitle');
  if (!title) return;

  if (!state) {
    $('#guestBannerTitle').textContent = 'Waiting…';
    $('#guestBannerText').textContent = 'Waiting for the device to report its security state…';
    $('#guestWho').textContent = 'Unknown User';
    $('#guestWhoSub').textContent = 'Not registered as owner';
    $('#guestChip').textContent = 'Unknown';
    $('#guestSince').textContent = '—';
    $('#guestDur').textContent = '—';
    $('#guestFailed').textContent = '—';
    $('#guestEvents').innerHTML = `<div class="ev" style="justify-content:center">
      <span class="ev-cat" style="text-align:center">No security events yet</span></div>`;
    return;
  }

  if (state === 'GUEST') {
    $('#guestBannerTitle').textContent = 'Guest Mode Active';
    $('#guestBannerText').textContent = 'Unknown user detected at the laptop';
    $('#guestWho').textContent = 'Unknown User';
    $('#guestWhoSub').textContent = 'Not registered as owner';
    $('#guestChip').textContent = 'Guest';
  } else if (state === 'OWNER') {
    $('#guestBannerTitle').textContent = 'No Guest Session';
    $('#guestBannerText').textContent = 'The owner is authenticated at the laptop';
    $('#guestWho').textContent = 'Owner';
    $('#guestWhoSub').textContent = 'Authenticated';
    $('#guestChip').textContent = 'Owner Mode';
  } else if (state === 'LOCKED') {
    $('#guestBannerTitle').textContent = 'Security Lock Active';
    $('#guestBannerText').textContent = 'Repeated failed authentication locked the laptop';
    $('#guestWho').textContent = 'Locked';
    $('#guestWhoSub').textContent = 'Only the owner can clear this';
    $('#guestChip').textContent = 'Locked';
  } else {
    $('#guestBannerTitle').textContent = 'Unknown User';
    $('#guestBannerText').textContent = 'The device stays protected until the owner authenticates';
    $('#guestWho').textContent = 'Unknown User';
    $('#guestWhoSub').textContent = 'Not registered as owner';
    $('#guestChip').textContent = 'Unknown';
  }

  const since = presence.since ? new Date(presence.since * 1000) : null;
  $('#guestSince').textContent = since ? since.toLocaleString() : '—';
  $('#guestDur').textContent = since ? elapsedLabel(since) : '—';
  $('#guestFailed').textContent = String(presence.failedAttempts ?? 0);

  // The topbar badge is the same source as the banner, so the two cannot
  // disagree: a device that never reported shows "Waiting", not the mockup's
  // permanent "Active".
  const gBadge = $('#guestBadge');
  if (gBadge) {
    const on = state === 'GUEST';
    gBadge.textContent = on ? 'Active' : state === 'OWNER' ? 'Owner' : state === 'LOCKED' ? 'Locked' : state ? 'Protected' : 'Waiting';
    gBadge.className = `badge ${on ? 'warn-badge' : state ? 'ok-badge' : ''}`;
  }

  // The real event timeline, newest-first — the same rows the Security
  // screen renders.
  const events = SEC_STATE.events || [];
  $('#guestEvents').replaceChildren(...events.map(guestEventRow));
  if (!$('#guestEvents').children.length) {
    $('#guestEvents').innerHTML = `<div class="ev" style="justify-content:center">
      <span class="ev-cat" style="text-align:center">No security events yet</span></div>`;
  }
}

function guestEventRow(ev) {
  const row = document.createElement('div');
  row.className = 'ev';
  const sev = ev.severity || 'warn';
  const time = new Date(ev.timestamp).toLocaleString([], {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
  const label = SEC_EVENT_LABEL[ev.eventType] || ev.eventType;
  row.innerHTML =
    `<span class="ev-time-l">${time}</span>
     <span class="ev-ico ${sev === 'bad' ? 'r' : sev === 'warn' ? 'a' : 'g'}">
       <svg class="ic"><use href="${SEC_EVENT_ICON[sev]}"/></svg></span>
     <div class="ev-body"><b>${escapeHtml(label)}</b>
       <span class="ev-cat">${escapeHtml(ev.description || '')}</span></div>
     <span class="sev ${sev === 'bad' ? 'bad' : sev === 'warn' ? 'warn' : 'ok'}"></span>`;
  return row;
}

function elapsedLabel(since) {
  const seconds = Math.max(0, Math.floor((Date.now() - since.getTime()) / 1000));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

/**
 * The home screen's security card. Same source as the Security screen — the
 * device's own owner-presence report — so the two can never disagree, and a
 * device that never reported renders the honest waiting state rather than a
 * claimed "Protected".
 */
function paintHomeSecurityCard(presence) {
  const card = $('#homeSecCard');
  if (!card) return;
  const state = presence?.state;
  const caps = presence?.capabilities || {};

  if (!state) {
    $('#homeSecText').textContent = 'Waiting for the device to report its security state…';
    $('#homeSecBadge').textContent = 'Waiting';
    $('#homeSecBadge').className = 'badge';
    card.className = 'card sec-card';
    $('#homeSecRows').innerHTML = '';
    return;
  }

  const tone = SEC_MODE_TONE[state] || SEC_MODE_TONE.UNKNOWN;
  card.className = `card sec-card ${tone.shield}`.trim();
  $('#homeSecText').textContent = secModeText(state);
  const badge = $('#homeSecBadge');
  badge.textContent = state === 'OWNER' ? 'Secure' : state === 'GUEST' ? 'Guest' : state === 'LOCKED' ? 'Locked' : 'Protected';
  badge.className = `badge ${state === 'OWNER' ? 'ok-badge' : 'warn-badge'}`;

  const rows = [];
  rows.push(`<div class="sec-row"><span class="dot ${state === 'OWNER' ? 'g' : 'r'}"></span> ${
    state === 'OWNER' ? 'Owner authenticated' : 'Owner not authenticated'}</div>`);
  rows.push(`<div class="sec-row"><span class="dot ${caps.faceRecognition ? 'g' : 'd'}"></span> Face authentication ${
    caps.faceRecognition ? 'active' : 'unavailable (PIN fallback)'}</div>`);
  rows.push(`<div class="sec-row"><span class="dot ${caps.guestMode ? 'g' : 'd'}"></span> Guest Monitoring: ${
    state === 'GUEST' ? 'ACTIVE' : caps.guestMode ? 'STANDBY' : 'OFF'}</div>`);
  $('#homeSecRows').innerHTML = rows.join('');
}

/**
 * The menu card's security label. It is the same presence the Security screen
 * renders, so a device that never reported shows "Waiting…" rather than the
 * mockup's permanent "Protected" — a claim the device has not made.
 */
function paintMenuSecurityCard(presence) {
  const el = $('#menuSecState');
  if (!el) return;
  const state = presence?.state;
  el.textContent = !state
    ? 'Waiting…'
    : state === 'OWNER' ? 'Protected' : state === 'GUEST' ? 'Guest Mode' : state === 'LOCKED' ? 'Locked' : 'Protected';
}

function setSecSwitch(key, on) {
  for (const el of $$(`[data-sec-switch="${key}"]`)) {
    el.classList.toggle('on', !!on);
    el.setAttribute('aria-checked', String(!!on));
  }
}

function secSwitchState(key) {
  const el = $(`[data-sec-switch="${key}"]`);
  return !!el?.classList.contains('on');
}

function paintSecurityEvents(events) {
  SEC_STATE.events = Array.isArray(events) ? events : [];
  const list = $('#secEvents');
  if (list) {

  if (!SEC_STATE.events.length) {
    list.innerHTML = `<div class="ev" style="justify-content:center">
      <span class="ev-cat" style="text-align:center">No security events yet</span></div>`;
    $('#secIntrusionCard').hidden = true;
    return;
  }

  list.replaceChildren(...SEC_STATE.events.map(ev => {
    const row = document.createElement('div');
    row.className = 'ev';
    const sev = ev.severity || 'warn';
    const time = new Date(ev.timestamp).toLocaleString([], {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
    const label = SEC_EVENT_LABEL[ev.eventType] || ev.eventType;
    const meta = [];
    if (ev.metadata?.failedAttempts) meta.push(`${ev.metadata.failedAttempts} attempts`);
    if (ev.metadata?.bytesFreed) meta.push(`freed ${fmtBytes(ev.metadata.bytesFreed)}`);
    if (ev.metadata?.reason) meta.push(ev.metadata.reason);
    row.innerHTML =
      `<span class="ev-ico ${sev === 'bad' ? 'r' : sev === 'warn' ? 'a' : 'g'}">
         <svg class="ic"><use href="${SEC_EVENT_ICON[sev]}"/></svg></span>
       <div class="ev-body"><b>${escapeHtml(label)}</b>
         <span class="ev-time">${time}${meta.length ? ' · ' + escapeHtml(meta.join(' · ')) : ''}</span>
         <span class="ev-cat">${escapeHtml(ev.description || '')}</span></div>
       <span class="sev ${sev === 'bad' ? 'bad' : sev === 'warn' ? 'warn' : 'ok'}"></span>`;
    return row;
  }));

  // The intrusion card is derived from real events, not shown as a static
  // mockup. A lock or a captured photo within the recent window raises it.
  const recent = SEC_STATE.events.slice(0, 10);
  const intrusion = recent.find(e =>
    e.eventType === 'security_lock_triggered' ||
    e.eventType === 'security_photo_captured' ||
    e.eventType === 'unknown_person_detected');
  const card = $('#secIntrusionCard');
  if (intrusion) {
    card.hidden = false;
    $('#secIntrusionTime').textContent = new Date(intrusion.timestamp).toLocaleString();
    const rows = [];
    const lock = recent.find(e => e.eventType === 'security_lock_triggered');
    const photo = recent.find(e => e.eventType === 'security_photo_captured');
    const guest = recent.find(e => e.eventType === 'guest.mode_activated');
    if (lock) rows.push(`<div class="sec-row"><span class="dot r"></span> Security lock triggered</div>`);
    if (photo) rows.push(`<div class="sec-row"><span class="dot r"></span> Security photo captured</div>`);
    if (guest) rows.push(`<div class="sec-row"><span class="dot a"></span> Guest Mode activated</div>`);
    rows.push(`<div class="sec-row"><span class="dot a"></span> Alert sent to your phone</div>`);
    $('#secIntrusionRows').innerHTML = rows.join('');
  } else {
    card.hidden = true;
  }

  // The guest screen's event list is the same timeline.
  if ($('#guestEvents') && SEC_STATE.presence) paintGuestScreen(SEC_STATE.presence);
  }
}

function paintDailyCleanupConfig(config) {
  if (!config) return;
  SEC_STATE.daily = config;
  setSecSwitch('dailyCleanup', !!config.enabled);
  const labels = {
    CLEAN_TEMP: 'Temporary Files',
    CLEAN_BROWSER_CACHE: 'Browser Cache',
    CLEAN_THUMBNAIL_CACHE: 'Thumbnail Cache',
    CLEAN_PACKAGE_CACHE: 'Package Cache',
    CLEAN_OLD_LOGS: 'Old Logs',
    CLEAN_TRASH: 'Trash',
  };
  const cats = (config.categories || []).map(id =>
    `<div class="sec-row"><span class="dot g"></span> ${escapeHtml(labels[id] || id)}${
      id === 'CLEAN_TRASH' ? ' (opt-in)' : ''}</div>`).join('');
  $('#secDailyCategories').innerHTML = cats ||
    '<div class="sec-row"><span class="dot a"></span> No safe categories selected</div>';
  paintHomeCleanupLine();
}

/**
 * The home and Settings "last daily cleanup" lines. Rendered from the
 * device's own reported date, never a fabricated timestamp.
 */
function paintHomeCleanupLine() {
  const last = SEC_STATE.presence?.lastDailyCleanupDate || SEC_STATE.daily?._lastCleanupDate;
  const text = last
    ? `Last: ${new Date(`${last}T00:00:00`).toLocaleDateString()}`
    : 'Last: Never';
  const home = $('#homeCleanupText');
  if (home) home.textContent = text;
}

async function loadSecurity(deviceId) {
  try {
    const view = await Vigilia.security(deviceId);
    paintSecurityPresence(view.presence);
    paintSecurityEvents(view.events);
    SEC_STATE.daily = { ...view.dailyCleanup?.config, _lastCleanupDate: view.dailyCleanup?.lastCleanupDate ?? null };
    paintDailyCleanupConfig(view.dailyCleanup?.config);
    paintHomeCleanupLine();
    return view;
  } catch (err) {
    console.warn('[vigilia] security state unavailable:', err.message);
    return null;
  }
}

// The security screen loads on open and follows the live pushes: a state
// change or a new event re-renders while the operator is looking at it.
screenHooks.security = async () => {
  const deviceId = window.BACKEND?.state?.deviceId;
  if (deviceId) await loadSecurity(deviceId);
};

/**
 * 5G — security notifications.
 *
 * Not every event surfaces: only the ones an owner would want to be woken up
 * for. `owner.authenticated` and a skipped cleanup are real but harmless, so
 * they land in the timeline and not the toast surface; a lock, an unknown
 * person and a completed or failed cleanup do notify.
 */
const SEC_NOTIFY = {
  'security_lock_triggered': { icon: 'i-alert', tone: 'bad' },
  'security_photo_captured': { icon: 'i-photo', tone: 'warn' },
  'unknown_person_detected': { icon: 'i-users', tone: 'warn' },
  'guest.mode_activated': { icon: 'i-eye', tone: 'warn' },
  'daily_cleanup.completed': { icon: 'i-broom', tone: 'ok' },
  'daily_cleanup.failed': { icon: 'i-alert', tone: 'bad' },
  'owner.locked': { icon: 'i-lock', tone: 'bad' },
};

function notifySecurityEvent(event) {
  if (!event) return;
  const spec = SEC_NOTIFY[event.eventType];
  if (!spec) return;
  const label = SEC_EVENT_LABEL[event.eventType] || event.eventType;
  const freed = event.metadata?.bytesFreed;
  const detail = freed
    ? `Freed: ${fmtBytes(freed)}`
    : (event.description || '');
  toast(`${label}${detail ? ` — ${detail}` : ''}`, spec.icon);
}

// Switches write through the paired command token, then re-render from the
// device's own next report — so the UI never asserts a state the device has
// not confirmed.
$$('[data-sec-switch]').forEach(el => {
  el.addEventListener('click', async () => {
    const deviceId = window.BACKEND?.state?.deviceId;
    if (!deviceId) { toast('No device selected'); return; }
    const key = el.dataset.secSwitch;
    const next = !secSwitchState(key);
    // Optimistic only until the request lands; a failure reverts.
    setSecSwitch(key, next);
    try {
      if (key === 'dailyCleanup') {
        const config = {
          enabled: next,
          trigger: 'AFTER_OWNER_LOGIN',
          categories: SEC_STATE.daily?.categories || SEC_STATE.presence
            ? (SEC_STATE.daily?.categories?.length
              ? SEC_STATE.daily.categories
              : ['CLEAN_TEMP', 'CLEAN_BROWSER_CACHE', 'CLEAN_THUMBNAIL_CACHE', 'CLEAN_PACKAGE_CACHE'])
            : ['CLEAN_TEMP', 'CLEAN_BROWSER_CACHE', 'CLEAN_THUMBNAIL_CACHE', 'CLEAN_PACKAGE_CACHE'],
          trashEnabled: !!SEC_STATE.daily?.trashEnabled,
          logRetentionDays: SEC_STATE.daily?.logRetentionDays || 30,
        };
        const { config: stored } = await Vigilia.setDailyCleanupConfig(deviceId, config);
        paintDailyCleanupConfig(stored);
        toast(next ? 'Daily Auto Cleanup enabled' : 'Daily Auto Cleanup disabled');
      } else {
        const settings = { [key]: next };
        await Vigilia.setSecuritySettings(deviceId, settings);
        toast(`${key} ${next ? 'on' : 'off'}`);
      }
    } catch (err) {
      setSecSwitch(key, !next);
      toast(err.message === 'NOT_PAIRED'
        ? 'Pair the device first (Settings → Command token)'
        : `Could not save: ${err.message}`);
    }
  });
});

/* ---------------- ACTIVITY LOG (audit trail) ---------------- */
// The activity feed is the audit trail of every remote command: the backend
// wrote each row over the command lifecycle, so this list is never invented
// client-side. On failure the list is left untouched rather than filled with
// mock rows that could be mistaken for real activity.
const ACT_ICON = { ok: '#i-check', bad: '#i-alert', warn: '#i-clock' };

function activityCategory(cmd) {
  return /FAILED|EXPIRED/i.test(cmd) ? 'failed' : /PENDING|QUEUED|SENT/i.test(cmd) ? 'pending' : 'success';
}

function activityLabel(cmd) {
  return cmd.replace(/^APPLICATION_/, '').replace(/_/g, ' ');
}

function activityRow(c) {
  const cat = activityCategory(c.status);
  const time = new Date(c.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const detail = c.target ? `Application · ${c.target}` : 'System · power';
  const reason = c.result ? (c.status === 'failed' || c.status === 'expired' ? c.result : '') : '';
  const ev = document.createElement('div');
  ev.className = 'ev';
  ev.dataset.cat = cat;
  ev.innerHTML =
    `<span class="ev-time-l">${time}</span>
     <span class="ev-ico ${cat === 'failed' ? 'r' : cat === 'pending' ? 'a' : 'g'}">
       <svg class="ic"><use href="${ACT_ICON[cat === 'failed' ? 'bad' : cat === 'pending' ? 'warn' : 'ok']}"/></svg></span>
     <div class="ev-body">
       <b>${c.target ? `${c.target} → ${activityLabel(c.command)}` : activityLabel(c.command)}</b>
       <span class="ev-cat">${detail}${reason ? ` · ${reason}` : ''}</span>
     </div>
     <span class="sev ${cat === 'failed' ? 'bad' : cat === 'pending' ? 'warn' : 'ok'}"></span>`;
  return ev;
}

async function loadActivity(deviceId) {
  const list = $('#actList');
  if (!list) return;
  try {
    const { commands } = await Vigilia.commandHistory(deviceId, 25);
    list.replaceChildren(...(commands || []).map(activityRow));
    if (!list.children.length) {
      list.innerHTML = `<div class="ev" data-cat="all" style="justify-content:center">
        <span class="ev-cat" style="text-align:center">${list.dataset.empty || 'No activity yet'}</span></div>`;
    }
    // A command that just settled over the socket may not be in the REST page
    // yet, so re-render on the frame too.
    paintActivityFilter();
  } catch (err) {
    console.warn('[vigilia] activity unavailable:', err.message);
  }
}

function paintActivityFilter() {
  const active = $('#filters .fbtn.active')?.dataset.f || 'all';
  $$('#actList .ev').forEach(ev => {
    ev.classList.toggle('hidden', active !== 'all' && ev.dataset.cat !== active);
  });
}

$$('#filters .fbtn').forEach(b => b.addEventListener('click', () => {
  $$('#filters .fbtn').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  paintActivityFilter();
}));

screenHooks.activity = () => {
  const deviceId = window.BACKEND?.state?.deviceId;
  if (deviceId) loadActivity(deviceId);
};
// The guest session duration ticks from the device's own reported `since`,
// so it is a real elapsed count and not an incrementing mock number. When no
// state has been reported the label stays "—".
setInterval(() => {
  const el = $('#guestDur');
  if (!el || !$('#s-guest').classList.contains('active')) return;
  const since = SEC_STATE.presence?.since;
  el.textContent = since ? elapsedLabel(new Date(since * 1000)) : '—';
}, 1000);

/* ---------------- STATUS BAR CLOCK ---------------- */
function tickClock() {
  const d = new Date();
  $('#sbTime').textContent =
    String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}
tickClock(); setInterval(tickClock, 30000);

/* ---------------- GLOBAL GESTURES ---------------- */
// swipe right → back (edge)
let touchX = 0, touchY = 0, edge = false;
document.addEventListener('touchstart', e => {
  const t = e.touches[0];
  edge = t.clientX < 22;
  touchX = t.clientX; touchY = t.clientY;
}, { passive: true });
document.addEventListener('touchend', e => {
  if (!edge) return;
  const t = e.changedTouches[0];
  if (t.clientX - touchX > 55 && Math.abs(t.clientY - touchY) < 60) {
    if (history.length) navTo(history.pop(), true);
  }
}, { passive: true });

/* splash initial state */
navTo('home', true);
screens.forEach(s => s.classList.remove('active'));
$('#s-splash').classList.add('active');
bnav.style.display = 'none';

/* ============================================================
   VIGILIA — backend integration
   ------------------------------------------------------------
   The screens above were built as a static mockup. This section
   replaces the hardcoded numbers with live data from the backend:
     - device list → /api/devices
     - stat cards  → /api/monitoring/:id/latest + the /ws stream
   Nothing is invented on failure: a card that has no data shows
   "—" and the reason it could not be filled is logged to console.
   ============================================================ */

const BACKEND = (function () {
  const STAT_KEYS = ['cpu', 'ram', 'gpu', 'temp', 'storage', 'battery', 'network', 'uptime'];
  const state = { deviceId: null, telemetry: null, status: null };

  // The eight home stat cards, resolved by their labels (index.html has no
  // ids on them), so the lookup survives a markup reorder.
  const cardEls = {};
  function resolveCards() {
    if (Object.keys(cardEls).length) return;
    const home = $('#s-home');
    if (!home) return;
    const cards = $$('.card.stat', home);
    for (const card of cards) {
      const lbl = $('.stat-lbl', card);
      if (!lbl) continue;
      const raw = (lbl.textContent || '').trim().toLowerCase();
      // The card labels say "Temperature", the telemetry field is "temp".
      const key = raw === 'temperature' ? 'temp' : raw;
      cardEls[key] = {
        value: $('.stat-val', card),
        status: $('.status', card),
        spark: $('.spark', card),
        bar: $('.bar-track i', card),
      };
    }
  }

  function setStatus(el, text, kind) {
    if (!el) return;
    el.textContent = text;
    el.className = 'status' + (kind ? ' ' + kind : '');
  }

  // Map a telemetry sample onto one home stat card.
  function paintStat(key, value, unit, statusKind, statusText, sparkColor) {
    const el = cardEls[key];
    if (!el) return;
    if (value === null || value === undefined || Number.isNaN(value)) {
      el.value.innerHTML = '<small>—</small>';
      setStatus(el.status, 'No data');
      return;
    }
    el.value.innerHTML = `${value}<small>${unit}</small>`;
    if (statusText) setStatus(el.status, statusText, statusKind);
    if (sparkColor && el.spark) el.spark.dataset.color = sparkColor;
  }

  function sparkFrom(series, key) {
    return series.map(s => s?.[key]).filter(v => typeof v === 'number').join(',');
  }

  function uptimeLabel(seconds) {
    if (typeof seconds !== 'number') return null;
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d > 0) return `${d}<small>d</small> ${h}<small>h</small>`;
    if (h > 0) return `${h}<small>h</small> ${m}<small>m</small>`;
    return `${m}<small>m</small>`;
  }

  function networkLabel(net) {
    // The agent reports connectivity + interface, not throughput. Show a
    // human label rather than inventing a Mbps figure.
    if (!net) return { value: null };
    if (!net.connected) return { value: 0, unit: '<small>off</small>' };
    return { value: null, unit: '<small>up</small>', text: net.interface || 'connected' };
  }

  function paint(telemetry) {
    if (!telemetry) return;
    state.telemetry = telemetry;
    resolveCards();

    paintStat('cpu', telemetry.cpu, '%', telemetry.cpu > 80 ? 'warn' : 'ok', telemetry.cpu > 80 ? 'High' : 'Normal');
    paintStat('ram', telemetry.ram, '%', telemetry.ram > 80 ? 'warn' : 'ok', telemetry.ram > 80 ? 'High' : 'Normal');
    const hardware = telemetry.hardware || {};
    paintStat('gpu', hardware.gpuUsage, '%', null, hardware.capabilities?.gpu ? 'Live' : 'Unsupported');
    paintStat('temp', hardware.cpuTemperatureC ?? telemetry.temp, '°C', null, hardware.capabilities?.cpuTemperature ? 'Live' : 'Unsupported');
    paintStat('storage', telemetry.storage, '%', telemetry.storage > 85 ? 'warn' : 'ok', telemetry.storage > 85 ? 'High' : 'Normal');
    paintStat('battery', telemetry.battery, '%', telemetry.battery < 20 ? 'warn' : 'ok', telemetry.battery < 20 ? 'Low' : 'Charged');
    const detailedNetwork = telemetry.networkTelemetry;
    const net = detailedNetwork ? { value: detailedNetwork.downloadMbps, unit: ' Mbps', text: detailedNetwork.interface || (detailedNetwork.connected ? 'connected' : 'offline') } : networkLabel(telemetry.network);
    if (net.text) {
      const el = cardEls['network'];
      if (el) {
        el.value.innerHTML = `${escapeHtml(net.text)}<small> up</small>`;
        setStatus(el.status, 'Connected', 'ok');
      }
    } else {
      paintStat('network', net.value, net.unit, telemetry.network?.connected ? 'ok' : 'warn', telemetry.network?.connected ? 'Connected' : 'Offline');
    }
    paintStat('uptime', null);
    const el = cardEls['uptime'];
    if (el) el.value.innerHTML = uptimeLabel(telemetry.uptime) || '<small>—</small>';

    // Bars (storage/battery) and sparklines redraw from the same sample.
    if (cardEls['storage']?.bar) cardEls['storage'].bar.style.width = `${telemetry.storage ?? 0}%`;
    if (cardEls['battery']?.bar) cardEls['battery'].bar.style.width = `${telemetry.battery ?? 0}%`;

    const os = telemetry.os || state.osLabel || '';
    const pill = $('#osPill');
    if (pill && os) {
      const isWin = /windows/i.test(os);
      pill.innerHTML = `<svg class="ic"><use href="#${isWin ? 'i-windows' : 'i-ubuntu'}"/></svg> ${escapeHtml(os)}
        <svg class="ic xs"><use href="#i-chev"/></svg>`;
    }

    // The Guest screen's usage tiles render from the same sample, so a device
    // that never reported telemetry shows "—" rather than the mockup's
    // numbers.
    const setCpu = $('#guestCpu'), setRam = $('#guestRam'), setNet = $('#guestNet');
    if (setCpu) setCpu.innerHTML = telemetry.cpu == null ? '—' : `${Math.round(telemetry.cpu)}<small>%</small>`;
    if (setRam) setRam.innerHTML = telemetry.ram == null ? '—' : `${Math.round(telemetry.ram)}<small>%</small>`;
    const guestNet = networkLabel(telemetry.network);
    if (setNet) setNet.innerHTML = guestNet.value == null ? '—' : `${guestNet.value}<small>${guestNet.unit || ''}</small>`;
  }

  function paintSparklines(series) {
    resolveCards();
    const map = { cpu: '139,92,246', ram: '59,130,246', temp: '245,158,11', network: '59,130,246', uptime: '16,185,129' };
    for (const key of ['cpu', 'ram', 'temp', 'network']) {
      const el = cardEls[key];
      if (!el?.spark) continue;
      const data = sparkFrom(series, key);
      if (!data) {
        // No real series for this metric — drop the mockup's sparkline so it
        // cannot be mistaken for live history.
        el.spark.removeAttribute('data-spark');
        el.spark.innerHTML = '';
        continue;
      }
      el.spark.dataset.spark = data;
      el.spark.dataset.color = map[key] || el.spark.dataset.color;
      drawSpark(el.spark);
    }

    // The Guest screen's tiles sit outside `cardEls` (they are not the home
    // cards), so their sparklines are handled here: drawn from the same real
    // series, and dropped when there is none so the mockup's waveform cannot
    // be mistaken for history. The network tile is a label, not a series, so
    // its sparkline is the first to go.
    const guestSeries = sparkFrom(series, 'cpu');
    for (const spark of $$('#s-guest .spark')) {
      if (!guestSeries) { spark.removeAttribute('data-spark'); spark.innerHTML = ''; continue; }
      spark.dataset.spark = guestSeries;
      drawSpark(spark);
    }
  }

  function paintIdentity(device) {
    state.deviceId = device.id;
    state.osLabel = device.os;
    const name = $('#deviceName');
    if (name) name.childNodes[0].nodeValue = `${device.name} `;
    const dot = $('#deviceDot');
    if (dot) dot.className = 'online-dot' + (device.status === 'online' ? '' : ' offline');
    const pill = $('#osPill');
    if (pill && device.os) {
      const isWin = /windows/i.test(device.os);
      pill.innerHTML = `<svg class="ic"><use href="#${isWin ? 'i-windows' : 'i-ubuntu'}"/></svg> ${escapeHtml(device.os)}
        <svg class="ic xs"><use href="#i-chev"/></svg>`;
    }
  }

  // ---------------- device resolution ----------------
  // The UI is a single-device app; pick the device to follow from, in order:
  //   ?device=<id> in the URL, the most recently seen device, or the first one.
  async function resolveDevice() {
    const explicit = new URLSearchParams(location.search).get('device');
    let device = null;
    try {
      // The single-device route returns the device flat (no envelope), while
      // the list route wraps its rows in { devices }.
      if (explicit) device = await Vigilia.device(explicit);
      else {
        const { devices } = await Vigilia.devices();
        // The count is kept so the Settings screen can report the real number
        // of connected devices instead of a hardcoded one.
        if (state) state.deviceCount = devices.length;
        if (devices.length) device = devices[0]; // ordered by lastSeen desc
      }
    } catch (err) {
      console.warn('[vigilia] backend unreachable:', err.message);
      return null;
    }
    if (!device) {
      console.warn('[vigilia] no registered device yet; start an agent first');
      return null;
    }
  paintIdentity(device);
  return device;
}

/**
 * The Settings "Laptop" group: the real device count, the OS the agent
 * reported, and the real online status. Renders from `/api/devices`, never
 * from a hardcoded value.
 */
function paintLaptopGroup(device) {
  const count = $('#setDeviceCount');
  if (count) count.textContent = String(window.BACKEND?.state?.deviceCount ?? 1);
  const os = $('#setDeviceOs');
  if (os && device) {
    const parts = [device.os, device.hostname].filter(Boolean);
    os.textContent = parts.length ? parts.join(' · ') : '—';
    os.className = 'val';
  }
  const status = $('#setAgentStatus');
  if (status && device) {
    const online = device.status === 'online';
    status.textContent = online ? 'Online' : `Offline · ${device.status || 'unknown'}`;
    status.className = `val ${online ? 'ok-v' : ''}`;
  }
}

  async function loadHistory(deviceId) {
    try {
      const { samples } = await Vigilia.history(deviceId, 30);
      if (samples.length) paintSparklines(samples);
    } catch (err) {
      console.warn('[vigilia] history unavailable:', err.message);
    }
  }

  async function start() {
    resolveCards();
    const device = await resolveDevice();
    if (!device) return;

    await loadHistory(device.id);
    try {
      const { telemetry } = await Vigilia.latest(device.id);
      if (telemetry) paint(telemetry);
    } catch (err) {
      console.warn('[vigilia] latest telemetry unavailable:', err.message);
    }

    state.deviceId = device.id;
    paintLaptopGroup(device);
    loadApplications(device.id);
    loadCleanupPlan(device.id);
    loadStorage(device.id);
    loadSecurity(device.id);

    Vigilia.on(frame => {
      if (frame.deviceId !== state.deviceId) return;
      if (frame.type === 'telemetry') paint(frame.data);
      else if (frame.type === 'applications') setAppCatalog(frame.data);
      else if (frame.type === 'storage') paintStorage(frame.data);
      else if (frame.type === 'cleanup') paintCleanupPlan(frame.data);
      else if (frame.type === 'security.state') {
        paintSecurityPresence(frame.data);
        // A state change may have completed a daily cleanup, so the events
        // that follow it are worth fetching rather than waiting for the
        // screen to be opened.
        loadSecurity(state.deviceId);
      }
      else if (frame.type === 'security.event') {
        paintSecurityEvents([frame.data, ...(SEC_STATE.events || [])]);
        // 5G: a security notification for the events that matter. Only the
        // severe ones and the cleanup outcomes reach the toast surface, so
        // harmless internal events do not spam.
        notifySecurityEvent(frame.data);
      }
      else if (frame.type === 'status') {
        const dot = $('#deviceDot');
        if (dot) dot.className = 'online-dot' + (frame.data?.status === 'online' ? '' : ' offline');
      } else if (frame.type === 'command.result') settleAppCommand(frame.data);
      else if (frame.type === 'file.result') handleFileResult(frame.data);
      else if (frame.type === 'file.transfer.start' && fileState.pendingUploads.has(frame.data.requestId)) uploadChunks(frame.data.requestId, fileState.pendingUploads.get(frame.data.requestId)).catch(err => { $('#filesStatus').textContent = mapCommandError(err.message); });
      else if (frame.type === 'file.transfer.progress') $('#filesStatus').textContent = `Transfer ${frame.data.transferred} / ${frame.data.total} bytes`;
      else if (frame.type === 'file.transfer.complete' && fileState.downloads.delete(frame.data.requestId)) downloadFileTransfer(frame.data.requestId).then(() => { $('#filesStatus').textContent = 'Download complete'; }).catch(err => { $('#filesStatus').textContent = mapCommandError(err.message); });
      else if (frame.type === 'file.transfer.failed') $('#filesStatus').textContent = frame.data.reason || 'Transfer failed';
      else if (frame.type === 'remote.frame') paintRemoteFrame(frame.data);
    });
    Vigilia.connect(device.id);
  }

  return { start, paint, state };
})();

window.BACKEND = BACKEND;
document.addEventListener('DOMContentLoaded', () => BACKEND.start());

/* ---------------- PAIRING (settings) ---------------- */
// The command token is entered once by hand. It is the credential the
// backend issues with the agent token, so it cannot be fetched from the
// browser: pairing is an out-of-band step on purpose.
const pairInput = $('#pairTokenInput');
const pairStatus = $('#pairStatus');
function paintPairStatus() {
  const stored = Vigilia.commandToken();
  if (pairStatus) pairStatus.textContent = stored ? `Paired (${stored.slice(0, 8)}…)` : 'Not paired';
  if (pairInput) pairInput.value = stored;
}
if (pairInput) {
  paintPairStatus();
  $('#savePairToken')?.addEventListener('click', () => {
    Vigilia.setCommandToken(pairInput.value.trim());
    paintPairStatus();
    toast(Vigilia.commandToken() ? 'Command token saved' : 'Command token cleared');
  });
}
