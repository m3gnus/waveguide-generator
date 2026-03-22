import {
  writeWorkspaceFile
} from '../workspace/folderWorkspace.js';
import { resolveGenerationRuntimeArtifactFileName } from '../workspace/generationArtifacts.js';
import {
  buildTaskIndexEntriesFromJobs,
  loadTaskIndex,
  rebuildIndexFromManifests,
  writeTaskIndex
} from '../workspace/taskIndex.js';
import {
  resolveTaskWorkspaceDirectoryName,
  updateTaskManifestForJob
} from '../workspace/taskManifest.js';

function isObject(value) {
  return value !== null && typeof value === 'object';
}

function normalizeWarningList(...values) {
  return values.flat().map((value) => String(value || '').trim()).filter(Boolean);
}

function buildArtifactWriteInput(fileName, content, contentType) {
  return {
    fileName,
    content,
    saveOptions: {
      contentType
    }
  };
}

export async function readSimulationWorkspaceJobs() {
  return {
    items: [],
    available: false,
    repaired: false,
    warnings: []
  };
}

export function syncSimulationWorkspaceIndex(jobEntries = []) {
  return Promise.resolve({
    synced: false,
    available: false,
    items: []
  });
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
      workspaceSubdir: subDirName
    });
  };

  const result = await updateTaskManifestForJob(
    null,
    job,
    nextUpdates,
    { fallbackWriteFile }
  );
  if (result.warning) {
    console.warn(result.warning);
  }
  return result.manifest;
}

export async function deleteTaskWorkspaceDirectory(job) {
  return false;
}

export async function writeSimulationTaskBundleFile(job, file, { fallbackWrite = null, dirName = null, subDir = null } = {}) {
  if (typeof fallbackWrite === 'function') {
    await fallbackWrite(file);
  }

  return {
    fileName: file.fileName,
    wroteToTaskFolder: false
  };
}

export async function persistSimulationGenerationArtifacts(
  job,
  {
    results = null,
    meshArtifactText = null
  } = {}
) {
  const jobId = String(job?.id || '').trim();
  if (!jobId) {
    return {
      rawResultsFile: null,
      meshArtifactFile: null,
      warnings: ['Cannot persist generation artifacts without a job id.']
    };
  }

  const subDirName = resolveTaskWorkspaceDirectoryName(job, { fallbackId: jobId });
  const warnings = [];
  let rawResultsFile = null;
  let meshArtifactFile = null;

  if (results && typeof results === 'object') {
    rawResultsFile = resolveGenerationRuntimeArtifactFileName('raw_results', { baseName: subDirName });
    const rawResultsContent = `${JSON.stringify(results, null, 2)}\n`;
    try {
      await writeWorkspaceFile(rawResultsFile, rawResultsContent, {
        contentType: 'application/json',
        workspaceSubdir: subDirName
      });
    } catch (error) {
      rawResultsFile = null;
      warnings.push(`Raw results artifact write failed: ${error?.message || 'unknown error'}`);
    }
  }

  const normalizedMeshText = typeof meshArtifactText === 'string'
    ? meshArtifactText.trim()
    : '';
  if (normalizedMeshText) {
    meshArtifactFile = resolveGenerationRuntimeArtifactFileName('mesh_artifact', { baseName: subDirName });
    try {
      await writeWorkspaceFile(meshArtifactFile, `${normalizedMeshText}\n`, {
        contentType: 'text/plain',
        workspaceSubdir: subDirName
      });
    } catch (error) {
      meshArtifactFile = null;
      warnings.push(`Mesh artifact write failed: ${error?.message || 'unknown error'}`);
    }
  }

  return {
    rawResultsFile,
    meshArtifactFile,
    warnings
  };
}
