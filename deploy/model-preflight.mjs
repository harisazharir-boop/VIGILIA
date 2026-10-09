import { validateModelArtifact } from '../ai/model_artifact.mjs';
const result = validateModelArtifact(process.env.VIGILIA_LOCAL_AI_MODEL_PATH || process.env.VIGILIA_QWEN_GGUF || '', process.env.VIGILIA_LOCAL_AI_MODEL_SHA256 || process.env.VIGILIA_QWEN_GGUF_SHA256 || '');
if (!result.ok) { console.error(`Local model preflight failed: ${result.reason}`); process.exit(1); }
console.log(`Local GGUF artifact preflight passed (${result.size} bytes)`);
