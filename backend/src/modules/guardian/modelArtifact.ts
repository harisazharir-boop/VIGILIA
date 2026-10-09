import { createHash } from 'node:crypto';
import { closeSync, existsSync, openSync, readFileSync, readSync, statSync } from 'node:fs';

export function validateModelArtifact(path: string, expectedSha256 = ''): { ok: boolean } {
  if (!path || !existsSync(path)) return { ok: false };
  const stat = statSync(path); if (!stat.isFile() || stat.size < 64 * 1024 * 1024) return { ok: false };
  const fd = openSync(path, 'r'); const header = Buffer.alloc(4); try { readSync(fd, header, 0, 4, 0); } finally { closeSync(fd); }
  if (header.toString('ascii') !== 'GGUF') return { ok: false };
  if (!expectedSha256) return { ok: true };
  return { ok: createHash('sha256').update(readFileSync(path)).digest('hex') === expectedSha256.toLowerCase() };
}
