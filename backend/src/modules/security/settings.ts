/**
 * Security settings (Phase 5F) — the small set of toggles the mobile app owns.
 *
 * Three booleans, no more:
 *
 *   intrusionDetection  whether the device acts on an unknown user at all
 *   guestMode           whether an unknown user is admitted as a guest or
 *                       stays in the protected `UNKNOWN` state
 *   autoLock            whether repeated failures raise `LOCKED`
 *
 * They are written to the `OwnerState.capabilities` row, which is the state
 * the device pushed and re-reads. The device keeps the authority: if its
 * platform cannot perform one of these, its next `security.state` frame
 * reports the real capability and the phone's switch re-syncs to the truth
 * rather than pretending.
 *
 * There is deliberately no toggle for face recognition or liveness here.
 * Those are enrolment states the device owns, not settings a phone can flip
 * remotely — turning face recognition off from the phone would silently
 * downgrade the owner's protection.
 */
import { prisma } from '../../config/db.js';
import { logger } from '../../config/logger.js';
import { Prisma } from '@prisma/client';
import type { SecurityCapabilities, SecuritySettings } from '@vigilia/shared';

/** The settings the mobile app may write, mapped onto capability flags. */
export function settingsToCapabilities(
  base: Partial<SecurityCapabilities>,
  settings: SecuritySettings,
): { capabilities: SecurityCapabilities; autoLock: boolean } {
  const capabilities: SecurityCapabilities = {
    faceRecognition: base.faceRecognition ?? false,
    liveness: base.liveness ?? false,
    guestMode: base.guestMode ?? false,
    intrusionDetection: base.intrusionDetection ?? false,
    dailyCleanup: base.dailyCleanup ?? false,
  };
  if (settings.intrusionDetection !== undefined)
    capabilities.intrusionDetection = settings.intrusionDetection;
  if (settings.guestMode !== undefined) capabilities.guestMode = settings.guestMode;
  return { capabilities, autoLock: settings.autoLock ?? true };
}

/**
 * Merge the requested settings into the stored capabilities and persist them.
 *
 * `autoLock` is not a capability the device reports — the lock is a
 * consequence of the intrusion policy — but the device reads it from the same
 * row, so it is stored there and the capabilities object carries it through.
 */
export async function applySecuritySettings(
  deviceId: string,
  settings: SecuritySettings,
): Promise<SecuritySettings> {
  const row = await prisma.ownerState.findUnique({ where: { deviceId } });
  const stored = (row?.capabilities ?? {}) as Partial<SecurityCapabilities> & {
    autoLock?: boolean;
  };
  const base: Partial<SecurityCapabilities> = {
    faceRecognition: !!stored.faceRecognition,
    liveness: !!stored.liveness,
    guestMode: !!stored.guestMode,
    intrusionDetection: !!stored.intrusionDetection,
    dailyCleanup: !!stored.dailyCleanup,
  };
  const { capabilities } = settingsToCapabilities(base, settings);
  const merged: SecurityCapabilities & { autoLock: boolean } = {
    ...capabilities,
    autoLock: settings.autoLock ?? stored.autoLock ?? true,
  };

  const json = merged as unknown as Prisma.InputJsonValue;
  if (row) {
    await prisma.ownerState.update({ where: { deviceId }, data: { capabilities: json } });
  } else {
    await prisma.ownerState.create({
      data: {
        id: deviceId,
        deviceId,
        state: 'UNKNOWN',
        since: Math.floor(Date.now() / 1000),
        failedAttempts: 0,
        capabilities: json,
      },
    });
  }

  logger.info({ deviceId, settings }, 'security settings applied');
  return {
    intrusionDetection: capabilities.intrusionDetection,
    guestMode: capabilities.guestMode,
    autoLock: merged.autoLock === true,
  };
}

/** Read the stored settings back, with the same defaults. */
export async function readSecuritySettings(deviceId: string): Promise<SecuritySettings> {
  const row = await prisma.ownerState.findUnique({ where: { deviceId } });
  const stored = (row?.capabilities ?? {}) as Partial<SecurityCapabilities> & {
    autoLock?: boolean;
  };
  return {
    intrusionDetection: !!stored.intrusionDetection,
    guestMode: !!stored.guestMode,
    autoLock: stored.autoLock !== false,
  };
}
