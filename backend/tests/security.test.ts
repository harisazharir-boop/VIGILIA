/**
 * Phase 5 service tests — owner state, security events, daily cleanup.
 *
 * These run against a scratch SQLite database with the current schema, so the
 * once-per-day rule and the severity derivation are checked against real rows
 * rather than mocks, and the routes are exercised through a real Fastify app
 * with the paired command token — the same credential a command uses.
 */
import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import Fastify from 'fastify';
import { PrismaClient } from '@prisma/client';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, '../..');

const deviceId = 'cmtestphase5device';
const otherDevice = 'cmtestotherdevice';

let prisma: PrismaClient;
let cleanup: () => Promise<void>;
let app: Fastify.FastifyInstance;
let _pairToken: string;

/**
 * Point the service's own config at the scratch database *before* the modules
 * are imported, then push the schema into it.
 */
/**
 * The scratch database has to exist *before* the service is imported: the
 * Prisma singleton resolves `config.databaseUrl` at import time, and once it
 * has read the repo `.env` the datasource URL cannot be turned. So the temp
 * directory and the schema push happen at module top-level here, and the
 * service is imported inside `before()` — not with a top-level `await
 * import`, which the runner resolves before any hook runs.
 */
const SCRATCH_DIR = mkdtempSync(join(tmpdir(), 'vigilia-p5-'));
const SCRATCH_URL = `file:${join(SCRATCH_DIR, 'test.db')}`;
process.env.DATABASE_URL = SCRATCH_URL;

// Push the current schema into the temp file: without it, every write fails
// on a foreign key to a device row that does not exist.
//
// `execFileSync`, not `execSync`: the repo path contains a space, and a shell
// split of `node <path>` turns it into two arguments.
execFileSync(
  process.execPath,
  [
    path.join(REPO_ROOT, 'node_modules/prisma/build/index.js'),
    'db', 'push', '--force-reset', '--skip-generate',
    `--schema=${path.resolve(REPO_ROOT, 'backend/prisma/schema.prisma')}`,
  ],
  {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'ignore', 'pipe'],
    env: { ...process.env, DATABASE_URL: SCRATCH_URL },
    timeout: 120_000,
  },
);

/**
 * The security service under test, plus the routes. Imported here, *after*
 * the env points at the scratch database, through a function the `before`
 * hook awaits — the runner evaluates a top-level `await import()` before it
 * ever runs a hook, which is too late.
 */
let securityService: typeof import('../src/modules/security/service.js');
let securitySettings: typeof import('../src/modules/security/settings.js');
let securityRoutes: import('../src/modules/security/routes.js').SecurityRoutes;
let DEFAULT_DAILY_CLEANUP_CONFIG: typeof import('@vigilia/shared').DEFAULT_DAILY_CLEANUP_CONFIG;

before(async () => {
  prisma = new PrismaClient({ datasources: { db: { url: SCRATCH_URL } } });
  await prisma.$connect();
  cleanup = async () => {
    await prisma.$disconnect();
    rmSync(SCRATCH_DIR, { recursive: true, force: true });
  };

  securityService = await import('../src/modules/security/service.js');
  securitySettings = await import('../src/modules/security/settings.js');
  securityRoutes = (await import('../src/modules/security/routes.js')).securityRoutes;
  DEFAULT_DAILY_CLEANUP_CONFIG = (await import('@vigilia/shared')).DEFAULT_DAILY_CLEANUP_CONFIG;

  app = Fastify({ logger: false });
  await app.register(securityRoutes, { prefix: '/api/security' });

  // A device row plus a paired command token, so the writes can be authorised
  // with the real credential rather than bypassing it.
  await prisma.device.create({
    data: {
      id: deviceId,
      name: 'Phase 5 Test Laptop',
      os: 'ubuntu',
      hostname: 'p5-test',
      commandToken: 'vc_test_command_token_phase5',
    },
  });
  await prisma.device.create({
    data: {
      id: otherDevice,
      name: 'Other Laptop',
      os: 'ubuntu',
      hostname: 'other-test',
      commandToken: 'vc_other_device_token',
    },
  });
  await app.ready();
});

