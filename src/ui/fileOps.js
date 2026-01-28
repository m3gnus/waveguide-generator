
export async function saveFile(content, fileName, options = {}) {
    const prefix = document.getElementById('export-prefix')?.value || 'horn';
    const counterEl = document.getElementById('export-counter');
    const counter = counterEl ? counterEl.value : '1';
    const finalName = `${prefix}_${counter}${options.extension}`;

    // Helper to increment counter
    const incrementCounter = () => {
        if (counterEl) counterEl.value = parseInt(counterEl.value) + 1;
    };

    if ('showSaveFilePicker' in window) {
        try {
            const handle = await window.showSaveFilePicker({
                suggestedName: finalName,
                types: [options.typeInfo]
            });
            const writable = await handle.createWritable();
            await writable.write(content);
            await writable.close();
            incrementCounter();
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
    incrementCounter();
}
