import { test } from 'node:test';
import assert from 'node:assert/strict';
import { WsHub } from '../src/ws/hub.js';

function socket() {
  const frames: unknown[] = [];
  return { OPEN: 1, readyState: 1, send: (frame: string | Buffer) => frames.push(frame), frames } as unknown as import('ws').WebSocket & { frames: unknown[] };
}

test('WsHub isolates device-indexed broadcasts and removes reconnect indexes', () => {
  const hub = new WsHub();
  const agentA = socket(); const mobileA = socket(); const mobileB = socket();
  hub.register('agent-a', agentA, 'a', false);
  hub.register('mobile-a', mobileA, 'a', true);
  hub.register('mobile-b', mobileB, 'b', true);
  hub.broadcastToDevice('a', 'telemetry.latest', { cpu: 1 });
  assert.equal(agentA.frames.length, 1);
  assert.equal(mobileA.frames.length, 1);
  assert.equal(mobileB.frames.length, 0);
  hub.sendToAgent('a', 'command.dispatch', {});
  assert.equal(agentA.frames.length, 2);
  assert.equal(mobileA.frames.length, 1);
  hub.remove('mobile-a');
  hub.register('mobile-a', mobileA, 'b', true);
  hub.broadcastToDevice('a', 'telemetry.latest', { cpu: 2 });
  assert.equal(mobileA.frames.length, 1);
  hub.broadcastToDevice('b', 'telemetry.latest', { cpu: 3 });
  assert.equal(mobileA.frames.length, 2);
  hub.remove('agent-a'); hub.remove('mobile-a'); hub.remove('mobile-b');
  assert.equal(hub.size(), 0);
});