after(async () => {
  if (app) await app.close();
  if (cleanup) await cleanup();
});

async function clearRows() {
  await prisma.securityEvent.deleteMany({});
  await prisma.ownerState.deleteMany({});
  await prisma.dailyCleanupState.deleteMany({});
}

describe('phase 5 — owner presence and security events', { concurrency: false }, () => {
  test('severity is derived from the event type, never accepted from a client', () => {
    assert.equal(securityService.severityFor('owner.authenticated'), 'ok');
    assert.equal(securityService.severityFor('security_lock_triggered'), 'bad');
    assert.equal(securityService.severityFor('owner.authentication_failed'), 'warn');
    assert.equal(securityService.severityFor('daily_cleanup.skipped'), 'warn');
    assert.equal(securityService.severityFor('an.unknown.type'), 'warn');
  });

  test('an unknown owner state defaults to the protected UNKNOWN', () => {
    const presence = securityService.absentOwnerPresence(deviceId);
    assert.equal(presence.state, 'UNKNOWN');
    assert.equal(presence.failedAttempts, 0);
    assert.equal(presence.capabilities.faceRecognition, false);
  });

  test('a security event is recorded with the derived severity', async () => {
    await clearRows();
    const event = await securityService.recordSecurityEvent(deviceId, {
      deviceId,
      eventType: 'security_lock_triggered',
      description: 'Security lock triggered after 3 failed authentication attempts',
      since: 1790000000,
      metadata: { failedAttempts: 3 },
    });
    assert.ok(event);
    assert.equal(event.severity, 'bad');
    assert.equal(event.eventType, 'security_lock_triggered');

    const stored = await securityService.latestSecurityEvents(deviceId, 10);
    assert.equal(stored.length, 1);
    assert.equal(stored[0].id, event!.id);
  });

  test('a hostile metadata key is dropped, not stored', async () => {
    await clearRows();
    const event = await securityService.recordSecurityEvent(deviceId, {
      deviceId,
      eventType: 'owner.authenticated',
      description: 'Owner authenticated via face',
      metadata: {
        // A path-shaped key the schema does not name: dropped, because the
        // backend cannot be tricked into logging a path.
        path: '/etc/passwd',
        token: 'vig_secret',
        failedAttempts: 1,
      } as never,
    });
    assert.ok(event);
    assert.equal(event!.metadata?.path, undefined);
    assert.equal(event!.metadata?.token, undefined);
    assert.equal(event!.metadata?.failedAttempts, 1);
  });

  test('an unknown event type is rejected', async () => {
    await clearRows();
    const event = await securityService.recordSecurityEvent(deviceId, {
      deviceId,
      eventType: 'owner.became_admin' as never,
      description: 'an attempt to invent an event type',
    });
    assert.equal(event, null);
    const stored = await securityService.latestSecurityEvents(deviceId, 10);
    assert.equal(stored.length, 0);
  });

  test('events for one device are invisible to another (cross-device isolation)', async () => {
    await clearRows();
    await securityService.recordSecurityEvent(deviceId, {
      deviceId,
      eventType: 'owner.authenticated',
      description: 'owner on the first device',
    });
    await securityService.recordSecurityEvent(otherDevice, {
      deviceId: otherDevice,
      eventType: 'owner.locked',
      description: 'lock on the second device',
    });

    const mine = await securityService.latestSecurityEvents(deviceId, 10);
    const theirs = await securityService.latestSecurityEvents(otherDevice, 10);
    assert.equal(mine.length, 1);
    assert.equal(mine[0].deviceId, deviceId);
    assert.equal(theirs.length, 1);
    assert.equal(theirs[0].deviceId, otherDevice);
    assert.equal(theirs[0].eventType, 'owner.locked');
  });

  test('a completed daily cleanup advances the durable once-per-day state', async () => {
    await clearRows();
    await securityService.recordDailyCleanupCompletion(deviceId, '2026-10-05', 1024 * 1024 * 1024, true, 'ok');
    const view = await securityService.getDailyCleanupConfig(deviceId);
    assert.equal(view.lastCleanupDate, '2026-10-05');
    assert.ok(view.lastCleanupAt! > 0);
  });

  test('old events are pruned past the retention window', async () => {
    await clearRows();
    await securityService.recordSecurityEvent(deviceId, {
      deviceId,
      eventType: 'owner.authenticated',
      description: 'recent',
    });
    const old = new Date(Date.now() - 400 * 24 * 60 * 60 * 1000);
    await prisma.securityEvent.create({
      data: {
        deviceId,
        eventType: 'daily_cleanup.skipped',
        severity: 'warn',
        description: 'very old',
        createdAt: old,
      },
    });
    const removed = await securityService.pruneSecurityEvents();
    assert.equal(removed, 1);
    const left = await securityService.latestSecurityEvents(deviceId, 10);
    assert.equal(left.length, 1);
    assert.equal(left[0].description, 'recent');
  });
});

