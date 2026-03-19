import { getSelectedFolderHandle } from '../workspace/folderWorkspace.js';
import {
  ensureFolderWritePermission,
  resetSelectedFolder
} from '../workspace/folderWorkspace.js';
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

export async function writeSimulationTaskBundleFile(job, file, { fallbackWrite = null, dirName = null } = {}) {
  const folderHandle = getSelectedFolderHandle();
  if (folderHandle && job?.id) {
    try {
      const permissionGranted = await ensureFolderWritePermission(folderHandle);
      if (!permissionGranted) {
        throw new Error('Write permission for selected folder was denied.');
      }

      const subDirName = dirName || resolveTaskWorkspaceDirectoryName(job);
      const taskDir = await folderHandle.getDirectoryHandle(subDirName, { create: true });
      const fileHandle = await taskDir.getFileHandle(file.fileName, { create: true });
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
