import { createTaskManifestFromJob, normalizeTaskManifest, readTaskManifest } from './taskManifest.js';

export const TASK_INDEX_FILE_NAME = '.waveguide-tasks.index.v1.json';
export const TASK_INDEX_VERSION = 1;

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

function normalizeExportedFiles(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item || '').trim()).filter(Boolean);
}

function resolveEntryTimestamp(entry) {
  return Date.parse(
    entry?.updatedAt
    || entry?.completedAt
    || entry?.startedAt
    || entry?.createdAt
    || entry?.queuedAt
    || ''
  ) || 0;
}

function prefersHumanReadableLabel(entry) {
  const label = String(entry?.label || '').trim();
  const id = String(entry?.id || '').trim();
  return Boolean(label && id && label !== id);
}

function dedupeTaskIndexItems(items = []) {
  const byId = new Map();

  for (const item of items) {
    const id = String(item?.id || '').trim();
    if (!id) continue;

    const existing = byId.get(id);
    if (!existing) {
      byId.set(id, item);
      continue;
    }

    const currentTs = resolveEntryTimestamp(item);
    const existingTs = resolveEntryTimestamp(existing);
    if (currentTs > existingTs) {
      byId.set(id, item);
      continue;
    }

    if (currentTs === existingTs && prefersHumanReadableLabel(item) && !prefersHumanReadableLabel(existing)) {
      byId.set(id, item);
    }
  }

  return Array.from(byId.values());
}

export function normalizeTaskIndexEntry(raw = {}) {
  const fromManifest = normalizeTaskManifest(raw, { id: raw.id, label: raw.label });
  return {
    id: String(fromManifest.id || '').trim(),
    label: fromManifest.label ?? null,
    status: String(fromManifest.status || 'error'),
    progress: Number.isFinite(Number(fromManifest.progress))
      ? Math.max(0, Math.min(1, Number(fromManifest.progress)))
      : 0,
    createdAt: fromManifest.createdAt ?? null,
    queuedAt: fromManifest.queuedAt ?? null,
    startedAt: fromManifest.startedAt ?? null,
    completedAt: fromManifest.completedAt ?? null,
    autoExportCompletedAt: fromManifest.autoExportCompletedAt ?? null,
    rating: fromManifest.rating ?? null,
    exportedFiles: normalizeExportedFiles(fromManifest.exportedFiles),
    rawResultsFile: fromManifest.rawResultsFile ?? null,
    meshArtifactFile: fromManifest.meshArtifactFile ?? null,
    updatedAt: fromManifest.updatedAt ?? null,
    scriptSchemaVersion: Number.isFinite(Number(fromManifest.scriptSchemaVersion))
      ? Number(fromManifest.scriptSchemaVersion)
      : 1,
    scriptSnapshot: fromManifest.scriptSnapshot ?? null,
    script: fromManifest.scriptSnapshot ?? null
  };
}

function normalizeTaskIndexPayload(raw = {}) {
  const normalizedItems = Array.isArray(raw.items)
    ? raw.items.map((item) => normalizeTaskIndexEntry(item)).filter((item) => item.id)
    : [];
  const items = dedupeTaskIndexItems(normalizedItems);

  return {
    version: TASK_INDEX_VERSION,
    savedAt: raw.savedAt ?? raw.saved_at ?? nowIso(),
    items
  };
}

export async function loadTaskIndex(rootDirectoryHandle) {
  if (!rootDirectoryHandle || typeof rootDirectoryHandle.getFileHandle !== 'function') {
    return { items: [], warning: 'Workspace folder handle unavailable.', exists: false };
  }

  try {
    const fileHandle = await rootDirectoryHandle.getFileHandle(TASK_INDEX_FILE_NAME);
    const file = await fileHandle.getFile();
    const text = await file.text();
    const parsed = safeJsonParse(text);
    if (!parsed || typeof parsed !== 'object') {
      return { items: [], warning: 'Task index JSON is invalid.', exists: true };
    }
    const normalized = normalizeTaskIndexPayload(parsed);
    return { items: normalized.items, warning: null, exists: true };
  } catch (error) {
    if (error?.name === 'NotFoundError') {
      return { items: [], warning: 'Task index file missing.', exists: false };
    }
    return {
      items: [],
      warning: `Task index read failed: ${error?.message || 'unknown error'}`,
      exists: false
    };
  }
}

export async function writeTaskIndex(rootDirectoryHandle, items = []) {
  const payload = normalizeTaskIndexPayload({
    version: TASK_INDEX_VERSION,
    savedAt: nowIso(),
    items
  });

  const fileHandle = await rootDirectoryHandle.getFileHandle(TASK_INDEX_FILE_NAME, { create: true });
  const writable = await fileHandle.createWritable();
  await writable.write(`${JSON.stringify(payload, null, 2)}\n`);
  await writable.close();
  return payload;
}

export function buildTaskIndexEntriesFromJobs(jobEntries = []) {
  return jobEntries
    .map((entry) => normalizeTaskIndexEntry(createTaskManifestFromJob(entry)))
    .filter((item) => item.id);
}

async function readManifestFromTaskDirectory(taskDirHandle) {
  const { manifest, warning } = await readTaskManifest(taskDirHandle);
  if (!manifest) {
    return { item: null, warning };
  }

  const normalized = normalizeTaskIndexEntry(manifest);
  if (!normalized.id) {
    return { item: null, warning: warning || 'Task manifest missing id.' };
  }
  return { item: normalized, warning };
}

export async function rebuildIndexFromManifests(rootDirectoryHandle) {
  if (!rootDirectoryHandle || typeof rootDirectoryHandle.entries !== 'function') {
    return {
      items: [],
      repaired: false,
      warnings: ['Workspace folder does not support directory enumeration.']
    };
  }

  const items = [];
  const warnings = [];

  for await (const [, entryHandle] of rootDirectoryHandle.entries()) {
    if (!entryHandle || entryHandle.kind !== 'directory') {
      continue;
    }

    const { item, warning } = await readManifestFromTaskDirectory(entryHandle);
    if (warning) {
      warnings.push(warning);
    }
    if (item) {
      items.push(item);
    }
  }

  const dedupedItems = dedupeTaskIndexItems(items);
  dedupedItems.sort((a, b) => {
    const left = Date.parse(a.createdAt || a.queuedAt || '') || 0;
    const right = Date.parse(b.createdAt || b.queuedAt || '') || 0;
    return right - left;
  });

  return {
    items: dedupedItems,
    repaired: true,
    warnings
  };
}
