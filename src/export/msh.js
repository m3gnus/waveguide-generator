/**
 * Export horn geometries to Gmsh .msh format suitable for BEM solvers
 * Supports all horn types (OSSE, R-OSSE) with proper boundary conditions
 *
 * Uses canonical mesh payloads generated from geometry/meshBuilder.
 */

import {
    formatNumber,
    evalParam,
    isFullCircle
} from '../geometry/common.js';

const toFiniteNumber = (value, fallback = 0) => {
    const num = Number(value);
    return Number.isFinite(num) ? num : fallback;
};

export const mapVertexToAth = (x, y, z, { verticalOffset = 0, offsetSign = 1 } = {}) => {
    return [
        x,
        z + (verticalOffset * offsetSign),
        y
    ];
};

const transformVerticesToAth = (vertices, options = {}) => {
    const verticalOffset = toFiniteNumber(options.verticalOffset, 0);
    const offsetSign = toFiniteNumber(options.offsetSign, 1);
    const out = new Array(vertices.length);
    for (let i = 0; i < vertices.length; i += 3) {
        const x = vertices[i];
        const y = vertices[i + 1];
        const z = vertices[i + 2];
        const [athX, athY, athZ] = mapVertexToAth(x, y, z, { verticalOffset, offsetSign });
        out[i] = athX;
        out[i + 1] = athY;
        out[i + 2] = athZ;
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
    const pointsPerRing = Math.max(1, Math.round(options.ringCount || angularSegments));
    const fullCircle = options.fullCircle !== undefined
        ? Boolean(options.fullCircle)
        : isFullCircle(params.quadrants);
    const verticalOffset = Number(params.verticalOffset) || 0;

    const pointIdAt = (ringIdx, pointIdx) => {
        const wrapped = ((pointIdx % pointsPerRing) + pointsPerRing) % pointsPerRing;
        return (ringIdx * pointsPerRing) + wrapped + 1;
    };

    const athVertexForGeo = (vx, vy, vz) => {
        if (useSplines) {
            return mapVertexToAth(vx, vy, vz, { verticalOffset, offsetSign: 1 });
        }
        return mapVertexToAth(vx, vy, vz, { verticalOffset, offsetSign: -1 });
    };

    let geo = `Mesh.Algorithm = 2;\n`;
    geo += `Mesh.MshFileVersion = 2.2;\n`;
    geo += `General.Verbosity = 2;\n`;

    if (!useSplines) {
        // Output points (1-indexed)
        for (let j = 0; j < numRings; j++) {
            for (let i = 0; i < pointsPerRing; i++) {
                const idx = (j * pointsPerRing + i) * 3;
                const vx = vertices[idx];
                const vy = vertices[idx + 1];
                const vz = vertices[idx + 2];
                const [geoX, geoY, geoZ] = athVertexForGeo(vx, vy, vz);
                const pointId = pointIdAt(j, i);
                geo += `Point(${pointId})={${geoX.toFixed(3)},${geoY.toFixed(3)},${geoZ.toFixed(3)},${meshSizeFixed.toFixed(1)}};\n`;
            }
        }

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
        // Spline-based logic with ring-wise interleaving to match ATH output structure.
        const ptsPerSpline = 10;
        let splineId = 1;
        let lineId = 2000;
        let loopId = 6000;
        let surfaceId = 1;
        const surfaceIds = [];
        const ringSplineIds = [];

        for (let j = 0; j < numRings; j++) {
            const t = j / (numRings - 1);
            const meshSize = throatResolution + (mouthResolution - throatResolution) * t;
            for (let i = 0; i < pointsPerRing; i++) {
                const idx = (j * pointsPerRing + i) * 3;
                const vx = vertices[idx];
                const vy = vertices[idx + 1];
                const vz = vertices[idx + 2];
                const [geoX, geoY, geoZ] = athVertexForGeo(vx, vy, vz);
                geo += `Point(${pointIdAt(j, i)})={${geoX.toFixed(3)},${geoY.toFixed(3)},${geoZ.toFixed(3)},${meshSize.toFixed(1)}};\n`;
            }

            const ringSplines = [];
            const splineSteps = fullCircle ? pointsPerRing : Math.max(1, pointsPerRing - 1);
            for (let s = 0; s < splineSteps; s += ptsPerSpline) {
                const startI = s;
                let count = Math.min(ptsPerSpline, splineSteps - startI);
                if (count <= 0) continue;
                const pts = [];
                for (let k = 0; k <= count; k++) {
                    pts.push(pointIdAt(j, startI + k));
                }
                geo += `Spline(${splineId})={${pts.join(',')}};\n`;
                ringSplines.push({ id: splineId++, startI, endI: startI + count });
            }
            ringSplineIds.push(ringSplines);

            if (j > 0) {
                const prev = ringSplineIds[j - 1];
                const curr = ringSplineIds[j];
                const patchCount = Math.min(prev.length, curr.length);
                for (let s = 0; s < patchCount; s++) {
                    const s1 = prev[s];
                    const s2 = curr[s];
                    const startLineId = lineId++;
                    const endLineId = lineId++;
                    geo += `Line(${startLineId})={${pointIdAt(j - 1, s1.startI)},${pointIdAt(j, s2.startI)}};\n`;
                    geo += `Line(${endLineId})={${pointIdAt(j - 1, s1.endI)},${pointIdAt(j, s2.endI)}};\n`;
                    geo += `Curve Loop(${loopId})={${s2.id},-${endLineId},-${s1.id},${startLineId}};\n`;
                    geo += `Surface(${surfaceId})={${loopId}};\n`;
                    surfaceIds.push(surfaceId);
                    surfaceId += 1;
                    loopId += 1;
                }
            }
        }
        if (surfaceIds.length > 0) {
            geo += `\nPhysical Surface("SD1G0", 1)={${surfaceIds.join(',')}};\n`;
        }
    }

    geo += `\nMesh 2;\n`;
    geo += `Save "${outputName}.msh";\n`;
    return geo;
}

/**
 * Export horn mesh to Gmsh .msh using canonical surface tags.
 *
 * Canonical tag map:
 * 1 = rigid/walls
 * 2 = source
 * 3 = optional secondary domain
 * 4 = symmetry/interface
 */
export function exportMSH(vertices, indices, surfaceTags = null, options = {}) {
    const vertexList = Array.from(vertices);
    const indexList = Array.from(indices);
    const transformed = transformVerticesToAth(vertexList, {
        verticalOffset: options.verticalOffset || 0,
        offsetSign: 1
    });
    const triangleCount = indexList.length / 3;

    const physicalTags = Array.isArray(surfaceTags)
        ? surfaceTags.slice(0, triangleCount).map((v) => Number(v) || 1)
        : new Array(triangleCount).fill(1);

    while (physicalTags.length < triangleCount) {
        physicalTags.push(1);
    }

    const tagSet = new Set(physicalTags);
    const names = [
        { id: 1, name: 'SD1G0' },
        { id: 2, name: 'SD1D1001' }
    ];
    if (tagSet.has(3)) names.push({ id: 3, name: 'SD2G0' });
    if (tagSet.has(4)) names.push({ id: 4, name: 'I1-2' });

    return buildMsh(transformed, indexList, physicalTags, names);
}
