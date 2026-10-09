import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AGENT_VERSION, BACKEND_VERSION, MIN_SUPPORTED_PROTOCOL_VERSION, PROTOCOL_VERSION, registerDeviceSchema } from '@vigilia/shared';
import { protocolCompatibility } from '@vigilia/shared';

test('shared version constants define protocol baseline', () => {
  assert.equal(AGENT_VERSION, '1.0.0'); assert.equal(BACKEND_VERSION, '1.0.0'); assert.equal(PROTOCOL_VERSION, 1); assert.equal(MIN_SUPPORTED_PROTOCOL_VERSION, 1);
});
test('registration schema accepts bounded protocol version', () => {
  assert.equal(registerDeviceSchema.safeParse({ name: 'agent', os: 'linux', hostname: 'host', agentVersion: '1.0.0', protocolVersion: 1 }).success, true);
  assert.equal(registerDeviceSchema.safeParse({ name: 'agent', os: 'linux', hostname: 'host', protocolVersion: 0 }).success, false);
});
test('protocol compatibility rejects missing and unsupported values', () => {
  assert.equal(protocolCompatibility(1).compatible, true);
  assert.equal(protocolCompatibility(0).compatible, false);
  assert.equal(protocolCompatibility(2).compatible, false);
  assert.equal(protocolCompatibility(undefined).compatible, false);
  assert.equal(protocolCompatibility('1').compatible, false);
});
