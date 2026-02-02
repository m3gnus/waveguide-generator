/**
 * Export profile coordinates in MWG CSV format
 * Format: X;Y;Z (semicolon delimiter)
 * Each cross-section separated by blank line
 */

export function exportProfilesCSV(vertices, params) {
    const { angularSegments, lengthSegments } = params;
    let csv = '';

    for (let j = 0; j <= lengthSegments; j++) {
        for (let i = 0; i <= angularSegments; i++) {
            const idx = j * (angularSegments + 1) + i;
            const x = vertices[idx * 3];
            const y = vertices[idx * 3 + 1];
            const z = vertices[idx * 3 + 2];
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
        for (let i = 0; i <= angularSegments; i++) {
            const idx = j * (angularSegments + 1) + i;
            const x = vertices[idx * 3];
            const y = vertices[idx * 3 + 1];
            const z = vertices[idx * 3 + 2];
            geo += `Point(${pointIndex})={${x.toFixed(3)},${y.toFixed(3)},${z.toFixed(3)},${meshSize}};\r\n`;
            pointIndex++;
        }
    }

    return geo;
}

/**
 * Compare our vertices with legacy ATH reference data
 * Returns statistics about differences
 */
export function compareWithReference(ourVertices, referenceCSV, params) {
    const { angularSegments, lengthSegments } = params;

    // Parse reference CSV
    const lines = referenceCSV.split('\n');
    const refVertices = [];

    for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed === '' || trimmed.startsWith('#')) continue;

        const parts = trimmed.split(';');
        if (parts.length >= 3) {
            refVertices.push(
                parseFloat(parts[0]),
                parseFloat(parts[1]),
                parseFloat(parts[2])
            );
        }
    }

    // Calculate differences
    const diffs = [];
    let maxDiff = 0;
    let sumSqDiff = 0;
    let count = 0;

    const minLen = Math.min(ourVertices.length, refVertices.length);

    for (let i = 0; i < minLen; i += 3) {
        const dx = ourVertices[i] - refVertices[i];
        const dy = ourVertices[i + 1] - refVertices[i + 1];
        const dz = ourVertices[i + 2] - refVertices[i + 2];
        const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);

        diffs.push({
            index: i / 3,
            ourPoint: [ourVertices[i], ourVertices[i + 1], ourVertices[i + 2]],
            refPoint: [refVertices[i], refVertices[i + 1], refVertices[i + 2]],
            diff: [dx, dy, dz],
            distance: dist
        });

        maxDiff = Math.max(maxDiff, dist);
        sumSqDiff += dist * dist;
        count++;
    }

    const rms = Math.sqrt(sumSqDiff / count);

    return {
        maxError: maxDiff,
        rmsError: rms,
        pointCount: count,
        differences: diffs,
        vertexCountMatch: ourVertices.length === refVertices.length
    };
}
