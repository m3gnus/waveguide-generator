// @ts-check

import { createSimulationClient } from '../../modules/simulation/useCases.js';
import {
  readSimulationWorkspaceJobs
} from '../../modules/simulation/useCases.js';
import {
  createJobTracker,
  loadLocalIndex,
  mergeJobs,
  setJobsFromEntries,
  persistPanelJobs
} from './jobTracker.js';

const ACTIVE_STATUSES = new Set(['queued', 'running']);

const DEFAULT_SIMULATION_PARAM_BINDINGS = Object.freeze([
  { id: 'freq-start', key: 'freqStart', parse: (value) => parseFloat(value) },
  { id: 'freq-end', key: 'freqEnd', parse: (value) => parseFloat(value) },
  { id: 'freq-steps', key: 'numFreqs', parse: (value) => parseInt(value, 10) }
]);

export const SIMULATION_CONTROLLER_FIELDS = Object.freeze([
  'solver',
  'currentJobId',
  'pollInterval',
  'connectionPollTimer',
  'lastResults',
  'jobs',
  'resultCache',
  'activeJobId',
  'pollTimer',
  'pollDelayMs',
  'pollBackoffMs',
  'consecutivePollFailures',
  'isPolling',
  'stageStatusActive',
  'completedStatusMessage',
  'simulationStartedAtMs',
  'lastSimulationDurationMs',
  'currentSmoothing',
  'simulationParamBindings'
]);

function hasActiveJobs(controller) {
  return Array.from(controller.jobs.values()).some((job) => ACTIVE_STATUSES.has(job.status));
}

function syncCurrentJobId(controller) {
  controller.currentJobId = controller.activeJobId || null;
}

function cloneSimulationParamBindings() {
  return DEFAULT_SIMULATION_PARAM_BINDINGS.map((entry) => ({ ...entry }));
}

export function createSimulationControllerStore({ solver = createSimulationClient() } = {}) {
  return {
    solver,
    currentJobId: null,
    pollInterval: null,
    connectionPollTimer: null,
    lastResults: null,
    jobs: new Map(),
    resultCache: new Map(),
    activeJobId: null,
    pollTimer: null,
    pollDelayMs: 1000,
    pollBackoffMs: 1000,
    consecutivePollFailures: 0,
    isPolling: false,
    stageStatusActive: false,
    completedStatusMessage: null,
    simulationStartedAtMs: null,
    lastSimulationDurationMs: null,
    currentSmoothing: 'none',
    simulationParamBindings: cloneSimulationParamBindings()
  };
}

export function bindSimulationControllerState(panelAdapter, controller) {
  for (const key of SIMULATION_CONTROLLER_FIELDS) {
    Object.defineProperty(panelAdapter, key, {
      configurable: true,
      enumerable: true,
      get() {
        return controller[key];
      },
      set(nextValue) {
        controller[key] = nextValue;
      }
    });
  }
}

export async function restoreSimulationControllerJobs(
  controller,
  {
    onJobsUpdated = () => {},
    onStartPolling = () => {},
    onRecoverFromManifests = () => {}
  } = {}
) {
  const tracker = createJobTracker();
  controller.jobs = tracker.jobs;
  controller.resultCache = tracker.resultCache;
  controller.activeJobId = tracker.activeJobId;
  controller.pollTimer = tracker.pollTimer;
  controller.pollDelayMs = tracker.pollDelayMs;
  controller.pollBackoffMs = tracker.pollBackoffMs;
  controller.consecutivePollFailures = Number(tracker.consecutivePollFailures) || 0;
  controller.isPolling = tracker.isPolling;
  syncCurrentJobId(controller);

  const local = loadLocalIndex();
  let seedItems = local;
  const workspace = await readSimulationWorkspaceJobs();

  if (workspace.items.length > 0) {
    seedItems = mergeJobs(local, workspace.items);
  }
  if (workspace.repaired || workspace.warnings.length > 0) {
    onRecoverFromManifests();
  }

  setJobsFromEntries(controller, seedItems);
  syncCurrentJobId(controller);
  onJobsUpdated();

  try {
    const remote = await controller.solver.listJobs({ limit: 200, offset: 0 });
    const merged = mergeJobs(seedItems, remote.items || []);
    setJobsFromEntries(controller, merged);
    syncCurrentJobId(controller);
    persistPanelJobs(controller);

    onJobsUpdated();
    if (controller.activeJobId || hasActiveJobs(controller)) {
      onStartPolling();
    }
  } catch (_error) {
    persistPanelJobs(controller);
    onJobsUpdated();
  }
}
