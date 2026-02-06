/**
 * CAD Web Worker
 *
 * Runs the OpenCascade WASM kernel in a background thread.
 * Receives parameter objects, produces tessellated meshes and STEP data.
 *
 * Messages:
 *   IN:  { type: 'init' }                     → Load WASM
 *   IN:  { type: 'buildAndTessellate', params, options } → Build + tessellate for display
 *   IN:  { type: 'buildForMSH', params, options }        → Build + tessellate with face groups for MSH
 *   IN:  { type: 'exportSTEP', params, options }         → Build + export STEP
 *   OUT: { type: 'ready' }                     → WASM loaded
 *   OUT: { type: 'mesh', vertices, indices, normals }    → Tessellated mesh (display)
 *   OUT: { type: 'mshMesh', vertices, indices, faceGroups, faceMapping } → Tagged mesh (MSH)
 *   OUT: { type: 'step', data }                → STEP file bytes
 *   OUT: { type: 'error', message }            → Error
 *   OUT: { type: 'progress', stage, message }  → Progress update
 */

// Note: This worker uses importScripts-style loading for the OpenCascade module.
// The geometry modules (hornModels, morphing, etc.) are imported as ES modules.

import { buildHornCAD, addWallThickness } from './cadBuilder.js';
import { buildEnclosureCAD } from './cadEnclosure.js';
import { tessellate, tessellateWithGroups } from './tessellator.js';
import { exportToSTEP } from './cadExport.js';
import { initOCCT } from './occtInit.js';

let oc = null;

function sendProgress(stage, message) {
    self.postMessage({ type: 'progress', stage, message });
}

function sendError(message) {
    self.postMessage({ type: 'error', message });
}

async function handleInit() {
    try {
        sendProgress('init', 'Loading OpenCascade WASM kernel...');
        oc = await initOCCT();
        self.postMessage({ type: 'ready' });
    } catch (err) {
        sendError(`Failed to initialize OpenCascade: ${err.message}`);
    }
}

async function handleBuildAndTessellate(params, options = {}) {
    if (!oc) {
        sendError('OpenCascade not initialized. Send "init" first.');
        return;
    }

    try {
        sendProgress('build', 'Building parametric geometry...');
        const result = buildHornCAD(oc, params, {
            numStations: options.numStations || 30,
            numAngles: options.numAngles || 64,
            includeSource: options.includeSource !== false
        });

        // Optionally add wall thickness for free-standing horns
        let finalShape = result.shape;
        if (options.wallThickness > 0) {
            sendProgress('thickness', 'Adding wall thickness...');
            finalShape = addWallThickness(oc, result.hornShape, options.wallThickness);

            // Re-compound with source
            if (result.sourceShape) {
                const builder = new oc.BRep_Builder();
                const compound = new oc.TopoDS_Compound();
                builder.MakeCompound(compound);
                builder.Add(compound, finalShape);
                builder.Add(compound, result.sourceShape);
                finalShape = compound;
            }
        }

        sendProgress('tessellate', 'Tessellating for display...');
        const mesh = tessellate(oc, finalShape, {
            linearDeflection: options.linearDeflection || 0.5,
            angularDeflection: options.angularDeflection || 0.3
        });

        // Transfer ArrayBuffers to avoid copying
        self.postMessage(
            {
                type: 'mesh',
                vertices: mesh.vertices,
                indices: mesh.indices,
                normals: mesh.normals
            },
            [mesh.vertices.buffer, mesh.indices.buffer, mesh.normals.buffer]
        );
    } catch (err) {
        sendError(`Build failed: ${err.message}`);
        console.error('[CADWorker]', err);
    }
}

/**
 * Build horn (+ enclosure) and tessellate with face group IDs for MSH export.
 * Returns vertices, indices, faceGroups, and a faceMapping object that maps
 * face group IDs to physical surface categories (horn, source, enclosure).
 */
