/**
 * Export horn geometries to Gmsh .msh format suitable for BEM solvers
 * Supports all horn types (OSSE, R-OSSE) with proper boundary conditions
 *
 * Two export paths:
 * 1. Legacy: from raw vertices/indices (buildHornMesh)
 * 2. CAD: from tessellated B-Rep shapes (tessellateWithGroups)
 */

import {
    EPS,
    cleanNumber,
    formatNumber,
    evalParam,
    isFullCircle
} from '../geometry/common.js';


const transformVerticesToAth = (vertices) => {
    const out = new Array(vertices.length);
    for (let i = 0; i < vertices.length; i += 3) {
        const x = vertices[i];
        const y = vertices[i + 1];
        const z = vertices[i + 2];
        // ATH axis convention: X = horizontal, Y = vertical, Z = axial
        out[i] = x;
        out[i + 1] = z;
        out[i + 2] = y;
    }
    return out;
};

const appendThroatCap = (vertices, indices, params, ringCount = null) => {
    let count = Number.isFinite(ringCount) && ringCount > 1 ? ringCount : null;
    if (!count) {
        const radialSteps = Number(params.angularSegments || 0);
        if (!Number.isFinite(radialSteps) || radialSteps <= 0) {
            return { capTriangleCount: 0 };
        }
        count = Math.max(2, Math.round(radialSteps));
    }
    if (!Number.isFinite(count) || count <= 1) {
        return { capTriangleCount: 0 };
    }

    if (vertices.length < count * 3) {
        return { capTriangleCount: 0 };
    }

    let throatY = Infinity;
    let maxR = 0;

    for (let i = 0; i < count; i++) {
        const idx = i * 3;
        const y = vertices[idx + 1];
        if (y < throatY) throatY = y;
    }

    const r0Param = evalParam(params.r0 ?? 0, 0);
    const verticalOffset = Number.isFinite(params?.verticalOffset) ? params.verticalOffset : parseFloat(params?.verticalOffset) || 0;
    const cx = 0;
    const cz = verticalOffset;

    for (let i = 0; i < count; i++) {
        const idx = i * 3;
        const x = vertices[idx];
        const z = vertices[idx + 2];
        const r = Math.hypot(x - cx, z - cz);
        if (r > maxR) maxR = r;
    }

    const throatRadius = Number.isFinite(r0Param) && r0Param > 0 ? r0Param : maxR;

    const a0Deg = evalParam(params.a0 ?? 0, 0);
    const a0Rad = Number.isFinite(a0Deg) ? (a0Deg * Math.PI) / 180 : 0;
    let capScale = 1;
    if (String(params.type) === 'R-OSSE') {
        capScale = 0.5;
    } else if (Number.isFinite(a0Deg) && a0Deg <= 12) {
        capScale = 0.5;
    }

    let capHeight = throatRadius * Math.tan(a0Rad) * capScale;
    if (!Number.isFinite(capHeight) || capHeight < 0) {
        capHeight = 0;
    }

    const centerIndex = vertices.length / 3;
    vertices.push(cx, throatY + capHeight, cz);

    const fullCircle = isFullCircle(params.quadrants);
    const segmentCount = fullCircle ? count : Math.max(0, count - 1);
    const capStartIndex = indices.length / 3;

    for (let i = 0; i < segmentCount; i++) {
        const i2 = fullCircle ? (i + 1) % count : i + 1;
        if (!fullCircle && i2 >= count) break;
        indices.push(centerIndex, i2, i);
    }

    return { capTriangleCount: (indices.length / 3) - capStartIndex };
};

