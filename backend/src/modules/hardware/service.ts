import { prisma } from '../../config/db.js';
import { wsHub } from '../../ws/hub.js';

const capabilities = new Map<string, Record<string, boolean>>();
function requestId(): string { const bytes = new Uint8Array(12); crypto.getRandomValues(bytes); return `req_${Buffer.from(bytes).toString('base64url')}`; }
export function storeCapabilities(deviceId: string, value: Record<string, boolean>) { capabilities.set(deviceId, value); }
export function capabilityFor(deviceId: string) { return capabilities.get(deviceId) ?? {}; }
export async function dispatchBrightness(deviceId: string, value: number) {
  const id = requestId();
  if (!capabilityFor(deviceId).BRIGHTNESS) { await prisma.hardwareAudit.create({ data: { requestId: id, deviceId, control: 'BRIGHTNESS', status: 'UNSUPPORTED', reason: 'native brightness unavailable' } }); return { requestId: id, control: 'BRIGHTNESS', status: 'UNSUPPORTED' as const, reason: 'native brightness unavailable' }; }
  await prisma.hardwareAudit.create({ data: { requestId: id, deviceId, control: 'BRIGHTNESS', status: 'SENT' } });
  wsHub.sendToAgent(deviceId, 'hardware.control', { requestId: id, control: 'BRIGHTNESS', value });
  return { requestId: id, control: 'BRIGHTNESS', status: 'SENT' as const };
}
export async function applyResult(deviceId: string, result: { requestId: string; control: string; status: string; reason?: string }) {
  const row = await prisma.hardwareAudit.findUnique({ where: { requestId: result.requestId } }); if (!row || row.deviceId !== deviceId || row.control !== result.control) return;
  await prisma.hardwareAudit.update({ where: { requestId: result.requestId }, data: { status: result.status, reason: result.reason?.slice(0, 280) } });
  wsHub.broadcastToDevice(deviceId, 'hardware.control.result', result);
}
