export const TASK_MANIFEST_FILE_NAME = 'task.manifest.json';
export const TASK_MANIFEST_VERSION = 1;
export const TASK_SCRIPT_SCHEMA_VERSION = 1;

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
  return value
    .map((item) => String(item || '').trim())
    .filter(Boolean);
}

export function normalizeTaskManifest(raw = {}, fallback = {}) {
  const id = String(raw.id || fallback.id || '').trim();
  const createdAt = raw.createdAt ?? raw.created_at ?? fallback.createdAt ?? null;
  const queuedAt = raw.queuedAt ?? raw.queued_at ?? fallback.queuedAt ?? null;
  const startedAt = raw.startedAt ?? raw.started_at ?? fallback.startedAt ?? null;
  const completedAt = raw.completedAt ?? raw.completed_at ?? fallback.completedAt ?? null;
  const status = String(raw.status || fallback.status || 'queued');

  const scriptSnapshot = raw.scriptSnapshot
    ?? raw.script_snapshot
    ?? raw.script
    ?? fallback.scriptSnapshot
    ?? fallback.script
    ?? null;

  return {
    version: TASK_MANIFEST_VERSION,
    id,
    label: raw.label ?? fallback.label ?? id,
    status,
    progress: Number.isFinite(Number(raw.progress ?? fallback.progress))
      ? Math.max(0, Math.min(1, Number(raw.progress ?? fallback.progress)))
      : 0,
    createdAt,
    queuedAt,
    startedAt,
    completedAt,
    rating: raw.rating ?? fallback.rating ?? null,
    exportedFiles: normalizeExportedFiles(raw.exportedFiles ?? raw.exported_files ?? fallback.exportedFiles),
    scriptSchemaVersion: Number.isFinite(Number(raw.scriptSchemaVersion ?? raw.script_schema_version ?? fallback.scriptSchemaVersion))
      ? Number(raw.scriptSchemaVersion ?? raw.script_schema_version ?? fallback.scriptSchemaVersion)
      : TASK_SCRIPT_SCHEMA_VERSION,
    scriptSnapshot,
    updatedAt: raw.updatedAt ?? raw.updated_at ?? nowIso()
  };
}

export function createTaskManifestFromJob(job = {}) {
  return normalizeTaskManifest({
    id: job.id,
    label: job.label,
    status: job.status,
    progress: job.progress,
    createdAt: job.createdAt,
    queuedAt: job.queuedAt,
    startedAt: job.startedAt,
    completedAt: job.completedAt,
    rating: job.rating,
    exportedFiles: job.exportedFiles,
    scriptSchemaVersion: job.scriptSchemaVersion,
    scriptSnapshot: job.scriptSnapshot ?? job.script
  });
}

export async function readTaskManifest(taskDirectoryHandle) {
  if (!taskDirectoryHandle || typeof taskDirectoryHandle.getFileHandle !== 'function') {
    return { manifest: null, warning: 'Task directory handle unavailable.' };
  }

  try {
    const fileHandle = await taskDirectoryHandle.getFileHandle(TASK_MANIFEST_FILE_NAME);
    const file = await fileHandle.getFile();
    const text = await file.text();
    const parsed = safeJsonParse(text);
    if (!parsed || typeof parsed !== 'object') {
      return { manifest: null, warning: 'Task manifest JSON is invalid.' };
    }
    return { manifest: normalizeTaskManifest(parsed), warning: null };
  } catch (error) {
    if (error?.name === 'NotFoundError') {
      return { manifest: null, warning: null };
    }
    return { manifest: null, warning: `Task manifest read failed: ${error?.message || 'unknown error'}` };
  }
}

export async function writeTaskManifest(taskDirectoryHandle, manifestInput) {
  const manifest = normalizeTaskManifest(manifestInput);
  const fileHandle = await taskDirectoryHandle.getFileHandle(TASK_MANIFEST_FILE_NAME, { create: true });
  const writable = await fileHandle.createWritable();
  await writable.write(`${JSON.stringify(manifest, null, 2)}\n`);
  await writable.close();
  return manifest;
}

export async function updateTaskManifestForJob(rootDirectoryHandle, job, updates = {}) {
  if (!rootDirectoryHandle || typeof rootDirectoryHandle.getDirectoryHandle !== 'function') {
    return { manifest: null, warning: 'Workspace folder is unavailable.' };
  }

  const jobId = String(job?.id || '').trim();
  if (!jobId) {
    return { manifest: null, warning: 'Job id missing for task manifest update.' };
  }

  try {
    const taskDir = await rootDirectoryHandle.getDirectoryHandle(jobId, { create: true });
    const existing = await readTaskManifest(taskDir);
    const base = createTaskManifestFromJob(job);
    const next = normalizeTaskManifest({
      ...(existing.manifest || {}),
      ...base,
      ...updates,
      exportedFiles: updates.exportedFiles ?? base.exportedFiles ?? existing.manifest?.exportedFiles,
      updatedAt: nowIso()
    }, { id: jobId, label: job?.label });

    return { manifest: await writeTaskManifest(taskDir, next), warning: existing.warning };
  } catch (error) {
    return { manifest: null, warning: `Task manifest update failed: ${error?.message || 'unknown error'}` };
  }
}