const buildMsh = (vertices, indices, physicalTags, physicalNames = null) => {
    const nodeCount = vertices.length / 3;
    const elementCount = indices.length / 3;

    const names = physicalNames ?? [
        { id: 1, name: 'SD1G0' },
        { id: 2, name: 'SD1D1001' }
    ];

    let mshContent = `$MeshFormat\n`;
    mshContent += `2.2 0 8\n`;
    mshContent += `$EndMeshFormat\n`;
    mshContent += `$PhysicalNames\n`;
    mshContent += `${names.length}\n`;
    names.forEach(({ id, name }) => {
        mshContent += `2 ${id} "${name}"\n`;
    });
    mshContent += `$EndPhysicalNames\n`;
    mshContent += `$Nodes\n`;
    mshContent += `${nodeCount}\n`;

    for (let i = 0; i < vertices.length; i += 3) {
        const id = i / 3 + 1;
        const x = formatNumber(vertices[i]);
        const y = formatNumber(vertices[i + 1]);
        const z = formatNumber(vertices[i + 2]);
        mshContent += `${id} ${x} ${y} ${z}\n`;
    }

    mshContent += `$EndNodes\n`;
    mshContent += `$Elements\n`;
    mshContent += `${elementCount}\n`;

    for (let i = 0; i < indices.length; i += 3) {
        const id = i / 3 + 1;
        const n1 = indices[i] + 1;
        const n2 = indices[i + 1] + 1;
        const n3 = indices[i + 2] + 1;
        const physical = physicalTags ? physicalTags[i / 3] : 1;
        const entity = physical;
        mshContent += `${id} 2 2 ${physical} ${entity} ${n1} ${n2} ${n3}\n`;
    }

    mshContent += `$EndElements\n`;
    return mshContent;
};

/**
 * Export horn geometry to Gmsh .msh format (version 2.2)
 * @param {Object} params - The complete parameter object
 * @param {Array<number>} vertices - Flat array of vertex coordinates [x,y,z, x,y,z, ...]
 * @param {Array<number>} indices - Triangle index array
 * @returns {string} Gmsh .msh file content
 */
export function exportHornToMSH(vertices, indices, params, meshInfo = null) {
    const vertexList = Array.isArray(vertices) ? vertices.slice() : Array.from(vertices);
    const indexList = Array.isArray(indices) ? indices.slice() : Array.from(indices || []);
    const transformed = transformVerticesToAth(vertexList);
    const physicalTags = new Array(indexList.length / 3).fill(1);
    return buildMsh(transformed, indexList, physicalTags);
}

/**
 * Export horn geometry to Gmsh .geo format (alternative for better compatibility)
 *
 * NOTE: There's also `exportGmshGeo` in profiles.js which is simpler but doesn't
 * include physical surface definitions. This version is more complete for BEM.
 */
export function exportHornToGeo(vertices, params) {
    const { angularSegments, lengthSegments } = params;
    let geoContent = `// Gmsh .geo file for MWG horn export\n`;
    geoContent += `Mesh.Algorithm = 2;\n`;
    geoContent += `Mesh.MshFileVersion = 2.2;\n`;
    geoContent += `General.Verbosity = 2;\n`;

    const meshSize = params.throatResolution || 50.0;
    let pointIndex = 1;
    for (let i = 0; i < vertices.length; i += 3) {
        const x = vertices[i];
        const y = vertices[i + 1];
        const z = vertices[i + 2];
        geoContent += `Point(${pointIndex})={${x.toFixed(3)},${y.toFixed(3)},${z.toFixed(3)},${meshSize}};\n`;
        pointIndex++;
    }
    geoContent += `Physical Surface("Throat") = {1};\n`;
    geoContent += `Physical Surface("HornWalls") = {2};\n`;
    geoContent += `Physical Surface("Mouth") = {3};\n`;
    return geoContent;
}

/**
 * Generate complete Gmsh .geo file matching ATH format exactly.
 * This file can be processed by Gmsh to produce bitwise-identical .msh and .stl files.
 *
 * Supports two modes:
 * 1. Quad-based (default): matches regular ATH 'mesh.geo'. 
 *    - Coordinate mapping: X=vx, Y=vz-offset, Z=vy. 
 *    - Fixed meshSize (default 50.0).
 * 2. Spline-based (useSplines: true): matches 'bem_mesh.geo' (Edge Loop fix).
 *    - Coordinate mapping: X=vx, Y=vz, Z=vy minus extension? Or just vy.
 *    - Actually, bem_mesh.geo for tritonia: Point(1)={12.7, 80.0, -6.148, 5.0}
 *      MWG coords for throat: vx=12.7, vy=0, vz=80.
 *      So X=vx, Y=vz, Z=vy - (some constant?). 
 *      Wait, Z = vy - 6.148? 
 *      If vy starts at 0, and reference Z starts at -6.148, then Z = vy - 6.148.
 *    - Variable meshSize based on resolution parameters.
 */
