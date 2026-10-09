/**
 * Storage report service (Phase 4A).
 *
 * One `StorageReport` row per device, replaced wholesale on every report. The
 * agent is the only writer, over its own authenticated socket, and the row is
 * broadcast to every mobile socket watching the device.
 *
 * Read-only by construction: there is no delete, no rename and no cleanup verb
 * anywhere in this module. It stores a measurement and replays it.
 */
import { prisma } from '../../config/db.js';
import { wsHub } from '../../ws/hub.js';
import { logger } from '../../config/logger.js';
import { Prisma } from '@prisma/client';
import type { StorageSummaryPublic, StorageSummaryInput } from '@vigilia/shared';

/**
 * Persist the summary the agent pushed and broadcast it.
 *
 * @param deviceId the authenticated connection's device — never the frame's
 * claim, so an agent cannot publish a breakdown for another machine.
 */
export async function storeStorageSummary(
  deviceId: string,
  summary: StorageSummaryInput,
): Promise<StorageSummaryPublic> {
  const json = (summary as unknown as Prisma.InputJsonValue) ?? {};
  const row = await prisma.storageReport.upsert({
    where: { deviceId },
    create: { id: deviceId, deviceId, summary: json },
    update: { summary: json },
  });

  const payload = (row.summary ?? {}) as unknown as StorageSummaryPublic;
  wsHub.broadcastToDevice(deviceId, 'storage.latest', { deviceId, summary: payload });

  logger.debug(
    { deviceId, usagePercent: payload.usagePercent, categories: payload.categories?.length ?? 0 },
    'storage summary stored',
  );
  return payload;
}

/** Latest summary for a device, or null when the agent has never sent one. */
export async function latestStorageSummary(
  deviceId: string,
): Promise<StorageSummaryPublic | null> {
  const row = await prisma.storageReport.findUnique({ where: { deviceId } });
  if (!row) return null;
  return (row.summary ?? {}) as unknown as StorageSummaryPublic;
}
