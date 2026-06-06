import { showError, showSuccess } from './feedback.js';
import { validateOutputName, validateCounter, sanitizeFileName } from './inputValidation.js';
import { debugWarn } from '../logging/debug.js';
import { subscribeFolderWorkspace, writeWorkspaceFile } from './workspace/folderWorkspace.js';

export { selectOutputFolder } from './workspace/folderWorkspace.js';

let hasPendingParameterChanges = false;
let skipNextParameterChange = false;
const DEFAULT_OUTPUT_NAME = 'horn_design';
const DEFAULT_COUNTER = 1;
const MAX_COUNTER = 999999;
const EXPORT_FIELDS_STORAGE_KEY = 'waveguide-export-fields';
const EXPORT_FIELDS_SCHEMA_VERSION = 1;
let folderWorkspaceBound = false;
let exportFieldPersistenceBound = false;

function bindFolderWorkspaceLabel() {
  if (folderWorkspaceBound || typeof document === 'undefined') {
    return;
  }
  folderWorkspaceBound = true;
  subscribeFolderWorkspace(({ label }) => {
    if (typeof document === 'undefined') {
      return;
    }
    const nameEl = document.getElementById('output-folder-name');
    if (nameEl) {
      nameEl.textContent = label;
    }
  });
}

function normalizeOutputName(value, fallback = DEFAULT_OUTPUT_NAME) {
  const text = String(value ?? '').trim();
  if (text) return text;
  return String(fallback ?? DEFAULT_OUTPUT_NAME).trim() || DEFAULT_OUTPUT_NAME;
}

function normalizeCounter(value, fallback = DEFAULT_COUNTER) {
  const num = Number(value);
  if (Number.isFinite(num) && num >= 1) {
    return Math.floor(num);
  }
  const fallbackNum = Number(fallback);
  if (Number.isFinite(fallbackNum) && fallbackNum >= 1) {
    return Math.floor(fallbackNum);
  }
  return DEFAULT_COUNTER;
}

function getStorage() {
  if (typeof localStorage === 'undefined') return null;
  if (typeof localStorage.getItem !== 'function' || typeof localStorage.setItem !== 'function') {
    return null;
  }
  return localStorage;
}

export function loadExportFields() {
  const storage = getStorage();
  if (!storage) {
    return { outputName: DEFAULT_OUTPUT_NAME, counter: DEFAULT_COUNTER };
  }

  try {
    const raw = storage.getItem(EXPORT_FIELDS_STORAGE_KEY);
    if (!raw) {
      return { outputName: DEFAULT_OUTPUT_NAME, counter: DEFAULT_COUNTER };
    }
    const parsed = JSON.parse(raw);
    const fields =
      parsed?.schemaVersion === EXPORT_FIELDS_SCHEMA_VERSION && parsed.exportFields
        ? parsed.exportFields
        : {};
    return {
      outputName: normalizeOutputName(fields.outputName, DEFAULT_OUTPUT_NAME),
      counter: normalizeCounter(fields.counter, DEFAULT_COUNTER),
    };
  } catch {
    return { outputName: DEFAULT_OUTPUT_NAME, counter: DEFAULT_COUNTER };
  }
}

export function saveExportFields(fields = {}) {
  const normalized = {
    outputName: normalizeOutputName(fields.outputName, DEFAULT_OUTPUT_NAME),
    counter: normalizeCounter(fields.counter, DEFAULT_COUNTER),
  };

  const storage = getStorage();
  if (!storage) return normalized;

  try {
    storage.setItem(
      EXPORT_FIELDS_STORAGE_KEY,
      JSON.stringify({
        schemaVersion: EXPORT_FIELDS_SCHEMA_VERSION,
        exportFields: normalized,
      })
    );
  } catch {
    // Storage failures should not block exports.
  }

  return normalized;
}

export function persistCurrentExportFields(doc = globalThis.document) {
  if (!doc || typeof doc.getElementById !== 'function') {
    return loadExportFields();
  }
  return saveExportFields({
    outputName: doc.getElementById('export-prefix')?.value,
    counter: doc.getElementById('export-counter')?.value,
  });
}

