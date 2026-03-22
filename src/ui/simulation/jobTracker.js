import { syncSimulationWorkspaceIndex } from './workspaceTasks.js';

const STORAGE_KEY = 'ath_simulation_jobs:v1';
const STORAGE_VERSION = 1;
const MAX_LOCAL_ITEMS = 50;

// One-time cleanup: remove stale localStorage job data (output folder is now the single source).
try {
  if (typeof localStorage !== 'undefined') {
    localStorage.removeItem(STORAGE_KEY);
  }
} catch { /* ignore */ }

const TERMINAL = new Set(['complete', 'error', 'cancelled']);
const ACTIVE = new Set(['queued', 'running']);

function nowIso() {
  return new Date().toISOString();
}

function safeJsonParse(raw) {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function normalizeItem(raw = {}) {
  const exportedFilesInput = raw.exportedFiles ?? raw.exported_files;
  const exportedFiles = Array.isArray(exportedFilesInput)
    ? exportedFilesInput.map((item) => String(item || '').trim()).filter(Boolean)
    : [];
  const scriptSnapshot = raw.scriptSnapshot ?? raw.script_snapshot ?? raw.script ?? null;
  const scriptSchemaInput = raw.scriptSchemaVersion ?? raw.script_schema_version;

  return {
    id: String(raw.id || ''),
    status: String(raw.status || 'error'),
    progress: Number.isFinite(Number(raw.progress)) ? Math.max(0, Math.min(1, Number(raw.progress))) : 0,
    stage: raw.stage || null,
    stageMessage: raw.stageMessage ?? raw.stage_message ?? null,
    createdAt: raw.createdAt ?? raw.created_at ?? null,
    queuedAt: raw.queuedAt ?? raw.queued_at ?? null,
    startedAt: raw.startedAt ?? raw.started_at ?? null,
    completedAt: raw.completedAt ?? raw.completed_at ?? null,
    autoExportCompletedAt: raw.autoExportCompletedAt ?? raw.auto_export_completed_at ?? null,
    configSummary: raw.configSummary ?? raw.config_summary ?? {},
    hasResults: Boolean(raw.hasResults ?? raw.has_results),
    hasMeshArtifact: Boolean(raw.hasMeshArtifact ?? raw.has_mesh_artifact),
    label: raw.label ?? null,
    errorMessage: raw.errorMessage ?? raw.error_message ?? null,
    cancellationRequested: Boolean(raw.cancellationRequested ?? raw.cancellation_requested),
    meshStats: raw.meshStats ?? raw.mesh_stats ?? null,
    script: raw.script ?? scriptSnapshot,
    rating: raw.rating ?? null,
    exportedFiles,
    justCompleted: Boolean(raw.justCompleted),
    rawResultsFile: raw.rawResultsFile ?? raw.raw_results_file ?? null,
    meshArtifactFile: raw.meshArtifactFile ?? raw.mesh_artifact_file ?? null,
    scriptSchemaVersion: Number.isFinite(Number(scriptSchemaInput))
      ? Number(scriptSchemaInput)
      : null,
    scriptSnapshot
  };
}

function toStorageItem(item) {
  return {
    id: item.id,
    status: item.status,
    progress: item.progress,
    stage: item.stage,
    stage_message: item.stageMessage,
    created_at: item.createdAt,
    queued_at: item.queuedAt,
    started_at: item.startedAt,
    completed_at: item.completedAt,
    auto_export_completed_at: item.autoExportCompletedAt ?? null,
    config_summary: item.configSummary,
    has_results: item.hasResults,
    has_mesh_artifact: item.hasMeshArtifact,
    label: item.label,
    error_message: item.errorMessage,
    cancellation_requested: Boolean(item.cancellationRequested),
    mesh_stats: item.meshStats ?? null,
    script: item.script,
    rating: item.rating ?? null,
    exported_files: Array.isArray(item.exportedFiles) ? item.exportedFiles : [],
    raw_results_file: item.rawResultsFile ?? null,
    mesh_artifact_file: item.meshArtifactFile ?? null,
    script_schema_version: item.scriptSchemaVersion !== null
      && item.scriptSchemaVersion !== undefined
      && Number.isFinite(Number(item.scriptSchemaVersion))
      ? Number(item.scriptSchemaVersion)
      : 1,
    script_snapshot: item.scriptSnapshot ?? item.script ?? null
  };
}

function resolveSortTimestamp(item) {
  return Date.parse(item.completedAt || item.createdAt || item.queuedAt || item.startedAt || '') || 0;
}

export function sortJobs(items, { sortBy = 'completed_desc' } = {}) {
  return [...items]
    .map((item, index) => ({ item, index }))
    .sort((left, right) => {
      if (sortBy === 'rating_desc') {
        const ratingDelta = (right.item.rating ?? 0) - (left.item.rating ?? 0);
        if (ratingDelta !== 0) {
          return ratingDelta;
        }
        const timeDelta = resolveSortTimestamp(right.item) - resolveSortTimestamp(left.item);
        if (timeDelta !== 0) {
          return timeDelta;
        }
      } else if (sortBy === 'label_asc') {
        const labelDelta = String(left.item.label || left.item.id || '').localeCompare(
          String(right.item.label || right.item.id || ''),
          undefined,
          { sensitivity: 'base' }
        );
        if (labelDelta !== 0) {
          return labelDelta;
        }
      } else {
        const timeDelta = resolveSortTimestamp(right.item) - resolveSortTimestamp(left.item);
        if (timeDelta !== 0) {
          return timeDelta;
        }
      }

      return left.index - right.index;
    })
    .map(({ item }) => item);
}

function prune(items) {
  return sortJobs(items, { sortBy: 'completed_desc' }).slice(0, MAX_LOCAL_ITEMS);
}

export function createJobTracker() {
  return {
    jobs: new Map(),
    resultCache: new Map(),
    activeJobId: null,
    pollTimer: null,
    pollDelayMs: 1000,
    pollBackoffMs: 1000,
    consecutivePollFailures: 0,
    isPolling: false
  };
}

export function loadLocalIndex() {
  if (typeof localStorage === 'undefined') {
    return [];
  }
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    return [];
  }
  const parsed = safeJsonParse(raw);
  if (!parsed || parsed.version !== STORAGE_VERSION || !Array.isArray(parsed.items)) {
    return [];
  }
  return parsed.items.map((item) => normalizeItem(item)).filter((item) => item.id);
}

