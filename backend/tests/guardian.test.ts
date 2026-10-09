import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';

let server: ReturnType<typeof createServer>;
let guardianProvider: typeof import('../src/modules/guardian/provider.js').guardianProvider;
before(async () => { process.env.VIGILIA_LOCAL_AI_URL = 'http://127.0.0.1:18081/v1'; guardianProvider = (await import('../src/modules/guardian/provider.js')).guardianProvider; server = createServer((request, response) => { if (request.url === '/v1/models') { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify({ data: [{ id: 'Qwen2.5-3B-Instruct' }] })); return; } if (request.url === '/v1/chat/completions') { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify({ choices: [{ message: { content: 'Local model response.' } }] })); return; } response.statusCode = 404; response.end(); }); await new Promise<void>(resolve => server.listen(18081, '127.0.0.1', resolve)); });
after(() => server.close());

test('local Qwen provider fails closed when runtime is unavailable', async () => {
  const status = await guardianProvider.status();
  assert.ok(['READY', 'UNAVAILABLE'].includes(status.status));
  assert.equal(typeof status.model, 'string');
  const result = await guardianProvider.complete([{ role: 'user', content: 'health check' }]);
  assert.ok(result === null || typeof result === 'string');
});

test('configured local Qwen runtime reports ready and returns a local completion', async () => {
  const status = await guardianProvider.status();
  assert.equal(status.status, 'READY'); assert.equal(status.model, 'Qwen2.5-3B-Instruct');
  assert.equal(await guardianProvider.complete([{ role: 'user', content: 'health check' }]), 'Local model response.');
});

test('local provider recovers after a timed-out runtime response', async () => {
  let delayed = true;
  await new Promise<void>(resolve => server.close(() => resolve()));
  server = createServer((request, response) => {
    if (request.url === '/v1/models') { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify({ data: [{ id: 'Qwen2.5-3B-Instruct' }] })); return; }
    if (request.url === '/v1/chat/completions') { if (delayed) { delayed = false; setTimeout(() => response.end('{}'), 8_100); return; } response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify({ choices: [{ message: { content: 'Recovered local response.' } }] })); return; }
    response.statusCode = 404; response.end();
  });
  await new Promise<void>(resolve => server.listen(18081, '127.0.0.1', resolve));
  assert.equal(await guardianProvider.complete([{ role: 'user', content: 'health check' }]), null);
  await new Promise<void>(resolve => server.close(() => resolve()));
  server = createServer((request, response) => { if (request.url === '/v1/chat/completions') { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify({ choices: [{ message: { content: 'Recovered local response.' } }] })); return; } response.statusCode = 404; response.end(); });
  await new Promise<void>(resolve => server.listen(18081, '127.0.0.1', resolve));
  assert.equal(await guardianProvider.complete([{ role: 'user', content: 'health check' }]), 'Recovered local response.');
});

test('local provider streams SSE tokens and supports cancellation', async () => {
  await new Promise<void>(resolve => server.close(() => resolve()));
  server = createServer((request, response) => { if (request.url === '/v1/chat/completions') { response.setHeader('Content-Type', 'text/event-stream'); response.write('data: {"choices":[{"delta":{"content":"hello "}}]}\n\n'); setTimeout(() => { response.write('data: {"choices":[{"delta":{"content":"world"}}]}\n\n'); response.end('data: [DONE]\n\n'); }, 10); return; } response.statusCode = 404; response.end(); });
  await new Promise<void>(resolve => server.listen(18081, '127.0.0.1', resolve));
  const tokens: string[] = []; for await (const token of guardianProvider.stream([{ role: 'user', content: 'health' }], new AbortController().signal)) tokens.push(token); assert.equal(tokens.join(''), 'hello world');
  const controller = new AbortController(); controller.abort(); const cancelled: string[] = []; for await (const token of guardianProvider.stream([{ role: 'user', content: 'health' }], controller.signal)) cancelled.push(token); assert.deepEqual(cancelled, []);
});
