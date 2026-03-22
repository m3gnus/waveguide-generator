import { getSelectedFolderHandle } from '../workspace/folderWorkspace.js';
import {
  ensureFolderWritePermission,
  resetSelectedFolder,
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

let pendingSimulationWorkspaceIndexSync = Promise.resolve({
  synced: false,
  available: false,
  items: []
});

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

async function writeGenerationArtifactWithFallback(job, file, subDirName) {
  return writeSimulationTaskBundleFile(job, file, {
    dirName: subDirName,
    fallbackWrite: async (nextFile) => {
      await writeWorkspaceFile(nextFile.fileName, nextFile.content, {
        contentType: nextFile.saveOptions?.contentType,
        workspaceSubdir: subDirName
      });
    }
  });
}

export async function readSimulationWorkspaceJobs() {
  const folderHandle = getSelectedFolderHandle();
  if (!folderHandle) {
    return {
      items: [],
      available: false,
      repaired: false,
      warnings: []
    };
  }

  const indexResult = await loadTaskIndex(folderHandle);
  if (indexResult.items.length > 0) {
    return {
      items: indexResult.items,
      available: true,
      repaired: false,
      warnings: normalizeWarningList(indexResult.warning)
    };
  }

  const rebuilt = await rebuildIndexFromManifests(folderHandle);
  if (rebuilt.items.length > 0) {
    await writeTaskIndex(folderHandle, rebuilt.items);
  }

  return {
    items: rebuilt.items,
    available: true,
    repaired: rebuilt.items.length > 0,
    warnings: normalizeWarningList(indexResult.warning, rebuilt.warnings)
  };
}

export function syncSimulationWorkspaceIndex(jobEntries = []) {
  const folderHandle = getSelectedFolderHandle();
  if (!folderHandle) {
    return Promise.resolve({
      synced: false,
      available: false,
      items: []
    });
  }

  const items = buildTaskIndexEntriesFromJobs(jobEntries);
  pendingSimulationWorkspaceIndexSync = pendingSimulationWorkspaceIndexSync.then(async () => {
    try {
      await writeTaskIndex(folderHandle, items);
      return {
        synced: true,
        available: true,
        items
      };
    } catch (error) {
      console.warn('Simulation workspace index sync failed:', error);
      return {
        synced: false,
        available: true,
        items,
        error
      };
    }
  });

  return pendingSimulationWorkspaceIndexSync;
}

export async function syncSimulationWorkspaceJobManifest(job, updates = null) {
  const folderHandle = getSelectedFolderHandle();
  if (!folderHandle || !job?.id) {
    return null;
  }

  const nextUpdates = isObject(updates) ? updates : {};
  const result = await updateTaskManifestForJob(folderHandle, job, nextUpdates);
  if (result.warning) {
    console.warn(result.warning);
  }
  return result.manifest;
}

export async function writeSimulationTaskBundleFile(job, file, { fallbackWrite = null, dirName = null, subDir = null } = {}) {
  const folderHandle = getSelectedFolderHandle();
  if (folderHandle && job?.id) {
    try {
      const permissionGranted = await ensureFolderWritePermission(folderHandle);
      if (!permissionGranted) {
        throw new Error('Write permission for selected folder was denied.');
      }

      const subDirName = dirName || resolveTaskWorkspaceDirectoryName(job);
      const taskDir = await folderHandle.getDirectoryHandle(subDirName, { create: true });
      const writeDir = subDir
        ? await taskDir.getDirectoryHandle(subDir, { create: true })
        : taskDir;
      const fileHandle = await writeDir.getFileHandle(file.fileName, { create: true });
      const writable = await fileHandle.createWritable();
      const blob = file.content instanceof Blob
        ? file.content
        : new Blob([file.content], { type: file.saveOptions?.contentType || 'application/octet-stream' });
      await writable.write(blob);
      await writable.close();
      return {
        fileName: file.fileName,
        wroteToTaskFolder: true
      };
    } catch (error) {
      console.warn('Task folder export failed, falling back to standard save flow:', error);
      resetSelectedFolder();
    }
  }

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
      await writeGenerationArtifactWithFallback(
        job,
        buildArtifactWriteInput(rawResultsFile, rawResultsContent, 'application/json'),
        subDirName
      );
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
      await writeGenerationArtifactWithFallback(
        job,
        buildArtifactWriteInput(meshArtifactFile, `${normalizedMeshText}\n`, 'text/plain'),
        subDirName
      );
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
