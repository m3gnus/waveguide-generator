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

export function buildScriptSnapshotExportState(scriptSnapshot) {
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

async function writeScriptSnapshotArtifact(manifest, { fallbackWriteFile = null } = {}) {
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
    if (typeof fallbackWriteFile === 'function') {
      await fallbackWriteFile(fileName, content, 'text/plain');
    } else {
      return { fileName: null, warning: 'Script snapshot write skipped: no write target available.' };
    }
    return { fileName, warning: null };
  } catch (error) {
    return {
      fileName: null,
      warning: `Script snapshot write failed: ${error?.message || 'unknown error'}`
    };
  }
}

async function writeGenerationProjectManifest({
  directoryName,
  manifest,
  scriptSnapshotFileName,
  fallbackWriteFile = null
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
    const content = `${JSON.stringify(payload, null, 2)}\n`;
    if (typeof fallbackWriteFile === 'function') {
      await fallbackWriteFile(GENERATION_PROJECT_MANIFEST_FILE_NAME, content, 'application/json');
    } else {
      return 'Project manifest write skipped: no write target available.';
    }
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

export async function readTaskManifest() {
  return { manifest: null, warning: null };
}

export async function writeTaskManifest(taskDirectoryHandle, manifestInput) {
  return normalizeTaskManifest(manifestInput);
}

export async function updateTaskManifestForJob(rootDirectoryHandle, job, updates = {}, { fallbackWriteFile = null } = {}) {
  if (typeof fallbackWriteFile !== 'function') {
    return { manifest: null, warning: 'Workspace folder is unavailable.' };
  }

  const jobId = String(job?.id || '').trim();
  if (!jobId) {
    return { manifest: null, warning: 'Job id missing for task manifest update.' };
  }

  try {
    const preferredDirectoryName = resolveTaskWorkspaceDirectoryName(job, { fallbackId: jobId });

    const base = createTaskManifestFromJob(job);
    const hasJobExportedFiles = Array.isArray(job?.exportedFiles);
    const next = normalizeTaskManifest({
      ...base,
      ...updates,
      autoExportCompletedAt: updates.autoExportCompletedAt
        ?? base.autoExportCompletedAt,
      exportedFiles: updates.exportedFiles
        ?? (hasJobExportedFiles ? base.exportedFiles : base.exportedFiles),
      scriptSnapshot: updates.scriptSnapshot
        ?? base.scriptSnapshot
        ?? null,
      rawResultsFile: updates.rawResultsFile
        ?? base.rawResultsFile
        ?? null,
      meshArtifactFile: updates.meshArtifactFile
        ?? base.meshArtifactFile
        ?? null,
      updatedAt: nowIso()
    }, { id: jobId, label: job?.label });

    const manifest = next;
    const manifestContent = `${JSON.stringify(manifest, null, 2)}\n`;
    await fallbackWriteFile(TASK_MANIFEST_FILE_NAME, manifestContent, 'application/json');

    const snapshotResult = await writeScriptSnapshotArtifact(manifest, { fallbackWriteFile });
    const projectWarning = await writeGenerationProjectManifest({
      directoryName: preferredDirectoryName,
      manifest,
      scriptSnapshotFileName: snapshotResult.fileName,
      fallbackWriteFile
    });

    return {
      manifest,
      warning: combineWarnings(snapshotResult.warning, projectWarning)
    };
  } catch (error) {
    return { manifest: null, warning: `Task manifest update failed: ${error?.message || 'unknown error'}` };
  }
}
