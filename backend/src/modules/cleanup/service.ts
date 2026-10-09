/**
 * Storage cleanup service — Phase 4B/4C.
 *
 * Cleanup is built on the Phase 2 command surface rather than beside it:
 * there is no new credential, no new socket capability and no second audit
 * system. What this module adds is the *action allowlist* and the preview.
 *
 * The rules, each enforced in code:
 *
 *  1. **An action id is the only cleanup request shape.** `CLEAN_*` is a
 *     fixed enum in `shared`; a request carrying anything else is a
 *     validation error. There is no `path`, `target` or `glob` field on a
 *     cleanup request, so an arbitrary-deletion request cannot be expressed.
 *  2. **The backend is the authorization boundary.** A preview is public
 *     (read-only, no token) because it changes nothing. Execution goes
 *     through `POST /api/devices/:id/commands` with the paired command token,
 *     exactly like `APPLICATION_CLOSE`.
 *  3. **The device decides what an action means.** The backend stores the
 *     action descriptors the agent pushed and never computes a path. An
 *     action the device never reported is `NOT_ALLOWED` before any audit row
 *     exists — the same gate the application catalog uses.
 *  4. **Preview is dry-run.** It is answered from the stored breakdown and
 *     never reaches the agent, because nothing is modified.
 *  5. **AI is advisory only.** `scoreRecommendations` turns the stored
 *     breakdown into structured recommendations and validates every action id
 *     against the allowlist; a recommendation naming an unknown action is
 *     dropped rather than stored or shown.
 */
import { prisma } from '../../config/db.js';
import { wsHub } from '../../ws/hub.js';
import { logger } from '../../config/logger.js';
import { Prisma } from '@prisma/client';
import type { CleanupPlan as PrismaCleanupPlan } from '@prisma/client';
import type {
  CleanAction,
  CleanActionDescriptor,
  CleanupRecommendation,
  StorageCategory,
  StorageSummaryPublic,
} from '@vigilia/shared';
import {
  CLEAN_ACTIONS,
  CLEAN_ACTION_CATEGORIES,
  CLEAN_ACTION_DESCRIPTIONS,
  CLEAN_ACTION_LABELS,
  CLEAN_PLATFORMS,
} from '@vigilia/shared';

/** A plan row older than this is stale: the agent has not refreshed it. */
export const CLEANUP_PLAN_STALE_SECONDS = 600;

export function planRowToPublic(
  row: PrismaCleanupPlan,
  devicePlatform: 'linux' | 'windows' | null,
): { actions: CleanActionDescriptor[]; recommendations: CleanupRecommendation[] } {
  const stored = (row.plan ?? {}) as {
    actions?: CleanActionDescriptor[];
    recommendations?: CleanupRecommendation[];
  };
  const actions = (Array.isArray(stored.actions) ? stored.actions : []).filter((a) =>
    (CLEAN_ACTIONS as readonly string[]).includes(a.actionId),
  );
  const recommendations = (Array.isArray(stored.recommendations) ? stored.recommendations : []).filter(
    (r) => (CLEAN_ACTIONS as readonly string[]).includes(r.actionId),
  );

  return {
    actions: devicePlatform
      ? actions.filter((a) => (a.platforms as readonly string[]).includes(devicePlatform))
      : actions,
    recommendations,
  };
}

/**
 * Persist the plan the agent pushed and broadcast it to the device's
 * subscribers.
 *
 * @param deviceId the authenticated connection's device, never the frame's
 * claim — an agent cannot publish a cleanup plan for another machine.
 */
