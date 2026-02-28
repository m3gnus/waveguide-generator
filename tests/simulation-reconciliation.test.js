import test from 'node:test';
import assert from 'node:assert/strict';

import {
  mergeJobs,
  hasActiveJobs,
  persistPanelJobs,
  allJobs,
  JOB_TRACKER_CONSTANTS
} from '../src/ui/simulation/jobTracker.js';

function installLocalStorageMock() {
  const store = new Map();
  global.localStorage = {
    getItem(key) {
      return store.has(key) ? store.get(key) : null;
    },
    setItem(key, value) {
      store.set(key, String(value));
    },
    removeItem(key) {
      store.delete(key);
    }
  };
  return store;
}

test('mergeJobs prefers backend status updates for same job id', () => {
  const merged = mergeJobs(
    [{ id: 'job-1', status: 'running', progress: 0.2 }],
    [{ id: 'job-1', status: 'complete', progress: 1 }]
  );

  assert.equal(merged.length, 1);
  assert.equal(merged[0].status, 'complete');
  assert.equal(merged[0].progress, 1);
});

test('hasActiveJobs checks queued/running states', () => {
  const panel = {
    jobs: new Map([
      ['a', { id: 'a', status: 'error' }],
      ['b', { id: 'b', status: 'queued' }]
    ])
  };

  assert.equal(hasActiveJobs(panel), true);
  panel.jobs.set('b', { id: 'b', status: 'complete' });
  assert.equal(hasActiveJobs(panel), false);
});

test('persistPanelJobs writes bounded local index payload', () => {
  const store = installLocalStorageMock();
  const panel = {
    jobs: new Map()
  };

  for (let i = 0; i < 60; i += 1) {
    panel.jobs.set(`job-${i}`, {
      id: `job-${i}`,
      status: 'complete',
      progress: 1,
      createdAt: new Date(2026, 1, 1, 0, i, 0).toISOString()
    });
  }

  persistPanelJobs(panel);
  const saved = JSON.parse(store.get(JOB_TRACKER_CONSTANTS.STORAGE_KEY));
  assert.equal(saved.version, 1);
  assert.equal(saved.items.length, JOB_TRACKER_CONSTANTS.MAX_LOCAL_ITEMS);
  assert.equal(allJobs(panel).length, 60);
});

test('mergeJobs preserves manifest metadata when backend omits fields', () => {
  const merged = mergeJobs(
    [
      {
        id: 'job-1',
        status: 'queued',
        rating: 4,
        exportedFiles: ['first.csv'],
        scriptSchemaVersion: 2,
        scriptSnapshot: { outputName: 'horn' }
      }
    ],
    [{ id: 'job-1', status: 'running', progress: 0.4 }]
  );

  assert.equal(merged.length, 1);
  assert.equal(merged[0].status, 'running');
  assert.equal(merged[0].rating, 4);
  assert.deepEqual(merged[0].exportedFiles, ['first.csv']);
  assert.equal(merged[0].scriptSchemaVersion, 2);
  assert.deepEqual(merged[0].scriptSnapshot, { outputName: 'horn' });
});
