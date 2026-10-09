/* ============================================================
   VIGILIA — backend client
   ------------------------------------------------------------
   Bridges the static mobile UI to the real backend:
     - REST:   /api/devices, /api/monitoring/:id/latest|history
     - WS:     /ws?deviceId=<id>   (read-only mobile stream)
   No build step, no dependencies; loaded from index.html.
   ============================================================ */

const Vigilia = (() => {
  const API = window.VIGILIA_API_BASE || '';
  const ORIGIN = window.location.origin;

  const wsUrl = (() => {
    if (window.VIGILIA_WS_URL) return window.VIGILIA_WS_URL;
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}/ws`;
  })();

  let socket = null;
  let pingTimer = null;
  let socketId = null;
  let wantDeviceId = null;
  let listeners = new Set();

  /* ---------------- REST ---------------- */
  async function request(path, options = {}) {
    const res = await fetch(`${API}${path}`, {
      headers: { Accept: 'application/json', ...options.headers },
      method: options.method || 'GET',
      ...(options.body !== undefined ? { body: JSON.stringify(options.body) } : {}),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} on ${path}`);
    return res.json();
  }

  const devices = () => request('/api/devices');
  const device = id => request(`/api/devices/${id}`);
  const latest = id => request(`/api/monitoring/${id}/latest`);
  const history = (id, limit = 30) => request(`/api/monitoring/${id}/history?limit=${limit}`);

  /* ---------------- application control (Phase 3B) ---------------- */
  // The command token is the pairing credential: it is issued once by
  // /api/devices/:id/pair (which needs the agent token, so it is done out of
  // band) and stored on the phone only. The mobile client never holds an
  // agent token.
  const COMMAND_TOKEN_KEY = 'vigilia.commandToken';

  function commandToken() { return localStorage.getItem(COMMAND_TOKEN_KEY) || ''; }
  function setCommandToken(token) {
    if (token) localStorage.setItem(COMMAND_TOKEN_KEY, token);
    else localStorage.removeItem(COMMAND_TOKEN_KEY);
  }

  const applications = id => request(`/api/applications/${id}`);

  // Dispatch one allowlisted command. `body` is always a structured object:
  // { command, target } for application commands, { command } for power ones.
  // There is no string command, no shell, no path.
  async function command(deviceId, body) {
    const token = commandToken();
    if (!token) throw new Error('NOT_PAIRED');
    const res = await fetch(`${API}/api/devices/${deviceId}/commands`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-vigilia-command-token': token },
      body: JSON.stringify(body),
    });
    const text = await res.text();
    const payload = text ? JSON.parse(text) : {};
    if (!res.ok) {
      const code = payload?.error?.code || `HTTP_${res.status}`;
      throw new Error(code, { cause: payload?.error?.message || '' });
    }
    return payload.command;
  }

  const commandHistory = (id, limit = 10) =>
    request(`/api/devices/${id}/commands?limit=${limit}`, { headers: { 'x-vigilia-command-token': commandToken() } });

  async function fileRequest(deviceId, body) {
    const token = commandToken();
    if (!token) throw new Error('NOT_PAIRED');
    const res = await fetch(`${API}/api/files/${deviceId}/requests`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'x-vigilia-command-token': token }, body: JSON.stringify(body),
    });
    const payload = await res.json();
    if (!res.ok) throw new Error(payload?.error?.code || `HTTP_${res.status}`);
    return payload;
  }
  async function fileUploadChunk(deviceId, requestId, sequence, bytes) {
    const res = await fetch(`${API}/api/files/${deviceId}/transfers/${requestId}/chunks/${sequence}`, { method: 'PUT', headers: { 'Content-Type': 'application/octet-stream', 'x-vigilia-command-token': commandToken() }, body: bytes });
    if (!res.ok) throw new Error((await res.json())?.error?.code || 'NOT_ALLOWED'); return res.json();
  }
  async function fileCompleteUpload(deviceId, requestId) { const res = await fetch(`${API}/api/files/${deviceId}/transfers/${requestId}/complete`, { method: 'POST', headers: { 'x-vigilia-command-token': commandToken() } }); if (!res.ok) throw new Error('NOT_ALLOWED'); }
  async function fileCancel(deviceId, requestId) { await fetch(`${API}/api/files/${deviceId}/transfers/${requestId}`, { method: 'DELETE', headers: { 'x-vigilia-command-token': commandToken() } }); }
  async function remoteRequest(deviceId, path, options = {}) { const token = commandToken(); if (!token) throw new Error('NOT_PAIRED'); const res = await fetch(`${API}/api/remote/${deviceId}${path}`, { ...options, headers: { 'Content-Type': 'application/json', 'x-vigilia-command-token': token, ...(options.headers || {}) } }); const payload = await res.json(); if (!res.ok) throw new Error(payload?.error?.code || `HTTP_${res.status}`); return payload; }
  const remoteCapabilities = id => remoteRequest(id, '/capabilities');
  const remoteStart = id => remoteRequest(id, '/sessions', { method: 'POST', body: JSON.stringify({ operation: 'REMOTE_DESKTOP_START' }) });
  const remoteStop = (id, sessionId) => remoteRequest(id, `/sessions/${sessionId}`, { method: 'DELETE' });
  const hardware = id => request(`/api/hardware/${id}`);
  const networkDiagnostics = id => request(`/api/network/${id}/diagnostics`);
  async function hardwareControl(deviceId, body) {
    const token = commandToken(); if (!token) throw new Error('NOT_PAIRED');
    const res = await fetch(`${API}/api/hardware/${deviceId}/controls`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'x-vigilia-command-token': token }, body: JSON.stringify(body) });
    const payload = await res.json(); if (!res.ok && payload?.result?.status !== 'UNSUPPORTED') throw new Error(payload?.error?.code || `HTTP_${res.status}`); return payload;
  }
  async function guardianChat(deviceId, message) {
    const token = commandToken(); if (!token) throw new Error('NOT_PAIRED');
    const res = await fetch(`${API}/api/guardian/${deviceId}/chat`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'x-vigilia-command-token': token }, body: JSON.stringify({ message }) });
    const payload = await res.json(); if (!res.ok) throw new Error(payload?.error?.code || `HTTP_${res.status}`); return payload;
  }
  async function guardianStream(deviceId, message, signal, onToken) { const token = commandToken(); if (!token) throw new Error('NOT_PAIRED'); const res = await fetch(`${API}/api/guardian/${deviceId}/chat/stream`, { method: 'POST', signal, headers: { 'Content-Type': 'application/json', 'x-vigilia-command-token': token }, body: JSON.stringify({ message }) }); if (!res.ok || !res.body) throw new Error('MODEL_UNAVAILABLE'); const reader = res.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; while (true) { const part = await reader.read(); if (part.done) return; buffer += decoder.decode(part.value, { stream: true }); const events = buffer.split('\n\n'); buffer = events.pop() || ''; for (const event of events) if (event.startsWith('data: ')) { const value = JSON.parse(event.slice(6)); if (value.token) onToken(value.token); } } }
  const clearGuardianContext = id => guardianProtected(id, '/context', { method: 'DELETE' });
  async function guardianProtected(deviceId, path, options = {}) { const token = commandToken(); if (!token) throw new Error('NOT_PAIRED'); const headers = { 'x-vigilia-command-token': token, ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) }; const res = await fetch(`${API}/api/guardian/${deviceId}${path}`, { ...options, headers }); const payload = await res.json(); if (!res.ok) throw new Error(payload?.error?.code || `HTTP_${res.status}`); return payload; }
  const guardianAlerts = id => guardianProtected(id, '/alerts');
  const guardianAudit = id => guardianProtected(id, '/audit');
  const guardianPreferences = id => guardianProtected(id, '/preferences');
  const setGuardianPreferences = (id, preferences) => guardianProtected(id, '/preferences', { method: 'PUT', body: JSON.stringify(preferences) });
  const guardianModelStatus = () => request('/api/guardian/model-status');
  async function guardianConfirmation(deviceId, requestId, approved) { const token = commandToken(); if (!token) throw new Error('NOT_PAIRED'); const res = await fetch(`${API}/api/guardian/${deviceId}/confirmations`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'x-vigilia-command-token': token }, body: JSON.stringify({ requestId, approved }) }); const payload = await res.json(); if (!res.ok) throw new Error(payload?.error?.code || `HTTP_${res.status}`); return payload; }

  /* ---------------- storage + cleanup (Phase 4A/4B/4C) ----------------
     Both are read-only and public, like the process and application
     surfaces: the phone holds no token for them, and neither can modify a
     file. Execution of a cleanup goes through `command()` below, which is
     the only path that carries the paired command token — and the only
     path that can reach the audit trail. */

  // Phase 4A: the read-only disk breakdown. Byte totals and item counts
  // only; the payload carries no file name, no path and no content.
  const storage = id => request(`/api/storage/${id}`);

  // Phase 4B/4C: the actions this device offers plus the advisory AI
  // recommendations. `fresh` says whether the agent refreshed the plan
  // recently, so the UI can say "report may be stale" instead of pretending.
  const cleanupPlan = id => request(`/api/cleanup/${id}`);

  // Phase 4B: a dry-run estimate for one structured action id. The query
  // string is the whole request: there is no body, no path field and no
  // target field, so a client cannot name a file to delete.
  const cleanupPreview = (id, actionId) =>
    request(`/api/cleanup/${id}/preview?actionId=${encodeURIComponent(actionId)}`);

  /* ---------------- security & owner protection (Phase 5) ----------------
     Reads are public like the other surfaces. The two writes take the paired
     command token — the same credential a command dispatch uses — because
     they change how the device behaves. No biometric data crosses any of
     these: the state is the *outcome* of an authentication. */

  const security = id => request(`/api/security/${id}`);
  const securityState = id => request(`/api/security/${id}/state`);
  const securityEvents = (id, limit = 25) =>
    request(`/api/security/${id}/events?limit=${limit}`);

  async function putSecurity(deviceId, path, body) {
    const token = commandToken();
    if (!token) throw new Error('NOT_PAIRED');
    const res = await fetch(`${API}/api/security/${deviceId}${path}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'x-vigilia-command-token': token },
      body: JSON.stringify(body),
    });
    const text = await res.text();
    const payload = text ? JSON.parse(text) : {};
    if (!res.ok) {
      const code = payload?.error?.code || `HTTP_${res.status}`;
      throw new Error(code, { cause: payload?.error?.message || '' });
    }
    return payload;
  }

  const setDailyCleanupConfig = (deviceId, config) =>
    putSecurity(deviceId, '/daily-cleanup', { config });

  const setSecuritySettings = (deviceId, settings) =>
    putSecurity(deviceId, '/settings', { settings });

  /* ---------------- WS ---------------- */
  // Browser WebSocket has no ping API of its own; send a protocol-level ping
  // every 25s so NAT/proxies do not idle the socket closed.
  function sendPing() {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    const data = new Uint8Array([0x89, 0x80, 0x00, 0x00, 0x00, 0x00]);
    socket.send(data.buffer);
  }

  function emit(type, deviceId, data) {
    for (const fn of [...listeners]) {
      try { fn({ type, deviceId, data }); } catch (e) { /* a listener error must not break the others */ }
    }
  }

  function connect(deviceId) {
    if (!deviceId) return;
    wantDeviceId = deviceId;

    if (socket && socket.readyState in { 0: 1, 1: 1 } && socketId === deviceId) return;

    socketId = deviceId;
    if (socket) { try { socket.onclose = null; socket.close(); } catch (e) {} }
    socket = new WebSocket(`${wsUrl}?deviceId=${encodeURIComponent(deviceId)}`);
    socket.binaryType = 'arraybuffer';
    clearInterval(pingTimer);

    socket.onopen = () => {
      pingTimer = setInterval(sendPing, 25000);
      emit('socket.open', deviceId);
    };
    socket.onmessage = event => {
      if (event.data instanceof ArrayBuffer) {
        const bytes = new Uint8Array(event.data);
        if (bytes.length < 10 || String.fromCharCode(...bytes.slice(0, 4)) !== 'VIG7' || bytes[4] !== 1) return;
        const idLength = bytes[5]; if (!idLength || bytes.length <= 6 + idLength) return;
        const sessionId = new TextDecoder().decode(bytes.slice(6, 6 + idLength));
        const jpeg = bytes.slice(6 + idLength);
        if (jpeg[0] === 0xff && jpeg[1] === 0xd8) emit('remote.frame', deviceId, { sessionId, jpeg: jpeg.buffer });
        return;
      }
      let frame;
      try { frame = JSON.parse(event.data); } catch (e) { return; }
      if (frame.deviceId && frame.deviceId !== deviceId) return;
      if (frame.type === 'telemetry.latest' && frame.data) emit('telemetry', deviceId, frame.data.telemetry ?? null);
      else if (frame.type === 'applications.latest' && frame.data) emit('applications', deviceId, frame.data.catalog ?? null);
      else if (frame.type === 'device.status' && frame.data) emit('status', deviceId, frame.data);
       else if (frame.type === 'command.result' && frame.data) emit('command.result', deviceId, frame.data);
       else if (frame.type === 'file.result' && frame.data) emit('file.result', deviceId, frame.data);
       else if (frame.type === 'file.transfer.start' && frame.data) emit('file.transfer.start', deviceId, frame.data);
       else if (frame.type === 'file.transfer.progress' && frame.data) emit('file.transfer.progress', deviceId, frame.data);
       else if (frame.type === 'file.transfer.complete' && frame.data) emit('file.transfer.complete', deviceId, frame.data);
       else if (frame.type === 'file.transfer.failed' && frame.data) emit('file.transfer.failed', deviceId, frame.data);
      else       if (frame.type === 'storage.latest' && frame.data) emit('storage', deviceId, frame.data.summary ?? null);
      else if (frame.type === 'cleanup.latest' && frame.data) emit('cleanup', deviceId, frame.data);
      else if (frame.type === 'security.state' && frame.data) emit('security.state', deviceId, frame.data.presence ?? null);
      else if (frame.type === 'security.event' && frame.data) emit('security.event', deviceId, frame.data.event ?? null);
      else if (frame.type === 'error') emit('error', deviceId, frame.data);
      else emit(frame.type ?? 'message', deviceId, frame.data ?? null);
    };
    socket.onerror = () => emit('socket.error', deviceId);
    socket.onclose = () => {
      clearInterval(pingTimer);
      emit('socket.close', deviceId);
      // Reconnect only if the user is still looking at this device.
      if (socketId === deviceId) setTimeout(() => connect(deviceId), 2000);
    };
  }

  function disconnect() {
    socketId = null;
    wantDeviceId = null;
    clearInterval(pingTimer);
    if (socket) {
      socket.onclose = null;
      try { socket.close(); } catch (e) {}
      socket = null;
    }
  }

  function on(fn) { listeners.add(fn); return () => listeners.delete(fn); }

  return {
    devices, device, latest, history,
    applications, command, commandHistory, fileRequest, fileUploadChunk, fileCompleteUpload, fileCancel, remoteCapabilities, remoteStart, remoteStop, hardware, networkDiagnostics, hardwareControl, guardianChat, guardianStream, clearGuardianContext, guardianAlerts, guardianAudit, guardianPreferences, setGuardianPreferences, guardianModelStatus, guardianConfirmation,
    storage, cleanupPlan, cleanupPreview,
    security, securityState, securityEvents,
    setDailyCleanupConfig, setSecuritySettings,
    commandToken, setCommandToken,
    connect, disconnect, on,
    get wsUrl() { return wsUrl; },
    get connectedDeviceId() { return socketId; },
    get isOpen() { return !!socket && socket.readyState === WebSocket.OPEN; },
  };
})();

window.Vigilia = Vigilia;
