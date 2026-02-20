/**
 * Export profile coordinates as two CSV files:
 *   - profiles: angular segments (for each angular position, points along the length)
 *   - slices:   length segments (for each length position, points around the cross-section)
 *
 * Format: X;Y;Z (semicolon delimiter), sections separated by blank lines.
 */

/**
 * Export angular profiles — for each angular index i, iterate along the length j.
 */
export function exportProfilesCSV(vertices, params) {
    const { angularSegments, lengthSegments } = params;
    let csv = '';
    const scale = 0.1;

    for (let i = 0; i < angularSegments; i++) {
        for (let j = 0; j <= lengthSegments; j++) {
            const idx = j * angularSegments + i;
            const x = vertices[idx * 3] * scale;
            const y = vertices[idx * 3 + 2] * scale;
            const z = vertices[idx * 3 + 1] * scale;
            csv += `${x.toFixed(6)};${y.toFixed(6)};${z.toFixed(6)}\r\n`;
        }
        csv += '\r\n';
    }

    return csv;
}

/**
 * Export length slices — for each length index j, iterate around the cross-section i.
 */
export function exportSlicesCSV(vertices, params) {
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
        csv += '\r\n';
    }

    return csv;
}