export function applySavedExportFields(doc = globalThis.document) {
  return setExportFields(loadExportFields(), doc, { persist: false });
}

function bindExportFieldPersistence() {
  if (exportFieldPersistenceBound || typeof document === 'undefined') {
    return;
  }
  exportFieldPersistenceBound = true;

  applySavedExportFields(document);

  const persist = () => persistCurrentExportFields(document);
  document.getElementById('export-prefix')?.addEventListener('input', persist);
  document.getElementById('export-prefix')?.addEventListener('change', persist);
  document.getElementById('export-counter')?.addEventListener('input', persist);
  document.getElementById('export-counter')?.addEventListener('change', persist);
}

export function deriveExportFieldsFromFileName(fileName, options = {}) {
  const fallbackOutputName = normalizeOutputName(options.defaultOutputName, DEFAULT_OUTPUT_NAME);
  const fallbackCounter = normalizeCounter(options.defaultCounter, DEFAULT_COUNTER);
  const rawName = String(fileName ?? '').trim();

  if (!rawName) {
    return { outputName: fallbackOutputName, counter: fallbackCounter };
  }

  const stem = rawName.replace(/\.[^./\\]+$/, '').trim();
  const normalizedStem = normalizeOutputName(stem, fallbackOutputName);
  const suffixMatch = normalizedStem.match(/^(.*)_([0-9]+)$/);

  if (!suffixMatch) {
    const trailingDigitsMatch = normalizedStem.match(/^(.*?)([0-9]+)$/);
    if (!trailingDigitsMatch) {
      return { outputName: normalizedStem, counter: fallbackCounter };
    }

    const parsedCounter = Number(trailingDigitsMatch[2]);
    if (!Number.isInteger(parsedCounter) || parsedCounter < 1) {
      return { outputName: normalizedStem, counter: fallbackCounter };
    }

    const parsedOutputName = trailingDigitsMatch[1].trim();
    if (!parsedOutputName) {
      return { outputName: normalizedStem, counter: fallbackCounter };
    }

    return { outputName: parsedOutputName, counter: parsedCounter };
  }

  const parsedCounter = Number(suffixMatch[2]);
  if (!Number.isInteger(parsedCounter) || parsedCounter < 1) {
    return { outputName: normalizedStem, counter: fallbackCounter };
  }

  const parsedOutputName = suffixMatch[1].trim();
  if (!parsedOutputName) {
    return { outputName: normalizedStem, counter: fallbackCounter };
  }

  return { outputName: parsedOutputName, counter: parsedCounter };
}

export function setExportFields(
  { outputName, counter } = {},
  doc = globalThis.document,
  options = {}
) {
  if (!doc || typeof doc.getElementById !== 'function') return;

  const normalizedOutputName = normalizeOutputName(outputName, DEFAULT_OUTPUT_NAME);
  const normalizedCounter = normalizeCounter(counter, DEFAULT_COUNTER);

  const prefixEl = doc.getElementById('export-prefix');
  if (prefixEl) {
    prefixEl.value = normalizedOutputName;
  }

  const counterEl = doc.getElementById('export-counter');
  if (counterEl) {
    counterEl.value = String(normalizedCounter);
  }

  if (options.persist !== false) {
    saveExportFields({ outputName: normalizedOutputName, counter: normalizedCounter });
  }
}

export function getExportBaseName() {
  const prefixEl = document.getElementById('export-prefix');
  const counterEl = document.getElementById('export-counter');

  // Validate prefix
  const rawPrefix = prefixEl?.value || '';
  const prefixValidation = validateOutputName(rawPrefix);
  const prefix = prefixValidation.valid ? prefixValidation.normalized : 'horn';

  // Validate counter
  const rawCounter = counterEl?.value || '1';
  const counterValidation = validateCounter(Number(rawCounter));
  const counter = counterValidation.valid ? counterValidation.normalized : 1;

  return `${prefix}_${counter}`;
}

