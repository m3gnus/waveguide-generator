import { AppEvents } from '../../events.js';

const DEFAULT_FOLDER_LABEL = 'No folder selected';
const BACKEND_URL = 'http://localhost:8000';

let selectedFolderHandle = null;
let selectedFolderLabel = DEFAULT_FOLDER_LABEL;

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
 * Ask the backend to open a native OS folder picker dialog and set the
 * selected folder as the workspace path.  Works on any browser (including
 * Firefox) because the native dialog runs in the backend process.
 *
 * Returns the selected path string, or null if the user cancelled.
 */
export async function requestBackendFolderSelection() {
  try {
    const res = await fetch(`${BACKEND_URL}/api/workspace/select`, {
      method: 'POST',
      signal: AbortSignal.timeout(130000)
    });
    if (!res.ok) return null;
    const data = await res.json();
    if (data.selected && data.path) {
      // No folder handle available — clear it but update the label so
      // the UI shows the selected path.  All writes go through the
      // backend /api/export-file endpoint.
      selectedFolderHandle = null;
      selectedFolderLabel = data.path.split('/').pop() || data.path;
      emitChange();
      return data.path;
    }
    return null;
  } catch {
    return null;
  }
}

function normalizeWorkspaceSubdir(subdir) {
  const raw = String(subdir ?? '').trim();
  if (!raw) return '';

  const normalized = raw.replace(/\\/g, '/').split('/').map((part) => part.trim()).filter(Boolean);
  if (normalized.some((part) => part === '.' || part === '..')) {
    throw new Error('Invalid workspace subdirectory');
  }
  return normalized.join('/');
}

/**
 * Write a file into the backend-managed workspace root.
 * `workspaceSubdir` is always interpreted relative to the backend workspace root.
 */
export async function writeWorkspaceFile(fileName, content, options = {}) {
  const formData = new FormData();
  const blob = content instanceof Blob
    ? content
    : new Blob([content], { type: options.contentType || 'text/plain' });
  formData.append('file', blob, fileName);

  const workspaceSubdir = normalizeWorkspaceSubdir(options.workspaceSubdir);
  if (workspaceSubdir) {
    formData.append('workspace_subdir', workspaceSubdir);
  }

  const response = await fetch(`${BACKEND_URL}/api/export-file`, {
    method: 'POST',
    body: formData,
    signal: AbortSignal.timeout(options.timeoutMs || 30000)
  });

  if (!response.ok) {
    const errorPayload = await response.json().catch(() => ({}));
    const detailValue = errorPayload?.detail;
    const detail = typeof detailValue === 'string'
      ? detailValue
      : detailValue
        ? JSON.stringify(detailValue)
        : (response.statusText || 'Unknown error');
    const error = new Error(detail);
    error.statusCode = response.status;
    throw error;
  }

  return response.json();
}

/**
 * Show a proper panel dialog for browsers that do not support showDirectoryPicker
 * (e.g. Firefox). Provides a "Choose Folder" button that opens a native OS folder
 * picker via the backend, plus an "Open in Finder" button and current path display.
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
      'Select a folder to save exported files. The folder picker runs via the ' +
      'backend server so it works in any browser.';
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

    // Choose Folder button (native OS picker via backend)
    const chooseBtn = document.createElement('button');
    chooseBtn.type = 'button';
    chooseBtn.className = 'ui-choice-btn';

    const chooseBtnLabel = document.createElement('span');
    chooseBtnLabel.className = 'ui-choice-btn-label';
    chooseBtnLabel.textContent = 'Choose Folder';
    chooseBtn.appendChild(chooseBtnLabel);

    const chooseBtnHelp = document.createElement('span');
    chooseBtnHelp.className = 'ui-choice-btn-help';
    chooseBtnHelp.textContent = 'Opens a native folder picker dialog.';
    chooseBtn.appendChild(chooseBtnHelp);

    chooseBtn.addEventListener('click', async () => {
      chooseBtn.disabled = true;
      chooseBtnHelp.textContent = 'Waiting for folder selection…';
      const selectedPath = await requestBackendFolderSelection();
      chooseBtn.disabled = false;
      if (selectedPath) {
        pathBox.textContent = selectedPath;
        chooseBtnHelp.textContent = 'Folder selected.';
      } else {
        chooseBtnHelp.textContent = 'No folder selected. Try again or close.';
      }
    });
    actions.appendChild(chooseBtn);

    // Open in Finder button
    const openBtn = document.createElement('button');
    openBtn.type = 'button';
    openBtn.className = 'ui-choice-btn secondary';
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
