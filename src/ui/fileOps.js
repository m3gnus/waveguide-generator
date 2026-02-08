import { showError } from './feedback.js';

let outputDirHandle = null;

export function getExportBaseName() {
    const prefix = document.getElementById('export-prefix')?.value || 'horn';
    const counterEl = document.getElementById('export-counter');
    const counter = counterEl ? counterEl.value : '1';
    return `${prefix}_${counter}`;
}

export function incrementExportCounter() {
    const counterEl = document.getElementById('export-counter');
    if (!counterEl) return;
    counterEl.value = parseInt(counterEl.value, 10) + 1;
}

export async function selectOutputFolder() {
    if (!window.showDirectoryPicker) {
        showError('Your browser does not support folder selection.');
        return;
    }
    try {
        outputDirHandle = await window.showDirectoryPicker({ mode: 'readwrite' });
        const nameEl = document.getElementById('output-folder-name');
        if (nameEl) nameEl.textContent = outputDirHandle.name;
    } catch (err) {
        if (err.name !== 'AbortError') {
            console.error('Folder selection failed:', err);
        }
    }
}

export async function saveFile(content, fileName, options = {}) {
    const finalName = fileName || `${getExportBaseName()}${options.extension || ''}`;
    const incrementCounter = options.incrementCounter !== false;

    // If a folder is selected, write directly to it
    if (outputDirHandle) {
        try {
            const fileHandle = await outputDirHandle.getFileHandle(finalName, { create: true });
            const writable = await fileHandle.createWritable();
            const blob = content instanceof Blob
                ? content
                : new Blob([content], { type: options.contentType || 'text/plain' });
            await writable.write(blob);
            await writable.close();
            if (incrementCounter) incrementExportCounter();
            return;
        } catch (err) {
            console.warn('Direct folder write failed, falling back to file picker:', err);
            outputDirHandle = null;
            const nameEl = document.getElementById('output-folder-name');
            if (nameEl) nameEl.textContent = 'No folder selected';
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
            if (incrementCounter) incrementExportCounter();
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
    if (incrementCounter) incrementExportCounter();
}
