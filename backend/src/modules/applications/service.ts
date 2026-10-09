/**
 * Application catalog service (Phase 3B).
 *
 * The catalog is what the device's own agent discovered on the machine it runs
 * on. The backend stores it and replays it; it never launches anything, and
 * it never asks the agent to launch anything it was not told about. An
 * application id is a registry key, not an executable path: the agent resolves
 * the id against its own registry, so a mobile client cannot choose what runs.
 */
import { prisma } from '../../config/db.js';
import { wsHub } from '../../ws/hub.js';
import { logger } from '../../config/logger.js';
import { Prisma } from '@prisma/client';
import { APPLICATION_ID_MAX_LENGTH } from '@vigilia/shared';
import type { ApplicationPublic } from '@vigilia/shared';

/**
 * The application id charset. Deliberately narrow: lowercase letters, digits,
 * `-`, `_` and `.`. No path separators, no spaces, no shell metacharacters,
 * so even a hostile client cannot smuggle anything that looks like a path or
 * an argument through this field.
 */
const APP_ID_RE = /^[a-z0-9][a-z0-9._-]*$/;

/**
 * Persist the catalog the agent pushed and broadcast it.
 *
 * @param deviceId the authenticated connection's device, never the frame's
 * claim — an agent cannot publish a catalog for another machine.
 */
export async function storeApplicationCatalog(
  deviceId: string,
  catalog: { applications?: ApplicationPublic[] },
): Promise<ApplicationPublic[]> {
  const applications = (catalog.applications ?? []).filter((a: ApplicationPublic) => {
    if (typeof a.applicationId !== 'string') return false;
    if (a.applicationId.length > APPLICATION_ID_MAX_LENGTH) return false;
    if (!APP_ID_RE.test(a.applicationId)) return false;
    return true;
  });

  const json = { applications } as unknown as Prisma.InputJsonValue;
  const row = await prisma.applicationCatalog.upsert({
    where: { deviceId },
    create: { id: deviceId, deviceId, catalog: json },
    update: { catalog: json },
  });

  const payload = (row.catalog ?? { applications: [] }) as unknown as { applications: ApplicationPublic[] };
  wsHub.broadcastToDevice(deviceId, 'applications.latest', { deviceId, catalog: payload });

  logger.debug({ deviceId, applications: payload.applications?.length ?? 0 }, 'application catalog stored');
  return payload.applications ?? [];
}

/** Latest catalog for a device, or null when the agent has never sent one. */
export async function latestApplicationCatalog(
  deviceId: string,
): Promise<ApplicationPublic[] | null> {
  const row = await prisma.applicationCatalog.findUnique({ where: { deviceId } });
  if (!row) return null;
  const payload = (row.catalog ?? { applications: [] }) as unknown as { applications: ApplicationPublic[] };
  const applications = payload.applications ?? null;
  if (!applications) return null;
  return applications;
}
