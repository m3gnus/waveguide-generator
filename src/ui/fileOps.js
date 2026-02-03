
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

export async function saveFile(content, fileName, options = {}) {
    const baseName = options.baseName || getExportBaseName();
    const extension = options.extension || '';
    const finalName = `${baseName}${extension}`;
    const incrementCounter = options.incrementCounter !== false;

    if ('showSaveFilePicker' in window) {
        try {
            const handle = await window.showSaveFilePicker({
                suggestedName: finalName,
                types: [options.typeInfo]
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
