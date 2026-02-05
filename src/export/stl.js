/**
 * STL Export Module
 * 
 * Exports triangle mesh to STL format (binary or ASCII).
 * STL is widely supported for 3D printing and can be imported into CAD software.
 */

/**
 * Export mesh to binary STL format
 * 
 * @param {string} filename - Output filename
 * @param {Float32Array} vertices - Vertex positions [x, y, z, ...]
 * @param {Uint32Array} indices - Triangle indices
 * @param {string} modelName - Model name for STL header (max 80 chars)
 * @returns {ArrayBuffer} Binary STL data
 */
export function exportSTLBinary(vertices, indices, modelName = 'MWG Horn') {
    const numTriangles = indices.length / 3;
    
    // STL binary format:
    // - 80 byte header
    // - 4 byte triangle count (uint32)
    // - For each triangle:
    //   - 12 bytes normal vector (3 floats)
    //   - 36 bytes vertices (9 floats: v1.xyz, v2.xyz, v3.xyz)
    //   - 2 bytes attribute (uint16, usually 0)
    
    const bufferSize = 80 + 4 + numTriangles * 50;
    const buffer = new ArrayBuffer(bufferSize);
    const view = new DataView(buffer);
    
    // Header (80 bytes)
    const headerBytes = new TextEncoder().encode(modelName.substring(0, 79));
    for (let i = 0; i < headerBytes.length; i++) {
        view.setUint8(i, headerBytes[i]);
    }
    
    // Triangle count
    view.setUint32(80, numTriangles, true); // little-endian
    
    let offset = 84;
    
    // Write triangles
    for (let i = 0; i < indices.length; i += 3) {
        const i0 = indices[i] * 3;
        const i1 = indices[i + 1] * 3;
        const i2 = indices[i + 2] * 3;
        
        // Get vertices
        const v0 = [vertices[i0], vertices[i0 + 1], vertices[i0 + 2]];
        const v1 = [vertices[i1], vertices[i1 + 1], vertices[i1 + 2]];
        const v2 = [vertices[i2], vertices[i2 + 1], vertices[i2 + 2]];
        
        // Calculate normal
        const edge1 = [v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]];
        const edge2 = [v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]];
        const normal = [
            edge1[1] * edge2[2] - edge1[2] * edge2[1],
            edge1[2] * edge2[0] - edge1[0] * edge2[2],
            edge1[0] * edge2[1] - edge1[1] * edge2[0]
        ];
        
        // Normalize
        const len = Math.sqrt(normal[0] ** 2 + normal[1] ** 2 + normal[2] ** 2);
        if (len > 0) {
            normal[0] /= len;
            normal[1] /= len;
            normal[2] /= len;
        }
        
        // Write normal (12 bytes)
        view.setFloat32(offset, normal[0], true); offset += 4;
        view.setFloat32(offset, normal[1], true); offset += 4;
        view.setFloat32(offset, normal[2], true); offset += 4;
        
        // Write vertices (36 bytes)
        view.setFloat32(offset, v0[0], true); offset += 4;
        view.setFloat32(offset, v0[1], true); offset += 4;
        view.setFloat32(offset, v0[2], true); offset += 4;
        
        view.setFloat32(offset, v1[0], true); offset += 4;
        view.setFloat32(offset, v1[1], true); offset += 4;
        view.setFloat32(offset, v1[2], true); offset += 4;
        
        view.setFloat32(offset, v2[0], true); offset += 4;
        view.setFloat32(offset, v2[1], true); offset += 4;
        view.setFloat32(offset, v2[2], true); offset += 4;
        
        // Attribute byte count (2 bytes, usually 0)
        view.setUint16(offset, 0, true); offset += 2;
    }
    
    return buffer;
}

/**
 * Export mesh to ASCII STL format
 * 
 * @param {Float32Array} vertices - Vertex positions
 * @param {Uint32Array} indices - Triangle indices
 * @param {string} modelName - Model name
 * @returns {string} ASCII STL content
 */
export function exportSTLAscii(vertices, indices, modelName = 'MWG Horn') {
    const lines = [];
    
    lines.push(`solid ${modelName}`);
    
    for (let i = 0; i < indices.length; i += 3) {
        const i0 = indices[i] * 3;
        const i1 = indices[i + 1] * 3;
        const i2 = indices[i + 2] * 3;
        
        // Get vertices
        const v0 = [vertices[i0], vertices[i0 + 1], vertices[i0 + 2]];
        const v1 = [vertices[i1], vertices[i1 + 1], vertices[i1 + 2]];
        const v2 = [vertices[i2], vertices[i2 + 1], vertices[i2 + 2]];
        
        // Calculate normal
        const edge1 = [v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]];
        const edge2 = [v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]];
        const normal = [
            edge1[1] * edge2[2] - edge1[2] * edge2[1],
            edge1[2] * edge2[0] - edge1[0] * edge2[2],
            edge1[0] * edge2[1] - edge1[1] * edge2[0]
        ];
        
        // Normalize
        const len = Math.sqrt(normal[0] ** 2 + normal[1] ** 2 + normal[2] ** 2);
        if (len > 0) {
            normal[0] /= len;
            normal[1] /= len;
            normal[2] /= len;
        }
        
        lines.push(`  facet normal ${normal[0]} ${normal[1]} ${normal[2]}`);
        lines.push(`    outer loop`);
        lines.push(`      vertex ${v0[0]} ${v0[1]} ${v0[2]}`);
        lines.push(`      vertex ${v1[0]} ${v1[1]} ${v1[2]}`);
        lines.push(`      vertex ${v2[0]} ${v2[1]} ${v2[2]}`);
        lines.push(`    endloop`);
        lines.push(`  endfacet`);
    }
    
    lines.push(`endsolid ${modelName}`);
    
    return lines.join('\n');
}

/**
 * Write STL file to disk (Node.js)
 * 
 * @param {string} filepath - Output file path
 * @param {Float32Array} vertices - Vertex positions
 * @param {Uint32Array} indices - Triangle indices
 * @param {Object} options - Export options
 */
export async function writeSTLFile(filepath, vertices, indices, options = {}) {
    const {
        binary = true,
        modelName = 'MWG Horn'
    } = options;
    
    const { writeFileSync } = await import('fs');
    
    if (binary) {
        const buffer = exportSTLBinary(vertices, indices, modelName);
        writeFileSync(filepath, Buffer.from(buffer));
    } else {
        const ascii = exportSTLAscii(vertices, indices, modelName);
        writeFileSync(filepath, ascii, 'utf8');
    }
    
    console.log(`✓ Exported STL: ${filepath}`);
}
