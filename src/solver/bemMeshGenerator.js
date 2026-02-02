/**
 * BEM Mesh Generator
 *
 * Prepares horn geometry for BEM acoustic simulation by:
 * 1. Adding a throat surface (1-inch diameter circular cap) as acoustic source
 * 2. Tagging triangles by boundary type (throat, walls, mouth)
 * 3. Ensuring proper mesh format for the BEM solver
 */

import { GlobalState } from '../state.js';

// Standard 1-inch throat diameter in mm
const THROAT_DIAMETER_MM = 25.4;
const THROAT_RADIUS_MM = THROAT_DIAMETER_MM / 2;

/**
 * Generate BEM-ready mesh with throat surface and boundary tags
 *
 * @param {Object} meshData - Raw mesh data from Three.js
 * @param {Float32Array} meshData.vertices - Vertex positions [x,y,z,...]
 * @param {Uint32Array} meshData.indices - Triangle indices
 * @returns {Object} BEM mesh with vertices, indices, and boundary info
 */
export function generateBemMesh(meshData) {
    const { vertices, indices } = meshData;

    // Get current state to understand horn parameters
    const state = GlobalState.get();
    const params = state.params;

    // Find throat and mouth positions by analyzing vertex Y coordinates
    // In our coordinate system, Y is the axis along the horn length
    const vertexCount = vertices.length / 3;

    // Validate mesh indices before processing
    const maxIndex = Math.max(...indices);
    const minIndex = Math.min(...indices);
    if (maxIndex >= vertexCount) {
        console.error(`[BEM Mesh] Invalid mesh: index ${maxIndex} >= vertex count ${vertexCount}`);
        throw new Error(
            `Mesh data is corrupted: triangle index ${maxIndex} references non-existent vertex. ` +
            `Mesh has ${vertexCount} vertices but index requires at least ${maxIndex + 1}. ` +
            `Try regenerating the horn geometry.`
        );
    }
    if (minIndex < 0) {
        console.error(`[BEM Mesh] Invalid mesh: negative index ${minIndex}`);
        throw new Error(`Mesh data is corrupted: negative vertex index ${minIndex}`);
    }

    console.log(`[BEM Mesh] Input validated: ${vertexCount} vertices, ${indices.length / 3} triangles, index range [${minIndex}, ${maxIndex}]`);

    let minY = Infinity, maxY = -Infinity;

    for (let i = 0; i < vertexCount; i++) {
        const y = vertices[i * 3 + 1];
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
    }

    // Throat is at minY, mouth is at maxY
    const throatY = minY;
    const mouthY = maxY;
    const tolerance = (maxY - minY) * 0.02; // 2% tolerance for boundary detection

    // Classify triangles by boundary type
    const triangleCount = indices.length / 3;
    const boundaryTags = new Array(triangleCount);

    // Track vertices at throat and mouth for generating cap surfaces
    const throatVertexIndices = new Set();
    const mouthVertexIndices = new Set();

    for (let i = 0; i < triangleCount; i++) {
        const i0 = indices[i * 3];
        const i1 = indices[i * 3 + 1];
        const i2 = indices[i * 3 + 2];

        const y0 = vertices[i0 * 3 + 1];
        const y1 = vertices[i1 * 3 + 1];
        const y2 = vertices[i2 * 3 + 1];

        const avgY = (y0 + y1 + y2) / 3;

        // Check if all vertices are at throat
        if (Math.abs(y0 - throatY) < tolerance &&
            Math.abs(y1 - throatY) < tolerance &&
            Math.abs(y2 - throatY) < tolerance) {
            boundaryTags[i] = 'throat';
            throatVertexIndices.add(i0);
            throatVertexIndices.add(i1);
            throatVertexIndices.add(i2);
        }
        // Check if all vertices are at mouth
        else if (Math.abs(y0 - mouthY) < tolerance &&
                 Math.abs(y1 - mouthY) < tolerance &&
                 Math.abs(y2 - mouthY) < tolerance) {
            boundaryTags[i] = 'mouth';
            mouthVertexIndices.add(i0);
            mouthVertexIndices.add(i1);
            mouthVertexIndices.add(i2);
        }
        // Everything else is horn wall
        else {
            boundaryTags[i] = 'wall';
        }
    }

    // Generate throat cap (circular surface for acoustic source)
    const throatCap = generateThroatCap(vertices, throatVertexIndices, throatY);

    // Combine original mesh with throat cap
    const combinedVertices = [...vertices, ...throatCap.vertices];
    const combinedIndices = [...indices];

    // Offset throat cap indices and add them
    const vertexOffset = vertexCount;
    for (let i = 0; i < throatCap.indices.length; i++) {
        combinedIndices.push(throatCap.indices[i] + vertexOffset);
    }

    // Add throat cap triangles to boundary tags
    const throatCapTriangleCount = throatCap.indices.length / 3;
    for (let i = 0; i < throatCapTriangleCount; i++) {
        boundaryTags.push('throat');
    }

    // Convert boundary tags to numeric surface IDs for BEM solver
    // 1 = throat (pressure source), 2 = wall (rigid), 3 = mouth (radiation)
    const surfaceTags = boundaryTags.map(tag => {
        switch (tag) {
            case 'throat': return 1;
            case 'wall': return 2;
            case 'mouth': return 3;
            default: return 2;
        }
    });

    // Count boundaries for logging
    const throatCount = surfaceTags.filter(t => t === 1).length;
    const wallCount = surfaceTags.filter(t => t === 2).length;
    const mouthCount = surfaceTags.filter(t => t === 3).length;

    console.log(`[BEM Mesh] Generated mesh with ${combinedIndices.length / 3} triangles:`);
    console.log(`  - Throat (source): ${throatCount} triangles`);
    console.log(`  - Walls (rigid): ${wallCount} triangles`);
    console.log(`  - Mouth (radiation): ${mouthCount} triangles`);

    return {
        vertices: combinedVertices,
        indices: combinedIndices,
        surfaceTags: surfaceTags,
        format: 'bem',
        boundaryConditions: {
            throat: {
                type: 'velocity',  // Acoustic source - prescribed velocity
                surfaceTag: 1,
                value: 1.0  // Unit velocity source
            },
            wall: {
                type: 'neumann',  // Rigid boundary - zero normal velocity
                surfaceTag: 2,
                value: 0.0
            },
            mouth: {
                type: 'robin',  // Radiation boundary
                surfaceTag: 3,
                impedance: 'spherical'  // Spherical wave impedance
            }
        },
        metadata: {
            throatDiameterMm: THROAT_DIAMETER_MM,
            throatY: throatY,
            mouthY: mouthY,
            hornLength: mouthY - throatY
        }
    };
}

