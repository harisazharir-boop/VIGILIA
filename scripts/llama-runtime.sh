#!/usr/bin/env bash
set -euo pipefail

# External GGUF artifact and llama-server binary are deployment inputs, never
# committed. Conservative defaults fit Qwen2.5-3B-Instruct Q4_K_M on laptops.
: "${VIGILIA_LLAMA_SERVER:=llama-server}"
: "${VIGILIA_QWEN_GGUF:?set VIGILIA_QWEN_GGUF to Qwen2.5-3B-Instruct GGUF path}"
: "${VIGILIA_LOCAL_AI_PORT:=8080}"
: "${VIGILIA_LOCAL_AI_THREADS:=4}"
: "${VIGILIA_LOCAL_AI_CONTEXT:=2048}"
: "${VIGILIA_LOCAL_AI_PARALLEL:=1}"
: "${VIGILIA_LOCAL_AI_MODEL:=Qwen2.5-3B-Instruct}"
if [[ "$VIGILIA_LOCAL_AI_THREADS" -lt 1 || "$VIGILIA_LOCAL_AI_THREADS" -gt 16 || "$VIGILIA_LOCAL_AI_CONTEXT" -lt 512 || "$VIGILIA_LOCAL_AI_CONTEXT" -gt 4096 || "$VIGILIA_LOCAL_AI_PARALLEL" -ne 1 ]]; then
  printf '%s\n' 'Invalid local AI resource bounds' >&2; exit 1
fi
VIGILIA_LOCAL_AI_MODEL_PATH="$VIGILIA_QWEN_GGUF" node "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/deploy/model-preflight.mjs"
exec "$VIGILIA_LLAMA_SERVER" --model "$VIGILIA_QWEN_GGUF" --host 127.0.0.1 --port "$VIGILIA_LOCAL_AI_PORT" --alias "$VIGILIA_LOCAL_AI_MODEL" --threads "$VIGILIA_LOCAL_AI_THREADS" --ctx-size "$VIGILIA_LOCAL_AI_CONTEXT" --parallel "$VIGILIA_LOCAL_AI_PARALLEL" --no-webui
