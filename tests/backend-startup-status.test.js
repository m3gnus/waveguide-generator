import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createBackendOutputCapture,
  summarizeBackendFailure,
  writeBackendStartupStatus,
} from '../scripts/backend-startup-status.js';
import {
  describeBackendStartupStatus,
  fetchBackendStartupStatus,
} from '../src/modules/runtime/backendStartup.js';

test('backend output capture keeps the useful tail across partial chunks', () => {
  const capture = createBackendOutputCapture();
  capture.append('Traceback line\nModuleNot');
  capture.append("FoundError: No module named 'fastapi'\n");
  assert.match(capture.details(), /Traceback line/);
  assert.match(capture.details(), /ModuleNotFoundError: No module named 'fastapi'/);
});

test('backend startup diagnostics never block startup when the status file is unwritable', () => {
  const fsImpl = {
    mkdirSync() {
      throw new Error('read only');
    },
  };
  assert.equal(writeBackendStartupStatus('/repo', { state: 'starting' }, { fsImpl }), false);
});

test('backend failure summary gives actionable missing-module guidance', () => {
  assert.deepEqual(
    summarizeBackendFailure({
      exitCode: 1,
      details: "ModuleNotFoundError: No module named 'fastapi'",
    }),
    {
      reason: 'The backend Python environment is missing the fastapi module.',
      guidance: 'Re-run the installer to repair the backend dependencies.',
    }
  );
});

test('backend failure summary identifies an occupied backend port', () => {
  const result = summarizeBackendFailure({ details: 'OSError: [WinError 10048] address in use' });
  assert.match(result.reason, /port 8000 is already in use/i);
});

test('startup status formatter replaces the ambiguous offline message', () => {
  assert.deepEqual(
    describeBackendStartupStatus({
      state: 'error',
      reason: 'The configured Python interpreter could not be started.',
      guidance: 'Re-run the installer.',
    }),
    {
      label: 'Backend failed to start',
      help: 'The configured Python interpreter could not be started. Re-run the installer.',
    }
  );
});

test('startup status fetch uses the frontend-local diagnostic endpoint', async () => {
  let request = null;
  const result = await fetchBackendStartupStatus({
    async fetchImpl(url, options) {
      request = { url, options };
      return {
        ok: true,
        async json() {
          return { state: 'starting', reason: 'Starting.' };
        },
      };
    },
  });

  assert.deepEqual(request, {
    url: '/api/backend-startup-status',
    options: { cache: 'no-store' },
  });
  assert.deepEqual(result, { state: 'starting', reason: 'Starting.' });
});
