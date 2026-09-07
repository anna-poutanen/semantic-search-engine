import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as os from 'node:os';
import * as http from 'node:http';
import { once } from 'node:events';
import { SearchController } from '../src/controller';
import { defaultOptions } from '../src/schema';
import { response, source, status } from './fixtures';

test('native HTTP→controller→saved evidence covers edit/refresh/reconnect and malformed path response', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'semantic-integration-'));
  await fs.mkdir(path.join(root, 'src')); await fs.writeFile(path.join(root, 'src/app.py'), source);
  let refreshes = 0;
  const server = http.createServer((request, reply) => {
    let body = ''; request.on('data', chunk => body += chunk); request.on('end', () => {
      reply.setHeader('Content-Type', 'application/json');
      if (request.url === '/status') { reply.end(JSON.stringify(status(root))); return; }
      if (request.url === '/index') { refreshes++; reply.end(JSON.stringify({ indexstats: { total_windows: 1 } })); return; }
      const data = JSON.parse(body) as { query: string; request_id: string };
      if (request.url === '/cancel') { reply.end(JSON.stringify({ request_id: data.request_id, cancelled: true })); return; }
      void fs.readFile(path.join(root, 'src/app.py'), 'utf8').then(saved => {
        const found = response(root, data.query, saved);
        if (data.query === 'malformed') { const first = found.results[0]; if (first) { first.path = '../outside.py'; } }
        reply.end(JSON.stringify(found));
      });
    });
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); assert.ok(address && typeof address === 'object');
  const controller = new SearchController(() => true, () => undefined);
  try {
    assert.ok(await controller.connect(root, `http://localhost:${address.port}`));
    await controller.search('initial', defaultOptions);
    const first = controller.state.entries[0]; assert.ok(first);
    assert.ok(await controller.navigation(first, async () => false));
    const edited = source.replace('ready', 'updated'); await fs.writeFile(path.join(root, 'src/app.py'), edited);
    await assert.rejects(controller.navigation(first, async () => false), /changed after/);
    await controller.refresh(); assert.equal(refreshes, 1);
    await controller.search('edited', defaultOptions); assert.equal(controller.state.entries[0]?.result.source, edited);
    await controller.search('malformed', defaultOptions); assert.equal(controller.state.phase, 'error'); assert.equal(controller.state.entries.length, 0);
    controller.disconnect(); assert.equal(controller.repository, null);
    assert.ok(await controller.connect(root, `http://127.0.0.1:${address.port}`));
  } finally { controller.dispose(); server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); await fs.rm(root, { recursive: true }); }
});