describe('phase 5 — daily cleanup configuration', () => {
  test('the default is conservative: disabled, no trash', async () => {
    await clearRows();
    const { config } = await securityService.getDailyCleanupConfig(deviceId);
    assert.equal(config.enabled, false);
    assert.equal(config.trashEnabled, false);
    assert.equal(config.trigger, 'AFTER_OWNER_LOGIN');
    assert.deepEqual(config.categories, DEFAULT_DAILY_CLEANUP_CONFIG.categories);
  });

  test('a stored config filters out actions that are not daily-safe', async () => {
    await clearRows();
    const stored = await securityService.setDailyCleanupConfig(deviceId, {
      enabled: true,
      trigger: 'AFTER_OWNER_LOGIN',
      // CLEAN_USER_CACHE is a manual-only action; it must be dropped.
      categories: ['CLEAN_TEMP', 'CLEAN_USER_CACHE', 'CLEAN_OLD_LOGS', 'NOT_AN_ACTION'],
      trashEnabled: false,
      logRetentionDays: 14,
    });
    assert.ok(!stored.categories.includes('CLEAN_USER_CACHE'));
    assert.ok(!stored.categories.includes('NOT_AN_ACTION'));
    assert.ok(stored.categories.includes('CLEAN_TEMP'));
    assert.ok(stored.categories.includes('CLEAN_OLD_LOGS'));
  });

  test('trash is armed only through trashEnabled', async () => {
    await clearRows();
    const stored = await securityService.setDailyCleanupConfig(deviceId, {
      enabled: true,
      trigger: 'AFTER_OWNER_LOGIN',
      categories: ['CLEAN_TEMP'],
      trashEnabled: true,
      logRetentionDays: 30,
    });
    assert.ok(stored.categories.includes('CLEAN_TRASH'));
  });

  test('log retention is bounded', () => {
    assert.equal(
      securityService.normalizeDailyCleanupConfig({ logRetentionDays: 99999 }).logRetentionDays,
      365,
    );
    assert.equal(
      securityService.normalizeDailyCleanupConfig({ logRetentionDays: 0 }).logRetentionDays,
      1,
    );
    assert.equal(
      securityService.normalizeDailyCleanupConfig({ logRetentionDays: 'abc' }).logRetentionDays,
      30,
    );
  });
});