async function handleBuildForMSH(params, options = {}) {
    if (!oc) {
        sendError('OpenCascade not initialized. Send "init" first.');
        return;
    }

    try {
        sendProgress('build', 'Building parametric geometry for MSH export...');
        const result = buildHornCAD(oc, params, {
            numStations: options.numStations || 30,
            numAngles: options.numAngles || 64,
            includeSource: true
        });

        // Track which shapes contribute which face groups
        let combinedShape;
        const hornFaces = [];
        const sourceFaces = [];
        const enclosureFaces = [];

        // Count faces in horn shape to know face indices
        let faceIndex = 0;
        const countFaces = (shape) => {
            let count = 0;
            const exp = new oc.TopExp_Explorer_2(
                shape,
                oc.TopAbs_ShapeEnum.TopAbs_FACE,
                oc.TopAbs_ShapeEnum.TopAbs_SHAPE
            );
            while (exp.More()) { count++; exp.Next(); }
            return count;
        };

        // Horn faces
        const hornFaceCount = countFaces(result.hornShape);
        for (let i = 0; i < hornFaceCount; i++) {
            hornFaces.push(faceIndex++);
        }

        // Source faces
        if (result.sourceShape) {
            const sourceFaceCount = countFaces(result.sourceShape);
            for (let i = 0; i < sourceFaceCount; i++) {
                sourceFaces.push(faceIndex++);
            }
        }

        combinedShape = result.shape; // compound of horn + source

        // Build enclosure if requested
        if (options.includeEnclosure && Number(params.encDepth || 0) > 0) {
            sendProgress('build', 'Building enclosure geometry...');

            // Estimate mouth extents from the last cross-section
            // We use a simple approach: evaluate profile at t=1 for a few angles
            const mouthExtents = options.mouthExtents || { halfW: 100, halfH: 100 };
            const encShape = buildEnclosureCAD(oc, params, mouthExtents, result.hornShape);

            if (encShape) {
                const encFaceCount = countFaces(encShape);
                for (let i = 0; i < encFaceCount; i++) {
                    enclosureFaces.push(faceIndex++);
                }

                // Add enclosure to compound
                const builder = new oc.BRep_Builder();
                const compound = new oc.TopoDS_Compound();
                builder.MakeCompound(compound);
                builder.Add(compound, combinedShape);
                builder.Add(compound, encShape);
                combinedShape = compound;
            }
        }

        sendProgress('tessellate', 'Tessellating for MSH export...');
        const mesh = tessellateWithGroups(oc, combinedShape, {
            linearDeflection: options.linearDeflection || 0.3,
            angularDeflection: options.angularDeflection || 0.2
        });

        const faceMapping = {
            hornFaces,
            sourceFaces,
            enclosureFaces,
            interfaceFaces: [] // TODO: interface support for multi-domain BEM
        };

        self.postMessage(
            {
                type: 'mshMesh',
                vertices: mesh.vertices,
                indices: mesh.indices,
                faceGroups: mesh.faceGroups,
                faceMapping
            },
            [mesh.vertices.buffer, mesh.indices.buffer, mesh.faceGroups.buffer]
        );
    } catch (err) {
        sendError(`MSH build failed: ${err.message}`);
        console.error('[CADWorker]', err);
    }
}

async function handleExportSTEP(params, options = {}) {
    if (!oc) {
        sendError('OpenCascade not initialized. Send "init" first.');
        return;
    }

    try {
        sendProgress('build', 'Building parametric geometry for STEP export...');
        const result = buildHornCAD(oc, params, {
            numStations: options.numStations || 40,
            numAngles: options.numAngles || 80,
            includeSource: options.includeSource !== false
        });

        let finalShape = result.shape;
        if (options.wallThickness > 0) {
            sendProgress('thickness', 'Adding wall thickness...');
            const thickHorn = addWallThickness(oc, result.hornShape, options.wallThickness);
            const builder = new oc.BRep_Builder();
            const compound = new oc.TopoDS_Compound();
            builder.MakeCompound(compound);
            builder.Add(compound, thickHorn);
            if (result.sourceShape) {
                builder.Add(compound, result.sourceShape);
            }
            finalShape = compound;
        }

        sendProgress('export', 'Writing STEP file...');
        const data = exportToSTEP(oc, finalShape);

        self.postMessage(
            { type: 'step', data },
            [data.buffer]
        );
    } catch (err) {
        sendError(`STEP export failed: ${err.message}`);
        console.error('[CADWorker]', err);
    }
}

self.onmessage = async (event) => {
    const { type, params, options } = event.data;

    switch (type) {
        case 'init':
            await handleInit();
            break;
        case 'buildAndTessellate':
            await handleBuildAndTessellate(params, options);
            break;
        case 'buildForMSH':
            await handleBuildForMSH(params, options);
            break;
        case 'exportSTEP':
            await handleExportSTEP(params, options);
            break;
        default:
            sendError(`Unknown message type: ${type}`);
    }
};
