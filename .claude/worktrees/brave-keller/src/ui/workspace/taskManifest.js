import { buildMwgConfigExportFiles } from '../../modules/export/useCases.js';
import {
  GENERATION_PROJECT_MANIFEST_FILE_NAME,
  buildGenerationProjectManifest,
  resolveGenerationScriptSnapshotFileName
} from './generationArtifacts.js';

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

function isObject(value) {
  return value !== null && typeof value === 'object';
}

function normalizeExportedFiles(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => String(item || '').trim())
    .filter(Boolean);
}

function normalizeDirectoryName(value) {
  const text = String(value ?? '').trim();
  return text || null;
}

function normalizeArtifactFileName(value) {
  const text = String(value ?? '').trim();
  return text || null;
}

function combineWarnings(...values) {
  const warnings = values.map((value) => String(value || '').trim()).filter(Boolean);
  return warnings.length > 0 ? warnings.join(' | ') : null;
}

function deriveGenerationFolderNameFromScript(scriptSnapshot) {
  if (!scriptSnapshot || typeof scriptSnapshot !== 'object') {
    return null;
  }

  const outputName = normalizeDirectoryName(scriptSnapshot.outputName);
  if (!outputName) {
    return null;
  }

  const counter = Number(scriptSnapshot.counter);
  if (Number.isFinite(counter) && counter >= 1) {
    return `${outputName}_${Math.floor(counter)}`;
  }
  return outputName;
}

function buildScriptSnapshotExportState(scriptSnapshot) {
  if (!isObject(scriptSnapshot)) {
    return null;
  }

  const params = isObject(scriptSnapshot.params)
    ? { ...scriptSnapshot.params }
    : isObject(scriptSnapshot.stateSnapshot?.params)
      ? { ...scriptSnapshot.stateSnapshot.params }
      : null;
  if (!isObject(params)) {
    return null;
  }

  const type = params.type ?? scriptSnapshot.stateSnapshot?.type;
  if (!type) return null;

  if (scriptSnapshot.frequencyStart !== undefined) params.freqStart = scriptSnapshot.frequencyStart;
  if (scriptSnapshot.frequencyEnd !== undefined) params.freqEnd = scriptSnapshot.frequencyEnd;
  if (scriptSnapshot.numFrequencies !== undefined) params.numFreqs = scriptSnapshot.numFrequencies;

  return {
    type: String(type),
    params
  };
}

async function writeWorkspaceTextFile(taskDirectoryHandle, fileName, content) {
  const fileHandle = await taskDirectoryHandle.getFileHandle(fileName, { create: true });
  const writable = await fileHandle.createWritable();
  await writable.write(content);
  await writable.close();
}

async function writeScriptSnapshotArtifact(taskDirectoryHandle, manifest) {
  const exportState = buildScriptSnapshotExportState(manifest?.scriptSnapshot);
  if (!exportState) {
    return { fileName: null, warning: null };
  }

  try {
    const fileName = resolveGenerationScriptSnapshotFileName();
    const files = buildMwgConfigExportFiles(exportState, { baseName: 'script.snapshot' });
    const content = typeof files?.[0]?.content === 'string'
      ? files[0].content
      : null;
    if (!content) {
      return { fileName: null, warning: 'Script snapshot build failed: config content missing.' };
    }
    await writeWorkspaceTextFile(taskDirectoryHandle, fileName, content);
    return { fileName, warning: null };
  } catch (error) {
    return {
      fileName: null,
      warning: `Script snapshot write failed: ${error?.message || 'unknown error'}`
    };
  }
}

async function writeGenerationProjectManifest(taskDirectoryHandle, {
  directoryName,
  manifest,
  scriptSnapshotFileName
} = {}) {
  try {
    const payload = buildGenerationProjectManifest({
      directoryName,
      job: manifest,
      exportedFiles: manifest?.exportedFiles || [],
      scriptSnapshotFileName,
      rawResultsFileName: manifest?.rawResultsFile ?? null,
      meshArtifactFileName: manifest?.meshArtifactFile ?? null,
      updatedAt: manifest?.updatedAt
    });
    await writeWorkspaceTextFile(
      taskDirectoryHandle,
      GENERATION_PROJECT_MANIFEST_FILE_NAME,
      `${JSON.stringify(payload, null, 2)}\n`
    );
    return null;
  } catch (error) {
    return `Project manifest write failed: ${error?.message || 'unknown error'}`;
  }
}

export function resolveTaskWorkspaceDirectoryName(job = {}, { fallbackId = null } = {}) {
  const label = normalizeDirectoryName(job?.label);
  if (label) {
    return label;
  }

  const scriptName = deriveGenerationFolderNameFromScript(job?.scriptSnapshot ?? job?.script);
  if (scriptName) {
    return scriptName;
  }

  return normalizeDirectoryName(fallbackId ?? job?.id) || '';
}

