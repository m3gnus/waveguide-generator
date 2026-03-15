import { AppEvents } from '../../events.js';

const DEFAULT_FOLDER_LABEL = 'No folder selected';
const STORAGE_KEY = 'mwg_server_folder_path';
const BACKEND_URL = 'http://localhost:8000';

let selectedFolderHandle = null;
let selectedFolderLabel = DEFAULT_FOLDER_LABEL;
let serverFolderPath = null;

const changeListeners = new Set();
const warningListeners = new Set();

function emitChange() {
  const snapshot = {
    handle: selectedFolderHandle,
    label: selectedFolderLabel
  };
  AppEvents.emit('ui:folder-workspace-changed', snapshot);
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

export function setServerFolderPath(path) {
  serverFolderPath = path ? String(path).trim() : null;
  if (serverFolderPath) {
    try {
      localStorage.setItem(STORAGE_KEY, serverFolderPath);
      selectedFolderLabel = `Server: ${serverFolderPath}`;
    } catch (err) {
      console.warn('Failed to save folder path to localStorage:', err);
      selectedFolderLabel = `Server: ${serverFolderPath}`;
    }
  } else {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch (err) {
      console.warn('Failed to clear folder path from localStorage:', err);
    }
    // Only reset the label when no native folder handle is active.
    // When requestFolderSelection sets a handle, its label takes precedence.
    if (!selectedFolderHandle) {
      selectedFolderLabel = DEFAULT_FOLDER_LABEL;
    }
  }
  emitChange();
}

export function getServerFolderPath() {
  return serverFolderPath;
}

export function loadServerFolderPathFromStorage() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      setServerFolderPath(saved);
    }
  } catch (err) {
    console.warn('Failed to load folder path from localStorage:', err);
  }
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

/**
 * Fetch the current output folder path from the backend.
 * Returns the absolute path string or null on failure.
 */
export async function fetchWorkspacePath() {
  try {
    const res = await fetch(`${BACKEND_URL}/api/workspace/path`, {
      signal: AbortSignal.timeout(5000)
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data.path || null;
  } catch {
    return null;
  }
}

/**
 * Ask the backend to open the output folder in the OS file manager.
 * Returns true on success, false on failure.
 */
export async function openWorkspaceInFinder() {
  try {
    const res = await fetch(`${BACKEND_URL}/api/workspace/open`, {
      method: 'POST',
      signal: AbortSignal.timeout(8000)
    });
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * Show a proper panel dialog for browsers that do not support showDirectoryPicker
 * (e.g. Firefox). Displays the current output folder path and an "Open in Finder"
 * button wired to the backend endpoint.
 *
 * Returns a Promise that resolves when the user closes the dialog.
 */
export function showOutputFolderPanel() {
  if (typeof document === 'undefined') return Promise.resolve();

  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'ui-choice-backdrop';

    const dialog = document.createElement('div');
    dialog.className = 'ui-choice-dialog';
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.setAttribute('aria-label', 'Output Folder');

    const title = document.createElement('h4');
    title.className = 'ui-choice-title';
    title.textContent = 'Output Folder';
    dialog.appendChild(title);

    const subtitle = document.createElement('p');
    subtitle.className = 'ui-choice-subtitle';
    subtitle.textContent =
      'Firefox does not support selecting a custom output folder via the browser ' +
      '(the File System Access API is not implemented in Firefox). Files are saved ' +
      'to the default server output folder shown below.';
    dialog.appendChild(subtitle);

    // Path display
    const pathBox = document.createElement('pre');
    pathBox.className = 'ui-command-box';
    pathBox.textContent = 'Loading…';
    dialog.appendChild(pathBox);

    // Actions
    const actions = document.createElement('div');
    actions.className = 'ui-choice-actions';
    dialog.appendChild(actions);

    const finalize = () => {
      window.removeEventListener('keydown', onKeyDown);
      backdrop.remove();
      resolve();
    };

    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        finalize();
      }
    };

    const openBtn = document.createElement('button');
    openBtn.type = 'button';
    openBtn.className = 'ui-choice-btn';
    openBtn.disabled = true;

    const openBtnLabel = document.createElement('span');
    openBtnLabel.className = 'ui-choice-btn-label';
    openBtnLabel.textContent = 'Open in Finder';
    openBtn.appendChild(openBtnLabel);

    const openBtnHelp = document.createElement('span');
    openBtnHelp.className = 'ui-choice-btn-help';
    openBtnHelp.textContent = 'Opens the output folder in the OS file manager.';
    openBtn.appendChild(openBtnHelp);

    openBtn.addEventListener('click', async () => {
      openBtn.disabled = true;
      const ok = await openWorkspaceInFinder();
      openBtn.disabled = false;
      if (!ok) {
        openBtnHelp.textContent = 'Could not open folder — is the backend running?';
      }
    });
    actions.appendChild(openBtn);

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'ui-choice-btn secondary';
    closeBtn.textContent = 'Close';
    closeBtn.addEventListener('click', finalize);
    actions.appendChild(closeBtn);

    backdrop.addEventListener('click', (event) => {
      if (event.target === backdrop) finalize();
    });

    window.addEventListener('keydown', onKeyDown);
    backdrop.appendChild(dialog);
    document.body.appendChild(backdrop);

    // Fetch path asynchronously after dialog is shown
    fetchWorkspacePath().then((path) => {
      if (path) {
        pathBox.textContent = path;
        openBtn.disabled = false;
      } else {
        pathBox.textContent = 'Backend unavailable — path unknown.';
      }
    });
  });
}