export async function storeCleanupPlan(
  deviceId: string,
  actions: CleanActionDescriptor[],
  recommendations: CleanupRecommendation[],
): Promise<{ actions: CleanActionDescriptor[]; recommendations: CleanupRecommendation[] }> {
  // Re-validate here rather than trusting the validated frame: the stored row
  // is what the mobile client picks from, so only allowlisted ids land in it.
  const safeActions = actions.filter((a) => (CLEAN_ACTIONS as readonly string[]).includes(a.actionId));
  const safeRecommendations = recommendations.filter((r) =>
    (CLEAN_ACTIONS as readonly string[]).includes(r.actionId),
  );

  const plan = {
    actions: safeActions.map((a) => ({
      actionId: a.actionId,
      label: a.label.slice(0, 48),
      description: a.description.slice(0, 280),
      estimatedBytes: Math.max(0, Math.floor(a.estimatedBytes)),
      itemCount: Math.max(0, Math.floor(a.itemCount)),
      platforms: a.platforms.filter((p) => (CLEAN_PLATFORMS as readonly string[]).includes(p)),
      dryRunSupported: true,
      estimateReliable: !!a.estimateReliable,
    })),
    recommendations: safeRecommendations.map((r) => ({
      actionId: r.actionId,
      reason: r.reason.slice(0, 280),
      estimatedBytes: Math.max(0, Math.floor(r.estimatedBytes)),
    })),
  };

  const json = (plan as unknown as Prisma.InputJsonValue) ?? {};
  const row = await prisma.cleanupPlan.upsert({
    where: { deviceId },
    create: { id: deviceId, deviceId, plan: json },
    update: { plan: json },
  });

  const publicPlan = planRowToPublic(row, null);
  wsHub.broadcastToDevice(deviceId, 'cleanup.latest', {
    deviceId,
    actions: publicPlan.actions,
    recommendations: publicPlan.recommendations,
  });

  logger.debug(
    { deviceId, actions: publicPlan.actions.length, recommendations: publicPlan.recommendations.length },
    'cleanup plan stored',
  );
  return publicPlan;
}

/** Latest plan for a device, or null when the agent has never sent one. */
export async function latestCleanupPlan(
  deviceId: string,
  devicePlatform: 'linux' | 'windows' | null = null,
): Promise<{ actions: CleanActionDescriptor[]; recommendations: CleanupRecommendation[] } | null> {
  const row = await prisma.cleanupPlan.findUnique({ where: { deviceId } });
  if (!row) return null;
  return planRowToPublic(row, devicePlatform);
}

/** True when the plan is recent enough to call "current". */
export function isPlanFresh(row: { updatedAt: Date } | null): boolean {
  if (!row) return false;
  const ageSeconds = (Date.now() - row.updatedAt.getTime()) / 1000;
  return ageSeconds <= CLEANUP_PLAN_STALE_SECONDS;
}

/**
 * Resolve a mobile-supplied action id against the device's own plan.
 *
 * Returns the descriptor the agent pushed, or null when the device never
 * reported that action. This is the single check that decides whether a
 * cleanup is dispatched at all: no plan entry, no command — the mobile
 * client cannot name an action the device did not register, and it can never
 * name a path.
 */
export async function resolveCleanupAction(
  deviceId: string,
  actionId: string | undefined,
): Promise<CleanActionDescriptor | null> {
  if (!actionId) return null;
  if (!(CLEAN_ACTIONS as readonly string[]).includes(actionId)) return null;
  const plan = await latestCleanupPlan(deviceId);
  if (!plan) return null;
  return plan.actions.find((a) => a.actionId === actionId) ?? null;
}

/**
 * Compute a preview for one action from the device's stored breakdown.
 *
 * Read-only by construction: this reads `StorageReport` and `CleanupPlan`
 * rows and never writes anything. The agent is not contacted, because there
 * is nothing for it to do — nothing is modified.
 */
export async function previewCleanup(
  deviceId: string,
  actionId: CleanAction,
): Promise<
  | { ok: true; preview: CleanActionDescriptor; storage: StorageSummaryPublic | null }
  | { ok: false; code: string; message: string; status: number }