export function saveLocalIndex(jobEntries) {
  if (typeof localStorage === 'undefined') {
    return;
  }
  const payload = {
    version: STORAGE_VERSION,
    saved_at: nowIso(),
    items: prune(jobEntries).map((item) => toStorageItem(item))
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
}

export function mergeJobs(localItems, remoteItems) {
  const merged = new Map();

  for (const item of localItems || []) {
    const normalized = normalizeItem(item);
    if (normalized.id) {
      merged.set(normalized.id, normalized);
    }
  }

  const remoteSeen = new Set();
  for (const item of remoteItems || []) {
    const normalized = normalizeItem(item);
    if (!normalized.id) {
      continue;
    }
    const existing = merged.get(normalized.id);
    if (existing) {
      merged.set(normalized.id, {
        ...normalized,
        label: normalized.label ?? existing.label ?? null,
        script: normalized.script ?? existing.script ?? null,
        meshStats: normalized.meshStats ?? existing.meshStats ?? null,
        rating: normalized.rating ?? existing.rating ?? null,
        autoExportCompletedAt: normalized.autoExportCompletedAt ?? existing.autoExportCompletedAt ?? null,
        exportedFiles: normalized.exportedFiles?.length
          ? normalized.exportedFiles
          : (existing.exportedFiles ?? []),
        justCompleted: normalized.status === 'complete' && existing.status !== 'complete',
        rawResultsFile: normalized.rawResultsFile ?? existing.rawResultsFile ?? null,
        meshArtifactFile: normalized.meshArtifactFile ?? existing.meshArtifactFile ?? null,
        scriptSchemaVersion: normalized.scriptSchemaVersion !== null
          && normalized.scriptSchemaVersion !== undefined
          && Number.isFinite(Number(normalized.scriptSchemaVersion))
          ? Number(normalized.scriptSchemaVersion)
          : (existing.scriptSchemaVersion ?? 1),
        scriptSnapshot: normalized.scriptSnapshot ?? existing.scriptSnapshot ?? null
      });
    } else {
      merged.set(normalized.id, normalized);
    }
    remoteSeen.add(normalized.id);
  }

  for (const [id, item] of merged.entries()) {
    if (remoteSeen.has(id)) {
      continue;
    }
    if (ACTIVE.has(item.status)) {
      merged.set(id, {
        ...item,
        status: 'error',
        stage: 'error',
        stageMessage: 'Job missing from backend after reconnect',
        errorMessage: 'Job state was lost after backend restart or reset.',
        completedAt: item.completedAt || nowIso()
      });
      continue;
    }
    merged.delete(id);
  }

  return prune(Array.from(merged.values()));
}

export function setJobsFromEntries(panel, items) {
  panel.jobs.clear();
  for (const raw of items) {
    const item = normalizeItem(raw);
    if (item.id) {
      panel.jobs.set(item.id, item);
    }
  }

  if (!panel.activeJobId || !panel.jobs.has(panel.activeJobId)) {
    const running = Array.from(panel.jobs.values()).find((job) => ACTIVE.has(job.status));
    panel.activeJobId = running ? running.id : null;
  }
}

export function upsertJob(panel, rawEntry) {
  const next = normalizeItem(rawEntry);
  if (!next.id) {
    return null;
  }
  const existing = panel.jobs.get(next.id);
  panel.jobs.set(next.id, {
    ...(existing || {}),
    ...next,
    label: next.label ?? existing?.label ?? null,
    script: next.script ?? existing?.script ?? null,
    meshStats: next.meshStats ?? existing?.meshStats ?? null,
    rating: next.rating ?? existing?.rating ?? null,
    autoExportCompletedAt: next.autoExportCompletedAt ?? existing?.autoExportCompletedAt ?? null,
    exportedFiles: next.exportedFiles?.length
      ? next.exportedFiles
      : (existing?.exportedFiles ?? []),
    justCompleted: next.justCompleted ?? existing?.justCompleted ?? false,
    rawResultsFile: next.rawResultsFile ?? existing?.rawResultsFile ?? null,
    meshArtifactFile: next.meshArtifactFile ?? existing?.meshArtifactFile ?? null,
    scriptSchemaVersion: next.scriptSchemaVersion !== null
      && next.scriptSchemaVersion !== undefined
      && Number.isFinite(Number(next.scriptSchemaVersion))
      ? Number(next.scriptSchemaVersion)
      : (existing?.scriptSchemaVersion ?? 1),
    scriptSnapshot: next.scriptSnapshot ?? existing?.scriptSnapshot ?? null
  });
  if (!panel.activeJobId && ACTIVE.has(next.status)) {
    panel.activeJobId = next.id;
  }
  return panel.jobs.get(next.id);
}

export function allJobs(panel, options = {}) {
  const minRating = Number.isFinite(Number(options.minRating))
    ? Math.max(0, Math.min(5, Number(options.minRating)))
    : 0;
  const filtered = Array.from(panel.jobs.values()).filter((job) => (job.rating ?? 0) >= minRating);
  return sortJobs(filtered, { sortBy: options.sortBy || 'completed_desc' });
}

export function persistPanelJobs(panel) {
  const jobs = allJobs(panel);
  void syncSimulationWorkspaceIndex(jobs);
}

export function removeJob(panel, jobId) {
  const id = String(jobId || '').trim();
  if (!id) {
    return false;
  }
  const removed = panel.jobs.delete(id);
  if (panel.activeJobId === id) {
    panel.activeJobId = null;
    panel.currentJobId = null;
  }
  if (panel.resultCache?.has(id)) {
    panel.resultCache.delete(id);
  }
  return removed;
}

export function clearFailedJobs(panel) {
  let removed = 0;
  for (const [id, job] of panel.jobs.entries()) {
    if (job.status === 'error') {
      panel.jobs.delete(id);
      panel.resultCache?.delete(id);
      removed += 1;
      if (panel.activeJobId === id) {
        panel.activeJobId = null;
        panel.currentJobId = null;
      }
    }
  }
  return removed;
}

export function hasActiveJobs(panel) {
  return Array.from(panel.jobs.values()).some((job) => ACTIVE.has(job.status));
}

export function isTerminalStatus(status) {
  return TERMINAL.has(String(status || '').trim().toLowerCase());
}

export function toUiJob(entry) {
  return normalizeItem(entry);
}

export const JOB_TRACKER_CONSTANTS = {
  STORAGE_KEY,
  STORAGE_VERSION,
  MAX_LOCAL_ITEMS
};
