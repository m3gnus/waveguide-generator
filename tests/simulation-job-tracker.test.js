import test from 'node:test';
import assert from 'node:assert/strict';

import {
  clearFailedJobs,
  createJobTracker,
  JOB_TRACKER_CONSTANTS,
  loadLocalIndex,
  mergeJobs,
  removeJob,
  setJobsFromEntries,
  upsertJob
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

test('createJobTracker creates expected baseline state', () => {
  const tracker = createJobTracker();
  assert.ok(tracker.jobs instanceof Map);
  assert.ok(tracker.resultCache instanceof Map);
  assert.equal(tracker.activeJobId, null);
  assert.equal(tracker.pollDelayMs, 1000);
});

test('loadLocalIndex reads valid storage payload', () => {
  const store = installLocalStorageMock();
  store.set(
    JOB_TRACKER_CONSTANTS.STORAGE_KEY,
    JSON.stringify({
      version: 1,
      saved_at: '2026-02-22T18:20:31.305018',
      items: [{ id: 'job-1', status: 'queued', progress: 0 }]
    })
  );

  const items = loadLocalIndex();
  assert.equal(items.length, 1);
  assert.equal(items[0].id, 'job-1');
});

test('setJobsFromEntries and upsertJob manage active selection', () => {
  const panel = {
    jobs: new Map(),
    activeJobId: null
  };

  setJobsFromEntries(panel, [
    { id: 'job-complete', status: 'complete' },
    { id: 'job-running', status: 'running' }
  ]);

  assert.equal(panel.activeJobId, 'job-running');

  upsertJob(panel, { id: 'job-queued', status: 'queued' });
  assert.equal(panel.jobs.get('job-queued').status, 'queued');
});

test('mergeJobs keeps backend as source of truth and marks missing active local jobs as error', () => {
  const merged = mergeJobs(
    [
      { id: 'local-running', status: 'running' },
      { id: 'local-complete', status: 'complete' }
    ],
    [
      { id: 'remote-running', status: 'running' },
      { id: 'local-complete', status: 'error' }
    ]
  );

  const byId = new Map(merged.map((item) => [item.id, item]));
  assert.equal(byId.get('local-complete').status, 'error');
  assert.equal(byId.get('remote-running').status, 'running');
  assert.equal(byId.get('local-running').status, 'error');
  assert.match(byId.get('local-running').errorMessage, /lost/i);
});

test('mergeJobs preserves local label and script metadata when backend omits them', () => {
  const merged = mergeJobs(
    [
      {
        id: 'job-1',
        status: 'queued',
        label: 'horn_design_1_91c1',
        script: { outputName: 'horn_design', counter: 1 }
      }
    ],
    [{ id: 'job-1', status: 'running', progress: 0.4, label: null, script: null }]
  );

  assert.equal(merged.length, 1);
  assert.equal(merged[0].status, 'running');
  assert.equal(merged[0].label, 'horn_design_1_91c1');
  assert.deepEqual(merged[0].script, { outputName: 'horn_design', counter: 1 });
});

test('removeJob removes job and result cache entry', () => {
  const panel = {
    jobs: new Map([['job-1', { id: 'job-1', status: 'complete' }]]),
    resultCache: new Map([['job-1', { ok: true }]]),
    activeJobId: 'job-1',
    currentJobId: 'job-1'
  };

  const removed = removeJob(panel, 'job-1');
  assert.equal(removed, true);
  assert.equal(panel.jobs.has('job-1'), false);
  assert.equal(panel.resultCache.has('job-1'), false);
  assert.equal(panel.activeJobId, null);
  assert.equal(panel.currentJobId, null);
});

test('clearFailedJobs removes only error jobs', () => {
  const panel = {
    jobs: new Map([
      ['job-1', { id: 'job-1', status: 'error' }],
      ['job-2', { id: 'job-2', status: 'cancelled' }],
      ['job-3', { id: 'job-3', status: 'complete' }]
    ]),
    resultCache: new Map()
  };

  const removed = clearFailedJobs(panel);
  assert.equal(removed, 1);
  assert.equal(panel.jobs.has('job-1'), false);
  assert.equal(panel.jobs.has('job-2'), true);
  assert.equal(panel.jobs.has('job-3'), true);
});

test('upsertJob preserves existing label and script when incoming payload omits them', () => {
  const panel = {
    jobs: new Map([
      ['job-1', { id: 'job-1', status: 'queued', label: 'horn_design_1_91c1', script: { outputName: 'horn_design', counter: 1 } }]
    ]),
    activeJobId: null
  };

  const next = upsertJob(panel, { id: 'job-1', status: 'running', label: null, script: null });
  assert.equal(next.label, 'horn_design_1_91c1');
  assert.deepEqual(next.script, { outputName: 'horn_design', counter: 1 });
});
