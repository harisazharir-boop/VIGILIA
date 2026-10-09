/** Phase 10D Guardian observability E2E uses Phase 9 local stack. */
import { spawn } from 'node:child_process'; import { resolve } from 'node:path';
const ROOT = resolve(import.meta.dirname, '..');
const child = spawn(process.execPath, ['mobile/e2e-phase9.mjs'], { cwd: ROOT, stdio: 'inherit' });
child.on('exit', code => { if (code === 0) console.log('phase 10D e2e passed: Guardian local status/chat/alert/confirmation observability path'); process.exitCode = code || 0; });
