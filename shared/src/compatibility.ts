import { MIN_SUPPORTED_PROTOCOL_VERSION, PROTOCOL_VERSION } from './version.js';

export type ProtocolCompatibility = { compatible: true; protocolVersion: number; reason: null } | { compatible: false; protocolVersion: number | null; reason: 'MISSING_PROTOCOL_VERSION' | 'INVALID_PROTOCOL_VERSION' | 'UNSUPPORTED_PROTOCOL_VERSION' };

export function protocolCompatibility(value: unknown): ProtocolCompatibility {
  if (value === undefined || value === null) return { compatible: false, protocolVersion: null, reason: 'MISSING_PROTOCOL_VERSION' };
  if (!Number.isInteger(value) || typeof value !== 'number') return { compatible: false, protocolVersion: null, reason: 'INVALID_PROTOCOL_VERSION' };
  if (value < MIN_SUPPORTED_PROTOCOL_VERSION || value > PROTOCOL_VERSION) return { compatible: false, protocolVersion: value, reason: 'UNSUPPORTED_PROTOCOL_VERSION' };
  return { compatible: true, protocolVersion: value, reason: null };
}
