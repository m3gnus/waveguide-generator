import { writeWorkspaceFile } from '../workspace/folderWorkspace.js';
import { resolveGenerationRuntimeArtifactFileName } from '../workspace/generationArtifacts.js';
import {
  resolveTaskWorkspaceDirectoryName,
  updateTaskManifestForJob,
} from '../workspace/taskManifest.js';

function isObject(value) {
  return value !== null && typeof value === 'object';
}

function resolveGenerationFileBaseName(job, fallbackId) {
  const label = String(job?.label || '').trim();
  if (label) {
    return label;
  }
  const script = job?.scriptSnapshot ?? job?.script ?? job?.script_snapshot ?? null;
  const outputName = String(script?.outputName || '').trim();
  const counter = Number(script?.counter);
  if (outputName && Number.isFinite(counter) && counter >= 1) {
    return `${outputName}_${Math.floor(counter)}`;
  }
  return String(fallbackId || '').trim() || 'generation';
}

export async function syncSimulationWorkspaceJobManifest(job, updates = null) {
  if (!job?.id) {
    return null;
  }

  const nextUpdates = isObject(updates) ? updates : {};

  const jobId = String(job.id).trim();
  const subDirName = resolveTaskWorkspaceDirectoryName(job, { fallbackId: jobId });

  const fallbackWriteFile = async (fileName, content, contentType) => {
    await writeWorkspaceFile(fileName, content, {
      contentType,
      workspaceSubdir: subDirName,
    });
  };

  const result = await updateTaskManifestForJob(job, nextUpdates, { fallbackWriteFile });
  if (result.warning) {
    console.warn(result.warning);
  }
  return result.manifest;
}

async function writeGenerationArtifact({
  fileName,
  content,
  contentType,
  workspaceSubdir,
  warningLabel,
}) {
  try {
    await writeWorkspaceFile(fileName, content, {
      contentType,
      workspaceSubdir,
    });
    return { fileName, warning: null };
  } catch (error) {
    return {
      fileName: null,
      warning: `${warningLabel} write failed: ${error?.message || 'unknown error'}`,
    };
  }
}

export async function persistSimulationGenerationArtifacts(
  job,
  { results = null, meshArtifactText = null } = {}
) {
  const jobId = String(job?.id || '').trim();
  if (!jobId) {
    return {
      rawResultsFile: null,
      meshArtifactFile: null,
      warnings: ['Cannot persist generation artifacts without a job id.'],
    };
  }

  const subDirName = resolveTaskWorkspaceDirectoryName(job, { fallbackId: jobId });
  const fileBaseName = resolveGenerationFileBaseName(job, jobId);
  const warnings = [];
  let rawResultsFile = null;
  let meshArtifactFile = null;

  if (results && typeof results === 'object') {
    const result = await writeGenerationArtifact({
      fileName: resolveGenerationRuntimeArtifactFileName('raw_results', { baseName: fileBaseName }),
      content: `${JSON.stringify(results, null, 2)}\n`,
      contentType: 'application/json',
      workspaceSubdir: subDirName,
      warningLabel: 'Raw results artifact',
    });
    rawResultsFile = result.fileName;
    if (result.warning) {
      warnings.push(result.warning);
    }
  }

  const normalizedMeshText = typeof meshArtifactText === 'string' ? meshArtifactText.trim() : '';
  if (normalizedMeshText) {
    const result = await writeGenerationArtifact({
      fileName: resolveGenerationRuntimeArtifactFileName('mesh_artifact', {
        baseName: fileBaseName,
      }),
      content: `${normalizedMeshText}\n`,
      contentType: 'text/plain',
      workspaceSubdir: subDirName,
      warningLabel: 'Mesh artifact',
    });
    meshArtifactFile = result.fileName;
    if (result.warning) {
      warnings.push(result.warning);
    }
  }

  return {
    rawResultsFile,
    meshArtifactFile,
    warnings,
  };
}
