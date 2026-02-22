const STORAGE_KEY = 'ath_simulation_jobs:v1';
const STORAGE_VERSION = 1;
const MAX_LOCAL_ITEMS = 50;

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
    configSummary: raw.configSummary ?? raw.config_summary ?? {},
    hasResults: Boolean(raw.hasResults ?? raw.has_results),
    hasMeshArtifact: Boolean(raw.hasMeshArtifact ?? raw.has_mesh_artifact),
    label: raw.label ?? null,
    errorMessage: raw.errorMessage ?? raw.error_message ?? null,
    script: raw.script ?? null
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
    config_summary: item.configSummary,
    has_results: item.hasResults,
    has_mesh_artifact: item.hasMeshArtifact,
    label: item.label,
    error_message: item.errorMessage,
    script: item.script
  };
}

function sortByCreatedDesc(items) {
  return [...items].sort((a, b) => {
    const left = Date.parse(a.createdAt || a.queuedAt || '') || 0;
    const right = Date.parse(b.createdAt || b.queuedAt || '') || 0;
    return right - left;
  });
}

function prune(items) {
  return sortByCreatedDesc(items).slice(0, MAX_LOCAL_ITEMS);
}

export function createJobTracker() {
  return {
    jobs: new Map(),
    resultCache: new Map(),
    activeJobId: null,
    pollTimer: null,
    pollDelayMs: 1000,
    pollBackoffMs: 1000,
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
        script: normalized.script ?? existing.script ?? null
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
    }
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
    script: next.script ?? existing?.script ?? null
  });
  if (!panel.activeJobId && ACTIVE.has(next.status)) {
    panel.activeJobId = next.id;
  }
  return panel.jobs.get(next.id);
}

export function allJobs(panel) {
  return sortByCreatedDesc(Array.from(panel.jobs.values()));
}

export function persistPanelJobs(panel) {
  saveLocalIndex(allJobs(panel));
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
