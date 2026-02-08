/**
 * Export profile coordinates in MWG CSV format
 * Format: X;Y;Z (semicolon delimiter)
 * Each cross-section separated by blank line
 */

export function exportProfilesCSV(vertices, params) {
    const { angularSegments, lengthSegments } = params;
    let csv = '';
    const scale = 0.1;

    for (let j = 0; j <= lengthSegments; j++) {
        for (let i = 0; i <= angularSegments; i++) {
            const wrappedIndex = i % angularSegments;
            const idx = j * angularSegments + wrappedIndex;
            const x = vertices[idx * 3] * scale;
            const y = vertices[idx * 3 + 2] * scale;
            const z = vertices[idx * 3 + 1] * scale;
            csv += `${x.toFixed(6)};${y.toFixed(6)};${z.toFixed(6)}\r\n`;
        }
        csv += '\r\n'; // Blank line between cross-sections
    }

    return csv;
}

/**
 * Export geometry in Gmsh .geo format (simple version)
 * Format: Point(index)={x,y,z,meshSize};
 *
 * NOTE: For BEM usage with physical surfaces, prefer `exportHornToGeo` from msh.js
 */
export function exportGmshGeo(vertices, params) {
    const { angularSegments, lengthSegments } = params;
    let geo = `Mesh.Algorithm = 2;\r\nMesh.MshFileVersion = 2.2;\r\nGeneral.Verbosity = 2;\r\n`;

    let pointIndex = 1;
    const meshSize = 50.0;  // MWG uses 50.0

    for (let j = 0; j <= lengthSegments; j++) {
        for (let i = 0; i < angularSegments; i++) {
            const idx = j * angularSegments + i;
            const x = vertices[idx * 3];
            const y = vertices[idx * 3 + 2];
            const z = vertices[idx * 3 + 1];
            geo += `Point(${pointIndex})={${x.toFixed(3)},${y.toFixed(3)},${z.toFixed(3)},${meshSize}};\r\n`;
            pointIndex++;
        }
    }

    return geo;
}