export function exportFullGeo(vertices, params, options = {}) {
    const angularSegments = Number(params.angularSegments || 80);
    const lengthSegments = Number(params.lengthSegments || 20);
    const useSplines = options.useSplines || false;
    const meshSizeFixed = options.meshSize !== undefined ? options.meshSize : 50.0;
    const throatResolution = Number(params.throatResolution || 5);
    const mouthResolution = Number(params.mouthResolution || 10);
    const outputName = options.outputName || 'output';

    const numRings = lengthSegments + 1;
    const pointsPerRing = angularSegments;
    const fullCircle = isFullCircle(params.quadrants);
    const verticalOffset = Number(params.verticalOffset) || 0;

    // Longitudinal Z-offset check (some ATH versions add extension)
    // We'll peek at the first vertex vy to see if we need an adjustment
    const firstVy = vertices[1] || 0;
    const zAdjustment = useSplines ? firstVy : 0;

    let geo = `Mesh.Algorithm = 2;\n`;
    geo += `Mesh.MshFileVersion = 2.2;\n`;
    geo += `General.Verbosity = 2;\n`;

    // Output points (1-indexed)
    for (let j = 0; j < numRings; j++) {
        const t = j / (numRings - 1);
        const meshSize = useSplines
            ? throatResolution + (mouthResolution - throatResolution) * t
            : meshSizeFixed;

        for (let i = 0; i < pointsPerRing; i++) {
            const idx = (j * pointsPerRing + i) * 3;
            const vx = vertices[idx];
            const vy = vertices[idx + 1];
            const vz = vertices[idx + 2];

            let geoX, geoY, geoZ;
            if (useSplines) {
                // bem_mesh.geo convention: X=vx, Y=vz, Z=vy
                geoX = vx;
                geoY = vz;
                geoZ = vy;
            } else {
                // regular mesh.geo convention: X=vx, Y=vz-offset, Z=vy
                geoX = vx;
                geoY = vz - verticalOffset;
                geoZ = vy;
            }

            const pointId = j * pointsPerRing + i + 1;
            geo += `Point(${pointId})={${geoX.toFixed(3)},${geoY.toFixed(3)},${geoZ.toFixed(3)},${meshSize.toFixed(1)}};\n`;
        }
    }

    if (!useSplines) {
        // Quad-based Line/Surface logic...
        const radialLineId = (j, i) => (j >= numRings - 1) ? null : j * pointsPerRing * 2 + 2 * i + 1;
        const angularLineId = (j, i) => {
            const lastIdx = fullCircle ? pointsPerRing : pointsPerRing - 1;
            if (i >= lastIdx) return null;
            return (j < numRings - 1) ? j * pointsPerRing * 2 + 2 * i + 2 : (numRings - 1) * pointsPerRing * 2 + i + 1;
        };

        let lineId = 1;
        for (let j = 0; j < numRings; j++) {
            for (let i = 0; i < pointsPerRing; i++) {
                const p1 = j * pointsPerRing + i + 1;
                if (j < numRings - 1) {
                    const p2 = (j + 1) * pointsPerRing + i + 1;
                    geo += `Line(${lineId++})={${p1},${p2}};\n`;
                }
                const lastI = fullCircle ? pointsPerRing : pointsPerRing - 1;
                if (i < lastI) {
                    const p2 = j * pointsPerRing + (i + 1) % pointsPerRing + 1;
                    geo += `Line(${lineId++})={${p1},${p2}};\n`;
                }
            }
        }

        let loopId = 511;
        let surfaceId = 1;
        const angularLimit = fullCircle ? pointsPerRing : pointsPerRing - 1;
        const surfaceIds = [];
        for (let j = 0; j < numRings - 1; j++) {
            for (let i = 0; i < angularLimit; i++) {
                const l1 = radialLineId(j, i);
                const l2 = angularLineId(j + 1, i);
                const l3 = radialLineId(j, (i + 1) % pointsPerRing);
                const l4 = angularLineId(j, i);
                if (l1 && l2 && l3 && l4) {
                    geo += `Curve Loop(${loopId})={${l1},${l2},-${l3},-${l4}};\n`;
                    geo += `Plane Surface(${surfaceId})={${loopId}};\n`;
                    surfaceIds.push(surfaceId++);
                    loopId++;
                }
            }
        }
        geo += `\nPhysical Surface("${outputName}", 1)={${surfaceIds.join(',')}};\n`;
    } else {
        // Spline-based (Patch) logic...
        const ptsPerSpline = 10;
        const numSplines = Math.ceil(pointsPerRing / ptsPerSpline);
        let splineId = 1;
        const ringSplineIds = [];
        for (let j = 0; j < numRings; j++) {
            const ringSplines = [];
            for (let s = 0; s < numSplines; s++) {
                const startI = s * ptsPerSpline;
                let count = ptsPerSpline;
                const isLast = (s === numSplines - 1);
                if (isLast && !fullCircle) count = (pointsPerRing - 1) - startI;
                if (count <= 0) continue;
                const pts = [];
                for (let k = 0; k <= count; k++) pts.push(j * pointsPerRing + (startI + k) % pointsPerRing + 1);
                geo += `Spline(${splineId})={${pts.join(',')}};\n`;
                ringSplines.push({ id: splineId++, startI, endI: (startI + count) % pointsPerRing });
            }
            ringSplineIds.push(ringSplines);
        }

        let radialLineId = splineId + 100;
        const radialLines = {};
        for (let j = 0; j < numRings - 1; j++) {
            for (const s of ringSplineIds[j]) {
                const addLine = (pI) => {
                    const key = `${j},${pI}`;
                    if (!radialLines[key]) {
                        geo += `Line(${radialLineId})={${j * pointsPerRing + pI + 1},${(j + 1) * pointsPerRing + pI + 1}};\n`;
                        radialLines[key] = radialLineId++;
                    }
                };
                addLine(s.startI);
                if (!fullCircle) addLine(s.endI);
            }
        }

        let loopId = radialLineId + 100;
        let surfaceId = 1;
        const surfaceIds = [];
        for (let j = 0; j < numRings - 1; j++) {
            for (let s = 0; s < ringSplineIds[j].length; s++) {
                const s1 = ringSplineIds[j][s];
                const s2 = ringSplineIds[j + 1][s];
                const r1 = radialLines[`${j},${s1.startI}`];
                const r2 = radialLines[`${j},${s1.endI}`];
                if (r1 && r2) {
                    geo += `Curve Loop(${loopId})={${s2.id},-${r2},-${s1.id},${r1}};\n`;
                    geo += `Surface(${surfaceId})={${loopId}};\n`;
                    surfaceIds.push(surfaceId++);
                    loopId++;
                }
            }
        }
        geo += `\nPhysical Surface("SD1G0", 1)={${surfaceIds.join(',')}};\n`;
    }

    geo += `\nMesh 2;\n`;
    geo += `Save "${outputName}.msh";\n`;
    return geo;
}

