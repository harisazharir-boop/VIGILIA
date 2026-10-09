import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AutomaticGuardianScheduler } from '../src/modules/guardian/automatic.js';

const tick = () => new Promise<void>((resolve) => setImmediate(resolve));

test('automatic Guardian coalesces same-device telemetry without overlap and uses newest pending work', async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let active = 0;
  let maxActive = 0;
  const runs: number[] = [];
  let newest = 1;
  const scheduler = new AutomaticGuardianScheduler(async () => true, async () => {
    active += 1;
    maxActive = Math.max(maxActive, active);
    runs.push(newest);
    if (runs.length === 1) await gate;
    active -= 1;
  });
  scheduler.schedule('device');
  await tick();
  newest = 3;
  scheduler.schedule('device');
  scheduler.schedule('device');
  release();
  await tick();
  await tick();
  assert.equal(maxActive, 1);
  assert.deepEqual(runs, [1, 3]);
  assert.equal(scheduler.size(), 1);
  scheduler.cancel('device');
  assert.equal(scheduler.size(), 0);
});

test('automatic model-unavailable backoff is bounded and retries after cooldown', async () => {
  let now = 0;
  const realNow = Date.now;
  Date.now = () => now;
  let checks = 0;
  const uses: boolean[] = [];
  const scheduler = new AutomaticGuardianScheduler(async () => { checks += 1; return false; }, async (_device, useModel) => { uses.push(useModel); }, 100);
  try {
    scheduler.schedule('device'); await tick(); await tick();
    scheduler.schedule('device'); await tick(); await tick();
    assert.equal(checks, 1);
    assert.deepEqual(uses, [false, false]);
    now = 100;
    scheduler.schedule('device'); await tick(); await tick();
    assert.equal(checks, 2);
  } finally { Date.now = realNow; }
});

test('cancelling a device clears pending automatic work', async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let runs = 0;
  const scheduler = new AutomaticGuardianScheduler(async () => true, async () => { runs += 1; await gate; });
  scheduler.schedule('device');
  await tick();
  scheduler.schedule('device');
  scheduler.cancel('device');
  release();
  await tick(); await tick();
  assert.equal(runs, 1);
  assert.equal(scheduler.size(), 0);
});
