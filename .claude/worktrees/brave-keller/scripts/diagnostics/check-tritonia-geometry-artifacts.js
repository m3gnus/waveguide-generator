import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { parseConfig } from '../../src/config/index.js';
import { prepareGeometryParams, buildGeometryArtifacts } from '../../src/geometry/index.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const configPath = path.join(__dirname, '../../_references/testconfigs/tritonia.txt');
const configText = fs.readFileSync(configPath, 'utf8');
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
