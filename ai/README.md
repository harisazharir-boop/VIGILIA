# VIGILIA local AI

Production inference is separate from training. Default runtime is llama.cpp
`llama-server` with a quantized Qwen2.5-3B-Instruct GGUF artifact. Model files
remain external deployment artifacts and must never be committed.

```sh
# Build/install llama.cpp separately, then set a local GGUF path.
export VIGILIA_QWEN_GGUF=/opt/vigilia/models/Qwen2.5-3B-Instruct-Q4_K_M.gguf
export VIGILIA_LLAMA_SERVER=/opt/llama.cpp/build/bin/llama-server
./scripts/llama-runtime.sh
```

The profile binds only `127.0.0.1:8080`, exposes the alias
`Qwen2.5-3B-Instruct`, limits default context to 2048 tokens, threads to four,
and parallel requests to one. Override `VIGILIA_LOCAL_AI_PORT`,
`VIGILIA_LOCAL_AI_THREADS`, `VIGILIA_LOCAL_AI_CONTEXT` or
`VIGILIA_LOCAL_AI_PARALLEL` only after sizing the target laptop. Backend uses
`VIGILIA_LOCAL_AI_URL`, `VIGILIA_LOCAL_AI_MODEL`, bounded inference timeout
and bounded health timeout. A runtime missing the configured model reports
`UNAVAILABLE`; Guardian keeps deterministic safe fallback.

Production artifacts live under `/opt/vigilia/models/`, outside Git, with
`/opt/vigilia/bin/llama-server`. Provision the official Qwen Q4_K_M GGUF using
a trusted SHA-256 in protected `/etc/vigilia/llama.env`. Runtime model ID must
remain `Qwen2.5-3B-Instruct`; keep it distinct from
`VIGILIA_LOCAL_AI_MODEL_PATH`. `vigilia-llama.service` validates artifact then
binds llama.cpp only to `127.0.0.1:8080`.

Training datasets belong outside production DB exports. Allowed records are
bounded telemetry features, derived anomaly labels, security event types and
redacted user/assistant instruction pairs. Do not include tokens, paths, file
contents, credentials, screenshots, face templates, images or embeddings.

## Phase 10B anomaly pipeline

Export a redacted offline dataset from an existing SQLite telemetry database:

```sh
python3 -m ai.export_telemetry backend/prisma/dev.db /tmp/vigilia-telemetry.json
```

Only complete numeric vectors are exported: CPU, RAM, storage, CPU
temperature, latency and packet loss. The export excludes device labels,
tokens, paths, files, conversations and biometric material and caps rows at
10,000. `ai.anomaly.train()` requires 64 real complete vectors and optional
local `scikit-learn`; it emits versioned `vigilia-anomaly` JSON with feature
contract, training/evaluation metadata, calibration and SHA-256 integrity.

Set `VIGILIA_AI_ANOMALY_ARTIFACT` only to an evaluated artifact. Production
rejects absent, malformed, tampered, incomplete or unvalidated artifacts and
uses existing real threshold rules instead. No synthetic labels or cloud
training are used.