describe('phase 5 — daily cleanup eligibility (the once-per-day rule)', () => {
  const owner = () => ({ ...securityService.absentOwnerPresence(deviceId), state: 'OWNER' as const });

  test('not the owner -> NOT_AUTHENTICATED, no cleanup ever', () => {
    const out = securityService.dailyCleanupEligibility({
      presence: securityService.absentOwnerPresence(deviceId),
      config: { ...DEFAULT_DAILY_CLEANUP_CONFIG, enabled: true },
      lastCleanupDate: null,
      today: '2026-10-05',
    });
    assert.equal(out.eligible, false);
    assert.equal(out.reason, 'NOT_AUTHENTICATED');
  });

  test('owner but disabled -> DISABLED', () => {
    const out = securityService.dailyCleanupEligibility({
      presence: owner(),
      config: { ...DEFAULT_DAILY_CLEANUP_CONFIG, enabled: false },
      lastCleanupDate: null,
      today: '2026-10-05',
    });
    assert.equal(out.eligible, false);
    assert.equal(out.reason, 'DISABLED');
  });

  test('already done today -> ALREADY_DONE', () => {
    const out = securityService.dailyCleanupEligibility({
      presence: owner(),
      config: { ...DEFAULT_DAILY_CLEANUP_CONFIG, enabled: true },
      lastCleanupDate: '2026-10-05',
      today: '2026-10-05',
    });
    assert.equal(out.eligible, false);
    assert.equal(out.reason, 'ALREADY_DONE');
  });

  test('a new day is eligible again', () => {
    const out = securityService.dailyCleanupEligibility({
      presence: owner(),
      config: { ...DEFAULT_DAILY_CLEANUP_CONFIG, enabled: true },
      lastCleanupDate: '2026-10-05',
      today: '2026-10-06',
    });
    assert.equal(out.eligible, true);
    assert.equal(out.reason, undefined);
  });

  test('no safe categories -> NO_CATEGORIES', () => {
    const out = securityService.dailyCleanupEligibility({
      presence: owner(),
      config: { ...DEFAULT_DAILY_CLEANUP_CONFIG, enabled: true, categories: [], trashEnabled: false },
      lastCleanupDate: null,
      today: '2026-10-05',
    });
    assert.equal(out.eligible, false);
    assert.equal(out.reason, 'NO_CATEGORIES');
  });

  test('GUEST, UNKNOWN and LOCKED never grant a cleanup', () => {
    for (const state of ['GUEST', 'LOCKED', 'UNKNOWN'] as const) {
      const out = securityService.dailyCleanupEligibility({
        presence: { ...securityService.absentOwnerPresence(deviceId), state },
        config: { ...DEFAULT_DAILY_CLEANUP_CONFIG, enabled: true },
        lastCleanupDate: null,
        today: '2026-10-05',
      });
      assert.equal(out.eligible, false, `${state} must not be eligible`);
      assert.equal(out.reason, 'NOT_AUTHENTICATED');
    }
  });
});