export function normalizeTaskManifest(raw = {}, fallback = {}) {
  const id = String(raw.id || fallback.id || '').trim();
  const createdAt = raw.createdAt ?? raw.created_at ?? fallback.createdAt ?? null;
  const queuedAt = raw.queuedAt ?? raw.queued_at ?? fallback.queuedAt ?? null;
  const startedAt = raw.startedAt ?? raw.started_at ?? fallback.startedAt ?? null;
  const completedAt = raw.completedAt ?? raw.completed_at ?? fallback.completedAt ?? null;
  const autoExportCompletedAt = raw.autoExportCompletedAt
    ?? raw.auto_export_completed_at
    ?? fallback.autoExportCompletedAt
    ?? null;
  const status = String(raw.status || fallback.status || 'queued');

  const scriptSnapshot = raw.scriptSnapshot
    ?? raw.script_snapshot
    ?? raw.script
    ?? fallback.scriptSnapshot
    ?? fallback.script
    ?? null;
  const rawResultsFile = normalizeArtifactFileName(
    raw.rawResultsFile
    ?? raw.raw_results_file
    ?? fallback.rawResultsFile
  );
  const meshArtifactFile = normalizeArtifactFileName(
    raw.meshArtifactFile
    ?? raw.mesh_artifact_file
    ?? fallback.meshArtifactFile
  );

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
    autoExportCompletedAt,
    rating: raw.rating ?? fallback.rating ?? null,
    exportedFiles: normalizeExportedFiles(raw.exportedFiles ?? raw.exported_files ?? fallback.exportedFiles),
    scriptSchemaVersion: Number.isFinite(Number(raw.scriptSchemaVersion ?? raw.script_schema_version ?? fallback.scriptSchemaVersion))
      ? Number(raw.scriptSchemaVersion ?? raw.script_schema_version ?? fallback.scriptSchemaVersion)
      : TASK_SCRIPT_SCHEMA_VERSION,
    scriptSnapshot,
    rawResultsFile,
    meshArtifactFile,
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
    autoExportCompletedAt: job.autoExportCompletedAt,
    rating: job.rating,
    exportedFiles: job.exportedFiles,
    scriptSchemaVersion: job.scriptSchemaVersion,
    scriptSnapshot: job.scriptSnapshot ?? job.script,
    rawResultsFile: job.rawResultsFile,
    meshArtifactFile: job.meshArtifactFile
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

async function getDirectoryHandleIfExists(rootDirectoryHandle, directoryName) {
  if (!directoryName) return null;
  try {
    return await rootDirectoryHandle.getDirectoryHandle(directoryName);
  } catch (error) {
    if (error?.name === 'NotFoundError') {
      return null;
    }
    throw error;
  }
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
    const preferredDirectoryName = resolveTaskWorkspaceDirectoryName(job, { fallbackId: jobId });
    const legacyDirectoryName = jobId;

    let existing = { manifest: null, warning: null };
    const preferredExistingDir = await getDirectoryHandleIfExists(rootDirectoryHandle, preferredDirectoryName);
    if (preferredExistingDir) {
      existing = await readTaskManifest(preferredExistingDir);
    }

    if (!existing.manifest && preferredDirectoryName !== legacyDirectoryName) {
      const legacyDir = await getDirectoryHandleIfExists(rootDirectoryHandle, legacyDirectoryName);
      if (legacyDir) {
        existing = await readTaskManifest(legacyDir);
      }
    }

    const base = createTaskManifestFromJob(job);
    const hasJobExportedFiles = Array.isArray(job?.exportedFiles);
    const next = normalizeTaskManifest({
      ...(existing.manifest || {}),
      ...base,
      ...updates,
      autoExportCompletedAt: updates.autoExportCompletedAt
        ?? base.autoExportCompletedAt
        ?? existing.manifest?.autoExportCompletedAt,
      exportedFiles: updates.exportedFiles
        ?? (hasJobExportedFiles ? base.exportedFiles : existing.manifest?.exportedFiles ?? base.exportedFiles),
      scriptSnapshot: updates.scriptSnapshot
        ?? base.scriptSnapshot
        ?? existing.manifest?.scriptSnapshot
        ?? null,
      rawResultsFile: updates.rawResultsFile
        ?? base.rawResultsFile
        ?? existing.manifest?.rawResultsFile
        ?? null,
      meshArtifactFile: updates.meshArtifactFile
        ?? base.meshArtifactFile
        ?? existing.manifest?.meshArtifactFile
        ?? null,
      updatedAt: nowIso()
    }, { id: jobId, label: job?.label });

    const taskDir = await rootDirectoryHandle.getDirectoryHandle(preferredDirectoryName, { create: true });
    const manifest = await writeTaskManifest(taskDir, next);
    const snapshotResult = await writeScriptSnapshotArtifact(taskDir, manifest);
    const projectWarning = await writeGenerationProjectManifest(taskDir, {
      directoryName: preferredDirectoryName,
      manifest,
      scriptSnapshotFileName: snapshotResult.fileName
    });

    return {
      manifest,
      warning: combineWarnings(existing.warning, snapshotResult.warning, projectWarning)
    };
  } catch (error) {
    return { manifest: null, warning: `Task manifest update failed: ${error?.message || 'unknown error'}` };
  }
}