/**
 * Export horn to MSH from CAD tessellated data with face groups.
 *
 * The CAD pipeline produces separate shapes (horn, source, enclosure) which are
 * tessellated with per-triangle face group IDs. This function maps those face groups
 * to the physical surface tags expected by ABEC/BEM solvers.
 *
 * @param {Float32Array} vertices - Flat vertex array from tessellateWithGroups
 * @param {Uint32Array} indices - Triangle index array from tessellateWithGroups
 * @param {Int32Array} faceGroups - Per-triangle face group ID from tessellateWithGroups
 * @param {Object} faceMapping - Maps face group IDs to physical surface tags
 * @param {number[]} faceMapping.hornFaces - Face group IDs belonging to horn walls (tag 1 = SD1G0)
 * @param {number[]} faceMapping.sourceFaces - Face group IDs for acoustic source (tag 2 = SD1D1001)
 * @param {number[]} faceMapping.enclosureFaces - Face group IDs for enclosure (tag 3 = SD2G0)
 * @param {number[]} faceMapping.interfaceFaces - Face group IDs for interfaces (tag 4 = I1-2)
 * @returns {string} Gmsh .msh file content
 */
export function exportHornToMSHFromCAD(vertices, indices, faceGroups, faceMapping = {}) {
    const vertexList = Array.from(vertices);
    const indexList = Array.from(indices);
    const transformed = transformVerticesToAth(vertexList);

    const hornFaces = new Set(faceMapping.hornFaces || []);
    const sourceFaces = new Set(faceMapping.sourceFaces || []);
    const enclosureFaces = new Set(faceMapping.enclosureFaces || []);
    const interfaceFaces = new Set(faceMapping.interfaceFaces || []);

    const hasEnclosure = enclosureFaces.size > 0;
    const hasInterface = interfaceFaces.size > 0;

    const triangleCount = indexList.length / 3;
    const physicalTags = new Array(triangleCount);

    for (let i = 0; i < triangleCount; i++) {
        const group = faceGroups[i];
        if (sourceFaces.has(group)) {
            physicalTags[i] = 2; // SD1D1001
        } else if (enclosureFaces.has(group)) {
            physicalTags[i] = 3; // SD2G0
        } else if (interfaceFaces.has(group)) {
            physicalTags[i] = 4; // I1-2
        } else {
            physicalTags[i] = 1; // SD1G0 (horn walls, default)
        }
    }

    const physicalNames = [];
    physicalNames.push({ id: 1, name: 'SD1G0' });
    physicalNames.push({ id: 2, name: 'SD1D1001' });
    if (hasEnclosure) physicalNames.push({ id: 3, name: 'SD2G0' });
    if (hasInterface) physicalNames.push({ id: 4, name: 'I1-2' });

    return buildMsh(transformed, indexList, physicalTags, physicalNames);
}

