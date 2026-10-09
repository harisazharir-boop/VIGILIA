import { createHash } from 'node:crypto';
import { closeSync, existsSync, openSync, readFileSync, readSync, statSync } from 'node:fs';

export function validateModelArtifact(path, expectedSha256 = '') {
  if (!path || !existsSync(path)) return { ok: false, reason: 'MODEL_MISSING' };
  const stat = statSync(path); if (!stat.isFile() || stat.size < 64 * 1024 * 1024) return { ok: false, reason: 'MODEL_INVALID_SIZE' };
  const fd = openSync(path, 'r'); const header = Buffer.alloc(4); try { readSync(fd, header, 0, 4, 0); } finally { closeSync(fd); }
  if (header.toString('ascii') !== 'GGUF') return { ok: false, reason: 'MODEL_INVALID_FORMAT' };
  if (!expectedSha256) return { ok: true, sha256: null, size: stat.size };
  const digest = createHash('sha256').update(readFileSync(path)).digest('hex');
  return digest === expectedSha256.toLowerCase() ? { ok: true, sha256: digest, size: stat.size } : { ok: false, reason: 'MODEL_CHECKSUM_MISMATCH' };
}
