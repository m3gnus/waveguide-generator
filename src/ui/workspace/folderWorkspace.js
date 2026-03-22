import { AppEvents } from '../../events.js';
import { DEFAULT_BACKEND_URL } from '../../config/backendUrl.js';

const DEFAULT_FOLDER_LABEL = 'No folder selected';

let selectedFolderLabel = DEFAULT_FOLDER_LABEL;

const changeListeners = new Set();
const warningListeners = new Set();

function emitChange() {
  const snapshot = {
    handle: null,
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

export function supportsFolderSelection() {
  return false;
}

export function getSelectedFolderHandle() {
  return null;
}

export function getSelectedFolderLabel() {
  return selectedFolderLabel;
}

export function setSelectedFolderHandle(handle, options = {}) {
  selectedFolderLabel = String(options.label || DEFAULT_FOLDER_LABEL);
  emitChange();
  return null;
}

export function resetSelectedFolder(options = {}) {
  selectedFolderLabel = String(options.label || DEFAULT_FOLDER_LABEL);
  emitChange();
}

export function subscribeFolderWorkspace(listener) {
  if (typeof listener !== 'function') {
    return () => {};
  }
  changeListeners.add(listener);
  listener({ handle: null, label: selectedFolderLabel });
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

export async function ensureFolderWritePermission() {
  return false;
}

export async function requestFolderSelection() {
  return requestBackendFolderSelection();
}

export async function fetchWorkspacePath() {
  try {
    const res = await fetch(`${DEFAULT_BACKEND_URL}/api/workspace/path`, {
      signal: AbortSignal.timeout(5000)
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data.path || null;
  } catch {
    return null;
  }
}

export async function openWorkspaceInFinder({ job } = {}) {
  try {
    const body = {};
    if (job) {
      const { resolveTaskWorkspaceDirectoryName } = await import('./taskManifest.js');
      const subdir = resolveTaskWorkspaceDirectoryName(job, { fallbackId: job.id });
      if (subdir) {
        body.subdir = subdir;
      }
    }
    const res = await fetch(`${DEFAULT_BACKEND_URL}/api/workspace/open`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(8000)
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function requestBackendFolderSelection() {
  try {
    const res = await fetch(`${DEFAULT_BACKEND_URL}/api/workspace/select`, {
      method: 'POST',
      signal: AbortSignal.timeout(130000)
    });
    if (!res.ok) return null;
    const data = await res.json();
    if (data.selected && data.path) {
      selectedFolderLabel = data.path.split('/').pop() || data.path;
      emitChange();
      return data.path;
    }
    return null;
  } catch {
    return null;
  }
}

export async function selectOutputFolder() {
  return requestBackendFolderSelection();
}

export function normalizeWorkspaceSubdir(subdir) {
  const raw = String(subdir ?? '').trim();
  if (!raw) return '';

  const normalized = raw.replace(/\\/g, '/').split('/').map((part) => part.trim()).filter(Boolean);
  if (normalized.some((part) => part === '.' || part === '..')) {
    throw new Error('Invalid workspace subdirectory');
  }
  return normalized.join('/');
}

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

  const response = await fetch(`${DEFAULT_BACKEND_URL}/api/export-file`, {
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
