/**
 * Process telemetry service (Phase 3A).
 *
 * Read-only telemetry with the same properties as every other collector: the
 * agent reports, the backend stores one latest row per device, and no route
 * can touch a process.
 */
import { prisma } from '../../config/db.js';
import { wsHub } from '../../ws/hub.js';
import { logger } from '../../config/logger.js';
import { Prisma } from '@prisma/client';
import type { ProcessSnapshotInput } from '@vigilia/shared';

export async function storeProcessSnapshot(
  deviceId: string,
  snapshot: ProcessSnapshotInput,
): Promise<ProcessSnapshotInput> {
  const json = (snapshot as unknown as Prisma.InputJsonValue) ?? {};
  const row = await prisma.processSnapshot.create({
    data: {
      deviceId,
      snapshot: json,
      totalCount: Number(snapshot.totalCount ?? snapshot.processes?.length ?? 0),
      limit: Number(snapshot.limit ?? snapshot.processes?.length ?? 0),
    },
  });

  wsHub.broadcastToDevice(deviceId, 'processes.latest', {
    deviceId,
    snapshot: row.snapshot,
  });

  logger.debug({ deviceId, count: row.totalCount }, 'process snapshot stored');
  return row.snapshot as unknown as ProcessSnapshotInput;
}

export async function latestProcessSnapshot(
  deviceId: string,
): Promise<ProcessSnapshotInput | null> {
  const row = await prisma.processSnapshot.findFirst({
    where: { deviceId },
    orderBy: { timestamp: 'desc' },
    take: 1,
  });
  if (!row) return null;
  return row.snapshot as unknown as ProcessSnapshotInput;
}
