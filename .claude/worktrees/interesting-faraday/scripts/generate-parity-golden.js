#!/usr/bin/env node
/**
 * Generate golden reference values for geometry parity testing.
 *
 * Evaluates JS profile functions at a standard (t, phi) grid for multiple
 * reference configs and writes the results to tests/fixtures/geometry-parity-golden.json.
 *
 * Usage:
 *   node scripts/generate-parity-golden.js
 */

import { writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { calculateOSSE } from '../src/geometry/engine/profiles/osse.js';
import { calculateROSSE } from '../src/geometry/engine/profiles/rosse.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUTPUT = join(__dirname, '..', 'tests', 'fixtures', 'geometry-parity-golden.json');

const T_VALUES = [0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0];
const PHI_VALUES = [0, Math.PI / 4, Math.PI / 2, Math.PI, 1.5 * Math.PI];

const CONFIGS = {
  osse_basic: {
    formula_type: 'OSSE',
    type: 'OSSE',
    L: 120, a: 45, a0: 15.5, r0: 12.7,
    s: 0.6, n: 4.158, q: 0.991, k: 7, h: 0,
    throatExtLength: 0, throatExtAngle: 0, slotLength: 0,
    throatProfile: 1, rot: 0, gcurveType: 0,
    morphTarget: 0,
    // Python aliases
    throat_ext_length: 0, throat_ext_angle: 0, slot_length: 0,
    throat_profile: 1, gcurve_type: 0, morph_target: 0,
  },
  rosse_basic: {
    formula_type: 'R-OSSE',
    type: 'R-OSSE',
    R: 140, a: 45, a0: 15.5, r0: 12.7,
    k: 2, r: 0.4, b: 0.2, m: 0.85, q: 3.4, tmax: 1.0,
  },
  osse_with_ext: {
    formula_type: 'OSSE',
    type: 'OSSE',
    L: 120, a: 45, a0: 15.5, r0: 12.7,
    s: 0.6, n: 4.158, q: 0.991, k: 7, h: 0,
    throatExtLength: 10, throatExtAngle: 5, slotLength: 5,
    throatProfile: 1, rot: 0, gcurveType: 0,
    morphTarget: 0,
    throat_ext_length: 10, throat_ext_angle: 5, slot_length: 5,
    throat_profile: 1, gcurve_type: 0, morph_target: 0,
  },
  osse_with_rot: {
    formula_type: 'OSSE',
    type: 'OSSE',
    L: 120, a: 45, a0: 15.5, r0: 12.7,
    s: 0.6, n: 4.158, q: 0.991, k: 7, h: 0,
    throatExtLength: 0, throatExtAngle: 0, slotLength: 0,
    throatProfile: 1, rot: 15, gcurveType: 0,
    morphTarget: 0,
    throat_ext_length: 0, throat_ext_angle: 0, slot_length: 0,
    throat_profile: 1, gcurve_type: 0, morph_target: 0,
  },
};

function evaluateConfig(name, config) {
  const results = [];
  const isRosse = config.formula_type === 'R-OSSE';

  for (const phi of PHI_VALUES) {
    for (const t of T_VALUES) {
      let profile;
      if (isRosse) {
        profile = calculateROSSE(t, phi, config);
      } else {
        const extLen = config.throatExtLength || 0;
        const slotLen = config.slotLength || 0;
        const totalLength = config.L + extLen + slotLen;
        const z = t * totalLength;
        profile = calculateOSSE(z, phi, config);
      }

      results.push({
        t,
        phi,
        x: profile.x,
        y: profile.y,
      });
    }
  }

  return results;
}

const golden = {
  _meta: {
    generated: new Date().toISOString(),
    t_values: T_VALUES,
    phi_values: PHI_VALUES,
    description: 'Golden reference values from JS profile functions for cross-pipeline parity testing.',
  },
};

for (const [name, config] of Object.entries(CONFIGS)) {
  golden[name] = evaluateConfig(name, config);
}

mkdirSync(join(__dirname, '..', 'tests', 'fixtures'), { recursive: true });
writeFileSync(OUTPUT, JSON.stringify(golden, null, 2) + '\n');
console.log(`Written golden reference to ${OUTPUT}`);
console.log(`Configs: ${Object.keys(CONFIGS).join(', ')}`);
console.log(`Points per config: ${T_VALUES.length * PHI_VALUES.length}`);
