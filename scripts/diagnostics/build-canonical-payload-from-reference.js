import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { prepareGeometryParams, buildGeometryArtifacts } from '../../src/geometry/index.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

async function run() {
    const outputDir = path.join(__dirname, 'out');
    const outputPath = path.join(outputDir, 'payload.json');

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

    console.log('Building payload...');
    const params = prepareGeometryParams(rawConfig, rawConfig);
    const artifacts = buildGeometryArtifacts(params, { includeEnclosure: false });

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
