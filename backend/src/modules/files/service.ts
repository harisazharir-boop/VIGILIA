import { prisma } from '../../config/db.js';
import { wsHub } from '../../ws/hub.js';
import { newRequestId } from '../commands/service.js';
import type { FileRequest, FileResult } from '@vigilia/shared';
import { createWriteStream, createReadStream, existsSync, unlinkSync } from 'node:fs';
import { mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import { createHash } from 'node:crypto';

const transfers = new Map<string, { deviceId: string; direction: 'download' | 'upload'; size: number; received: number; next: number; expires: number; file: string; writer?: ReturnType<typeof createWriteStream> }>();
const TRANSFER_DIR = join(tmpdir(), 'vigilia-file-transfers');
const MAX_CHUNK_BYTES = 64 * 1024;

function removeTransfer(requestId: string): void {
  const transfer = transfers.get(requestId); transfers.delete(requestId);
  if (transfer?.writer) transfer.writer.destroy();
  if (transfer && existsSync(transfer.file)) unlinkSync(transfer.file);
}

function safeTargetHash(input: FileRequest): string | null {
  const value = input.entryId ?? input.destinationId ?? input.rootId;
  return value ? createHash('sha256').update(value).digest('hex') : null;
}

export async function dispatchFileRequest(deviceId: string, input: FileRequest): Promise<{ requestId: string; status: string }> {
  const requestId = newRequestId();
  const expiresAt = new Date(Date.now() + 120_000);
  await prisma.commandLog.create({ data: {
    deviceId, requestId, command: input.operation, status: 'queued', expiresAt,
    // CommandLog is existing audit system. Only operation ID and opaque IDs go in; no raw path/content.
    target: input.entryId ? `ref:${input.entryId.slice(0, 20)}` : input.rootId ?? null,
    note: input.name ? `name:${input.name.slice(0, 120)}` : null,
  } });
  await prisma.filesystemAudit.create({ data: {
    requestId, deviceId, operation: input.operation, targetHash: safeTargetHash(input), status: 'sent',
  } });
  wsHub.sendToAgent(deviceId, 'file.dispatch', { ...input, requestId, deadline: Math.floor(expiresAt.getTime() / 1000) });
  await prisma.commandLog.update({ where: { requestId }, data: { status: 'sent' } });
  return { requestId, status: 'sent' };
}

export async function applyFileResult(deviceId: string, result: FileResult): Promise<void> {
  const row = await prisma.commandLog.findUnique({ where: { requestId: result.requestId } });
  if (!row || row.deviceId !== deviceId || row.command !== result.operation || !row.command.startsWith('FILE_')) return;
  if (!['queued', 'sent'].includes(row.status)) return;
  const status = result.status === 'SUCCEEDED' ? 'succeeded' : 'failed';
  const detail = [result.code, result.reason, result.bytes !== undefined ? `bytes=${result.bytes}` : ''].filter(Boolean).join(' · ').slice(0, 1000);
  await prisma.commandLog.update({ where: { requestId: result.requestId }, data: { status, result: detail || null } });
  await prisma.filesystemAudit.update({
    where: { requestId: result.requestId },
    data: { status: result.status === 'NOT_ALLOWED' ? 'denied' : status, reason: detail || null, completedAt: new Date() },
  });
  wsHub.broadcastToDevice(deviceId, 'file.result', { ...result, deviceId });
}

export async function startAgentTransfer(deviceId: string, data: { requestId: string; direction: 'download' | 'upload'; size: number }): Promise<boolean> {
  const row = await prisma.commandLog.findUnique({ where: { requestId: data.requestId } });
  if (!row || row.deviceId !== deviceId || !['FILE_DOWNLOAD', 'FILE_UPLOAD'].includes(row.command) || data.size > 10 * 1024 * 1024) return false;
  await mkdir(TRANSFER_DIR, { recursive: true, mode: 0o700 });
  removeTransfer(data.requestId);
  const file = join(TRANSFER_DIR, `${data.requestId}.part`);
  const transfer: { deviceId: string; direction: 'download' | 'upload'; size: number; received: number; next: number; expires: number; file: string; writer?: ReturnType<typeof createWriteStream> } = { deviceId, direction: data.direction, size: data.size, received: 0, next: 0, expires: Date.now() + 120_000, file };
  if (data.direction === 'download') transfer.writer = createWriteStream(file, { flags: 'wx', mode: 0o600 });
  transfers.set(data.requestId, transfer);
  wsHub.broadcastToDevice(deviceId, 'file.transfer.start', data);
  if (data.direction === 'upload') wsHub.sendToAgent(deviceId, 'file.transfer.accepted', { requestId: data.requestId });
  return true;
}

export async function acceptAgentChunk(deviceId: string, data: { requestId: string; sequence: number; dataBase64: string }): Promise<boolean> {
  const transfer = transfers.get(data.requestId);
  if (!transfer || transfer.deviceId !== deviceId || transfer.direction !== 'download' || transfer.expires < Date.now() || data.sequence !== transfer.next) return false;
  const bytes = Buffer.from(data.dataBase64, 'base64');
  if (!bytes.length || bytes.length > MAX_CHUNK_BYTES || transfer.received + bytes.length > transfer.size) { removeTransfer(data.requestId); return false; }
  transfer.writer?.write(bytes); transfer.received += bytes.length; transfer.next += 1;
  wsHub.broadcastToDevice(deviceId, 'file.transfer.progress', { requestId: data.requestId, transferred: transfer.received, total: transfer.size });
  return true;
}

export function completeAgentTransfer(deviceId: string, requestId: string): boolean {
  const transfer = transfers.get(requestId);
  if (!transfer || transfer.deviceId !== deviceId || transfer.direction !== 'download' || transfer.received !== transfer.size) { removeTransfer(requestId); return false; }
  const writer = transfer.writer;
  transfer.writer = undefined;
  if (writer) writer.end(() => wsHub.broadcastToDevice(deviceId, 'file.transfer.complete', { requestId }));
  else wsHub.broadcastToDevice(deviceId, 'file.transfer.complete', { requestId });
  return true;
}

export async function acceptUploadChunk(deviceId: string, requestId: string, sequence: number, bytes: Buffer): Promise<{ transferred: number; total: number }> {
  const transfer = transfers.get(requestId);
  if (!transfer || transfer.deviceId !== deviceId || transfer.direction !== 'upload' || transfer.expires < Date.now() || sequence !== transfer.next || !bytes.length || bytes.length > MAX_CHUNK_BYTES || transfer.received + bytes.length > transfer.size) throw new Error('NOT_ALLOWED');
  transfer.received += bytes.length; transfer.next += 1;
  wsHub.sendToAgent(deviceId, 'file.transfer.chunk', { requestId, sequence, dataBase64: bytes.toString('base64') });
  return { transferred: transfer.received, total: transfer.size };
}

export function completeUpload(deviceId: string, requestId: string): void {
  const transfer = transfers.get(requestId);
  if (!transfer || transfer.deviceId !== deviceId || transfer.direction !== 'upload' || transfer.received !== transfer.size) throw new Error('NOT_ALLOWED');
  wsHub.sendToAgent(deviceId, 'file.transfer.complete', { requestId });
}

export function downloadStream(deviceId: string, requestId: string): Readable | null {
  const transfer = transfers.get(requestId);
  if (!transfer || transfer.deviceId !== deviceId || transfer.direction !== 'download' || transfer.received !== transfer.size || !existsSync(transfer.file)) return null;
  return createReadStream(transfer.file).on('close', () => removeTransfer(requestId));
}

export function cancelTransfer(deviceId: string, requestId: string): void {
  const transfer = transfers.get(requestId); if (!transfer || transfer.deviceId !== deviceId) return;
  wsHub.sendToAgent(deviceId, 'file.transfer.cancel', { requestId }); removeTransfer(requestId);
}
