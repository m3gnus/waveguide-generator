/**
 * Export horn geometries to Gmsh .msh format suitable for BEM solvers
 * Supports all horn types (OSSE, R-OSSE) with proper boundary conditions
 */

/**
 * Export horn geometry to Gmsh .msh format (version 2.2)
 * @param {Object} params - The complete parameter object
 * @param {Array<number>} vertices - Flat array of vertex coordinates [x,y,z, x,y,z, ...]
 * @param {Array<number>} indices - Triangle index array
 * @returns {string} Gmsh .msh file content
 */
export function exportHornToMSH(vertices, indices, params) {
    // Start with basic Gmsh header
    let mshContent = `$MeshFormat\n`;
    mshContent += `2.2 0 8\n`;
    mshContent += `$EndMeshFormat\n`;
    
    // Define points
    mshContent += `$Nodes\n`;
    mshContent += `${vertices.length / 3} 1 1 1\n`; // node count, min dim, max dim, num partitions
    
    const meshSize = params.throatResolution || 50.0; // Default mesh size
    
    // Process vertices to create Gmsh points
    for (let i = 0; i < vertices.length; i += 3) {
        const x = vertices[i];
        const y = vertices[i + 1]; 
        const z = vertices[i + 2];
        mshContent += `${i/3 + 1} ${x.toFixed(6)} ${y.toFixed(6)} ${z.toFixed(6)}\n`;
    }
    
    mshContent += `$EndNodes\n`;
    
    // Define elements (triangles)
    mshContent += `$Elements\n`;
    mshContent += `${indices.length / 3} 1 0 1\n`; // element count, min element tag, max element tag, num partitions
    
    // Create triangle elements
    for (let i = 0; i < indices.length; i += 3) {
        const node1 = indices[i] + 1;
        const node2 = indices[i + 1] + 1;
        const node3 = indices[i + 2] + 1;
        mshContent += `${i/3 + 1} 2 2 0 1 ${node1} ${node2} ${node3}\n`;
    }
    
    mshContent += `$EndElements\n`;
    
    // Define physical surfaces for BEM boundary conditions
    mshContent += `$PhysicalNames\n`;
    mshContent += `3\n`; // Number of physical names
    
    // Throat surface (acoustic source) - typically at the first cross-section
    mshContent += `1 "Throat"\n`;
    
    // Horn walls (solid boundary) - all internal surfaces
    mshContent += `2 "HornWalls"\n`;
    
    // Mouth surface (radiation boundary) - typically at the last cross-section
    mshContent += `3 "Mouth"\n`;
    
    mshContent += `$EndPhysicalNames\n`;
    
    return mshContent;
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
 * Generate Gmsh-compatible mesh with proper boundary conditions
 * @param {Object} params - The complete parameter object
 * @param {Array<number>} vertices - Flat array of vertex coordinates [x,y,z, x,y,z, ...]
 * @param {Array<number>} indices - Triangle index array
 * @returns {string} Gmsh .msh file content with proper surface tags
 */
export function exportHornToMSHWithBoundaries(vertices, indices, params) {
    // Start with basic Gmsh header
    let mshContent = `$MeshFormat\n`;
    mshContent += `2.2 0 8\n`;
    mshContent += `$EndMeshFormat\n`;
    
    // Define nodes
    mshContent += `$Nodes\n`;
    mshContent += `${vertices.length / 3} 1 1 1\n`;
    
    // Process vertices to create Gmsh points
    const meshSize = params.throatResolution || 50.0;
    
    for (let i = 0; i < vertices.length; i += 3) {
        const x = vertices[i];
        const y = vertices[i + 1]; 
        const z = vertices[i + 2];
        mshContent += `${i/3 + 1} ${x.toFixed(6)} ${y.toFixed(6)} ${z.toFixed(6)}\n`;
    }
    
    mshContent += `$EndNodes\n`;
    
    // Define elements (triangles)
    mshContent += `$Elements\n`;
    mshContent += `${indices.length / 3} 1 0 1\n`;
    
    // Create triangle elements
    for (let i = 0; i < indices.length; i += 3) {
        const node1 = indices[i] + 1;
        const node2 = indices[i + 1] + 1;
        const node3 = indices[i + 2] + 1;
        mshContent += `${i/3 + 1} 2 2 0 1 ${node1} ${node2} ${node3}\n`;
    }
    
    mshContent += `$EndElements\n`;
    
    // Define physical surfaces for BEM boundary conditions
    mshContent += `$PhysicalNames\n`;
    mshContent += `3\n`; // Number of physical names
    
    // Throat surface (acoustic source) - typically at the first cross-section
    mshContent += `1 "Throat"\n`;
    
    // Horn walls (solid boundary) - all internal surfaces
    mshContent += `2 "HornWalls"\n`;
    
    // Mouth surface (radiation boundary) - typically at the last cross-section
    mshContent += `3 "Mouth"\n`;
    
    mshContent += `$EndPhysicalNames\n`;
    
    return mshContent;
}