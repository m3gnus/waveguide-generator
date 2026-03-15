import { showError } from './feedback.js';
import { validateOutputName, validateCounter, sanitizeFileName } from './inputValidation.js';
import {
    ensureFolderWritePermission,
    getSelectedFolderHandle,
    requestFolderSelection,
    resetSelectedFolder,
    subscribeFolderWorkspace,
    supportsFolderSelection,
    setServerFolderPath,
    getServerFolderPath,
    loadServerFolderPathFromStorage,
    showOutputFolderPanel
} from './workspace/folderWorkspace.js';

let hasPendingParameterChanges = false;
let skipNextParameterChange = false;
const DEFAULT_OUTPUT_NAME = 'horn_design';
const DEFAULT_COUNTER = 1;
const OUTPUT_FOLDER_BUTTON_LABEL = 'Output Folder';
const MAX_OUTPUT_NAME_LENGTH = 128;
const MAX_COUNTER = 999999;
let folderWorkspaceBound = false;

function bindFolderWorkspaceLabel() {
    if (folderWorkspaceBound || typeof document === 'undefined') {
        return;
    }
    folderWorkspaceBound = true;
    subscribeFolderWorkspace(({ label }) => {
        const nameEl = document.getElementById('output-folder-name');
        if (nameEl) {
            nameEl.textContent = label;
        }
        const chooseBtn = document.getElementById('choose-folder-btn');
        if (chooseBtn) {
            const hasSelection = label && label !== 'No folder selected';
            chooseBtn.textContent = OUTPUT_FOLDER_BUTTON_LABEL;
            chooseBtn.title = hasSelection
                ? `Selected output folder: ${label}`
                : 'Choose output folder workspace';
            if (typeof chooseBtn.setAttribute === 'function') {
                chooseBtn.setAttribute('aria-label', chooseBtn.title);
            }
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

export function setExportFields({ outputName, counter } = {}, doc = document) {
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
        showError(`Counter reached maximum (${MAX_COUNTER}). Set manually to continue.`);
        return;
    }

    counterEl.value = String(next);
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

bindFolderWorkspaceLabel();
loadServerFolderPathFromStorage();

export async function selectOutputFolder() {
    bindFolderWorkspaceLabel();

    // Try native File System Access API first (Chrome/Edge/Firefox with flag)
    if (supportsFolderSelection(window)) {
        try {
            const handle = await requestFolderSelection(window);
            if (handle) {
                setServerFolderPath(null); // Clear server path if using native API
            }
        } catch (err) {
            if (err.name !== 'AbortError') {
                showError('Failed to select folder. Your browser may not support this feature.');
            }
        }
        return;
    }

    // Fallback for browsers without showDirectoryPicker (e.g. Firefox):
    // Show a proper panel with the current output path and an "Open in Finder" button.
    await showOutputFolderPanel();
}

export async function saveFile(content, fileName, options = {}) {
    bindFolderWorkspaceLabel();

    // Sanitize and validate filename
    const finalName = sanitizeFileName(fileName || `${getExportBaseName()}${options.extension || ''}`);
    if (!finalName) {
        showError('Invalid filename. Please set a valid output name.');
        return;
    }

    // Check if server-side folder is configured
    const serverFolderPath = getServerFolderPath();
    if (serverFolderPath) {
        try {
            const formData = new FormData();
            const blob = content instanceof Blob
                ? content
                : new Blob([content], { type: options.contentType || 'text/plain' });
            formData.append('file', blob, finalName);
            formData.append('folder_path', serverFolderPath);

            const response = await fetch('http://localhost:8000/api/export-file', {
                method: 'POST',
                body: formData,
                signal: AbortSignal.timeout(30000) // 30 second timeout
            });

            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                const statusCode = response.status;
                let errorMsg = error.detail || response.statusText || 'Unknown error';

                // Provide more specific error messages
                if (statusCode === 401 || statusCode === 403) {
                    errorMsg = 'Permission denied: Cannot write to output folder';
                } else if (statusCode === 413) {
                    errorMsg = 'File too large. Please reduce output size or split into multiple exports.';
                } else if (statusCode === 500 || statusCode === 503) {
                    errorMsg = 'Server error. Please try again or choose a different output folder.';
                }

                throw new Error(errorMsg);
            }

            finalizeExportCounter(options);
            return;
        } catch (err) {
            console.warn('Server-side export failed:', err);

            // Distinguish between network and other errors
            if (err.name === 'AbortError') {
                showError('Export timeout. Server took too long to respond. Try again or use local folder.');
            } else if (err instanceof TypeError) {
                showError('Network error: Cannot reach export server. Check server connection.');
            } else {
                showError(`Export failed: ${err.message}`);
            }
            return;
        }
    }

    // Fallback to native File System Access API
    const outputDirHandle = getSelectedFolderHandle();
    if (outputDirHandle) {
        try {
            const permissionGranted = await ensureFolderWritePermission(outputDirHandle);
            if (!permissionGranted) {
                throw new Error('Write permission for selected folder was denied.');
            }
            const fileHandle = await outputDirHandle.getFileHandle(finalName, { create: true });
            const writable = await fileHandle.createWritable();
            const blob = content instanceof Blob
                ? content
                : new Blob([content], { type: options.contentType || 'text/plain' });
            await writable.write(blob);
            await writable.close();
            finalizeExportCounter(options);
            return;
        } catch (err) {
            console.warn('Direct folder write failed, falling back to file picker:', err);
            resetSelectedFolder();
        }
    }

    if ('showSaveFilePicker' in window) {
        try {
            const handle = await window.showSaveFilePicker({
                suggestedName: finalName,
                types: options.typeInfo ? [options.typeInfo] : undefined
            });
            const writable = await handle.createWritable();
            await writable.write(content);
            await writable.close();
            finalizeExportCounter(options);
            return;
        } catch (err) {
            if (err.name === 'AbortError') return;
            console.warn('showSaveFilePicker failed, fallback to legacy', err);
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
