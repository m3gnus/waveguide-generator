import test from 'node:test';
import assert from 'node:assert/strict';

import { resolveServerUrls } from '../scripts/server-urls.js';

test('start-all URLs use the local defaults', () => {
  assert.deepEqual(resolveServerUrls({}), {
    frontend: 'http://localhost:3000',
    backend: 'http://localhost:8000',
  });
});

test('start-all URLs honor frontend and backend host and port overrides', () => {
  assert.deepEqual(
    resolveServerUrls({
      HOST: '0.0.0.0',
      PORT: '4000',
      MWG_BACKEND_HOST: '192.0.2.10',
      MWG_BACKEND_PORT: '8123',
    }),
    {
      frontend: 'http://0.0.0.0:4000',
      backend: 'http://192.0.2.10:8123',
    }
  );
});

test('start-all URL ports fall back when an override is invalid', () => {
  assert.deepEqual(resolveServerUrls({ PORT: 'not-a-port', MWG_BACKEND_PORT: '70000' }), {
    frontend: 'http://localhost:3000',
    backend: 'http://localhost:8000',
  });
});
