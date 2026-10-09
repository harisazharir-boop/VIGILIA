/**
 * Typed, validated environment access. Process exits loudly on missing config.
 */
import 'dotenv/config';
import dotenv from 'dotenv';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// npm workspaces run the backend from backend/, but the project .env sits at
// the repo root. dotenv/config only reads cwd/.env, so load the root copy
// explicitly (cwd/.env above still wins where both exist).
const ROOT_DIR = path.resolve(__dirname, '../../..');
dotenv.config({ path: path.resolve(ROOT_DIR, '.env') });

/**
 * A required value must resolve or the process exits loudly. The overloads
 * keep the return type `string` (never `string | undefined`) so callers in
 * the config object stay typed cleanly.
 */
function required(key: string): string;
function required(key: string, fallback: string): string;
function required(key: string, fallback?: string): string {
  const v = process.env[key] ?? fallback;
  if (!v) throw new Error(`[config] missing required env var: ${key}`);
  return v;
}

function integer(key: string, fallback: number): number {
  const raw = process.env[key] ?? String(fallback);
  const n = Number.parseInt(raw, 10);
  if (!Number.isFinite(n)) throw new Error(`[config] ${key} is not an integer: ${raw}`);
  return n;
}
function boundedInteger(key: string, fallback: number, min: number, max: number): number { const value = integer(key, fallback); if (value < min || value > max) throw new Error(`[config] ${key} must be between ${min} and ${max}`); return value; }

export const config = {
  nodeEnv: required('NODE_ENV', 'development'),
  host: required('HOST', '0.0.0.0'),
  port: integer('PORT', 3000),
  logLevel: required('LOG_LEVEL', 'info'),
  databaseUrl: required('DATABASE_URL'),
  corsOrigin: required('VIGILIA_CORS_ORIGIN', 'http://127.0.0.1:3000'),
  agentTokenTtlDays: integer('VIGILIA_AGENT_TOKEN_TTL_DAYS', 365),
  commandTtlSeconds: integer('VIGILIA_COMMAND_TTL_SECONDS', 120),
  localAiUrl: process.env.VIGILIA_LOCAL_AI_URL ?? 'http://127.0.0.1:8080/v1',
  localAiModel: process.env.VIGILIA_LOCAL_AI_MODEL ?? 'Qwen2.5-3B-Instruct',
  localAiModelPath: process.env.VIGILIA_LOCAL_AI_MODEL_PATH ?? '',
  localAiModelSha256: process.env.VIGILIA_LOCAL_AI_MODEL_SHA256 ?? '',
  localAiTimeoutMs: boundedInteger('VIGILIA_LOCAL_AI_TIMEOUT_MS', 8_000, 500, 60_000),
  localAiHealthTimeoutMs: boundedInteger('VIGILIA_LOCAL_AI_HEALTH_TIMEOUT_MS', 1_500, 250, 10_000),
  retentionDays: boundedInteger('VIGILIA_RETENTION_DAYS', 90, 30, 3650),
  retentionBatchSize: boundedInteger('VIGILIA_RETENTION_BATCH_SIZE', 100, 1, 1000),
  retentionIntervalMs: boundedInteger('VIGILIA_RETENTION_INTERVAL_MS', 6 * 60 * 60 * 1000, 60_000, 7 * 24 * 60 * 60 * 1000),
  aiAutoCleanup: process.env.VIGILIA_AI_AUTO_CLEANUP === '1',
  aiAnomalyArtifact: process.env.VIGILIA_AI_ANOMALY_ARTIFACT ?? '',
} as const;

export type AppConfig = typeof config;
