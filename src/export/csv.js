/**
 * Export horn geometries to CSV format for debugging and validation
 */

/**
 * Export vertex coordinates in CSV format (semicolon delimited)
 * @param {Array<number>} vertices - Flat array of vertex coordinates [x,y,z, x,y,z, ...]
 * @param {Object} params - The complete parameter object (for context)
 * @returns {string} CSV content with vertex coordinates
 */
export function exportVerticesToCSV(vertices, params) {
    let csv = 'X;Y;Z\n'; // Header
    
    // Process vertices to create CSV rows
    for (let i = 0; i < vertices.length; i += 3) {
        const x = vertices[i];
        const y = vertices[i + 2]; 
        const z = vertices[i + 1];
        csv += `${x.toFixed(6)};${y.toFixed(6)};${z.toFixed(6)}\n`;
    }
    
    return csv;
}

/**
 * Export vertex coordinates in CSV format with additional metadata
 * @param {Array<number>} vertices - Flat array of vertex coordinates [x,y,z, x,y,z, ...]
 * @param {Object} params - The complete parameter object (for context)
 * @returns {string} CSV content with vertex coordinates and metadata
 */
export function exportVerticesToCSVWithMetadata(vertices, params) {
    let csv = 'X;Y;Z;Type;Index\n'; // Header with metadata
    
    // Process vertices to create CSV rows with metadata
    for (let i = 0; i < vertices.length; i += 3) {
        const x = vertices[i];
        const y = vertices[i + 2]; 
        const z = vertices[i + 1];
        const index = i / 3;
        
        // Determine type based on position (simplified)
        let type = 'Horn';
        if (index < params.angularSegments) {
            type = 'Throat';
        } else if (index >= vertices.length / 3 - params.angularSegments) {
            type = 'Mouth';
        }
        
        csv += `${x.toFixed(6)};${y.toFixed(6)};${z.toFixed(6)};${type};${index}\n`;
    }
    
    return csv;
}

/**
 * Export cross-section profiles in CSV format (semicolon delimited)
 * @param {Array<number>} vertices - Flat array of vertex coordinates [x,y,z, x,y,z, ...]
 * @param {Object} params - The complete parameter object (for context)
 * @returns {string} CSV content with cross-section profiles
 */
export function exportCrossSectionProfilesCSV(vertices, params) {
    const { angularSegments, lengthSegments } = params;
    let csv = '';
    
    // Export each cross-section as a separate section
    for (let j = 0; j <= lengthSegments; j++) {
        csv += `Cross-section ${j}\n`;
        csv += 'X;Y;Z\n'; // Header for this cross-section
        
        for (let i = 0; i <= angularSegments; i++) {
            const idx = j * (angularSegments + 1) + i;
            if (idx < vertices.length / 3) {
                const x = vertices[idx * 3];
                const y = vertices[idx * 3 + 2]; 
                const z = vertices[idx * 3 + 1];
                csv += `${x.toFixed(6)};${y.toFixed(6)};${z.toFixed(6)}\n`;
            }
        }
        csv += '\n'; // Blank line between cross-sections
    }
    
    return csv;
}
