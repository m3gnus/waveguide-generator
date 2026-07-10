import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, symlink, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

import {
  createDevServer,
  isAllowedStaticPath,
  resolveDevServerHost,
} from '../scripts/dev-server.js';

test('dev server defaults to loopback and permits explicit host override', () => {
  assert.equal(resolveDevServerHost({}), '127.0.0.1');
  assert.equal(resolveDevServerHost({ HOST: '0.0.0.0' }), '0.0.0.0');
});

test('dev server static allowlist excludes repository and runtime internals', () => {
  assert.equal(isAllowedStaticPath('index.html'), true);
  assert.equal(isAllowedStaticPath('src/main.js'), true);
  assert.equal(isAllowedStaticPath('node_modules/three/build/three.module.js'), true);
  assert.equal(isAllowedStaticPath('src/../server/data/simulations.db'), false);
  assert.equal(isAllowedStaticPath('node_modules/three/build/../../../../.git/config'), false);
  assert.equal(isAllowedStaticPath('.git/config'), false);
  assert.equal(isAllowedStaticPath('.waveguide/backend-python.path'), false);
  assert.equal(isAllowedStaticPath('server/data/simulations.db'), false);
  assert.equal(isAllowedStaticPath('output/private.json'), false);
});

test('dev server serves UI assets but returns 404 for sensitive paths', async (t) => {
  const server = createDevServer();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });

  const address = server.address();
  const baseUrl = `http://127.0.0.1:${address.port}`;
  for (const requestPath of ['/', '/src/main.js', '/node_modules/three/build/three.module.js']) {
    const response = await fetch(`${baseUrl}${requestPath}`);
    assert.equal(response.status, 200, requestPath);
  }
  for (const requestPath of [
    '/.git/config',
    '/.waveguide/backend-python.path',
    '/server/data/simulations.db',
    '/output/private.json',
  ]) {
    const response = await fetch(`${baseUrl}${requestPath}`);
    assert.equal(response.status, 404, requestPath);
  }
});

test('dev server rejects symlinks from allowed paths to files outside its root', async (t) => {
  const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'waveguide-dev-server-'));
  const publicRoot = path.join(tempRoot, 'public');
  const outsideFile = path.join(tempRoot, 'secret.txt');
  await mkdir(path.join(publicRoot, 'src'), { recursive: true });
  await writeFile(path.join(publicRoot, 'index.html'), '<!doctype html>');
  await writeFile(outsideFile, 'secret');
  await symlink(outsideFile, path.join(publicRoot, 'src', 'linked-secret.txt'));

  const server = createDevServer({ rootDir: publicRoot });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  t.after(async () => {
    server.closeAllConnections();
    server.close();
    await rm(tempRoot, { recursive: true, force: true });
  });

  const address = server.address();
  const response = await fetch(`http://127.0.0.1:${address.port}/src/linked-secret.txt`);
  assert.equal(response.status, 404);
});
