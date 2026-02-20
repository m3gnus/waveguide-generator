import fs from 'fs';
import { parseConfig } from './src/config/index.js';
import { prepareGeometryParams, buildGeometryArtifacts } from './src/geometry/index.js';

async function run() {
    const configText = fs.readFileSync('_references/testconfigs/tritonia.txt', 'utf8');
    const rawConfig = parseConfig(configText);
    // Force enclosure parameters just to be sure
    rawConfig.params.encDepth = 100;
    rawConfig.params.encEdge = 20;

    console.log('Building payload...');
    const params = prepareGeometryParams(rawConfig, rawConfig);
    const artifacts = buildGeometryArtifacts(params, { includeEnclosure: true });

    const payload = {
        ...artifacts.simulation.payload,
        format: 'msh'
    };

    console.log('Writing payload to payload.json...');
    fs.writeFileSync('payload.json', JSON.stringify(payload, null, 2));
    console.log('Done.');
}

run();