export function incrementExportCounter() {
  const counterEl = document.getElementById('export-counter');
  if (!counterEl) return;

  const current = Number(counterEl.value) || 1;
  const next = current + 1;

  // Respect maximum counter value
  if (next > MAX_COUNTER) {
    counterEl.value = String(MAX_COUNTER);
    persistCurrentExportFields();
    showError(`Counter reached maximum (${MAX_COUNTER}). Set manually to continue.`);
    return;
  }

  counterEl.value = String(next);
  persistCurrentExportFields();
}

export function markParametersChanged() {
  if (skipNextParameterChange) {
    skipNextParameterChange = false;
    return;
  }
  if (!hasPendingParameterChanges) {
    incrementExportCounter();
  }
  hasPendingParameterChanges = true;
}

export function resetParameterChangeTracking({ skipNext = false } = {}) {
  hasPendingParameterChanges = false;
  skipNextParameterChange = Boolean(skipNext);
}

function shouldIncrementCounter(options = {}) {
  if (options.incrementCounter === false) return false;
  return hasPendingParameterChanges;
}

function finalizeExportCounter(options = {}) {
  if (!shouldIncrementCounter(options)) return;
  hasPendingParameterChanges = false;
}

function showWorkspaceSaveSuccess(fileName, result) {
  if (typeof document === 'undefined' || typeof document.createElement !== 'function') return;

  const path = String(result?.path || '').trim();
  const workspaceRoot = String(result?.workspaceRoot || '').trim();
  const destination = path || workspaceRoot;
  if (!destination) {
    showSuccess(`Saved ${fileName}.`);
    return;
  }

  showSuccess(`Saved ${fileName} to ${destination}.`, 7000);
}

bindFolderWorkspaceLabel();
bindExportFieldPersistence();

export async function saveFile(content, fileName, options = {}) {
  bindFolderWorkspaceLabel();

  // Sanitize and validate filename
  const finalName = sanitizeFileName(
    fileName || `${getExportBaseName()}${options.extension || ''}`
  );
  if (!finalName) {
    showError('Invalid filename. Please set a valid output name.');
    return;
  }

  try {
    const result = await writeWorkspaceFile(finalName, content, {
      contentType: options.contentType,
      workspaceSubdir: options.workspaceSubdir,
      timeoutMs: 30000,
    });
    finalizeExportCounter(options);
    showWorkspaceSaveSuccess(finalName, result);
    return;
  } catch (err) {
    debugWarn('Backend workspace export failed:', err);

    // Distinguish between network and other errors
    if (err.name === 'AbortError') {
      showError('Workspace export timed out. Falling back to browser download path.');
    } else if (err instanceof TypeError) {
      showError('Cannot reach backend workspace. Falling back to browser download path.');
    } else {
      const statusCode = Number(err.statusCode || 0);
      let prefix = 'Workspace export failed';
      if (statusCode === 401 || statusCode === 403) {
        prefix = 'Workspace permission denied';
      } else if (statusCode === 413) {
        prefix = 'Workspace export too large';
      } else if (statusCode >= 500) {
        prefix = 'Workspace server error';
      }
      showError(`${prefix}. Falling back to browser download path.`);
    }
  }

  if ('showSaveFilePicker' in (globalThis?.window || {})) {
    try {
      const handle = await globalThis.window.showSaveFilePicker({
        suggestedName: finalName,
        types: options.typeInfo ? [options.typeInfo] : undefined,
      });
      const writable = await handle.createWritable();
      await writable.write(content);
      await writable.close();
      finalizeExportCounter(options);
      return;
    } catch (err) {
      if (err.name === 'AbortError') return;
      debugWarn('showSaveFilePicker failed, fallback to legacy', err);
    }
  }

  const blob = new Blob([content], { type: options.contentType || 'application/octet-stream' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = finalName;
  link.click();
  URL.revokeObjectURL(url);
  finalizeExportCounter(options);
}
