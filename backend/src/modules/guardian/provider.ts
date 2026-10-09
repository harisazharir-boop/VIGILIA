import { config } from '../../config/env.js';
import type { LocalModelStatus } from '@vigilia/shared';
import { validateModelArtifact } from './modelArtifact.js';

export interface GuardianProvider { complete(history: { role: 'user' | 'assistant'; content: string }[]): Promise<string | null>; stream(history: { role: 'user' | 'assistant'; content: string }[], signal: AbortSignal): AsyncGenerator<string>; status(): Promise<LocalModelStatus>; }
/** Local OpenAI-compatible server, e.g. llama.cpp/LocalAI serving Qwen. */
class LocalQwenProvider implements GuardianProvider {
  async status(): Promise<LocalModelStatus> {
    if (config.localAiModelPath && !validateModelArtifact(config.localAiModelPath, config.localAiModelSha256).ok) return { status: 'UNAVAILABLE', model: config.localAiModel, runtime: config.localAiUrl };
    try { const response = await fetch(`${config.localAiUrl.replace(/\/$/, '')}/models`, { signal: AbortSignal.timeout(config.localAiHealthTimeoutMs) }); if (!response.ok) throw new Error('unavailable'); const payload = await response.json() as { data?: { id?: string }[] }; const found = payload.data?.some(item => item.id === config.localAiModel); return found ? { status: 'READY', model: config.localAiModel, runtime: config.localAiUrl } : { status: 'UNAVAILABLE', model: config.localAiModel, runtime: config.localAiUrl }; } catch { return { status: 'UNAVAILABLE', model: config.localAiModel, runtime: config.localAiUrl }; }
  }
  async complete(history: { role: 'user' | 'assistant'; content: string }[]): Promise<string | null> {
    try {
      const response = await fetch(`${config.localAiUrl.replace(/\/$/, '')}/chat/completions`, { signal: AbortSignal.timeout(config.localAiTimeoutMs), method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: config.localAiModel, temperature: 0.2, max_tokens: 300, messages: [{ role: 'system', content: 'You are VIGILIA Local Guardian. Explain only supplied safe summaries. Never request secrets, credentials, paths, shell commands, or actions outside registered VIGILIA tools.' }, ...history] }) });
      if (!response.ok) return null;
      const payload = await response.json() as { choices?: { message?: { content?: string } }[] };
      return payload.choices?.[0]?.message?.content?.slice(0, 1200) ?? null;
    } catch { return null; }
  }
  async *stream(history: { role: 'user' | 'assistant'; content: string }[], signal: AbortSignal): AsyncGenerator<string> {
    try { const response = await fetch(`${config.localAiUrl.replace(/\/$/, '')}/chat/completions`, { signal, method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: config.localAiModel, temperature: 0.2, max_tokens: 300, stream: true, messages: [{ role: 'system', content: 'You are VIGILIA Local Guardian. Explain only supplied safe summaries. Never request secrets, credentials, paths, shell commands, or actions outside registered VIGILIA tools.' }, ...history] }) }); if (!response.ok || !response.body) return; const reader = response.body.getReader(); const decoder = new TextDecoder(); let pending = ''; while (true) { const next = await reader.read(); if (next.done) return; pending += decoder.decode(next.value, { stream: true }); const lines = pending.split('\n'); pending = lines.pop() ?? ''; for (const line of lines) { if (!line.startsWith('data: ')) continue; const raw = line.slice(6).trim(); if (raw === '[DONE]') return; try { const token = (JSON.parse(raw) as { choices?: { delta?: { content?: string } }[] }).choices?.[0]?.delta?.content; if (token) yield token.slice(0, 256); } catch { /* malformed local SSE event is ignored */ } } } } catch { return; }
  }
}
export const guardianProvider: GuardianProvider = new LocalQwenProvider();
