import { prisma } from '../../config/db.js';
import { config } from '../../config/env.js';
import { logger } from '../../config/logger.js';

async function prune(table: 'aiMessage' | 'aiAudit' | 'securityEvent', cutoff: Date): Promise<number> {
  if (table === 'aiMessage') {
    const rows = await prisma.aiMessage.findMany({ where: { createdAt: { lt: cutoff } }, orderBy: { createdAt: 'asc' }, take: config.retentionBatchSize, select: { id: true } });
    return rows.length ? (await prisma.aiMessage.deleteMany({ where: { id: { in: rows.map((row) => row.id) } } })).count : 0;
  }
  if (table === 'aiAudit') {
    const rows = await prisma.aiAudit.findMany({ where: { createdAt: { lt: cutoff } }, orderBy: { createdAt: 'asc' }, take: config.retentionBatchSize, select: { id: true } });
    return rows.length ? (await prisma.aiAudit.deleteMany({ where: { id: { in: rows.map((row) => row.id) } } })).count : 0;
  }
  const rows = await prisma.securityEvent.findMany({ where: { createdAt: { lt: cutoff } }, orderBy: { createdAt: 'asc' }, take: config.retentionBatchSize, select: { id: true } });
  return rows.length ? (await prisma.securityEvent.deleteMany({ where: { id: { in: rows.map((row) => row.id) } } })).count : 0;
}

export async function pruneRetention(now = new Date()): Promise<{ aiMessage: number; aiAudit: number; securityEvent: number }> {
  const cutoff = new Date(now.getTime() - config.retentionDays * 24 * 60 * 60 * 1000);
  return {
    aiMessage: await prune('aiMessage', cutoff),
    aiAudit: await prune('aiAudit', cutoff),
    securityEvent: await prune('securityEvent', cutoff),
  };
}

export async function pruneRetentionSafely(prune: () => Promise<unknown> = pruneRetention): Promise<boolean> {
  try { await prune(); return true; } catch (err) { logger.warn({ err }, 'retention pruning failed'); return false; }
}
