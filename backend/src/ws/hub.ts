/**
 * The WebSocket hub. It tracks sockets by device so a frame can be broadcast
 * to every client watching a machine, and it is the only place a socket is
 * registered or removed.
 *
 * Read-only mobile sockets and authenticated agent sockets both live here;
 * the difference is established at handshake time in `ws/routes.ts` and this
 * module never re-checks it. A socket that never authenticated is not in the
 * map at all.
 */
import type { WebSocket } from 'ws';
import { logger } from '../config/logger.js';

export interface OutboundFrame {
  type: string;
  deviceId?: string;
  data?: unknown;
  timestamp?: string;
}

type SocketId = string;

interface TrackedSocket {
  socket: WebSocket;
  deviceId: string;
  readOnly: boolean;
}

export class WsHub {
  private sockets = new Map<SocketId, TrackedSocket>();
  private deviceSockets = new Map<string, Set<SocketId>>();

  register(socketId: SocketId, socket: WebSocket, deviceId: string, readOnly: boolean): void {
    this.remove(socketId);
    this.sockets.set(socketId, { socket, deviceId, readOnly });
    const ids = this.deviceSockets.get(deviceId) ?? new Set<SocketId>();
    ids.add(socketId);
    this.deviceSockets.set(deviceId, ids);
  }

  remove(socketId: SocketId): void {
    const tracked = this.sockets.get(socketId);
    if (!tracked) return;
    this.sockets.delete(socketId);
    const ids = this.deviceSockets.get(tracked.deviceId);
    ids?.delete(socketId);
    if (ids?.size === 0) this.deviceSockets.delete(tracked.deviceId);
  }

  /** Send to one socket. A closed socket is dropped silently. */
  send(socket: WebSocket, frame: OutboundFrame): void {
    if (socket.readyState !== socket.OPEN) return;
    try {
      socket.send(JSON.stringify({ ...frame, timestamp: frame.timestamp ?? new Date().toISOString() }));
    } catch (err) {
      logger.warn({ err }, 'failed to send a ws frame');
    }
  }

  /**
   * Broadcast to every socket subscribed to `deviceId`. Used for telemetry,
   * the application catalog, storage, the cleanup plan and command results:
   * anything the agent pushes and the phone wants live.
   */
  broadcastToDevice(deviceId: string, type: string, data: unknown): void {
    for (const socketId of this.deviceSockets.get(deviceId) ?? []) {
      const tracked = this.sockets.get(socketId);
      if (tracked) this.send(tracked.socket, { type, deviceId, data });
    }
  }

  /** Remote media is binary and reaches subscribed read-only clients only. */
  broadcastBinaryToReadOnly(deviceId: string, payload: Buffer): void {
    for (const socketId of this.deviceSockets.get(deviceId) ?? []) {
      const tracked = this.sockets.get(socketId);
      if (!tracked || !tracked.readOnly || tracked.socket.readyState !== tracked.socket.OPEN) continue;
      try { tracked.socket.send(payload, { binary: true }); } catch (err) { logger.warn({ err }, 'failed to send remote media'); }
    }
  }

  /** Commands and filesystem capabilities must never reach read-only sockets. */
  sendToAgent(deviceId: string, type: string, data: unknown): void {
    for (const socketId of this.deviceSockets.get(deviceId) ?? []) {
      const tracked = this.sockets.get(socketId);
      if (tracked && !tracked.readOnly) this.send(tracked.socket, { type, deviceId, data });
    }
  }

  size(): number {
    return this.sockets.size;
  }
}

export const wsHub = new WsHub();
