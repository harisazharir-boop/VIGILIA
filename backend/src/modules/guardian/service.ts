import { prisma } from '../../config/db.js';
import { newRequestId } from '../commands/service.js';
import type { AiActionPreview, AiAlert, AiResponse, AiRiskLevel, AiTool } from '@vigilia/shared';
import { OS_COMMANDS } from '@vigilia/shared';
import { dispatchCommand } from '../commands/service.js';
import { wsHub } from '../../ws/hub.js';
import { guardianProvider } from './provider.js';
import { getDailyCleanupConfig, dailyCleanupEligibility } from '../security/service.js';
import { readFileSync } from 'node:fs';
import { config } from '../../config/env.js';
import { createHash } from 'node:crypto';
import { AutomaticGuardianScheduler } from './automatic.js';

const rank: Record<AiRiskLevel, number> = { NORMAL: 0, WARNING: 1, HIGH: 2, CRITICAL: 3 };
const ALERT_COOLDOWN_MS = 5 * 60_000;
function canonical(value: unknown): string { if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`; if (value && typeof value === 'object') { const object = value as Record<string, unknown>; return `{${Object.keys(object).sort().map(key => `${JSON.stringify(key)}:${canonical(object[key])}`).join(',')}}`; } return JSON.stringify(value); }
type Artifact = { format: 'vigilia-anomaly'; version: 2; algorithm: 'isolation-forest'; features: string[]; training: { rows: number }; evaluation: { validated: boolean }; fallbackCalibration: { means: number[]; scales: number[]; zThreshold: number }; sha256: string };
function anomaly(sample: Record<string, unknown>): number | null {
  if (!config.aiAnomalyArtifact) return null;
  try { const artifact = JSON.parse(readFileSync(config.aiAnomalyArtifact, 'utf8')) as Artifact; const unsigned = { ...artifact }; delete (unsigned as { sha256?: string }).sha256; const digest = createHash('sha256').update(canonical(unsigned)).digest('hex'); const means = artifact.fallbackCalibration?.means; const scales = artifact.fallbackCalibration?.scales; if (artifact.format !== 'vigilia-anomaly' || artifact.version !== 2 || artifact.sha256 !== digest || artifact.algorithm !== 'isolation-forest' || !artifact.evaluation.validated || artifact.training.rows < 64 || artifact.features.join(',') !== 'cpu,ram,storage,temperature,latency,packet_loss' || !Array.isArray(means) || !Array.isArray(scales) || means.length !== 6 || scales.length !== 6 || means.some(value => typeof value !== 'number') || scales.some(value => typeof value !== 'number' || value <= 0)) return null; const hardware = sample.hardware as { cpuTemperatureC?: number } | undefined; const network = sample.networkTelemetry as { latencyMs?: number; packetLossPercent?: number } | undefined; const values = [sample.cpu, sample.ram, sample.storage, hardware?.cpuTemperatureC, network?.latencyMs, network?.packetLossPercent]; if (values.some(value => typeof value !== 'number')) return null; const z = values.reduce<number>((sum, value, index) => sum + Math.abs((Number(value) - Number(means[index])) / Number(scales[index])), 0) / values.length; return z >= artifact.fallbackCalibration.zThreshold ? z : null; } catch { return null; }
}
function riskFor(sample: Record<string, unknown>, owner: { state: string; failedAttempts: number } | null): { risk: AiRiskLevel; messages: string[]; tools: AiTool[] } {
  const messages: string[] = []; let risk: AiRiskLevel = 'NORMAL'; const raise = (value: AiRiskLevel, message: string) => { if (rank[value] > rank[risk]) risk = value; messages.push(message); };
  const percent = (key: string) => typeof sample[key] === 'number' ? sample[key] : null;
  const cpu = percent('cpu'); const ram = percent('ram'); const storage = percent('storage');
  const hardware = sample.hardware as { cpuTemperatureC?: number } | undefined;
  const network = sample.networkTelemetry as { connected?: boolean; packetLossPercent?: number } | undefined;
  if (cpu !== null && cpu >= 95) raise('HIGH', `CPU is sustained at ${cpu}%.`); else if (cpu !== null && cpu >= 85) raise('WARNING', `CPU is elevated at ${cpu}%.`);
  if (ram !== null && ram >= 95) raise('HIGH', `RAM is critically high at ${ram}%.`); else if (ram !== null && ram >= 85) raise('WARNING', `RAM is elevated at ${ram}%.`);
  if (storage !== null && storage >= 95) raise('HIGH', `Storage is nearly full at ${storage}%.`); else if (storage !== null && storage >= 85) raise('WARNING', `Storage is high at ${storage}%.`);
  if (hardware?.cpuTemperatureC !== undefined && hardware.cpuTemperatureC >= 90) raise('HIGH', `CPU temperature is high at ${hardware.cpuTemperatureC} C.`);
  if (network?.connected === false) raise('WARNING', 'Network is disconnected.');
  if ((network?.packetLossPercent ?? 0) >= 50) raise('WARNING', `Network packet loss is ${network?.packetLossPercent}%.`);
  if (owner?.state === 'LOCKED' || (owner?.failedAttempts ?? 0) >= 3) raise('CRITICAL', 'Security is locked after failed authentication attempts.');
  else if (owner?.state === 'UNKNOWN' || owner?.state === 'GUEST') raise('WARNING', `Security state is ${owner.state}.`);
  const outlier = anomaly(sample); if (outlier !== null) raise('WARNING', 'Local Isolation Forest artifact detected an anomalous telemetry trend.');
  return { risk, messages, tools: ['INSPECT_HEALTH', 'INSPECT_SECURITY', 'INSPECT_NETWORK', 'RECOMMEND_CLEANUP'] };
}

function intent(message: string): { action: string; reason: string } | null { const text = message.toLowerCase(); for (const action of OS_COMMANDS) if (text.includes(action.toLowerCase())) return { action, reason: `Existing ${action} command will be dispatched only after confirmation.` }; return /\b(delete|remove|clean|terminate|close)\b/.test(text) ? { action: 'UNSUPPORTED', reason: 'Guardian cannot map this request to a safe existing command.' } : null; }
async function runGuardian(deviceId: string, message: string, useModel: boolean): Promise<AiResponse & { preview?: AiActionPreview }> {
  const [telemetry, owner] = await Promise.all([prisma.telemetry.findUnique({ where: { deviceId } }), prisma.ownerState.findUnique({ where: { deviceId } })]);
  const requestId = newRequestId(); const sample = (telemetry?.sample ?? {}) as Record<string, unknown>; const assessment = riskFor(sample, owner);
  const alerts: AiAlert[] = [];
  if (assessment.risk !== 'NORMAL') {
    const category = assessment.risk === 'CRITICAL' ? 'security' : 'health'; const message = assessment.messages.join(' ').slice(0, 280);
    const [previous, preferences] = await Promise.all([prisma.aiAlert.findFirst({ where: { deviceId, severity: assessment.risk, category, message, createdAt: { gte: new Date(Date.now() - ALERT_COOLDOWN_MS) } }, orderBy: { createdAt: 'desc' } }), prisma.guardianPreferences.findUnique({ where: { deviceId } })]);
    if (!previous) {
      const alert = await prisma.aiAlert.create({ data: { deviceId, severity: assessment.risk, category, message } });
    alerts.push({ id: alert.id, deviceId, severity: alert.severity as AiRiskLevel, category: alert.category as AiAlert['category'], message: alert.message, createdAt: alert.createdAt.toISOString() });
      const enabled = category === 'security' ? preferences?.securityAlerts !== false : preferences?.healthAlerts !== false;
      if (enabled) wsHub.broadcastToDevice(deviceId, 'guardian.alert', { alert: alerts[0] });
    }
  }
  await Promise.all(assessment.tools.map(tool => prisma.aiAudit.create({ data: { requestId: `${requestId}_${tool}`, deviceId, tool, status: 'SUCCEEDED', reason: 'read-only inspection' } })));
  const cleanup = await getDailyCleanupConfig(deviceId);
  const cleanupEligible = dailyCleanupEligibility({ presence: owner ? { deviceId, state: owner.state as 'OWNER' | 'GUEST' | 'UNKNOWN' | 'LOCKED', since: owner.since, lastAuthenticatedAt: owner.lastAuthenticatedAt, failedAttempts: owner.failedAttempts, capabilities: { faceRecognition: false, liveness: false, guestMode: false, intrusionDetection: false, dailyCleanup: false }, lastDailyCleanupDate: owner.lastDailyCleanupDate, timestamp: owner.updatedAt.toISOString() } : null, config: cleanup.config, lastCleanupDate: cleanup.lastCleanupDate, today: new Date().toISOString().slice(0, 10) });
  const summary = assessment.messages.length ? assessment.messages.join(' ') : 'Laptop health and security signals are normal.';
  const safeUser = message.replace(/(?:token|password|secret|api[_ -]?key|\.env)/gi, '[redacted]').slice(0, 500);
  await prisma.aiMessage.create({ data: { deviceId, role: 'user', content: safeUser } });
  const history = await prisma.aiMessage.findMany({ where: { deviceId }, orderBy: { createdAt: 'desc' }, take: 8 });
  const modelReply = useModel ? await guardianProvider.complete([...history].reverse().map(item => ({ role: item.role === 'assistant' ? 'assistant' as const : 'user' as const, content: item.content }))) : null;
  const mapped = intent(safeUser); const confirmation = mapped && mapped.action !== 'UNSUPPORTED' ? await prisma.aiConfirmation.create({ data: { requestId, deviceId, action: mapped.action, status: 'PENDING', expiresAt: new Date(Date.now() + 5 * 60_000) } }) : null;
  const cleanupNote = cleanupEligible.eligible ? ' Daily Auto Cleanup policy is eligible; only existing device-side owner/once-per-day policy may run it.' : '';
  const output = confirmation ? `${summary} Confirmation required: ${mapped?.reason}${cleanupNote}` : mapped ? `${summary} Unsupported: ${mapped.reason}` : modelReply ? `${summary} ${modelReply}`.slice(0, 1200) : `${summary} Request: ${safeUser}${cleanupNote}`;
  await prisma.aiMessage.create({ data: { deviceId, role: 'assistant', content: output } });
  if (confirmation) await prisma.aiAudit.create({ data: { requestId: `${requestId}_CONFIRM`, deviceId, tool: 'CONFIRMATION', status: 'PENDING', reason: 'sensitive action requires approval' } });
  return { requestId, risk: assessment.risk, message: output, tools: assessment.tools.map(tool => ({ tool, status: 'SUCCEEDED' })), alerts, ...(confirmation ? { preview: { requestId, action: confirmation.action as AiActionPreview['action'], expiresAt: confirmation.expiresAt.toISOString(), confirmationRequired: true, reason: mapped!.reason } } : {}) };
}
export async function guardian(deviceId: string, message: string): Promise<AiResponse & { preview?: AiActionPreview }> { return runGuardian(deviceId, message, true); }
export async function decideConfirmation(deviceId: string, requestId: string, approved: boolean): Promise<{ status: string; commandRequestId?: string }> { const row = await prisma.aiConfirmation.findUnique({ where: { requestId } }); if (!row || row.deviceId !== deviceId || row.status !== 'PENDING') throw new Error('CONFIRMATION_NOT_FOUND'); const status = row.expiresAt < new Date() ? 'EXPIRED' : approved ? 'APPROVED' : 'DENIED'; await prisma.aiConfirmation.update({ where: { requestId }, data: { status, decidedAt: new Date() } }); let commandRequestId: string | undefined; if (status === 'APPROVED' && (OS_COMMANDS as readonly string[]).includes(row.action)) { const record = await dispatchCommand({ id: deviceId }, row.action as typeof OS_COMMANDS[number], 'Guardian confirmed action', undefined); commandRequestId = record.id; } await prisma.aiAudit.create({ data: { requestId: `${requestId}_DECISION`, deviceId, tool: 'CONFIRMATION', status, reason: commandRequestId ? `existing command ${row.action} dispatched: ${commandRequestId}` : 'no command dispatched' } }); return { status, ...(commandRequestId ? { commandRequestId } : {}) }; }
export async function modelStatus() { return guardianProvider.status(); }
const automaticScheduler = new AutomaticGuardianScheduler(
  async () => (await guardianProvider.status()).status === 'READY',
  async (deviceId, useModel) => { await runGuardian(deviceId, 'Automatic read-only health evaluation.', useModel); },
);
export function evaluateTelemetry(deviceId: string): void { automaticScheduler.schedule(deviceId); }
export function cancelTelemetryEvaluation(deviceId: string): void { automaticScheduler.cancel(deviceId); }
export function clearTelemetryEvaluations(): void { automaticScheduler.clear(); }
export async function alerts(deviceId: string): Promise<AiAlert[]> { const rows = await prisma.aiAlert.findMany({ where: { deviceId }, orderBy: { createdAt: 'desc' }, take: 50 }); return rows.map(row => ({ id: row.id, deviceId: row.deviceId, severity: row.severity as AiRiskLevel, category: row.category as AiAlert['category'], message: row.message, createdAt: row.createdAt.toISOString() })); }
export async function clearContext(deviceId: string) { await prisma.aiMessage.deleteMany({ where: { deviceId } }); }
export async function streamContext(deviceId: string, message: string, signal: AbortSignal) { const safe = message.replace(/(?:token|password|secret|api[_ -]?key|\.env)/gi, '[redacted]').slice(0, 500); await prisma.aiMessage.create({ data: { deviceId, role: 'user', content: safe } }); const rows = await prisma.aiMessage.findMany({ where: { deviceId }, orderBy: { createdAt: 'desc' }, take: 8 }); return guardianProvider.stream([...rows].reverse().map(row => ({ role: row.role === 'assistant' ? 'assistant' as const : 'user' as const, content: row.content })), signal); }
export async function auditHistory(deviceId: string) { return prisma.aiAudit.findMany({ where: { deviceId }, orderBy: { createdAt: 'desc' }, take: 100, select: { requestId: true, tool: true, status: true, reason: true, createdAt: true } }); }
export async function preferences(deviceId: string) { const row = await prisma.guardianPreferences.findUnique({ where: { deviceId } }); return { healthAlerts: row?.healthAlerts ?? true, securityAlerts: row?.securityAlerts ?? true }; }
export async function setPreferences(deviceId: string, value: { healthAlerts: boolean; securityAlerts: boolean }) { const row = await prisma.guardianPreferences.upsert({ where: { deviceId }, create: { deviceId, ...value }, update: value }); return { healthAlerts: row.healthAlerts, securityAlerts: row.securityAlerts }; }
