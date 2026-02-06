/**
 * CAD Manager - Main thread interface to the CAD Web Worker.
 *
 * Provides a promise-based API for building horn geometry and exporting STEP files.
 * The actual OpenCascade operations run in a background Web Worker thread.
 */

let worker = null;
let workerReady = false;
let initPromise = null;
let pendingCallbacks = new Map();
let requestId = 0;
let progressCallback = null;

/**
 * Initialize the CAD worker.
 * @param {Function} onProgress - Optional progress callback (stage, message)
 * @returns {Promise<void>}
 */
export function initCADWorker(onProgress) {
    if (initPromise) return initPromise;

    progressCallback = onProgress || null;

    initPromise = new Promise((resolve, reject) => {
        try {
            worker = new Worker(
                new URL('./cad.worker.js', import.meta.url),
                { type: 'module' }
            );
        } catch (e) {
            reject(new Error(`Failed to create CAD worker: ${e.message}`));
            return;
        }

        worker.onmessage = (event) => {
            const msg = event.data;

            switch (msg.type) {
                case 'ready':
                    workerReady = true;
                    resolve();
                    break;

                case 'progress':
                    if (progressCallback) {
                        progressCallback(msg.stage, msg.message);
                    }
                    break;

                case 'mesh': {
                    const cb = pendingCallbacks.get('mesh');
                    if (cb) {
                        cb.resolve(msg);
                        pendingCallbacks.delete('mesh');
                    }
                    break;
                }

                case 'mshMesh': {
                    const cb = pendingCallbacks.get('mshMesh');
                    if (cb) {
                        cb.resolve({
                            vertices: msg.vertices,
                            indices: msg.indices,
                            faceGroups: msg.faceGroups,
                            faceMapping: msg.faceMapping
                        });
                        pendingCallbacks.delete('mshMesh');
                    }
                    break;
                }

                case 'step': {
                    const cb = pendingCallbacks.get('step');
                    if (cb) {
                        cb.resolve(msg.data);
                        pendingCallbacks.delete('step');
                    }
                    break;
                }

                case 'error': {
                    console.error('[CADManager] Worker error:', msg.message);
                    // Reject all pending callbacks
                    for (const [key, cb] of pendingCallbacks) {
                        cb.reject(new Error(msg.message));
                    }
                    pendingCallbacks.clear();
                    break;
                }
            }
        };

        worker.onerror = (err) => {
            console.error('[CADManager] Worker fatal error:', err);
            const msg = err.message || 'Worker crashed';
            for (const [key, cb] of pendingCallbacks) {
                cb.reject(new Error(msg));
            }
            pendingCallbacks.clear();
            if (!workerReady) {
                reject(new Error(msg));
            }
        };

        // Send init command to load WASM
        worker.postMessage({ type: 'init' });
    });

    return initPromise;
}

/**
 * Check if the CAD system is ready.
 * @returns {boolean}
 */
export function isCADReady() {
    return workerReady;
}

/**
 * Build horn geometry and get tessellated mesh for viewport display.
 *
 * @param {Object} params - Horn parameters
 * @param {Object} options - Build/tessellation options
 * @returns {Promise<{ vertices: Float32Array, indices: Uint32Array, normals: Float32Array }>}
 */
export async function buildAndTessellate(params, options = {}) {
    if (!workerReady) {
        await initCADWorker();
    }

    return new Promise((resolve, reject) => {
        pendingCallbacks.set('mesh', { resolve, reject });
        worker.postMessage({
            type: 'buildAndTessellate',
            params: serializeParams(params),
            options
        });
    });
}

/**
 * Build horn (+ enclosure) and tessellate with face group data for MSH export.
 *
 * @param {Object} params - Horn parameters
 * @param {Object} options - Build/tessellation options
 * @returns {Promise<{ vertices: Float32Array, indices: Uint32Array, faceGroups: Int32Array, faceMapping: Object }>}
 */
export async function buildForMSH(params, options = {}) {
    if (!workerReady) {
        await initCADWorker();
    }

    return new Promise((resolve, reject) => {
        pendingCallbacks.set('mshMesh', { resolve, reject });
        worker.postMessage({
            type: 'buildForMSH',
            params: serializeParams(params),
            options
        });
    });
}

/**
 * Build horn geometry and export as STEP file.
 *
 * @param {Object} params - Horn parameters
 * @param {Object} options - Build options
 * @returns {Promise<Uint8Array>} STEP file data
 */
export async function exportSTEP(params, options = {}) {
    if (!workerReady) {
        await initCADWorker();
    }

    return new Promise((resolve, reject) => {
        pendingCallbacks.set('step', { resolve, reject });
        worker.postMessage({
            type: 'exportSTEP',
            params: serializeParams(params),
            options
        });
    });
}

/**
 * Download STEP file via browser.
 *
 * @param {Object} params - Horn parameters
 * @param {string} filename - Download filename
 * @param {Object} options - Build options
 */
export async function downloadSTEP(params, filename = 'horn.step', options = {}) {
    const data = await exportSTEP(params, options);
    const blob = new Blob([data], { type: 'application/step' });
    const url = URL.createObjectURL(blob);

    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    URL.revokeObjectURL(url);
}

/**
 * Terminate the CAD worker.
 */
export function terminateCADWorker() {
    if (worker) {
        worker.terminate();
        worker = null;
        workerReady = false;
        initPromise = null;
        pendingCallbacks.clear();
    }
}

/**
 * Serialize params for transfer to worker.
 * Functions (from expression parsing) cannot be transferred via postMessage,
 * so we send the raw string expressions and let the worker re-parse them.
 */
function serializeParams(params) {
    const serialized = {};
    for (const [key, value] of Object.entries(params)) {
        if (typeof value === 'function') {
            // Functions can't be postMessage'd; send the original string if available
            // The worker will re-parse expressions
            serialized[key] = value._source || String(value);
        } else if (key === '__gcurveCache') {
            // Skip internal caches
            continue;
        } else {
            serialized[key] = value;
        }
    }
    return serialized;
}
