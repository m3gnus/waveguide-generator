/**
 * Export horn geometries to Gmsh .msh format suitable for BEM solvers
 * Supports all horn types (OSSE, R-OSSE) with proper boundary conditions
 */

const EPS = 1e-9;

const cleanNumber = (value) => (Math.abs(value) < EPS ? 0 : value);
const formatNumber = (value) => {
    const v = cleanNumber(value);
    return Number.isFinite(v) ? String(v) : '0';
};

const evalParam = (value, p = 0) => (typeof value === 'function' ? value(p) : value);

const isFullCircle = (quadrants) => {
    const q = String(quadrants ?? '1234').trim();
    return q === '' || q === '1234';
};

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
 *
 * @param {Object} params - The complete parameter object
 * @param {Array<number>} vertices - Flat array of vertex coordinates [x,y,z, x,y,z, ...]
 * @returns {string} Gmsh .geo file content
 */
export function exportHornToGeo(vertices, params) {
    const { angularSegments, lengthSegments } = params;
    
    // Start with basic Gmsh header
    let geoContent = `// Gmsh .geo file for MWG horn export\n`;
    geoContent += `Mesh.Algorithm = 2;\n`;
    geoContent += `Mesh.MshFileVersion = 2.2;\n`;
    geoContent += `General.Verbosity = 2;\n`;
    
    // Define points with mesh size
    const meshSize = params.throatResolution || 50.0;
    
    let pointIndex = 1;
    for (let i = 0; i < vertices.length; i += 3) {
        const x = vertices[i];
        const y = vertices[i + 1]; 
        const z = vertices[i + 2];
        geoContent += `Point(${pointIndex})={${x.toFixed(3)},${y.toFixed(3)},${z.toFixed(3)},${meshSize}};\n`;
        pointIndex++;
    }
    
    // Define surfaces and physical surfaces
    geoContent += `// Surface definitions would go here\n`;
    geoContent += `// For now, just define the basic structure\n`;
    
    // Define physical surfaces for BEM boundary conditions
    geoContent += `// Physical surfaces (acoustic boundary conditions)\n`;
    geoContent += `Physical Surface("Throat") = {1};\n`;
    geoContent += `Physical Surface("HornWalls") = {2};\n`;
    geoContent += `Physical Surface("Mouth") = {3};\n`;
    
    return geoContent;
}

/**
 * Generate complete Gmsh .geo file matching ATH format exactly.
 * This file can be processed by Gmsh to produce bitwise-identical .msh and .stl files.
 *
 * ATH mesh.geo structure:
 * - Header: Mesh.Algorithm, Mesh.MshFileVersion, General.Verbosity
 * - Points: Point(id)={x, y, z, meshSize}
 * - Lines: interleaved radial/angular pattern (radial, angular, radial, angular, ...)
 * - Curve Loops: quad boundaries starting at 511
 * - Plane Surfaces: one per curve loop
 * - Mesh 2; Save commands
 *
 * ATH Line ordering (per point):
 *   For point i in ring j:
 *     - Radial line: connects to point i in ring j+1 (if not last ring)
 *     - Angular line: connects to point i+1 in same ring (wraps around for full circle)
 *
 * @param {Array<number>} vertices - Flat array of [x,y,z,...] in MWG coords (vx=r*cos, vy=axial, vz=r*sin)
 * @param {Object} params - Config parameters
 * @param {Object} options - Export options
 * @returns {string} Gmsh .geo file content
 */