> {
  if (!(CLEAN_ACTIONS as readonly string[]).includes(actionId)) {
    return { ok: false, code: 'VALIDATION_FAILED', message: 'unknown cleanup action', status: 400 };
  }

  const plan = await latestCleanupPlan(deviceId);
  const storageRow = await prisma.storageReport.findUnique({ where: { deviceId } });
  const summary = storageRow
    ? ((storageRow.summary ?? {}) as unknown as StorageSummaryPublic)
    : null;

  if (plan) {
    const descriptor = plan.actions.find((a) => a.actionId === actionId);
    if (descriptor) {
      return { ok: true, preview: descriptor, storage: summary };
    }
  }

  // No plan yet: fall back to the stored breakdown so a preview is still
  // possible before the agent's next push. The estimate is marked
  // unreliable so the UI says "partial" rather than presenting it as exact.
  if (summary) {
    const category = (summary.categories ?? []).find(
      (c) => c.categoryId === CLEAN_ACTION_CATEGORIES[actionId],
    );
    if (category) {
      return {
        ok: true,
        preview: {
          actionId,
          label: CLEAN_ACTION_LABELS[actionId],
          description: CLEAN_ACTION_DESCRIPTIONS[actionId],
          estimatedBytes: category.bytes,
          itemCount: category.itemCount,
          platforms: [...CLEAN_PLATFORMS],
          dryRunSupported: true,
          estimateReliable: category.safeToAnalyze,
        },
        storage: summary,
      };
    }
  }

  return {
    ok: false,
    code: 'NOT_ALLOWED',
    message: `${actionId} is not offered by this device`,
    status: 400,
  };
}

/**
 * Score the stored breakdown into AI cleanup recommendations (Phase 4C).
 *
 * The rules are deliberately conservative, and the point of scoring locally
 * is that the recommendation set is *derived from the device's own
 * measurement* rather than from anything a client or an AI endpoint
 * supplied:
 *
 *  - only categories the analyser measured and marked `safeToAnalyze`;
 *  - only actions in the cleanup allowlist;
 *  - only actions the device's plan says are supported on its platform;
 *  - nothing below a threshold, so the UI is not flooded with 2 KB tips;
 *  - an empty list when nothing meaningful is safe to recommend.
 *
 * Every returned action id is re-validated against `CLEAN_ACTIONS` before it
 * is stored, so a malformed or hostile upstream cannot smuggle an unknown
 * action into the plan.
 */
export function scoreRecommendations(
  summary: StorageSummaryPublic | null,
  actions: CleanActionDescriptor[],
): CleanupRecommendation[] {
  if (!summary) return [];

  const measured = new Map<string, StorageCategory>();
  for (const category of summary.categories ?? []) {
    measured.set(category.categoryId, category);
  }

  const supported = new Set(actions.map((a) => a.actionId));
  const out: CleanupRecommendation[] = [];

  for (const actionId of CLEAN_ACTIONS) {
    const categoryId = CLEAN_ACTION_CATEGORIES[actionId];
    const category = measured.get(categoryId);
    if (!category) continue;
    if (!category.safeToAnalyze) continue;
    if (category.bytes < MIN_RECOMMENDABLE_BYTES) continue;
    if (!supported.has(actionId)) continue;

    out.push({
      actionId,
      reason: `${CLEAN_ACTION_LABELS[actionId]} is using ${humanBytes(category.bytes)} of storage.`,
      estimatedBytes: category.bytes,
    });
  }

  // Largest first: the operator wants the biggest win at the top.
  return out.sort((a, b) => b.estimatedBytes - a.estimatedBytes).slice(0, MAX_RECOMMENDATIONS);
}

/** A category smaller than this is not worth a recommendation. */
export const MIN_RECOMMENDABLE_BYTES = 10 * 1024 * 1024;
/** Ceiling on the recommendation list, so the UI cannot be flooded. */
export const MAX_RECOMMENDATIONS = 5;

function humanBytes(size: number): string {
  let value = Math.max(0, size);
  for (const unit of ['B', 'KB', 'MB', 'GB']) {
    if (value < 1024) {
      return unit === 'B' ? `${Math.floor(value)} ${unit}` : `${value.toFixed(1)} ${unit}`;
    }
    value /= 1024;
  }
  return `${value.toFixed(1)} TB`;
}

/** The cleanup allowlist, for anything that needs it as a readonly array. */
export const CLEANUP_ACTIONS: readonly CleanAction[] = CLEAN_ACTIONS;