describe('phase 5 — security routes and authorization', { concurrency: false }, () => {
  test('GET /api/security/:id is public and answers the protected default', async () => {
    await clearRows();
    const res = await app.inject({
      method: 'GET',
      url: `/api/security/${deviceId}`,
    });
    assert.equal(res.statusCode, 200);
    const body = res.json();
    assert.equal(body.presence.state, 'UNKNOWN');
    assert.equal(Array.isArray(body.events), true);
    assert.equal(body.dailyCleanup.config.enabled, false);
  });

  test('PUT daily-cleanup without a command token is 401', async () => {
    const res = await app.inject({
      method: 'PUT',
      url: `/api/security/${deviceId}/daily-cleanup`,
      payload: { config: { ...DEFAULT_DAILY_CLEANUP_CONFIG, enabled: true } },
    });
    assert.equal(res.statusCode, 401);
  });

  test('PUT daily-cleanup with another device token is 401', async () => {
    const res = await app.inject({
      method: 'PUT',
      url: `/api/security/${deviceId}/daily-cleanup`,
      headers: { 'x-vigilia-command-token': 'vc_other_device_token' },
      payload: { config: { ...DEFAULT_DAILY_CLEANUP_CONFIG, enabled: true } },
    });
    assert.equal(res.statusCode, 401);
  });

  test('PUT daily-cleanup with the paired token stores the config', async () => {
    await clearRows();
    const res = await app.inject({
      method: 'PUT',
      url: `/api/security/${deviceId}/daily-cleanup`,
      headers: { 'x-vigilia-command-token': 'vc_test_command_token_phase5' },
      payload: {
        config: {
          enabled: true,
          trigger: 'AFTER_OWNER_LOGIN',
          categories: ['CLEAN_TEMP', 'CLEAN_BROWSER_CACHE'],
          trashEnabled: false,
          logRetentionDays: 21,
        },
      },
    });
    assert.equal(res.statusCode, 200, res.text);
    assert.equal(res.json().config.enabled, true);
    const stored = await securityService.getDailyCleanupConfig(deviceId);
    assert.equal(stored.config.logRetentionDays, 21);
  });

  test('PUT daily-cleanup with an unknown field is 400 (strict schema)', async () => {
    const res = await app.inject({
      method: 'PUT',
      url: `/api/security/${deviceId}/daily-cleanup`,
      headers: { 'x-vigilia-command-token': 'vc_test_command_token_phase5' },
      payload: {
        config: {
          enabled: true,
          trigger: 'AFTER_OWNER_LOGIN',
          categories: ['CLEAN_TEMP'],
          trashEnabled: false,
          logRetentionDays: 30,
          // A path field: the schema is strict, so this is rejected rather
          // than silently dropped.
          path: '/home/owner',
        },
      },
    });
    assert.equal(res.statusCode, 400);
  });

  test('PUT settings with the paired token toggles intrusion detection', async () => {
    await clearRows();
    const res = await app.inject({
      method: 'PUT',
      url: `/api/security/${deviceId}/settings`,
      headers: { 'x-vigilia-command-token': 'vc_test_command_token_phase5' },
      payload: { settings: { intrusionDetection: false, guestMode: true } },
    });
    assert.equal(res.statusCode, 200, res.text);
    assert.equal(res.json().settings.intrusionDetection, false);
    assert.equal(res.json().settings.guestMode, true);

    const back = await securitySettings.readSecuritySettings(deviceId);
    assert.equal(back.intrusionDetection, false);
    assert.equal(back.guestMode, true);
  });

  test('PUT settings with no keys is 400', async () => {
    const res = await app.inject({
      method: 'PUT',
      url: `/api/security/${deviceId}/settings`,
      headers: { 'x-vigilia-command-token': 'vc_test_command_token_phase5' },
      payload: { settings: {} },
    });
    assert.equal(res.statusCode, 400);
  });

  test('the security view never contains a token, a path or biometric data', async () => {
    await clearRows();
    await securityService.storeOwnerPresence(deviceId, {
      deviceId,
      state: 'OWNER',
      since: 1790000000,
      lastAuthenticatedAt: 1790000001,
      failedAttempts: 0,
      capabilities: {
        faceRecognition: true,
        liveness: true,
        guestMode: true,
        intrusionDetection: true,
        dailyCleanup: true,
      },
      timestamp: new Date().toISOString(),
    });
    const res = await app.inject({ method: 'GET', url: `/api/security/${deviceId}` });
    // `res.text` is undefined on `inject()` in this light-my-request build —
    // the payload arrives as `res.body`, which is the string for a JSON reply.
    const text = typeof res.body === 'string' ? res.body : JSON.stringify(res.body ?? {});
    assert.equal(text.includes('vig_'), false, 'an agent token leaked');
    assert.equal(text.includes('vc_'), false, 'a command token leaked');
    assert.equal(text.includes('/home'), false, 'a path leaked');
    assert.equal(text.includes('template'), false, 'biometric material leaked');
    assert.equal(res.json().presence.state, 'OWNER');
  });
});