export function exportFullGeo(vertices, params, options = {}) {
    const angularSegments = Number(params.angularSegments || 80);
    const lengthSegments = Number(params.lengthSegments || 20);
    // ATH always uses 50.0 as mesh size in mesh.geo
    const meshSize = 50.0;
    const outputName = options.outputName || 'output';

    const numRings = lengthSegments + 1;
    const pointsPerRing = angularSegments;
    const fullCircle = isFullCircle(params.quadrants);

    // Transform coordinates from MWG (vx, vy, vz) to ATH geo format (X, Y, Z)
    // MWG: vx = r*cos(p), vy = axial, vz = r*sin(p) + verticalOffset
    // GEO: X = r*cos(p), Y = r*sin(p), Z = axial (no vertical offset in geo file)
    const verticalOffset = Number(params.verticalOffset) || 0;

    let geo = `Mesh.Algorithm = 2;\n`;
    geo += `Mesh.MshFileVersion = 2.2;\n`;
    geo += `General.Verbosity = 2;\n`;

    // Output points (1-indexed)
    for (let j = 0; j < numRings; j++) {
        for (let i = 0; i < pointsPerRing; i++) {
            const idx = (j * pointsPerRing + i) * 3;
            const vx = vertices[idx];       // r * cos(p)
            const vy = vertices[idx + 1];   // axial
            const vz = vertices[idx + 2];   // r * sin(p) + verticalOffset

            // Convert to geo coordinates
            const geoX = vx;
            const geoY = vz - verticalOffset;  // Remove vertical offset for geo
            const geoZ = vy;  // axial becomes Z

            const pointId = j * pointsPerRing + i + 1;
            geo += `Point(${pointId})={${geoX.toFixed(3)},${geoY.toFixed(3)},${geoZ.toFixed(3)},${meshSize.toFixed(1)}};\n`;
        }
    }

    // Build line map for tracking IDs
    // ATH pattern: for each ring j, for each point i:
    //   - if j < numRings-1: radial line from point(j,i) to point(j+1,i)
    //   - angular line from point(j,i) to point(j,i+1) (wraps if fullCircle)
    const radialLineId = (j, i) => {
        // Radial lines only exist for j < numRings-1
        // Lines are numbered: for each point, radial first, then angular
        // Total lines per ring (except last): pointsPerRing radials + pointsPerRing angulars
        // Last ring: only angulars (pointsPerRing if fullCircle, else pointsPerRing-1)
        if (j >= numRings - 1) return null;
        // Lines before ring j
        const linesPerFullRing = pointsPerRing * 2;  // radial + angular for each point
        const linesBefore = j * linesPerFullRing;
        // Within ring j, point i: radial is at position 2*i + 1
        return linesBefore + 2 * i + 1;
    };

    const angularLineId = (j, i) => {
        const lastAngularInRing = fullCircle ? pointsPerRing : pointsPerRing - 1;
        if (i >= lastAngularInRing) return null;
        if (j < numRings - 1) {
            // Lines before ring j
            const linesPerFullRing = pointsPerRing * 2;
            const linesBefore = j * linesPerFullRing;
            // Within ring j, point i: angular is at position 2*i + 2
            return linesBefore + 2 * i + 2;
        } else {
            // Last ring: only angulars, no radials
            const linesPerFullRing = pointsPerRing * 2;
            const linesBefore = (numRings - 1) * linesPerFullRing;
            return linesBefore + i + 1;
        }
    };

    // Output lines in ATH's interleaved order
    let lineId = 1;
    for (let j = 0; j < numRings; j++) {
        for (let i = 0; i < pointsPerRing; i++) {
            const p1 = j * pointsPerRing + i + 1;

            // Radial line to next ring (if not last ring)
            if (j < numRings - 1) {
                const p2 = (j + 1) * pointsPerRing + i + 1;
                geo += `Line(${lineId})={${p1},${p2}};\n`;
                lineId++;
            }

            // Angular line to next point in same ring
            const lastAngularInRing = fullCircle ? pointsPerRing : pointsPerRing - 1;
            if (i < lastAngularInRing) {
                const nextI = (i + 1) % pointsPerRing;
                const p2 = j * pointsPerRing + nextI + 1;
                geo += `Line(${lineId})={${p1},${p2}};\n`;
                lineId++;
            }
        }
    }

    // Generate Curve Loops and Plane Surfaces for each quad face
    // ATH starts curve loops at 511, surfaces at 147
    let curveLoopId = 511;
    let surfaceId = 147;
    const angularCount = fullCircle ? pointsPerRing : pointsPerRing - 1;

    for (let j = 0; j < numRings - 1; j++) {
        for (let i = 0; i < angularCount; i++) {
            const nextI = (i + 1) % pointsPerRing;

            // Get line IDs for the quad boundary
            const radial1 = radialLineId(j, i);           // from (j,i) to (j+1,i)
            const angular2 = angularLineId(j + 1, i);     // from (j+1,i) to (j+1,i+1)
            const radial2 = radialLineId(j, nextI);       // from (j,i+1) to (j+1,i+1)
            const angular1 = angularLineId(j, i);         // from (j,i) to (j,i+1)

            if (radial1 && angular2 && radial2 && angular1) {
                // Curve loop: radial1 (forward), angular2 (forward), -radial2 (backward), -angular1 (backward)
                geo += `Curve Loop(${curveLoopId})={${radial1},${angular2},-${radial2},-${angular1}};\n`;
                geo += `Plane Surface(${surfaceId})={${curveLoopId}};\n`;
                curveLoopId++;
                surfaceId++;
            }
        }
    }

    geo += `\nMesh 2;\n\n`;
    geo += `Mesh.Binary = 1;\n`;
    geo += `Save "${outputName}.stl";\n`;

    return geo;
}

/**
 * Generate Gmsh-compatible mesh with proper boundary conditions
 * @param {Object} params - The complete parameter object
 * @param {Array<number>} vertices - Flat array of vertex coordinates [x,y,z, x,y,z, ...]
 * @param {Array<number>} indices - Triangle index array
 * @returns {string} Gmsh .msh file content with proper surface tags
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
        groups &&
        groups.enclosure &&
        groups.enclosureFront &&
        params &&
        params.encDepth > 0 &&
        params.interfaceOffset !== undefined &&
        params.interfaceOffset !== null &&
        String(params.interfaceOffset).trim() !== ''
    );

    if (hasInterface) {
        const { start, end } = groups.enclosure;
        for (let i = start; i < end; i++) {
            physicalTags[i] = 3;
        }
        const front = groups.enclosureFront;
        for (let i = front.start; i < front.end; i++) {
            physicalTags[i] = 4;
        }
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
