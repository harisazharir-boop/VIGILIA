/**
 * /api/devices — the device list and a single device.
 *
 * Public and read-only: the mobile dashboard carries no token, and these
 * routes cannot change anything. A device's tokens are never included in a
 * response — the agent token lives only in the agent, the command token only
 * on the paired phone.
 */
import type { FastifyPluginCallback } from 'fastify';
import { prisma } from '../../config/db.js';
import { sendError } from '../../utils/errors.js';

export const deviceRoutes: FastifyPluginCallback = (app, _opts, done) => {
  app.get('/devices', async (_request, reply) => {
    try {
      const devices = await prisma.device.findMany({
        orderBy: { lastSeen: 'desc' },
        // Never select the tokens: they must not reach the browser.
        select: {
          id: true,
          name: true,
          os: true,
          hostname: true,
          status: true,
          lastSeen: true,
          createdAt: true,
        },
      });
      return reply.send({
        devices: devices.map((d) => ({
          ...d,
          lastSeen: d.lastSeen.toISOString(),
          createdAt: d.createdAt.toISOString(),
        })),
      });
    } catch (err) {
      return sendError(reply, err);
    }
  });

  app.get('/devices/:id', async (request, reply) => {
    try {
      const { id } = request.params as { id: string };
      const device = await prisma.device.findUnique({
        where: { id },
        select: {
          id: true,
          name: true,
          os: true,
          hostname: true,
          status: true,
          lastSeen: true,
          createdAt: true,
        },
      });
      if (!device) return reply.status(404).send({ error: { code: 'NOT_FOUND', message: 'no such device' } });
      return reply.send({
        ...device,
        lastSeen: device.lastSeen.toISOString(),
        createdAt: device.createdAt.toISOString(),
      });
    } catch (err) {
      return sendError(reply, err);
    }
  });

  done();
};
