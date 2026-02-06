/**
 * OpenCascade.js WASM initialization singleton.
 *
 * Loads the OpenCascade CAD kernel (~25MB WASM) lazily on first use.
 * Designed to run inside a Web Worker to avoid blocking the main thread.
 *
 * Usage:
 *   const oc = await initOCCT();
 *   const box = new oc.BRepPrimAPI_MakeBox_2(10, 20, 30);
 */

let ocInstance = null;
let initPromise = null;

/**
 * Initialize OpenCascade WASM runtime.
 * Returns cached instance on subsequent calls.
 * @returns {Promise<Object>} The OpenCascade API object
 */
export async function initOCCT() {
    if (ocInstance) return ocInstance;
    if (initPromise) return initPromise;

    initPromise = (async () => {
        try {
            // Dynamic import for the OpenCascade WASM loader
            // The .wasm.js file is the Emscripten-generated JS loader
            // The .wasm file must be co-located or locatable via locateFile
            const wasmModule = await import('../../node_modules/opencascade.js/dist/opencascade.wasm.js');
            const opencascadeFactory = wasmModule.default || wasmModule;

            ocInstance = await opencascadeFactory({
                locateFile(filename) {
                    if (filename.endsWith('.wasm')) {
                        // Resolve relative to the project root
                        return new URL(
                            '../../node_modules/opencascade.js/dist/opencascade.wasm.wasm',
                            import.meta.url
                        ).href;
                    }
                    return filename;
                }
            });

            console.log('[OCCT] OpenCascade WASM initialized successfully');
            return ocInstance;
        } catch (err) {
            initPromise = null;
            console.error('[OCCT] Failed to initialize OpenCascade:', err);
            throw err;
        }
    })();

    return initPromise;
}

/**
 * Check if OpenCascade is already initialized.
 * @returns {boolean}
 */
export function isOCCTReady() {
    return ocInstance !== null;
}

/**
 * Get the cached OpenCascade instance (throws if not initialized).
 * @returns {Object}
 */
export function getOCCT() {
    if (!ocInstance) throw new Error('[OCCT] Not initialized. Call initOCCT() first.');
    return ocInstance;
}
