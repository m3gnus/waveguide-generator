const DEFAULT_FOLDER_LABEL = 'No folder selected';

let selectedFolderHandle = null;
let selectedFolderLabel = DEFAULT_FOLDER_LABEL;

const changeListeners = new Set();
const warningListeners = new Set();

function emitChange() {
  const snapshot = {
    handle: selectedFolderHandle,
    label: selectedFolderLabel
  };
  for (const listener of changeListeners) {
    try {
      listener(snapshot);
    } catch (error) {
      console.warn('folderWorkspace change listener failed:', error);
    }
  }
}

function emitWarning(message, context = null) {
  for (const listener of warningListeners) {
    try {
      listener({ message, context });
    } catch (error) {
      console.warn('folderWorkspace warning listener failed:', error);
    }
  }
}

export function supportsFolderSelection(targetWindow = globalThis?.window) {
  return typeof targetWindow?.showDirectoryPicker === 'function';
}

export function getSelectedFolderHandle() {
  return selectedFolderHandle;
}

export function getSelectedFolderLabel() {
  return selectedFolderLabel;
}

export function setSelectedFolderHandle(handle, options = {}) {
  if (!handle) {
    selectedFolderHandle = null;
    selectedFolderLabel = options.label || DEFAULT_FOLDER_LABEL;
    emitChange();
    return null;
  }

  selectedFolderHandle = handle;
  selectedFolderLabel = String(options.label || handle.name || DEFAULT_FOLDER_LABEL);
  emitChange();
  return selectedFolderHandle;
}

export function resetSelectedFolder(options = {}) {
  selectedFolderHandle = null;
  selectedFolderLabel = String(options.label || DEFAULT_FOLDER_LABEL);
  emitChange();
}

export function subscribeFolderWorkspace(listener) {
  if (typeof listener !== 'function') {
    return () => {};
  }
  changeListeners.add(listener);
  listener({ handle: selectedFolderHandle, label: selectedFolderLabel });
  return () => {
    changeListeners.delete(listener);
  };
}

export function subscribeFolderWorkspaceWarnings(listener) {
  if (typeof listener !== 'function') {
    return () => {};
  }
  warningListeners.add(listener);
  return () => {
    warningListeners.delete(listener);
  };
}

export async function ensureFolderWritePermission(handle) {
  if (!handle) {
    return false;
  }

  try {
    if (typeof handle.queryPermission === 'function') {
      const permission = await handle.queryPermission({ mode: 'readwrite' });
      if (permission === 'granted') {
        return true;
      }
      if (permission === 'denied') {
        return false;
      }
    }

    if (typeof handle.requestPermission === 'function') {
      const permission = await handle.requestPermission({ mode: 'readwrite' });
      return permission === 'granted';
    }
  } catch (error) {
    emitWarning('Unable to verify folder write permission.', error);
    return false;
  }

  return true;
}

export async function requestFolderSelection(targetWindow = globalThis?.window) {
  if (!supportsFolderSelection(targetWindow)) {
    emitWarning('Folder selection is not supported in this browser.');
    return null;
  }

  try {
    const handle = await targetWindow.showDirectoryPicker({ mode: 'readwrite' });
    setSelectedFolderHandle(handle);
    return handle;
  } catch (error) {
    if (error?.name === 'AbortError') {
      return null;
    }

    emitWarning('Folder selection failed.', error);
    return null;
  }
}
