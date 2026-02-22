import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { parseConfig } from '../../src/config/index.js';
import { prepareGeometryParams, buildGeometryArtifacts } from '../../src/geometry/index.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

async function run() {
    const configPath = path.join(__dirname, '../../_references/testconfigs/tritonia.txt');
    const outputDir = path.join(__dirname, 'out');
    const outputPath = path.join(outputDir, 'payload.json');

    const configText = fs.readFileSync(configPath, 'utf8');
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

    console.log(`Writing payload to ${outputPath}...`);
    fs.mkdirSync(outputDir, { recursive: true });
    fs.writeFileSync(outputPath, JSON.stringify(payload, null, 2));
    console.log('Done.');
}

run();
