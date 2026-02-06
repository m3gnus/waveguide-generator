/**
 * CAD Module - Parametric STEP-based geometry system.
 *
 * Public API for the rest of the application.
 * The actual OpenCascade operations run in a Web Worker via cadManager.
 */

export {
    initCADWorker,
    isCADReady,
    buildAndTessellate,
    buildForMSH,
    exportSTEP,
    downloadSTEP,
    terminateCADWorker
} from './cadManager.js';
