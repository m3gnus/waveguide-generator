import { showError } from './feedback.js';
import {
    ensureFolderWritePermission,
    getSelectedFolderHandle,
    requestFolderSelection,
    resetSelectedFolder,
    subscribeFolderWorkspace,
    supportsFolderSelection,
    setServerFolderPath,
    getServerFolderPath,
    loadServerFolderPathFromStorage
} from './workspace/folderWorkspace.js';

let hasPendingParameterChanges = false;
let skipNextParameterChange = false;
const DEFAULT_OUTPUT_NAME = 'horn_design';
const DEFAULT_COUNTER = 1;
const OUTPUT_FOLDER_BUTTON_LABEL = 'Output Folder';
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
    const prefix = document.getElementById('export-prefix')?.value || 'horn';
    const counterEl = document.getElementById('export-counter');
    const counter = counterEl ? counterEl.value : '1';
    return `${prefix}_${counter}`;
}

export function incrementExportCounter() {
    const counterEl = document.getElementById('export-counter');
    if (!counterEl) return;
    counterEl.value = String(parseInt(counterEl.value, 10) + 1);
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
        const handle = await requestFolderSelection(window);
        if (handle) {
            setServerFolderPath(null); // Clear server path if using native API
        }
        return;
    }

    // Fallback: Prompt for server-side folder path
    const currentPath = getServerFolderPath();
    const defaultPath = 'output';
    const promptText = `Enter server output folder path (relative to repo root):\n\nExamples: "output", "exports/my_project"`;
    const folderPath = prompt(promptText, currentPath || defaultPath);

    if (folderPath === null) {
        // User cancelled
        return;
    }

    if (!folderPath.trim()) {
        showError('Folder path cannot be empty.');
        return;
    }

    setServerFolderPath(folderPath);
}

export async function saveFile(content, fileName, options = {}) {
    bindFolderWorkspaceLabel();
    const finalName = fileName || `${getExportBaseName()}${options.extension || ''}`;

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
                body: formData
            });

            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.detail || `Upload failed: ${response.statusText}`);
            }

            finalizeExportCounter(options);
            return;
        } catch (err) {
            console.warn('Server-side export failed:', err);
            showError(`Export to server failed: ${err.message}`);
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
