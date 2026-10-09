/**
 * Header names the protocol uses, in one place.
 */
export const HEADER_COMMAND_TOKEN = 'x-vigilia-command-token';
export const HEADER_DEVICE_ID = 'x-vigilia-device-id';
export const HEADER_AGENT_TOKEN = 'authorization';

export function readCommandToken(headers: Record<string, string | string[] | undefined>): string {
  const raw = headers[HEADER_COMMAND_TOKEN];
  if (Array.isArray(raw)) return raw[0] ?? '';
  return raw ?? '';
}

export function readDeviceId(headers: Record<string, string | string[] | undefined>): string {
  const raw = headers[HEADER_DEVICE_ID];
  if (Array.isArray(raw)) return raw[0] ?? '';
  return raw ?? '';
}

export function readBearerToken(headers: Record<string, string | string[] | undefined>): string {
  const raw = headers[HEADER_AGENT_TOKEN];
  const value = Array.isArray(raw) ? (raw[0] ?? '') : (raw ?? '');
  // "Bearer <token>" or the bare token; both are accepted so the agent's
  // urllib client and a curl one-liner both work.
  const match = /^Bearer\s+(.+)$/i.exec(value);
  return match ? match[1] : value;
}
