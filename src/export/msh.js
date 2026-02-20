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
import {
    mapVertexToAth as mapVertexToAthTransform,
    transformVerticesToAth
} from '../geometry/transforms.js';

export const mapVertexToAth = mapVertexToAthTransform;

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
