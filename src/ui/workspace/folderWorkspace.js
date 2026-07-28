import { AppEvents } from '../../events.js';
import { DEFAULT_BACKEND_URL } from '../../config/backendUrl.js';
import { debugWarn } from '../../logging/debug.js';

const DEFAULT_FOLDER_LABEL = 'No folder selected';

let selectedFolderPath = null;

const changeListeners = new Set();

function labelFromPath(path) {
  const normalized = String(path || '')
    .trim()
    .replace(/\\/g, '/')
    .replace(/\/+$/, '');
  if (!normalized) return DEFAULT_FOLDER_LABEL;
  return normalized.split('/').pop() || normalized;
}

function updateSelectedWorkspacePath(path) {
  const normalizedPath = String(path || '').trim() || null;
  if (selectedFolderPath === normalizedPath) {
    return;
  }
  selectedFolderPath = normalizedPath;
  emitChange();
}

function emitChange() {
  const snapshot = {
    label: getSelectedFolderLabel(),
    path: selectedFolderPath,
  };
  AppEvents.emit('ui:folder-workspace-changed', snapshot);
  for (const listener of changeListeners) {
    try {
      listener(snapshot);
    } catch (error) {
      debugWarn('folderWorkspace change listener failed:', error);
    }
  }
}

export function getSelectedFolderLabel() {
  return labelFromPath(selectedFolderPath);
}

export function getSelectedFolderPath() {
  return selectedFolderPath;
}

export function subscribeFolderWorkspace(listener) {
  if (typeof listener !== 'function') {
    return () => {};
  }
  changeListeners.add(listener);
  listener({ label: getSelectedFolderLabel(), path: selectedFolderPath });
  return () => {
    changeListeners.delete(listener);
  };
}

export async function fetchWorkspacePath() {
  try {
    const res = await fetch(`${DEFAULT_BACKEND_URL}/api/workspace/path`, {
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return null;
    const data = await res.json();
    if (data.path) {
      updateSelectedWorkspacePath(data.path);
    }
    return data.path || null;
  } catch {
    return null;
  }
}

export async function openWorkspaceInFinder() {
  try {
    const res = await fetch(`${DEFAULT_BACKEND_URL}/api/workspace/open`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
      signal: AbortSignal.timeout(8000),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function selectOutputFolder() {
  try {
    const res = await fetch(`${DEFAULT_BACKEND_URL}/api/workspace/select`, {
      method: 'POST',
      signal: AbortSignal.timeout(130000),
    });
    if (!res.ok) return null;
    const data = await res.json();
    if (data.selected && data.path) {
      updateSelectedWorkspacePath(data.path);
      return data.path;
    }
    return null;
  } catch {
    return null;
  }
}

export function normalizeWorkspaceSubdir(subdir) {
  const raw = String(subdir ?? '').trim();
  if (!raw) return '';

  const normalized = raw
    .replace(/\\/g, '/')
    .split('/')
    .map((part) => part.trim())
    .filter(Boolean);
  if (normalized.some((part) => part === '.' || part === '..')) {
    throw new Error('Invalid workspace subdirectory');
  }
  return normalized.join('/');
}

async function readWorkspaceErrorDetail(response) {
  const jsonPayload = await response.json().catch(() => null);
  const detailValue = jsonPayload?.detail;
  if (typeof detailValue === 'string' && detailValue.trim()) {
    return detailValue.trim();
  }
  if (detailValue) {
    return JSON.stringify(detailValue);
  }

  const textPayload = await response.text?.().catch(() => '');
  const text = String(textPayload || '').trim();
  if (text) {
    return text;
  }

  return response.statusText || 'Unknown error';
}

export async function writeWorkspaceFile(fileName, content, options = {}) {
  const formData = new FormData();
  const blob =
    content instanceof Blob
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
    signal: AbortSignal.timeout(options.timeoutMs || 30000),
  });

  if (!response.ok) {
    const detail = await readWorkspaceErrorDetail(response);
    const error = new Error(detail);
    error.statusCode = response.status;
    throw error;
  }

  const result = await response.json();
  if (result?.workspaceRoot) {
    updateSelectedWorkspacePath(result.workspaceRoot);
  }
  return result;
}
