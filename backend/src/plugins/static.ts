/**
 * Serves the mobile UI from the repo root, so the backend is the only thing
 * that has to run: the phone points at `http://<host>:3000/` and gets the
 * dashboard. Static files are read-only by definition; there is no upload and
 * no write route anywhere in the backend.
 */
import type { FastifyPluginCallback } from 'fastify';
import fastifyStatic from '@fastify/static';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export const staticPlugin: FastifyPluginCallback = (app, _opts, done) => {
  const root = path.resolve(__dirname, '../../..');
  app.register(fastifyStatic, {
    root,
    // Root contains node_modules and agent sources. Letting fastify-static
    // crawl it delays startup until the server cannot bind; expose only UI
    // assets explicitly below.
    serve: false,
    cacheControl: false,
    serveDotFiles: false,
  });
  app.get('/', (_request, reply) => reply.sendFile('index.html'));
  app.get('/index.html', (_request, reply) => reply.sendFile('index.html'));
  app.get('/app.js', (_request, reply) => reply.sendFile('app.js'));
  app.get('/vigilia.js', (_request, reply) => reply.sendFile('vigilia.js'));
  app.get('/styles.css', (_request, reply) => reply.sendFile('styles.css'));
  done();
};