/**
 * Generate Gmsh-compatible mesh with proper boundary conditions
 */
export function exportHornToMSHWithBoundaries(vertices, indices, params, groups = null, meshInfo = null) {
    const vertexList = Array.isArray(vertices) ? vertices.slice() : Array.from(vertices);
    const indexList = Array.isArray(indices) ? indices.slice() : Array.from(indices || []);

    const ringCount = meshInfo?.ringCount;
    const { capTriangleCount } = appendThroatCap(vertexList, indexList, params, ringCount);
    const transformed = transformVerticesToAth(vertexList);

    const triangleCount = indexList.length / 3;
    const physicalTags = new Array(triangleCount).fill(1);
    const hasInterface = Boolean(
        groups && groups.enclosure && groups.enclosureFront &&
        params && params.encDepth > 0 &&
        params.interfaceOffset !== undefined && params.interfaceOffset !== null &&
        String(params.interfaceOffset).trim() !== ''
    );

    if (hasInterface) {
        const { start, end } = groups.enclosure;
        for (let i = start; i < end; i++) physicalTags[i] = 3;
        const front = groups.enclosureFront;
        for (let i = front.start; i < front.end; i++) physicalTags[i] = 4;
    }

    if (capTriangleCount > 0) {
        for (let i = triangleCount - capTriangleCount; i < triangleCount; i++) {
            if (i >= 0) physicalTags[i] = 2;
        }
    }

    const physicalNames = hasInterface
        ? [
            { id: 1, name: 'SD1G0' },
            { id: 2, name: 'SD1D1001' },
            { id: 3, name: 'SD2G0' },
            { id: 4, name: 'I1-2' }
        ]
        : null;

    return buildMsh(transformed, indexList, physicalTags, physicalNames);
}
