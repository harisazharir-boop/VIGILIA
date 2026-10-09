import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import Fastify from 'fastify';
import websocket from '@fastify/websocket';
import { WebSocket } from 'ws';
import { makeScratchDb } from './helpers/scratch-db.js';

const scratch = makeScratchDb();
process.env.DATABASE_URL = scratch.url;
const port = 3117;
const base = `http://127.0.0.1:${port}`;
let app: ReturnType<typeof Fastify>;
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
function open(url: string, protocols?: string[]): Promise<WebSocket> { return new Promise((resolveSocket, reject) => { const socket = new WebSocket(url, protocols); socket.once('open', () => resolveSocket(socket)); socket.once('error', reject); }); }

before(async () => {
  const { wsRoutes } = await import('../src/ws/routes.js');
  const { remoteRoutes } = await import('../src/modules/remote/routes.js');
  const { registerDevice } = await import('../src/modules/devices/register.js');
  const { pairDevice } = await import('../src/modules/devices/pair.js');
  app = Fastify(); await app.register(websocket); await app.register(wsRoutes); await app.register(remoteRoutes, { prefix: '/api' });
  app.post('/register', async (request, reply) => registerDevice(request.headers as Record<string, string | string[] | undefined>, request.body, reply));
  app.post('/pair/:id', async (request, reply) => pairDevice(request.headers as Record<string, string | string[] | undefined>, (request.params as { id: string }).id, request.body, reply));
  await app.listen({ host: '127.0.0.1', port });
});
after(async () => { await app.close(); await scratch.cleanup(); });

describe('phase 7 binary remote relay', { concurrency: false }, () => {
  test('rejects binary relay frames before agent hello', async () => {
    const registration = await fetch(`${base}/register`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'relay-no-hello', os: 'linux', hostname: 'relay-no-hello', protocolVersion: 1 }) });
    const device = await registration.json();
    const agent = await open(`ws://127.0.0.1:${port}/ws`, [`vigilia.${device.deviceId}.${device.token}`]);
    const closed = new Promise<number>((resolve) => agent.once('close', (code) => resolve(code)));

    agent.send(Buffer.from('VIG7\x01\x01x\xff\xd8\xff\xd9'));

    assert.equal(await closed, 4406);
  });

  test('relays bounded JPEG media only from authenticated agent to device browser', async () => {
    const registration = await fetch(`${base}/register`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'relay', os: 'linux', hostname: 'relay', agentVersion: 'test', protocolVersion: 1, systemInfo: {} }) });
    const device = await registration.json();
    const agent = await open(`ws://127.0.0.1:${port}/ws`, [`vigilia.${device.deviceId}.${device.token}`]);
    agent.send(JSON.stringify({ type: 'agent.hello', data: { agentVersion: '1.0.0', protocolVersion: 1 } }));
    await sleep(50);
    const browser = await open(`ws://127.0.0.1:${port}/ws?deviceId=${device.deviceId}`);
    agent.send(JSON.stringify({ type: 'remote.capabilities', data: { screenshot: false, streaming: false, mouse: false, keyboard: false, maxWidth: 1280, maxHeight: 720, maxFps: 5, supportedFormats: ['jpeg'] } }));
    await sleep(100);
    const pair = await fetch(`${base}/pair/${device.deviceId}`, { method: 'POST', headers: { Authorization: `Bearer ${device.token}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ deviceId: device.deviceId }) });
    const commandToken = (await pair.json()).commandToken;
    const startFramePromise = new Promise<Buffer>(resolveFrame => agent.once('message', data => resolveFrame(Buffer.from(data))));
    const started = await fetch(`${base}/api/remote/${device.deviceId}/sessions`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'x-vigilia-command-token': commandToken }, body: JSON.stringify({ operation: 'REMOTE_DESKTOP_START' }) });
    const sessionId = (await started.json()).session.sessionId;
    const startFrame = await startFramePromise;
    assert.equal(JSON.parse(startFrame.toString()).type, 'remote.session.start');
    agent.send(JSON.stringify({ type: 'remote.session.result', data: { sessionId, status: 'ACTIVE', capabilities: { screenshot: true, streaming: true, mouse: false, keyboard: false, maxWidth: 1280, maxHeight: 720, maxFps: 5, supportedFormats: ['jpeg'] } } }));
    await sleep(100);
    const jpeg = Buffer.from([0xff, 0xd8, 0xff, 0xd9]);
    const media = Buffer.concat([Buffer.from('VIG7'), Buffer.from([1, sessionId.length]), Buffer.from(sessionId), jpeg]);
    const received = new Promise<Buffer>(resolveFrame => browser.once('message', data => resolveFrame(Buffer.from(data))));
    agent.send(media);
    assert.deepEqual(await received, media);
    agent.close(); browser.close();
  });
});
