import { prepareGeometryParams, buildGeometryArtifacts } from '../../src/geometry/index.js';

// Reference horn: freestanding R-OSSE with default parameters and 6mm wall thickness
const rawConfig = {
    params: {
        formulaType: 'R-OSSE',
        R: 140,
        a: 25,
        a0: 15.5,
        r0: 12.7,
        k: 2.0,
        q: 3.4,
        r: 0.4,
        b: 0.2,
        m: 0.85,
        tmax: 1.0,
        encDepth: 0,
        wallThickness: 6.0,
    },
};
const params = prepareGeometryParams(rawConfig, rawConfig);

try {
    console.log('Building geometry artifacts...');
    const artifacts = buildGeometryArtifacts(params, { includeEnclosure: false });
    console.log('Success!');
    console.log('Vertices:', artifacts.mesh.vertices.length / 3);
    console.log('Triangles:', artifacts.mesh.indices.length / 3);
    if (artifacts.mesh.groups) {
        console.log('Groups:', artifacts.mesh.groups);
    }
} catch (e) {
    console.error('Failed to build mesh:', e);
}