/**
 * Generate a circular cap surface at the throat
 * Creates triangulated disc to close the throat opening
 *
 * @param {Array} vertices - Original vertex array
 * @param {Set} throatVertexIndices - Indices of vertices at throat
 * @param {number} throatY - Y position of throat plane
 * @returns {Object} Cap mesh with vertices and indices
 */
function generateThroatCap(vertices, throatVertexIndices, throatY) {
    // Find center and radius of throat opening
    let centerX = 0, centerZ = 0;
    let maxRadius = 0;

    const throatVerts = [];
    for (const idx of throatVertexIndices) {
        const x = vertices[idx * 3];
        const z = vertices[idx * 3 + 2];
        throatVerts.push({ x, z, idx });
        centerX += x;
        centerZ += z;
    }

    if (throatVerts.length === 0) {
        // No throat vertices found, create a standard 1-inch cap at origin
        return generateCircularCap(0, throatY, 0, THROAT_RADIUS_MM, 16);
    }

    centerX /= throatVerts.length;
    centerZ /= throatVerts.length;

    // Find maximum radius
    for (const v of throatVerts) {
        const r = Math.sqrt((v.x - centerX) ** 2 + (v.z - centerZ) ** 2);
        if (r > maxRadius) maxRadius = r;
    }

    // Use actual throat radius or standard 1-inch, whichever is smaller
    const capRadius = Math.min(maxRadius, THROAT_RADIUS_MM);

    // Generate circular cap
    return generateCircularCap(centerX, throatY, centerZ, capRadius, 16);
}

/**
 * Generate a circular disc mesh
 *
 * @param {number} cx - Center X
 * @param {number} cy - Center Y (along horn axis)
 * @param {number} cz - Center Z
 * @param {number} radius - Disc radius
 * @param {number} segments - Number of angular segments
 * @returns {Object} Disc mesh with vertices and indices
 */
function generateCircularCap(cx, cy, cz, radius, segments) {
    const vertices = [];
    const indices = [];

    // Center vertex
    vertices.push(cx, cy, cz);
    const centerIdx = 0;

    // Ring vertices
    for (let i = 0; i < segments; i++) {
        const angle = (i / segments) * Math.PI * 2;
        const x = cx + radius * Math.cos(angle);
        const z = cz + radius * Math.sin(angle);
        vertices.push(x, cy, z);
    }

    // Create triangles (fan from center)
    for (let i = 0; i < segments; i++) {
        const next = (i + 1) % segments;
        // Wind triangles so normal points into the horn (positive Y direction)
        indices.push(centerIdx, i + 1, next + 1);
    }

    return { vertices, indices };
}

/**
 * Export mesh in Gmsh MSH format with physical surface tags
 *
 * @param {Object} bemMesh - BEM mesh from generateBemMesh
 * @returns {string} Gmsh MSH file content
 */
export function exportToGmshMSH(bemMesh) {
    const { vertices, indices, surfaceTags } = bemMesh;
    const vertexCount = vertices.length / 3;
    const triangleCount = indices.length / 3;

    let msh = '';

    // Header
    msh += '$MeshFormat\n';
    msh += '2.2 0 8\n';
    msh += '$EndMeshFormat\n';

    // Physical names
    msh += '$PhysicalNames\n';
    msh += '3\n';
    msh += '2 1 "Throat"\n';
    msh += '2 2 "HornWalls"\n';
    msh += '2 3 "Mouth"\n';
    msh += '$EndPhysicalNames\n';

    // Nodes
    msh += '$Nodes\n';
    msh += `${vertexCount}\n`;
    for (let i = 0; i < vertexCount; i++) {
        const x = vertices[i * 3];
        const y = vertices[i * 3 + 1];
        const z = vertices[i * 3 + 2];
        msh += `${i + 1} ${x.toFixed(6)} ${y.toFixed(6)} ${z.toFixed(6)}\n`;
    }
    msh += '$EndNodes\n';

    // Elements (triangles with physical surface tags)
    msh += '$Elements\n';
    msh += `${triangleCount}\n`;
    for (let i = 0; i < triangleCount; i++) {
        const n1 = indices[i * 3] + 1;
        const n2 = indices[i * 3 + 1] + 1;
        const n3 = indices[i * 3 + 2] + 1;
        const tag = surfaceTags[i];
        // Format: elem-id elem-type num-tags phys-tag geom-tag node1 node2 node3
        // Type 2 = triangle, 2 tags
        msh += `${i + 1} 2 2 ${tag} ${tag} ${n1} ${n2} ${n3}\n`;
    }
    msh += '$EndElements\n';

    return msh;
}
