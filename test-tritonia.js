import fs from 'fs';
import { parseConfig } from './src/config/index.js';
import { prepareGeometryParams, buildGeometryArtifacts } from './src/geometry/index.js';

const configText = fs.readFileSync('_references/testconfigs/tritonia.txt', 'utf8');
const rawConfig = parseConfig(configText);
rawConfig.params.encDepth = 100;
rawConfig.params.encEdge = 20;
const params = prepareGeometryParams(rawConfig, rawConfig);

try {
    console.log('Building geometry artifacts...');
    const artifacts = buildGeometryArtifacts(params, { includeEnclosure: true });
    console.log('Success!');
    console.log('Vertices:', artifacts.mesh.vertices.length / 3);
    console.log('Triangles:', artifacts.mesh.indices.length / 3);
    if (artifacts.mesh.groups) {
        console.log('Groups:', artifacts.mesh.groups);
    }
} catch (e) {
    console.error('Failed to build mesh:', e);
}
